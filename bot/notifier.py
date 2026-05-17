"""Telegram-нотификатор для importance≥1 событий с rate-limit."""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import time
from time import monotonic

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile
from loguru import logger

from db import DB
from dss.client import DSSClient
from dss.models import Event
from dss.persons import DSSPersonClient
from bot.formatters import format_event
from bot.lang import LangResolver

# Кэш «person_id → группы из DSS» для нотификаций. TTL чтобы изменения в DSS
# не висели вечно; короткий — чтобы новые сотрудники получили метку быстро.
_DSS_GROUPS_TTL_SEC = 600  # 10 минут


# Скорость > полнота: пользователь хочет уведомления мгновенно. Если фото
# не успело подгрузиться на DSS-сторадж за ~10 сек, шлём текстом без фото.
# Большинство КП1-снимков отдаются с первой попытки; «упрямые» теряем —
# это осознанный компромисс ради instant-уведомлений.
SNAPSHOT_RETRY_DELAYS_SEC = (0.0, 1.5, 3.0, 6.0)


class TelegramNotifier:
    def __init__(
        self,
        bot: Bot,
        chat_ids: list[int],
        db: DB,
        resolver: LangResolver,
        dss: DSSClient | None = None,
        max_per_min: int = 20,
        work_day_start: time | None = None,
        work_day_end: time | None = None,
        chat_group_filters: dict[int, frozenset[str]] | None = None,
    ):
        self.bot = bot
        self.chat_ids = list(chat_ids)
        self.db = db
        self.resolver = resolver
        self.dss = dss
        self.max_per_min = max_per_min
        self.work_day_start = work_day_start
        self.work_day_end = work_day_end
        # chat_id → разрешённые группы. Чат без записи получает все события.
        self.chat_group_filters: dict[int, frozenset[str]] = dict(
            chat_group_filters or {}
        )
        self._sent_times: deque[float] = deque()
        self._lock = asyncio.Lock()
        # Удерживаем ссылки на фоновые задачи, иначе сборщик мусора может
        # убить незавершённую отправку с долгим ретраем.
        self._pending: set[asyncio.Task] = set()
        # DSS-fallback для меток отделов в уведомлениях, если локальная
        # person_groups ещё не подобрала человека (например, его DSS-группа
        # не в DSS_AUTO_SYNC_GROUPS, или авто-синк только что стартанул).
        self._dss_persons: DSSPersonClient | None = (
            DSSPersonClient(dss) if dss is not None else None
        )
        # person_id → (frozenset(groups), expires_at_monotonic).
        self._groups_cache: dict[str, tuple[frozenset[str], float]] = {}

    async def _fetch_snapshot(self, url: str) -> bytes | None:
        """Качает фото из DSS с долгими ретраями. Возвращает байты или None."""
        for attempt, delay in enumerate(SNAPSHOT_RETRY_DELAYS_SEC, start=1):
            if delay:
                await asyncio.sleep(delay)
            try:
                data = await self.dss.download_bytes(url)  # type: ignore[union-attr]
            except Exception as e:
                logger.warning("snapshot download failed (try {}): {}", attempt, e)
                return None
            if data:
                logger.debug("snapshot ok try={} size={}B", attempt, len(data))
                return data
            logger.debug("snapshot empty try={}, retry", attempt)
        logger.warning(
            "snapshot still empty after {} tries: {}",
            len(SNAPSHOT_RETRY_DELAYS_SEC), url,
        )
        return None

    async def _chat_lang(self, chat_id: int) -> str:
        return await self.resolver.get(chat_id)

    async def _send_one(
        self,
        chat_id: int,
        text: str,
        photo_data: bytes | None,
        photo_filename: str | None,
    ) -> bool:
        """Отправка одному получателю. Слот rate-limit берём здесь — на каждый
        чат свой счётчик, иначе при 3 админах в пиках упрёмся в 20 msg/мин."""
        async with self._lock:
            await self._wait_slot()
        try:
            if photo_data:
                try:
                    await self.bot.send_photo(
                        chat_id,
                        photo=BufferedInputFile(
                            photo_data, filename=photo_filename or "photo.jpg"
                        ),
                        caption=text,
                        parse_mode="HTML",
                    )
                    return True
                except TelegramAPIError as e:
                    logger.warning(
                        "send_photo rejected for chat={}, fallback to text: {}",
                        chat_id, e,
                    )
            await self.bot.send_message(chat_id, text, parse_mode="HTML")
            return True
        except TelegramAPIError as e:
            # Один админ заблокировал бота / удалил чат → не валим остальных.
            logger.warning("TG send fail for chat={}: {}", chat_id, e)
            return False
        except Exception as e:
            logger.exception("TG send unexpected for chat={}: {}", chat_id, e)
            return False

    def _format(
        self, event_row: dict, lang: str, groups: frozenset[str] | None = None
    ) -> str:
        return format_event(
            event_row,
            work_day_start=self.work_day_start,
            work_day_end=self.work_day_end,
            lang=lang,
            groups=groups,
        )

    async def _person_groups(self, person_id: str | None) -> frozenset[str]:
        """Группы человека для метки 🏷 в уведомлении.

        Источник 1 — локальная `person_groups` (быстро, дёшево).
        Источник 2 (fallback) — прямой запрос к DSS, если локально пусто.
        Это закрывает кейс «авто-синк ещё не подобрал, но человек уже ходит».
        Результат кэшируется на _DSS_GROUPS_TTL_SEC, чтобы не дёргать DSS на
        каждое событие — большинство людей либо уже синкнуты, либо лежат
        в кэше после первого прохода.
        """
        if not person_id:
            return frozenset()
        local = frozenset(await self.db.groups_for_person(person_id))
        if local:
            return local
        if self._dss_persons is None:
            return frozenset()
        now = monotonic()
        cached = self._groups_cache.get(person_id)
        if cached and cached[1] > now:
            return cached[0]
        try:
            dss_groups = await self._dss_persons.find_person_groups(person_id)
        except Exception as e:
            logger.warning("notifier DSS groups fallback failed: {}", e)
            dss_groups = []
        result = frozenset(dss_groups)
        self._groups_cache[person_id] = (result, now + _DSS_GROUPS_TTL_SEC)
        return result

    def _chat_allowed(
        self, chat_id: int, person_groups: frozenset[str]
    ) -> bool:
        """Чат без фильтра — пропускаем всегда. С фильтром — только если человек
        состоит хотя бы в одной из разрешённых групп."""
        allowed = self.chat_group_filters.get(chat_id)
        if not allowed:
            return True
        return bool(person_groups & allowed)

    async def _broadcast(
        self,
        event_row: dict,
        photo_data: bytes | None,
        photo_filename: str | None,
        person_id: str | None,
    ) -> tuple[int, int]:
        """Возвращает (sent_ok, eligible).
        eligible — сколько чатов прошли фильтр и должны были получить событие;
        sent_ok — сколько реально получили (Telegram-ошибки и т.п. вычитаются).
        Форматирует текст один раз на язык (per-broadcast cache).
        """
        person_groups = await self._person_groups(person_id)
        per_lang: dict[str, str] = {}
        ok = 0
        eligible = 0
        for chat_id in self.chat_ids:
            if not self._chat_allowed(chat_id, person_groups):
                continue
            eligible += 1
            lang = await self._chat_lang(chat_id)
            text = per_lang.get(lang)
            if text is None:
                text = self._format(event_row, lang, groups=person_groups)
                per_lang[lang] = text
            if await self._send_one(chat_id, text, photo_data, photo_filename):
                ok += 1
        return ok, eligible

    async def _wait_slot(self) -> None:
        while True:
            now = monotonic()
            while self._sent_times and now - self._sent_times[0] > 60:
                self._sent_times.popleft()
            if len(self._sent_times) < self.max_per_min:
                self._sent_times.append(now)
                return
            sleep_for = 60 - (now - self._sent_times[0]) + 0.1
            logger.warning("rate-limit, sleep {:.1f}s", sleep_for)
            await asyncio.sleep(sleep_for)

    async def notify(self, event: Event, importance: int, db_id: int) -> None:
        """Возвращается мгновенно: реальная отправка идёт в фоне, чтобы
        ретрай DSS-картинки не блокировал обработку следующих событий."""
        if not self.chat_ids:
            return
        task = asyncio.create_task(self._dispatch(event, importance, db_id))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _dispatch(self, event: Event, importance: int, db_id: int) -> None:
        event_row = {
            "importance": importance,
            "person_name": event.person_name,
            "door_name": event.door_name,
            "direction": event.direction,
            "event_name": event.event_name,
            "event_type": event.event_type,
            "occurred_at": event.occurred_at,
        }
        data: bytes | None = None
        filename: str | None = None
        if event.snapshot_url and self.dss is not None:
            data = await self._fetch_snapshot(event.snapshot_url)
            if data:
                filename = event.snapshot_url.rsplit("/", 1)[-1] or "photo.jpg"

        ok, eligible = await self._broadcast(
            event_row, data, filename, event.person_id
        )
        # Помечаем sent, если хотя бы одному админу дошло ИЛИ событие
        # отфильтровано для всех (eligible=0): ретраить бессмысленно, фильтр
        # не изменится в drain_unsent.
        if ok > 0 or eligible == 0:
            await self.db.mark_sent(db_id)

    async def drain_unsent(self) -> int:
        """Раз в N сек добивает упавшие отправки."""
        rows = await self.db.fetch_unsent(min_importance=1, limit=20)
        count = 0
        for r in rows:
            d = dict(r)
            url = d.get("snapshot_url") or ""
            data: bytes | None = None
            fname: str | None = None
            if url and self.dss is not None:
                data = await self._fetch_snapshot(url)
                if data:
                    fname = url.rsplit("/", 1)[-1] or "photo.jpg"
            try:
                ok, eligible = await self._broadcast(
                    d, data, fname, d.get("person_id")
                )
                if ok > 0 or eligible == 0:
                    await self.db.mark_sent(int(r["id"]))
                    count += 1
            except Exception as e:
                logger.warning("drain fail #{}: {}", r["id"], e)
                break
        return count
