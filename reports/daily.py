"""Ежедневные сводки в чат TG_CHAT_REPORTS через APScheduler."""
from __future__ import annotations

from datetime import datetime, timedelta, time as dtime

import pytz
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from db import DB
from bot.formatters import (
    format_evening_report,
    format_midday_report,
    format_morning_report,
)
from bot.lang import LangResolver


def _today_bounds(tz: pytz.BaseTzInfo) -> tuple[datetime, datetime]:
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


async def _morning(bot: Bot, db: DB, resolver: LangResolver, chat_id: int,
                   tz: pytz.BaseTzInfo, late_threshold: dtime) -> None:
    if not chat_id:
        return
    start, end = _today_bounds(tz)
    stats = await db.stats_today(start, end)
    rows = await db.events_between(start, end)
    late = sum(
        1
        for r in rows
        if r["direction"] == "in"
        and _t(r["occurred_at"]) >= late_threshold
        and _t(r["occurred_at"]) < dtime(12, 0)
    )
    lang = await resolver.get(chat_id)
    try:
        await bot.send_message(
            chat_id, format_morning_report(stats, late, lang=lang), parse_mode="HTML"
        )
    except Exception as e:
        logger.warning("morning report failed: {}", e)


async def _midday(bot: Bot, db: DB, resolver: LangResolver, chat_id: int) -> None:
    if not chat_id:
        return
    inside = await db.count_inside()
    lang = await resolver.get(chat_id)
    try:
        await bot.send_message(
            chat_id, format_midday_report(inside, lang=lang), parse_mode="HTML"
        )
    except Exception as e:
        logger.warning("midday report failed: {}", e)


async def _evening(bot: Bot, db: DB, resolver: LangResolver, chat_id: int,
                   tz: pytz.BaseTzInfo) -> None:
    if not chat_id:
        return
    start, end = _today_bounds(tz)
    stats = await db.stats_today(start, end)
    inside = await db.count_inside()
    lang = await resolver.get(chat_id)
    try:
        await bot.send_message(
            chat_id, format_evening_report(stats, inside, lang=lang), parse_mode="HTML"
        )
    except Exception as e:
        logger.warning("evening report failed: {}", e)


def _t(value) -> dtime:
    if isinstance(value, datetime):
        return value.time()
    try:
        return datetime.fromisoformat(str(value)).time()
    except ValueError:
        return dtime(0, 0)


def schedule_reports(
    *,
    bot: Bot,
    db: DB,
    resolver: LangResolver,
    chat_id: int,
    tz_name: str,
    late_threshold: dtime,
) -> AsyncIOScheduler:
    tz = pytz.timezone(tz_name)
    scheduler = AsyncIOScheduler(timezone=tz)
    common = {"bot": bot, "db": db, "resolver": resolver, "chat_id": chat_id}
    scheduler.add_job(
        _morning,
        CronTrigger(hour=8, minute=45, timezone=tz),
        kwargs={**common, "tz": tz, "late_threshold": late_threshold},
        id="morning",
    )
    scheduler.add_job(
        _midday,
        CronTrigger(hour=13, minute=0, timezone=tz),
        kwargs=common,
        id="midday",
    )
    scheduler.add_job(
        _evening,
        CronTrigger(hour=18, minute=0, timezone=tz),
        kwargs={**common, "tz": tz},
        id="evening",
    )
    return scheduler
