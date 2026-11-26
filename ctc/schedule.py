"""Who is around when, for a badge request, in everyone's own time.

An hour is stored as the Unix time it starts, so a TI marking 19:00 in the UK
and a trainee marking 14:00 in New York land on the same value. Everything to
do with time zones happens here, away from Discord, so it can be tested.

Ranges are ``(start, end)`` pairs of Unix times, end exclusive.

On Windows, ``zoneinfo`` has no system time zone list to read and needs the
``tzdata`` package installed alongside the bot.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

HOUR = 3600

#: How far ahead the hour lists go, counting today.
DAYS_AHEAD = 7

#: Offered when someone sets their time zone. Named for the people picking,
#: grouped the way TAW members are spread, and at most 25 so one select holds
#: them all.
TIMEZONES: list[tuple[str, str]] = [
    ("Europe/London", "UK, Ireland, Portugal"),
    ("Europe/Amsterdam", "Central Europe (NL, DE, FR, PL, SE, NO, IT, ES)"),
    ("Europe/Helsinki", "Eastern Europe (FI, EE, RO, GR, UA, BG)"),
    ("Europe/Istanbul", "Turkey"),
    ("Europe/Moscow", "Moscow"),
    ("America/Halifax", "Atlantic Canada"),
    ("America/New_York", "US and Canada Eastern"),
    ("America/Chicago", "US and Canada Central"),
    ("America/Denver", "US and Canada Mountain"),
    ("America/Phoenix", "Arizona"),
    ("America/Los_Angeles", "US and Canada Pacific"),
    ("America/Anchorage", "Alaska"),
    ("Pacific/Honolulu", "Hawaii"),
    ("America/Sao_Paulo", "Brazil (Sao Paulo)"),
    ("Africa/Johannesburg", "South Africa"),
    ("Asia/Dubai", "Gulf (UAE, Oman)"),
    ("Asia/Kolkata", "India"),
    ("Asia/Singapore", "Singapore, Philippines, Perth"),
    ("Asia/Tokyo", "Japan, Korea"),
    ("Australia/Brisbane", "Queensland"),
    ("Australia/Sydney", "Sydney, Melbourne, Canberra"),
    ("Australia/Adelaide", "Adelaide"),
    ("Pacific/Auckland", "New Zealand"),
    ("Etc/UTC", "UTC"),
]

_LABELS = dict(TIMEZONES)


def zone(tz: str) -> ZoneInfo:
    return ZoneInfo(tz)


def zone_label(tz: str) -> str:
    return _LABELS.get(tz, tz)


@dataclass(frozen=True)
class Hour:
    """One hour on someone's own clock."""

    hour: int
    #: When it starts, as Unix time. None for the hour a clock change skips.
    start: int | None


def day_of(tz: str, offset: int, now: float) -> date:
    """The date ``offset`` days after today, on that person's own calendar."""
    return datetime.fromtimestamp(now, zone(tz)).date() + timedelta(days=offset)


def at(tz: str, day: date, hour: int, minute: int = 0) -> int | None:
    """The Unix time of a time on that person's clock, or None if a clock
    change skips it."""
    here = zone(tz)
    start = int(datetime(day.year, day.month, day.day, hour, minute, tzinfo=here).timestamp())
    back = datetime.fromtimestamp(start, here)
    return start if (back.date(), back.hour, back.minute) == (day, hour, minute) else None


def hours_of(tz: str, day: date) -> list[Hour]:
    """The 24 hours of ``day`` on that person's clock.

    When the clocks go forward an hour does not exist, and it comes back with
    no start rather than as a second button for an instant already shown.
    """
    seen: set[int] = set()
    hours = []
    for hour in range(24):
        start = at(tz, day, hour)
        if start is None or start in seen:
            hours.append(Hour(hour, None))
        else:
            seen.add(start)
            hours.append(Hour(hour, start))
    return hours


def day_label(day: date) -> str:
    return f"{day:%a} {day.day} {day:%b}"


def local_label(tz: str, when: int) -> str:
    """A start time written out on that person's clock, for places Discord
    will not render a timestamp: button and option labels."""
    at = datetime.fromtimestamp(when, zone(tz))
    return f"{day_label(at.date())} {at:%H:%M}"


# ------------------------------------------------------------------ ranges


def ranges(starts: Iterable[int]) -> list[tuple[int, int]]:
    """Merge hour starts into continuous ranges."""
    return merge((start, start + HOUR) for start in starts)


def overlap(
    a: Sequence[tuple[int, int]], b: Sequence[tuple[int, int]]
) -> list[tuple[int, int]]:
    """Where two sets of ranges are both free."""
    both = []
    for a_start, a_end in a:
        for b_start, b_end in b:
            start, end = max(a_start, b_start), min(a_end, b_end)
            if start < end:
                both.append((start, end))
    return merge(both)


def merge(items: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Join ranges that touch or overlap."""
    merged: list[list[int]] = []
    for start, end in sorted(items):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def covers(spans: Sequence[tuple[int, int]], start: int, end: int) -> bool:
    """True if any range touches ``start`` to ``end``.

    Touching rather than filling, so someone half an hour off (India,
    Adelaide) still sees the hours they share with a whole-hour zone.
    """
    return any(s < end and start < e for s, e in spans)


def upcoming(spans: Sequence[tuple[int, int]], now: float) -> list[tuple[int, int]]:
    """Drop what has already ended and trim what has already started."""
    return [(max(s, int(now)), e) for s, e in spans if e > now]


def booking_starts(spans: Sequence[tuple[int, int]], limit: int = 25) -> list[int]:
    """Start times on offer for booking: each hour into a free range."""
    starts = []
    for start, end in spans:
        when = start
        while when < end and len(starts) < limit:
            starts.append(when)
            when += HOUR
    return starts


@dataclass(frozen=True)
class Summary:
    """Everything the shared message shows, worked out from the marked hours."""

    trainers: list[str]
    trainer_free: list[tuple[int, int]]
    trainee_free: list[tuple[int, int]]
    both: list[tuple[int, int]]


def summarise(rows: Iterable[Any], now: float) -> Summary:
    """Turn ``ctc_availability`` rows into ranges from now on.

    Trainers are pooled: with a TI and a Guest TI either can run it, so the
    trainee sees every hour at least one of them is around.
    """
    trainers: list[str] = []
    trainer_starts: list[int] = []
    trainee_starts: list[int] = []
    for row in rows:
        if row["side"] == "trainer":
            trainer_starts.append(int(row["slot_start"]))
            if row["user_id"] not in trainers:
                trainers.append(row["user_id"])
        else:
            trainee_starts.append(int(row["slot_start"]))
    trainer_free = upcoming(ranges(trainer_starts), now)
    trainee_free = upcoming(ranges(trainee_starts), now)
    return Summary(trainers, trainer_free, trainee_free, overlap(trainer_free, trainee_free))


def total_hours(spans: Sequence[tuple[int, int]]) -> str:
    """How much time a set of ranges covers, written as hours."""
    hours = sum(e - s for s, e in spans) / HOUR
    return f"{hours:g} h"


def by_day(tz: str, spans: Sequence[tuple[int, int]]) -> str:
    """Ranges written out on one person's clock, a line per day.

    Only for text a single person sees, since it is fixed to their zone:
    "Wed 25 Nov · 06:00-07:00, 21:00-22:00". A range is filed under the day
    it starts.
    """
    if not spans:
        return "Nothing yet"
    here = zone(tz)
    days: dict[date, list[str]] = {}
    for start, end in spans:
        a, b = datetime.fromtimestamp(start, here), datetime.fromtimestamp(end, here)
        days.setdefault(a.date(), []).append(f"{a:%H:%M}-{b:%H:%M}")
    return "\n".join(f"**{day_label(day)}** · {', '.join(times)}" for day, times in days.items())


def describe(spans: Sequence[tuple[int, int]], limit: int = 10) -> str:
    """Ranges as Discord timestamps, which every reader sees in their own time."""
    if not spans:
        return "Nothing yet"
    lines = [f"<t:{s}:f> to <t:{e}:t>" for s, e in spans[:limit]]
    if len(spans) > limit:
        lines.append(f"…and {len(spans) - limit} more")
    return "\n".join(lines)
