"""SQLite-backed storage for plans (events) and RSVPs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER,
    title TEXT NOT NULL,
    location TEXT,
    price TEXT,
    notes TEXT,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    closed INTEGER NOT NULL DEFAULT 0,
    ai_comment TEXT
);

CREATE TABLE IF NOT EXISTS rsvps (
    event_id INTEGER NOT NULL REFERENCES events(id),
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('yes', 'no')),
    responded_at TEXT NOT NULL,
    PRIMARY KEY (event_id, user_id)
);

CREATE TABLE IF NOT EXISTS dm_messages (
    event_id INTEGER NOT NULL REFERENCES events(id),
    user_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    PRIMARY KEY (event_id, user_id)
);

CREATE TABLE IF NOT EXISTS blocked_creators (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    blocked_by INTEGER NOT NULL,
    blocked_at TEXT NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);
"""


@dataclass(frozen=True, slots=True)
class Event:
    id: int
    guild_id: int
    channel_id: int
    message_id: int | None
    title: str
    location: str | None
    price: str | None
    notes: str | None
    created_by: int
    created_at: str
    closed: bool
    ai_comment: str | None

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> Event:
        return cls(
            id=row["id"],
            guild_id=row["guild_id"],
            channel_id=row["channel_id"],
            message_id=row["message_id"],
            title=row["title"],
            location=row["location"],
            price=row["price"],
            notes=row["notes"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            closed=bool(row["closed"]),
            ai_comment=row["ai_comment"],
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
        await self._migrate()
        await self._conn.commit()

    async def _migrate(self) -> None:
        """Additive, idempotent patches for a database created before a
        schema change (e.g. the deployed omashu database, which predates
        `price`/`ai_comment` and still has the now-removed `when_text`).
        A brand-new database already matches SCHEMA above and needs none
        of this.
        """
        cursor = await self.conn.execute("PRAGMA table_info(events)")
        columns = {row["name"] for row in await cursor.fetchall()}

        if "when_text" in columns:
            await self.conn.execute("ALTER TABLE events DROP COLUMN when_text")
        if "price" not in columns:
            await self.conn.execute("ALTER TABLE events ADD COLUMN price TEXT")
        if "ai_comment" not in columns:
            await self.conn.execute("ALTER TABLE events ADD COLUMN ai_comment TEXT")

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
        location: str | None,
        price: str | None,
        notes: str | None,
        created_by: int,
        ai_comment: str | None = None,
    ) -> Event:
        now = datetime.now(UTC).isoformat()
        cursor = await self.conn.execute(
            """
            INSERT INTO events (
                guild_id, channel_id, title, location, price, notes, created_by, created_at,
                ai_comment
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (guild_id, channel_id, title, location, price, notes, created_by, now, ai_comment),
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
        now = datetime.now(UTC).isoformat()
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

    async def record_dm_message(
        self, event_id: int, user_id: int, channel_id: int, message_id: int
    ) -> None:
        """Remember a traveler's DM copy of an event so it can be kept live.

        Upserts on (event_id, user_id): if this traveler is DMed again for
        the same event (e.g. a /event remind after the initial /event
        create), the newer message replaces the older one as the copy we
        keep updated — we don't try to keep every past DM in sync.
        """
        await self.conn.execute(
            """
            INSERT INTO dm_messages (event_id, user_id, channel_id, message_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (event_id, user_id) DO UPDATE SET
                channel_id = excluded.channel_id, message_id = excluded.message_id
            """,
            (event_id, user_id, channel_id, message_id),
        )
        await self.conn.commit()

    async def get_dm_messages(self, event_id: int) -> list[tuple[int, int, int]]:
        """Return (user_id, channel_id, message_id) for every tracked DM of this event."""
        cursor = await self.conn.execute(
            "SELECT user_id, channel_id, message_id FROM dm_messages WHERE event_id = ?",
            (event_id,),
        )
        rows = await cursor.fetchall()
        return [(row["user_id"], row["channel_id"], row["message_id"]) for row in rows]

    async def delete_event(self, event_id: int) -> None:
        """Delete an event and everything keyed to it (RSVPs, tracked DMs).

        Only touches this store's rows — the caller is responsible for
        deleting the actual Discord messages (channel announcement + every
        tracked DM) first, since once this runs `get_dm_messages` can no
        longer tell you which messages those were.
        """
        await self.conn.execute("DELETE FROM rsvps WHERE event_id = ?", (event_id,))
        await self.conn.execute("DELETE FROM dm_messages WHERE event_id = ?", (event_id,))
        await self.conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        await self.conn.commit()

    async def block_creator(self, guild_id: int, user_id: int, blocked_by: int) -> None:
        """Stop `user_id` from creating new plans in `guild_id` — doesn't
        touch their traveler role, so they can still RSVP/vote normally.
        """
        now = datetime.now(UTC).isoformat()
        await self.conn.execute(
            """
            INSERT INTO blocked_creators (guild_id, user_id, blocked_by, blocked_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (guild_id, user_id) DO UPDATE SET
                blocked_by = excluded.blocked_by, blocked_at = excluded.blocked_at
            """,
            (guild_id, user_id, blocked_by, now),
        )
        await self.conn.commit()

    async def unblock_creator(self, guild_id: int, user_id: int) -> None:
        await self.conn.execute(
            "DELETE FROM blocked_creators WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        await self.conn.commit()

    async def is_blocked_creator(self, guild_id: int, user_id: int) -> bool:
        cursor = await self.conn.execute(
            "SELECT 1 FROM blocked_creators WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        return await cursor.fetchone() is not None
