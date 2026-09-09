"""Offline checks for the Combat Training Centre badge flow.

Exercises the catalogue, the database lifecycle and the component builders
without connecting to Discord. Run with:

    python -m ctc.selftest
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

import aiosqlite
import discord

from cogs.ctc import DEFAULTS
from ctc import CTCDatabaseManager, TransitionError
from ctc import catalogue as catalogue_module
from ctc.units import Units
from ctc.views import (
    MAX_BADGES_PER_REQUEST,
    PANEL_CUSTOM_ID,
    TICKET_ACTIONS,
    AmendView,
    BadgePickerView,
    LevelView,
    PanelButton,
    ResultView,
    TicketButton,
    catalogue_embed,
    entry_point_payload,
    ticket_embed,
    ticket_view,
)

ROOT = Path(__file__).resolve().parent
CHECKS: list[tuple[str, object]] = []


def check(name):
    def wrap(fn):
        CHECKS.append((name, fn))
        return fn

    return wrap


class FakeUser:
    def __init__(self, uid: int = 1) -> None:
        self.id = uid
        self.display_name = "Tester"


class FakeRole:
    def __init__(self, rid: int) -> None:
        self.id = rid


class FakeMember(discord.Member):
    """A member that satisfies ``isinstance(user, discord.Member)``.

    ``holds_any`` checks the type before reading roles, so a plain stand-in
    would be treated as a DM user and silently denied every permission. ``id``
    and ``roles`` are properties on the real class, hence the overrides.
    """

    def __init__(self, uid: int, roles=()) -> None:
        self._uid = uid
        self._roles = [FakeRole(r) for r in roles]

    @property
    def id(self) -> int:
        return self._uid

    @property
    def roles(self):
        return self._roles


class FakeUnit:
    """A single battalion, standing in for ctc.units.Unit."""

    def __init__(self, catalogue, key: str = "default", name: str = "Test Battalion") -> None:
        self.key = key
        self.name = name
        self.catalogue = catalogue
        self.settings = dict(DEFAULTS)


class FakeUnits:
    def __init__(self, *units) -> None:
        self._by_key = {u.key: u for u in units}
        self.primary = next(iter(self._by_key))

    def __len__(self):
        return len(self._by_key)

    def __iter__(self):
        return iter(self._by_key.values())

    def get(self, key):
        return self._by_key.get(key)

    def require(self, key):
        return self._by_key[key]


class FakeCog:
    """Just enough of the cog for the views to build."""

    def __init__(self, catalogue) -> None:
        self.unit = FakeUnit(catalogue)
        self.units = FakeUnits(self.unit)

    def unit_of(self, row):
        return self.units.get(row["unit"]) or self.unit


def unwrap(item):
    """DynamicItem wraps the real component — ticket buttons arrive that way."""
    return getattr(item, "item", item)


def buttons(view: discord.ui.View) -> list[discord.ui.Button]:
    return [u for u in map(unwrap, view.children) if isinstance(u, discord.ui.Button)]


#: Every select kind, listed by name rather than by their shared base class:
#: the base lives in a module shadowed by the ``discord.ui.select`` decorator,
#: and the option-list ``Select`` is not an ancestor of the others. Matching on
#: ``Select`` alone would quietly skip a MentionableSelect and let a view
#: exceed five rows without this noticing.
SELECT_TYPES = tuple(
    getattr(discord.ui, name)
    for name in ("Select", "UserSelect", "RoleSelect", "MentionableSelect", "ChannelSelect")
    if hasattr(discord.ui, name)
)


def selects(view: discord.ui.View):
    return [u for u in map(unwrap, view.children) if isinstance(u, SELECT_TYPES)]


def assert_component_limits(view: discord.ui.View, label: str) -> None:
    """Discord: 5 action rows; a row holds 5 buttons or one select of <=25."""
    # Items without an explicit row are auto-flowed by discord.py; the total
    # still has to fit five rows once selects are given a row each.
    estimated = len(selects(view)) + -(-len(buttons(view)) // 5)
    assert estimated <= 5, f"{label}: needs {estimated} rows, max 5"

    for select in selects(view):
        # Only option-backed selects carry a list; the user/role/mentionable
        # ones are populated by Discord itself.
        options = getattr(select, "options", None)
        if options is not None:
            assert len(options) <= 25, f"{label}: select has {len(options)} options"
        assert select.max_values <= 25, f"{label}: max_values {select.max_values} above 25"


def labels(view: discord.ui.View) -> list[str]:
    return [b.label for b in buttons(view)]


def button_named(view: discord.ui.View, prefix: str) -> discord.ui.Button:
    return next(b for b in buttons(view) if (b.label or "").startswith(prefix))


def select_named(view: discord.ui.View, fragment: str):
    for select in selects(view):
        if fragment.lower() in (select.placeholder or "").lower():
            return select
    return None


def _cog(catalogue) -> FakeCog:
    """A cog whose single unit wraps this catalogue."""
    return FakeCog(catalogue)


# ---------------------------------------------------------------- catalogue

CAT = catalogue_module.load()


@check("catalogue loads and fits a single select menu")
def _() -> None:
    assert len(CAT.all()) <= 25
    assert len(CAT.requestable()) == len([b for b in CAT.all() if not b.wip])
    tabs = sorted(b.name for b in CAT.all() if not b.has_levels)
    assert "Airborne" in tabs and "Radio" in tabs


@check("the three badge kinds are distinct")
def _() -> None:
    timed = sorted(b.name for b in CAT.all() if b.timed)
    assert timed == ["CQC", "Gun Range"], timed
    for badge in CAT.all():
        if badge.timed:
            assert badge.has_levels and not badge.needs_level_choice
    assert CAT.get("airborne").kind == "tab"
    assert CAT.get("grenadier").kind == "graded"
    assert CAT.get("cqc").kind == "timed"


@check("levels normalise to progression order however they were clicked")
def _() -> None:
    assert CAT.sort_levels("gun_range", ["M", "B", "E", "A"]) == ["B", "A", "E", "M"]
    assert CAT.label("grenadier", ["E", "B", "A"]) == "Grenadier — Basic, Advanced, Expert"
    assert CAT.label("grenadier", "B,A,E") == "Grenadier — Basic, Advanced, Expert"
    assert CAT.label("airborne", None) == "Airborne"
    assert CAT.parse_levels("grenadier", None) == []


@check("the short label abbreviates levels for thread names")
def _() -> None:
    assert CAT.label("grenadier", ["E", "B", "A"], short=True) == "Grenadier — B / A / E"
    assert CAT.label("grenadier", "B,A", short=True) == "Grenadier — B / A"
    assert CAT.label("gun_range", None, short=True) == "Gun Range", "no trailing dash for timed"
    assert CAT.label("airborne", None, short=True) == "Airborne", "no trailing dash for tabs"
    # The full form is unchanged — cards and pickers still spell levels out.
    assert CAT.label("grenadier", "B,A") == "Grenadier — Basic, Advanced"


@check("awardable levels differ by badge kind")
def _() -> None:
    assert CAT.awardable_levels("grenadier", "B,A") == ["B", "A"]
    assert CAT.awardable_levels("cqc", None) == ["B", "A", "E", "M"]
    assert CAT.awardable_levels("airborne", None) == []


@check("Gun Range carries its variants, CQC does not")
def _() -> None:
    assert CAT.variants("gun_range") == ["Rifle", "Pistol", "SMG", "Shotgun", "HMG"]
    assert CAT.variants("cqc") == []
    assert CAT.resolve_by_name("HMG").key == "gun_range", "the old badge name still resolves"
    assert CAT.resolve_by_name("Explosive Ordnance").key == "combat_eng"


@check("generated keys avoid collisions")
def _() -> None:
    assert CAT.key_for("Night Ops") == "night_ops"
    assert CAT.key_for("Medical!!") == "medical_2"
    assert CAT.key_for("***") == "badge"


@check("an invalid catalogue edit is rejected and never reaches disk")
def _() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "catalogue.json"
        shutil.copy(catalogue_module.CATALOGUE_PATH, path)
        before = path.read_text(encoding="utf-8")

        def bad_level(doc):
            next(b for b in doc["badges"] if b["key"] == "medical")["levels"] = ["Z"]

        def bad_category(doc):
            next(b for b in doc["badges"] if b["key"] == "medical")["category"] = "nope"

        def duplicate(doc):
            doc["badges"].append(dict(doc["badges"][0]))

        def timed_tab(doc):
            next(b for b in doc["badges"] if b["key"] == "airborne")["timed"] = True

        for fn, fragment in [
            (bad_level, "unknown level"),
            (bad_category, "unknown category"),
            (duplicate, "Duplicate"),
            (timed_tab, "no levels to award"),
        ]:
            try:
                catalogue_module.mutate(fn, path)
            except catalogue_module.CatalogueError as error:
                assert fragment.lower() in str(error).lower(), f"{fragment} vs {error}"
            else:
                raise AssertionError(f"expected {fragment} to be rejected")

        assert path.read_text(encoding="utf-8") == before, "file untouched"


@check("a valid catalogue edit is written and reloads")
def _() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "catalogue.json"
        shutil.copy(catalogue_module.CATALOGUE_PATH, path)

        updated = catalogue_module.mutate(
            lambda doc: doc["badges"].append(
                {"key": "test_marksman", "name": "Test Marksman", "category": "infantry",
                 "levels": [], "wip": True}
            ),
            path,
        )
        assert updated.get("test_marksman").name == "Test Marksman"
        assert updated.get("test_marksman").wip, "new badges start hidden"
        assert "test_marksman" not in [b.key for b in updated.requestable()]

        reread = json.loads(path.read_text(encoding="utf-8"))
        assert any(b["key"] == "test_marksman" for b in reread["badges"])


# ----------------------------------------------------------------- views


@check("badge picker offers only requestable badges, capped at five")
def _() -> None:
    view = BadgePickerView(_cog(CAT), FakeUser(), _cog(CAT).unit)
    assert_component_limits(view, "picker")
    select = view.select
    assert len(select.options) == len(CAT.requestable())
    assert not any(CAT.get(o.value).wip for o in select.options)
    assert select.min_values == 1
    assert select.max_values == min(MAX_BADGES_PER_REQUEST, len(CAT.requestable())) == 5
    assert "up to 5" in (select.placeholder or "")
    assert "up to **5**" in view.content()


@check("timed tests and tabs get no level dropdown")
def _() -> None:
    view = LevelView(_cog(CAT), FakeUser(), ["cqc", "gun_range", "airborne"], _cog(CAT).unit)
    assert_component_limits(view, "levels/none")
    assert not [i for i in view.children if isinstance(i, discord.ui.Select)]
    submit = button_named(view, "Submit")
    assert submit.disabled is False
    assert "timed test, level set by your result" in view.content()


@check("graded badges gate submission until every one has a level")
def _() -> None:
    view = LevelView(_cog(CAT), FakeUser(), ["cqc", "grenadier"], _cog(CAT).unit)
    assert_component_limits(view, "levels/mixed")
    assert len([i for i in view.children if isinstance(i, discord.ui.Select)]) == 1
    submit = button_named(view, "Submit")
    assert submit.disabled is True, "the graded badge still gates submission"

    view.levels["grenadier"] = ["B", "A", "E"]
    view.rebuild()
    submit = button_named(view, "Submit")
    assert submit.disabled is False
    assert "Basic, Advanced, Expert" in view.content()


@check("a full five-badge selection paginates instead of overflowing")
def _() -> None:
    graded = [b.key for b in CAT.requestable() if b.needs_level_choice][:5]
    assert len(graded) == 5, "fixture needs five graded badges"
    view = LevelView(_cog(CAT), FakeUser(), graded, _cog(CAT).unit)
    assert_component_limits(view, "levels/page1")
    assert len([i for i in view.children if isinstance(i, discord.ui.Select)]) == 4
    assert "Page 1 of 2" in view.content()

    view.page = 1
    view.rebuild()
    assert_component_limits(view, "levels/page2")
    assert len([i for i in view.children if isinstance(i, discord.ui.Select)]) == 1


@check("each level dropdown offers only that badge's own levels")
def _() -> None:
    view = LevelView(_cog(CAT), FakeUser(), ["medical", "grenadier"], _cog(CAT).unit)
    medical = select_named(view, "Medical")
    grenadier = select_named(view, "Grenadier")
    assert [o.value for o in medical.options] == ["B", "A"], "Medical has no Expert"
    assert medical.max_values == 2
    assert [o.value for o in grenadier.options] == ["B", "A", "E"]


@check("the catalogue embed lists every badge, in full-width rows")
def _() -> None:
    embed = catalogue_embed(CAT)
    text = "\n".join(f.value for f in embed.fields)

    for badge in CAT.all():
        assert badge.name in text, f"{badge.name} missing from the catalogue"

    # Ragged three-column packing is what inline=True causes; every field here
    # must be full width.
    assert all(f.inline is False for f in embed.fields), "all fields must be full width"

    assert "by result" in text, "timed badges are marked"
    assert "**Airborne** Tab" in text
    assert f"{len(CAT.all())} badges" in embed.footer.text


@check("the catalogue separates in-development badges and explains the codes")
def _() -> None:
    embed = catalogue_embed(CAT)
    names = [f.name for f in embed.fields]
    assert "Key" in names, "the level codes are explained"
    assert names[-1] == "Key", "the key sits at the bottom"

    key = next(f.value for f in embed.fields if f.name == "Key")
    for code, word in [("B", "basic"), ("A", "advanced"), ("E", "expert"), ("M", "master")]:
        assert f"**{code}** {word}" in key, code
    assert "by result" in key

    wip = [b.name for b in CAT.all() if b.wip]
    if wip:
        pending = next(f.value for f in embed.fields if f.name == "In development")
        for name in wip:
            assert name in pending, f"{name} should be listed as pending"
        # ...and nowhere in the requestable rows.
        rows = "\n".join(f.value for f in embed.fields if f.name not in ("In development", "Key"))
        for name in wip:
            assert name not in rows, f"{name} should not appear as requestable"


@check("a level can be in development while the badge stays requestable")
def _() -> None:
    raw = json.loads(catalogue_module.CATALOGUE_PATH.read_text(encoding="utf-8"))
    for entry in raw["badges"]:
        if entry["key"] == "grenadier":
            entry["wipLevels"] = ["A", "E"]
    cat = catalogue_module.Catalogue(raw)

    badge = cat.get("grenadier")
    assert badge.levels == ["B", "A", "E"], "the ladder is unchanged"
    assert badge.available_levels == ["B"], "only Basic can be run"
    assert badge.partly_wip is True
    assert "grenadier" in [b.key for b in cat.requestable()], "still requestable at Basic"

    # The picker must not offer a level nobody can be tested on.
    view = LevelView(_cog(cat), FakeUser(), ["grenadier"], _cog(cat).unit)
    select = select_named(view, "Grenadier")
    assert [o.value for o in select.options] == ["B"]
    assert select.max_values == 1

    # The whole ladder stays visible, with the unrunnable levels struck out.
    embed = catalogue_embed(cat)
    text = "\n".join(f.value for f in embed.fields)
    assert "**Grenadier** B / ~~A~~ / ~~E~~" in text, text

    key = next(f.value for f in embed.fields if f.name == "Key")
    assert "~~Struck through~~" in key, "the key explains the strikethrough"


@check("a badge with every level in development drops out of the picker")
def _() -> None:
    raw = json.loads(catalogue_module.CATALOGUE_PATH.read_text(encoding="utf-8"))
    for entry in raw["badges"]:
        if entry["key"] == "medical":
            entry["wipLevels"] = ["B", "A"]
    cat = catalogue_module.Catalogue(raw)

    assert cat.get("medical").available_levels == []
    assert "medical" not in [b.key for b in cat.requestable()], "nothing left to ask for"
    pending = next(f.value for f in catalogue_embed(cat).fields if f.name == "In development")
    assert "Medical" in pending


@check("a level marked in development must actually exist on the badge")
def _() -> None:
    raw = json.loads(catalogue_module.CATALOGUE_PATH.read_text(encoding="utf-8"))
    for entry in raw["badges"]:
        if entry["key"] == "medical":
            entry["wipLevels"] = ["M"]  # Medical is B / A only
    try:
        catalogue_module.Catalogue(raw)
    except catalogue_module.CatalogueError as error:
        assert "in development" in str(error).lower()
    else:
        raise AssertionError("expected an unknown wip level to be rejected")


@check("a timed test cannot award a level that is in development")
def _() -> None:
    raw = json.loads(catalogue_module.CATALOGUE_PATH.read_text(encoding="utf-8"))
    for entry in raw["badges"]:
        if entry["key"] == "cqc":
            entry["wipLevels"] = ["M"]
    cat = catalogue_module.Catalogue(raw)
    assert cat.awardable_levels("cqc", None) == ["B", "A", "E"], "Master is not runnable yet"


@check("the editor can mark individual levels in development")
def _() -> None:
    from ctc.config_views import ConfigEditorView

    raw = json.loads(catalogue_module.CATALOGUE_PATH.read_text(encoding="utf-8"))
    for entry in raw["badges"]:
        if entry["key"] == "grenadier":
            entry["wipLevels"] = ["A", "E"]
    cat = catalogue_module.Catalogue(raw)
    cog = FakeCog(cat)

    view = ConfigEditorView(cog, FakeUser(), "grenadier")
    assert_component_limits(view, "configEditor/wipLevels")

    wip = select_named(view, "in development")
    assert wip is not None, "the editor offers a levels-in-development picker"
    assert [o.value for o in wip.options] == ["B", "A", "E"], "its own ladder, nothing else"
    assert [o.value for o in wip.options if o.default] == ["A", "E"], "prefilled from the badge"
    assert wip.min_values == 0, "clearing it marks everything available"
    assert "in development: Advanced, Expert" in view.content()

    # A Tab has no levels, so the picker is not offered at all.
    tab = ConfigEditorView(cog, FakeUser(), "airborne")
    assert_component_limits(tab, "configEditor/tab")
    assert select_named(tab, "in development") is None


@check("role settings survive being given a list")
def _() -> None:
    # Regression: role_ids() handled lists but four call sites still did
    # int(settings[...]), which threw TypeError as soon as a setting held more
    # than one role. It crashed the nudge loop hourly and broke every request.
    from cogs.ctc import CTC, DEFAULTS

    class Bot:
        config = {"discord": {"combat_training_centre": {
            "instructor_role_id": [111, 222],
            "config_role_id": 333,
            "panel_role_id": [],
        }}}

    cog = CTC.__new__(CTC)
    cog.units = Units(Bot.config["discord"]["combat_training_centre"], DEFAULTS)
    unit = cog.units.require("default")

    assert cog.role_ids(unit, "instructor_role_id") == [111, 222], "a list of roles"
    assert cog.role_ids(unit, "config_role_id") == [333], "a bare id still works"
    assert cog.role_ids(unit, "panel_role_id") == [], "empty means unset"

    # Every configured role gets mentioned, not just the first.
    assert cog.role_mention(unit, "instructor_role_id", "x") == "<@&111> <@&222>"
    assert cog.role_mention(unit, "config_role_id", "x") == "<@&333>"
    assert cog.role_mention(unit, "panel_role_id", "nobody") == "nobody", "falls back when unset"

    # Nothing may pass a role setting straight to int().
    source = (ROOT.parent / "cogs" / "ctc.py").read_text(encoding="utf-8")
    for setting in ("instructor_role_id", "config_role_id", "panel_role_id"):
        assert f'int(settings["{setting}"]' not in source, setting
        assert f'int(self.settings["{setting}"]' not in source, setting

    assert set(DEFAULTS) >= {"instructor_role_id", "config_role_id", "panel_role_id"}


@check("the panel renders with and without the catalogue")
def _() -> None:
    plain = entry_point_payload(CAT, with_catalogue=False, unit="default")
    assert len(plain["embeds"]) == 1
    both = entry_point_payload(CAT, with_catalogue=True, unit="default")
    assert len(both["embeds"]) == 2
    assert both["embeds"][0].title.endswith("Badge catalogue")
    panel_item = plain["view"].children[0]
    assert isinstance(panel_item, PanelButton), "the panel must be a persistent dynamic item"


@check("the panel button is not swallowed by the ticket button template")
def _() -> None:
    # Regression: the panel used custom_id "ctc:panel:0" against a loose
    # `[a-z]+` template, so DynamicItem dispatched it as action "panel" on
    # request 0 and the member got "Request #0 no longer exists."
    pattern = TicketButton.__discord_ui_compiled_template__

    assert pattern.fullmatch(PANEL_CUSTOM_ID) is None, (
        f"{PANEL_CUSTOM_ID} is captured by the ticket template"
    )
    assert pattern.fullmatch("ctc:panel:0") is None, "the old colliding id must not match either"

    # Every real ticket action still routes, and nothing else does.
    for action in TICKET_ACTIONS:
        match = pattern.fullmatch(f"ctc:{action}:42")
        assert match and match["action"] == action and match["rid"] == "42", action
    for bogus in ("ctc:sync:1", "ctc:claim:abc", "ctc:claim", "ctc:claimed:1", "other:claim:1"):
        assert pattern.fullmatch(bogus) is None, bogus


# ------------------------------------------------------------- lifecycle


async def lifecycle() -> list[tuple[str, Exception | None]]:
    results: list[tuple[str, Exception | None]] = []

    async def step(name, fn):
        try:
            await fn()
            results.append((name, None))
        except Exception as error:  # noqa: BLE001
            results.append((name, error))

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.db"
        connection = await aiosqlite.connect(path)
        with (ROOT / "schema.sql").open() as handle:
            await connection.executescript(handle.read())
        await connection.commit()
        db = CTCDatabaseManager(connection=connection, catalogue=lambda _unit: CAT)

        state: dict[str, object] = {}

        async def one_ticket_per_badge():
            rows = await db.create_group(
                guild_id="g",
                unit="default",
                member_id="m1",
                member_name="Pvt. Hall",
                notes="Weeknights",
                items=[("airborne", []), ("grenadier", ["B", "A", "E"]), ("cqc", [])],
            )
            assert len(rows) == 3, "three badges, three tickets"
            assert len({r["group_id"] for r in rows}) == 1
            assert rows[0]["levels"] is None, "tabs store no levels"
            assert rows[1]["levels"] == "B,A,E", "all levels ride on one ticket"
            assert rows[2]["levels"] is None, "a timed test requests no levels"
            state["rows"] = rows

        async def happy_path():
            row = state["rows"][1]
            r = await db.transition(
                row["id"],
                "claimed",
                instructor_id="ti1",
                instructor_name="Sgt",
            )
            assert r["status"] == "claimed" and r["instructor_id"] == "ti1"
            r = await db.transition(row["id"], "completed")
            assert r["levels_achieved"] == "B,A,E", "no result given means a clean sweep"
            assert r["instructor_id"] == "ti1", "completing keeps the instructor"
            r = await db.transition(row["id"], "awarded")
            assert r["status"] == "awarded" and r["awarded_at"]

        async def awarded_is_terminal():
            row = state["rows"][1]
            for target in ("claimed", "completed", "cancelled", "requested"):
                try:
                    await db.transition(row["id"], target)
                except TransitionError:
                    pass
                else:
                    raise AssertionError(f"awarded -> {target} should be blocked")

        async def double_click_is_harmless():
            rows = await db.create_group(
                guild_id="g", unit="default", member_id="m2", member_name="Pvt. Two", notes=None,
                items=[("scouting", ["B"])],
            )
            rid = rows[0]["id"]
            await db.transition(rid, "claimed", instructor_id="ti1", instructor_name="Sgt")
            try:
                await db.transition(rid, "claimed", instructor_id="ti2", instructor_name="Cpl")
            except TransitionError:
                pass
            else:
                raise AssertionError("second claim should be refused")
            assert (await db.by_id(rid))["instructor_id"] == "ti1", "no claim stealing"

        async def timed_awards_one_level():
            rows = await db.create_group(
                guild_id="g", unit="default", member_id="m3", member_name="Pvt. Three", notes=None,
                items=[("gun_range", [])],
            )
            rid = rows[0]["id"]
            await db.transition(rid, "claimed", instructor_id="ti1", instructor_name="Sgt")

            try:
                await db.transition(rid, "completed", levels_achieved=["A", "E"], variant="Rifle")
            except TransitionError:
                pass
            else:
                raise AssertionError("a timed run cannot earn two levels")

            try:
                await db.transition(rid, "completed", levels_achieved=["E"])
            except TransitionError:
                pass
            else:
                raise AssertionError("Gun Range must say which weapon")

            try:
                await db.transition(rid, "completed", levels_achieved=["E"], variant="Crossbow")
            except TransitionError:
                pass
            else:
                raise AssertionError("variant must be real")

            r = await db.transition(rid, "completed", levels_achieved=["E"], variant="Shotgun")
            assert r["levels_achieved"] == "E" and r["variant"] == "Shotgun"
            state["timed"] = r

        async def graded_can_clear_several():
            rows = await db.create_group(
                guild_id="g", unit="default", member_id="m4", member_name="Pvt. Four", notes=None,
                items=[("grenadier", ["B", "A", "E"])],
            )
            rid = rows[0]["id"]
            await db.transition(rid, "claimed", instructor_id="ti1", instructor_name="Sgt")
            r = await db.transition(rid, "completed", levels_achieved=["E", "B"])
            assert r["levels_achieved"] == "B,E", "graded badges are unaffected, and still sort"
            state["partial"] = r

        async def reopen_clears_result():
            row = state["timed"]
            r = await db.reopen(row["id"], instructor_id="ti9", instructor_name="Fixer")
            assert r["status"] == "claimed"
            assert r["levels_achieved"] is None and r["variant"] is None
            assert r["instructor_id"] == "ti9", "whoever reopened owns it"
            assert r["amended_at"]

        async def amend_the_request():
            rows = await db.create_group(
                guild_id="g", unit="default", member_id="m5", member_name="Pvt. Five", notes=None,
                items=[("grenadier", ["B"])],
            )
            rid = rows[0]["id"]
            r = await db.amend_request(
                rid,
                levels=["E", "B", "A"],
                actor_id="ti9",
                actor_name="Fixer",
            )
            assert r["levels"] == "B,A,E", "stored in progression order"
            assert r["status"] == "requested", "amending does not move it along"
            assert r["amended_by_name"] == "Fixer"

            for bad in (["M"], []):
                try:
                    await db.amend_request(rid, levels=bad, actor_id="x", actor_name="X")
                except TransitionError:
                    pass
                else:
                    raise AssertionError(f"{bad} should be refused")

            await db.transition(rid, "claimed", instructor_id="ti1", instructor_name="Sgt")
            await db.transition(rid, "completed")
            try:
                await db.amend_request(rid, levels=["B"], actor_id="x", actor_name="X")
            except TransitionError:
                pass
            else:
                raise AssertionError("a finished request must be reopened first")

        async def timed_and_tabs_have_nothing_to_amend():
            rows = await db.create_group(
                guild_id="g", unit="default", member_id="m6", member_name="Pvt. Six", notes=None,
                items=[("cqc", []), ("airborne", [])],
            )
            for row in rows:
                try:
                    await db.amend_request(row["id"], levels=["B"], actor_id="x", actor_name="X")
                except TransitionError:
                    pass
                else:
                    raise AssertionError(f"{row['badge_key']} has nothing to amend")

        async def queue_and_stale():
            open_rows = await db.queue()
            assert all(r["status"] in ("requested", "claimed", "completed") for r in open_rows)
            assert not [r for r in open_rows if r["status"] == "awarded"]

            assert await db.stale("requested", 48) == []
            await connection.execute(
                "UPDATE ctc_requests SET created_at = datetime('now','-96 hours') "
                "WHERE status = 'requested'"
            )
            await connection.commit()
            stale = await db.stale("requested", 48)
            assert stale, "something should be overdue"
            for row in stale:
                await db.mark_nudged(row["id"])
            assert await db.stale("requested", 48) == [], "no re-nudging within 24h"

        async def panels_are_tracked_for_refresh():
            assert await db.panels() == [], "nothing tracked yet"

            await db.add_panel("chan-1", "msg-1", True, "default")
            await db.add_panel("chan-1", "msg-2", False, "default")
            assert len(await db.panels()) == 2

            # Only panels embedding a catalogue need re-rendering.
            with_cat = await db.panels(with_catalogue_only=True)
            assert [r["message_id"] for r in with_cat] == ["msg-1"]

            # Re-posting over the same message must not duplicate the row.
            await db.add_panel("chan-1", "msg-1", True, "default")
            assert len(await db.panels()) == 2

            await db.remove_panel("msg-1")
            assert [r["message_id"] for r in await db.panels()] == ["msg-2"]

        async def stats_report():
            stats = await db.stats()
            assert any(r["status"] == "awarded" for r in stats["by_status"])
            assert stats["oldest_open"] is not None

        async def assigning_a_request():
            rows = await db.create_group(
                guild_id="g", unit="default", member_id="m9", member_name="Pvt. Nine", notes=None,
                items=[("rotary", ["B"])],
            )
            rid = rows[0]["id"]

            # Assigning unclaimed work picks it up in the same move.
            done = await db.assign(rid, instructor_id="900", instructor_name="Kondor")
            assert done["status"] == "claimed", done["status"]
            assert done["instructor_id"] == "900"
            assert done["instructor_name"] == "Kondor"
            assert done["claimed_at"], "claiming must be stamped"

            # Reassigning is not a state change, so it must not trip the
            # claimed -> claimed guard that transition() enforces.
            moved = await db.assign(rid, instructor_id="901", instructor_name="Other")
            assert moved["status"] == "claimed"
            assert moved["instructor_id"] == "901"
            assert moved["instructor_name"] == "Other"

            # That guard must stay in place for ordinary claims.
            try:
                await db.transition(rid, "claimed", instructor_id="902", instructor_name="No")
            except TransitionError:
                pass
            else:
                raise AssertionError("transition should still refuse claimed -> claimed")

            # Finished work cannot be handed to anyone.
            await db.transition(rid, "cancelled")
            try:
                await db.assign(rid, instructor_id="903", instructor_name="Nope")
            except TransitionError as error:
                assert "cancelled" in str(error), error
            else:
                raise AssertionError("a cancelled request must not be assignable")

        async def cards_render_for_every_status():
            row = dict(state["rows"][1])
            ellipsis = "…"
            for status, expected in [
                ("requested", ["Claim", "Cancel", f"Assign{ellipsis}"]),
                (
                    "claimed",
                    ["All passed", "Partial", "Release", f"Reassign{ellipsis}", "Cancel"],
                ),
                ("completed", ["Open taw.net", "Awarded on taw.net", "Reopen"]),
                ("awarded", []),
                ("cancelled", []),
            ]:
                row["status"] = status
                row["instructor_id"] = None if status == "requested" else "ti1"
                view = ticket_view(CAT, row, award_url="https://www.taw.net/")
                assert_component_limits(view, f"ticket/{status}")
                assert labels(view) == expected, f"{status}: {labels(view)}"
                ticket_embed(CAT, row)

        async def timed_card_and_buttons():
            row = dict(state["timed"])
            row.update(status="claimed", levels_achieved=None, variant=None, instructor_id="ti1")
            view = ticket_view(CAT, row)
            assert labels(view) == [
                "Record result",
                "Release",
                "Reassign…",
                "Cancel",
            ]
            embed = ticket_embed(CAT, row)
            timed_field = next(f for f in embed.fields if "Timed test" in f.name)
            assert "set by the score achieved" in timed_field.value
            assert "Rifle · Pistol" in timed_field.value

            row.update(status="completed", levels_achieved="E", variant="Shotgun")
            embed = ticket_embed(CAT, row)
            assert next(f for f in embed.fields if f.name == "Run").value == "Shotgun"
            assert not [f for f in embed.fields if "Partial" in f.name]

        async def partial_card_spells_it_out():
            row = dict(state["partial"])
            embed = ticket_embed(CAT, row)
            levels = next(f for f in embed.fields if f.name.startswith("Levels to run"))
            assert levels.value.splitlines() == [
                "\N{WHITE HEAVY CHECK MARK} Basic",
                "\N{CROSS MARK} Advanced",
                "\N{WHITE HEAVY CHECK MARK} Expert",
            ], levels.value
            warning = next(f for f in embed.fields if "Partial" in f.name)
            assert "Not achieved: Advanced" in warning.value

        async def result_view_shapes():
            cog = FakeCog(CAT)
            gun_range = dict(state["timed"])
            view = ResultView(cog, gun_range, FakeUser())
            assert_component_limits(view, "result/variant")
            variant = select_named(view, "which one was run")
            assert [o.value for o in variant.options] == CAT.variants("gun_range")
            level = select_named(view, "level earned")
            assert level.max_values == 1, "one run, one level"
            assert level.min_values == 0, "or none, if they failed"
            confirm = button_named(view, "Confirm result")
            assert confirm.disabled is True, "cannot confirm without a variant"

            view.variant = "SMG"
            view.rebuild()
            confirm = button_named(view, "Confirm result")
            assert confirm.disabled is False
            assert "Run: **SMG**" in view.content()

            graded = dict(state["partial"])
            view = ResultView(cog, graded, FakeUser())
            assert_component_limits(view, "result/graded")
            level = select_named(view, "levels passed")
            assert level.max_values == len(CAT.parse_levels("grenadier", graded["levels"]))
            assert button_named(view, "Confirm result").disabled is False

        async def amend_view_shapes():
            cog = FakeCog(CAT)
            row = dict(state["rows"][1])
            row.update(status="requested", levels="B,A")
            view = AmendView(cog, row, FakeUser())
            assert_component_limits(view, "amend/open")
            select = select_named(view, "levels this request needs")
            assert [o.value for o in select.options] == ["B", "A", "E"], "the full ladder"
            assert [o.value for o in select.options if o.default] == ["B", "A"]
            assert select.min_values == 1, "cannot empty a request"
            assert button_named(view, "Save changes").disabled is True

            view.levels = ["B", "A", "E"]
            view.rebuild()
            assert button_named(view, "Save changes").disabled is False
            assert "Change to — **Basic, Advanced, Expert**" in view.content()

            row["status"] = "awarded"
            finished = AmendView(cog, row, FakeUser())
            assert labels(finished) == ["Reopen for re-run", "Cancel"]
            assert "Already marked" in finished.content()

        for name, fn in [
            ("one badge is one ticket, however many levels", one_ticket_per_badge),
            ("the happy path walks requested to awarded", happy_path),
            ("awarded is terminal", awarded_is_terminal),
            ("a double-clicked claim cannot steal a ticket", double_click_is_harmless),
            ("a timed test awards one level and needs its variant", timed_awards_one_level),
            ("a graded badge can clear several levels at once", graded_can_clear_several),
            ("reopening clears the result and reassigns", reopen_clears_result),
            ("an open request's levels can be amended", amend_the_request),
            ("timed tests and tabs have nothing to amend", timed_and_tabs_have_nothing_to_amend),
            ("the queue excludes finished work and nudges cool down", queue_and_stale),
            (
                "panels are tracked so the catalogue can be refreshed",
                panels_are_tracked_for_refresh,
            ),
            ("stats report status, load and turnaround", stats_report),
            ("a request can be assigned, reassigned and picked up", assigning_a_request),
            ("ticket cards render the right buttons per status", cards_render_for_every_status),
            ("a timed ticket shows the ladder, then the result", timed_card_and_buttons),
            ("a partial result is spelled out on the card", partial_card_spells_it_out),
            ("the result form adapts to badge kind", result_view_shapes),
            ("the amend form offers the full ladder", amend_view_shapes),
        ]:
            await step(name, fn)

        await connection.close()
    return results



@check("extraTrainers must be real Discord mentions")
def _() -> None:
    import copy

    raw = json.loads((ROOT / "catalogue.json").read_text(encoding="utf-8"))
    for bad in ("189362064995778560", "<@abc>", "@someone", "<@&12>"):
        doc = copy.deepcopy(raw)
        doc["badges"][0]["extraTrainers"] = [bad]
        try:
            catalogue_module.Catalogue(doc)
        except catalogue_module.CatalogueError:
            continue
        raise AssertionError(f"catalogue accepted {bad!r} as a mention")

    # A role mention is as valid as a user mention.
    doc = copy.deepcopy(raw)
    doc["badges"][0]["extraTrainers"] = ["<@&1001589735581552731>"]
    catalogue_module.Catalogue(doc)


@check("split_mentions sorts users from roles")
def _() -> None:
    from cogs.ctc import split_mentions

    users, roles = split_mentions(
        ["<@&111111111111111111>", "<@189362064995778560>", "<@!222222222222222222>"]
    )
    assert roles == [111111111111111111], roles
    assert users == [189362064995778560, 222222222222222222], users
    assert split_mentions([]) == ([], [])


@check("the aviation badges carry their extra trainer")
def _() -> None:
    # Fixed Wing and Rotary cannot be run by every instructor, so they name
    # someone specific. If this ever trips, the catalogue was edited rather
    # than the test being wrong -- check that was intended.
    for key in ("fixed_wing", "rotary"):
        extra = CAT.extra_trainers(key)
        assert extra, f"{key} lost its extra trainer"
        assert all(m.startswith("<@") for m in extra), extra
    assert CAT.extra_trainers("gun_range") == []


@check("the opening ping spoilers the tags and never pings an empty set")
def _() -> None:
    src = (ROOT.parent / "cogs" / "ctc.py").read_text(encoding="utf-8")
    assert "We will assist with this as soon as we can." in src
    # The old wording named the roles inline; it must be gone entirely.
    assert "will be able to assist with this when they can" not in src
    # Tags go inside a spoiler, and only when there is something to tag.
    assert 'greeting += ' in src
    assert '" ".join(pings)' in src
    assert "if pings:" in src
    # The spoiler bars must actually be in the source that builds the line.
    assert src.count("||") >= 2



@check("the badge editor offers a trainers screen and still fits five rows")
def _() -> None:
    from ctc.config_views import ConfigEditorView

    for key in ("rotary", "medical", "airborne"):
        view = ConfigEditorView(FakeCog(CAT), FakeUser(), key)
        assert_component_limits(view, f"editor {key}")
        names = labels(view)
        assert any((n or "").startswith("Trainers") for n in names), names
        # Row 3 is the edit-action row and is now full; a sixth button there
        # would silently overflow into another row.
        row3 = [b for b in buttons(view) if b.row == 3]
        assert len(row3) <= 5, f"{key}: {len(row3)} buttons on row 3"

    # The count is surfaced on the button, so it is visible without opening it.
    def trainers_label(key: str) -> str:
        view = ConfigEditorView(FakeCog(CAT), FakeUser(), key)
        return button_named(view, "Trainers").label

    assert trainers_label("rotary") == "Trainers (1)"
    assert trainers_label("medical") == "Trainers"


@check("the trainers screen pre-selects who is already set")
def _() -> None:
    import discord

    from ctc.config_views import ExtraTrainersView

    view = ExtraTrainersView(FakeCog(CAT), FakeUser(), "rotary")
    assert_component_limits(view, "trainers rotary")
    picker = selects(view)[0]
    assert isinstance(picker, discord.ui.MentionableSelect), type(picker)
    assert picker.min_values == 0, "must allow clearing by picking nothing"

    defaults = picker.default_values
    assert len(defaults) == 1, defaults
    assert defaults[0].id == 189362064995778560, defaults
    assert defaults[0].type is discord.SelectDefaultValueType.user, defaults[0].type

    # Clear only appears when there is something to clear.
    assert "Clear" in labels(view)
    assert "Clear" not in labels(ExtraTrainersView(FakeCog(CAT), FakeUser(), "medical"))
    assert "Back" in labels(view)


@check("a picked role and a picked user become the right mentions")
def _() -> None:
    from ctc.config_views import _as_default, _as_mention

    class FakeRole(discord.Role):
        # A real Role subclass, so the isinstance branch in _as_mention is the
        # thing under test rather than a stand-in for it.
        def __init__(self) -> None:
            self.id = 1001589735581552731

    class FakeMember:
        id = 189362064995778560

    assert _as_mention(FakeRole()) == "<@&1001589735581552731>"
    assert _as_mention(FakeMember()) == "<@189362064995778560>"

    # And back again, so a saved value re-selects itself next time.
    assert _as_default("<@&1001589735581552731>").type is discord.SelectDefaultValueType.role
    assert _as_default("<@189362064995778560>").type is discord.SelectDefaultValueType.user
    assert _as_default("<@189362064995778560>").id == 189362064995778560



@check("an extra trainer can work their own badge and nothing else")
def _() -> None:
    from cogs.ctc import CTC

    INSTRUCTOR_ROLE = 1001588316602368090
    AVIATOR = 189362064995778560
    OUTSIDER = 999999999999999999

    cog = CTC.__new__(CTC)
    cog.units = Units({"instructor_role_id": INSTRUCTOR_ROLE}, DEFAULTS)
    unit = cog.units.require("default")

    instructor = FakeMember(555555555555555555, [INSTRUCTOR_ROLE])
    aviator = FakeMember(AVIATOR)
    outsider = FakeMember(OUTSIDER)

    # The named aviator works Rotary and Fixed Wing...
    for key in ("rotary", "fixed_wing"):
        assert cog.can_work(unit, aviator, key), f"aviator locked out of {key}"
    # ...but is nobody special on a badge that does not name them.
    assert not cog.can_work(unit, aviator, "medical"), "aviator should not reach Medical"

    # Instructors keep working everything, including the aviation badges.
    for key in ("rotary", "medical", "airborne"):
        assert cog.can_work(unit, instructor, key), f"instructor locked out of {key}"

    # Everyone else stays out.
    for key in ("rotary", "medical"):
        assert not cog.can_work(unit, outsider, key), f"outsider reached {key}"

    # A missing or unknown badge key must not throw or grant anything.
    assert not cog.can_work(unit, aviator, None)
    assert not cog.can_work(unit, aviator, "no_such_badge")


@check("a role named as an extra trainer also grants access")
def _() -> None:
    import copy

    from cogs.ctc import CTC

    SPECIALIST_ROLE = 1001589735581552731

    raw = copy.deepcopy(json.loads((ROOT / "catalogue.json").read_text(encoding="utf-8")))
    next(b for b in raw["badges"] if b["key"] == "medical")["extraTrainers"] = [
        f"<@&{SPECIALIST_ROLE}>"
    ]
    cat = catalogue_module.Catalogue(raw)

    cog = CTC.__new__(CTC)
    cog.units = Units({"instructor_role_id": 1001588316602368090}, DEFAULTS)
    unit = cog.units.require("default")
    unit.catalogue = cat

    holder = FakeMember(777777777777777777, [SPECIALIST_ROLE])
    assert cog.can_work(unit, holder, "medical"), "role-based extra trainer locked out"
    assert not cog.can_work(unit, holder, "rotary"), "role should not carry to other badges"


@check("named trainers are added to the thread, roles cannot be")
def _() -> None:
    src = (ROOT.parent / "cogs" / "ctc.py").read_text(encoding="utf-8")
    # The requester and the badge's named people are added in one pass.
    assert "for uid in (int(row[\"member_id\"]), *extra_users):" in src
    assert "extra_users, _ = split_mentions(extra)" in src
    # Permission alone is not enough on a private thread, so this must stay.
    assert "add_user" in src



@check("only the assign role may set who is working on a request")
def _() -> None:
    from cogs.ctc import CTC

    INSTRUCTOR = 1001588316602368090
    SPECIALIST = 1001589735581552731

    def cog_with(**ctc_settings):
        cog = CTC.__new__(CTC)
        cog.units = Units({"instructor_role_id": INSTRUCTOR, **ctc_settings}, DEFAULTS)
        return cog

    def unit_of(cog):
        return cog.units.require("default")

    instructor = FakeMember(1, [INSTRUCTOR])
    specialist = FakeMember(2, [SPECIALIST])
    nobody = FakeMember(3)

    # Unset: falls back to the instructor roles, so it works out of the box.
    loose = cog_with()
    assert loose.can_assign(unit_of(loose), instructor)
    assert not loose.can_assign(unit_of(loose), specialist)
    assert not loose.can_assign(unit_of(loose), nobody)

    # Set: narrows to exactly that role, instructors included.
    tight = cog_with(assign_role_id=SPECIALIST)
    assert tight.can_assign(unit_of(tight), specialist)
    assert not tight.can_assign(unit_of(tight), instructor), "narrowing must actually exclude"
    assert not tight.can_assign(unit_of(tight), nobody)

    # A list of roles works, as everywhere else.
    both = cog_with(assign_role_id=[SPECIALIST, INSTRUCTOR])
    assert both.can_assign(unit_of(both), specialist) and both.can_assign(unit_of(both), instructor)
    assert not both.can_assign(unit_of(both), nobody)


@check("the assign picker preselects whoever holds the request")
def _() -> None:
    from ctc.views import AssignView

    claimed = {"id": 1, "instructor_id": "189362064995778560", "badge_key": "rotary",
               "levels": "B", "member_id": "5", "status": "claimed", "unit": "default"}
    view = AssignView(FakeCog(CAT), claimed)
    assert_component_limits(view, "assign")
    picker = selects(view)[0]
    assert isinstance(picker, discord.ui.UserSelect), type(picker)
    assert picker.min_values == 1 and picker.max_values == 1, "exactly one person"
    assert [d.id for d in picker.default_values] == [189362064995778560]

    # Nothing preselected on unclaimed work.
    unclaimed = {**claimed, "instructor_id": None, "status": "requested"}
    assert selects(AssignView(FakeCog(CAT), unclaimed))[0].default_values == []


@check("assign is a real ticket action and survives a restart")
def _() -> None:
    # The button is a DynamicItem, so the action must be in the template or the
    # custom_id stops matching after a restart and the button goes dead.
    assert "assign" in TICKET_ACTIONS
    pattern = TicketButton.__discord_ui_compiled_template__
    assert pattern.fullmatch("ctc:assign:42"), "assign custom_id must match the template"
    # And it must not collide with the panel button, as an earlier action did.
    assert not pattern.fullmatch(PANEL_CUSTOM_ID)



@check("a closed thread is prefixed, once, and still fits Discord's name limit")
def _() -> None:
    from cogs.ctc import CLOSED_PREFIX, MAX_THREAD_NAME, closed_name, open_name

    assert closed_name("Almerra — Rotary B") == "CLOSED: Almerra — Rotary B"

    # Closing twice must not stack prefixes -- reopen/close cycles are normal.
    once = closed_name("Almerra — Rotary B")
    assert closed_name(once) == once

    # A name already at the limit still fits once prefixed.
    long_name = "x" * MAX_THREAD_NAME
    assert len(closed_name(long_name)) == MAX_THREAD_NAME
    assert closed_name(long_name).startswith(CLOSED_PREFIX)

    # Reopening takes it back off, and leaves an unprefixed name alone.
    assert open_name("CLOSED: Almerra — Rotary B") == "Almerra — Rotary B"
    assert open_name("Almerra — Rotary B") == "Almerra — Rotary B"
    assert open_name(closed_name("Almerra — CQC")) == "Almerra — CQC"


@check("closing renames even when archiving is off, and reopening undoes it")
def _() -> None:
    src = (ROOT.parent / "cogs" / "ctc.py").read_text(encoding="utf-8")
    close = src[src.index("async def close_thread"):src.index("async def reopen_thread")]
    # The early return must only be about a missing thread; gating the whole
    # method on archive_on_award would skip the rename too.
    assert 'if not row["thread_id"]:' in close
    assert "archive_on_award" in close, "archiving is still conditional"
    assert close.index('if not row["thread_id"]:') < close.index("archive_on_award")
    # One edit call -- renaming an archived thread needs it unarchived first.
    assert close.count("await thread.edit(") == 1, "name and archive must be one edit"
    # Reopening a request has to clear the prefix or the title lies.
    assert "await self.reopen_thread(updated)" in src


@check("the daily bump is silent, skips archived threads, and can be switched off")
def _() -> None:
    from cogs.ctc import DEFAULTS

    assert DEFAULTS["daily_bump"] is True

    src = (ROOT.parent / "cogs" / "ctc.py").read_text(encoding="utf-8")
    bump = src[src.index("async def bump_loop"):src.index("async def clear_bump")]
    assert '"Bump to keep alive"' in bump
    # No pings: the point is to keep threads alive, not to notify anyone.
    assert "AllowedMentions.none()" in bump
    # Posting into an archived thread would silently unarchive it.
    assert 'getattr(thread, "archived", False)' in bump
    assert 'daily_bump' in bump
    # Runs once a day at a fixed time, not on an interval.
    assert "tasks.loop(time=datetime.time(hour=0, minute=0" in src
    assert "self.bump_loop.start()" in src
    assert "self.bump_loop.cancel()" in src


@check("each bump replaces the last rather than piling up")
def _() -> None:
    src = (ROOT.parent / "cogs" / "ctc.py").read_text(encoding="utf-8")
    bump = src[src.index("async def bump_loop"):src.index("async def clear_bump")]

    # Order matters: the new bump must land before the old one is removed, so
    # the thread is never left without a message holding the clock open.
    post = bump.index("await thread.send(")
    clear = bump.index("await self.clear_bump(")
    record = bump.index("set_bump_message(")
    assert post < clear < record, "post, then clear, then record"

    # A failed post must not delete yesterday's — that would strand the thread.
    assert "continue" in bump[bump.index("except discord.HTTPException"):]

    # Closing tidies the last one away, before the thread is archived.
    close = src[src.index("async def close_thread"):src.index("async def reopen_thread")]
    assert "await self.clear_bump(" in close
    assert close.index("clear_bump") < close.index("await thread.edit(")


@check("the bump column is added to databases that predate it")
def _() -> None:
    import asyncio as _asyncio

    from cogs.ctc import LATER_COLUMNS, ensure_columns

    assert "bump_message_id" in LATER_COLUMNS["ctc_requests"]
    # It must also be in the schema, or a fresh database would rely on the
    # migration and the two would drift.
    assert "bump_message_id" in (ROOT / "schema.sql").read_text(encoding="utf-8")

    async def run() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.db"
            async with aiosqlite.connect(path) as db:
                # A table as it existed before the column was introduced.
                await db.execute(
                    "CREATE TABLE ctc_requests (id INTEGER PRIMARY KEY, status TEXT)"
                )
                await db.execute("INSERT INTO ctc_requests (status) VALUES ('requested')")
                await db.commit()

                await ensure_columns(db, "default")
                await db.commit()
                cursor = await db.execute("PRAGMA table_info(ctc_requests)")
                assert "bump_message_id" in {r[1] for r in await cursor.fetchall()}

                # Running again must not raise -- it happens on every startup.
                await ensure_columns(db, "default")
                await db.commit()
                cursor = await db.execute("SELECT COUNT(*) FROM ctc_requests")
                assert (await cursor.fetchone())[0] == 1, "existing rows survive"

    _asyncio.run(run())



@check("a single-battalion config still works untouched")
def _() -> None:
    # config.json lives on the host and is not deployed with the code, so an
    # upgrade must not require editing it before the bot will start.
    units = Units({"instructor_role_id": 111, "queue_channel_id": 222}, DEFAULTS)
    assert units.keys() == ["default"], units.keys()
    assert units.primary == "default"
    assert units.only is not None, "one unit means never having to ask which"
    assert units.require("default").settings["instructor_role_id"] == 111


@check("units keep separate badges, channels and staff")
def _() -> None:
    cfg = {
        "taw_award_url": "https://www.taw.net/",
        "primary_unit": "am2",
        "units": {
            "am2": {"name": "2nd Battalion", "queue_channel_id": 10, "instructor_role_id": [1]},
            "am1": {"name": "1st Battalion", "queue_channel_id": 20, "instructor_role_id": [2]},
        },
    }
    units = Units(cfg, DEFAULTS)
    assert len(units) == 2
    assert units.primary == "am2"
    assert units.only is None, "two units must never resolve implicitly"

    # Shared keys reach both; per-unit keys do not leak.
    assert units.require("am1").settings["taw_award_url"] == "https://www.taw.net/"
    assert units.require("am1").settings["queue_channel_id"] == 20
    assert units.require("am2").settings["queue_channel_id"] == 10

    # Routing by channel, including a thread's parent.
    assert units.for_channel(10).key == "am2"
    assert units.for_channel(20).key == "am1"
    assert units.for_channel(999) is None

    # Routing by role, and the ambiguity guard that matters most.
    assert units.for_member(FakeMember(1, [1])).key == "am2"
    assert units.for_member(FakeMember(2, [2])).key == "am1"
    assert units.for_member(FakeMember(3, [1, 2])) is None, "both battalions is ambiguous"
    assert units.for_member(FakeMember(4)) is None


@check("a bad units block is rejected rather than half-loaded")
def _() -> None:
    from ctc.units import UnitError

    bad = [
        ({"units": {"Bad Key": {}}}, "key"),
        ({"units": {"am1": {"catalogue": "nope.json"}}}, "does not exist"),
        ({"units": {"am1": {}}, "primary_unit": "am9"}, "primary_unit"),
    ]
    for cfg, fragment in bad:
        try:
            Units(cfg, DEFAULTS)
        except UnitError as error:
            assert fragment.lower() in str(error).lower(), f"{fragment} vs {error}"
        else:
            raise AssertionError(f"expected {fragment!r} to be rejected")


@check("panels carry their unit and old ones still resolve")
def _() -> None:
    from ctc.views import LEGACY_PANEL_UNIT, PanelButton

    pattern = PanelButton.__discord_ui_compiled_template__
    assert pattern.fullmatch("ctc:panel:am2")["unit"] == "am2"
    assert pattern.fullmatch("ctc:panel:am1")["unit"] == "am1"

    # A panel pinned before battalions existed must keep working.
    legacy = pattern.fullmatch(f"ctc:panel:{LEGACY_PANEL_UNIT}")
    assert legacy is not None and legacy["unit"] == LEGACY_PANEL_UNIT
    assert f"ctc:panel:{LEGACY_PANEL_UNIT}" == PANEL_CUSTOM_ID

    # The two templates must not poach each other's ids -- a loose panel
    # pattern is what broke ticket buttons once already.
    ticket = TicketButton.__discord_ui_compiled_template__
    assert ticket.fullmatch("ctc:panel:am2") is None
    assert pattern.fullmatch("ctc:claim:42") is None


@check("the schema is applied after the columns it indexes exist")
def _() -> None:
    # Upgrade-only trap: CREATE TABLE IF NOT EXISTS leaves a live table alone,
    # so an index over a newly added column runs against a column that is not
    # there yet. Columns must be added before the schema file is executed.
    src = (ROOT.parent / "cogs" / "ctc.py").read_text(encoding="utf-8")
    load = src[src.index("async def cog_load"):src.index("async def cog_unload")]
    assert load.index("ensure_columns(") < load.index("executescript("), (
        "ensure_columns must run before the schema script"
    )
    assert "idx_ctc_requests_unit" in (ROOT / "schema.sql").read_text(encoding="utf-8")



@check("editing one battalion's catalogue leaves the other alone")
def _() -> None:
    # The whole point of separate files: /badge config in one battalion must
    # not touch the other's badges.
    with tempfile.TemporaryDirectory() as tmp:
        one = Path(tmp) / "one.json"
        two = Path(tmp) / "two.json"
        shutil.copy(catalogue_module.CATALOGUE_PATH, one)
        shutil.copy(catalogue_module.CATALOGUE_PATH, two)
        untouched = two.read_text(encoding="utf-8")

        def rename(doc):
            next(b for b in doc["badges"] if b["key"] == "medical")["name"] = "Field Medicine"

        edited = catalogue_module.mutate(rename, one)
        assert edited.get("medical").name == "Field Medicine"
        assert two.read_text(encoding="utf-8") == untouched, "the other file must not move"
        assert catalogue_module.load(two).get("medical").name == "Medical"


@check("the shipped AM1 catalogue loads alongside AM2")
def _() -> None:
    am1 = ROOT / "am1.json"
    if not am1.exists():
        return  # only shipped once a second battalion is set up

    units = Units(
        {
            "primary_unit": "am2",
            "units": {
                "am2": {"catalogue": "catalogue.json"},
                "am1": {"catalogue": "am1.json"},
            },
        },
        DEFAULTS,
    )
    a2 = units.require("am2").catalogue
    a1 = units.require("am1").catalogue
    assert a1 is not a2
    assert len(a1.all()) and len(a2.all())
    # Extra trainers name specific people, so a copied catalogue must not
    # inherit the other battalion's staff.
    assert not any(a1.extra_trainers(b.key) for b in a1.all()), (
        "AM1 must not start with AM2's named trainers"
    )



@check("member roles gate who may request, and are kept out of routing")
def _() -> None:
    from cogs.ctc import CTC

    TI, TS, MEMBER, OTHER = 10, 20, 30, 40

    cog = CTC.__new__(CTC)
    cog.units = Units(
        {
            "primary_unit": "a",
            "units": {
                "a": {
                    "catalogue": "catalogue.json",
                    "instructor_role_id": [TI, TS],
                    "member_role_id": [MEMBER],
                },
                "b": {"catalogue": "catalogue.json", "instructor_role_id": [OTHER]},
            },
        },
        DEFAULTS,
    )
    a, b = cog.units.require("a"), cog.units.require("b")

    assert cog.can_request(a, FakeMember(1, [MEMBER])), "a member may request"
    assert cog.can_request(a, FakeMember(2, [TI])), "staff may request without the member role"
    assert not cog.can_request(a, FakeMember(3, [OTHER])), "the other battalion may not"
    assert not cog.can_request(a, FakeMember(4)), "nobody with no roles"

    # Unset means anyone, which is how a single-battalion server has behaved.
    assert cog.can_request(b, FakeMember(5)), "no member_role_id means open"

    # A member role must never decide which battalion a loose command is about:
    # a division-wide member role would silently misfile the request.
    assert cog.units.for_member(FakeMember(6, [MEMBER])) is None, (
        "member roles must not drive routing"
    )
    assert cog.units.for_member(FakeMember(7, [TI])).key == "a", "staff roles still route"


@check("the panel is gated the same way as the command")
def _() -> None:
    # Otherwise the pinned panel is a way straight past the member check.
    src = (ROOT / "views.py").read_text(encoding="utf-8")
    panel = src[src.index("class PanelButton"):src.index("def panel_view")]
    assert "cog.can_request(unit, interaction.user)" in panel, "panel must check can_request"

    cog_src = (ROOT.parent / "cogs" / "ctc.py").read_text(encoding="utf-8")
    request = cog_src[
        cog_src.index("async def badge_request") : cog_src.index("async def badge_catalogue")
    ]
    assert "self.can_request(unit, interaction.user)" in request



@check("every command takes a unit option, and it is autocompleted")
def _() -> None:
    from cogs.ctc import CTC

    for command in CTC.badge.walk_commands():
        names = [p.name for p in command.parameters]
        if command.name == "amend":
            # Thread-scoped: the request in the thread already names its unit.
            assert "unit" not in names, "amend needs no unit option"
            continue
        assert "unit" in names, f"/badge {command.name} is missing the unit option"
        assert not command.parameters[names.index("unit")].required, (
            f"/badge {command.name}: unit must stay optional"
        )

    src = (ROOT.parent / "cogs" / "ctc.py").read_text(encoding="utf-8")
    # Choices come from the live config, so adding a battalion is config-only.
    assert "async def unit_autocomplete(" in src
    assert "for unit in cog.units" in src
    assert src.count("@app_commands.autocomplete(unit=unit_autocomplete)") == 6


@check("an unresolvable request asks which battalion instead of refusing")
def _() -> None:
    from ctc.views import UnitPickerView

    cog = FakeCog(CAT)
    cog.units = FakeUnits(
        FakeUnit(CAT, "am2", "2nd Battalion"),
        FakeUnit(CAT, "am1", "1st Battalion"),
    )
    view = UnitPickerView(cog, FakeUser(), None)
    assert_component_limits(view, "unit picker")
    assert labels(view) == ["2nd Battalion", "1st Battalion"], labels(view)

    src = (ROOT.parent / "cogs" / "ctc.py").read_text(encoding="utf-8")
    request = src[src.index("async def badge_request") : src.index("async def open_badge_picker")]
    assert "UnitPickerView(" in request, "an unresolved request must offer the picker"
    # And a named-but-wrong unit is an error, not a silent fall through to one.
    assert "return  # resolve_unit already said the name was wrong" in request


@check("an explicit unit beats everything else")
def _() -> None:
    src = (ROOT.parent / "cogs" / "ctc.py").read_text(encoding="utf-8")
    resolve = src[src.index("async def resolve_unit") : src.index("async def queue_channel")]
    # The explicit choice is checked before channel, thread or roles.
    assert resolve.index("if chosen:") < resolve.index("channel = interaction.channel")
    assert resolve.index("if chosen:") < resolve.index("for_member")



@check("a catalogue edit refreshes only that battalion's panels")
def _() -> None:
    src = (ROOT.parent / "cogs" / "ctc.py").read_text(encoding="utf-8")
    apply = src[src.index("def apply_catalogue_edit") : src.index("async def refresh_panels")]
    assert "refresh_panels(unit=unit.key)" in apply, (
        "editing one unit must not rewrite the other's pinned messages"
    )
    refresh = src[src.index("async def refresh_panels") : src.index("@badge.command(name=\"panel\"")]
    assert "panels(with_catalogue_only=True, unit=unit)" in refresh



@check("site links point at the right battalion's pages, or are absent")
def _() -> None:
    from ctc.views import badge_url

    URL = "http://am2.taw.net:6001"
    # The site names pages after the key with underscores swapped for hyphens.
    assert badge_url(URL, "gun_range") == f"{URL}/badge/gun-range.html"
    assert badge_url(URL, "cqc") == f"{URL}/badge/cqc.html"
    # A trailing slash in config must not double up.
    assert badge_url(URL + "/", "cqc") == f"{URL}/badge/cqc.html"

    row = {
        "id": 1, "member_id": "5", "badge_key": "cqc", "levels": None,
        "levels_achieved": None, "variant": None, "status": "requested",
        "instructor_id": None, "created_at": "2026-01-01 00:00:00", "notes": None,
    }

    # Configured: every surface carries a link.
    assert "badge/cqc.html" in ticket_embed(CAT, row, URL).description
    assert any(f.name == "Full details" for f in catalogue_embed(CAT, URL).fields)
    panel = entry_point_payload(
        CAT, with_catalogue=False, unit="am2", site_url=URL
    )["embeds"][0]
    assert URL in panel.description

    # Unset: nothing is added, so a server without a site sees no dead links.
    assert "http" not in ticket_embed(CAT, row).description
    assert not any(f.name == "Full details" for f in catalogue_embed(CAT).fields)
    bare = entry_point_payload(CAT, with_catalogue=False, unit="am2")["embeds"][0]
    assert "http" not in bare.description


@check("site_url is per battalion, so each links to its own site")
def _() -> None:
    from cogs.ctc import DEFAULTS

    assert DEFAULTS["site_url"] == "", "unset by default"

    units = Units(
        {
            "primary_unit": "am2",
            "units": {
                "am2": {"catalogue": "catalogue.json", "site_url": "http://x:6002"},
                "am1": {"catalogue": "am1.json", "site_url": "http://x:6001"},
            },
        },
        DEFAULTS,
    )
    assert units.require("am2").settings["site_url"] == "http://x:6002"
    assert units.require("am1").settings["site_url"] == "http://x:6001"

    # A shared block still works for a server with one site.
    shared = Units(
        {"site_url": "http://x:6001", "units": {"am1": {"catalogue": "am1.json"}}},
        DEFAULTS,
    )
    assert shared.require("am1").settings["site_url"] == "http://x:6001"


def main() -> int:
    passed = 0
    total = 0

    for name, fn in CHECKS:
        total += 1
        try:
            fn()
            print(f"  ok   {name}")
            passed += 1
        except Exception as error:  # noqa: BLE001
            print(f"  FAIL {name}\n       {error}")
            traceback.print_exc(limit=2)

    for name, error in asyncio.run(lifecycle()):
        total += 1
        if error is None:
            print(f"  ok   {name}")
            passed += 1
        else:
            print(f"  FAIL {name}\n       {error}")

    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
