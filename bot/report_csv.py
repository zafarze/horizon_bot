"""CSV-отчёт за период для Excel (UTF-8 + BOM, разделитель `;`). RU/EN/TJ."""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, time
from typing import Any, Iterable, Mapping

from .i18n import parse_date_word, t


def parse_date_input(text: str) -> date | None:
    """Парсит пользовательский ввод даты на любом из 3 языков.

    См. bot.i18n.parse_date_word — там же ключевые слова.
    """
    return parse_date_word(text)


def _parse_iso(s: Any) -> datetime | None:
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    try:
        return datetime.fromisoformat(str(s))
    except ValueError:
        return None


def _to_min(t_: time) -> int:
    return t_.hour * 60 + t_.minute


def generate_attendance_csv(
    rows: Iterable[Mapping[str, Any]],
    work_start: time,
    work_end: time,
    lang: str = "ru",
) -> bytes:
    """Возвращает байты CSV (UTF-8 BOM, ;-разделитель).
    Заголовки и статусы локализованы по `lang`.
    """
    work_start_min = _to_min(work_start)
    work_end_min = _to_min(work_end)

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([
        t("csv_h_date", lang),
        t("csv_h_name", lang),
        t("csv_h_in", lang),
        t("csv_h_out", lang),
        t("csv_h_late", lang),
        t("csv_h_early", lang),
        t("csv_h_passes", lang),
        t("csv_h_status", lang),
    ])

    for r in rows:
        day = r.get("day") or ""
        name = (r.get("person_name") or "").strip()
        first_in = _parse_iso(r.get("first_in"))
        last_out = _parse_iso(r.get("last_out"))
        passes = r.get("passes") or ""

        in_str = first_in.strftime("%H:%M:%S") if first_in else ""
        out_str = last_out.strftime("%H:%M:%S") if last_out else ""

        late_min: int | str = ""
        early_min: int | str = ""
        status = ""

        if first_in is None:
            status = t("csv_st_only_out", lang)
        else:
            in_m = first_in.hour * 60 + first_in.minute
            if in_m >= work_end_min:
                status = t("csv_st_after_hours", lang)
            elif in_m > work_start_min:
                late_min = in_m - work_start_min
                status = t("csv_st_late", lang)
            else:
                if last_out is None:
                    status = t("csv_st_no_out", lang)
                else:
                    out_m = last_out.hour * 60 + last_out.minute
                    if out_m < work_end_min:
                        early_min = work_end_min - out_m
                        status = t("csv_st_early", lang)
                    else:
                        status = t("csv_st_on_time", lang)

        writer.writerow([day, name, in_str, out_str, late_min, early_min, passes, status])

    # BOM нужен, чтобы Excel в Windows распознал UTF-8 без шаманства.
    return ("﻿" + buf.getvalue()).encode("utf-8")


def generate_workers_csv(
    rows: Iterable[Mapping[str, Any]],
    lang: str = "ru",
) -> bytes:
    """CSV всех известных людей за окно: Имя · Проходов · Последний раз · Дверь."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([
        t("csv_w_name", lang),
        t("csv_w_passes", lang),
        t("csv_w_last_seen", lang),
        t("csv_w_last_door", lang),
    ])
    for r in rows:
        last_seen = _parse_iso(r.get("last_seen"))
        when = last_seen.strftime("%Y-%m-%d %H:%M:%S") if last_seen else ""
        writer.writerow([
            (r.get("person_name") or "").strip(),
            r.get("total_passes") or 0,
            when,
            (r.get("last_door") or "").strip(),
        ])
    return ("﻿" + buf.getvalue()).encode("utf-8")
