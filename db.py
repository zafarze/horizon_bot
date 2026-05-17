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

CREATE TABLE IF NOT EXISTS person_groups (
    person_id  TEXT NOT NULL,
    group_name TEXT NOT NULL,
    PRIMARY KEY (person_id, group_name)
);
CREATE INDEX IF NOT EXISTS idx_person_groups_group ON person_groups(group_name);

CREATE TABLE IF NOT EXISTS teachers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    phone         TEXT,
    name_en       TEXT,
    name_ru       TEXT,
    name_tg       TEXT,
    subject       TEXT,
    position      TEXT,
    person_id     TEXT,
    photo_file_id TEXT,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME
);
-- Уникальность телефона только когда он непустой: учителя без телефона
-- идентифицируются по name_en (не PK, дубликаты теоретически возможны,
-- защита от них — на уровне upsert_teacher).
CREATE UNIQUE INDEX IF NOT EXISTS idx_teachers_phone
  ON teachers(phone) WHERE phone IS NOT NULL AND phone != '';
CREATE INDEX IF NOT EXISTS idx_teachers_person_id ON teachers(person_id);
CREATE INDEX IF NOT EXISTS idx_teachers_name_en ON teachers(name_en COLLATE NOCASE);
"""


class DB:
    def __init__(self, path: Path):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._migrate()
        await self._conn.commit()

    async def _migrate(self) -> None:
        """Лёгкие миграции — добавление колонок в существующие таблицы.
        SQLite ALTER TABLE ADD COLUMN не поддерживает IF NOT EXISTS, поэтому
        проверяем через PRAGMA table_info."""
        cur = await self._conn.execute("PRAGMA table_info(teachers)")
        cols = {row[1] for row in await cur.fetchall()}
        if "photo_file_id" not in cols:
            await self._conn.execute(
                "ALTER TABLE teachers ADD COLUMN photo_file_id TEXT"
            )
        if "position" not in cols:
            await self._conn.execute(
                "ALTER TABLE teachers ADD COLUMN position TEXT"
            )

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "DB не подключена"
        return self._conn

    @staticmethod
    def _group_filter(
        restrict_groups: Iterable[str] | None,
        column: str = "person_id",
    ) -> tuple[str, list]:
        """Возвращает SQL-фрагмент и параметры для фильтра по членству в группе.
        Пустой restrict_groups → ('', []) — фильтр не применяется."""
        groups = [g for g in (restrict_groups or []) if g]
        if not groups:
            return "", []
        ph = ",".join(["?"] * len(groups))
        clause = (
            f" AND {column} IN ("
            f"SELECT person_id FROM person_groups WHERE group_name IN ({ph}))"
        )
        return clause, list(groups)

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

    async def list_inside(
        self, restrict_groups: Iterable[str] | None = None
    ) -> list[aiosqlite.Row]:
        clause, params = self._group_filter(restrict_groups)
        cur = await self.conn.execute(
            f"SELECT * FROM persons_inside WHERE 1=1{clause} ORDER BY entered_at",
            params,
        )
        return list(await cur.fetchall())

    async def count_inside(
        self, restrict_groups: Iterable[str] | None = None
    ) -> int:
        clause, params = self._group_filter(restrict_groups)
        cur = await self.conn.execute(
            f"SELECT COUNT(*) FROM persons_inside WHERE 1=1{clause}",
            params,
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    # --- queries ---
    async def find_by_name(
        self, name: str, limit: int = 10,
        restrict_groups: Iterable[str] | None = None,
    ) -> list[aiosqlite.Row]:
        clause, params = self._group_filter(restrict_groups)
        cur = await self.conn.execute(
            f"""SELECT * FROM events
               WHERE person_name LIKE ?{clause}
               ORDER BY occurred_at DESC LIMIT ?""",
            (f"%{name}%", *params, limit),
        )
        return list(await cur.fetchall())

    async def find_by_name_with_image(
        self, name: str, limit: int = 5,
        restrict_groups: Iterable[str] | None = None,
    ) -> list[aiosqlite.Row]:
        clause, params = self._group_filter(restrict_groups)
        cur = await self.conn.execute(
            f"""SELECT * FROM events
               WHERE person_name LIKE ?
                 AND snapshot_url IS NOT NULL AND snapshot_url != ''{clause}
               ORDER BY occurred_at DESC LIMIT ?""",
            (f"%{name}%", *params, limit),
        )
        return list(await cur.fetchall())

    async def events_by_door(
        self, door: str, since: datetime, limit: int = 30,
        restrict_groups: Iterable[str] | None = None,
    ) -> list[aiosqlite.Row]:
        clause, params = self._group_filter(restrict_groups)
        cur = await self.conn.execute(
            f"""SELECT * FROM events
               WHERE door_name LIKE ? AND occurred_at >= ?{clause}
               ORDER BY occurred_at DESC LIMIT ?""",
            (f"%{door}%", since.isoformat(), *params, limit),
        )
        return list(await cur.fetchall())

    async def events_between(
        self, start: datetime, end: datetime,
        restrict_groups: Iterable[str] | None = None,
    ) -> list[aiosqlite.Row]:
        clause, params = self._group_filter(restrict_groups)
        cur = await self.conn.execute(
            f"""SELECT * FROM events
               WHERE occurred_at >= ? AND occurred_at < ?{clause}
               ORDER BY occurred_at""",
            (start.isoformat(), end.isoformat(), *params),
        )
        return list(await cur.fetchall())

    async def last_event_with_image(
        self, restrict_groups: Iterable[str] | None = None,
    ) -> aiosqlite.Row | None:
        clause, params = self._group_filter(restrict_groups)
        cur = await self.conn.execute(
            f"""SELECT * FROM events
               WHERE snapshot_url IS NOT NULL AND snapshot_url != ''{clause}
               ORDER BY occurred_at DESC LIMIT 1""",
            params,
        )
        return await cur.fetchone()

    async def absent_today(
        self,
        today_start: datetime,
        today_end: datetime,
        lookback_start: datetime,
        restrict_groups: Iterable[str] | None = None,
    ) -> list[aiosqlite.Row]:
        """Люди, которых видели в [lookback_start..today_start], но не было сегодня."""
        clause, params = self._group_filter(restrict_groups)
        cur = await self.conn.execute(
            f"""SELECT
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
                 ){clause}
               GROUP BY person_id
               ORDER BY MAX(occurred_at) DESC""",
            (
                lookback_start.isoformat(),
                today_start.isoformat(),
                today_start.isoformat(),
                today_end.isoformat(),
                *params,
            ),
        )
        return list(await cur.fetchall())

    async def absent_range(
        self,
        start_iso: str,
        end_iso: str,
        lookback_iso: str,
        restrict_groups: Iterable[str] | None = None,
    ) -> tuple[list[aiosqlite.Row], dict[str, set[str]]]:
        """Возвращает (regulars, seen_by_day) для построения pivot «не пришли».

        regulars: люди, которые были активны в окне [lookback_iso, start_iso) —
        список aiosqlite.Row с полями person_id, person_name. Это «база»,
        от которой считаем отсутствия (новички, появившиеся только внутри
        периода, в базу не попадают, чтобы не показывать им фиктивные
        пропуски за дни до их появления).

        seen_by_day: словарь day_iso (YYYY-MM-DD) → множество person_id,
        которые в этот день хоть раз отметились в [start_iso, end_iso).
        """
        clause, params = self._group_filter(restrict_groups)
        cur = await self.conn.execute(
            f"""SELECT person_id, MAX(person_name) AS person_name
               FROM events
               WHERE occurred_at >= ? AND occurred_at < ?
                 AND person_id IS NOT NULL AND person_id != ''{clause}
               GROUP BY person_id
               ORDER BY person_name COLLATE NOCASE""",
            (lookback_iso, start_iso, *params),
        )
        regulars = list(await cur.fetchall())

        cur = await self.conn.execute(
            f"""SELECT substr(occurred_at, 1, 10) AS day, person_id
               FROM events
               WHERE occurred_at >= ? AND occurred_at < ?
                 AND person_id IS NOT NULL AND person_id != ''{clause}
               GROUP BY day, person_id""",
            (start_iso, end_iso, *params),
        )
        seen_by_day: dict[str, set[str]] = {}
        for row in await cur.fetchall():
            seen_by_day.setdefault(row["day"], set()).add(str(row["person_id"]))
        return regulars, seen_by_day

    async def list_known_persons(
        self, lookback_start: datetime,
        restrict_groups: Iterable[str] | None = None,
    ) -> list[aiosqlite.Row]:
        """Все уникальные люди за окно. Last_seen и last_door."""
        clause, params = self._group_filter(restrict_groups)
        cur = await self.conn.execute(
            f"""SELECT
                 person_id,
                 MAX(person_name) AS person_name,
                 MAX(occurred_at) AS last_seen,
                 (SELECT door_name FROM events e2
                    WHERE e2.person_id = e.person_id
                    ORDER BY occurred_at DESC LIMIT 1) AS last_door,
                 COUNT(*) AS total_passes
               FROM events e
               WHERE occurred_at >= ?
                 AND person_id IS NOT NULL AND person_id != ''{clause}
               GROUP BY person_id
               ORDER BY person_name COLLATE NOCASE""",
            (lookback_start.isoformat(), *params),
        )
        return list(await cur.fetchall())

    async def attendance_range(
        self, start_iso: str, end_iso: str,
        restrict_groups: Iterable[str] | None = None,
    ) -> list[aiosqlite.Row]:
        """Период по дням: person_id × локальная дата → first_in / last_out / passes.

        substr(occurred_at, 1, 10) даёт 'YYYY-MM-DD' прямо из ISO-строки
        (без UTC-конверсии SQLite, что важно при tz-aware хранении +05:00).
        """
        clause, params = self._group_filter(restrict_groups)
        cur = await self.conn.execute(
            f"""SELECT
                 substr(occurred_at, 1, 10) AS day,
                 person_id,
                 MAX(person_name) AS person_name,
                 MIN(CASE WHEN direction='in'  THEN occurred_at END) AS first_in,
                 MAX(CASE WHEN direction='out' THEN occurred_at END) AS last_out,
                 COUNT(*) AS passes
               FROM events
               WHERE occurred_at >= ? AND occurred_at < ?
                 AND person_id IS NOT NULL AND person_id != ''{clause}
               GROUP BY day, person_id
               ORDER BY day, person_name COLLATE NOCASE""",
            (start_iso, end_iso, *params),
        )
        return list(await cur.fetchall())

    async def attendance_today(
        self, day_start: datetime, day_end: datetime,
        restrict_groups: Iterable[str] | None = None,
    ) -> list[aiosqlite.Row]:
        """Для каждого person_id за день: первый вход и последний выход."""
        clause, params = self._group_filter(restrict_groups)
        cur = await self.conn.execute(
            f"""SELECT
                 person_id,
                 MAX(person_name) AS person_name,
                 MIN(CASE WHEN direction='in'  THEN occurred_at END) AS first_in,
                 MAX(CASE WHEN direction='out' THEN occurred_at END) AS last_out,
                 COUNT(*) AS total_passes
               FROM events
               WHERE occurred_at >= ? AND occurred_at < ?
                 AND person_id IS NOT NULL AND person_id != ''{clause}
               GROUP BY person_id
               ORDER BY person_name COLLATE NOCASE""",
            (day_start.isoformat(), day_end.isoformat(), *params),
        )
        return list(await cur.fetchall())

    async def stats_today(
        self, day_start: datetime, day_end: datetime,
        restrict_groups: Iterable[str] | None = None,
    ) -> dict[str, int]:
        clause, params = self._group_filter(restrict_groups)
        cur = await self.conn.execute(
            f"""SELECT
                 SUM(CASE WHEN direction='in'  THEN 1 ELSE 0 END) AS ins,
                 SUM(CASE WHEN direction='out' THEN 1 ELSE 0 END) AS outs,
                 COUNT(*) AS total
               FROM events
               WHERE occurred_at >= ? AND occurred_at < ?{clause}""",
            (day_start.isoformat(), day_end.isoformat(), *params),
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

    # --- person_groups ---
    async def add_person_to_group(self, person_id: str, group: str) -> bool:
        cur = await self.conn.execute(
            "INSERT OR IGNORE INTO person_groups(person_id, group_name) VALUES(?, ?)",
            (person_id, group),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def remove_person_from_group(self, person_id: str, group: str) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM person_groups WHERE person_id=? AND group_name=?",
            (person_id, group),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def replace_group_members(
        self, group: str, person_ids: Iterable[str]
    ) -> tuple[int, int]:
        """Зеркалирует членство группы под перечень person_ids.

        Атомарно (одна транзакция): добавляет недостающих, удаляет лишних.
        Возвращает (added, removed). Используется фоновым DSS-синком — НЕ
        вызывать с пустым списком, если есть подозрение на ошибку запроса
        (вызывающий должен сам валидировать «реально пустая группа vs
        битый ответ»)."""
        incoming = {str(p).strip() for p in person_ids if str(p).strip()}
        cur = await self.conn.execute(
            "SELECT person_id FROM person_groups WHERE group_name = ?", (group,)
        )
        existing = {str(row[0]) for row in await cur.fetchall()}
        to_add = incoming - existing
        to_remove = existing - incoming
        if to_add:
            await self.conn.executemany(
                "INSERT OR IGNORE INTO person_groups(person_id, group_name) "
                "VALUES(?, ?)",
                [(pid, group) for pid in to_add],
            )
        if to_remove:
            ph = ",".join(["?"] * len(to_remove))
            await self.conn.execute(
                f"DELETE FROM person_groups "
                f"WHERE group_name = ? AND person_id IN ({ph})",
                (group, *to_remove),
            )
        await self.conn.commit()
        return len(to_add), len(to_remove)

    async def groups_for_person(self, person_id: str) -> set[str]:
        cur = await self.conn.execute(
            "SELECT group_name FROM person_groups WHERE person_id=?", (person_id,)
        )
        return {row[0] for row in await cur.fetchall()}

    async def persons_in_group(self, group: str) -> list[aiosqlite.Row]:
        """person_id и (если встречалось в events) последнее имя."""
        cur = await self.conn.execute(
            """SELECT pg.person_id AS person_id,
                      (SELECT person_name FROM events e
                        WHERE e.person_id = pg.person_id AND person_name IS NOT NULL
                        ORDER BY occurred_at DESC LIMIT 1) AS person_name
               FROM person_groups pg
               WHERE pg.group_name = ?
               ORDER BY person_name COLLATE NOCASE""",
            (group,),
        )
        return list(await cur.fetchall())

    async def list_groups(self) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            """SELECT group_name, COUNT(*) AS n
               FROM person_groups
               GROUP BY group_name
               ORDER BY group_name COLLATE NOCASE"""
        )
        return list(await cur.fetchall())

    async def find_unique_persons_by_name(
        self, query: str, limit: int = 10
    ) -> list[aiosqlite.Row]:
        """Уникальные (person_id, person_name) из events по подстроке имени."""
        cur = await self.conn.execute(
            """SELECT person_id,
                      MAX(person_name) AS person_name,
                      MAX(occurred_at) AS last_seen
               FROM events
               WHERE person_name LIKE ?
                 AND person_id IS NOT NULL AND person_id != ''
               GROUP BY person_id
               ORDER BY last_seen DESC
               LIMIT ?""",
            (f"%{query}%", limit),
        )
        return list(await cur.fetchall())

    # --- teachers ---
    async def upsert_teacher(
        self,
        *,
        phone: str | None,
        name_en: str | None,
        name_ru: str | None,
        name_tg: str | None,
        subject: str | None,
        position: str | None = None,
        photo_file_id: str | None = None,
    ) -> tuple[int, bool]:
        """Возвращает (teacher_id, created).
        Дедуп: сначала по phone (если непустой), иначе по name_en.
        Существующая строка обновляется, person_id не трогается.
        photo_file_id обновляется только если передан непустой.
        """
        phone_n = (phone or "").strip() or None
        existing = None
        if phone_n:
            cur = await self.conn.execute(
                "SELECT id FROM teachers WHERE phone = ?", (phone_n,)
            )
            existing = await cur.fetchone()
        if existing is None and name_en:
            cur = await self.conn.execute(
                "SELECT id FROM teachers WHERE phone IS NULL AND name_en = ? COLLATE NOCASE",
                (name_en,),
            )
            existing = await cur.fetchone()
        if existing is not None:
            await self.conn.execute(
                """UPDATE teachers SET
                     phone = COALESCE(?, phone),
                     name_en = COALESCE(?, name_en),
                     name_ru = COALESCE(?, name_ru),
                     name_tg = COALESCE(?, name_tg),
                     subject = COALESCE(?, subject),
                     position = COALESCE(?, position),
                     photo_file_id = COALESCE(?, photo_file_id),
                     updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (
                    phone_n, name_en, name_ru, name_tg, subject, position,
                    photo_file_id, int(existing["id"]),
                ),
            )
            await self.conn.commit()
            return int(existing["id"]), False
        cur = await self.conn.execute(
            """INSERT INTO teachers
                 (phone, name_en, name_ru, name_tg, subject, position,
                  photo_file_id, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (phone_n, name_en, name_ru, name_tg, subject, position,
             photo_file_id),
        )
        await self.conn.commit()
        return int(cur.lastrowid), True

    async def link_teacher(self, teacher_id: int, person_id: str) -> None:
        await self.conn.execute(
            "UPDATE teachers SET person_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (person_id, teacher_id),
        )
        await self.conn.commit()

    async def unlink_teacher(self, teacher_id: int) -> None:
        await self.conn.execute(
            "UPDATE teachers SET person_id = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (teacher_id,),
        )
        await self.conn.commit()

    async def find_teacher_by_person_id(
        self, person_id: str
    ) -> aiosqlite.Row | None:
        """Запись из teachers, привязанная к указанному DSS person_id.
        Используется для отображения карточки человека (должность, предмет)."""
        if not person_id:
            return None
        cur = await self.conn.execute(
            "SELECT * FROM teachers WHERE person_id = ? LIMIT 1",
            (str(person_id),),
        )
        return await cur.fetchone()

    async def get_teacher(self, teacher_id: int) -> aiosqlite.Row | None:
        cur = await self.conn.execute(
            "SELECT * FROM teachers WHERE id = ?", (teacher_id,)
        )
        return await cur.fetchone()

    async def teachers_unlinked(self, limit: int = 200) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            """SELECT * FROM teachers
               WHERE person_id IS NULL OR person_id = ''
               ORDER BY name_en COLLATE NOCASE
               LIMIT ?""",
            (limit,),
        )
        return list(await cur.fetchall())

    async def teachers_stats(self) -> dict[str, int]:
        cur = await self.conn.execute(
            """SELECT
                 COUNT(*) AS total,
                 SUM(CASE WHEN person_id IS NOT NULL AND person_id != '' THEN 1 ELSE 0 END) AS linked
               FROM teachers"""
        )
        row = await cur.fetchone()
        total = int(row["total"] or 0) if row else 0
        linked = int(row["linked"] or 0) if row else 0
        return {"total": total, "linked": linked, "unlinked": total - linked}

    async def sync_linked_teachers_to_group(self, group: str) -> int:
        """Добавляет всех привязанных к DSS учителей в указанную группу.
        Идемпотентно: дубликаты игнорируются. Возвращает число фактически
        добавленных (не считая уже существовавших)."""
        cur = await self.conn.execute(
            """INSERT OR IGNORE INTO person_groups(person_id, group_name)
               SELECT DISTINCT person_id, ? FROM teachers
                WHERE person_id IS NOT NULL AND person_id != ''""",
            (group,),
        )
        await self.conn.commit()
        return cur.rowcount or 0

    async def find_teacher(self, query: str) -> list[aiosqlite.Row]:
        """Поиск по телефону (точно, нормализованно) или подстроке любого имени."""
        q = (query or "").strip()
        if not q:
            return []
        # нормализованный телефон: только цифры
        digits = "".join(ch for ch in q if ch.isdigit())
        if digits and len(digits) >= 7:
            cur = await self.conn.execute(
                """SELECT * FROM teachers
                   WHERE replace(replace(replace(replace(phone, ' ', ''), '-', ''), '(', ''), ')', '')
                         LIKE ?""",
                (f"%{digits}%",),
            )
            rows = list(await cur.fetchall())
            if rows:
                return rows
        like = f"%{q}%"
        cur = await self.conn.execute(
            """SELECT * FROM teachers
               WHERE name_en LIKE ? OR name_ru LIKE ? OR name_tg LIKE ?
               ORDER BY name_en COLLATE NOCASE
               LIMIT 10""",
            (like, like, like),
        )
        return list(await cur.fetchall())

    async def last_event_id(self) -> int | None:
        cur = await self.conn.execute("SELECT MAX(id) FROM events")
        row = await cur.fetchone()
        return int(row[0]) if row and row[0] is not None else None
