"""Badge catalogue for the Combat Training Centre.

The catalogue lives in ``ctc/catalogue.json`` rather than the database so it
stays reviewable in git and hand-editable, while ``/badge config`` can still
write to it and reload without a restart.

There are three kinds of badge:

``tab``     ``levels: []`` — no levels at all (Airborne, Radio).
``graded``  ``levels: [...]`` — the member picks every level they need run, and
            the instructor runs them in one session.
``timed``   ``levels: [...]`` plus ``timed: true`` — the score achieved decides
            the level. Nothing is picked at request time and exactly one level
            is awarded on completion.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable

CATALOGUE_PATH = Path(__file__).resolve().parent / "catalogue.json"

# Discord caps a select menu at 25 options, and the picker renders one option
# per requestable badge.
MAX_SELECT_OPTIONS = 25

_KEY_RE = re.compile(r"^[a-z0-9_]+$")


class CatalogueError(Exception):
    """Raised when a catalogue document would not produce a usable catalogue."""


class Badge:
    """A single badge. Thin wrapper so call sites read like prose."""

    __slots__ = ("key", "name", "category", "levels", "timed", "wip", "variants", "former_names")

    def __init__(self, raw: dict[str, Any]) -> None:
        self.key: str = raw["key"]
        self.name: str = raw["name"]
        self.category: str = raw["category"]
        self.levels: list[str] = list(raw.get("levels") or [])
        self.timed: bool = raw.get("timed") is True
        self.wip: bool = raw.get("wip") is True
        self.variants: list[str] = list(raw.get("variants") or [])
        self.former_names: list[str] = list(raw.get("formerNames") or [])

    @property
    def has_levels(self) -> bool:
        return bool(self.levels)

    @property
    def has_variants(self) -> bool:
        return bool(self.variants)

    @property
    def needs_level_choice(self) -> bool:
        """True only for graded badges — the ones the member picks levels for."""
        return self.has_levels and not self.timed

    @property
    def kind(self) -> str:
        if self.timed:
            return "timed"
        return "graded" if self.has_levels else "tab"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Badge {self.key} ({self.kind})>"


class Catalogue:
    """Validated, read-only view over the catalogue document."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw
        self.level_names: dict[str, str] = raw["levels"]
        self.categories: list[dict[str, Any]] = raw["categories"]

        order = {c["key"]: i for i, c in enumerate(self.categories)}
        self._category_by_key = {c["key"]: c for c in self.categories}

        by_key: dict[str, Badge] = {}
        for entry in raw["badges"]:
            badge = Badge(entry)
            if badge.key in by_key:
                raise CatalogueError(f"Duplicate badge key: {badge.key}")
            if not _KEY_RE.match(badge.key):
                raise CatalogueError(
                    f'Badge key "{badge.key}" must be lowercase letters, numbers and underscores'
                )
            if not badge.name.strip():
                raise CatalogueError(f"Badge {badge.key} needs a name")
            if badge.category not in order:
                raise CatalogueError(f"Badge {badge.key} has unknown category {badge.category}")
            for level in badge.levels:
                if level not in self.level_names:
                    raise CatalogueError(f"Badge {badge.key} has unknown level {level}")
            if badge.timed and not badge.levels:
                raise CatalogueError(f"{badge.name} is timed but has no levels to award")
            if len(badge.variants) > MAX_SELECT_OPTIONS:
                raise CatalogueError(f"Badge {badge.key} has more than 25 variants")
            by_key[badge.key] = badge

        requestable = [b for b in by_key.values() if not b.wip]
        if len(requestable) > MAX_SELECT_OPTIONS:
            raise CatalogueError(
                f"{len(requestable)} requestable badges exceeds the "
                f"{MAX_SELECT_OPTIONS}-option select menu limit"
            )

        self._by_key = by_key
        self._sorted = sorted(by_key.values(), key=lambda b: (order[b.category], b.name.lower()))

    # ------------------------------------------------------------- reading

    def all(self) -> list[Badge]:
        return list(self._sorted)

    def requestable(self) -> list[Badge]:
        """The badges the picker actually offers."""
        return [b for b in self._sorted if not b.wip]

    def get(self, key: str) -> Badge | None:
        return self._by_key.get(key)

    def category(self, key: str) -> dict[str, Any]:
        return self._category_by_key[key]

    def level_name(self, code: str) -> str:
        return self.level_names.get(code, code)

    def level_codes(self) -> list[str]:
        return list(self.level_names)

    def variants(self, badge_key: str) -> list[str]:
        badge = self.get(badge_key)
        return list(badge.variants) if badge else []

    def resolve_by_name(self, name: str) -> Badge | None:
        """Resolve by current name or any former name, for reading historic rows."""
        needle = name.strip().lower()
        for badge in self._sorted:
            if badge.name.lower() == needle:
                return badge
            if any(f.lower() == needle for f in badge.former_names):
                return badge
        return None

    # ------------------------------------------------------------- levels

    def sort_levels(self, badge_key: str, levels: Iterable[str]) -> list[str]:
        """Levels always render in the badge's own progression order."""
        badge = self.get(badge_key)
        order = badge.levels if badge else []
        return sorted(
            (l for l in levels),
            key=lambda l: order.index(l) if l in order else len(order),
        )

    def parse_levels(self, badge_key: str, stored: str | None) -> list[str]:
        """Parse the stored "B,A,E" form back into an ordered list."""
        if not stored:
            return []
        return self.sort_levels(badge_key, [p for p in stored.split(",") if p])

    def awardable_levels(self, badge_key: str, requested: str | Iterable[str] | None) -> list[str]:
        """Levels an instructor may record.

        Graded badges are limited to what was asked for; a timed test can land
        on any level the badge offers, because the score is what decides it.
        """
        if isinstance(requested, str) or requested is None:
            asked = self.parse_levels(badge_key, requested)
        else:
            asked = self.sort_levels(badge_key, requested)
        if asked:
            return asked
        badge = self.get(badge_key)
        return list(badge.levels) if badge and badge.timed else []

    def label(
        self, badge_key: str, levels: str | Iterable[str] | None = None, *, short: bool = False
    ) -> str:
        """A badge and its levels.

        ``short=True`` gives the abbreviated form used where space is tight —
        "Grenadier — B / A / E" rather than "Grenadier — Basic, Advanced, Expert".
        """
        badge = self.get(badge_key)
        if badge is None:
            return badge_key
        if isinstance(levels, str) or levels is None:
            items = self.parse_levels(badge_key, levels)
        else:
            items = self.sort_levels(badge_key, levels)
        if not items:
            return badge.name
        if short:
            return f"{badge.name} — {' / '.join(items)}"
        return f"{badge.name} — {', '.join(self.level_name(l) for l in items)}"

    def key_for(self, name: str) -> str:
        """Derive a stable key from a display name, avoiding collisions."""
        base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:24] or "badge"
        taken = set(self._by_key)
        if base not in taken:
            return base
        n = 2
        while f"{base}_{n}" in taken:
            n += 1
        return f"{base}_{n}"


def load(path: Path = CATALOGUE_PATH) -> Catalogue:
    with path.open(encoding="utf-8") as handle:
        return Catalogue(json.load(handle))


def mutate(fn: Callable[[dict[str, Any]], None], path: Path = CATALOGUE_PATH) -> Catalogue:
    """Apply an edit to the catalogue file.

    The mutated document is validated before it is written, and the previous
    file is restored if the reload somehow fails, so a rejected edit can never
    leave the bot with a broken catalogue.
    """
    backup = path.read_text(encoding="utf-8")
    doc = json.loads(backup)

    fn(doc)
    Catalogue(doc)  # raises before anything touches disk

    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        return load(path)
    except Exception:
        path.write_text(backup, encoding="utf-8")
        raise
