"""Фоновая зеркальная синхронизация DSS person-groups → локальная person_groups.

Зачем: фильтр уведомлений по чатам (TG_CHAT_GROUP_FILTERS) читает локальную
таблицу. Без авто-синка завучам пришлось бы помнить /group_add /group_sync
после каждой правки в DSS UI. С синком: добавил человека в DSS-группу
«Primary» — через `interval` секунд бот уже видит его как члена Primary.
"""
from __future__ import annotations

import asyncio
from typing import Iterable

from loguru import logger

from db import DB
from dss.client import DSSClient, DSSSessionConflict
from dss.persons import DSSPersonClient, DSSPersonError

# Холодный старт: пока бот ловит остатки прошлой WEB-сессии на DSS (~70-90с
# таймаут), любой запрос на login возвращает code=2004. Не имеет смысла
# дёргать синк до этого — лишний шум в логах. Пропускаем первые N секунд.
INITIAL_DELAY = 90


# Корневой контейнер DSS, который не нужно синкать как обычную группу
# (содержит всех персон скопом). Имена варьируются между билдами/локалями.
_ROOT_GROUP_NAMES = frozenset({
    "All Persons and Vehicles",
    "All Persons",
    "All Person",
    "All",
})


async def sync_once(
    persons: DSSPersonClient, db: DB, groups: Iterable[str]
) -> dict[str, tuple[int, int, int] | None]:
    """Один проход по всем группам. Возвращает {group: (total, added, removed)}.
    Значение None — группа не найдена в DSS или ошибка (см. логи).

    После прохода по `groups` (с рекурсивным разворачиванием подгрупп) —
    второй «label-only» проход по всем остальным группам DSS. Это нужно,
    чтобы у каждого человека в локальной person_groups был хотя бы один
    отдел — иначе уведомления о его проходах не получают метку 🏷.
    Фильтрация чатов от этого не ломается: TG_CHAT_GROUP_FILTERS работает
    по именам групп, а человек просто оказывается записан в нескольких.
    """
    result: dict[str, tuple[int, int, int] | None] = {}
    configured = list(groups)
    for name in configured:
        try:
            codes = await persons.get_org_codes_recursive(name)
            if not codes:
                logger.warning("group_sync: DSS group {!r} not found, skip", name)
                result[name] = None
                continue
            # Собираем persons из всех подгрупп и дедуплицируем.
            ids_set: set[str] = set()
            for code in codes:
                for pid in await persons.list_persons_in_group(code):
                    ids_set.add(pid)
            ids = list(ids_set)
            added, removed = await db.replace_group_members(name, ids)
            logger.info(
                "group_sync {}: total={} (+{} -{}) [from {} subgroups]",
                name, len(ids), added, removed, len(codes),
            )
            result[name] = (len(ids), added, removed)
        except DSSPersonError as e:
            logger.warning("group_sync {}: {}", name, e)
            result[name] = None
        except DSSSessionConflict as e:
            # Транзиентно: DSS ещё держит старую сессию того же user/clientType.
            # На следующей итерации обычно проходит. Без traceback — это шум.
            logger.warning("group_sync {}: DSS session conflict, retry next tick", name)
            result[name] = None
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("group_sync {} crashed: {}", name, e)
            result[name] = None

    # --- Label-only проход: остальные группы DSS-дерева ---
    # Чтобы у каждого человека была хотя бы одна метка отдела в уведомлениях.
    try:
        all_groups = await persons.list_groups(force=False)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("group_sync label-only: list_groups failed: {}", e)
        return result

    configured_set = set(configured)
    for grp in all_groups:
        name = grp.get("name") if isinstance(grp, dict) else None
        org_code = grp.get("orgCode") if isinstance(grp, dict) else None
        if not name or not org_code:
            continue
        if name in configured_set:
            continue  # уже синкнут рекурсивно
        if name in _ROOT_GROUP_NAMES:
            continue
        try:
            ids = await persons.list_persons_in_group(org_code)
        except DSSPersonError as e:
            logger.warning("group_sync(label) {}: {}", name, e)
            continue
        except DSSSessionConflict:
            logger.warning("group_sync(label) {}: session conflict, skip", name)
            continue
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("group_sync(label) {} crashed: {}", name, e)
            continue
        if not ids:
            # Пусто — может быть «контейнер» (только подгруппы внутри),
            # либо реально пустая группа. Не зеркалируем — иначе при
            # любом сбое DSS можем стереть локальные данные.
            continue
        try:
            added, removed = await db.replace_group_members(name, ids)
            logger.info(
                "group_sync(label) {}: total={} (+{} -{})",
                name, len(ids), added, removed,
            )
        except Exception as e:
            logger.warning("group_sync(label) db {}: {}", name, e)

    return result


async def auto_sync_loop(
    dss: DSSClient, db: DB, groups: list[str], interval: int
) -> None:
    """Бесконечный цикл: первый прогон сразу, далее каждые `interval` секунд.

    Минимальный интервал — 30с (защита от случайной DDoS DSS из-за опечатки
    в .env). Цикл не падает: любые ошибки логируются и через паузу пробуем
    снова, как и dss_loop.
    """
    if not groups:
        logger.info("auto_sync_loop: groups list is empty, loop not started")
        return
    persons = DSSPersonClient(dss)
    pause = max(int(interval), 30)
    logger.info(
        "auto_sync_loop: groups={} interval={}s, first run in {}s",
        ",".join(groups), pause, INITIAL_DELAY,
    )
    try:
        await asyncio.sleep(INITIAL_DELAY)
    except asyncio.CancelledError:
        raise
    while True:
        try:
            await sync_once(persons, db, groups)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("auto_sync_loop iteration error: {}", e)
        try:
            await asyncio.sleep(pause)
        except asyncio.CancelledError:
            raise
