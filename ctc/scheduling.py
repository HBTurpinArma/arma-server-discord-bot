"""Scheduling a badge session: the schedule tool, booking and rescheduling.

Everything between a request being claimed and its session starting. It is a
mixin for the CTC cog rather than a cog of its own, because it works on the
same requests through the same database and thread helpers; it lives here so
that file is not twice the length.

The cog provides ``bot``, ``database``, ``_later``, ``catalogue_of``,
``channel_by_id``, ``post_to_thread``, ``refresh_ticket`` and ``clear_bump``.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Sequence
from typing import Any

import discord
from discord.ext import tasks

from . import (
    TransitionError,
    booked_ahead,
    booked_for,
    first_side,
    home_of,
    instructors_of,
    mention_all,
    rescheduling,
)
from . import schedule as sched
from .schedule_views import (
    AvailabilityPicker,
    BookView,
    RescheduleView,
    SetDateView,
    SideChoiceView,
    TimezoneView,
    all_times_embed,
    times_embed,
    times_view,
)

#: Said when someone acts on a request they are no longer part of, or one
#: that has been finished or released since they opened a picker.
NOT_OPEN = "This request is no longer open for you to schedule."

#: Marks a reschedule, on the card button and in the thread.
RESCHEDULE_EMOJI = "\N{CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS}"

#: How long before a booked session everyone on it is pinged, in seconds.
REMIND_BEFORE = 3600

#: How long after someone's last change to their hour lists the other side is
#: pinged, in seconds. Long enough that the other side has often acted already.
PING_AFTER = 300

#: Booking, by Book a time or Set a date, is for the people running the
#: session, not the trainee or another instructor passing by.
SEATED_ONLY = "Only the TI on this request (or its Guest TI) can book or set the time."


class Scheduling:
    """The scheduling half of the CTC cog."""

    @staticmethod
    def is_seated(user: discord.abc.User, row: Any) -> bool:
        """True for the request's own TI or Guest TI."""
        return str(user.id) in instructors_of(row)

    def side_of(self, user: discord.abc.User, row: Any) -> str | None:
        """``trainee`` for the member who asked, ``trainer`` for the request's
        own TI or Guest TI. Nobody else takes part, other instructors included.
        """
        if str(user.id) == row["member_id"]:
            return "trainee"
        if self.is_seated(user, row):
            return "trainer"
        return None

    def sides_of(self, user: discord.abc.User, row: Any) -> list[str]:
        """Every side someone is on. Usually one; both when a TI has claimed
        their own request."""
        sides = []
        if str(user.id) == row["member_id"]:
            sides.append("trainee")
        if self.is_seated(user, row):
            sides.append("trainer")
        return sides

    async def open_tool(
        self, interaction: discord.Interaction, row: Any, *, edit: bool = False
    ) -> None:
        """Open someone's hour lists, asking which side first if they are on both."""
        sides = self.sides_of(interaction.user, row)
        if len(sides) < 2:
            await self.open_picker(interaction, row, sides[0], edit=edit)
            return
        view = SideChoiceView(self, int(row["id"]))
        if edit:
            await interaction.response.edit_message(content=view.CONTENT, view=view)
        else:
            await interaction.response.send_message(view.CONTENT, view=view, ephemeral=True)

    async def choose_side(
        self, interaction: discord.Interaction, request_id: int, side: str
    ) -> None:
        row = await self.database.by_id(request_id)
        if row is None or row["status"] != "claimed" or side not in self.sides_of(
            interaction.user, row
        ):
            await interaction.response.edit_message(
                content=NOT_OPEN, view=None
            )
            return
        await self.open_picker(interaction, row, side, edit=True)

    async def marked_hours(self, row: Any) -> list[Any]:
        """The hours that count: the trainee's, and those of whoever is seated
        on the request now. Hours left by an instructor who has since been
        released or reassigned are ignored.
        """
        seated = instructors_of(row)
        return [
            r for r in await self.database.availability(int(row["id"]))
            if r["side"] == "trainee" or r["user_id"] in seated
        ]

    async def times_summary(self, request_id: int) -> sched.Summary:
        row = await self.database.by_id(request_id)
        return sched.summarise(await self.marked_hours(row) if row else [], time.time())

    async def times_channel(self, row: Any) -> Any:
        return await self.channel_by_id(home_of(row))

    async def ensure_times_message(self, row: Any) -> None:
        """Post the shared schedule message in the request's thread, once."""
        if row["times_message_id"] or not home_of(row):
            return
        try:
            channel = await self.times_channel(row)
            message = await channel.send(
                embed=times_embed(
                    self.catalogue_of(row), row, await self.times_summary(int(row["id"]))
                ),
                view=times_view(int(row["id"])),
            )
        except discord.HTTPException as error:
            self.bot.logger.warning(f"CTC: could not post times for #{row['id']}: {error}")
            return
        await self.database.set_times_message(int(row["id"]), str(message.id))

    async def post_times_note(self, row: Any, content: str) -> None:
        """Post a scheduling ping and remember it, so booking can tidy it away."""
        if not home_of(row):
            return
        try:
            message = await (await self.times_channel(row)).send(content)
        except discord.HTTPException as error:
            self.bot.logger.warning(f"CTC: could not post to thread for #{row['id']}: {error}")
            return
        await self.database.add_times_note(int(row["id"]), str(message.id))

    async def clear_times_messages(self, row: Any) -> None:
        """Delete the schedule message and every ping it led to.

        Called once a time is booked. Pings still waiting to be sent are
        dropped as well. Open Schedule Tool on the card posts a fresh message if the
        session needs moving.
        """
        request_id = int(row["id"])
        for key in [k for k in self._later if k[0] in ("ping", "summary") and k[1] == request_id]:
            self._later.pop(key).cancel()
        ids = [row["times_message_id"], *(row["times_note_ids"] or "").split(",")]
        ids = [i for i in ids if i]
        if ids and home_of(row):
            with contextlib.suppress(discord.HTTPException):
                channel = await self.times_channel(row)
                for message_id in ids:
                    # Best effort each: one tidied away by hand must not
                    # leave the rest behind.
                    with contextlib.suppress(discord.HTTPException):
                        await channel.get_partial_message(int(message_id)).delete()
        await self.database.clear_times_messages(request_id)

    async def refresh_times_message(self, request_id: int) -> None:
        row = await self.database.by_id(request_id)
        if row is None or not row["times_message_id"]:
            return
        try:
            channel = await self.times_channel(row)
            message = await channel.fetch_message(int(row["times_message_id"]))
            await message.edit(
                embed=times_embed(
                    self.catalogue_of(row), row, await self.times_summary(request_id)
                ),
                view=times_view(request_id),
            )
        except discord.NotFound:
            # Deleted by hand: forget it, so the next Open Schedule Tool posts a new one.
            await self.database.set_times_message(request_id, None)
        except discord.HTTPException as error:
            self.bot.logger.warning(f"CTC: could not refresh times for #{request_id}: {error}")

    def later(self, key: tuple[Any, ...], delay: float, job: Any) -> None:
        """Run ``job`` once nobody has called this with ``key`` for ``delay`` seconds.

        The hour lists save a day at a time, and a summary edit or a ping per save would
        bury the thread, so both wait for the tapping to stop. A restart drops
        whatever is waiting, which costs at most one ping.
        """
        pending = self._later.pop(key, None)
        if pending is not None:
            pending.cancel()

        async def run() -> None:
            try:
                await asyncio.sleep(delay)
                await job()
            except asyncio.CancelledError:
                pass
            except Exception as error:  # noqa: BLE001 - a lost ping must not kill the task
                self.bot.logger.warning(f"CTC: scheduled job {key} failed: {error}")
            finally:
                if self._later.get(key) is task:
                    del self._later[key]

        task = asyncio.get_running_loop().create_task(run())
        self._later[key] = task

    async def card_schedule_action(
        self, interaction: discord.Interaction, action: str, row: Any
    ) -> None:
        """Open Schedule Tool, Set a Time/Date or Reschedule, pressed on the card."""
        request_id = int(row["id"])
        side = self.side_of(interaction.user, row)
        seated = self.is_seated(interaction.user, row)
        when = booked_for(row)
        if when:
            # A booked session only moves with a reason, whichever button was
            # pressed, so a stale card cannot skip it.
            if side is None:
                await interaction.response.send_message(SEATED_ONLY, ephemeral=True)
                return
            await interaction.response.send_message(
                RescheduleView.content(when),
                view=RescheduleView(self, request_id, can_set=seated),
                ephemeral=True,
            )
        elif action == "resched":
            await interaction.response.send_message(
                "Nothing is booked on this request any more.", ephemeral=True
            )
            await self.refresh_ticket(row)
        elif action == "date":
            if not seated:
                await interaction.response.send_message(SEATED_ONLY, ephemeral=True)
                return
            await self.open_set_date(interaction, row)
        else:
            # Answer first: posting the shared message is a second request to
            # Discord, and the press has to be answered within three seconds.
            await self.open_tool(interaction, row)
            await self.ensure_times_message(row)

    async def handle_schedule_action(
        self, interaction: discord.Interaction, action: str, request_id: int
    ) -> None:
        """A press on the shared schedule message: check who pressed, then
        hand over to the function for that button."""
        row = await self.database.by_id(request_id)
        if row is None:
            await interaction.response.send_message(
                f"Request #{request_id} no longer exists.", ephemeral=True
            )
            return

        side = self.side_of(interaction.user, row)
        if side is None:
            await interaction.response.send_message(
                "This is for the trainee and the TI on this request (or its Guest TI).",
                ephemeral=True,
            )
        elif action == "zone":
            current = await self.database.timezone_of(str(interaction.user.id))
            await interaction.response.send_message(
                TimezoneView.content(current),
                view=TimezoneView(self, request_id, current=current, then=None),
                ephemeral=True,
            )
        elif row["status"] != "claimed":
            await interaction.response.send_message(
                f"Request #{request_id} is {row['status']}, so there is nothing to schedule.",
                ephemeral=True,
            )
        elif action == "mark":
            await self.open_tool(interaction, row)
        elif action == "hours":
            await interaction.response.send_message(
                embed=all_times_embed(
                    row,
                    await self.times_summary(request_id),
                    await self.database.timezone_of(str(interaction.user.id)),
                ),
                ephemeral=True,
            )
        elif action == "none":
            await self.ask_for_more_hours(interaction, row, side)
        elif not self.is_seated(interaction.user, row):
            # What is left, Set a Time/Date and Book a time, is for the TI.
            await interaction.response.send_message(SEATED_ONLY, ephemeral=True)
        elif action == "date":
            await self.open_set_date(interaction, row)
        elif action == "book":
            await self.open_booking(interaction, row)

    async def ask_for_more_hours(
        self, interaction: discord.Interaction, row: Any, side: str
    ) -> None:
        """None of these work. Either side can say so: whoever went second
        only sees the other's hours, so this is how they ask for more."""
        if side == "trainee":
            trainers = (await self.times_summary(int(row["id"]))).trainers or instructors_of(row)
            others, told = mention_all(trainers), "the instructor"
        else:
            others, told = f"<@{row['member_id']}>", "the trainee"
        await interaction.response.send_message(
            f"Told {told}. They will be asked to mark some more hours.", ephemeral=True
        )
        await self.post_times_note(
            row,
            f"{others} <@{interaction.user.id}> cannot make any of the hours marked so "
            "far. Add some more with **Mark my times**.",
        )

    async def open_booking(self, interaction: discord.Interaction, row: Any) -> None:
        """Book a time: offer the start times both sides are free."""
        request_id = int(row["id"])
        starts = sched.booking_starts((await self.times_summary(request_id)).both)
        if not starts:
            await interaction.response.send_message(
                "Nothing lines up yet. Start times show here once the trainee has "
                "picked hours the instructor is around.",
                ephemeral=True,
            )
            return
        tz = await self.database.timezone_of(str(interaction.user.id)) or "Etc/UTC"
        await interaction.response.send_message(
            "When does it start?",
            view=BookView(self, request_id, starts=starts, tz=tz),
            ephemeral=True,
        )

    async def build_picker(
        self, row: Any, user_id: int, side: str, tz: str, page: int
    ) -> AvailabilityPicker | None:
        """Someone's hour lists, or None for a trainee with nothing to pick from."""
        now = time.time()
        rows = await self.marked_hours(row)
        mine = {
            int(r["slot_start"]) for r in rows if r["user_id"] == str(user_id) and r["side"] == side
        }
        other = "trainee" if side == "trainer" else "trainer"
        theirs = sched.upcoming(
            sched.ranges(int(r["slot_start"]) for r in rows if r["side"] == other), now
        )
        # Whoever marked hours first sees every hour; the other side picks
        # from those. Where nothing was recorded (a request from before this
        # rule), having no hours yet while the other side has some means
        # going second. A reschedule opens it again: both sides still have
        # their old hours, and whoever saves first becomes the first side.
        first = first_side(row)
        limited = bool(theirs) and (
            first == other if first else not mine and not rescheduling(row)
        )
        picker = AvailabilityPicker(
            self, row, user_id=user_id, side=side, tz=tz, page=page,
            mine=mine, theirs=theirs, now=now, limited=limited,
        )
        return picker if picker.days else None

    async def open_picker(
        self,
        interaction: discord.Interaction,
        row: Any,
        side: str,
        *,
        page: int = 0,
        edit: bool = False,
    ) -> None:
        """Show someone their hour lists, asking for their time zone first if need be."""
        tz = await self.database.timezone_of(str(interaction.user.id))
        if tz is None:
            content = TimezoneView.content(None)
            view: discord.ui.View | None = TimezoneView(
                self, int(row["id"]), current=None, then="mark"
            )
        else:
            view = await self.build_picker(row, interaction.user.id, side, tz, page)
            content = (
                view.content()
                if view is not None
                else "None of the hours marked so far are still to come. Ask for some more."
            )
        if edit:
            await interaction.response.edit_message(content=content, view=view)
        elif view is not None:
            await interaction.response.send_message(content, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(content, ephemeral=True)

    async def open_set_date(
        self, interaction: discord.Interaction, row: Any, *, edit: bool = False
    ) -> None:
        """Show the day, hour and minute picker, asking for a time zone first if need be."""
        tz = await self.database.timezone_of(str(interaction.user.id))
        if tz is None:
            content = TimezoneView.content(None)
            view: discord.ui.View = TimezoneView(self, int(row["id"]), current=None, then="date")
        else:
            view = SetDateView(self, int(row["id"]), tz=tz, now=time.time())
            content = view.content()
        if edit:
            await interaction.response.edit_message(content=content, view=view)
        else:
            await interaction.response.send_message(content, view=view, ephemeral=True)

    async def start_reschedule(
        self, interaction: discord.Interaction, request_id: int, mode: str, reason: str
    ) -> None:
        """A reason has been given: say so in the thread, then open the
        schedule tool (``mode`` "tool") or the Set a Time/Date picker ("date").

        The current booking stays until a new time is booked, so the thread
        keeps quiet and the reminder holds if the move is never finished.
        """
        row = await self.database.by_id(request_id)
        side = self.side_of(interaction.user, row) if row is not None else None
        if (
            row is None
            or row["status"] != "claimed"
            or side is None
            or (mode == "date" and not self.is_seated(interaction.user, row))
        ):
            await interaction.response.edit_message(
                content=NOT_OPEN, view=None
            )
            return

        await self.database.start_reschedule(request_id)
        if mode == "date":
            await self.open_set_date(interaction, row, edit=True)
        else:
            await self.open_tool(interaction, row, edit=True)

        if not home_of(row):
            return
        when = booked_for(row)
        people = dict.fromkeys([row["member_id"], *instructors_of(row)])
        try:
            await (await self.times_channel(row)).send(
                f"{RESCHEDULE_EMOJI} <@{interaction.user.id}> is rescheduling this session"
                + (f" (booked for <t:{when}:F>)" if when else "")
                + f".\n**Reason:** {reason.strip()}\n"
                + mention_all(people),
                # The reason is free text: it must not be able to ping anyone.
                allowed_mentions=discord.AllowedMentions(
                    everyone=False,
                    roles=False,
                    users=[discord.Object(id=int(p)) for p in people],
                ),
            )
        except discord.HTTPException as error:
            self.bot.logger.warning(f"CTC: could not announce reschedule of #{request_id}: {error}")
        if mode != "date":
            await self.ensure_times_message(await self.database.by_id(request_id))

    async def save_day(
        self,
        interaction: discord.Interaction,
        picker: AvailabilityPicker,
        offered: Sequence[int],
        chosen: Sequence[int],
    ) -> None:
        """Save one day's list: of the hours it offered, exactly the ticked ones."""
        row = await self.database.by_id(picker.request_id)
        # Checked again here: the seats can change while the lists sit open.
        if (
            row is None
            or row["status"] != "claimed"
            or picker.side not in self.sides_of(interaction.user, row)
        ):
            await interaction.response.edit_message(
                content=NOT_OPEN, view=None
            )
            return

        # The first save on a request settles who went first.
        if first_side(row) is None and chosen:
            other = "trainee" if picker.side == "trainer" else "trainer"
            other_marked = any(r["side"] == other for r in await self.marked_hours(row))
            # During a reschedule the other side's old hours do not make them first.
            went_second = other_marked and not rescheduling(row)
            await self.database.set_first_side(
                picker.request_id, other if went_second else picker.side
            )

        # An hour can pass while the list sits open; it is left as it was.
        now = time.time()
        changed = await self.database.set_slots(
            picker.request_id,
            user_id=str(interaction.user.id),
            side=picker.side,
            offered=[start for start in offered if start > now],
            chosen=chosen,
        )
        if changed:
            request_id, side, actor = picker.request_id, picker.side, interaction.user.id
            self.later(("summary", request_id), 5, lambda: self.refresh_times_message(request_id))
            self.later(
                ("ping", request_id, side),
                PING_AFTER,
                lambda: self.announce_times(request_id, side, actor),
            )
        picker.stop()
        await self.open_picker(interaction, row, picker.side, page=picker.page, edit=True)

    async def show_page(
        self, interaction: discord.Interaction, picker: AvailabilityPicker, page: int
    ) -> None:
        row = await self.database.by_id(picker.request_id)
        if row is None:
            await interaction.response.edit_message(content="This request is gone.", view=None)
            return
        picker.stop()
        await self.open_picker(interaction, row, picker.side, page=page, edit=True)

    async def save_timezone(
        self, interaction: discord.Interaction, view: TimezoneView, tz: str
    ) -> None:
        await self.database.set_timezone(str(interaction.user.id), tz)
        view.stop()
        row = await self.database.by_id(view.request_id) if view.then else None
        side = self.side_of(interaction.user, row) if row is not None else None
        if side is not None and view.then == "mark":
            await self.open_tool(interaction, row, edit=True)
            return
        if row is not None and view.then == "date" and self.is_seated(interaction.user, row):
            await self.open_set_date(interaction, row, edit=True)
            return
        await interaction.response.edit_message(
            content=f"Time zone set to **{sched.zone_label(tz)}**.", view=None
        )

    async def announce_times(self, request_id: int, side: str, actor_id: int) -> None:
        """Tell the other side, once someone has stopped marking hours.

        Once per side per request: the first set of hours is news, later
        changes only update the shared message. Recorded on the row, so a
        restart does not ping again.

        And not at all when the ping would ask for something already done:
        the trainee is not told to pick hours they have already picked, and
        the instructor is not told to book a session that is already booked.
        """
        row = await self.database.by_id(request_id)
        if row is None or row["status"] != "claimed" or row[f"{side}_times_pinged_at"]:
            return
        summary = await self.times_summary(request_id)
        settled = booked_ahead(row) and not rescheduling(row)
        answered = settled or (side == "trainer" and summary.trainee_free)
        if answered:
            await self.database.mark_times_pinged(request_id, side)
            return
        if side == "trainer":
            if not summary.trainer_free:
                return
            await self.post_times_note(
                row,
                f"<@{row['member_id']}> <@{actor_id}> has marked when they are around. "
                "Press **Mark my times** on the schedule message to pick the hours you can do.",
            )
            await self.database.mark_times_pinged(request_id, side)
            return

        trainers = summary.trainers or instructors_of(row)
        if not trainers:
            return
        starts = sched.booking_starts(summary.both)
        if starts:
            tail = (
                f" {len(starts)} start time{'' if len(starts) == 1 else 's'} line up, the first "
                f"<t:{starts[0]}:f>. Press **Book a time** to fix one."
            )
        elif summary.trainer_free:
            tail = " Nothing lines up with your hours yet."
        else:
            tail = " Press **Mark my times** on the schedule message to add yours."
        await self.post_times_note(
            row,
            f"{mention_all(trainers)} <@{row['member_id']}> has picked the "
            f"hours they can do.{tail}",
        )
        await self.database.mark_times_pinged(request_id, side)

    async def book_time(self, interaction: discord.Interaction, request_id: int, when: int) -> None:
        """Book a session, from Book a time or Set a date. Checked again here,
        since the seats can change while a picker sits open."""
        row = await self.database.by_id(request_id)
        if row is None or not self.is_seated(interaction.user, row):
            await interaction.response.edit_message(content=SEATED_ONLY, view=None)
            return
        if when <= time.time():
            await interaction.response.edit_message(
                content="That time has already passed. Press **Book a time** again.", view=None
            )
            return
        try:
            updated = await self.database.book(
                request_id, when=when, booked_by=str(interaction.user.id)
            )
        except TransitionError as error:
            await interaction.response.edit_message(content=f"\N{WARNING SIGN} {error}", view=None)
            return

        await interaction.response.edit_message(content=f"Booked for <t:{when}:F>.", view=None)
        await self.refresh_ticket(updated)
        # The schedule message and its pings have done their job. The
        # card carries the booked time from here on.
        await self.clear_times_messages(updated)
        # The thread goes quiet until the reminder, so the last keep-alive
        # bump has nothing left to do.
        if updated["bump_message_id"] and updated["thread_id"]:
            with contextlib.suppress(discord.HTTPException):
                await self.clear_bump(await self.times_channel(updated), updated["bump_message_id"])
            await self.database.set_bump_message(request_id, None)
        people = dict.fromkeys(
            [updated["member_id"], *instructors_of(updated), str(interaction.user.id)]
        )
        if not home_of(updated):
            return
        try:
            channel = await self.times_channel(updated)
            # A rebooking replaces the old announcement rather than adding to it.
            if updated["booked_message_id"]:
                with contextlib.suppress(discord.HTTPException):
                    await channel.get_partial_message(int(updated["booked_message_id"])).delete()
            message = await channel.send(
                f"\N{CALENDAR} Booked for <t:{when}:F> (<t:{when}:R>). "
                + mention_all(people)
            )
        except discord.HTTPException as error:
            self.bot.logger.warning(f"CTC: could not announce booking for #{request_id}: {error}")
            return
        await self.database.set_booked_message(request_id, str(message.id))

    @tasks.loop(minutes=5)
    async def reminder_loop(self) -> None:
        """Ping everyone on a booked session in the hour before it starts."""
        if self.database is None:
            return
        for row in await self.database.due_reminders(now=int(time.time()), within=REMIND_BEFORE):
            when = booked_for(row)
            people = dict.fromkeys([row["member_id"], *instructors_of(row)])
            await self.post_to_thread(
                row,
                "\N{ALARM CLOCK} " + mention_all(people)
                + f" training starts <t:{when}:R>, at <t:{when}:t>.",
            )
            await self.database.mark_reminded(int(row["id"]))

    @reminder_loop.before_loop
    async def before_reminder_loop(self) -> None:
        await self.bot.wait_until_ready()
