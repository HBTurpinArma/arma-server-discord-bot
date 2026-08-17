"""Combat Training Centre badge request tracking.

``CTCDatabaseManager`` owns the request lifecycle and is the only place the
status machine is enforced, so an invalid move raises rather than writing a bad
row — which also makes a double-clicked button harmless.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Sequence
from typing import Any

import aiosqlite

from .catalogue import Badge, Catalogue, CatalogueError, load, mutate  # noqa: F401

OPEN_STATUSES = ("requested", "claimed", "completed")

#: Legal status transitions. Anything not listed here is refused.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "requested": ("claimed", "cancelled"),
    "claimed": ("completed", "requested", "cancelled"),
    "completed": ("awarded", "claimed", "cancelled"),
    "awarded": (),
    "cancelled": (),
}

TIMESTAMP_FOR = {
    "claimed": "claimed_at",
    "completed": "completed_at",
    "awarded": "awarded_at",
    "cancelled": "cancelled_at",
}


class TransitionError(Exception):
    """A requested change is not legal for the row's current state."""


class CTCDatabaseManager:
    def __init__(
        self,
        *,
        connection: aiosqlite.Connection,
        catalogue: Callable[[], Catalogue],
    ) -> None:
        self.connection = connection
        self.connection.row_factory = aiosqlite.Row
        #: Callable rather than a value so edits made through /badge config are
        #: picked up without rebuilding the manager.
        self._catalogue = catalogue

    @property
    def catalogue(self) -> Catalogue:
        return self._catalogue()

    # ------------------------------------------------------------ reading

    async def _one(self, sql: str, params: Sequence[Any] = ()) -> aiosqlite.Row | None:
        async with self.connection.execute(sql, params) as cursor:
            return await cursor.fetchone()

    async def _many(self, sql: str, params: Sequence[Any] = ()) -> list[aiosqlite.Row]:
        async with self.connection.execute(sql, params) as cursor:
            return list(await cursor.fetchall())

    async def by_id(self, request_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM ctc_requests WHERE id = ?", (request_id,))

    async def by_thread(self, thread_id: str) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM ctc_requests WHERE thread_id = ?", (str(thread_id),))

    async def by_group(self, group_id: str) -> list[aiosqlite.Row]:
        return await self._many(
            "SELECT * FROM ctc_requests WHERE group_id = ? ORDER BY id", (group_id,)
        )

    async def open_for_member(self, member_id: str) -> list[aiosqlite.Row]:
        return await self._many(
            "SELECT * FROM ctc_requests WHERE member_id = ? AND status IN "
            "('requested','claimed','completed') ORDER BY id",
            (str(member_id),),
        )

    async def queue(
        self, *, statuses: Sequence[str] = OPEN_STATUSES, instructor_id: str | None = None
    ) -> list[aiosqlite.Row]:
        placeholders = ",".join("?" for _ in statuses)
        sql = f"SELECT * FROM ctc_requests WHERE status IN ({placeholders})"
        params: list[Any] = list(statuses)
        if instructor_id:
            sql += " AND instructor_id = ?"
            params.append(str(instructor_id))
        sql += " ORDER BY created_at ASC"
        return await self._many(sql, params)

    async def stale(self, status: str, hours: int) -> list[aiosqlite.Row]:
        """Rows sat in `status` longer than `hours`, not nudged in the last day."""
        return await self._many(
            "SELECT * FROM ctc_requests WHERE status = ? "
            "AND created_at <= datetime('now', ?) "
            "AND (last_nudged_at IS NULL OR last_nudged_at <= datetime('now','-24 hours')) "
            "ORDER BY created_at ASC",
            (status, f"-{int(hours)} hours"),
        )

    async def counts_for_badge(self, badge_key: str) -> int:
        row = await self._one(
            "SELECT COUNT(*) AS n FROM ctc_requests WHERE badge_key = ?", (badge_key,)
        )
        return int(row["n"]) if row else 0

    # ------------------------------------------------------------ writing

    async def create_group(
        self,
        *,
        guild_id: str,
        member_id: str,
        member_name: str,
        notes: str | None,
        items: Iterable[tuple[str, Sequence[str]]],
    ) -> list[aiosqlite.Row]:
        """Create one row per badge. All of them land, or none do."""
        group_id = str(uuid.uuid4())
        ids: list[int] = []
        try:
            for badge_key, levels in items:
                cursor = await self.connection.execute(
                    "INSERT INTO ctc_requests "
                    "(group_id, guild_id, member_id, member_name, badge_key, levels, notes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        group_id,
                        str(guild_id),
                        str(member_id),
                        member_name,
                        badge_key,
                        ",".join(levels) if levels else None,
                        notes or None,
                    ),
                )
                ids.append(int(cursor.lastrowid))
            await self.connection.commit()
        except Exception:
            await self.connection.rollback()
            raise
        return [row for row in [await self.by_id(i) for i in ids] if row is not None]

    async def transition(
        self,
        request_id: int,
        nxt: str,
        *,
        instructor_id: str | None = None,
        instructor_name: str | None = None,
        levels_achieved: Sequence[str] | None = None,
        variant: str | None = None,
    ) -> aiosqlite.Row:
        row = await self.by_id(request_id)
        if row is None:
            raise TransitionError(f"Request #{request_id} not found.")
        if row["status"] == nxt:
            raise TransitionError(f"Request #{request_id} is already {nxt}.")
        if nxt not in TRANSITIONS.get(row["status"], ()):
            raise TransitionError(f"Cannot go from {row['status']} to {nxt}.")

        cat = self.catalogue
        badge = cat.get(row["badge_key"])
        sets = ["status = ?"]
        params: list[Any] = [nxt]

        stamp = TIMESTAMP_FOR.get(nxt)
        if stamp:
            sets.append(f"{stamp} = datetime('now')")

        if nxt == "completed":
            allowed = cat.awardable_levels(row["badge_key"], row["levels"])
            achieved = list(levels_achieved) if levels_achieved is not None else (
                cat.parse_levels(row["badge_key"], row["levels"])
            )

            unknown = [lvl for lvl in achieved if lvl not in allowed]
            if unknown:
                raise TransitionError(
                    f"Level(s) {', '.join(unknown)} cannot be awarded for this request."
                )

            # A timed test produces one result, so it earns one level — a Rifle
            # run scored at Expert does not also confer Advanced.
            if badge is not None and badge.timed and len(achieved) > 1:
                raise TransitionError(
                    "A timed test awards a single level — pick just the one earned."
                )

            sets.append("levels_achieved = ?")
            params.append(",".join(cat.sort_levels(row["badge_key"], achieved)) or None)

            allowed_variants = cat.variants(row["badge_key"])
            if allowed_variants:
                if not variant:
                    raise TransitionError("Select which variant was run.")
                if variant not in allowed_variants:
                    raise TransitionError(f'"{variant}" is not a valid variant for this badge.')
                sets.append("variant = ?")
                params.append(variant)

        if nxt == "claimed":
            sets += ["instructor_id = ?", "instructor_name = ?"]
            params += [str(instructor_id) if instructor_id else None, instructor_name]
            # Bouncing back from completed re-opens the work and voids the result.
            if row["status"] == "completed":
                sets += ["completed_at = NULL", "levels_achieved = NULL", "variant = NULL"]

        # Releasing a claim clears the instructor and the claim timestamp.
        if nxt == "requested":
            sets += ["instructor_id = NULL", "instructor_name = NULL", "claimed_at = NULL"]

        params.append(request_id)
        await self.connection.execute(
            f"UPDATE ctc_requests SET {', '.join(sets)} WHERE id = ?", params
        )
        await self.connection.commit()
        return await self.by_id(request_id)  # type: ignore[return-value]

    async def amend_request(
        self, request_id: int, *, levels: Sequence[str], actor_id: str, actor_name: str
    ) -> aiosqlite.Row:
        """Change what a request is asking for — the levels still to be run.

        A tracking correction, not a record of achievement: a member who asked
        for Basic but actually needs Basic and Advanced shouldn't start over.
        Only valid while the work is still open; once a result exists, reopen.
        """
        row = await self.by_id(request_id)
        if row is None:
            raise TransitionError(f"Request #{request_id} not found.")
        if row["status"] == "cancelled":
            raise TransitionError(f"Request #{request_id} was cancelled.")
        if row["status"] in ("completed", "awarded"):
            raise TransitionError(
                f"Request #{request_id} already has a result. "
                "Reopen it first if the levels were wrong."
            )

        badge = self.catalogue.get(row["badge_key"])
        if badge is None or not badge.needs_level_choice:
            name = badge.name if badge else row["badge_key"]
            raise TransitionError(f"{name} has no requested levels — there is nothing to change.")

        wanted = list(levels)
        if not wanted:
            raise TransitionError("A request needs at least one level.")
        unknown = [lvl for lvl in wanted if lvl not in badge.levels]
        if unknown:
            raise TransitionError(f"Level(s) {', '.join(unknown)} do not exist for {badge.name}.")

        await self.connection.execute(
            "UPDATE ctc_requests SET levels = ?, amended_at = datetime('now'), "
            "amended_by = ?, amended_by_name = ? WHERE id = ?",
            (
                ",".join(self.catalogue.sort_levels(row["badge_key"], wanted)),
                str(actor_id),
                actor_name,
                request_id,
            ),
        )
        await self.connection.commit()
        return await self.by_id(request_id)  # type: ignore[return-value]

    async def reopen(
        self, request_id: int, *, instructor_id: str, instructor_name: str
    ) -> aiosqlite.Row:
        """Send a finished request back for a re-run, clearing the result.

        The normal machine will not leave ``awarded``; this is the escape hatch.
        """
        row = await self.by_id(request_id)
        if row is None:
            raise TransitionError(f"Request #{request_id} not found.")
        if row["status"] not in ("completed", "awarded"):
            raise TransitionError(f"Request #{request_id} is {row['status']} — it is already open.")

        await self.connection.execute(
            "UPDATE ctc_requests SET status = 'claimed', levels_achieved = NULL, variant = NULL, "
            "completed_at = NULL, awarded_at = NULL, instructor_id = ?, instructor_name = ?, "
            "claimed_at = COALESCE(claimed_at, datetime('now')), amended_at = datetime('now'), "
            "amended_by = ?, amended_by_name = ? WHERE id = ?",
            (str(instructor_id), instructor_name, str(instructor_id), instructor_name, request_id),
        )
        await self.connection.commit()
        return await self.by_id(request_id)  # type: ignore[return-value]

    async def set_queue_message(
        self, request_id: int, channel_id: str, message_id: str, thread_id: str | None
    ) -> None:
        await self.connection.execute(
            "UPDATE ctc_requests SET queue_channel_id = ?, queue_message_id = ?, thread_id = ? "
            "WHERE id = ?",
            (str(channel_id), str(message_id), str(thread_id) if thread_id else None, request_id),
        )
        await self.connection.commit()

    async def mark_nudged(self, request_id: int) -> None:
        await self.connection.execute(
            "UPDATE ctc_requests SET last_nudged_at = datetime('now') WHERE id = ?", (request_id,)
        )
        await self.connection.commit()

    # -------------------------------------------------------------- stats

    async def stats(self) -> dict[str, Any]:
        by_status = await self._many(
            "SELECT status, COUNT(*) AS n FROM ctc_requests GROUP BY status"
        )
        by_instructor = await self._many(
            "SELECT instructor_name AS name, COUNT(*) AS total, "
            "SUM(CASE WHEN status IN ('claimed','completed') THEN 1 ELSE 0 END) AS open, "
            "SUM(CASE WHEN status = 'awarded' THEN 1 ELSE 0 END) AS awarded "
            "FROM ctc_requests WHERE instructor_id IS NOT NULL "
            "GROUP BY instructor_id, instructor_name ORDER BY total DESC"
        )
        turnaround = await self._many(
            "SELECT badge_key, COUNT(*) AS n, "
            "ROUND(AVG(julianday(awarded_at) - julianday(created_at)), 1) AS avg_days "
            "FROM ctc_requests WHERE status = 'awarded' AND awarded_at IS NOT NULL "
            "GROUP BY badge_key ORDER BY n DESC"
        )
        oldest_open = await self._one(
            "SELECT * FROM ctc_requests WHERE status IN ('requested','claimed','completed') "
            "ORDER BY created_at ASC LIMIT 1"
        )
        return {
            "by_status": by_status,
            "by_instructor": by_instructor,
            "turnaround": turnaround,
            "oldest_open": oldest_open,
        }
