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

from ctc import CTCDatabaseManager, TransitionError
from ctc import catalogue as catalogue_module
from ctc.views import (
    MAX_BADGES_PER_REQUEST,
    PANEL_CUSTOM_ID,
    TICKET_ACTIONS,
    AmendView,
    BadgePickerView,
    LevelView,
    PanelView,
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


class FakeCog:
    """Just enough of the cog for the views to build."""

    def __init__(self, catalogue) -> None:
        self._catalogue = catalogue

    @property
    def catalogue(self):
        return self._catalogue


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
    view = BadgePickerView(FakeCog(CAT), FakeUser())
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
    view = LevelView(FakeCog(CAT), FakeUser(), ["cqc", "gun_range", "airborne"])
    assert_component_limits(view, "levels/none")
    assert not [i for i in view.children if isinstance(i, discord.ui.Select)]
    submit = button_named(view, "Submit")
    assert submit.disabled is False
    assert "timed test, level set by your result" in view.content()


@check("graded badges gate submission until every one has a level")
def _() -> None:
    view = LevelView(FakeCog(CAT), FakeUser(), ["cqc", "grenadier"])
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
    view = LevelView(FakeCog(CAT), FakeUser(), graded)
    assert_component_limits(view, "levels/page1")
    assert len([i for i in view.children if isinstance(i, discord.ui.Select)]) == 4
    assert "Page 1 of 2" in view.content()

    view.page = 1
    view.rebuild()
    assert_component_limits(view, "levels/page2")
    assert len([i for i in view.children if isinstance(i, discord.ui.Select)]) == 1


@check("each level dropdown offers only that badge's own levels")
def _() -> None:
    view = LevelView(FakeCog(CAT), FakeUser(), ["medical", "grenadier"])
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
    view = LevelView(FakeCog(cat), FakeUser(), ["grenadier"])
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
    cog.bot = Bot()

    assert cog.role_ids("instructor_role_id") == [111, 222], "a list of roles"
    assert cog.role_ids("config_role_id") == [333], "a bare id still works"
    assert cog.role_ids("panel_role_id") == [], "empty means unset"

    # Every configured role gets mentioned, not just the first.
    assert cog.role_mention("instructor_role_id", "x") == "<@&111> <@&222>"
    assert cog.role_mention("config_role_id", "x") == "<@&333>"
    assert cog.role_mention("panel_role_id", "nobody") == "nobody", "falls back when unset"

    # Nothing may pass a role setting straight to int().
    source = (ROOT.parent / "cogs" / "ctc.py").read_text(encoding="utf-8")
    for setting in ("instructor_role_id", "config_role_id", "panel_role_id"):
        assert f'int(settings["{setting}"]' not in source, setting
        assert f'int(self.settings["{setting}"]' not in source, setting

    assert set(DEFAULTS) >= {"instructor_role_id", "config_role_id", "panel_role_id"}


@check("the panel renders with and without the catalogue")
def _() -> None:
    plain = entry_point_payload(CAT, with_catalogue=False)
    assert len(plain["embeds"]) == 1
    both = entry_point_payload(CAT, with_catalogue=True)
    assert len(both["embeds"]) == 2
    assert both["embeds"][0].title.endswith("Badge catalogue")
    assert isinstance(plain["view"], PanelView), "the panel must be a persistent view"


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
        db = CTCDatabaseManager(connection=connection, catalogue=lambda: CAT)

        state: dict[str, object] = {}

        async def one_ticket_per_badge():
            rows = await db.create_group(
                guild_id="g", member_id="m1", member_name="Pvt. Hall", notes="Weeknights",
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
            r = await db.transition(row["id"], "claimed", instructor_id="ti1", instructor_name="Sgt")
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
                guild_id="g", member_id="m2", member_name="Pvt. Two", notes=None,
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
                guild_id="g", member_id="m3", member_name="Pvt. Three", notes=None,
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
                guild_id="g", member_id="m4", member_name="Pvt. Four", notes=None,
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
                guild_id="g", member_id="m5", member_name="Pvt. Five", notes=None,
                items=[("grenadier", ["B"])],
            )
            rid = rows[0]["id"]
            r = await db.amend_request(rid, levels=["E", "B", "A"], actor_id="ti9", actor_name="Fixer")
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
                guild_id="g", member_id="m6", member_name="Pvt. Six", notes=None,
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

            await db.add_panel("chan-1", "msg-1", True)
            await db.add_panel("chan-1", "msg-2", False)
            assert len(await db.panels()) == 2

            # Only panels embedding a catalogue need re-rendering.
            with_cat = await db.panels(with_catalogue_only=True)
            assert [r["message_id"] for r in with_cat] == ["msg-1"]

            # Re-posting over the same message must not duplicate the row.
            await db.add_panel("chan-1", "msg-1", True)
            assert len(await db.panels()) == 2

            await db.remove_panel("msg-1")
            assert [r["message_id"] for r in await db.panels()] == ["msg-2"]

        async def stats_report():
            stats = await db.stats()
            assert any(r["status"] == "awarded" for r in stats["by_status"])
            assert stats["oldest_open"] is not None

        async def assigning_a_request():
            rows = await db.create_group(
                guild_id="g", member_id="m9", member_name="Pvt. Nine", notes=None,
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
            ("panels are tracked so the catalogue can be refreshed", panels_are_tracked_for_refresh),
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
    cog._catalogue = CAT
    cog.bot = type("B", (), {"config": {"discord": {"combat_training_centre": {
        "instructor_role_id": INSTRUCTOR_ROLE}}}})()

    instructor = FakeMember(555555555555555555, [INSTRUCTOR_ROLE])
    aviator = FakeMember(AVIATOR)
    outsider = FakeMember(OUTSIDER)

    # The named aviator works Rotary and Fixed Wing...
    for key in ("rotary", "fixed_wing"):
        assert cog.can_work(aviator, key), f"aviator locked out of {key}"
    # ...but is nobody special on a badge that does not name them.
    assert not cog.can_work(aviator, "medical"), "aviator should not reach Medical"

    # Instructors keep working everything, including the aviation badges.
    for key in ("rotary", "medical", "airborne"):
        assert cog.can_work(instructor, key), f"instructor locked out of {key}"

    # Everyone else stays out.
    for key in ("rotary", "medical"):
        assert not cog.can_work(outsider, key), f"outsider reached {key}"

    # A missing or unknown badge key must not throw or grant anything.
    assert not cog.can_work(aviator, None)
    assert not cog.can_work(aviator, "no_such_badge")


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
    cog._catalogue = cat
    cog.bot = type("B", (), {"config": {"discord": {"combat_training_centre": {
        "instructor_role_id": 1001588316602368090}}}})()

    holder = FakeMember(777777777777777777, [SPECIALIST_ROLE])
    assert cog.can_work(holder, "medical"), "role-based extra trainer locked out"
    assert not cog.can_work(holder, "rotary"), "role should not carry to other badges"


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
        cog._catalogue = CAT
        cog.bot = type("B", (), {"config": {"discord": {
            "combat_training_centre": {"instructor_role_id": INSTRUCTOR, **ctc_settings}}}})()
        return cog

    instructor = FakeMember(1, [INSTRUCTOR])
    specialist = FakeMember(2, [SPECIALIST])
    nobody = FakeMember(3)

    # Unset: falls back to the instructor roles, so it works out of the box.
    loose = cog_with()
    assert loose.can_assign(instructor)
    assert not loose.can_assign(specialist)
    assert not loose.can_assign(nobody)

    # Set: narrows to exactly that role, instructors included.
    tight = cog_with(assign_role_id=SPECIALIST)
    assert tight.can_assign(specialist)
    assert not tight.can_assign(instructor), "narrowing must actually exclude"
    assert not tight.can_assign(nobody)

    # A list of roles works, as everywhere else.
    both = cog_with(assign_role_id=[SPECIALIST, INSTRUCTOR])
    assert both.can_assign(specialist) and both.can_assign(instructor)
    assert not both.can_assign(nobody)


@check("the assign picker preselects whoever holds the request")
def _() -> None:
    from ctc.views import AssignView

    claimed = {"id": 1, "instructor_id": "189362064995778560", "badge_key": "rotary",
               "levels": "B", "member_id": "5", "status": "claimed"}
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
