"""AgentEvent — transport-agnostic normalized event dataclass.

All three entry points (direct cloud, hub relay, push webhook) normalize
their transport-specific events into ``AgentEvent`` before delegating to
``AgentResponseHandler`` for shared result processing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import InitVar, dataclass
from typing import Literal

from common.dto.hitl import A2AInteractionSpec
from execution.dispatch.a2a_interaction import (
    A2AInteractionDisposition,
    freeze_interaction_metadata,
)


@dataclass(frozen=True, slots=True)
class AgentInputObservation:
    """Private runtime evidence observed on an agent input-required event.

    The raw prompt and transport metadata can contain sensitive or untrusted
    content.  They are retained only for runtime classification and must not be
    copied into public delivery DTOs.
    """

    raw_prompt: str
    interaction_metadata: Mapping[str, object]
    task_id: str
    context_id: str
    observed_state: str
    interaction_spec: A2AInteractionSpec | None = None
    parser_disposition: A2AInteractionDisposition = A2AInteractionDisposition.UNTYPED
    parser_error: str | None = None
    schema_version: Literal[1] = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "interaction_metadata",
            freeze_interaction_metadata(self.interaction_metadata),
        )
        if self.parser_disposition == A2AInteractionDisposition.TYPED:
            if self.interaction_spec is None:
                raise ValueError("typed observation requires interaction_spec")
            if self.parser_error is not None:
                raise ValueError("typed observation cannot include parser_error")
        elif self.interaction_spec is not None:
            raise ValueError("only typed observation may include interaction_spec")
        if self.parser_disposition == A2AInteractionDisposition.INVALID:
            if not self.parser_error:
                raise ValueError("invalid observation requires parser_error")
        elif self.parser_error is not None:
            raise ValueError("only invalid observation may include parser_error")

        for field_name in ("task_id", "context_id"):
            value = getattr(self, field_name)
            normalized = value.strip().casefold()
            if (
                not normalized
                or normalized
                in {
                    "pending",
                    "provisional",
                    "unknown",
                }
                or normalized.startswith(("pending-", "relay-pending-", "provisional-"))
            ):
                raise ValueError(f"{field_name} must be authoritative")


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
    # Explicitly public, human-readable agent output. This remains separate
    # from transport text so structured responses do not accidentally promote
    # arbitrary remote status/history content at the public boundary.
    public_text: str | None = None
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
    artifact_update_id: str | None = None

    # Metadata
    is_final: bool = False
    agent_name: str | None = None
    step_number: int | None = None
    total_steps: int | None = None

    # Flow control
    skip_persist: bool = False
    files_materialized: bool = False
    details: dict[str, object] | str | None = None
    created_at: str | None = None
    emit_processing_status: bool = True
    retry_on_finalization_conflict: bool = False
    finalization_recovery_id: str | None = None
    end_turn: bool = False

    # InitVar keeps private evidence out of dataclasses.asdict(). Public delivery
    # translation must also remain explicit rather than serializing AgentEvent.
    input_observation: InitVar[AgentInputObservation | None] = None

    def __post_init__(
        self,
        input_observation: AgentInputObservation | None,
    ) -> None:
        self._input_observation = input_observation

    @property
    def private_input_observation(self) -> AgentInputObservation | None:
        return self._input_observation
