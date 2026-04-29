"""Форматирование сообщений для Telegram (HTML). Локализация — RU/EN/TJ."""
from __future__ import annotations

from datetime import datetime, time
from html import escape
from typing import Any, Mapping

from .i18n import EVENT_TYPE_KEY, fmt_dt, fmt_dt_short, fmt_minutes, t

IMPORTANCE_ICON = {0: "•", 1: "⚠️", 2: "🚨"}


def _event_label_html(row: Mapping[str, Any], lang: str) -> str:
    """Возвращает уже escaped HTML-готовое название события.

    Стратегия: сначала пытаемся перевести по коду (`EVENT_TYPE_KEY`) —
    это даёт локализованный текст и игнорирует сырое русское имя из DSS.
    Если код неизвестен и `event_name` нетривиален — используем его
    (DSS-сторона как источник истины для нестандартных событий).
    """
    code = str(row.get("event_type") or "")
    key = EVENT_TYPE_KEY.get(code)
    if key:
        return escape(t(key, lang))
    name = row.get("event_name")
    if name and not str(name).isdigit() and name != row.get("event_type"):
        return escape(str(name))
    if code:
        return escape(t("event_generic", lang, code=code))
    return escape(t("event_fallback", lang))


def _occurred_time(value: Any) -> time | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.time()
    try:
        return datetime.fromisoformat(str(value)).time()
    except ValueError:
        return None


def _format_work_status(
    row: Mapping[str, Any],
    work_day_start: time | None,
    work_day_end: time | None,
    lang: str,
) -> str:
    if work_day_start is None or work_day_end is None:
        return ""
    direction = row.get("direction")
    if direction not in ("in", "out"):
        return ""
    tt = _occurred_time(row.get("occurred_at"))
    if tt is None:
        return ""
    if direction == "in" and tt > work_day_start:
        diff = _to_minutes(tt) - _to_minutes(work_day_start)
        return t("late_by", lang, v=fmt_minutes(diff, lang))
    if direction == "out" and tt < work_day_end:
        diff = _to_minutes(work_day_end) - _to_minutes(tt)
        return t("left_early", lang, v=fmt_minutes(diff, lang))
    return ""


def format_event(
    row: Mapping[str, Any],
    work_day_start: time | None = None,
    work_day_end: time | None = None,
    lang: str = "ru",
) -> str:
    icon = IMPORTANCE_ICON.get(int(row["importance"] or 0), "•")
    person = escape(row["person_name"] or t("unknown_person", lang))
    door = escape(row["door_name"] or t("dash", lang))
    direction_raw = row["direction"]
    direction = {
        "in": t("dir_in", lang),
        "out": t("dir_out", lang),
    }.get(direction_raw, direction_raw or t("dash", lang))
    name = _event_label_html(row, lang)  # уже escaped
    when = fmt_dt(row["occurred_at"], lang)
    lines = [
        f"{icon} <b>{name}</b>",
        f"👤 {person}",
        f"🚪 {door} ({direction})",
        f"🕒 {when}",
    ]
    status = _format_work_status(row, work_day_start, work_day_end, lang)
    if status:
        lines.append(status)
    return "\n".join(lines)


def format_inside_list(
    rows: list[Mapping[str, Any]], limit: int = 50, lang: str = "ru"
) -> str:
    if not rows:
        return t("inside_empty", lang)
    head = t("inside_head", lang, n=len(rows))
    body = []
    dash = t("dash", lang)
    for r in rows[:limit]:
        body.append(
            f"• {escape(r['person_name'] or dash)} "
            f"({fmt_dt(r['entered_at'], lang)}, {escape(r['last_door'] or dash)})"
        )
    tail = t("and_more", lang, n=len(rows) - limit) if len(rows) > limit else ""
    return head + "\n".join(body) + tail


def format_find_results(
    name: str, rows: list[Mapping[str, Any]], lang: str = "ru"
) -> str:
    if not rows:
        return t("find_empty", lang, q=escape(name))
    head = t("find_head", lang, q=escape(name))
    dash = t("dash", lang)
    body = []
    for r in rows:
        direction = {
            "in": t("dir_in", lang),
            "out": t("dir_out", lang),
        }.get(r["direction"], dash)
        body.append(
            f"• {fmt_dt(r['occurred_at'], lang)} — "
            f"{escape(r['door_name'] or dash)} ({direction})"
        )
    return head + "\n".join(body)


def format_door_events(
    door: str, rows: list[Mapping[str, Any]], lang: str = "ru"
) -> str:
    if not rows:
        return t("door_empty", lang, d=escape(door))
    head = t("door_head", lang, d=escape(door))
    body = []
    dash = t("dash", lang)
    for r in rows:
        direction = {"in": "→", "out": "←"}.get(r["direction"], "·")
        body.append(
            f"{fmt_dt(r['occurred_at'], lang)} {direction} "
            f"{escape(r['person_name'] or dash)}"
        )
    return head + "\n".join(body)


def format_today(stats: dict, inside: int, lang: str = "ru") -> str:
    return (
        t("today_head", lang) + "\n"
        + t("today_total", lang, n=stats["total"]) + "\n"
        + t("today_ins", lang, n=stats["ins"]) + "\n"
        + t("today_outs", lang, n=stats["outs"]) + "\n"
        + t("today_inside", lang, n=inside)
    )


def _to_minutes(t_: time) -> int:
    return t_.hour * 60 + t_.minute


def _parse_iso(s: Any) -> datetime | None:
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    try:
        return datetime.fromisoformat(str(s))
    except ValueError:
        return None


def format_attendance(
    rows: list[Mapping[str, Any]],
    work_start: time,
    work_end: time,
    limit: int = 50,
    lang: str = "ru",
) -> str:
    if not rows:
        return t("att_empty", lang)

    work_start_min = _to_minutes(work_start)
    work_end_min = _to_minutes(work_end)
    dash = t("dash", lang)
    still_str = t("still_at_school", lang)

    deviations: list[tuple[int, str]] = []
    on_time_count = 0
    still_in_count = 0
    after_hours_count = 0
    total_people = len(rows)

    for r in rows:
        name = r["person_name"] or dash
        first_in = _parse_iso(r["first_in"])
        last_out = _parse_iso(r["last_out"])

        if first_in is None:
            continue

        in_min = _to_minutes(first_in.time())
        out_min = _to_minutes(last_out.time()) if last_out else None
        in_str = first_in.strftime("%H:%M")
        out_str = last_out.strftime("%H:%M") if last_out else still_str

        if in_min >= work_end_min:
            after_hours_count += 1
            continue

        if in_min > work_start_min:
            late = in_min - work_start_min
            line = (
                f"• <b>{escape(name)}</b>\n"
                f"   {in_str} → {out_str}\n"
                f"   " + t("att_late_line", lang, v=fmt_minutes(late, lang))
            )
            deviations.append((late, line))
            continue

        if out_min is None:
            still_in_count += 1
            continue
        if out_min < work_end_min:
            early = work_end_min - out_min
            line = (
                f"• <b>{escape(name)}</b>\n"
                f"   {in_str} → {out_str}\n"
                f"   " + t("att_early_line", lang, v=fmt_minutes(early, lang))
            )
            deviations.append((early, line))
            continue

        on_time_count += 1

    deviations.sort(key=lambda x: x[0], reverse=True)

    summary_parts = [
        t("att_total", lang, n=total_people),
        t("att_on_time", lang, n=on_time_count),
        t("att_still_in", lang, n=still_in_count),
    ]
    if after_hours_count:
        summary_parts.append(t("att_after", lang, n=after_hours_count))

    head = (
        t("att_head", lang) + "\n"
        + t("att_norm", lang, a=work_start.strftime("%H:%M"),
            b=work_end.strftime("%H:%M")) + "\n"
        + " · ".join(summary_parts) + "\n"
    )

    if not deviations:
        return head + "\n" + t("att_no_dev", lang)

    body = "\n\n".join(line for _, line in deviations[:limit])
    tail = ""
    if len(deviations) > limit:
        tail = t("att_dev_more", lang, n=len(deviations) - limit)

    return head + "\n" + body + tail


def format_late(
    rows: list[Mapping[str, Any]],
    work_start: time,
    work_end: time,
    limit: int = 100,
    lang: str = "ru",
) -> str:
    if not rows:
        return t("late_empty_today", lang)

    work_start_min = _to_minutes(work_start)
    work_end_min = _to_minutes(work_end)
    still_str = t("still_at_school", lang)
    dash = t("dash", lang)
    late_list: list[tuple[int, str]] = []

    for r in rows:
        first_in = _parse_iso(r["first_in"])
        if first_in is None:
            continue
        in_min = _to_minutes(first_in.time())
        if in_min <= work_start_min or in_min >= work_end_min:
            continue
        late = in_min - work_start_min
        name = r["person_name"] or dash
        last_out = _parse_iso(r["last_out"])
        out_str = last_out.strftime("%H:%M") if last_out else still_str
        line = (
            f"• <b>{escape(name)}</b>\n"
            f"   {first_in.strftime('%H:%M')} → {out_str}\n"
            f"   " + t("att_late_line", lang, v=fmt_minutes(late, lang))
        )
        late_list.append((late, line))

    late_list.sort(key=lambda x: x[0], reverse=True)

    head = (
        t("late_head", lang) + "\n"
        + t("late_norm", lang, a=work_start.strftime("%H:%M")) + "\n"
        + t("late_total", lang, n=len(late_list)) + "\n"
    )
    if not late_list:
        return head + "\n" + t("late_none", lang)
    body = "\n\n".join(line for _, line in late_list[:limit])
    tail = ""
    if len(late_list) > limit:
        tail = t("and_more", lang, n=len(late_list) - limit)
    return head + "\n" + body + tail


def format_absent(
    rows: list[Mapping[str, Any]], limit: int = 100, lang: str = "ru"
) -> str:
    if not rows:
        return t("absent_empty", lang)
    head = t("absent_head", lang, n=len(rows))
    body = []
    dash = t("dash", lang)
    for r in rows[:limit]:
        name = escape(r["person_name"] or dash)
        when = fmt_dt_short(r["last_seen"], lang) if r.get("last_seen") else dash
        body.append(f"• {name}\n   " + t("absent_last", lang, when=when))
    tail = ""
    if len(rows) > limit:
        tail = t("and_more", lang, n=len(rows) - limit)
    return head + "\n" + "\n\n".join(body) + tail


def format_workers(
    rows: list[Mapping[str, Any]], limit: int = 100, lang: str = "ru"
) -> str:
    if not rows:
        return t("workers_empty", lang)
    head = t("workers_head", lang, n=len(rows))
    body = []
    dash = t("dash", lang)
    for r in rows[:limit]:
        name = escape(r["person_name"] or dash)
        when = fmt_dt_short(r["last_seen"], lang) if r.get("last_seen") else dash
        passes = r.get("total_passes") or "?"
        body.append(f"• {name}\n   " + t("workers_line", lang, p=passes, when=when))
    tail = ""
    if len(rows) > limit:
        tail = t("and_more", lang, n=len(rows) - limit)
    return head + "\n" + "\n\n".join(body) + tail


def format_health(
    uptime: str, last_event_id: int | None, dss_ok: bool, lang: str = "ru"
) -> str:
    dash = t("dash", lang)
    return (
        t("health_head", lang) + "\n"
        + t("health_uptime", lang, v=uptime) + "\n"
        + t("health_dss", lang,
            v=t("health_dss_ok", lang) if dss_ok else t("health_dss_down", lang))
        + "\n"
        + t("health_last", lang,
            v=last_event_id if last_event_id is not None else dash)
    )


def format_morning_report(stats: dict, late: int, lang: str = "ru") -> str:
    return (
        t("morning_head", lang) + "\n"
        + t("morning_pass", lang, n=stats["ins"]) + "\n"
        + t("morning_late", lang, n=late)
    )


def format_midday_report(inside: int, lang: str = "ru") -> str:
    return t("midday", lang, n=inside)


def format_evening_report(
    stats: dict, still_inside: int, lang: str = "ru"
) -> str:
    return (
        t("evening_head", lang) + "\n"
        + t("evening_out", lang, n=stats["outs"]) + "\n"
        + t("evening_stuck", lang, n=still_inside)
    )
