"""Конфигурация из .env. Падает на старте, если обязательные поля отсутствуют."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")


def _required(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val or val.startswith("TODO"):
        raise RuntimeError(f"Required parameter {key} is not set in .env")
    return val


def _optional(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _parse_ids(raw: str) -> list[int]:
    if not raw or raw.startswith("TODO"):
        return []
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def _parse_chat_group_filters(raw: str) -> dict[int, frozenset[str]]:
    """Парсит TG_CHAT_GROUP_FILTERS вида `chat_id:Group1+Group2,chat_id:Group3`.

    Чат, отсутствующий в результате, получает все уведомления (как раньше).
    Несколько групп для одного чата — через `+`. Битые записи игнорируются.
    """
    out: dict[int, frozenset[str]] = {}
    if not raw or raw.startswith("TODO"):
        return out
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        cid_str, groups_str = chunk.split(":", 1)
        try:
            cid = int(cid_str.strip())
        except ValueError:
            continue
        groups = frozenset(g.strip() for g in groups_str.split("+") if g.strip())
        if groups:
            out[cid] = groups
    return out


def _parse_time(raw: str) -> time:
    h, m = raw.split(":")
    return time(int(h), int(m))


@dataclass(frozen=True)
class DSSConfig:
    host: str
    port: int
    user: str
    password: str

    @property
    def base_url(self) -> str:
        return f"{self.host}:{self.port}"


@dataclass(frozen=True)
class TGConfig:
    token: str
    admin_ids: list[int] = field(default_factory=list)
    # Список чатов для real-time уведомлений (можно несколько админов через
    # запятую в TG_CHAT_ALERTS). Пустой список = нотификатор не запускается.
    chat_alerts: list[int] = field(default_factory=list)
    # Список чатов для плановых сводок 08:45/13:00/18:00. Несколько id через
    # запятую в TG_CHAT_REPORTS. Пустой список = планировщик не запускается.
    chat_reports: list[int] = field(default_factory=list)
    # chat_id → разрешённые группы. Чат без записи получает все события.
    # Сообщения о людях без person_id или без членства в указанных группах
    # отфильтрованному чату не отправляются.
    chat_group_filters: dict[int, frozenset[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class AppConfig:
    dss: DSSConfig
    tg: TGConfig
    db_path: Path
    log_level: str
    tz: str
    school_day_start: time
    school_day_end: time
    late_threshold_junior: time
    work_day_start: time
    work_day_end: time
    # Подстроки имён устройств, события с которых пишутся в БД, но НЕ уведомляются.
    # Полезно для шумных или нерелевантных устройств (лифты, тестовые ридеры).
    notify_ignore_device_patterns: tuple[str, ...] = ()
    # DSS person-группы, которые периодически зеркалируются в локальную
    # person_groups (для chat_group_filters). Пусто = синк выключен.
    auto_sync_groups: tuple[str, ...] = ()
    # Интервал авто-синка в секундах. Минимум 30с (защита от опечатки).
    auto_sync_interval: int = 300


def load_config() -> AppConfig:
    return AppConfig(
        dss=DSSConfig(
            host=_optional("DSS_HOST", "https://192.168.30.20"),
            port=int(_optional("DSS_PORT", "443")),
            user=_optional("DSS_USER", "tg_bot"),
            password=_optional("DSS_PASS", "TODO"),
        ),
        tg=TGConfig(
            token=_optional("TG_TOKEN", "TODO"),
            admin_ids=_parse_ids(_optional("TG_ADMIN_IDS", "")),
            chat_alerts=_parse_ids(_optional("TG_CHAT_ALERTS", "")),
            chat_reports=_parse_ids(_optional("TG_CHAT_REPORTS", "")),
            chat_group_filters=_parse_chat_group_filters(
                _optional("TG_CHAT_GROUP_FILTERS", "")
            ),
        ),
        db_path=ROOT / _optional("DB_PATH", "./bot.db").lstrip("./"),
        log_level=_optional("LOG_LEVEL", "INFO"),
        tz=_optional("TZ", "Asia/Dushanbe"),
        school_day_start=_parse_time(_optional("SCHOOL_DAY_START", "08:00")),
        school_day_end=_parse_time(_optional("SCHOOL_DAY_END", "19:00")),
        late_threshold_junior=_parse_time(_optional("LATE_THRESHOLD_JUNIOR", "08:30")),
        work_day_start=_parse_time(_optional("WORK_DAY_START", "07:45")),
        work_day_end=_parse_time(_optional("WORK_DAY_END", "16:00")),
        notify_ignore_device_patterns=tuple(
            p.strip() for p in _optional("NOTIFY_IGNORE_DEVICES", "").split(",")
            if p.strip()
        ),
        auto_sync_groups=tuple(
            g.strip() for g in _optional("DSS_AUTO_SYNC_GROUPS", "").split(",")
            if g.strip()
        ),
        auto_sync_interval=int(_optional("DSS_AUTO_SYNC_INTERVAL", "300") or 300),
    )


def has_telegram_creds(cfg: AppConfig) -> bool:
    return bool(cfg.tg.token) and not cfg.tg.token.startswith("TODO")


def has_dss_creds(cfg: AppConfig) -> bool:
    return bool(cfg.dss.password) and not cfg.dss.password.startswith("TODO")
