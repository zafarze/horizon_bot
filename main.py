"""Точка входа: запуск aiogram + DSS-цикла + APScheduler."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from time import monotonic

from aiogram import Bot, Dispatcher as TGDispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from loguru import logger

from config import AppConfig, has_dss_creds, has_telegram_creds, load_config
from db import DB
from dss.client import DSSClient, DSSAuthFatal
from dss.events import EventSubscriber
from pipeline.dispatcher import Dispatcher as EventDispatcher
from pipeline.importance import ImportanceRules
from bot.handlers import register_handlers
from bot.lang import LangResolver
from bot.notifier import TelegramNotifier
from reports.daily import schedule_reports

LOGS_DIR = Path(__file__).parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)


def setup_logging(level: str) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:HH:mm:ss}</green> <level>{level:<7}</level> {message}",
    )
    logger.add(
        LOGS_DIR / "bot.log",
        level=level,
        rotation="10 MB",
        retention="14 days",
        encoding="utf-8",
        enqueue=True,
    )


async def dss_loop(
    cfg: AppConfig,
    db: DB,
    notifier: TelegramNotifier | None,
) -> None:
    """DSS-цикл с авто-восстановлением."""
    rules = ImportanceRules(
        day_start=cfg.school_day_start,
        day_end=cfg.school_day_end,
        late_threshold_junior=cfg.late_threshold_junior,
    )
    notify_cb = notifier.notify if notifier else None
    dispatcher = EventDispatcher(
        db=db,
        rules=rules,
        notify=notify_cb,
        notify_ignore_device_patterns=cfg.notify_ignore_device_patterns,
    )

    while True:
        client = DSSClient(cfg.dss.base_url, cfg.dss.user, cfg.dss.password)
        try:
            await client.start()
            await client.login()
            sub = EventSubscriber(client)
            await sub.subscribe()
            await dispatcher.run(sub.stream())
        except asyncio.CancelledError:
            await client.stop()
            raise
        except DSSAuthFatal as e:
            logger.error("DSS auth FATAL: {}", e)
            logger.error(
                "DSS loop stopped. Fix DSS_USER/DSS_PASS in .env and restart the bot. "
                "TG commands continue to work."
            )
            await client.stop()
            return
        except Exception as e:
            logger.exception("DSS loop crashed: {}", e)
        finally:
            await client.stop()
        logger.info("DSS loop reconnect in 5s")
        await asyncio.sleep(5)


async def drain_loop(notifier: TelegramNotifier) -> None:
    while True:
        try:
            await asyncio.sleep(30)
            await notifier.drain_unsent()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("drain_loop: {}", e)


async def heartbeat_loop() -> None:
    """Раз в 60с трогает logs/heartbeat.txt — для watchdog."""
    hb = LOGS_DIR / "heartbeat.txt"
    while True:
        try:
            hb.write_text(str(monotonic()), encoding="utf-8")
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("heartbeat: {}", e)
            await asyncio.sleep(60)


async def main() -> None:
    cfg = load_config()
    setup_logging(cfg.log_level)
    logger.info("=== Bot starting ===")

    db = DB(cfg.db_path)
    await db.connect()
    resolver = LangResolver(db)

    started_at = monotonic()

    if not has_telegram_creds(cfg):
        logger.error("TG_TOKEN is not set in .env - fill it in and restart.")
        await db.close()
        return

    bot = Bot(
        token=cfg.tg.token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    tg_dispatcher = TGDispatcher()
    router = Router()

    # DSS-клиент для /dss_ping и /health (отдельный от dss_loop, чтобы не дёргать рабочий)
    health_dss = DSSClient(cfg.dss.base_url, cfg.dss.user, cfg.dss.password)
    if has_dss_creds(cfg):
        await health_dss.start()

    register_handlers(
        router,
        db=db,
        dss=health_dss,
        resolver=resolver,
        admin_ids=cfg.tg.admin_ids,
        started_at=started_at,
        work_day_start=cfg.work_day_start,
        work_day_end=cfg.work_day_end,
    )
    tg_dispatcher.include_router(router)

    notifier: TelegramNotifier | None = None
    if cfg.tg.chat_alerts:
        notifier = TelegramNotifier(
            bot,
            cfg.tg.chat_alerts,
            db,
            resolver,
            dss=health_dss,
            work_day_start=cfg.work_day_start,
            work_day_end=cfg.work_day_end,
        )

    scheduler = None
    if cfg.tg.chat_reports:
        scheduler = schedule_reports(
            bot=bot,
            db=db,
            resolver=resolver,
            chat_id=cfg.tg.chat_reports,
            tz_name=cfg.tz,
            late_threshold=cfg.late_threshold_junior,
        )
        scheduler.start()
        logger.info("Reports scheduler started (TZ={})", cfg.tz)

    tasks: list[asyncio.Task] = [
        asyncio.create_task(tg_dispatcher.start_polling(bot)),
        asyncio.create_task(heartbeat_loop()),
    ]

    if has_dss_creds(cfg):
        tasks.append(asyncio.create_task(dss_loop(cfg, db, notifier)))
    else:
        logger.warning(
            "DSS_PASS is not set - DSS loop not started. Bot runs in TG-only mode."
        )

    if notifier:
        tasks.append(asyncio.create_task(drain_loop(notifier)))

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        for t in tasks:
            t.cancel()
        if scheduler:
            scheduler.shutdown(wait=False)
        await health_dss.stop()
        await bot.session.close()
        await db.close()
        logger.info("=== Bot stopped ===")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
