"""Structured planner context for v2 orchestration run state."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from execution.orchestration.resources import ResourceRef
from models.orchestration import OrchestrationRunState


class MissingRequiredQuoteError(ValueError):
    """Raised when a quoted turn requires a quote that could not be loaded."""


class CandidateAgentContext(BaseModel):
    """Planner-facing view of one sidecar-selected candidate agent."""

    model_config = ConfigDict(frozen=True)

    agent_id: str
    agent_name: str | None = None
    description: str = ""
    capabilities: list[str] = Field(default_factory=list)
    success_rate: float | None = None
    is_healthy: bool | None = None


class CandidateScopeContext(BaseModel):
    """Candidate scope supplied by the orchestration sidecar."""

    model_config = ConfigDict(frozen=True)

    mode: str | None = None
    group_id: str | None = None
    snapshot_version: int | None = None
    agent_ids: list[str] = Field(default_factory=list)
    agents: list[CandidateAgentContext] = Field(default_factory=list)


class ResourceProjectionContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    ref_id: str
    kind: str
    source_ref_id: str
    mime_type: str
    status: str
    recommended_for_input_modes: list[str] = Field(default_factory=list)
    summary: str | None = None
    failure_reason: str | None = None


class ResourceRefContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    ref_id: str
    kind: str
    origin: str
    source_message_id: str | None = None
    source_agent_id: str | None = None
    file_name: str | None = None
    mime_type: str | None = None
    status: str
    summary: str | None = None
    token_estimate: int | None = None
    supported_by_agent_ids: list[str] = Field(default_factory=list)
    projections: list[ResourceProjectionContext] = Field(default_factory=list)


class OrchestrationQuoteContext(BaseModel):
    """Quoted text and provenance supplied by the turn context."""

    model_config = ConfigDict(frozen=True)

    text: str
    quote_id: str | None = None
    sender_display_name: str | None = None
    source_message_id: str | None = None
    source_kind: str | None = None


class OrchestrationRunMetadata(BaseModel):
    """Stable run metadata available to the planner."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    room_id: str
    user_message_id: str
    goal: str
    client_request_id: str | None = None
    schema_version: int
    state_version: int
    status: str
    created_at: Any
    updated_at: Any
    terminal_reason: str | None = None


class OrchestrationStepContext(BaseModel):
    """Current orchestration budget and step position."""

    model_config = ConfigDict(frozen=True)

    steps_used: int
    step_budget: int
    steps_remaining: int
    next_step_number: int


class OrchestrationStateContext(BaseModel):
    """Deterministic snapshot of mutable orchestration run state."""

    model_config = ConfigDict(frozen=True)

    run: OrchestrationRunMetadata
    current_step: OrchestrationStepContext
    current_plan: list[dict[str, Any]] = Field(default_factory=list)
    facts: list[dict[str, Any]] = Field(default_factory=list)
    open_questions: list[dict[str, Any]] = Field(default_factory=list)
    agent_outputs: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    completion_criteria: list[dict[str, Any]] = Field(default_factory=list)
    decision_log: list[dict[str, Any]] = Field(default_factory=list)
    pending_hitl_request_ids: list[str] = Field(default_factory=list)
    summary_intent_id: str | None = None
    summary_message_id: str | None = None


class OrchestrationPlannerContext(BaseModel):
    """Structured input boundary for planner adapters."""

    model_config = ConfigDict(frozen=True)

    message_text: str
    quote: OrchestrationQuoteContext | None = None
    room_background: str | None = None
    candidate_scope: CandidateScopeContext
    state_context: OrchestrationStateContext
    available_resources: list[ResourceRefContext] = Field(default_factory=list)

    @property
    def candidate_agent_ids(self) -> list[str]:
        """Return selected candidate IDs without consulting free-text content."""

        return list(self.candidate_scope.agent_ids)

    def prompt_payload(self) -> dict[str, Any]:
        """Return planner prompt content without embedding validation schemas."""

        return {
            "message": {"text": self.message_text},
            "quote": (
                self.quote.model_dump(mode="json") if self.quote is not None else None
            ),
            "room_background": self.room_background,
            "candidate_scope": self.candidate_scope.model_dump(mode="json"),
            "state_context": self.state_context.model_dump(mode="json"),
            "available_resources": [
                resource.model_dump(mode="json")
                for resource in self.available_resources
            ],
        }


def build_orchestration_planner_context(
    *,
    run_state: OrchestrationRunState,
    candidate_scope: Sequence[Any] | Mapping[str, Any],
    message_text: str,
    quote: Any | None = None,
    quote_required: bool = False,
    room_background: str | None = None,
    available_resources: Sequence[Any] | None = None,
) -> OrchestrationPlannerContext:
    """Build a deterministic planner context from sidecar state and turn data."""

    quote_context = _build_quote_context(quote)
    if quote_required and quote_context is None:
        raise MissingRequiredQuoteError("quote_required=True but no quote was supplied")

    if not isinstance(message_text, str):
        raise TypeError("message_text must be a string")
    if room_background is not None and not isinstance(room_background, str):
        raise TypeError("room_background must be a string when supplied")

    scope_context = _build_candidate_scope_context(candidate_scope)
    state_context = _build_state_context(run_state)

    return OrchestrationPlannerContext(
        message_text=message_text,
        quote=quote_context,
        room_background=room_background,
        candidate_scope=scope_context,
        state_context=state_context,
        available_resources=_build_resource_contexts(available_resources or []),
    )


def _build_resource_contexts(resources: Sequence[Any]) -> list[ResourceRefContext]:
    contexts: list[ResourceRefContext] = []
    for resource in resources:
        if isinstance(resource, ResourceRef):
            raw = resource.model_dump(mode="json")
        elif isinstance(resource, Mapping):
            raw = dict(resource)
        elif hasattr(resource, "model_dump"):
            raw = resource.model_dump(mode="json")
        else:
            continue
        contexts.append(ResourceRefContext.model_validate(raw))
    return contexts


def _build_quote_context(quote: Any | None) -> OrchestrationQuoteContext | None:
    if quote is None:
        return None

    if isinstance(quote, str):
        return OrchestrationQuoteContext(text=quote) if quote != "" else None

    if isinstance(quote, Mapping):
        raw_text = _first_mapping_value(quote, "text", "quoted_text", "quote_text")
        if raw_text is None:
            return None
        if not isinstance(raw_text, str):
            raise TypeError("quote text must be a string")
        return OrchestrationQuoteContext(
            text=raw_text,
            quote_id=_optional_str(_first_mapping_value(quote, "quote_id")),
            sender_display_name=_optional_str(
                _first_mapping_value(
                    quote,
                    "sender_display_name",
                    "quoted_sender_display_name",
                    "quoted_sender_name",
                )
            ),
            source_message_id=_optional_str(
                _first_mapping_value(
                    quote,
                    "source_message_id",
                    "quoted_source_message_id",
                )
            ),
            source_kind=_optional_str(
                _first_mapping_value(quote, "source_kind", "quoted_source_kind")
            ),
        )

    raw_text = _first_attr_value(quote, "text", "quoted_text", "quote_text")
    if raw_text is None:
        return None
    if not isinstance(raw_text, str):
        raise TypeError("quote text must be a string")

    return OrchestrationQuoteContext(
        text=raw_text,
        quote_id=_optional_str(_first_attr_value(quote, "quote_id")),
        sender_display_name=_optional_str(
            _first_attr_value(
                quote,
                "sender_display_name",
                "quoted_sender_display_name",
                "quoted_sender_name",
            )
        ),
        source_message_id=_optional_str(
            _first_attr_value(quote, "source_message_id", "quoted_source_message_id")
        ),
        source_kind=_optional_str(
            _first_attr_value(quote, "source_kind", "quoted_source_kind")
        ),
    )


def _build_candidate_scope_context(
    candidate_scope: Sequence[Any] | Mapping[str, Any],
) -> CandidateScopeContext:
    mode = None
    group_id = None
    snapshot_version = None

    if isinstance(candidate_scope, Mapping):
        mode = _optional_str(
            _first_mapping_value(candidate_scope, "mode", "candidate_scope_mode")
        )
        group_id = _optional_str(
            _first_mapping_value(
                candidate_scope, "group_id", "candidate_scope_group_id"
            )
        )
        snapshot_value = _first_mapping_value(
            candidate_scope,
            "snapshot_version",
            "candidate_scope_snapshot_version",
        )
        snapshot_version = snapshot_value if isinstance(snapshot_value, int) else None
        raw_items = _candidate_items_from_mapping(candidate_scope)
    else:
        raw_items = list(candidate_scope)

    agents: list[CandidateAgentContext] = []
    seen_ids: set[str] = set()
    for raw_item in raw_items:
        agent = _candidate_agent_context(raw_item)
        if agent is None or agent.agent_id in seen_ids:
            continue
        seen_ids.add(agent.agent_id)
        agents.append(agent)

    return CandidateScopeContext(
        mode=mode,
        group_id=group_id,
        snapshot_version=snapshot_version,
        agent_ids=[agent.agent_id for agent in agents],
        agents=agents,
    )


def _candidate_items_from_mapping(candidate_scope: Mapping[str, Any]) -> list[Any]:
    raw_agents = _first_mapping_value(candidate_scope, "agents", "candidate_agents")
    if isinstance(raw_agents, Sequence) and not isinstance(raw_agents, str | bytes):
        return list(raw_agents)

    raw_ids = _first_mapping_value(candidate_scope, "agent_ids", "candidate_agent_ids")
    if isinstance(raw_ids, Sequence) and not isinstance(raw_ids, str | bytes):
        return list(raw_ids)

    if _looks_like_agent_mapping(candidate_scope):
        return [candidate_scope]

    return []


def _candidate_agent_context(raw_item: Any) -> CandidateAgentContext | None:
    if isinstance(raw_item, str):
        agent_id = raw_item.strip()
        if not agent_id:
            return None
        return CandidateAgentContext(agent_id=agent_id)

    if isinstance(raw_item, Mapping):
        agent_id = _optional_str(_first_mapping_value(raw_item, "agent_id", "id"))
        if agent_id is None:
            return None
        return CandidateAgentContext(
            agent_id=agent_id,
            agent_name=_optional_str(
                _first_mapping_value(raw_item, "agent_name", "name")
            ),
            description=_optional_str(_first_mapping_value(raw_item, "description"))
            or "",
            capabilities=_string_list(
                _first_mapping_value(raw_item, "capabilities", "skills")
            ),
            success_rate=_optional_float(
                _first_mapping_value(raw_item, "success_rate")
            ),
            is_healthy=_optional_bool(
                _first_mapping_value(raw_item, "is_healthy", "healthy")
            ),
        )

    agent_id = _optional_str(_first_attr_value(raw_item, "agent_id", "id"))
    if agent_id is None:
        return None

    agent_card = getattr(raw_item, "agent_card", None)
    agent_name = _optional_str(_first_attr_value(raw_item, "agent_name", "name"))
    description = _optional_str(_first_attr_value(raw_item, "description")) or ""
    capabilities = _string_list(_first_attr_value(raw_item, "capabilities", "skills"))
    if agent_card is not None:
        agent_name = agent_name or _optional_str(getattr(agent_card, "name", None))
        description = (
            description or _optional_str(getattr(agent_card, "description", None)) or ""
        )
        capabilities = capabilities or _skills_from_agent_card(agent_card)

    return CandidateAgentContext(
        agent_id=agent_id,
        agent_name=agent_name,
        description=description,
        capabilities=capabilities,
        success_rate=_optional_float(getattr(raw_item, "success_rate", None)),
        is_healthy=_candidate_health(raw_item),
    )


def _build_state_context(run_state: OrchestrationRunState) -> OrchestrationStateContext:
    steps_remaining = max(run_state.step_budget - run_state.steps_used, 0)
    run_metadata = OrchestrationRunMetadata(
        run_id=run_state.run_id,
        room_id=run_state.room_id,
        user_message_id=run_state.user_message_id,
        goal=run_state.goal,
        client_request_id=run_state.client_request_id,
        schema_version=run_state.schema_version,
        state_version=run_state.state_version,
        status=run_state.status.value,
        created_at=run_state.created_at,
        updated_at=run_state.updated_at,
        terminal_reason=run_state.terminal_reason,
    )
    current_step = OrchestrationStepContext(
        steps_used=run_state.steps_used,
        step_budget=run_state.step_budget,
        steps_remaining=steps_remaining,
        next_step_number=run_state.steps_used + 1,
    )

    return OrchestrationStateContext(
        run=run_metadata,
        current_step=current_step,
        current_plan=_stable_model_list(run_state.dispatch_intents),
        facts=_stable_mapping_list(run_state.facts),
        open_questions=_stable_mapping_list(run_state.open_questions),
        agent_outputs=_stable_model_list(run_state.agent_outputs),
        artifacts=_stable_mapping_list(run_state.artifacts),
        completion_criteria=_stable_mapping_list(run_state.completion_criteria),
        decision_log=_stable_mapping_list(run_state.decision_log),
        pending_hitl_request_ids=list(run_state.pending_hitl_request_ids),
        summary_intent_id=run_state.summary_intent_id,
        summary_message_id=run_state.summary_message_id,
    )


def _stable_model_list(values: Iterable[BaseModel]) -> list[dict[str, Any]]:
    return [_stable_data(value.model_dump(mode="json")) for value in values]


def _stable_mapping_list(values: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [_stable_data(value) for value in values]


def _stable_data(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _stable_data(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {
            str(key): _stable_data(value[key])
            for key in sorted(value.keys(), key=lambda item: str(item))
        }
    if isinstance(value, list):
        return [_stable_data(item) for item in value]
    return value


def _looks_like_agent_mapping(value: Mapping[str, Any]) -> bool:
    return any(key in value for key in ("agent_id", "id"))


def _first_mapping_value(mapping: Mapping[str, Any], *keys: str) -> Any | None:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _first_attr_value(value: Any, *attrs: str) -> Any | None:
    for attr in attrs:
        if hasattr(value, attr):
            return getattr(value, attr)
    return None


def _optional_str(value: Any | None) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _optional_float(value: Any | None) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _optional_bool(value: Any | None) -> bool | None:
    return value if isinstance(value, bool) else None


def _string_list(value: Any | None) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item:
            result.append(item)
            continue
        item_id = _optional_str(_first_attr_value(item, "id", "name"))
        if item_id is not None:
            result.append(item_id)
            continue
        if isinstance(item, Mapping):
            mapped_id = _optional_str(_first_mapping_value(item, "id", "name"))
            if mapped_id is not None:
                result.append(mapped_id)
    return result


def _skills_from_agent_card(agent_card: Any) -> list[str]:
    skills = getattr(agent_card, "skills", None)
    return _string_list(skills)


def _candidate_health(raw_item: Any) -> bool | None:
    direct = _optional_bool(_first_attr_value(raw_item, "is_healthy", "healthy"))
    if direct is not None:
        return direct
    status = getattr(raw_item, "agent_status", None)
    status_value = getattr(status, "value", status)
    if isinstance(status_value, str):
        return status_value == "active"
    return None
