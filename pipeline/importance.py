"""Классификация событий по важности.

Уровни:
  2 — Тревога:    forced_open, door_held_open, anti_passback, неизвестное лицо вне рабочих часов, отказ ночью
  1 — Важное:     любой штатный проход (вход/выход) — уведомляем админа с фото
  0 — Обычное:    неизвестное лицо в рабочее время и прочие шумные коды
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from dss.models import (
    EVT_ANTI_PASSBACK,
    EVT_DOOR_HELD_OPEN,
    EVT_FACE_UNKNOWN,
    EVT_FORCED_OPEN,
    EVT_PASS_DENIED,
    EVT_PASS_GRANTED,
    Event,
)


@dataclass(frozen=True)
class ImportanceRules:
    day_start: time
    day_end: time
    late_threshold_junior: time


ALARM_TYPES = frozenset(
    {EVT_FORCED_OPEN, EVT_DOOR_HELD_OPEN, EVT_ANTI_PASSBACK}
)


def classify(evt: Event, rules: ImportanceRules) -> int:
    """Возвращает 0 / 1 / 2."""
    t = evt.occurred_at.time()
    in_work_hours = rules.day_start <= t < rules.day_end

    # --- Тревоги (2) ---
    if evt.event_type in ALARM_TYPES:
        return 2

    if evt.event_type == EVT_FACE_UNKNOWN and not in_work_hours:
        return 2

    if evt.event_type == EVT_PASS_DENIED:
        # отказ в проходе — всегда хотя бы важно, в нерабочие часы — тревога
        return 2 if not in_work_hours else 1

    # --- Важные (1) ---
    # Любой штатный проход через турникет — уведомляем (вход И выход),
    # чтобы админ видел все перемещения с фото.
    if evt.event_type == EVT_PASS_GRANTED:
        return 1

    if evt.event_type == EVT_FACE_UNKNOWN:
        # неизвестное лицо в нерабочие часы — выше уже отдалось 2;
        # в рабочие — обычное (0), чтобы не шуметь.
        return 0

    # неизвестные коды — пропускаем как обычные
    return 0
