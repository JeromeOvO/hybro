"""AgentEvent — transport-agnostic normalized event dataclass.

All three entry points (direct cloud, hub relay, push webhook) normalize
their transport-specific events into ``AgentEvent`` before delegating to
``AgentResponseHandler`` for shared result processing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class AgentEvent:
    """Transport-agnostic agent event. All three entry points normalize into this."""

    kind: Literal[
        "artifact_update",
        "response",
        "error",
        "canceled",
        "task_submitted",
        "status_update",
        "interactive",
        "processing_status",
    ]

    # Required context
    message_id: str
    room_id: str
    agent_id: str

    # Turn attribution (optional in Phase 0, required in Phase 1a)
    turn_id: str | None = None

    # Content (populated per kind)
    text: str = ""
    state: str | None = None
    parts: list[dict] | None = None
    artifacts: list[dict] | None = None
    task_id: str | None = None
    context_id: str | None = None
    error_text: str | None = None
    related_message_id: str | None = None
    user_id: str | None = None
    client_request_id: str | None = None
    lifecycle_message_id: str | None = None

    # Artifact streaming flags (A2A spec)
    append: bool = False
    last_chunk: bool = False

    # Metadata
    is_final: bool = False
    agent_name: str | None = None
    step_number: int | None = None
    total_steps: int | None = None

    # Flow control
    skip_persist: bool = False
    s3_converted: bool = False
    details: str | None = None
    created_at: str | None = None
