"""Юнит-тесты для importance-фильтра."""
from __future__ import annotations

from datetime import datetime, time, timezone

import pytest

from dss.models import (
    EVT_ANTI_PASSBACK,
    EVT_DOOR_HELD_OPEN,
    EVT_FACE_UNKNOWN,
    EVT_FORCED_OPEN,
    EVT_PASS_DENIED,
    EVT_PASS_GRANTED,
    Event,
)
from pipeline.importance import ImportanceRules, classify


RULES = ImportanceRules(
    day_start=time(8, 0),
    day_end=time(19, 0),
    late_threshold_junior=time(8, 30),
)


def _evt(evt_type: str, hour: int, minute: int = 0, *, direction: str | None = "in") -> Event:
    return Event(
        dss_event_id=f"x-{evt_type}-{hour}-{minute}",
        event_type=evt_type,
        event_name=evt_type,
        person_id="p1",
        person_name="Иван Иванов",
        door_id="d1",
        door_name="КП1",
        direction=direction,
        occurred_at=datetime(2026, 4, 27, hour, minute, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize("evt_type", [EVT_FORCED_OPEN, EVT_DOOR_HELD_OPEN, EVT_ANTI_PASSBACK])
def test_alarm_types_always_2(evt_type):
    assert classify(_evt(evt_type, 10), RULES) == 2


def test_unknown_face_during_work_hours_is_zero_or_late():
    # неизвестное лицо в рабочие часы — обычное (0)
    assert classify(_evt(EVT_FACE_UNKNOWN, 12, 0), RULES) == 0


def test_unknown_face_outside_work_hours_is_alarm():
    assert classify(_evt(EVT_FACE_UNKNOWN, 22, 0), RULES) == 2
    assert classify(_evt(EVT_FACE_UNKNOWN, 5, 0), RULES) == 2


def test_pass_denied_in_hours_is_important():
    assert classify(_evt(EVT_PASS_DENIED, 10), RULES) == 1


def test_pass_denied_off_hours_is_alarm():
    assert classify(_evt(EVT_PASS_DENIED, 23), RULES) == 2


def test_every_pass_granted_is_important():
    # Любой проход (вход/выход, в любое время) уведомляется с фото.
    assert classify(_evt(EVT_PASS_GRANTED, 8, 15), RULES) == 1
    assert classify(_evt(EVT_PASS_GRANTED, 14, 0, direction="out"), RULES) == 1
    assert classify(_evt(EVT_PASS_GRANTED, 8, 45, direction="in"), RULES) == 1


def test_pass_after_day_end_is_important():
    assert classify(_evt(EVT_PASS_GRANTED, 19, 30), RULES) == 1


def test_pass_before_day_start_is_important():
    assert classify(_evt(EVT_PASS_GRANTED, 7, 0), RULES) == 1
