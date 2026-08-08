from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EventEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str = "internal_event"
    origin: str
    event_type: str
    event: dict[str, Any]
    trace_id: str | None = None
    timestamp: datetime


class EventDeadLetter(BaseModel):
    model_config = ConfigDict(frozen=True)

    origin: str
    failure_stage: str
    event_type: str | None = None
    trace_id: str | None = None
    payload: Any = None
    exception_class: str
    exception_message: str
    timestamp: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = ["EventDeadLetter", "EventEnvelope"]
