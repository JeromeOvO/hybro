"""TurnEvent models for the event-sourced turn architecture.

Wire format uses snake_case. Frontend adapter converts to camelCase.
See spec: docs/superpowers/specs/2026-04-11-room-message-area-redesign.md Section 4.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


# --- Phase Payloads ---

class PlanningPhase(BaseModel):
    name: Literal["planning"] = "planning"


class DelegatingPhase(BaseModel):
    name: Literal["delegating"] = "delegating"
    agent_names: list[str]
    count: int


class EvaluatingPhase(BaseModel):
    name: Literal["evaluating"] = "evaluating"


class SynthesizingPhase(BaseModel):
    name: Literal["synthesizing"] = "synthesizing"


class AwaitingInputPhase(BaseModel):
    name: Literal["awaiting_input"] = "awaiting_input"


class RoundPhase(BaseModel):
    name: Literal["round"] = "round"
    current: int
    total: int


class WorkflowStepPhase(BaseModel):
    name: Literal["workflow_step"] = "workflow_step"
    current: int
    total: int
    step_name: str


PhasePayload = (
    PlanningPhase
    | DelegatingPhase
    | EvaluatingPhase
    | SynthesizingPhase
    | AwaitingInputPhase
    | RoundPhase
    | WorkflowStepPhase
)


# --- Event Payloads ---

class TurnStartedPayload(BaseModel):
    user_input: dict[str, Any]


class TurnCompletedPayload(BaseModel):
    duration_ms: int


class TurnFailedPayload(BaseModel):
    reason: str
    code: Literal["rate_limited", "error", "timeout"] | None = None


class TurnCanceledPayload(BaseModel):
    pass


class PhaseChangedPayload(BaseModel):
    phase: PhasePayload


class SlotOpenedPayload(BaseModel):
    slot_id: str
    slot_type: Literal["agent", "summary"]
    agent_id: str | None = None
    agent_name: str | None = None
    mode: Literal["supervisor", "debate"] | None = None


class SlotDeltaPayload(BaseModel):
    slot_id: str
    text_delta: str


class ArtifactAppendedPayload(BaseModel):
    slot_id: str
    artifact: dict[str, Any]


class SlotSnapshotPayload(BaseModel):
    slot_id: str
    content: str
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


class SlotTerminatedPayload(BaseModel):
    slot_id: str
    status: Literal["completed", "failed", "canceled", "rejected"]
    error: str | None = None
    has_partial_content: bool | None = None


class HitlRequestedPayload(BaseModel):
    hitl_id: str
    source: Literal["supervisor", "agent"]
    agent_name: str | None = None
    prompt: str
    prompt_type: Literal["text", "choice", "confirmation"]
    choices: list[str] | None = None
    group_id: str | None = None
    group_total: int | None = None
    group_index: int | None = None


class HitlAnsweredPayload(BaseModel):
    hitl_id: str
    answer: str


class HitlExpiredPayload(BaseModel):
    hitl_id: str


class HitlCanceledPayload(BaseModel):
    hitl_id: str


class HitlErrorPayload(BaseModel):
    hitl_id: str
    error: str


# --- Event type string enum ---

class TurnEventType(StrEnum):
    TURN_STARTED = "turn_started"
    TURN_COMPLETED = "turn_completed"
    TURN_FAILED = "turn_failed"
    TURN_CANCELED = "turn_canceled"
    PHASE_CHANGED = "phase_changed"
    SLOT_OPENED = "slot_opened"
    SLOT_DELTA = "slot_delta"
    ARTIFACT_APPENDED = "artifact_appended"
    SLOT_SNAPSHOT = "slot_snapshot"
    SLOT_TERMINATED = "slot_terminated"
    HITL_REQUESTED = "hitl_requested"
    HITL_ANSWERED = "hitl_answered"
    HITL_EXPIRED = "hitl_expired"
    HITL_CANCELED = "hitl_canceled"
    HITL_ERROR = "hitl_error"


# Map event type to payload class for deserialization
_PAYLOAD_MAP: dict[str, type[BaseModel]] = {
    "turn_started": TurnStartedPayload,
    "turn_completed": TurnCompletedPayload,
    "turn_failed": TurnFailedPayload,
    "turn_canceled": TurnCanceledPayload,
    "phase_changed": PhaseChangedPayload,
    "slot_opened": SlotOpenedPayload,
    "slot_delta": SlotDeltaPayload,
    "artifact_appended": ArtifactAppendedPayload,
    "slot_snapshot": SlotSnapshotPayload,
    "slot_terminated": SlotTerminatedPayload,
    "hitl_requested": HitlRequestedPayload,
    "hitl_answered": HitlAnsweredPayload,
    "hitl_expired": HitlExpiredPayload,
    "hitl_canceled": HitlCanceledPayload,
    "hitl_error": HitlErrorPayload,
}


# --- TurnEvent envelope ---

class TurnEvent(BaseModel):
    event_id: str
    turn_id: str
    seq: int
    ts: int
    type: str
    payload: BaseModel
    client_request_id: str | None = None

    def to_wire(self) -> dict[str, Any]:
        """Serialize to flat snake_case wire format for SSE/API.

        Spec (section 4.1) defines wire format as FLAT:
          { event_id, turn_id, seq, ts, type, user_input, slot_id, ... }
        Payload fields are promoted to top level — no nested "payload" key.

        Persistence format (section 7.5) keeps the nested {"payload": dict}
        shape; to_wire() is used for SSE broadcast and API responses only.
        """
        base = {
            "event_id": self.event_id,
            "turn_id": self.turn_id,
            "seq": self.seq,
            "ts": self.ts,
            "type": self.type,
            **self.payload.model_dump(),  # flatten payload fields to top level
        }
        if self.client_request_id is not None:
            base["client_request_id"] = self.client_request_id
        return base

    @classmethod
    def from_db(cls, doc: dict[str, Any]) -> TurnEvent:
        """Deserialize from MongoDB document."""
        event_type = doc["type"]
        payload_cls = _PAYLOAD_MAP.get(event_type)
        if payload_cls is None:
            raise ValueError(f"Unknown event type: {event_type}")
        payload = payload_cls.model_validate(doc.get("payload", {}))
        return cls(
            event_id=doc["event_id"],
            turn_id=doc["turn_id"],
            seq=doc["seq"],
            ts=doc["ts"],
            type=event_type,
            payload=payload,
            client_request_id=doc.get("client_request_id"),
        )


# --- Turn document status ---

class TurnStatus(StrEnum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
