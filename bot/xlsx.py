"""Генерация .xlsx-отчётов через openpyxl. RU/EN/TJ."""
from __future__ import annotations

import io
from datetime import datetime, time
from typing import Any, Iterable, Mapping

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .i18n import t

_HEADER_FONT = Font(bold=True, color="FFFFFF")
_HEADER_FILL = PatternFill("solid", fgColor="2F5496")
_HEADER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _autosize(ws, max_width: int = 40) -> None:
    """Подбирает ширину колонок по содержимому. Учёт многобайтовой кириллицы
    через len(str(...)); для xlsx это норм, чисел не считаем."""
    for col_idx, col in enumerate(ws.columns, start=1):
        longest = 0
        for cell in col:
            v = cell.value
            if v is None:
                continue
            longest = max(longest, len(str(v)))
        ws.column_dimensions[get_column_letter(col_idx)].width = min(
            max(longest + 2, 10), max_width
        )


def _write_headers(ws, headers: list[str]) -> None:
    ws.append(headers)
    for cell in ws[1]:
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = _HEADER_ALIGN
    ws.freeze_panes = "A2"  # шапка зафиксирована при скролле
    ws.row_dimensions[1].height = 22


def _save(wb: Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _parse_iso(s: Any) -> datetime | None:
    """Возвращает naive datetime — Excel не поддерживает tz-aware значения.
    DSS отдаёт `+05:00`, для отчёта важно локальное wall-time, поэтому
    отбрасываем tzinfo без конвертации."""
    if not s:
        return None
    if isinstance(s, datetime):
        return s.replace(tzinfo=None) if s.tzinfo else s
    try:
        dt = datetime.fromisoformat(str(s))
    except ValueError:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def _to_min(t_: time) -> int:
    return t_.hour * 60 + t_.minute


def generate_workers_xlsx(
    rows: Iterable[Mapping[str, Any]],
    lang: str = "ru",
) -> bytes:
    """Лист «Работники»: Имя · Проходов · Последний раз · Дверь."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Workers"  # лист именуется латиницей — лимит Excel 31, без локализации
    headers = [
        t("csv_w_name", lang),
        t("csv_w_passes", lang),
        t("csv_w_last_seen", lang),
        t("csv_w_last_door", lang),
    ]
    _write_headers(ws, headers)
    for r in rows:
        last_seen = _parse_iso(r.get("last_seen"))
        ws.append([
            (r.get("person_name") or "").strip(),
            int(r.get("total_passes") or 0),
            last_seen,  # openpyxl сам отдаст как datetime
            (r.get("last_door") or "").strip(),
        ])
    # Формат даты для столбца C (last_seen)
    for cell in ws["C"][1:]:
        cell.number_format = "yyyy-mm-dd hh:mm:ss"
    _autosize(ws)
    return _save(wb)


def generate_attendance_xlsx(
    rows: Iterable[Mapping[str, Any]],
    work_start: time,
    work_end: time,
    lang: str = "ru",
) -> bytes:
    """Лист «Отчёт»: Дата · Имя · Первый вход · Последний выход ·
    Опоздание · Ранний уход · Проходов · Статус."""
    work_start_min = _to_min(work_start)
    work_end_min = _to_min(work_end)

    wb = Workbook()
    ws = wb.active
    ws.title = "Report"
    headers = [
        t("csv_h_date", lang),
        t("csv_h_name", lang),
        t("csv_h_in", lang),
        t("csv_h_out", lang),
        t("csv_h_late", lang),
        t("csv_h_early", lang),
        t("csv_h_passes", lang),
        t("csv_h_status", lang),
    ]
    _write_headers(ws, headers)

    for r in rows:
        day = r.get("day") or ""
        name = (r.get("person_name") or "").strip()
        first_in = _parse_iso(r.get("first_in"))
        last_out = _parse_iso(r.get("last_out"))
        passes = int(r.get("passes") or 0)

        late_val: int | str = ""
        early_val: int | str = ""
        status = ""

        if first_in is None:
            status = t("csv_st_only_out", lang)
        else:
            in_m = first_in.hour * 60 + first_in.minute
            if in_m >= work_end_min:
                status = t("csv_st_after_hours", lang)
            elif in_m > work_start_min:
                late_val = in_m - work_start_min
                status = t("csv_st_late", lang)
            else:
                if last_out is None:
                    status = t("csv_st_no_out", lang)
                else:
                    out_m = last_out.hour * 60 + last_out.minute
                    if out_m < work_end_min:
                        early_val = work_end_min - out_m
                        status = t("csv_st_early", lang)
                    else:
                        status = t("csv_st_on_time", lang)

        ws.append([
            day,
            name,
            first_in,  # время ячейки — datetime
            last_out,
            late_val,
            early_val,
            passes,
            status,
        ])

    # Форматы времени для колонок C и D
    for col in ("C", "D"):
        for cell in ws[col][1:]:
            if cell.value is not None:
                cell.number_format = "hh:mm:ss"

    _autosize(ws)
    return _save(wb)
