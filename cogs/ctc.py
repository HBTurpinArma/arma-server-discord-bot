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
import datetime
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
from ctc.units import Unit, Units
from ctc.views import (
    AmendView,
    AssignView,
    BadgePickerView,
    LevelView,
    PanelButton,
    ResultView,
    TicketButton,
    UnitPickerView,
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
    "private_threads": False,
    "hide_thread_notices": True,
    "archive_on_award": True,
    "lock_on_award": False,
    "assign_role_id": 0,
    "member_role_id": 0,
    "site_url": "",
    "manager_ids": [],
    "daily_bump": True,
    "nudge_unclaimed_hours": 48,
    "nudge_unawarded_hours": 72,
}


#: Columns added after the table first shipped. ``CREATE TABLE IF NOT EXISTS``
#: leaves an existing table exactly as it is, so the schema file alone only
#: helps a fresh database — a live one needs these adding by hand.
LATER_COLUMNS: dict[str, dict[str, str]] = {
    "ctc_requests": {
        "bump_message_id": "TEXT",
        "unit": "TEXT",
    },
    "ctc_panels": {"unit": "TEXT"},
}


async def ensure_columns(db: aiosqlite.Connection, primary_unit: str) -> None:
    """Add any missing later columns and file old rows under a unit.

    Safe to run on every startup. Rows written before battalions existed have
    no unit, and they all belong to whichever one was already running, so they
    are backfilled to the primary rather than left NULL — a NULL unit would
    drop them out of every queue.
    """
    for table, columns in LATER_COLUMNS.items():
        cursor = await db.execute(f"PRAGMA table_info({table})")
        present = {row[1] for row in await cursor.fetchall()}
        if not present:
            continue  # table not created yet; the schema file will make it
        for name, declaration in columns.items():
            if name not in present:
                await db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
        if "unit" in columns:
            await db.execute(
                f"UPDATE {table} SET unit = ? WHERE unit IS NULL OR unit = ''",
                (primary_unit,),
            )


#: Prefix marking a finished request's thread, so the channel can be read at a
#: glance without opening anything.
CLOSED_PREFIX = "CLOSED: "

#: Discord's hard limit on a thread name.
MAX_THREAD_NAME = 100


def closed_name(name: str) -> str:
    """``name`` with the closed prefix, trimmed to fit and never doubled."""
    if name.startswith(CLOSED_PREFIX):
        return name
    return (CLOSED_PREFIX + name)[:MAX_THREAD_NAME]


def open_name(name: str) -> str:
    """``name`` without the closed prefix.

    The original may have been truncated to make room for the prefix, so this
    does not always restore the exact name it started as — it only guarantees
    the thread no longer reads as closed.
    """
    return name[len(CLOSED_PREFIX):] if name.startswith(CLOSED_PREFIX) else name


def split_mentions(mentions: Sequence[str]) -> tuple[list[int], list[int]]:
    """Split literal Discord mentions into (user ids, role ids).

    Needed because ``allowed_mentions`` wants ids by kind, while the catalogue
    stores whole mentions so a badge can name a person or a role in one field.
    Anything unrecognised is dropped rather than raised on: the catalogue
    already rejects malformed entries at load, and a stray mention should never
    be the reason a request fails to post.
    """
    users: list[int] = []
    roles: list[int] = []
    for mention in mentions:
        digits = "".join(c for c in mention if c.isdigit())
        if not digits:
            continue
        (roles if mention.startswith("<@&") else users).append(int(digits))
    return users, roles


async def unit_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Offer the configured battalions, from the live config rather than a
    hard-coded list — adding a unit must not mean editing the commands."""
    cog = interaction.client.get_cog("ctc")
    if cog is None:  # pragma: no cover - only if the cog is unloaded
        return []
    needle = (current or "").lower()
    return [
        app_commands.Choice(name=unit.name, value=unit.key)
        for unit in cog.units
        if needle in unit.key.lower() or needle in unit.name.lower()
    ][:25]


class CTC(commands.Cog, name="ctc"):
    badge = app_commands.Group(name="badge", description="Combat Training Centre badge requests")

    def __init__(self, bot) -> None:
        self.bot = bot
        self.database: CTCDatabaseManager | None = None
        self.units = Units(self.raw_config(bot), DEFAULTS)

    # ------------------------------------------------------------- wiring

    @staticmethod
    def raw_config(bot) -> dict[str, Any]:
        return (
            bot.config.get("discord", {}).get("combat_training_centre", {})
            if hasattr(bot, "config")
            else {}
        )

    def unit_of(self, row: Any) -> Unit:
        """The battalion a request belongs to.

        Falls back to the primary rather than raising: a row whose unit was
        removed from the config is still real work someone is waiting on, and
        losing it would be worse than showing it in the wrong queue.
        """
        return self.units.get(row["unit"]) or self.units.require(self.units.primary)

    def catalogue_of(self, row: Any) -> catalogue_module.Catalogue:
        return self.unit_of(row).catalogue

    def settings_of(self, row: Any) -> dict[str, Any]:
        return self.unit_of(row).settings

    async def cog_load(self) -> None:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            # Columns first, then the schema. An existing table is left alone by
            # CREATE TABLE IF NOT EXISTS, so an index over a newly added column
            # would be created against a column that is not there yet. On a
            # fresh database there is no table to alter and this is a no-op.
            await ensure_columns(db, self.units.primary)
            with open(SCHEMA_PATH) as handle:
                await db.executescript(handle.read())
            await db.commit()

        self.database = CTCDatabaseManager(
            connection=await aiosqlite.connect(DATABASE_PATH),
            catalogue=lambda unit: (
                self.units.get(unit) or self.units.require(self.units.primary)
            ).catalogue,
        )

        # Ticket buttons carry their request id in the custom_id, so they keep
        # working after a restart without re-registering per-message views.
        self.bot.add_dynamic_items(TicketButton)
        # Panels carry their unit the same way, so one server can pin a
        # separate panel per battalion without them opening each other's
        # badges.
        self.bot.add_dynamic_items(PanelButton)
        self.nudge_loop.start()
        self.bump_loop.start()
        # Settings a panel renders -- the site links, the battalion's name --
        # only change in config.json, and nothing else would ever redraw a
        # pinned panel for them. Refreshing once on startup means a restart is
        # enough, rather than remembering to re-run /badge panel by hand.
        self.bot.loop.create_task(self.refresh_panels_when_ready())
        for unit in self.units:
            self.bot.logger.info(
                f"CTC[{unit.key}]: {len(unit.catalogue.all())} badges loaded, "
                f"{len(unit.catalogue.requestable())} requestable"
            )

    async def cog_unload(self) -> None:
        self.nudge_loop.cancel()
        self.bump_loop.cancel()
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

    @staticmethod
    def role_ids(unit: Unit, setting: str) -> list[int]:
        """Role ids for a setting, which may be a single id or a list of them."""
        value = unit.settings.get(setting)
        if not value:
            return []
        if isinstance(value, (list, tuple)):
            return [int(v) for v in value if v]
        return [int(value)]

    def role_mention(self, unit: Unit, setting: str, fallback: str) -> str:
        """Every configured role for a setting, as a mention string."""
        ids = self.role_ids(unit, setting)
        return " ".join(f"<@&{i}>" for i in ids) if ids else fallback

    def holds_any(self, unit: Unit, user: discord.abc.User, setting: str) -> bool:
        ids = set(self.role_ids(unit, setting))
        return (
            bool(ids)
            and isinstance(user, discord.Member)
            and any(r.id in ids for r in user.roles)
        )

    def manager_ids(self, unit: Unit) -> list[int]:
        """People who staff every battalion without holding any battalion's roles.

        For division-level staff who run the system itself: they need to
        manage a unit they are deliberately not a member of, and inventing a
        Discord role for that would put them in the unit's ping lists.

        Accepts a bare id, a list of them, or pasted mentions.
        """
        value = unit.settings.get("manager_ids")
        if not value:
            return []
        values = value if isinstance(value, (list, tuple)) else [value]
        ids = []
        for entry in values:
            digits = "".join(c for c in str(entry) if c.isdigit())
            if digits:
                ids.append(int(digits))
        return ids

    def is_manager(self, unit: Unit, user: discord.abc.User) -> bool:
        return user.id in self.manager_ids(unit)

    def is_instructor(self, unit: Unit, user: discord.abc.User) -> bool:
        if self.is_manager(unit, user):
            return True
        if not self.role_ids(unit, "instructor_role_id"):
            return True  # unset means "anyone", for local testing only
        return self.holds_any(unit, user, "instructor_role_id")

    def is_extra_trainer(self, unit: Unit, user: discord.abc.User, badge_key: str | None) -> bool:
        """True if this badge names this user, or a role they hold.

        Scoped to one badge on purpose: being the Rotary specialist grants
        nothing on a Medical ticket — and nothing at all in another battalion,
        since the lookup is against that unit's own catalogue.
        """
        mentions = unit.catalogue.extra_trainers(badge_key or "")
        if not mentions:
            return False
        user_ids, extra_roles = split_mentions(mentions)
        if user.id in user_ids:
            return True
        held = {role.id for role in getattr(user, "roles", [])}
        return bool(held & set(extra_roles))

    def can_work(self, unit: Unit, user: discord.abc.User, badge_key: str | None) -> bool:
        """Who may act on a ticket: instructors, plus the badge's own trainers."""
        return self.is_instructor(unit, user) or self.is_extra_trainer(unit, user, badge_key)

    def can_work_row(self, user: discord.abc.User, row: Any) -> bool:
        """As above, for a ticket that already knows its own battalion."""
        return self.can_work(self.unit_of(row), user, row["badge_key"])

    def can_request(self, unit: Unit, user: discord.abc.User) -> bool:
        """Who may raise a request with this battalion.

        Unset means anyone, which is how a single-battalion server has always
        behaved. Set it and only holders may ask — a member of one battalion
        cannot book training with the other.
        """
        if not self.role_ids(unit, "member_role_id"):
            return True
        return self.holds_any(unit, user, "member_role_id") or self.is_instructor(unit, user)

    def can_assign(self, unit: Unit, user: discord.abc.User) -> bool:
        """Who may put someone else's name on a request.

        Falls back to the instructor roles rather than Manage Server, so the
        ability exists out of the box and narrowing it to, say, Training
        Specialists is a deliberate choice rather than the default.
        """
        if self.is_manager(unit, user):
            return True
        if self.role_ids(unit, "assign_role_id"):
            return self.holds_any(unit, user, "assign_role_id")
        return self.is_instructor(unit, user)

    def has_role(self, unit: Unit, user: discord.abc.User, setting: str) -> bool:
        """True if any configured role is held.

        Falls back to the Manage Server permission when no role is set, so a
        partial config still leaves someone able to act.
        """
        if self.is_manager(unit, user):
            return True
        if self.role_ids(unit, setting):
            return self.holds_any(unit, user, setting)
        perms = getattr(user, "guild_permissions", None)
        return bool(perms and perms.manage_guild)

    def can_configure(self, unit: Unit, user: discord.abc.User) -> bool:
        return self.has_role(unit, user, "config_role_id")

    def can_post_panel(self, unit: Unit, user: discord.abc.User) -> bool:
        return self.has_role(unit, user, "panel_role_id")

    # ------------------------------------------------------------- routing

    async def resolve_unit(
        self,
        interaction: discord.Interaction,
        chosen: str | None = None,
        *,
        quiet: bool = False,
    ) -> Unit | None:
        """Which battalion this interaction is about.

        In order: an explicit choice, the request thread it was run in, the
        queue channel it was run in, the only configured unit, then the
        member's own staff roles. Somebody who staffs both battalions is
        genuinely ambiguous, so they are asked rather than guessed at.
        """
        if chosen:
            unit = self.units.find(chosen)
            if unit is None and not quiet:
                names = ", ".join(f"**{u.name}** (`{u.key}`)" for u in self.units)
                await interaction.response.send_message(
                    f'There is no battalion called "{chosen}". Pick one of {names}.',
                    ephemeral=True,
                )
            return unit

        channel = interaction.channel
        if self.database is not None and channel is not None:
            row = await self.database.by_thread(str(channel.id))
            if row is not None:
                return self.unit_of(row)

        parent_id = getattr(channel, "parent_id", None)
        unit = self.units.for_channel(getattr(channel, "id", None)) or self.units.for_channel(
            parent_id
        )
        if unit is not None:
            return unit

        if self.units.only is not None:
            return self.units.only

        unit = self.units.for_member(interaction.user)
        if unit is not None:
            return unit

        if not quiet:
            await interaction.response.send_message(
                "I cannot tell which battalion this is for. Add the `unit` option, "
                "run it in that battalion's badge channel, or use its request panel.",
                ephemeral=True,
            )
        return None

    async def queue_channel(self, unit: Unit) -> discord.abc.GuildChannel | None:
        channel_id = int(unit.settings.get("queue_channel_id") or 0)
        if not channel_id:
            return None
        return self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)

    # ------------------------------------------------------------ posting

    async def post_to_queue(self, rows: Sequence[Any]) -> None:
        for row in rows:
            unit = self.unit_of(row)
            settings = unit.settings
            cat = unit.catalogue

            channel = await self.queue_channel(unit)
            if channel is None:
                self.bot.logger.error(
                    f"CTC[{unit.key}]: queue_channel_id is not configured"
                )
                continue

            is_forum = isinstance(channel, discord.ForumChannel)
            role_ids = self.role_ids(unit, "instructor_role_id")
            base_pings = [f"<@&{i}>" for i in role_ids]
            embed = ticket_embed(cat, row, settings["site_url"])
            view = ticket_view(
                cat, row, award_url=settings["taw_award_url"] or None
            )
            # Member first — the channel sorts into a readable list of who is
            # waiting on what. Levels abbreviated to keep the name scannable;
            # the card inside spells them out in full.
            title = (
                f"{row['member_name']} — "
                f"{cat.label(row['badge_key'], row['levels'], short=True)}"
            )[:100]

            target: Any = channel
            thread_id = None
            message = None

            if is_forum:
                # A forum post is a thread whose starter message is the card.
                post = await channel.create_thread(name=title, embed=embed, view=view)
                target, thread_id, message = post.thread, post.thread.id, post.message
            elif settings["create_threads"]:
                # A private thread is visible only to members added to it, plus
                # anyone holding Manage Threads on the channel — which is how
                # instructors see every request without being added to each one.
                private = bool(settings["private_threads"])
                kwargs: dict[str, Any] = {}
                if private:
                    kwargs["invitable"] = False  # members cannot pull others in
                try:
                    thread = await channel.create_thread(
                        name=title,
                        type=discord.ChannelType.private_thread
                        if private
                        else discord.ChannelType.public_thread,
                        auto_archive_duration=10080,
                        reason=f"Badge request #{row['id']}",
                        **kwargs,
                    )
                    target, thread_id = thread, thread.id
                    # Private threads post no "started a thread" notice.
                    if settings["hide_thread_notices"] and not private:
                        await self.hide_thread_notice(channel, thread)
                except discord.HTTPException as error:
                    # Fall back to the channel rather than losing the request.
                    self.bot.logger.warning(f"CTC: thread failed for #{row['id']}: {error}")

            if message is None:
                message = await target.send(embed=embed, view=view)

            # Some badges can only be run by particular people, so the pings
            # are assembled per badge rather than once for the whole batch.
            extra = cat.extra_trainers(row["badge_key"])
            pings = base_pings + [m for m in extra if m not in base_pings]

            # Subscribe the requester so they follow their own ticket, and the
            # badge's named trainers so they can actually reach a private
            # thread they are allowed to claim. Best effort — a failure here
            # must not lose the request.
            #
            # Only named *people* can be added; Discord has no way to add a
            # role to a thread, so role-based extra trainers still need Manage
            # Threads on the parent channel to see it.
            if thread_id:
                extra_users, _ = split_mentions(extra)
                for uid in (int(row["member_id"]), *extra_users):
                    with contextlib.suppress(discord.HTTPException):
                        await target.add_user(discord.Object(id=uid))
            greeting = (
                f"Hello <@{row['member_id']}>, thanks for raising this request! "
                "We will assist with this as soon as we can."
            )
            if pings:
                # Spoilered so the thread reads as a greeting rather than a wall
                # of role tags. Discord still delivers the notifications.
                greeting += "\n||" + " ".join(pings) + "||"

            ping_users, ping_roles = split_mentions(pings)
            await target.send(
                greeting,
                allowed_mentions=discord.AllowedMentions(
                    roles=[discord.Object(id=i) for i in ping_roles] or False,
                    users=[
                        discord.Object(id=i)
                        for i in {int(row["member_id"]), *ping_users}
                    ],
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
                embed=ticket_embed(
                    self.catalogue_of(row), row, self.settings_of(row)["site_url"]
                ),
                view=ticket_view(
                    self.catalogue_of(row),
                    row,
                    award_url=self.settings_of(row)["taw_award_url"] or None,
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
        """Mark a finished request's thread closed, and archive it.

        The rename happens whether or not archiving is on: the prefix is how a
        finished request is told apart at a glance, and that is worth having
        even when threads are left in the list.
        """
        if not row["thread_id"]:
            return
        try:
            thread = self.bot.get_channel(int(row["thread_id"])) or await self.bot.fetch_channel(
                int(row["thread_id"])
            )
            # Drop the keep-alive before archiving — a finished thread has no
            # use for one, and it cannot be deleted once the thread is closed.
            await self.clear_bump(thread, row["bump_message_id"])
            await self.database.set_bump_message(int(row["id"]), None)

            edits: dict[str, Any] = {}
            name = closed_name(thread.name)
            if name != thread.name:
                edits["name"] = name
            if self.settings_of(row)["archive_on_award"]:
                edits["archived"] = True
                if self.settings_of(row)["lock_on_award"]:
                    edits["locked"] = True
            if edits:
                # One edit call: a name change on an already-archived thread
                # would need it unarchived again first.
                await thread.edit(**edits)
        except discord.HTTPException as error:
            self.bot.logger.warning(f"CTC: could not close thread for #{row['id']}: {error}")

    async def reopen_thread(self, row: Any) -> None:
        """Drop the CLOSED prefix when work starts again."""
        if not row["thread_id"]:
            return
        try:
            thread = self.bot.get_channel(int(row["thread_id"])) or await self.bot.fetch_channel(
                int(row["thread_id"])
            )
            name = open_name(thread.name)
            if name != thread.name:
                await thread.edit(name=name, archived=False)
        except discord.HTTPException as error:
            self.bot.logger.warning(f"CTC: could not reopen thread for #{row['id']}: {error}")

    # ------------------------------------------------------------ commands

    @badge.command(name="request", description="Request one or more badges")
    @app_commands.describe(unit="Which battalion — only needed if it cannot be worked out")
    @app_commands.autocomplete(unit=unit_autocomplete)
    async def badge_request(
        self, interaction: discord.Interaction, unit: str | None = None
    ) -> None:
        chosen = await self.resolve_unit(interaction, unit, quiet=bool(not unit))
        if chosen is None:
            if unit:
                return  # resolve_unit already said the name was wrong
            # Nothing could work it out, so ask rather than refuse: one click
            # and they are where they were trying to get to.
            picker = UnitPickerView(self, interaction.user, self.open_badge_picker)
            await interaction.response.send_message(
                picker.content(), view=picker, ephemeral=True
            )
            return
        await self.open_badge_picker(interaction, chosen, replace=False)

    async def open_badge_picker(
        self, interaction: discord.Interaction, unit: Unit, *, replace: bool = True
    ) -> None:
        """Step two: this battalion's badges, once we know which battalion."""
        if not self.can_request(unit, interaction.user):
            message = f"Badge requests for {unit.name} are open to its members only."
            if replace:
                await interaction.response.edit_message(content=message, view=None)
            else:
                await interaction.response.send_message(message, ephemeral=True)
            return
        view = BadgePickerView(self, interaction.user, unit)
        if replace:
            await interaction.response.edit_message(content=view.content(), view=view)
        else:
            await interaction.response.send_message(view.content(), view=view, ephemeral=True)

    @badge.command(name="catalogue", description="List every badge and its levels")
    @app_commands.describe(unit="Which battalion — only needed if it cannot be worked out")
    @app_commands.autocomplete(unit=unit_autocomplete)
    async def badge_catalogue(
        self, interaction: discord.Interaction, unit: str | None = None
    ) -> None:
        unit = await self.resolve_unit(interaction, unit)
        if unit is None:
            return
        await interaction.response.send_message(
            embed=catalogue_embed(
                unit.catalogue,
                unit.settings["site_url"],
                unit.name if len(self.units) > 1 else None,
            ),
            ephemeral=True,
        )

    @badge.command(name="queue", description="Show open badge requests")
    @app_commands.describe(
        mine="Requests you have claimed",
        open="Requests nobody has claimed yet",
        unit="Which battalion — only needed if it cannot be worked out",
    )
    @app_commands.autocomplete(unit=unit_autocomplete)
    async def badge_queue(
        self,
        interaction: discord.Interaction,
        mine: bool = False,
        open: bool = False,
        unit: str | None = None,
    ) -> None:
        unit = await self.resolve_unit(interaction, unit)
        if unit is None:
            return
        if not self.is_instructor(unit, interaction.user):
            await interaction.response.send_message(
                "Only Training Instructors can view the queue.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        channel = await self.queue_channel(unit)
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
    @app_commands.describe(unit="Which battalion — only needed if it cannot be worked out")
    @app_commands.autocomplete(unit=unit_autocomplete)
    async def badge_stats(
        self, interaction: discord.Interaction, unit: str | None = None
    ) -> None:
        unit = await self.resolve_unit(interaction, unit)
        if unit is None:
            return
        if not self.is_instructor(unit, interaction.user):
            await interaction.response.send_message(
                "Only Training Instructors can view pipeline stats.", ephemeral=True
            )
            return

        stats = await self.database.stats(unit=unit.key)
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
                badge = unit.catalogue.get(key)
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
                    f"#{oldest['id']} "
                    f"{unit.catalogue.label(oldest['badge_key'], oldest['levels'])}"
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
        if not self.can_work_row(interaction.user, row):
            await interaction.response.send_message(
                "Only Training Instructors can amend a request.", ephemeral=True
            )
            return
        if row["status"] == "cancelled":
            await interaction.response.send_message(
                f"Request #{row['id']} was cancelled.", ephemeral=True
            )
            return

        badge = self.catalogue_of(row).get(row["badge_key"])
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
    @app_commands.describe(unit="Which battalion — only needed if it cannot be worked out")
    @app_commands.autocomplete(unit=unit_autocomplete)
    async def badge_config(
        self, interaction: discord.Interaction, unit: str | None = None
    ) -> None:
        unit = await self.resolve_unit(interaction, unit)
        if unit is None:
            return
        if not self.can_configure(unit, interaction.user):
            await interaction.response.send_message(
                "You do not have the role required to edit the catalogue."
                if self.role_ids(unit, "config_role_id")
                else "You need Manage Server to edit the catalogue.",
                ephemeral=True,
            )
            return
        view = ConfigRootView(self, interaction.user, unit=unit)
        await interaction.response.send_message(view.content(), view=view, ephemeral=True)

    def apply_catalogue_edit(self, unit: Unit, fn) -> catalogue_module.Catalogue:
        """Validate, write and hot-reload one unit's catalogue.

        Raises on a bad edit, leaving the file as it was.
        """
        unit.catalogue = catalogue_module.mutate(fn, unit.catalogue_path)
        # This battalion's pinned panels now show a stale catalogue. Refresh
        # in the background so the edit itself stays responsive, and only this
        # unit's — the other battalion's badges did not change.
        self.bot.loop.create_task(self.refresh_panels(unit=unit.key))
        return unit.catalogue

    async def refresh_panels_when_ready(self) -> None:
        """Redraw every tracked panel once the gateway is up.

        cog_load runs before the bot is connected, so fetching the channels
        has to wait. Failures are logged and swallowed: a panel that cannot be
        redrawn is stale, which is not a reason to take the cog down.
        """
        await self.bot.wait_until_ready()
        try:
            await self.refresh_panels()
        except Exception:
            self.bot.logger.exception("CTC: could not refresh panels on startup")

    async def refresh_panels(self, *, unit: str | None = None) -> None:
        """Re-render the catalogue in panels that embed one.

        Scoped to a battalion when given, so editing one unit's badges does not
        rewrite the other unit's pinned messages for nothing.
        """
        if self.database is None:
            return
        for row in await self.database.panels(with_catalogue_only=True, unit=unit):
            try:
                channel = self.bot.get_channel(
                    int(row["channel_id"])
                ) or await self.bot.fetch_channel(int(row["channel_id"]))
                message = await channel.fetch_message(int(row["message_id"]))
                unit = self.units.get(row["unit"]) or self.units.require(self.units.primary)
                await message.edit(
                    **entry_point_payload(
                        unit.catalogue,
                        with_catalogue=True,
                        unit=unit.key,
                        unit_name=unit.name if len(self.units) > 1 else None,
                        site_url=unit.settings["site_url"],
                    )
                )
            except discord.NotFound:
                # Panel deleted; stop tracking it.
                await self.database.remove_panel(row["message_id"])
            except discord.HTTPException as error:
                self.bot.logger.warning(f"CTC: could not refresh panel {row['message_id']}: {error}")

    @badge.command(name="panel", description='Post the "Request a Badge" panel here')
    @app_commands.describe(
        catalogue="Include the full badge list above the button",
        message_id="Update an existing panel instead of posting a new one",
        unit="Which battalion this panel is for",
    )
    @app_commands.autocomplete(unit=unit_autocomplete)
    async def badge_panel(
        self,
        interaction: discord.Interaction,
        catalogue: bool = True,
        message_id: str | None = None,
        unit: str | None = None,
    ) -> None:
        unit = await self.resolve_unit(interaction, unit)
        if unit is None:
            return
        if not self.can_post_panel(unit, interaction.user):
            await interaction.response.send_message(
                "You do not have the role required to post the panel."
                if self.role_ids(unit, "panel_role_id")
                else "You need Manage Server to post the panel.",
                ephemeral=True,
            )
            return

        payload = entry_point_payload(
            unit.catalogue,
            with_catalogue=catalogue,
            unit=unit.key,
            unit_name=unit.name if len(self.units) > 1 else None,
            site_url=unit.settings["site_url"],
        )

        # Adopting an existing panel: rewrite it in place and start tracking it,
        # which is the only way a panel posted before tracking existed can be
        # brought up to date without replacing it and losing its pin.
        if message_id:
            try:
                message = await interaction.channel.fetch_message(int(message_id))
            except (ValueError, discord.NotFound):
                await interaction.response.send_message(
                    f"No message `{message_id}` in this channel. "
                    "Right-click the panel and Copy Message ID, and run this in its channel.",
                    ephemeral=True,
                )
                return
            except discord.HTTPException as error:
                await interaction.response.send_message(f"Could not fetch it: {error}", ephemeral=True)
                return

            if message.author.id != interaction.client.user.id:
                await interaction.response.send_message(
                    "That message was not posted by me, so I cannot edit it.", ephemeral=True
                )
                return

            await message.edit(**payload)
            await self.database.add_panel(
                str(interaction.channel_id), str(message.id), catalogue, unit.key
            )
            await interaction.response.send_message(
                "Panel updated and now tracked, so its catalogue stays current.", ephemeral=True
            )
            return

        message = await interaction.channel.send(**payload)
        await self.database.add_panel(
            str(interaction.channel_id), str(message.id), catalogue, unit.key
        )
        await interaction.response.send_message(
            f"Panel posted{' with the catalogue' if catalogue else ''}. "
            "Pin it so members can always find it."
            + (
                "\n-# The catalogue updates itself whenever a badge changes."
                if catalogue
                else ""
            ),
            ephemeral=True,
        )

    # --------------------------------------------------------- flow actions

    async def submit_request(self, interaction: discord.Interaction, view: LevelView) -> None:
        unit = view.unit
        cat = unit.catalogue

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
            unit=unit.key,
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
        if not own_cancel and not self.can_work_row(interaction.user, row):
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

        if action == "assign":
            if not self.can_assign(self.unit_of(row), interaction.user):
                await interaction.response.send_message(
                    "You cannot set who is working on a request.", ephemeral=True
                )
                return
            view = AssignView(self, row)
            await interaction.response.send_message(
                f"**{self.catalogue_of(row).label(row['badge_key'], row['levels'])}** "
                f"for <@{row['member_id']}>",
                view=view,
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
            embed=ticket_embed(
                self.catalogue_of(updated), updated, self.settings_of(updated)["site_url"]
            ),
            view=ticket_view(
                self.catalogue_of(updated),
                updated,
                award_url=self.settings_of(updated)["taw_award_url"] or None,
            ),
        )
        await self.post_to_thread(updated, f"<@{interaction.user.id}> {verb} this request.")

        if updated["status"] == "awarded":
            awarded = updated["levels_achieved"] or updated["levels"]
            await self.post_to_thread(
                updated,
                f"<@{updated['member_id']}> — "
                f"**{self.catalogue_of(updated).label(updated['badge_key'], awarded)}**"
                " is on your record. Congratulations. \N{MILITARY MEDAL}",
            )
            # Close last — anything sent afterwards would reopen the thread.
            await self.close_thread(updated)
        elif updated["status"] == "cancelled":
            await self.close_thread(updated)

    async def assign_request(
        self, interaction: discord.Interaction, row: Any, member: discord.abc.User
    ) -> None:
        """Put a named person on a request on someone else's behalf."""
        if not self.can_assign(self.unit_of(row), interaction.user):
            await interaction.response.edit_message(
                content="You cannot set who is working on a request.", view=None
            )
            return

        previous = row["instructor_id"]
        name = getattr(member, "display_name", str(member))
        try:
            updated = await self.database.assign(
                int(row["id"]), instructor_id=str(member.id), instructor_name=name
            )
        except TransitionError as error:
            await interaction.response.edit_message(
                content=f"\N{WARNING SIGN} {error}", view=None
            )
            return

        await interaction.response.edit_message(
            content=f"Assigned to {member.mention}.", view=None
        )
        await self.refresh_ticket(updated)

        # Say who did it, so a reassignment is not mistaken for the trainer
        # having claimed it themselves.
        if previous and previous != str(member.id):
            note = (
                f"<@{interaction.user.id}> reassigned this from <@{previous}> "
                f"to {member.mention}."
            )
        else:
            note = f"<@{interaction.user.id}> assigned this to {member.mention}."
        await self.post_to_thread(updated, note)

        # A private thread is invisible to someone who was never added to it,
        # and being handed the work is exactly when that matters.
        if updated["thread_id"]:
            with contextlib.suppress(discord.HTTPException):
                thread = self.bot.get_channel(
                    int(updated["thread_id"])
                ) or await self.bot.fetch_channel(int(updated["thread_id"]))
                await thread.add_user(discord.Object(id=int(member.id)))

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

        cat = self.catalogue_of(updated)
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

        cat = self.catalogue_of(updated)
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
        await self.reopen_thread(updated)
        await self.refresh_ticket(updated)
        await self.post_to_thread(
            updated,
            f"<@{interaction.user.id}> reopened this request. Any previous result has been cleared.",
        )

    # ------------------------------------------------------------- nudges

    @tasks.loop(time=datetime.time(hour=0, minute=0, tzinfo=datetime.timezone.utc))
    async def bump_loop(self) -> None:
        """Keep open request threads from auto-archiving.

        Discord archives a thread after its inactivity window, and a request
        waiting on a member's availability can easily go quiet for longer than
        that. Any message resets the timer, so a daily line is enough.

        Deliberately silent: no mentions, so it keeps threads alive without
        notifying anybody.

        Yesterday's bump is deleted only *after* today's has landed, so a
        thread is never briefly without one and the inactivity clock is reset
        by a message that genuinely exists. Posting and immediately deleting
        would leave no clutter either, but whether that still resets the clock
        is Discord's business and not something worth betting the queue on.
        """
        if self.database is None:
            return
        for row in await self.database.queue():
            if not row["thread_id"] or not self.settings_of(row)["daily_bump"]:
                continue
            try:
                thread = self.bot.get_channel(
                    int(row["thread_id"])
                ) or await self.bot.fetch_channel(int(row["thread_id"]))
                # An archived thread is either finished or already lost; posting
                # would silently unarchive it, so leave it be.
                if getattr(thread, "archived", False):
                    continue
                message = await thread.send(
                    "Bump to keep alive",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException as error:
                self.bot.logger.warning(f"CTC: could not bump #{row['id']}: {error}")
                continue

            await self.clear_bump(thread, row["bump_message_id"])
            await self.database.set_bump_message(int(row["id"]), str(message.id))

    async def clear_bump(self, thread: Any, message_id: str | None) -> None:
        """Delete a previous keep-alive message, if it is still there.

        Best effort: someone may have tidied it away by hand, and a bump that
        cannot be deleted is untidy rather than broken.
        """
        if not message_id:
            return
        with contextlib.suppress(discord.HTTPException):
            await (await thread.fetch_message(int(message_id))).delete()

    @bump_loop.before_loop
    async def before_bump_loop(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(hours=1)
    async def nudge_loop(self) -> None:
        """Chase work going stale, in the relevant ticket's own thread."""
        if self.database is None:
            return
        # Each battalion sets its own thresholds and tags its own instructors,
        # so the sweep runs once per unit rather than once overall.
        for unit in self.units:
            role_ids = self.role_ids(unit, "instructor_role_id")
            mention = self.role_mention(unit, "instructor_role_id", "Instructors")

            unclaimed_hours = int(unit.settings["nudge_unclaimed_hours"])
            for row in await self.database.stale(
                "requested", unclaimed_hours, unit=unit.key
            ):
                sent = await self._nudge(
                    row,
                    f"{mention} — still unclaimed after {unclaimed_hours}h: "
                    f"**{unit.catalogue.label(row['badge_key'], row['levels'])}** "
                    f"for <@{row['member_id']}>.",
                    role_ids=role_ids,
                )
                if sent:
                    await self.database.mark_nudged(int(row["id"]))

            await self._nudge_unawarded(unit)

    async def _nudge_unawarded(self, unit: Unit) -> None:
        unawarded_hours = int(unit.settings["nudge_unawarded_hours"])
        for row in await self.database.stale("completed", unawarded_hours, unit=unit.key):
            sent = await self._nudge(
                row,
                f"<@{row['instructor_id']}> — this was run {unawarded_hours}h ago but isn't "
                "marked awarded on taw.net yet.",
                user_id=row["instructor_id"],
            )
            if sent:
                await self.database.mark_nudged(int(row["id"]))

    async def _nudge(
        self,
        row: Any,
        content: str,
        *,
        role_ids: Sequence[int] = (),
        user_id: str | None = None,
    ) -> bool:
        allowed = discord.AllowedMentions(
            roles=[discord.Object(id=i) for i in role_ids] if role_ids else False,
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
