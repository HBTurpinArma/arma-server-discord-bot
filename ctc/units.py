"""Battalions sharing one bot.

Several units run their own training pipeline inside the same Discord server,
so almost everything the cog reads — badge catalogue, queue channel, instructor
roles — has to be looked up per unit rather than held once on the cog. A unit
is the tenant boundary: separate badges, separate queue, separate staff.

The guild cannot tell them apart on its own — both battalions share one server,
so ``guild_id`` is the same for every request. Every row, panel and interaction
therefore carries its unit key explicitly.

Settings resolve in three layers, most specific winning:

    cog DEFAULTS  <  the shared block  <  the unit's own block

so ``taw_award_url`` or ``daily_bump`` can be set once for everybody while
``queue_channel_id`` and the role ids stay per unit.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .catalogue import Catalogue, CatalogueError
from .catalogue import load as load_catalogue

PACKAGE_DIR = Path(__file__).resolve().parent

#: Unit keys end up inside component custom_ids, so they have to survive the
#: same round trip as a badge key.
_KEY_RE = re.compile(r"^[a-z0-9_]+$")

#: Config keys that describe the unit itself rather than its behaviour.
_META_KEYS = frozenset({"name", "catalogue"})


class UnitError(Exception):
    """Raised when the units block would not produce a usable setup."""


class Unit:
    """One battalion: its badges, its channel, its staff."""

    __slots__ = ("key", "name", "settings", "catalogue_path", "catalogue")

    def __init__(self, key: str, raw: dict[str, Any], defaults: dict[str, Any]) -> None:
        if not _KEY_RE.match(key):
            raise UnitError(
                f'Unit key "{key}" must be lowercase letters, numbers and underscores'
            )
        self.key = key
        self.name: str = str(raw.get("name") or key.upper())

        # Behaviour keys only — "name" and "catalogue" describe the unit, and
        # letting them through would put them in settings where a typo'd
        # setting name would look legitimate.
        self.settings: dict[str, Any] = {
            **defaults,
            **{k: v for k, v in raw.items() if k not in _META_KEYS},
        }

        given = str(raw.get("catalogue") or "catalogue.json")
        path = Path(given)
        # Relative names live beside the code, so config stays portable.
        self.catalogue_path = path if path.is_absolute() else PACKAGE_DIR / path
        if not self.catalogue_path.exists():
            raise UnitError(
                f'Unit "{key}" points at a catalogue that does not exist: '
                f"{self.catalogue_path}"
            )
        try:
            self.catalogue: Catalogue = load_catalogue(self.catalogue_path)
        except CatalogueError as error:
            raise UnitError(f'Unit "{key}" has an unusable catalogue: {error}') from error

    def reload(self) -> Catalogue:
        """Re-read this unit's catalogue from disk."""
        self.catalogue = load_catalogue(self.catalogue_path)
        return self.catalogue

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Unit {self.key} ({len(self.catalogue.all())} badges)>"


class Units:
    """Every configured unit, and how to work out which one an action belongs to."""

    def __init__(self, config: dict[str, Any], defaults: dict[str, Any]) -> None:
        raw_units = config.get("units")

        if not raw_units:
            # No units block: the whole thing is one unnamed unit. Keeps a
            # single-battalion config working untouched, which matters because
            # config.json lives on the host and is not deployed with the code.
            raw_units = {DEFAULT_UNIT_KEY: dict(config)}
            shared: dict[str, Any] = {}
        else:
            # Anything outside "units" is shared across all of them.
            shared = {k: v for k, v in config.items() if k != "units"}

        if not isinstance(raw_units, dict):
            raise UnitError('"units" must be an object keyed by unit, e.g. {"am2": {…}}')

        base = {**defaults, **shared}
        self._by_key: dict[str, Unit] = {
            key: Unit(key, value or {}, base) for key, value in raw_units.items()
        }
        if not self._by_key:
            raise UnitError("No units configured")

        primary = config.get("primary_unit")
        if primary and primary not in self._by_key:
            raise UnitError(f'primary_unit "{primary}" is not one of the configured units')
        #: Where rows that predate multi-unit support belong, and the answer
        #: when there is only one unit anyway.
        self.primary: str = primary or next(iter(self._by_key))

    # -------------------------------------------------------------- reading

    def __len__(self) -> int:
        return len(self._by_key)

    def __iter__(self):
        return iter(self._by_key.values())

    def __contains__(self, key: object) -> bool:
        return key in self._by_key

    def keys(self) -> list[str]:
        return list(self._by_key)

    def get(self, key: str | None) -> Unit | None:
        return self._by_key.get(key) if key else None

    def require(self, key: str | None) -> Unit:
        unit = self.get(key)
        if unit is None:
            raise UnitError(f'Unknown unit "{key}"')
        return unit

    @property
    def only(self) -> Unit | None:
        """The single unit, when there is only one — the common case."""
        return next(iter(self._by_key.values())) if len(self._by_key) == 1 else None

    # ------------------------------------------------------------- resolving

    def for_channel(self, channel_id: int | None) -> Unit | None:
        """The unit whose queue channel this is.

        Also matches a thread's parent, so a command run inside a request
        thread resolves even before the row is looked up.
        """
        if not channel_id:
            return None
        for unit in self._by_key.values():
            if int(unit.settings.get("queue_channel_id") or 0) == int(channel_id):
                return unit
        return None

    def for_member(self, user: Any) -> Unit | None:
        """The unit whose roles this member holds — but only if exactly one.

        Somebody who staffs both battalions is genuinely ambiguous, and
        guessing would silently file their request under the wrong one.
        """
        held = {role.id for role in getattr(user, "roles", [])}
        if not held:
            return None
        matched = [unit for unit in self._by_key.values() if held & _role_ids(unit)]
        return matched[0] if len(matched) == 1 else None


#: The key a single-battalion config is filed under, and what rows written
#: before units existed are backfilled to.
DEFAULT_UNIT_KEY = "default"

#: Settings naming roles that mean "this person belongs to this unit".
_MEMBERSHIP_SETTINGS = (
    "member_role_id",
    "instructor_role_id",
    "config_role_id",
    "assign_role_id",
)


def _role_ids(unit: Unit) -> set[int]:
    ids: set[int] = set()
    for setting in _MEMBERSHIP_SETTINGS:
        value = unit.settings.get(setting)
        if not value:
            continue
        values = value if isinstance(value, (list, tuple)) else [value]
        ids.update(int(v) for v in values if v)
    return ids
