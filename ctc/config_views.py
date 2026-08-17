"""Catalogue editor for /badge config.

Every control writes straight to ``catalogue.json`` through
``catalogue.mutate``, which validates the result before it reaches disk and
restores the previous file if the reload fails. A rejected edit surfaces its
reason on the same screen and changes nothing.
"""

from __future__ import annotations

from typing import Any

import discord

from .catalogue import Badge, Catalogue, CatalogueError

COLOUR = 0x5865F2


def _describe(badge: Badge) -> str:
    return {"timed": "Timed test", "graded": "Graded", "tab": "Tab"}[badge.kind]


def _badge_options(cat: Catalogue, selected: str | None = None) -> list[discord.SelectOption]:
    options = []
    for badge in cat.all():
        category = cat.category(badge.category)
        suffix = " · in development" if badge.wip else ""
        options.append(
            discord.SelectOption(
                label=badge.name,
                value=badge.key,
                description=f"{_describe(badge)} · {category['name']}{suffix}"[:100],
                default=badge.key == selected,
            )
        )
    return options


class _Editable(discord.ui.View):
    """Shared plumbing: who may use it, and how an edit is applied."""

    def __init__(self, cog: Any, actor: discord.abc.User, notice: str | None = None) -> None:
        super().__init__(timeout=600)
        self.cog = cog
        self.actor = actor
        self.notice = notice

    @property
    def cat(self) -> Catalogue:
        return self.cog.catalogue

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.actor.id

    async def apply(self, interaction: discord.Interaction, fn, notice: str, key: str | None) -> None:
        try:
            self.cog.apply_catalogue_edit(fn)
        except (CatalogueError, ValueError) as error:
            target = ConfigEditorView(self.cog, self.actor, key, f"\N{WARNING SIGN} {error}") if key \
                else ConfigRootView(self.cog, self.actor, f"\N{WARNING SIGN} {error}")
            await interaction.response.edit_message(content=target.content(), view=target)
            return
        target = ConfigEditorView(self.cog, self.actor, key, notice) if key \
            else ConfigRootView(self.cog, self.actor, notice)
        await interaction.response.edit_message(content=target.content(), view=target)


class _BadgePick(discord.ui.Select):
    def __init__(self, parent: _Editable, placeholder: str, *, availability: bool) -> None:
        self.parent_view = parent
        self.availability = availability
        super().__init__(placeholder=placeholder, options=_badge_options(parent.cat))

    async def callback(self, interaction: discord.Interaction) -> None:
        key = self.values[0]
        if not self.availability:
            view = ConfigEditorView(self.parent_view.cog, self.parent_view.actor, key)
            await interaction.response.edit_message(content=view.content(), view=view)
            return

        def toggle(doc):
            entry = next(b for b in doc["badges"] if b["key"] == key)
            if entry.get("wip"):
                entry.pop("wip")
            else:
                entry["wip"] = True

        try:
            self.parent_view.cog.apply_catalogue_edit(toggle)
        except CatalogueError as error:
            view = AvailabilityView(
                self.parent_view.cog, self.parent_view.actor, f"\N{WARNING SIGN} {error}"
            )
        else:
            badge = self.parent_view.cog.catalogue.get(key)
            state = "in development" if badge and badge.wip else "available"
            view = AvailabilityView(
                self.parent_view.cog, self.parent_view.actor, f"**{badge.name}** is now {state}."
            )
        await interaction.response.edit_message(content=view.content(), view=view)


class ConfigRootView(_Editable):
    def __init__(self, cog: Any, actor: discord.abc.User, notice: str | None = None) -> None:
        super().__init__(cog, actor, notice)
        self.add_item(_BadgePick(self, "Edit a badge…", availability=False))

        add = discord.ui.Button(label="Add badge", style=discord.ButtonStyle.success, row=1)
        add.callback = self.on_add
        self.add_item(add)

        avail = discord.ui.Button(label="Availability", row=1)
        avail.callback = self.on_availability
        self.add_item(avail)

    def content(self) -> str:
        head = f"{self.notice}\n\n" if self.notice else ""
        return (
            f"{head}**Badge catalogue**\n"
            f"{len(self.cat.all())} badges, {len(self.cat.requestable())} requestable."
        )

    async def on_add(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(NameModal(self.cog, self.actor, key=None))

    async def on_availability(self, interaction: discord.Interaction) -> None:
        view = AvailabilityView(self.cog, self.actor)
        await interaction.response.edit_message(content=view.content(), view=view)


class AvailabilityView(_Editable):
    def __init__(self, cog: Any, actor: discord.abc.User, notice: str | None = None) -> None:
        super().__init__(cog, actor, notice)
        self.add_item(_BadgePick(self, "Toggle a badge…", availability=True))
        back = discord.ui.Button(label="Back", row=1)
        back.callback = self.on_back
        self.add_item(back)

    def content(self) -> str:
        wip = [b for b in self.cat.all() if b.wip]
        head = f"{self.notice}\n\n" if self.notice else ""
        body = (
            "\n".join(f"\N{CONSTRUCTION SIGN} {b.name}" for b in wip)
            if wip
            else "*Everything is available.*"
        )
        return (
            f"{head}**Availability**\n"
            f"{len(self.cat.requestable())} requestable · {len(wip)} in development\n\n{body}"
        )

    async def on_back(self, interaction: discord.Interaction) -> None:
        view = ConfigRootView(self.cog, self.actor)
        await interaction.response.edit_message(content=view.content(), view=view)


class _CategorySelect(discord.ui.Select):
    def __init__(self, parent: ConfigEditorView, badge: Badge) -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Category",
            options=[
                discord.SelectOption(
                    label=c["name"], value=c["key"], emoji=c["emoji"], default=c["key"] == badge.category
                )
                for c in parent.cat.categories
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        key, chosen = self.parent_view.key, self.values[0]

        def edit(doc):
            next(b for b in doc["badges"] if b["key"] == key)["category"] = chosen

        name = self.parent_view.cat.category(chosen)["name"]
        await self.parent_view.apply(interaction, edit, f"Moved to {name}.", key)


class _LevelsSelect(discord.ui.Select):
    def __init__(self, parent: ConfigEditorView, badge: Badge) -> None:
        self.parent_view = parent
        codes = parent.cat.level_codes()
        super().__init__(
            placeholder="Levels — leave empty for a Tab badge",
            min_values=0,
            max_values=len(codes),
            options=[
                discord.SelectOption(
                    label=parent.cat.level_name(c), value=c, default=c in badge.levels
                )
                for c in codes
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        key = self.parent_view.key
        ordered = [c for c in self.parent_view.cat.level_codes() if c in self.values]

        def edit(doc):
            entry = next(b for b in doc["badges"] if b["key"] == key)
            entry["levels"] = ordered
            # A badge with no levels cannot be a timed test.
            if not ordered:
                entry.pop("timed", None)

        notice = (
            "Levels set to " + " / ".join(self.parent_view.cat.level_name(c) for c in ordered) + "."
            if ordered
            else "Now a Tab badge."
        )
        await self.parent_view.apply(interaction, edit, notice, key)


class _WipLevelsSelect(discord.ui.Select):
    """Which of a badge's own levels are not yet runnable."""

    def __init__(self, parent: ConfigEditorView, badge: Badge) -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Levels in development — leave empty if all are ready",
            min_values=0,
            max_values=len(badge.levels),
            row=2,
            options=[
                discord.SelectOption(
                    label=parent.cat.level_name(code),
                    value=code,
                    default=code in badge.wip_levels,
                )
                for code in badge.levels
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        key = self.parent_view.key
        badge = self.parent_view.cat.get(key)
        chosen = [c for c in badge.levels if c in self.values]

        def edit(doc):
            entry = next(b for b in doc["badges"] if b["key"] == key)
            if chosen:
                entry["wipLevels"] = chosen
            else:
                entry.pop("wipLevels", None)

        if chosen:
            notice = f"{', '.join(chosen)} marked in development."
            if len(chosen) == len(badge.levels):
                notice += " Nothing left to request, so the badge is hidden from the picker."
        else:
            notice = "All levels available."
        await self.parent_view.apply(interaction, edit, notice, key)


class ConfigEditorView(_Editable):
    def __init__(
        self, cog: Any, actor: discord.abc.User, key: str, notice: str | None = None
    ) -> None:
        super().__init__(cog, actor, notice)
        self.key = key
        badge = self.cat.get(key)
        if badge is None:
            return

        self.add_item(_CategorySelect(self, badge))
        self.add_item(_LevelsSelect(self, badge))
        # A Tab has no levels, so there is nothing to hold back.
        if badge.has_levels:
            self.add_item(_WipLevelsSelect(self, badge))

        rename = discord.ui.Button(label="Rename", row=3)
        rename.callback = self.on_rename
        self.add_item(rename)

        variants = discord.ui.Button(label="Variants", row=3)
        variants.callback = self.on_variants
        self.add_item(variants)

        timed = discord.ui.Button(
            label="Timed \N{HEAVY CHECK MARK}" if badge.timed else "Timed \N{HEAVY MULTIPLICATION X}",
            style=discord.ButtonStyle.primary if badge.timed else discord.ButtonStyle.secondary,
            row=3,
        )
        timed.callback = self.on_timed
        self.add_item(timed)

        wip = discord.ui.Button(
            label="Make available" if badge.wip else "Mark in development", row=3
        )
        wip.callback = self.on_wip
        self.add_item(wip)

        back = discord.ui.Button(label="Back", row=4)
        back.callback = self.on_back
        self.add_item(back)

        delete = discord.ui.Button(label="Delete", style=discord.ButtonStyle.danger, row=4)
        delete.callback = self.on_delete
        self.add_item(delete)

    def content(self) -> str:
        badge = self.cat.get(self.key)
        if badge is None:
            return "That badge no longer exists."
        levels = " / ".join(self.cat.level_name(lvl) for lvl in badge.levels) or "none (Tab)"
        if badge.wip_levels:
            held = ", ".join(self.cat.level_name(lvl) for lvl in badge.wip_levels)
            levels += f"  (in development: {held})"
        lines = [
            f"{self.notice}\n" if self.notice else None,
            f"**{badge.name}**  `{badge.key}`",
            f"Category — {self.cat.category(badge.category)['name']}",
            f"Levels — {levels}",
            f"Type — {_describe(badge)}",
            f"Variants — {', '.join(badge.variants) if badge.variants else 'none'}",
            "Availability — "
            + (
                "\N{CONSTRUCTION SIGN} in development"
                if badge.wip
                else "\N{WHITE HEAVY CHECK MARK} available"
            ),
            f"-# Also known as: {', '.join(badge.former_names)}" if badge.former_names else None,
        ]
        return "\n".join(line for line in lines if line)

    async def on_rename(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(NameModal(self.cog, self.actor, key=self.key))

    async def on_variants(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(VariantsModal(self.cog, self.actor, self.key))

    async def on_timed(self, interaction: discord.Interaction) -> None:
        key = self.key

        def edit(doc):
            entry = next(b for b in doc["badges"] if b["key"] == key)
            if entry.get("timed"):
                entry.pop("timed")
            else:
                entry["timed"] = True

        notice = "No longer a timed test." if self.cat.get(key).timed else "Now a timed test."
        await self.apply(interaction, edit, notice, key)

    async def on_wip(self, interaction: discord.Interaction) -> None:
        key = self.key

        def edit(doc):
            entry = next(b for b in doc["badges"] if b["key"] == key)
            if entry.get("wip"):
                entry.pop("wip")
            else:
                entry["wip"] = True

        notice = "Now available." if self.cat.get(key).wip else "Marked in development."
        await self.apply(interaction, edit, notice, key)

    async def on_back(self, interaction: discord.Interaction) -> None:
        view = ConfigRootView(self.cog, self.actor)
        await interaction.response.edit_message(content=view.content(), view=view)

    async def on_delete(self, interaction: discord.Interaction) -> None:
        used = await self.cog.database.counts_for_badge(self.key)
        view = DeleteConfirmView(self.cog, self.actor, self.key, used)
        await interaction.response.edit_message(content=view.content(), view=view)


class DeleteConfirmView(_Editable):
    def __init__(self, cog: Any, actor: discord.abc.User, key: str, used: int) -> None:
        super().__init__(cog, actor)
        self.key = key
        self.used = used

        confirm = discord.ui.Button(label="Delete permanently", style=discord.ButtonStyle.danger)
        confirm.callback = self.on_confirm
        self.add_item(confirm)

        instead = discord.ui.Button(
            label="Mark in development instead", style=discord.ButtonStyle.success
        )
        instead.callback = self.on_instead
        self.add_item(instead)

        cancel = discord.ui.Button(label="Cancel")
        cancel.callback = self.on_cancel
        self.add_item(cancel)

    def content(self) -> str:
        badge = self.cat.get(self.key)
        warning = (
            f"\N{WARNING SIGN} {self.used} request{'' if self.used == 1 else 's'} still reference "
            "this badge and would render as a raw key.\n\n"
            if self.used
            else "No requests reference this badge.\n\n"
        )
        return (
            f"**Delete {badge.name if badge else self.key}?**\n{warning}"
            "-# Marking it in development hides it from the picker without breaking history."
        )

    async def on_confirm(self, interaction: discord.Interaction) -> None:
        key = self.key
        name = self.cat.get(key).name if self.cat.get(key) else key
        await self.apply(
            interaction,
            lambda doc: doc.__setitem__("badges", [b for b in doc["badges"] if b["key"] != key]),
            f"Deleted **{name}**.",
            None,
        )

    async def on_instead(self, interaction: discord.Interaction) -> None:
        key = self.key

        def edit(doc):
            next(b for b in doc["badges"] if b["key"] == key)["wip"] = True

        await self.apply(interaction, edit, "Marked in development.", key)

    async def on_cancel(self, interaction: discord.Interaction) -> None:
        view = ConfigEditorView(self.cog, self.actor, self.key)
        await interaction.response.edit_message(content=view.content(), view=view)


class NameModal(discord.ui.Modal):
    """Text fields only exist inside modals, so naming needs one."""

    value = discord.ui.TextInput(label="Badge name", max_length=100, placeholder="Combat Engineer")

    def __init__(self, cog: Any, actor: discord.abc.User, *, key: str | None) -> None:
        super().__init__(title="Add a badge" if key is None else "Rename badge")
        self.cog = cog
        self.actor = actor
        self.key = key
        if key is not None:
            badge = cog.catalogue.get(key)
            if badge is not None:
                self.value.default = badge.name

    async def on_submit(self, interaction: discord.Interaction) -> None:
        name = str(self.value).strip()

        if self.key is None:
            new_key = self.cog.catalogue.key_for(name)

            def create(doc):
                doc["badges"].append(
                    {
                        "key": new_key,
                        "name": name,
                        "category": doc["categories"][0]["key"],
                        "levels": [],
                        "wip": True,
                    }
                )

            try:
                self.cog.apply_catalogue_edit(create)
            except CatalogueError as error:
                view = ConfigRootView(self.cog, self.actor, f"\N{WARNING SIGN} {error}")
            else:
                view = ConfigEditorView(
                    self.cog,
                    self.actor,
                    new_key,
                    "Created, and marked in development until you finish setting it up.",
                )
            await interaction.response.edit_message(content=view.content(), view=view)
            return

        key = self.key
        previous = self.cog.catalogue.get(key).name
        if name == previous:
            view = ConfigEditorView(self.cog, self.actor, key)
            await interaction.response.edit_message(content=view.content(), view=view)
            return

        def rename(doc):
            entry = next(b for b in doc["badges"] if b["key"] == key)
            # Keep the old name resolvable so historic records still read right.
            former = [f for f in entry.get("formerNames", []) if f != name]
            if entry["name"] not in former:
                former.append(entry["name"])
            entry["formerNames"] = former
            entry["name"] = name

        try:
            self.cog.apply_catalogue_edit(rename)
        except CatalogueError as error:
            view = ConfigEditorView(self.cog, self.actor, key, f"\N{WARNING SIGN} {error}")
        else:
            view = ConfigEditorView(
                self.cog, self.actor, key, f"Renamed from **{previous}**, old name kept for history."
            )
        await interaction.response.edit_message(content=view.content(), view=view)


class VariantsModal(discord.ui.Modal, title="Variants"):
    value = discord.ui.TextInput(
        label="Variants, comma separated",
        required=False,
        max_length=300,
        placeholder="Rifle, Pistol, SMG",
    )

    def __init__(self, cog: Any, actor: discord.abc.User, key: str) -> None:
        super().__init__()
        self.cog = cog
        self.actor = actor
        self.key = key
        badge = cog.catalogue.get(key)
        if badge is not None and badge.variants:
            self.value.default = ", ".join(badge.variants)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        key = self.key
        items = [p.strip() for p in str(self.value).split(",") if p.strip()]

        def edit(doc):
            entry = next(b for b in doc["badges"] if b["key"] == key)
            if items:
                entry["variants"] = items
            else:
                entry.pop("variants", None)

        try:
            self.cog.apply_catalogue_edit(edit)
        except CatalogueError as error:
            view = ConfigEditorView(self.cog, self.actor, key, f"\N{WARNING SIGN} {error}")
        else:
            notice = f"Variants set to {', '.join(items)}." if items else "Variants cleared."
            view = ConfigEditorView(self.cog, self.actor, key, notice)
        await interaction.response.edit_message(content=view.content(), view=view)
