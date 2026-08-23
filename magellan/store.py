"""SQLite-backed storage for plans (events) and RSVPs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER,
    title TEXT NOT NULL,
    when_text TEXT NOT NULL,
    location TEXT,
    notes TEXT,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    closed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS rsvps (
    event_id INTEGER NOT NULL REFERENCES events(id),
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('yes', 'no')),
    responded_at TEXT NOT NULL,
    PRIMARY KEY (event_id, user_id)
);
"""


@dataclass(frozen=True, slots=True)
class Event:
    id: int
    guild_id: int
    channel_id: int
    message_id: int | None
    title: str
    when_text: str
    location: str | None
    notes: str | None
    created_by: int
    created_at: str
    closed: bool

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> Event:
        return cls(
            id=row["id"],
            guild_id=row["guild_id"],
            channel_id=row["channel_id"],
            message_id=row["message_id"],
            title=row["title"],
            when_text=row["when_text"],
            location=row["location"],
            notes=row["notes"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            closed=bool(row["closed"]),
        )


class Store:
    """Thin async wrapper around a single sqlite connection.

    A single connection is fine at this scale (one small trip, a handful of
    concurrent users) — sqlite serializes writes internally either way.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Store.connect() must be called before use"
        return self._conn

    async def create_event(
        self,
        *,
        guild_id: int,
        channel_id: int,
        title: str,
        when_text: str,
        location: str | None,
        notes: str | None,
        created_by: int,
    ) -> Event:
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self.conn.execute(
            """
            INSERT INTO events (
                guild_id, channel_id, title, when_text, location, notes, created_by, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (guild_id, channel_id, title, when_text, location, notes, created_by, now),
        )
        await self.conn.commit()
        event = await self.get_event(cursor.lastrowid)
        assert event is not None
        return event

    async def set_message(self, event_id: int, message_id: int) -> None:
        await self.conn.execute(
            "UPDATE events SET message_id = ? WHERE id = ?", (message_id, event_id)
        )
        await self.conn.commit()

    async def get_event(self, event_id: int) -> Event | None:
        cursor = await self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,))
        row = await cursor.fetchone()
        return Event.from_row(row) if row else None

    async def list_events(self, guild_id: int, *, include_closed: bool = False) -> list[Event]:
        query = "SELECT * FROM events WHERE guild_id = ?"
        if not include_closed:
            query += " AND closed = 0"
        query += " ORDER BY id DESC"
        cursor = await self.conn.execute(query, (guild_id,))
        rows = await cursor.fetchall()
        return [Event.from_row(row) for row in rows]

    async def upsert_rsvp(self, event_id: int, user_id: int, status: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self.conn.execute(
            """
            INSERT INTO rsvps (event_id, user_id, status, responded_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (event_id, user_id) DO UPDATE SET
                status = excluded.status, responded_at = excluded.responded_at
            """,
            (event_id, user_id, status, now),
        )
        await self.conn.commit()

    async def get_rsvps(self, event_id: int) -> dict[int, str]:
        cursor = await self.conn.execute(
            "SELECT user_id, status FROM rsvps WHERE event_id = ?", (event_id,)
        )
        rows = await cursor.fetchall()
        return {row["user_id"]: row["status"] for row in rows}
