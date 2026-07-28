"""Combat Training Centre — badge requests and the training queue.

Replaces the Google Form → Sheet → Discord pipeline with an intake picker and a
queue instructors work from in place.

Each request becomes its own thread, created standalone so nothing lands in the
channel feed, with the ticket card as the first message inside it. The channel
is a list of open requests rather than a wall of embeds.

The cog owns its own SQLite file and registers its own persistent components in
``cog_load``, so ``bot.py`` needs no changes.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Sequence
from typing import Any

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks

from ctc import CTCDatabaseManager, TransitionError
from ctc import catalogue as catalogue_module
from ctc.config_views import ConfigRootView
from ctc.views import (
    AmendView,
    BadgePickerView,
    LevelView,
    PanelView,
    ResultView,
    TicketButton,
    catalogue_embed,
    entry_point_payload,
    relative,
    style_for,
    ticket_embed,
    ticket_view,
)

PACKAGE_DIR = os.path.join(os.path.realpath(os.path.dirname(os.path.dirname(__file__))), "ctc")
DATABASE_PATH = os.path.join(PACKAGE_DIR, "database.db")
SCHEMA_PATH = os.path.join(PACKAGE_DIR, "schema.sql")

DEFAULTS: dict[str, Any] = {
    "queue_channel_id": 0,
    "instructor_role_id": 0,
    "config_role_id": 0,
    "panel_role_id": 0,
    "taw_award_url": "",
    "create_threads": True,
    "hide_thread_notices": True,
    "archive_on_award": True,
    "lock_on_award": False,
    "nudge_unclaimed_hours": 48,
    "nudge_unawarded_hours": 72,
}


class CTC(commands.Cog, name="ctc"):
    badge = app_commands.Group(name="badge", description="Combat Training Centre badge requests")

    def __init__(self, bot) -> None:
        self.bot = bot
        self.database: CTCDatabaseManager | None = None
        self._catalogue = catalogue_module.load()

    # ------------------------------------------------------------- wiring

    @property
    def catalogue(self) -> catalogue_module.Catalogue:
        return self._catalogue

    @property
    def settings(self) -> dict[str, Any]:
        configured = (
            self.bot.config.get("discord", {}).get("combat_training_centre", {})
            if hasattr(self.bot, "config")
            else {}
        )
        return {**DEFAULTS, **configured}

    async def cog_load(self) -> None:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            with open(SCHEMA_PATH) as handle:
                await db.executescript(handle.read())
            await db.commit()

        self.database = CTCDatabaseManager(
            connection=await aiosqlite.connect(DATABASE_PATH),
            catalogue=lambda: self._catalogue,
        )

        # Ticket buttons carry their request id in the custom_id, so they keep
        # working after a restart without re-registering per-message views.
        self.bot.add_dynamic_items(TicketButton)
        # The pinned panel has a fixed id, so it is a plain persistent view.
        self.bot.add_view(PanelView())
        self.nudge_loop.start()
        self.bot.logger.info(
            f"CTC: {len(self._catalogue.all())} badges loaded, "
            f"{len(self._catalogue.requestable())} requestable"
        )

    async def cog_unload(self) -> None:
        self.nudge_loop.cancel()
        # Unregister so a cog reload does not double-register the handler.
        self.bot.remove_dynamic_items(TicketButton)
        if self.database is not None:
            await self.database.connection.close()

    async def active_threads(self, channel: Any) -> list[discord.Thread]:
        """Open threads in the queue channel, newest last.

        Fetched rather than read from ``channel.threads``, which is a cache that
        can be cold or incomplete after a restart. Falls back to the cache if
        the fetch fails.
        """
        try:
            threads = [t for t in await channel.guild.active_threads() if t.parent_id == channel.id]
        except discord.HTTPException as error:
            self.bot.logger.warning(f"CTC: could not fetch active threads: {error}")
            threads = list(channel.threads)
        return sorted(threads, key=lambda t: t.created_at or discord.utils.utcnow())

    # -------------------------------------------------------- permissions

    def is_instructor(self, user: discord.abc.User) -> bool:
        role_id = int(self.settings["instructor_role_id"] or 0)
        if not role_id:
            return True  # unset means "anyone", for local testing only
        return isinstance(user, discord.Member) and any(r.id == role_id for r in user.roles)

    def has_role(self, user: discord.abc.User, setting: str) -> bool:
        """True if the configured role is held.

        Falls back to the Manage Server permission when the role is unset, so a
        partial config still leaves someone able to act.
        """
        role_id = int(self.settings[setting] or 0)
        if role_id:
            return isinstance(user, discord.Member) and any(r.id == role_id for r in user.roles)
        perms = getattr(user, "guild_permissions", None)
        return bool(perms and perms.manage_guild)

    def can_configure(self, interaction: discord.Interaction) -> bool:
        return self.has_role(interaction.user, "config_role_id")

    def can_post_panel(self, interaction: discord.Interaction) -> bool:
        return self.has_role(interaction.user, "panel_role_id")

    async def queue_channel(self) -> discord.abc.GuildChannel | None:
        channel_id = int(self.settings["queue_channel_id"] or 0)
        if not channel_id:
            return None
        return self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)

    # ------------------------------------------------------------ posting

    async def post_to_queue(self, rows: Sequence[Any]) -> None:
        channel = await self.queue_channel()
        if channel is None:
            self.bot.logger.error("CTC: queue_channel_id is not configured")
            return

        settings = self.settings
        is_forum = isinstance(channel, discord.ForumChannel)
        role_id = int(settings["instructor_role_id"] or 0)

        for row in rows:
            embed = ticket_embed(self.catalogue, row)
            view = ticket_view(
                self.catalogue, row, award_url=settings["taw_award_url"] or None
            )
            # Member first — the channel sorts into a readable list of who is
            # waiting on what. Levels abbreviated to keep the name scannable;
            # the card inside spells them out in full.
            title = (
                f"{row['member_name']} — "
                f"{self.catalogue.label(row['badge_key'], row['levels'], short=True)}"
            )[:100]

            target: Any = channel
            thread_id = None
            message = None

            if is_forum:
                # A forum post is a thread whose starter message is the card.
                post = await channel.create_thread(name=title, embed=embed, view=view)
                target, thread_id, message = post.thread, post.thread.id, post.message
            elif settings["create_threads"]:
                try:
                    thread = await channel.create_thread(
                        name=title,
                        type=discord.ChannelType.public_thread,
                        auto_archive_duration=10080,
                        reason=f"Badge request #{row['id']}",
                    )
                    target, thread_id = thread, thread.id
                    if settings["hide_thread_notices"]:
                        await self.hide_thread_notice(channel, thread)
                except discord.HTTPException as error:
                    # Fall back to the channel rather than losing the request.
                    self.bot.logger.warning(f"CTC: thread failed for #{row['id']}: {error}")

            if message is None:
                message = await target.send(embed=embed, view=view)

            # Subscribe the requester so they follow their own ticket. Best
            # effort — a failure here must not lose the request.
            if thread_id:
                with contextlib.suppress(discord.HTTPException):
                    await target.add_user(discord.Object(id=int(row["member_id"])))

            mention = f"<@&{role_id}>" if role_id else "A Training Instructor"
            await target.send(
                f"Hello <@{row['member_id']}>, thanks for raising this request! "
                f"{mention} will be able to assist with this when they can.",
                allowed_mentions=discord.AllowedMentions(
                    roles=[discord.Object(id=role_id)] if role_id else False,
                    users=[discord.Object(id=int(row["member_id"]))],
                ),
            )

            await self.database.set_queue_message(
                int(row["id"]), str(target.id), str(message.id), str(thread_id) if thread_id else None
            )

    async def hide_thread_notice(self, channel: Any, thread: discord.Thread) -> None:
        """Delete Discord's "started a thread" system message from the parent."""
        try:
            async for message in channel.history(limit=10):
                if message.type is discord.MessageType.thread_created and (
                    (message.thread and message.thread.id == thread.id)
                    or message.content == thread.name
                ):
                    await message.delete()
                    return
        except discord.HTTPException as error:
            self.bot.logger.warning(f"CTC: could not hide thread notice: {error}")

    async def refresh_ticket(self, row: Any) -> None:
        if not row["queue_channel_id"] or not row["queue_message_id"]:
            return
        try:
            channel = self.bot.get_channel(int(row["queue_channel_id"])) or await self.bot.fetch_channel(
                int(row["queue_channel_id"])
            )
            message = await channel.fetch_message(int(row["queue_message_id"]))
            await message.edit(
                embed=ticket_embed(self.catalogue, row),
                view=ticket_view(
                    self.catalogue, row, award_url=self.settings["taw_award_url"] or None
                ),
            )
        except discord.HTTPException as error:
            self.bot.logger.warning(f"CTC: could not refresh #{row['id']}: {error}")

    async def post_to_thread(self, row: Any, content: str) -> None:
        if not row["thread_id"]:
            return
        try:
            thread = self.bot.get_channel(int(row["thread_id"])) or await self.bot.fetch_channel(
                int(row["thread_id"])
            )
            await thread.send(content)
        except discord.HTTPException as error:
            self.bot.logger.warning(f"CTC: could not post to thread for #{row['id']}: {error}")

    async def close_thread(self, row: Any) -> None:
        """Archive a finished request's thread so the channel lists live work only."""
        if not row["thread_id"] or not self.settings["archive_on_award"]:
            return
        try:
            thread = self.bot.get_channel(int(row["thread_id"])) or await self.bot.fetch_channel(
                int(row["thread_id"])
            )
            if self.settings["lock_on_award"]:
                await thread.edit(locked=True, archived=True)
            else:
                await thread.edit(archived=True)
        except discord.HTTPException as error:
            self.bot.logger.warning(f"CTC: could not archive thread for #{row['id']}: {error}")

    # ------------------------------------------------------------ commands

    @badge.command(name="request", description="Request one or more badges")
    async def badge_request(self, interaction: discord.Interaction) -> None:
        view = BadgePickerView(self, interaction.user)
        await interaction.response.send_message(view.content(), view=view, ephemeral=True)

    @badge.command(name="catalogue", description="List every badge and its levels")
    async def badge_catalogue(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=catalogue_embed(self.catalogue), ephemeral=True
        )

    @badge.command(name="queue", description="Show open badge requests")
    @app_commands.describe(
        mine="Requests you have claimed", open="Requests nobody has claimed yet"
    )
    async def badge_queue(
        self, interaction: discord.Interaction, mine: bool = False, open: bool = False
    ) -> None:
        if not self.is_instructor(interaction.user):
            await interaction.response.send_message(
                "Only Training Instructors can view the queue.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        channel = await self.queue_channel()
        if channel is None:
            await interaction.followup.send("Queue channel is not configured.", ephemeral=True)
            return

        # Live threads are the source of truth for what is still open — a thread
        # that was archived or deleted is done, whatever the database thinks.
        threads = await self.active_threads(channel)
        entries = [(t, await self.database.by_thread(str(t.id))) for t in threads]

        def is_mine(entry) -> bool:
            _, row = entry
            return bool(row and row["instructor_id"] == str(interaction.user.id))

        def is_unclaimed(entry) -> bool:
            _, row = entry
            return bool(row and not row["instructor_id"])

        if not mine and not open:
            filtered = entries
        else:
            filtered = [e for e in entries if (mine and is_mine(e)) or (open and is_unclaimed(e))]

        if not filtered:
            if mine and not open:
                message = "You have nothing claimed. \N{PARTY POPPER}"
            elif open and not mine:
                message = "Nothing unclaimed — every request has an instructor. \N{PARTY POPPER}"
            else:
                message = "No open requests. \N{PARTY POPPER}"
            await interaction.followup.send(message, ephemeral=True)
            return

        def render(entry) -> str:
            thread, row = entry
            if row is None:
                return f"\N{WHITE SMALL SQUARE} {thread.mention} *(no matching request)*"
            _, emoji, _ = style_for(row["status"])
            who = f" · <@{row['instructor_id']}>" if row["instructor_id"] else ""
            return (
                f"{emoji} {thread.mention} — <@{row['member_id']}>{who} · "
                f"{relative(row['created_at'])}"
            )

        embed = discord.Embed(colour=0x5865F2)
        embed.set_footer(text=f"{len(filtered)} open thread{'' if len(filtered) == 1 else 's'}")

        if mine and open:
            embed.title = "Your queue"
            claimed = [e for e in filtered if is_mine(e)]
            unclaimed = [e for e in filtered if is_unclaimed(e)]
            if claimed:
                embed.add_field(
                    name=f"Claimed by you ({len(claimed)})",
                    value="\n".join(render(e) for e in claimed)[:1024],
                    inline=False,
                )
            if unclaimed:
                embed.add_field(
                    name=f"Unclaimed ({len(unclaimed)})",
                    value="\n".join(render(e) for e in unclaimed)[:1024],
                    inline=False,
                )
        else:
            embed.title = (
                "Claimed by you" if mine else "Unclaimed requests" if open else "Open badge requests"
            )
            embed.description = "\n".join(render(e) for e in filtered)[:4000]

        await interaction.followup.send(embed=embed, ephemeral=True)

    @badge.command(name="stats", description="Pipeline stats and instructor load")
    async def badge_stats(self, interaction: discord.Interaction) -> None:
        if not self.is_instructor(interaction.user):
            await interaction.response.send_message(
                "Only Training Instructors can view pipeline stats.", ephemeral=True
            )
            return

        stats = await self.database.stats()
        embed = discord.Embed(colour=0x5865F2, title="Badge pipeline")

        by_status = stats["by_status"]
        embed.add_field(
            name="By status",
            value="\n".join(
                f"{style_for(r['status'])[1]} {r['status']}: **{r['n']}**" for r in by_status
            )
            or "*No requests yet*",
            inline=False,
        )

        if stats["by_instructor"]:
            embed.add_field(
                name="Instructor load",
                value="\n".join(
                    f"**{r['name']}** — {r['open']} open, {r['awarded']} awarded"
                    for r in stats["by_instructor"][:15]
                ),
                inline=False,
            )

        if stats["turnaround"]:
            def badge_name(key: str) -> str:
                badge = self.catalogue.get(key)
                return badge.name if badge else key

            embed.add_field(
                name="Avg days to award",
                value="\n".join(
                    f"{badge_name(r['badge_key'])} — **{r['avg_days']}d** ({r['n']})"
                    for r in stats["turnaround"][:15]
                ),
                inline=False,
            )

        oldest = stats["oldest_open"]
        if oldest:
            embed.add_field(
                name="Oldest open",
                value=(
                    f"#{oldest['id']} {self.catalogue.label(oldest['badge_key'], oldest['levels'])}"
                    f" — {relative(oldest['created_at'])}"
                ),
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @badge.command(
        name="amend", description="Change what the request in this thread is asking for"
    )
    async def badge_amend(self, interaction: discord.Interaction) -> None:
        row = await self.database.by_thread(str(interaction.channel_id))
        if row is None:
            await interaction.response.send_message(
                "Run this inside a badge request thread — it amends that request.", ephemeral=True
            )
            return
        if not self.is_instructor(interaction.user):
            await interaction.response.send_message(
                "Only Training Instructors can amend a request.", ephemeral=True
            )
            return
        if row["status"] == "cancelled":
            await interaction.response.send_message(
                f"Request #{row['id']} was cancelled.", ephemeral=True
            )
            return

        badge = self.catalogue.get(row["badge_key"])
        if (
            badge is not None
            and not badge.needs_level_choice
            and row["status"] not in ("completed", "awarded")
        ):
            await interaction.response.send_message(
                f"**{badge.name}** has no requested levels — there's nothing to change. "
                "Cancel it instead if it shouldn't be here.",
                ephemeral=True,
            )
            return

        view = AmendView(self, row, interaction.user)
        await interaction.response.send_message(view.content(), view=view, ephemeral=True)

    @badge.command(name="config", description="Add, edit or retire badges")
    async def badge_config(self, interaction: discord.Interaction) -> None:
        if not self.can_configure(interaction):
            role_id = int(self.settings["config_role_id"] or 0)
            await interaction.response.send_message(
                "You do not have the role required to edit the catalogue."
                if role_id
                else "You need Manage Server to edit the catalogue.",
                ephemeral=True,
            )
            return
        view = ConfigRootView(self, interaction.user)
        await interaction.response.send_message(view.content(), view=view, ephemeral=True)

    def apply_catalogue_edit(self, fn) -> catalogue_module.Catalogue:
        """Validate, write and hot-reload the catalogue. Raises on a bad edit."""
        self._catalogue = catalogue_module.mutate(fn)
        return self._catalogue

    @badge.command(name="panel", description='Post the "Request a Badge" panel here')
    @app_commands.describe(catalogue="Include the full badge list above the button")
    async def badge_panel(self, interaction: discord.Interaction, catalogue: bool = True) -> None:
        if not self.can_post_panel(interaction):
            role_id = int(self.settings["panel_role_id"] or 0)
            await interaction.response.send_message(
                "You do not have the role required to post the panel."
                if role_id
                else "You need Manage Server to post the panel.",
                ephemeral=True,
            )
            return

        payload = entry_point_payload(self.catalogue, with_catalogue=catalogue)
        await interaction.channel.send(**payload)
        await interaction.response.send_message(
            f"Panel posted{' with the catalogue' if catalogue else ''}. "
            "Pin it so members can always find it."
            + (
                "\n-# The catalogue is a snapshot — re-run `/badge panel` after changing badges."
                if catalogue
                else ""
            ),
            ephemeral=True,
        )

    # --------------------------------------------------------- flow actions

    async def submit_request(self, interaction: discord.Interaction, view: LevelView) -> None:
        cat = self.catalogue

        # The picker never offers a WIP badge, but a stale form could carry one.
        wip = [k for k in view.badge_keys if (b := cat.get(k)) and b.wip]
        if wip:
            names = ", ".join(cat.get(k).name for k in wip)
            await interaction.response.edit_message(
                content=(
                    f"\N{WARNING SIGN} {names} "
                    f"{'is' if len(wip) == 1 else 'are'} still in development and can't be "
                    "requested yet. Nothing was submitted."
                ),
                view=None,
            )
            return

        items = []
        for key in view.badge_keys:
            badge = cat.get(key)
            if badge is None:
                continue
            levels = (
                cat.sort_levels(key, view.levels.get(key, [])) if badge.needs_level_choice else []
            )
            items.append((key, levels))

        rows = await self.database.create_group(
            guild_id=str(interaction.guild_id),
            member_id=str(interaction.user.id),
            member_name=getattr(interaction.user, "display_name", str(interaction.user)),
            notes=view.notes,
            items=items,
        )

        listed = "\n".join(
            f"• {cat.label(r['badge_key'], r['levels'])} *(#{r['id']})*" for r in rows
        )
        await interaction.response.edit_message(
            content=(
                f"\N{WHITE HEAVY CHECK MARK} Submitted **{len(rows)}** "
                f"request{'' if len(rows) == 1 else 's'}:\n{listed}\n\n"
                "An instructor will pick these up shortly."
            ),
            view=None,
        )
        await self.post_to_queue(rows)

    async def handle_ticket_action(
        self, interaction: discord.Interaction, action: str, request_id: int
    ) -> None:
        row = await self.database.by_id(request_id)
        if row is None:
            await interaction.response.send_message(
                f"Request #{request_id} no longer exists.", ephemeral=True
            )
            return

        own_cancel = action == "cancel" and row["member_id"] == str(interaction.user.id)
        if not own_cancel and not self.is_instructor(interaction.user):
            await interaction.response.send_message(
                "Only Training Instructors can do that.", ephemeral=True
            )
            return

        # Whoever claimed it owns it, though any instructor can release it.
        if action in ("complete", "result", "award") and row["instructor_id"] != str(
            interaction.user.id
        ):
            await interaction.response.send_message(
                f"This is claimed by <@{row['instructor_id']}>. Ask them to release it first.",
                ephemeral=True,
            )
            return

        if action == "result":
            view = ResultView(self, row, interaction.user)
            await interaction.response.send_message(view.content(), view=view, ephemeral=True)
            return

        if action == "reopen":
            view = AmendView(self, row, interaction.user)
            await interaction.response.send_message(view.content(), view=view, ephemeral=True)
            return

        targets = {
            "claim": ("claimed", "claimed"),
            "release": ("requested", "released"),
            "complete": ("completed", "marked as run"),
            "award": ("awarded", "awarded on taw.net"),
            "cancel": ("cancelled", "cancelled"),
        }
        if action not in targets:
            await interaction.response.send_message("Unknown action.", ephemeral=True)
            return

        nxt, verb = targets[action]
        name = getattr(interaction.user, "display_name", str(interaction.user))
        try:
            updated = await self.database.transition(
                request_id,
                nxt,
                instructor_id=str(interaction.user.id) if nxt == "claimed" else None,
                instructor_name=name if nxt == "claimed" else None,
            )
        except TransitionError as error:
            await interaction.response.send_message(f"\N{WARNING SIGN} {error}", ephemeral=True)
            return

        await interaction.response.edit_message(
            embed=ticket_embed(self.catalogue, updated),
            view=ticket_view(
                self.catalogue, updated, award_url=self.settings["taw_award_url"] or None
            ),
        )
        await self.post_to_thread(updated, f"<@{interaction.user.id}> {verb} this request.")

        if updated["status"] == "awarded":
            awarded = updated["levels_achieved"] or updated["levels"]
            await self.post_to_thread(
                updated,
                f"<@{updated['member_id']}> — **{self.catalogue.label(updated['badge_key'], awarded)}**"
                " is on your record. Congratulations. \N{MILITARY MEDAL}",
            )
            # Close last — anything sent afterwards would reopen the thread.
            await self.close_thread(updated)
        elif updated["status"] == "cancelled":
            await self.close_thread(updated)

    async def record_result(self, interaction: discord.Interaction, view: ResultView) -> None:
        try:
            updated = await self.database.transition(
                view.request_id, "completed", levels_achieved=view.levels, variant=view.variant
            )
        except TransitionError as error:
            await interaction.response.edit_message(
                content=f"\N{WARNING SIGN} {error}", view=None
            )
            return

        cat = self.catalogue
        achieved = cat.parse_levels(updated["badge_key"], updated["levels_achieved"])
        requested = cat.parse_levels(updated["badge_key"], updated["levels"])
        listed = ", ".join(cat.level_name(lvl) for lvl in achieved) if achieved else "none"
        ran = f" ({updated['variant']})" if updated["variant"] else ""

        await interaction.response.edit_message(
            content=f"Recorded{ran}: **{listed}**.", view=None
        )
        await self.refresh_ticket(updated)

        if requested:
            failed = ", ".join(cat.level_name(lvl) for lvl in requested if lvl not in achieved)
            await self.post_to_thread(
                updated,
                f"<@{interaction.user.id}> logged a partial result — passed: **{listed}**, "
                f"not passed: **{failed}**.",
            )
        else:
            await self.post_to_thread(
                updated,
                f"<@{interaction.user.id}> recorded the result{ran} — earned: **{listed}**.",
            )

    async def save_amendment(self, interaction: discord.Interaction, view: AmendView) -> None:
        name = getattr(interaction.user, "display_name", str(interaction.user))
        try:
            updated = await self.database.amend_request(
                view.request_id,
                levels=view.levels,
                actor_id=str(interaction.user.id),
                actor_name=name,
            )
        except TransitionError as error:
            await interaction.response.edit_message(
                content=f"\N{WARNING SIGN} {error}", view=None
            )
            return

        cat = self.catalogue
        before = ", ".join(cat.level_name(lvl) for lvl in view.original) or "none"
        after = ", ".join(
            cat.level_name(lvl) for lvl in cat.parse_levels(updated["badge_key"], updated["levels"])
        )
        await interaction.response.edit_message(content=f"Updated — now needs **{after}**.", view=None)
        await self.refresh_ticket(updated)
        await self.post_to_thread(
            updated, f"<@{interaction.user.id}> amended this request — **{before}** → **{after}**."
        )

    async def reopen_request(self, interaction: discord.Interaction, view: AmendView) -> None:
        name = getattr(interaction.user, "display_name", str(interaction.user))
        try:
            updated = await self.database.reopen(
                view.request_id, instructor_id=str(interaction.user.id), instructor_name=name
            )
        except TransitionError as error:
            await interaction.response.edit_message(
                content=f"\N{WARNING SIGN} {error}", view=None
            )
            return

        await interaction.response.edit_message(
            content=f"Reopened — request #{view.request_id} is back with you to run again.",
            view=None,
        )
        await self.refresh_ticket(updated)
        await self.post_to_thread(
            updated,
            f"<@{interaction.user.id}> reopened this request. Any previous result has been cleared.",
        )

    # ------------------------------------------------------------- nudges

    @tasks.loop(hours=1)
    async def nudge_loop(self) -> None:
        """Chase work going stale, in the relevant ticket's own thread."""
        if self.database is None:
            return
        settings = self.settings
        role_id = int(settings["instructor_role_id"] or 0)
        mention = f"<@&{role_id}>" if role_id else "Instructors"

        unclaimed_hours = int(settings["nudge_unclaimed_hours"])
        for row in await self.database.stale("requested", unclaimed_hours):
            sent = await self._nudge(
                row,
                f"{mention} — still unclaimed after {unclaimed_hours}h: "
                f"**{self.catalogue.label(row['badge_key'], row['levels'])}** "
                f"for <@{row['member_id']}>.",
                role_id=role_id,
            )
            if sent:
                await self.database.mark_nudged(int(row["id"]))

        unawarded_hours = int(settings["nudge_unawarded_hours"])
        for row in await self.database.stale("completed", unawarded_hours):
            sent = await self._nudge(
                row,
                f"<@{row['instructor_id']}> — this was run {unawarded_hours}h ago but isn't "
                "marked awarded on taw.net yet.",
                user_id=row["instructor_id"],
            )
            if sent:
                await self.database.mark_nudged(int(row["id"]))

    async def _nudge(
        self, row: Any, content: str, *, role_id: int = 0, user_id: str | None = None
    ) -> bool:
        allowed = discord.AllowedMentions(
            roles=[discord.Object(id=role_id)] if role_id else False,
            users=[discord.Object(id=int(user_id))] if user_id else False,
        )
        for channel_id in (row["thread_id"], row["queue_channel_id"]):
            if not channel_id:
                continue
            try:
                channel = self.bot.get_channel(int(channel_id)) or await self.bot.fetch_channel(
                    int(channel_id)
                )
                await channel.send(content, allowed_mentions=allowed)
                return True
            except discord.HTTPException:
                continue
        return False

    @nudge_loop.before_loop
    async def before_nudge_loop(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot) -> None:
    await bot.add_cog(CTC(bot))
