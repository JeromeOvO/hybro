from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from common.utils.time import utcnow


class RunState(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    AWAITING_INPUT = "awaiting_input"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


TERMINAL_RUN_STATES = {
    RunState.COMPLETED,
    RunState.FAILED,
    RunState.CANCELED,
}

# Persisted `state` values for runs that are not terminal — keep in sync with
# `get_active_runs_by_room_id`, compaction skip-set, and stale-run watchdog.
NON_TERMINAL_RUN_STATE_VALUES: tuple[str, ...] = (
    RunState.QUEUED.value,
    RunState.PROCESSING.value,
    RunState.AWAITING_INPUT.value,
)


class RunEventType(StrEnum):
    RUN_CREATED = "run_created"
    RUN_STARTED = "run_started"
    RUN_AWAITING_INPUT = "run_awaiting_input"
    RUN_RESUMED = "run_resumed"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_CANCELED = "run_canceled"


class TerminalProjectionStep(BaseModel):
    state: str = "pending"
    claim_token: str | None = None
    claim_expires_at: datetime | None = None
    attempts: int = 0
    last_attempt_at: datetime | None = None
    next_attempt_at: datetime | None = None
    completed_at: datetime | None = None
    blocked_at: datetime | None = None
    last_error: str | None = None


class TerminalProjection(BaseModel):
    """Versioned, optional intent for recoverable terminal side effects.

    This is additive so older run documents remain valid without migration.
    The authoritative copy is stored on the terminal ``run_events`` fact.
    """

    version: int = 1
    event_id: str | None = None
    canonical_status: str
    frontend_message_id: str
    lifecycle_message_id: str
    descendant_cleanup_root_id: str | None = None
    client_request_id: str | None = None
    details: dict[str, Any] | None = None
    agents: list[dict[str, Any]] | None = None
    system_message_id: str | None = None
    system_task_status: str | None = None
    completion_kind: str | None = None
    turn_event_type: str | None = None
    turn_event_payload: dict[str, Any] | None = None
    delivery_id: str | None = None
    pending: bool = True
    next_attempt_at: datetime = Field(default_factory=utcnow)
    steps: dict[str, TerminalProjectionStep] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Run(BaseModel):
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    room_id: str
    agent_id: str | None = None
    parent_run_id: str | None = None
    trigger_message_id: str | None = None
    parent_message_id: str | None = None
    client_request_id: str | None = None
    state: RunState = RunState.QUEUED
    seq: int = 0
    error_code: str | None = None
    error_message: str | None = None
    terminal_summary: dict[str, Any] | None = None
    terminal_projection: TerminalProjection | None = None
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utcnow)


class RunEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    room_id: str
    seq: int
    type: RunEventType
    payload: dict = Field(default_factory=dict)
    correlation_id: str | None = None
    causation_id: str | None = None
    terminal_projection: TerminalProjection | None = None
    ts: datetime = Field(default_factory=utcnow)
