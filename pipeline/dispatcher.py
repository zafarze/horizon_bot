"""Связывает поток DSS-событий с БД и Telegram-нотификатором."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import AsyncIterator, Callable, Awaitable

from loguru import logger

from db import DB
from dss.models import Event
from .importance import ImportanceRules, classify


# Свежее событие — то, что произошло не более N секунд назад. Бэкфил при рестарте
# содержит историю за 24 часа; рассылать её всю в DM было бы флудом.
NORMAL_EVENT_FRESHNESS_SEC = 300  # 5 минут


class Dispatcher:
    def __init__(
        self,
        db: DB,
        rules: ImportanceRules,
        notify: Callable[[Event, int, int], Awaitable[None]] | None = None,
        notify_ignore_device_patterns: tuple[str, ...] = (),
    ):
        """notify(event, importance, db_id) — вызывается на каждое свежее событие
        (вход/выход) и на все важные/тревожные независимо от свежести.

        notify_ignore_device_patterns — устройства, чьи события пишутся в БД,
        но НЕ уведомляются (например, лифтовые ридеры)."""
        self.db = db
        self.rules = rules
        self.notify = notify
        self.notify_ignore_device_patterns = notify_ignore_device_patterns

    async def run(self, stream: AsyncIterator[Event]) -> None:
        async for evt in stream:
            try:
                await self._handle(evt)
            except Exception as e:
                logger.exception("dispatcher handle fail: {}", e)

    async def _handle(self, evt: Event) -> None:
        importance = classify(evt, self.rules)
        row = evt.to_db_row()
        row["importance"] = importance
        new_id = await self.db.insert_event(row)
        if new_id is None:
            return  # дубликат

        # Обновляем persons_inside по любому проходу с направлением. Привязка
        # к event_type "PassGranted" не работает: V8 отдаёт alarmTypeId=число.
        if evt.person_id and evt.direction in ("in", "out"):
            ts = evt.occurred_at.isoformat()
            if evt.direction == "in":
                await self.db.upsert_inside(
                    evt.person_id,
                    evt.person_name or "",
                    evt.door_name or "",
                    ts,
                )
            else:
                await self.db.remove_inside(evt.person_id)

        # маскированные ФИО для лога
        masked = _mask(evt.person_name)
        logger.info(
            "evt #{} imp={} {} | {} | door={} dir={}",
            new_id, importance, evt.event_type, masked,
            evt.door_name, evt.direction,
        )

        if self.notify is None:
            return

        # Глушим уведомления для шумных устройств (лифт и т.п.) — но событие
        # уже сохранено в БД, доступно через /find.
        if self.notify_ignore_device_patterns:
            haystack = (
                (evt.door_name or "")
                + " "
                + str(evt.raw.get("deviceName") or "")
            ).lower()
            for pattern in self.notify_ignore_device_patterns:
                if pattern.lower() in haystack:
                    return

        should_notify = False
        if importance >= 1:
            # тревоги и важные шлём всегда (даже из истории — вдруг бот лежал во
            # время инцидента и сейчас догнал).
            should_notify = True
        elif evt.person_id:
            # любое свежее событие с опознанным человеком — шлём (включая
            # Face4Elevator, где direction не парсится из имени устройства).
            # Свежесть нужна, чтобы рестарт бота не вылил backfill за 24ч.
            age = (datetime.now(timezone.utc) - evt.occurred_at).total_seconds()
            if 0 <= age <= NORMAL_EVENT_FRESHNESS_SEC:
                should_notify = True

        if should_notify:
            try:
                await self.notify(evt, importance, new_id)
            except Exception as e:
                logger.warning("notify failed: {}", e)


def _mask(name: str | None) -> str:
    if not name:
        return "—"
    parts = name.strip().split()
    if len(parts) == 1:
        p = parts[0]
        return p[0] + "***" if p else "—"
    return parts[0] + " " + parts[1][0] + "."
