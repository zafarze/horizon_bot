"""Покрытие i18n: словари полны на 3 языка, форматтеры не падают, парсер дат."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from bot import formatters as fm
from bot.i18n import (
    BTN_LABELS,
    DEFAULT_LANG,
    EVENT_TYPE_KEY,
    LANG_PICK_LABEL,
    LANGS,
    T,
    fmt_dt,
    fmt_dt_short,
    fmt_minutes,
    label,
    labels_for,
    normalize_lang,
    parse_date_word,
    t,
)

ALL_LANGS = LANGS  # ('ru', 'en', 'tj')


# === Полнота словарей ===

@pytest.mark.parametrize("key", list(T.keys()))
def test_T_has_all_langs(key: str) -> None:
    for lang in ALL_LANGS:
        assert lang in T[key], f"missing lang '{lang}' for key '{key}'"
        assert T[key][lang], f"empty translation for '{key}' / '{lang}'"


@pytest.mark.parametrize("key", list(BTN_LABELS.keys()))
def test_BTN_LABELS_has_all_langs(key: str) -> None:
    for lang in ALL_LANGS:
        assert lang in BTN_LABELS[key]
        assert BTN_LABELS[key][lang]


def test_LANG_PICK_LABEL_complete() -> None:
    for lang in ALL_LANGS:
        assert LANG_PICK_LABEL[lang]


def test_EVENT_TYPE_KEY_targets_exist() -> None:
    """Все коды DSS должны мапиться на ключ, который реально есть в T."""
    for code, key in EVENT_TYPE_KEY.items():
        assert key in T, f"EVENT_TYPE_KEY['{code}'] -> '{key}' missing in T"


# === Базовые функции ===

def test_normalize_lang_fallbacks() -> None:
    assert normalize_lang("ru") == "ru"
    assert normalize_lang(None) == DEFAULT_LANG
    assert normalize_lang("xx") == DEFAULT_LANG
    assert normalize_lang("") == DEFAULT_LANG


def test_t_format_kwargs() -> None:
    assert t("today_total", "en", n=5) == "Total passes: 5"
    assert "5" in t("today_total", "tj", n=5)


def test_t_unknown_key_returns_key() -> None:
    assert t("nonexistent_key_xyz", "ru") == "nonexistent_key_xyz"


def test_t_fallback_on_unknown_lang() -> None:
    # Неизвестный язык должен фоллбечиться на DEFAULT_LANG (ru).
    assert t("today_head", "xx") == t("today_head", "ru")


def test_label_and_labels_for() -> None:
    for lang in ALL_LANGS:
        assert label("today", lang) == BTN_LABELS["today"][lang]
    assert labels_for("today") == set(BTN_LABELS["today"].values())
    assert len(labels_for("today")) == 3


def test_fmt_minutes() -> None:
    assert fmt_minutes(45, "en") == "45 min"
    assert fmt_minutes(60, "en") == "1 h"
    assert fmt_minutes(125, "en") == "2 h 5 min"
    assert fmt_minutes(45, "ru") == "45 мин"
    assert fmt_minutes(60, "ru") == "1 ч"
    assert fmt_minutes(125, "tj") == "2 соат 5 дақ"


def test_fmt_dt_localized_en_uses_month_abbr() -> None:
    dt = datetime(2026, 4, 28, 9, 15, 3)
    en = fmt_dt(dt, "en")
    assert en.startswith("Apr 28,")
    assert "09:15:03" in en
    ru = fmt_dt(dt, "ru")
    assert ru == "28.04 09:15:03"
    tj = fmt_dt(dt, "tj")
    assert tj == "28.04 09:15:03"


def test_fmt_dt_short_skips_seconds() -> None:
    dt = datetime(2026, 4, 28, 9, 15, 3)
    assert fmt_dt_short(dt, "en") == "Apr 28, 09:15"
    assert fmt_dt_short(dt, "ru") == "28.04 09:15"


def test_fmt_dt_handles_iso_strings_and_none() -> None:
    assert "09:15" in fmt_dt("2026-04-28T09:15:00", "en")
    assert fmt_dt(None, "ru") == "—"


# === Парсер дат ===

def test_parse_date_word_keywords_per_lang() -> None:
    today = date.today()
    assert parse_date_word("today") == today
    assert parse_date_word("сегодня") == today
    assert parse_date_word("имрӯз") == today
    assert parse_date_word("yesterday") == today - timedelta(days=1)
    assert parse_date_word("дирӯз") == today - timedelta(days=1)
    assert parse_date_word("неделя") == today - timedelta(days=7)
    assert parse_date_word("ҳафта") == today - timedelta(days=7)
    assert parse_date_word("month") == today - timedelta(days=30)
    assert parse_date_word("моҳ") == today - timedelta(days=30)


def test_parse_date_word_numeric_formats() -> None:
    assert parse_date_word("01.04.2026") == date(2026, 4, 1)
    assert parse_date_word("2026-04-01") == date(2026, 4, 1)
    today = date.today()
    assert parse_date_word("01.04").year == today.year


def test_parse_date_word_garbage_returns_none() -> None:
    assert parse_date_word("garbage") is None
    assert parse_date_word("") is None


# === Форматтеры — smoke на 3 языках ===

@pytest.mark.parametrize("lang", ALL_LANGS)
def test_formatters_smoke_empty(lang: str) -> None:
    assert fm.format_inside_list([], lang=lang)
    assert fm.format_today({"total": 0, "ins": 0, "outs": 0}, 0, lang=lang)
    assert fm.format_find_results("Q", [], lang=lang)
    assert fm.format_door_events("KP1", [], lang=lang)
    assert fm.format_attendance([], time(8, 0), time(16, 0), lang=lang)
    assert fm.format_late([], time(8, 0), time(16, 0), lang=lang)
    assert fm.format_absent([], lang=lang)
    assert fm.format_workers([], lang=lang)
    assert fm.format_health("1h", 1, True, lang=lang)
    assert fm.format_morning_report({"ins": 0}, 0, lang=lang)
    assert fm.format_midday_report(0, lang=lang)
    assert fm.format_evening_report({"outs": 0}, 0, lang=lang)


@pytest.mark.parametrize("lang", ALL_LANGS)
def test_format_event_uses_code_first(lang: str) -> None:
    """DSS event_name='Доступ разрешён' должен игнорироваться,
    если есть известный код, чтобы английский админ не видел русское имя."""
    row = {
        "importance": 1,
        "person_name": "Test",
        "door_name": "KP1",
        "direction": "in",
        "event_name": "Доступ разрешён",
        "event_type": "AccessControl.PassGranted",
        "occurred_at": datetime(2026, 4, 28, 9, 15, 0),
    }
    out = fm.format_event(row, lang=lang)
    if lang == "en":
        assert "Pass" in out
        assert "Доступ" not in out
    elif lang == "tj":
        assert "Гузаштан" in out


@pytest.mark.parametrize("lang", ALL_LANGS)
def test_format_event_unknown_code_falls_back_to_event_name(lang: str) -> None:
    row = {
        "importance": 0,
        "person_name": "X",
        "door_name": "D",
        "direction": "out",
        "event_name": "CustomDSSName",
        "event_type": "Some.Unknown.Code",
        "occurred_at": datetime(2026, 4, 28, 9, 15, 0),
    }
    out = fm.format_event(row, lang=lang)
    assert "CustomDSSName" in out


def test_format_event_html_escapes_user_data() -> None:
    row = {
        "importance": 0,
        "person_name": "<script>",
        "door_name": "&\"<>",
        "direction": "in",
        "event_name": None,
        "event_type": "600005",
        "occurred_at": datetime(2026, 4, 28, 9, 15, 0),
    }
    out = fm.format_event(row, lang="ru")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "&amp;" in out
