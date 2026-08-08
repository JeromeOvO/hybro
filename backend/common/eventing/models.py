from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EventEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str = "internal_event"
    origin: str
    event_type: str
    event: dict[str, Any]
    trace_id: str | None = None
    timestamp: datetime

    @model_validator(mode="before")
    @classmethod
    def hydrate_legacy_timestamp(cls, value: Any) -> Any:
        """Accept pre-timestamp envelopes while always emitting the new wire shape."""
        if not isinstance(value, dict) or value.get("timestamp") is not None:
            return value
        hydrated = dict(value)
        event = hydrated.get("event")
        event_timestamp = event.get("timestamp") if isinstance(event, dict) else None
        hydrated["timestamp"] = event_timestamp or datetime.now(UTC)
        return hydrated


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
