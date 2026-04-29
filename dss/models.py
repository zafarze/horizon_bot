"""Доменные модели DSS-событий."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Event:
    dss_event_id: str
    event_type: str           # код DSS, например "AccessControl.Pass"
    event_name: str           # человекочитаемое имя
    person_id: str | None
    person_name: str | None
    door_id: str | None
    door_name: str | None
    direction: str | None     # "in" | "out" | None
    occurred_at: datetime
    snapshot_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_db_row(self) -> dict[str, Any]:
        return {
            "dss_event_id": self.dss_event_id,
            "event_type": self.event_type,
            "event_name": self.event_name,
            "person_id": self.person_id,
            "person_name": self.person_name,
            "door_id": self.door_id,
            "door_name": self.door_name,
            "direction": self.direction,
            "occurred_at": self.occurred_at.isoformat(),
            "raw": self.raw,
            "snapshot_url": self.snapshot_url,
        }


# --- Коды событий, на которых строится importance-фильтр ---
# Финальные коды нужно сверить с DSS Open API Reference. Ниже — типичные для Dahua DSS.
EVT_FORCED_OPEN = "AccessControl.DoorForcedOpen"
EVT_DOOR_HELD_OPEN = "AccessControl.DoorHeldOpen"
EVT_ANTI_PASSBACK = "AccessControl.AntiPassback"
EVT_FACE_UNKNOWN = "FaceRecognition.Stranger"
EVT_PASS_GRANTED = "AccessControl.PassGranted"
EVT_PASS_DENIED = "AccessControl.PassDenied"
