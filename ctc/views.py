"""Discord components for the Combat Training Centre badge flow.

Ticket buttons are ``DynamicItem`` so they keep working after a bot restart —
their state lives in the custom_id, not in a registered view instance. The
request, result, amend and config screens are ephemeral and short-lived, so
they are ordinary views built fresh each time.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

import discord

from .catalogue import Badge, Catalogue

# An action row holds 5 buttons or a single select, and a message holds 5 rows.
# The level step needs a row for its buttons, so 4 level selects fit per page.
LEVELS_PER_PAGE = 4

#: Cap on badges per submission. Stated on the picker, not just enforced.
MAX_BADGES_PER_REQUEST = 5

COLOUR = 0x5865F2

STATUS_STYLE: dict[str, tuple[int, str, str]] = {
    "requested": (0x5865F2, "\N{INBOX TRAY}", "Awaiting instructor"),
    "claimed": (0xFEE75C, "\N{RAISED HAND}", "Claimed"),
    "completed": (0xEB8C34, "\N{DIRECT HIT}", "Run — awaiting taw.net"),
    "awarded": (0x57F287, "\N{WHITE HEAVY CHECK MARK}", "Awarded"),
    "cancelled": (0x99AAB5, "\N{NO ENTRY SIGN}", "Cancelled"),
}


def style_for(status: str) -> tuple[int, str, str]:
    return STATUS_STYLE.get(status, STATUS_STYLE["requested"])


def relative(stamp: str | None) -> str:
    """SQLite writes naive UTC; Discord wants an epoch for its relative format."""
    if not stamp:
        return "unknown"
    try:
        parsed = datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)
    except ValueError:
        return stamp
    return f"<t:{int(parsed.timestamp())}:R>"


def describe_kind(badge: Badge) -> str:
    return {"timed": "Timed test", "graded": "Graded", "tab": "Tab"}[badge.kind]


# ----------------------------------------------------------------- embeds


def catalogue_embed(cat: Catalogue) -> discord.Embed:
    """The badge list, grouped by category. Shared by /badge catalogue and the panel."""
    embed = discord.Embed(colour=COLOUR, title="\N{MILITARY MEDAL} Badge catalogue")

    grouped: dict[str, list[Badge]] = {}
    for badge in cat.all():
        grouped.setdefault(badge.category, []).append(badge)

    # Full-width rows rather than Discord's default three ragged columns, and
    # short level codes so no badge wraps onto a second line.
    pending: list[str] = []

    for key, items in grouped.items():
        category = cat.category(key)
        lines = []
        for badge in items:
            if badge.wip:
                pending.append(badge.name)
                continue

            available = badge.available_levels
            if not badge.has_levels:
                line = f"**{badge.name}** Tab"
            elif not available:
                pending.append(badge.name)
                continue
            else:
                # Show the whole ladder and strike what cannot be run yet, so
                # the shape of the progression stays visible.
                ladder = " / ".join(
                    lvl if lvl in available else f"~~{lvl}~~" for lvl in badge.levels
                )
                line = f"**{badge.name}** {ladder}"
                if badge.timed:
                    line += " · by result"
            lines.append(line)

        if lines:
            embed.add_field(
                name=f"{category['emoji']} {category['name']}",
                value="\n".join(lines),
                inline=False,
            )

    if pending:
        embed.add_field(
            name="In development",
            value="Not yet requestable: " + ", ".join(sorted(pending)),
            inline=False,
        )

    key = [
        "**B** basic · **A** advanced · **E** expert · **M** master",
        "**Tab** no levels · **by result** your score sets the level, one per run",
    ]
    if any(b.partly_wip for b in cat.all()):
        key.append("~~Struck through~~ still in development, not yet requestable")

    embed.add_field(name="Key", value="\n".join(key), inline=False)
    embed.set_footer(
        text=f"{len(cat.all())} badges · {len(cat.requestable())} available to request"
    )
    return embed


def ticket_embed(cat: Catalogue, row: Any) -> discord.Embed:
    """The ticket card. Re-rendered in place on every status change."""
    colour, emoji, label = style_for(row["status"])
    badge = cat.get(row["badge_key"])
    requested = cat.parse_levels(row["badge_key"], row["levels"])
    achieved = cat.parse_levels(row["badge_key"], row["levels_achieved"])

    embed = discord.Embed(
        colour=colour,
        title=f"{emoji} {badge.name if badge else row['badge_key']}",
        description=f"Requested by <@{row['member_id']}>",
    )
    embed.set_footer(text=f"Request #{row['id']}")

    # The whole point of the ticket: the instructor needs to know what to run.
    if requested:
        done = set(achieved)
        recorded = bool(row["levels_achieved"])
        lines = []
        for code in requested:
            if not recorded:
                mark = "\N{WHITE SMALL SQUARE}"
            elif code in done:
                mark = "\N{WHITE HEAVY CHECK MARK}"
            else:
                mark = "\N{CROSS MARK}"
            lines.append(f"{mark} {cat.level_name(code)}")
        embed.add_field(
            name=f"Levels to run ({len(requested)})", value="\n".join(lines), inline=False
        )
    elif badge is not None and badge.timed:
        if row["levels_achieved"] or row["variant"]:
            value = (
                "Achieved: **"
                + (", ".join(cat.level_name(lvl) for lvl in achieved) if achieved else "none")
                + "**"
            )
        else:
            value = (
                "Level is set by the score achieved — "
                + " / ".join(cat.level_name(lvl) for lvl in badge.levels)
                + "."
            )
            if badge.variants:
                value += "\nRun as: " + " · ".join(badge.variants)
        embed.add_field(name="\N{STOPWATCH} Timed test", value=value, inline=False)
    else:
        embed.add_field(name="Level", value="Tab — no levels", inline=False)

    if row["variant"]:
        embed.add_field(name="Run", value=row["variant"], inline=True)

    embed.add_field(name="Status", value=label, inline=True)
    embed.add_field(
        name="Instructor",
        value=f"<@{row['instructor_id']}>" if row["instructor_id"] else "—",
        inline=True,
    )
    embed.add_field(name="Requested", value=relative(row["created_at"]), inline=True)

    if row["levels_achieved"] and requested and len(achieved) < len(requested):
        failed = [lvl for lvl in requested if lvl not in achieved]
        embed.add_field(
            name="\N{WARNING SIGN} Partial result",
            value=(
                f"Not achieved: {', '.join(cat.level_name(lvl) for lvl in failed)}. "
                "Only award the passed levels on taw.net."
            ),
            inline=False,
        )

    if row["notes"]:
        embed.add_field(name="Notes", value=row["notes"][:1024], inline=False)

    return embed


# ------------------------------------------------------- ticket buttons


#: Ticket actions, named explicitly in the template. A loose ``[a-z]+`` would
#: swallow any other ``ctc:*:*`` custom_id — the panel button did exactly that.
TICKET_ACTIONS = ("claim", "cancel", "release", "complete", "result", "award", "reopen")


class TicketButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=rf"ctc:(?P<action>{'|'.join(TICKET_ACTIONS)}):(?P<rid>[0-9]+)",
):
    """A ticket action. Persistent across restarts via its custom_id."""

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
                label=label, style=style, emoji=emoji, custom_id=f"ctc:{action}:{request_id}"
            )
        )

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /
    ) -> TicketButton:
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
        await cog.handle_ticket_action(interaction, self.action, self.request_id)


def ticket_view(cat: Catalogue, row: Any, *, award_url: str | None = None) -> discord.ui.View:
    """Buttons appropriate to the ticket's current state."""
    view = discord.ui.View(timeout=None)
    rid = int(row["id"])
    status = row["status"]
    badge = cat.get(row["badge_key"])
    requested = cat.parse_levels(row["badge_key"], row["levels"])

    if status == "requested":
        view.add_item(
            TicketButton(
                "claim", rid, label="Claim",
                style=discord.ButtonStyle.primary, emoji="\N{RAISED HAND}",
            )
        )
        view.add_item(TicketButton("cancel", rid, label="Cancel"))

    elif status == "claimed":
        # A badge with variants always goes through the form — there is no
        # shortcut that could know which one was run.
        if (badge is not None and badge.timed) or (badge is not None and badge.has_variants):
            view.add_item(
                TicketButton(
                    "result", rid, label="Record result",
                    style=discord.ButtonStyle.success, emoji="\N{STOPWATCH}",
                )
            )
        else:
            view.add_item(
                TicketButton(
                    "complete", rid,
                    label="All passed" if len(requested) > 1 else "Badge Completed",
                    style=discord.ButtonStyle.success, emoji="\N{DIRECT HIT}",
                )
            )
            if len(requested) > 1:
                view.add_item(
                    TicketButton("result", rid, label="Partial", emoji="\N{WARNING SIGN}")
                )
        view.add_item(TicketButton("release", rid, label="Release"))
        view.add_item(TicketButton("cancel", rid, label="Cancel", style=discord.ButtonStyle.danger))

    elif status == "completed":
        # taw.net cannot be driven by the bot, so this is the one manual hop
        # left: a deep link out, and a single button to confirm on the way back.
        if award_url:
            view.add_item(
                discord.ui.Button(
                    label="Open taw.net", url=award_url, emoji="\N{GLOBE WITH MERIDIANS}"
                )
            )
        view.add_item(
            TicketButton(
                "award", rid, label="Awarded on taw.net",
                style=discord.ButtonStyle.success, emoji="\N{WHITE HEAVY CHECK MARK}",
            )
        )
        view.add_item(TicketButton("reopen", rid, label="Reopen"))

    return view


# --------------------------------------------------------- request flow


class NotesModal(discord.ui.Modal, title="Add notes"):
    """Text input only exists inside a modal, so notes get their own little one."""

    value = discord.ui.TextInput(
        label="Anything the instructor should know?",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
        placeholder="Availability, timezone, specific goals…",
    )

    def __init__(self, parent: LevelView) -> None:
        super().__init__()
        self.parent = parent
        if parent.notes:
            self.value.default = parent.notes

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.parent.notes = str(self.value).strip()
        await self.parent.refresh(interaction)


class BadgePickerView(discord.ui.View):
    """Step one — choose badges. Edits itself into the level step."""

    def __init__(self, cog: Any, member: discord.abc.User) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.member = member

        cat: Catalogue = cog.catalogue
        offered = cat.requestable()
        limit = min(MAX_BADGES_PER_REQUEST, len(offered))

        options = []
        for badge in offered:
            category = cat.category(badge.category)
            if badge.timed:
                description = "Timed test — your score sets the level"
            elif badge.has_levels:
                description = " / ".join(cat.level_name(lvl) for lvl in badge.levels)
            else:
                description = "Tab — no levels"
            options.append(
                discord.SelectOption(
                    label=badge.name,
                    value=badge.key,
                    description=f"{category['name']} · {description}"[:100],
                    emoji=category["emoji"],
                )
            )

        self.select = discord.ui.Select(
            placeholder=f"Choose up to {limit} badges…",
            min_values=1,
            max_values=limit,
            options=options,
        )
        self.select.callback = self.on_pick
        self.add_item(self.select)

    def content(self) -> str:
        cat: Catalogue = self.cog.catalogue
        hidden = len(cat.all()) - len(cat.requestable())
        limit = min(MAX_BADGES_PER_REQUEST, len(cat.requestable()))
        lines = [
            "**Request a badge**",
            f"Pick up to **{limit}** badges in one go. "
            "You'll set levels for each on the next step.",
        ]
        if hidden:
            lines.append(
                f"\n-# {hidden} badge{'' if hidden == 1 else 's'} "
                f"{'is' if hidden == 1 else 'are'} still in development and not requestable yet."
            )
        return "\n".join(lines)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.member.id

    async def on_pick(self, interaction: discord.Interaction) -> None:
        view = LevelView(self.cog, self.member, list(self.select.values))
        await interaction.response.edit_message(content=view.content(), view=view)


class LevelSelect(discord.ui.Select):
    """One badge's level dropdown. Knows which badge it belongs to."""

    def __init__(self, parent: LevelView, badge: Badge, cat: Catalogue) -> None:
        self.parent_view = parent
        self.badge_key = badge.key
        chosen = parent.levels.get(badge.key, [])
        # Levels still in development are not offered at all.
        offered = badge.available_levels
        super().__init__(
            placeholder=f"{badge.name} — every level you still need"[:150],
            min_values=1,
            max_values=len(offered),
            options=[
                discord.SelectOption(
                    label=f"{badge.name} — {cat.level_name(code)}"[:100],
                    value=code,
                    default=code in chosen,
                )
                for code in offered
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        cat: Catalogue = self.parent_view.cog.catalogue
        self.parent_view.levels[self.badge_key] = cat.sort_levels(self.badge_key, self.values)
        await self.parent_view.refresh(interaction)


class LevelView(discord.ui.View):
    """Step two — set levels for the graded badges, then submit."""

    def __init__(self, cog: Any, member: discord.abc.User, badge_keys: Sequence[str]) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.member = member
        self.badge_keys = list(badge_keys)
        self.levels: dict[str, list[str]] = {}
        self.notes = ""
        self.page = 0
        self.rebuild()

    # Only graded badges get a dropdown. Tabs have no levels, and timed tests
    # have their level decided by the result rather than chosen up front.
    @property
    def _graded(self) -> list[Badge]:
        cat: Catalogue = self.cog.catalogue
        return [b for b in (cat.get(k) for k in self.badge_keys) if b and b.needs_level_choice]

    @property
    def _pages(self) -> int:
        return max(1, -(-len(self._graded) // LEVELS_PER_PAGE))

    def rebuild(self) -> None:
        self.clear_items()
        cat: Catalogue = self.cog.catalogue
        self.page = min(self.page, self._pages - 1)
        window = self._graded[self.page * LEVELS_PER_PAGE : (self.page + 1) * LEVELS_PER_PAGE]

        for badge in window:
            self.add_item(LevelSelect(self, badge, cat))

        if self._pages > 1:
            back = discord.ui.Button(label="Back", row=4, disabled=self.page == 0)
            back.callback = self._page(-1)
            nxt = discord.ui.Button(label="Next", row=4, disabled=self.page >= self._pages - 1)
            nxt.callback = self._page(1)
            self.add_item(back)
            self.add_item(nxt)

        notes = discord.ui.Button(
            label="Edit notes" if self.notes else "Add notes", row=4, emoji="\N{MEMO}"
        )
        notes.callback = self.on_notes
        self.add_item(notes)

        count = len(self.badge_keys)
        submit = discord.ui.Button(
            label=f"Submit {count} request{'' if count == 1 else 's'}",
            style=discord.ButtonStyle.success,
            row=4,
            disabled=bool(self._missing),
        )
        submit.callback = self.on_submit
        self.add_item(submit)

        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger, row=4)
        cancel.callback = self.on_cancel
        self.add_item(cancel)

    @property
    def _missing(self) -> list[Badge]:
        return [b for b in self._graded if not self.levels.get(b.key)]

    def _page(self, delta: int):
        async def callback(interaction: discord.Interaction) -> None:
            self.page += delta
            await self.refresh(interaction)

        return callback

    def content(self) -> str:
        cat: Catalogue = self.cog.catalogue
        chosen = [cat.get(k) for k in self.badge_keys]
        graded = self._graded

        summary = []
        for badge in chosen:
            if badge is None:
                continue
            if badge.timed:
                summary.append(
                    f"\N{STOPWATCH} **{badge.name}** — timed test, level set by your result"
                )
            elif not badge.has_levels:
                summary.append(f"\N{WHITE HEAVY CHECK MARK} **{badge.name}** — Tab")
            else:
                picked = cat.sort_levels(badge.key, self.levels.get(badge.key, []))
                if picked:
                    summary.append(
                        f"\N{WHITE HEAVY CHECK MARK} **{badge.name}** — "
                        + ", ".join(cat.level_name(lvl) for lvl in picked)
                    )
                else:
                    summary.append(f"\N{WHITE SMALL SQUARE} **{badge.name}** — *no levels selected*")

        lines = [
            "**Which levels do you need?**" if graded else "**Confirm your request**",
            "\n".join(summary),
        ]
        if graded:
            lines.append(
                "\n-# Levels are cumulative. If you hold nothing yet and want Expert, select "
                "**Basic, Advanced and Expert** — the instructor runs them all in one session."
            )
        else:
            lines.append("\n*Nothing to choose here — ready to submit.*")
        if self._pages > 1:
            lines.append(f"\n*Page {self.page + 1} of {self._pages}*")
        if self.notes:
            lines.append(f"\n\N{MEMO} **Notes:** {self.notes}")
        if self._missing:
            lines.append(
                "\n-# Select at least one level for "
                + ", ".join(b.name for b in self._missing)
                + " to submit."
            )
        return "\n".join(lines)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.member.id

    async def refresh(self, interaction: discord.Interaction) -> None:
        self.rebuild()
        await interaction.response.edit_message(content=self.content(), view=self)

    async def on_notes(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(NotesModal(self))

    async def on_cancel(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            content="Cancelled — nothing was submitted.", view=None, embed=None
        )
        self.stop()

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.submit_request(interaction, self)
        self.stop()


# ------------------------------------------------------- recording a result


class _VariantSelect(discord.ui.Select):
    def __init__(self, parent: ResultView, variants: Sequence[str]) -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Which one was run?",
            options=[
                discord.SelectOption(label=name, value=name, default=name == parent.variant)
                for name in variants
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.parent_view.variant = self.values[0]
        await self.parent_view.refresh(interaction)


class _ResultLevelSelect(discord.ui.Select):
    def __init__(self, parent: ResultView, options: Sequence[str], *, single: bool) -> None:
        self.parent_view = parent
        cat: Catalogue = parent.cog.catalogue
        super().__init__(
            placeholder="Level earned" if single else "Levels passed",
            min_values=0,
            # One run, one result — a timed test awards exactly one level.
            max_values=1 if single else len(options),
            options=[
                discord.SelectOption(
                    label=cat.level_name(code), value=code, default=code in parent.levels
                )
                for code in options
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        cat: Catalogue = self.parent_view.cog.catalogue
        self.parent_view.levels = cat.sort_levels(self.parent_view.badge_key, self.values)
        await self.parent_view.refresh(interaction)


class ResultView(discord.ui.View):
    """Records what a member achieved.

    Serves both a partial result on a graded badge and the outcome of a timed
    test — the difference is only which levels are on offer and how many.
    """

    def __init__(self, cog: Any, row: Any, instructor: discord.abc.User) -> None:
        super().__init__(timeout=600)
        self.cog = cog
        self.row = row
        self.instructor = instructor
        self.request_id = int(row["id"])
        self.badge_key = row["badge_key"]
        self.variant: str | None = None
        cat: Catalogue = cog.catalogue
        badge = cat.get(self.badge_key)
        self.timed = bool(badge and badge.timed)
        # A timed test starts blank; a graded one starts from what was requested.
        self.levels: list[str] = [] if self.timed else cat.parse_levels(self.badge_key, row["levels"])
        self.rebuild()

    def rebuild(self) -> None:
        self.clear_items()
        cat: Catalogue = self.cog.catalogue
        variants = cat.variants(self.badge_key)
        if variants:
            self.add_item(_VariantSelect(self, variants))
        self.add_item(
            _ResultLevelSelect(
                self, cat.awardable_levels(self.badge_key, self.row["levels"]), single=self.timed
            )
        )

        confirm = discord.ui.Button(
            label="Confirm result",
            style=discord.ButtonStyle.success,
            row=4,
            disabled=bool(variants) and not self.variant,
        )
        confirm.callback = self.on_confirm
        self.add_item(confirm)

        cancel = discord.ui.Button(label="Cancel", row=4)
        cancel.callback = self.on_cancel
        self.add_item(cancel)

    def content(self) -> str:
        cat: Catalogue = self.cog.catalogue
        badge = cat.get(self.badge_key)
        variants = cat.variants(self.badge_key)
        parts = [f"**{badge.name if badge else self.badge_key}** — request #{self.request_id}"]
        if variants:
            parts.append(f"Run: **{self.variant or '—'}**")
        earned = ", ".join(cat.level_name(lvl) for lvl in self.levels) if self.levels else "none"
        parts.append(f"{'Earned' if self.timed else 'Passed'}: **{earned}**")
        if variants and not self.variant:
            parts.append("\n-# Select which one was run to confirm.")
        else:
            parts.append("\n-# Confirm with no levels selected to record a fail.")
        return "\n".join(parts)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.instructor.id

    async def refresh(self, interaction: discord.Interaction) -> None:
        self.rebuild()
        await interaction.response.edit_message(content=self.content(), view=self)

    async def on_cancel(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content="Cancelled — nothing was recorded.", view=None)
        self.stop()

    async def on_confirm(self, interaction: discord.Interaction) -> None:
        await self.cog.record_result(interaction, self)
        self.stop()


# ------------------------------------------------------- amending a request


class _AmendLevelSelect(discord.ui.Select):
    def __init__(self, parent: AmendView, badge: Badge, cat: Catalogue) -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Levels this request needs",
            min_values=1,
            max_values=len(badge.levels),
            options=[
                discord.SelectOption(
                    label=cat.level_name(code), value=code, default=code in parent.levels
                )
                for code in badge.levels
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        cat: Catalogue = self.parent_view.cog.catalogue
        self.parent_view.levels = cat.sort_levels(self.parent_view.badge_key, self.values)
        await self.parent_view.refresh(interaction)


class AmendView(discord.ui.View):
    """Change what a request is asking for.

    A request that already has a result gets the reopen path instead — the
    levels can only move while the work is still open.
    """

    def __init__(self, cog: Any, row: Any, instructor: discord.abc.User) -> None:
        super().__init__(timeout=600)
        self.cog = cog
        self.row = row
        self.instructor = instructor
        self.request_id = int(row["id"])
        self.badge_key = row["badge_key"]
        self.original = cog.catalogue.parse_levels(self.badge_key, row["levels"])
        self.levels = list(self.original)
        self.finished = row["status"] in ("completed", "awarded")
        self.rebuild()

    def rebuild(self) -> None:
        self.clear_items()
        if self.finished:
            reopen = discord.ui.Button(label="Reopen for re-run", style=discord.ButtonStyle.danger)
            reopen.callback = self.on_reopen
            self.add_item(reopen)
            cancel = discord.ui.Button(label="Cancel")
            cancel.callback = self.on_cancel
            self.add_item(cancel)
            return

        cat: Catalogue = self.cog.catalogue
        badge = cat.get(self.badge_key)
        if badge is not None:
            # The full ladder, not just what was asked for — the common case is
            # adding a level that was missed.
            self.add_item(_AmendLevelSelect(self, badge, cat))

        save = discord.ui.Button(
            label="Save changes",
            style=discord.ButtonStyle.success,
            row=4,
            disabled=self.levels == self.original,
        )
        save.callback = self.on_save
        self.add_item(save)

        cancel = discord.ui.Button(label="Cancel", row=4)
        cancel.callback = self.on_cancel
        self.add_item(cancel)

    def content(self) -> str:
        cat: Catalogue = self.cog.catalogue
        badge = cat.get(self.badge_key)
        name = badge.name if badge else self.badge_key

        if self.finished:
            _, _, label = style_for(self.row["status"])
            return (
                f"**{name}** — request #{self.request_id}\n"
                f"Already marked **{label}**.\n\n"
                "Reopen it if the levels were wrong or it needs running again — "
                "that clears the result and puts it back on your plate."
            )

        fmt = lambda items: (  # noqa: E731
            ", ".join(cat.level_name(lvl) for lvl in cat.sort_levels(self.badge_key, items))
            if items
            else "none"
        )
        return (
            f"**Amend {name}** — request #{self.request_id}\n"
            f"Currently — **{fmt(self.original)}**\n"
            f"Change to — **{fmt(self.levels)}**\n\n"
            "-# Adjusts what still needs running. The member is not re-notified."
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.instructor.id

    async def refresh(self, interaction: discord.Interaction) -> None:
        self.rebuild()
        await interaction.response.edit_message(content=self.content(), view=self)

    async def on_cancel(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content="Cancelled — nothing was changed.", view=None)
        self.stop()

    async def on_save(self, interaction: discord.Interaction) -> None:
        await self.cog.save_amendment(interaction, self)
        self.stop()

    async def on_reopen(self, interaction: discord.Interaction) -> None:
        await self.cog.reopen_request(interaction, self)
        self.stop()


#: Fixed id, deliberately not matching the ticket template above. Registered
#: with bot.add_view in cog_load so the pinned panel survives restarts.
PANEL_CUSTOM_ID = "ctc:panel:open"


class PanelView(discord.ui.View):
    """The pinnable panel. One button, alive forever."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Request a Badge",
        style=discord.ButtonStyle.primary,
        emoji="\N{MILITARY MEDAL}",
        custom_id=PANEL_CUSTOM_ID,
    )
    async def request(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        cog = interaction.client.get_cog("ctc")
        if cog is None:  # pragma: no cover - only if the cog is unloaded
            await interaction.response.send_message("Badge system is offline.", ephemeral=True)
            return
        picker = BadgePickerView(cog, interaction.user)
        await interaction.response.send_message(picker.content(), view=picker, ephemeral=True)


def entry_point_payload(cat: Catalogue, *, with_catalogue: bool) -> dict[str, Any]:
    """The pinnable panel. Members click rather than remembering a command."""
    panel = discord.Embed(
        colour=COLOUR,
        title="\N{MILITARY MEDAL} Badge Requests",
        description=(
            "Click below to request training for one or more badges.\n"
            "You can also use `/badge request` anywhere in the server."
        ),
    )
    embeds = [catalogue_embed(cat), panel] if with_catalogue else [panel]
    return {"embeds": embeds, "view": PanelView()}
