"""Discord components for picking a time to run a badge request.

The shared message in the request's thread is persistent: its buttons carry
the request id in their custom_id, like the ticket buttons. Everything a
person opens from it (their hour lists, the time zone picker, the booking
list) is ephemeral, so it can be labelled in that one person's own time.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import discord

from . import booked_for
from . import schedule as sched
from .catalogue import Catalogue
from .views import COLOUR

#: Actions on the shared message. Named explicitly, as with ticket actions.
SCHEDULE_ACTIONS = ("mark", "book", "date", "none", "zone", "hours")

#: Both-free times shown on the shared message; the rest are a button away.
SHOWN_ON_MESSAGE = 5


class ScheduleButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=rf"ctcsched:(?P<action>{'|'.join(SCHEDULE_ACTIONS)}):(?P<rid>[0-9]+)",
):
    """A button on the shared message. Persistent across restarts via its custom_id."""

    def __init__(
        self,
        action: str,
        request_id: int,
        *,
        label: str,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        emoji: str | None = None,
    ) -> None:
        self.action = action
        self.request_id = request_id
        super().__init__(
            discord.ui.Button(
                label=label, style=style, emoji=emoji, custom_id=f"ctcsched:{action}:{request_id}"
            )
        )

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /
    ) -> ScheduleButton:
        return cls(
            match["action"],
            int(match["rid"]),
            label=item.label or "",
            style=item.style,
            emoji=str(item.emoji) if item.emoji else None,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("ctc")
        if cog is None:  # pragma: no cover - only if the cog is unloaded
            await interaction.response.send_message("Badge system is offline.", ephemeral=True)
            return
        await cog.handle_schedule_action(interaction, self.action, self.request_id)


def times_view(request_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(
        ScheduleButton(
            "mark", request_id, label="Mark my times",
            style=discord.ButtonStyle.primary, emoji="\N{SPIRAL CALENDAR PAD}",
        )
    )
    view.add_item(
        ScheduleButton(
            "book", request_id, label="Book a time",
            style=discord.ButtonStyle.success, emoji="\N{WHITE HEAVY CHECK MARK}",
        )
    )
    view.add_item(
        ScheduleButton("date", request_id, label="Set a Time/Date", emoji="\N{CALENDAR}")
    )
    view.add_item(ScheduleButton("none", request_id, label="None of these work"))
    view.add_item(
        ScheduleButton("zone", request_id, label="My time zone", emoji="\N{GLOBE WITH MERIDIANS}")
    )
    view.add_item(ScheduleButton("hours", request_id, label="All times"))
    return view


def times_embed(cat: Catalogue, row: Any, summary: sched.Summary) -> discord.Embed:
    """The shared message, kept short: who has marked hours, and the next
    few times both are free. The full lists are behind **All times**.

    Every time on it is a Discord timestamp, so each reader sees it in their
    own time zone without setting anything.
    """
    badge = cat.get(row["badge_key"])
    trainers = " ".join(f"<@{t}>" for t in summary.trainers) or "Instructor"
    lines = [
        f"{trainers}: "
        + (f"{sched.total_hours(summary.trainer_free)} marked" if summary.trainer_free
           else "no hours marked yet"),
        f"<@{row['member_id']}>: "
        + (f"{sched.total_hours(summary.trainee_free)} picked" if summary.trainee_free
           else "no hours picked yet"),
    ]
    embed = discord.Embed(
        colour=COLOUR,
        title=f"\N{SPIRAL CALENDAR PAD} Schedule · {badge.name if badge else row['badge_key']}",
        description="\n".join(lines),
    )
    when = booked_for(row)
    if when:
        embed.add_field(
            name="\N{CALENDAR} Booked", value=f"<t:{when}:F> (<t:{when}:R>)", inline=False
        )
    # Both show while a booked session is being moved.
    if summary.both:
        embed.add_field(
            name="\N{WHITE HEAVY CHECK MARK} Both free, in your time",
            value=sched.describe(summary.both, limit=SHOWN_ON_MESSAGE),
            inline=False,
        )
    embed.set_footer(text=f"Request #{row['id']} · All times shows every hour marked")
    return embed


def all_times_embed(row: Any, summary: sched.Summary, tz: str | None) -> discord.Embed:
    """Every marked hour, for one person. With their time zone known it is
    grouped a line per day on their clock; without, it falls back to
    timestamps."""

    def show(spans: list[tuple[int, int]]) -> str:
        text = sched.by_day(tz, spans) if tz else sched.describe(spans, limit=20)
        return text if len(text) <= 1024 else text[:1020].rsplit("\n", 1)[0] + "\n…"

    embed = discord.Embed(colour=COLOUR, title=f"All times · request #{row['id']}")
    embed.add_field(name="Instructor around", value=show(summary.trainer_free), inline=False)
    embed.add_field(name="Trainee can do", value=show(summary.trainee_free), inline=False)
    embed.add_field(
        name="\N{WHITE HEAVY CHECK MARK} Both free", value=show(summary.both), inline=False
    )
    embed.set_footer(
        text=f"In your time ({sched.zone_label(tz)})" if tz
        else "Set My time zone for a tidier list"
    )
    return embed


# ----------------------------------------------------------- hour picker


class AvailabilityPicker(discord.ui.View):
    """One person's hours, a list per day, on their own clock.

    Each day is a multi-select: open it, tick every hour, close it, and the
    whole day saves in one go. Ticking happens in the Discord app, so there
    is no wait between hours, unlike a button per hour.

    ``theirs`` is the other side's free time: the trainee's for an
    instructor, the instructors' for a trainee. Either side can go first.
    Whoever does is offered every hour still to come; the other side is then
    ``limited`` to the hours the first one marked, so anything they tick is a
    time both can do.
    """

    #: A message holds five rows; four lists leave one for the paging button.
    DAYS_PER_PAGE = 4

    def __init__(
        self,
        cog: Any,
        row: Any,
        *,
        user_id: int,
        side: str,
        tz: str,
        page: int,
        mine: set[int],
        theirs: list[tuple[int, int]],
        now: float,
        limited: bool = False,
    ) -> None:
        super().__init__(timeout=840)  # inside the 15 minutes an ephemeral can be edited
        self.limited = limited
        self.cog = cog
        self.row = row
        self.request_id = int(row["id"])
        self.user_id = user_id
        self.side = side
        self.tz = tz

        #: (date, [(hour, start, other side is free)]) for each day with anything to pick.
        self.days: list[tuple[date, list[tuple[int, int, bool]]]] = []
        for offset in range(sched.DAYS_AHEAD):
            day = sched.day_of(tz, offset, now)
            hours = []
            for hour in sched.hours_of(tz, day):
                if hour.start is None or hour.start <= now:
                    continue
                around = sched.covers(theirs, hour.start, hour.start + sched.HOUR)
                # An hour already ticked stays listed, so it can be unticked.
                if around or not limited or hour.start in mine:
                    hours.append((hour.hour, hour.start, around))
            if hours:
                self.days.append((day, hours))

        self.pages = max(1, -(-len(self.days) // self.DAYS_PER_PAGE))
        self.page = page % self.pages
        first = self.page * self.DAYS_PER_PAGE
        for index, (day, hours) in enumerate(self.days[first:first + self.DAYS_PER_PAGE]):
            self.add_item(_DaySelect(self, day, hours, mine, row=index))
        if self.pages > 1:
            self.add_item(_MoreDaysButton(self))

    def content(self) -> str:
        other = f"<@{self.row['member_id']}>" if self.side == "trainer" else "the instructor"
        how = (
            "Open a day, tick every hour you can do, then click outside the list to save it. "
        )
        if self.limited:
            how += f"Only the hours {other} marked are listed."
        elif self.side == "trainer":
            how += f"Hours {other} can already do are marked."
        else:
            how += "Hours an instructor is around are marked."
        return f"**Your hours** · your time ({sched.zone_label(self.tz)})\n{how}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id


class _DaySelect(discord.ui.Select):
    def __init__(
        self,
        picker: AvailabilityPicker,
        day: date,
        hours: list[tuple[int, int, bool]],
        mine: set[int],
        *,
        row: int,
    ) -> None:
        picked = sum(1 for _, start, _ in hours if start in mine)
        super().__init__(
            placeholder=(
                f"{sched.day_label(day)} · {picked} h picked" if picked
                else f"{sched.day_label(day)} · pick your hours"
            ),
            min_values=0,
            max_values=len(hours),
            options=[
                discord.SelectOption(
                    label=f"{hour:02d}:00",
                    value=str(start),
                    default=start in mine,
                    description=(
                        None if not around
                        else "Trainee can do this" if picker.side == "trainer"
                        else "Instructor is around"
                    ),
                )
                for hour, start, around in hours
            ],
            row=row,
        )
        self.picker = picker
        self.offered = [start for _, start, _ in hours]

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.picker.cog.save_day(
            interaction, self.picker, self.offered, [int(value) for value in self.values]
        )


class _MoreDaysButton(discord.ui.Button):
    def __init__(self, picker: AvailabilityPicker) -> None:
        arrow = "\N{BLACK RIGHT-POINTING TRIANGLE}"
        super().__init__(
            label=f"More days {arrow} (page {picker.page + 1} of {picker.pages})",
            style=discord.ButtonStyle.secondary,
            row=4,
        )
        self.picker = picker

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.picker.cog.show_page(interaction, self.picker, self.picker.page + 1)


# ------------------------------------------------------ time zone, booking


class TimezoneView(discord.ui.View):
    """Pick a time zone. ``then`` is what to open afterwards: ``mark``, ``date`` or nothing."""

    def __init__(
        self, cog: Any, request_id: int, *, current: str | None, then: str | None
    ) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.request_id = request_id
        self.then = then
        self.add_item(_TimezoneSelect(self, current))

    @staticmethod
    def content(current: str | None) -> str:
        if current:
            return f"Your time zone is **{sched.zone_label(current)}**. Pick another to change it."
        return (
            "Which time zone are you in? Discord does not tell the bot, so it asks once "
            "and remembers."
        )


class _TimezoneSelect(discord.ui.Select):
    def __init__(self, parent: TimezoneView, current: str | None) -> None:
        super().__init__(
            placeholder="Your time zone",
            options=[
                discord.SelectOption(label=label, value=tz, default=tz == current)
                for tz, label in sched.TIMEZONES
            ],
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent_view.cog.save_timezone(interaction, self.parent_view, self.values[0])


class SideChoiceView(discord.ui.View):
    """Whose hours to mark, for someone who is both the trainee and the TI
    on the same request. Rare in real use, routine when testing alone."""

    CONTENT = (
        "You are both the trainee and the instructor on this request. "
        "Whose hours are you marking?"
    )

    def __init__(self, cog: Any, request_id: int) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.request_id = request_id
        self.add_item(_SideButton(self, "trainer", "As instructor"))
        self.add_item(_SideButton(self, "trainee", "As trainee"))


class _SideButton(discord.ui.Button):
    def __init__(self, parent: SideChoiceView, side: str, label: str) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.parent_view = parent
        self.side = side

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent_view.cog.choose_side(interaction, self.parent_view.request_id, self.side)


class RescheduleView(discord.ui.View):
    """How to move a booked session: through the schedule tool, or straight
    to a new time. Either way a reason is asked for first."""

    def __init__(self, cog: Any, request_id: int, *, can_set: bool) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.request_id = request_id
        self.add_item(
            _RescheduleButton(
                self, "tool", "Open Schedule Tool",
                discord.ButtonStyle.primary, "\N{SPIRAL CALENDAR PAD}",
            )
        )
        # Setting a time directly is for the TI and Guest TI, as when booking.
        if can_set:
            self.add_item(
                _RescheduleButton(
                    self, "date", "Schedule a new Time/Date",
                    discord.ButtonStyle.secondary, "\N{CALENDAR}",
                )
            )

    @staticmethod
    def content(when: int) -> str:
        return (
            f"This session is booked for <t:{when}:F>. How do you want to move it?\n"
            "You will be asked for a reason, which is posted in the thread. The current "
            "booking stays until a new time is booked."
        )


class _RescheduleButton(discord.ui.Button):
    def __init__(
        self, parent: RescheduleView, mode: str, label: str,
        style: discord.ButtonStyle, emoji: str,
    ) -> None:
        super().__init__(label=label, style=style, emoji=emoji)
        self.parent_view = parent
        self.mode = mode

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            ReasonModal(self.parent_view.cog, self.parent_view.request_id, self.mode)
        )


class ReasonModal(discord.ui.Modal):
    """Why a booked session is being moved. Required, and posted in the
    thread, so a reschedule is never silent."""

    def __init__(self, cog: Any, request_id: int, mode: str) -> None:
        super().__init__(title="Reschedule", timeout=600)
        self.cog = cog
        self.request_id = request_id
        self.mode = mode
        self.reason = discord.ui.TextInput(
            label="Why does this need rescheduling?",
            style=discord.TextStyle.paragraph,
            placeholder="Posted in the thread for everyone on the request",
            min_length=5,
            max_length=300,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.start_reschedule(
            interaction, self.request_id, self.mode, self.reason.value
        )


class SetDateView(discord.ui.View):
    """Book a time the TI and trainee agreed without the hour lists.

    Day, hour and minute from lists rather than typed, so there is no 05/11
    to read two ways, all on the setter's own clock. Each pick redraws the
    view with the choice kept; the button books once all three are set.
    """

    #: Days offered, counting today. One select holds 25.
    DAYS = 25
    MINUTES = (0, 15, 30, 45)

    def __init__(
        self,
        cog: Any,
        request_id: int,
        *,
        tz: str,
        now: float,
        day: date | None = None,
        hour: int | None = None,
        minute: int | None = None,
    ) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.request_id = request_id
        self.tz = tz
        self.now = now
        self.day, self.hour, self.minute = day, hour, minute

        days = [sched.day_of(tz, offset, now) for offset in range(self.DAYS)]
        names = {0: "Today · ", 1: "Tomorrow · "}
        self.add_item(_DateSelect(self, "day", "Day", [
            discord.SelectOption(
                label=names.get(i, "") + sched.day_label(d), value=d.isoformat(), default=d == day
            )
            for i, d in enumerate(days)
        ]))
        self.add_item(_DateSelect(self, "hour", "Hour", [
            discord.SelectOption(label=f"{h:02d}", value=str(h), default=h == hour)
            for h in range(24)
        ]))
        self.add_item(_DateSelect(self, "minute", "Minute", [
            discord.SelectOption(label=f":{m:02d}", value=str(m), default=m == minute)
            for m in self.MINUTES
        ]))
        when = self.chosen()
        self.add_item(_SetDateButton(self, when))

    def chosen(self) -> int | None:
        """The picked time as Unix time, once day, hour and minute are all set."""
        if self.day is None or self.hour is None or self.minute is None:
            return None
        return sched.at(self.tz, self.day, self.hour, self.minute)

    def content(self) -> str:
        where = f"your time ({sched.zone_label(self.tz)})"
        when = self.chosen()
        if when is not None:
            label = sched.local_label(self.tz, when)
            return f"**{label}**, {where}. Everyone else sees it in theirs."
        if None not in (self.day, self.hour, self.minute):
            return "That time is skipped by a clock change. Pick another."
        return f"When does it run? Pick the day, hour and minute, in {where}."

    def redraw(self, **picked: Any) -> SetDateView:
        state = {"day": self.day, "hour": self.hour, "minute": self.minute, **picked}
        return SetDateView(self.cog, self.request_id, tz=self.tz, now=self.now, **state)


class _DateSelect(discord.ui.Select):
    def __init__(
        self, parent: SetDateView, field: str, placeholder: str,
        options: list[discord.SelectOption],
    ) -> None:
        super().__init__(placeholder=placeholder, options=options)
        self.parent_view = parent
        self.field = field

    async def callback(self, interaction: discord.Interaction) -> None:
        value = self.values[0]
        picked = date.fromisoformat(value) if self.field == "day" else int(value)
        view = self.parent_view.redraw(**{self.field: picked})
        self.parent_view.stop()
        await interaction.response.edit_message(content=view.content(), view=view)


class _SetDateButton(discord.ui.Button):
    def __init__(self, parent: SetDateView, when: int | None) -> None:
        super().__init__(
            label=f"Set for {sched.local_label(parent.tz, when)}" if when else "Set",
            style=discord.ButtonStyle.success,
            emoji="\N{CALENDAR}",
            disabled=when is None,
        )
        self.parent_view = parent
        self.when = when

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent_view.cog.book_time(interaction, self.parent_view.request_id, self.when)


class BookView(discord.ui.View):
    """Pick a start time from the hours both sides are free."""

    def __init__(self, cog: Any, request_id: int, *, starts: list[int], tz: str) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.request_id = request_id
        self.add_item(_BookSelect(self, starts, tz))


class _BookSelect(discord.ui.Select):
    def __init__(self, parent: BookView, starts: list[int], tz: str) -> None:
        super().__init__(
            placeholder=f"Start time, in your time ({sched.zone_label(tz)})",
            options=[
                discord.SelectOption(label=sched.local_label(tz, when), value=str(when))
                for when in starts
            ],
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent_view.cog.book_time(
            interaction, self.parent_view.request_id, int(self.values[0])
        )
