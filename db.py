"""SQLite-слой через aiosqlite. Создание схемы — на старте."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dss_event_id  TEXT UNIQUE,
    event_type    TEXT,
    event_name    TEXT,
    person_id     TEXT,
    person_name   TEXT,
    door_id       TEXT,
    door_name     TEXT,
    direction     TEXT,
    occurred_at   DATETIME,
    raw_json      TEXT,
    importance    INTEGER DEFAULT 0,
    sent_to_tg    INTEGER DEFAULT 0,
    snapshot_url  TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_occurred ON events(occurred_at);
CREATE INDEX IF NOT EXISTS idx_events_person   ON events(person_name);
CREATE INDEX IF NOT EXISTS idx_events_unsent   ON events(sent_to_tg, importance);

CREATE TABLE IF NOT EXISTS persons_inside (
    person_id   TEXT PRIMARY KEY,
    person_name TEXT,
    entered_at  DATETIME,
    last_door   TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS user_lang (
    user_id INTEGER PRIMARY KEY,
    lang    TEXT NOT NULL
);
"""


class DB:
    def __init__(self, path: Path):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "DB не подключена"
        return self._conn

    # --- events ---
    async def insert_event(self, e: dict[str, Any]) -> int | None:
        """Возвращает id новой записи или None, если дубликат."""
        try:
            cur = await self.conn.execute(
                """INSERT INTO events
                   (dss_event_id, event_type, event_name, person_id, person_name,
                    door_id, door_name, direction, occurred_at, raw_json,
                    importance, snapshot_url)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    e.get("dss_event_id"),
                    e.get("event_type"),
                    e.get("event_name"),
                    e.get("person_id"),
                    e.get("person_name"),
                    e.get("door_id"),
                    e.get("door_name"),
                    e.get("direction"),
                    e.get("occurred_at"),
                    json.dumps(e.get("raw"), ensure_ascii=False),
                    e.get("importance", 0),
                    e.get("snapshot_url"),
                ),
            )
            await self.conn.commit()
            return cur.lastrowid
        except aiosqlite.IntegrityError:
            return None  # дубликат по dss_event_id

    async def fetch_unsent(self, min_importance: int = 1, limit: int = 50) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            """SELECT * FROM events
               WHERE sent_to_tg = 0 AND importance >= ?
               ORDER BY id ASC LIMIT ?""",
            (min_importance, limit),
        )
        return list(await cur.fetchall())

    async def mark_sent(self, event_id: int) -> None:
        await self.conn.execute(
            "UPDATE events SET sent_to_tg = 1 WHERE id = ?", (event_id,)
        )
        await self.conn.commit()

    # --- persons_inside ---
    async def upsert_inside(self, person_id: str, person_name: str, door: str, ts: str) -> None:
        await self.conn.execute(
            """INSERT INTO persons_inside (person_id, person_name, entered_at, last_door)
               VALUES (?,?,?,?)
               ON CONFLICT(person_id) DO UPDATE SET
                 last_door=excluded.last_door,
                 person_name=excluded.person_name""",
            (person_id, person_name, ts, door),
        )
        await self.conn.commit()

    async def remove_inside(self, person_id: str) -> None:
        await self.conn.execute("DELETE FROM persons_inside WHERE person_id = ?", (person_id,))
        await self.conn.commit()

    async def list_inside(self) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT * FROM persons_inside ORDER BY entered_at"
        )
        return list(await cur.fetchall())

    async def count_inside(self) -> int:
        cur = await self.conn.execute("SELECT COUNT(*) FROM persons_inside")
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    # --- queries ---
    async def find_by_name(self, name: str, limit: int = 10) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            """SELECT * FROM events
               WHERE person_name LIKE ?
               ORDER BY occurred_at DESC LIMIT ?""",
            (f"%{name}%", limit),
        )
        return list(await cur.fetchall())

    async def find_by_name_with_image(
        self, name: str, limit: int = 5
    ) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            """SELECT * FROM events
               WHERE person_name LIKE ?
                 AND snapshot_url IS NOT NULL AND snapshot_url != ''
               ORDER BY occurred_at DESC LIMIT ?""",
            (f"%{name}%", limit),
        )
        return list(await cur.fetchall())

    async def events_by_door(self, door: str, since: datetime, limit: int = 30) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            """SELECT * FROM events
               WHERE door_name LIKE ? AND occurred_at >= ?
               ORDER BY occurred_at DESC LIMIT ?""",
            (f"%{door}%", since.isoformat(), limit),
        )
        return list(await cur.fetchall())

    async def events_between(
        self, start: datetime, end: datetime
    ) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            """SELECT * FROM events
               WHERE occurred_at >= ? AND occurred_at < ?
               ORDER BY occurred_at""",
            (start.isoformat(), end.isoformat()),
        )
        return list(await cur.fetchall())

    async def last_event_with_image(self) -> aiosqlite.Row | None:
        cur = await self.conn.execute(
            """SELECT * FROM events
               WHERE snapshot_url IS NOT NULL AND snapshot_url != ''
               ORDER BY occurred_at DESC LIMIT 1"""
        )
        return await cur.fetchone()

    async def absent_today(
        self,
        today_start: datetime,
        today_end: datetime,
        lookback_start: datetime,
    ) -> list[aiosqlite.Row]:
        """Люди, которых видели в [lookback_start..today_start], но не было сегодня."""
        cur = await self.conn.execute(
            """SELECT
                 person_id,
                 MAX(person_name) AS person_name,
                 MAX(occurred_at) AS last_seen,
                 (SELECT door_name FROM events e2
                    WHERE e2.person_id = e.person_id
                    ORDER BY occurred_at DESC LIMIT 1) AS last_door
               FROM events e
               WHERE occurred_at >= ? AND occurred_at < ?
                 AND person_id IS NOT NULL AND person_id != ''
                 AND person_id NOT IN (
                   SELECT DISTINCT person_id FROM events
                   WHERE occurred_at >= ? AND occurred_at < ?
                     AND person_id IS NOT NULL AND person_id != ''
                 )
               GROUP BY person_id
               ORDER BY MAX(occurred_at) DESC""",
            (
                lookback_start.isoformat(),
                today_start.isoformat(),
                today_start.isoformat(),
                today_end.isoformat(),
            ),
        )
        return list(await cur.fetchall())

    async def list_known_persons(
        self, lookback_start: datetime
    ) -> list[aiosqlite.Row]:
        """Все уникальные люди за окно. Last_seen и last_door."""
        cur = await self.conn.execute(
            """SELECT
                 person_id,
                 MAX(person_name) AS person_name,
                 MAX(occurred_at) AS last_seen,
                 (SELECT door_name FROM events e2
                    WHERE e2.person_id = e.person_id
                    ORDER BY occurred_at DESC LIMIT 1) AS last_door,
                 COUNT(*) AS total_passes
               FROM events e
               WHERE occurred_at >= ?
                 AND person_id IS NOT NULL AND person_id != ''
               GROUP BY person_id
               ORDER BY person_name COLLATE NOCASE""",
            (lookback_start.isoformat(),),
        )
        return list(await cur.fetchall())

    async def attendance_range(
        self, start_iso: str, end_iso: str
    ) -> list[aiosqlite.Row]:
        """Период по дням: person_id × локальная дата → first_in / last_out / passes.

        substr(occurred_at, 1, 10) даёт 'YYYY-MM-DD' прямо из ISO-строки
        (без UTC-конверсии SQLite, что важно при tz-aware хранении +05:00).
        """
        cur = await self.conn.execute(
            """SELECT
                 substr(occurred_at, 1, 10) AS day,
                 person_id,
                 MAX(person_name) AS person_name,
                 MIN(CASE WHEN direction='in'  THEN occurred_at END) AS first_in,
                 MAX(CASE WHEN direction='out' THEN occurred_at END) AS last_out,
                 COUNT(*) AS passes
               FROM events
               WHERE occurred_at >= ? AND occurred_at < ?
                 AND person_id IS NOT NULL AND person_id != ''
               GROUP BY day, person_id
               ORDER BY day, person_name COLLATE NOCASE""",
            (start_iso, end_iso),
        )
        return list(await cur.fetchall())

    async def attendance_today(
        self, day_start: datetime, day_end: datetime
    ) -> list[aiosqlite.Row]:
        """Для каждого person_id за день: первый вход и последний выход."""
        cur = await self.conn.execute(
            """SELECT
                 person_id,
                 MAX(person_name) AS person_name,
                 MIN(CASE WHEN direction='in'  THEN occurred_at END) AS first_in,
                 MAX(CASE WHEN direction='out' THEN occurred_at END) AS last_out,
                 COUNT(*) AS total_passes
               FROM events
               WHERE occurred_at >= ? AND occurred_at < ?
                 AND person_id IS NOT NULL AND person_id != ''
               GROUP BY person_id
               ORDER BY person_name COLLATE NOCASE""",
            (day_start.isoformat(), day_end.isoformat()),
        )
        return list(await cur.fetchall())

    async def stats_today(self, day_start: datetime, day_end: datetime) -> dict[str, int]:
        cur = await self.conn.execute(
            """SELECT
                 SUM(CASE WHEN direction='in'  THEN 1 ELSE 0 END) AS ins,
                 SUM(CASE WHEN direction='out' THEN 1 ELSE 0 END) AS outs,
                 COUNT(*) AS total
               FROM events
               WHERE occurred_at >= ? AND occurred_at < ?""",
            (day_start.isoformat(), day_end.isoformat()),
        )
        row = await cur.fetchone()
        return {
            "ins": int(row["ins"] or 0) if row else 0,
            "outs": int(row["outs"] or 0) if row else 0,
            "total": int(row["total"] or 0) if row else 0,
        }

    # --- meta ---
    async def set_meta(self, key: str, value: str) -> None:
        await self.conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await self.conn.commit()

    async def get_meta(self, key: str) -> str | None:
        cur = await self.conn.execute("SELECT value FROM meta WHERE key=?", (key,))
        row = await cur.fetchone()
        return row[0] if row else None

    # --- user language ---
    async def get_lang(self, user_id: int, default: str = "ru") -> str:
        cur = await self.conn.execute(
            "SELECT lang FROM user_lang WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        if row is None:
            return default
        return str(row[0]) or default

    async def set_lang(self, user_id: int, lang: str) -> None:
        await self.conn.execute(
            "INSERT INTO user_lang(user_id, lang) VALUES(?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET lang = excluded.lang",
            (user_id, lang),
        )
        await self.conn.commit()

    async def last_event_id(self) -> int | None:
        cur = await self.conn.execute("SELECT MAX(id) FROM events")
        row = await cur.fetchone()
        return int(row[0]) if row and row[0] is not None else None
