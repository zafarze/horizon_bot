"""Опрос событий DSS Pro V8 (pull-based).

DSS Pro V8 для access-control событий не предоставляет push/long-poll по умолчанию.
Вместо этого используется страничный fetch по временному окну:
    POST /obms/api/v1.1/acs/access/record/fetch/page
    body: {page, pageSize, startTime, endTime}  — времена unix-seconds как строки
    response: {data: {pageData: [...]}}

Эндпоинт и формат подтверждены реальной интеграцией DSS Pro V8
(см. mwandotheboss/Dahua-DSS-Integration).
"""
from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from datetime import datetime
from typing import AsyncIterator
from zoneinfo import ZoneInfo

from loguru import logger

from .client import DSSClient
from .models import Event


# Локальная TZ берётся из .env (TZ=Asia/Dushanbe). Если не задана — Душанбе.
# Все времена событий приводятся к этой зоне, чтобы /today, /attendance и
# отображение в Telegram показывали локальное время, а не UTC.
LOCAL_TZ = ZoneInfo(os.getenv("TZ", "Asia/Dushanbe"))

FETCH_PATH = "/obms/api/v1.1/acs/access/record/fetch/page"
PAGE_SIZE = 100
POLL_INTERVAL_SEC = 5.0
# overlap гарантирует, что событие на границе окна попадает в выборку дважды
# (дубль отсеется по id), но не теряется, если DSS пишет его с лагом.
LOOKBACK_OVERLAP_SEC = 60
# При первом запуске подтягиваем историю за последние сутки, чтобы /today и /inside
# сразу показывали реальные цифры за сегодня. Дедуп по id отсеивает повторы при
# рестарте, UNIQUE-constraint в БД — финальная защита.
BACKFILL_HOURS = 24
# верхняя граница циклов пагинации — защита от бесконечного цикла при сбоях DSS
# и достаточная ёмкость для backfill (200 * 100 = 20000 событий за сутки).
MAX_PAGES_PER_POLL = 200
# размер окна дедупликации — храним последние N id
DEDUP_WINDOW = 2000


class EventSubscriber:
    """Совместимый интерфейс с прежней long-poll реализацией."""

    def __init__(self, dss: DSSClient):
        self.dss = dss
        self._last_end: float | None = None
        self._seen_ids: deque[str] = deque(maxlen=DEDUP_WINDOW)
        self._seen_set: set[str] = set()

    async def subscribe(self) -> str:
        # Pull-based: подписываться нечего, фиксируем стартовую точку.
        # Сдвигаем назад на BACKFILL_HOURS, чтобы первый poll забрал историю за сутки.
        self._last_end = time.time() - BACKFILL_HOURS * 3600
        logger.info(
            "DSS event polling armed: path={}, interval={}s, overlap={}s, backfill={}h",
            FETCH_PATH, POLL_INTERVAL_SEC, LOOKBACK_OVERLAP_SEC, BACKFILL_HOURS,
        )
        return "polling"

    async def unsubscribe(self) -> None:
        return None

    async def poll_once(self) -> list[dict]:
        now = time.time()
        base = self._last_end if self._last_end is not None else now
        start = base - LOOKBACK_OVERLAP_SEC
        end = now

        all_records: list[dict] = []
        page = 1
        while page <= MAX_PAGES_PER_POLL:
            body = {
                "page": str(page),
                "pageSize": str(PAGE_SIZE),
                "startTime": str(int(start)),
                "endTime": str(int(end)),
            }
            resp = await self.dss.request("POST", FETCH_PATH, json=body)
            page_data = self._extract_page_data(resp)
            if not page_data:
                break
            all_records.extend(page_data)
            if len(page_data) < PAGE_SIZE:
                break
            page += 1

        self._last_end = end

        # Дамп первой записи окна — помогает увидеть реальные имена полей DSS.
        if all_records:
            sample = all_records[0]
            logger.debug(
                "DSS poll sample: keys={} item={}",
                sorted(sample.keys()) if isinstance(sample, dict) else type(sample),
                sample,
            )
            # Сколько записей в окне имеют URL фото — и дамп первой такой.
            with_img = [r for r in all_records if isinstance(r, dict) and r.get("captureImageUrl")]
            logger.debug(
                "DSS poll images: {}/{} имеют captureImageUrl",
                len(with_img), len(all_records),
            )
            if with_img:
                logger.debug("DSS poll first-with-image: {}", with_img[0])

        # Дедупликация по id.
        new_records: list[dict] = []
        for r in all_records:
            rid = str(r.get("id") or "")
            if not rid or rid in self._seen_set:
                continue
            self._seen_set.add(rid)
            self._seen_ids.append(rid)
            new_records.append(r)
        # Триммим set до размера deque (deque сам отбрасывает старые при maxlen).
        if len(self._seen_set) > DEDUP_WINDOW:
            self._seen_set = set(self._seen_ids)
        return new_records

    async def stream(self) -> AsyncIterator[Event]:
        backoff = 1.0
        await self.subscribe()
        while True:
            try:
                msgs = await self.poll_once()
                backoff = 1.0
                for raw in msgs:
                    evt = parse_event(raw)
                    if evt is not None:
                        yield evt
                await asyncio.sleep(POLL_INTERVAL_SEC)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("DSS poll error: {} (backoff {}s)", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    @staticmethod
    def _extract_page_data(resp: dict) -> list[dict]:
        # DSS V8 возвращает {data: {pageData: [...]}}.
        # На всякий случай поддерживаем варианты, встречающиеся в других билдах.
        if not isinstance(resp, dict):
            return []
        data = resp.get("data")
        if isinstance(data, dict):
            page_data = data.get("pageData") or data.get("list") or data.get("records")
            if isinstance(page_data, list):
                return page_data
        if isinstance(data, list):
            return data
        for key in ("pageData", "records", "list"):
            v = resp.get(key)
            if isinstance(v, list):
                return v
        return []


def parse_event(raw: dict) -> Event | None:
    """Маппинг записи DSS Pro V8 access-control в доменный Event.

    Реальные поля DSS V8 (из /obms/api/v1.1/acs/access/record/fetch/page):
      id, alarmTime, deviceCode, deviceName, channelId, channelName,
      alarmTypeId, alarmTypeName, personId, firstName, lastName,
      captureImageUrl, pointName.
    """
    try:
        evt_id = str(raw.get("id") or raw.get("messageId") or "")
        if not evt_id:
            return None

        evt_type = str(
            raw.get("alarmTypeId")
            or raw.get("eventCode")
            or raw.get("eventType")
            or "unknown"
        )
        evt_name = str(
            raw.get("alarmTypeName")
            or raw.get("eventName")
            or evt_type
        )

        person_id_raw = raw.get("personId") or raw.get("userId")
        person_id = str(person_id_raw) if person_id_raw not in (None, "", 0, "0") else None

        first = (raw.get("firstName") or "").strip()
        last = (raw.get("lastName") or "").strip()
        person_name = (f"{first} {last}".strip()) or raw.get("personName") or raw.get("userName")
        if isinstance(person_name, str):
            person_name = person_name.strip() or None

        door_id_raw = raw.get("channelId") or raw.get("doorId")
        door_id = str(door_id_raw) if door_id_raw not in (None, "") else None
        # deviceName первым — там номер турникета («Кп1 Вход турникет 4»),
        # тогда как channelName у этого DSS одинаковый ("Door1") для всех.
        door_name = (
            raw.get("deviceName")
            or raw.get("pointName")
            or raw.get("doorName")
            or raw.get("channelName")
        )

        direction = _extract_direction(raw)

        ts_raw = raw.get("alarmTime") or raw.get("time") or raw.get("eventTime")
        occurred_at = _parse_ts(ts_raw)

        snapshot = (
            raw.get("captureImageUrl")
            or raw.get("imageUrl")
            or raw.get("snapshotUrl")
            or raw.get("picUrl")
        )

        return Event(
            dss_event_id=evt_id,
            event_type=evt_type,
            event_name=evt_name,
            person_id=person_id,
            person_name=person_name if person_name else None,
            door_id=door_id,
            door_name=door_name if door_name else None,
            direction=direction,
            occurred_at=occurred_at,
            snapshot_url=snapshot,
            raw=raw,
        )
    except Exception as e:
        logger.warning("parse_event fail: {} for raw={}", e, raw)
        return None


def _extract_direction(data: dict) -> str | None:
    # 1) Явные поля направления (старые версии DSS / другие сборки).
    v = data.get("inOutType") or data.get("direction") or data.get("InOutType")
    if v is not None:
        if isinstance(v, int):
            return "in" if v == 1 else "out" if v == 2 else None
        s = str(v).lower()
        if "in" in s or s == "1" or "enter" in s:
            return "in"
        if "out" in s or s == "2" or "exit" in s:
            return "out"

    # 2) alarmTypeName иногда содержит "Enter"/"Exit" (англоязычные сборки V8).
    name = str(data.get("alarmTypeName") or "").lower()
    if "enter" in name or name.endswith(" in"):
        return "in"
    if "exit" in name or name.endswith(" out"):
        return "out"

    # 3) deviceName — конвенция текущей школы: турникеты названы
    # "КП1 Вход турникет ..." / "КП2 Выход турникет ...". Это и есть
    # единственный надёжный источник направления для нашего DSS.
    device = str(data.get("deviceName") or data.get("channelName") or "").lower()
    if "вход" in device or "enter" in device:
        return "in"
    if "выход" in device or "exit" in device:
        return "out"

    return None


def _parse_ts(raw) -> datetime:
    """Возвращает datetime в локальной TZ (Душанбе по умолчанию).

    DSS V8 присылает alarmTime как unix-seconds (UTC-инстант) — преобразуем
    к локальному представлению. Строки без TZ считаются уже локальными,
    т.к. DSS-сервер настроен на местное время.
    """
    if raw is None:
        return datetime.now(LOCAL_TZ)
    if isinstance(raw, (int, float)):
        if raw > 10**12:
            return datetime.fromtimestamp(raw / 1000, tz=LOCAL_TZ)
        return datetime.fromtimestamp(raw, tz=LOCAL_TZ)
    s = str(raw).strip()
    if s.isdigit():
        n = int(s)
        if n > 10**12:
            return datetime.fromtimestamp(n / 1000, tz=LOCAL_TZ)
        return datetime.fromtimestamp(n, tz=LOCAL_TZ)
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S%z",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=LOCAL_TZ)
            else:
                dt = dt.astimezone(LOCAL_TZ)
            return dt
        except ValueError:
            continue
    return datetime.now(LOCAL_TZ)
