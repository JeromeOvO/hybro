"""Provider-neutral and durable orchestrator contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    """Strict base for persisted orchestrator contracts."""

    model_config = ConfigDict(extra="forbid")


class ResolvedModelSnapshot(ContractModel):
    route: str
    provider: Literal["openai", "deepseek"]
    model_id: str
    api: Literal["chat_completions"]
    supports_native_tools: bool
    supports_provider_strict_schema: bool
    supports_local_structured_action: bool
    structured_action_validation: Literal["provider_strict", "local", "unsupported"]
    tool_strategy: Literal["native", "structured_action"]
    context_window: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    temperature: float | None
    provider_timeout_seconds: float = Field(gt=0)
    max_provider_retries: int = Field(ge=0)
    supported_thinking_levels: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _selected_strategy_is_supported(self) -> ResolvedModelSnapshot:
        if self.tool_strategy == "native" and not self.supports_native_tools:
            raise ValueError("native tool strategy requires native tool capability")
        if self.tool_strategy == "structured_action":
            if self.structured_action_validation == "unsupported":
                raise ValueError(
                    "structured-action strategy requires a validation capability"
                )
            if (
                self.structured_action_validation == "provider_strict"
                and not self.supports_provider_strict_schema
            ):
                raise ValueError(
                    "provider-strict strategy requires provider strict schema capability"
                )
            if (
                self.structured_action_validation == "local"
                and not self.supports_local_structured_action
            ):
                raise ValueError(
                    "local structured-action strategy requires local validation capability"
                )
        elif self.structured_action_validation != "unsupported":
            raise ValueError("native tool strategy cannot select structured validation")
        return self


class PromptSnapshot(ContractModel):
    prompt_id: str
    version: str
    content_digest: str
    rendered_system_prompt: str


class OrchestratorProfile(ContractModel):
    profile_id: Literal["fast", "ultimate"]
    model: ResolvedModelSnapshot
    prompt: PromptSnapshot
    thinking_level: str | None = None

    max_model_turns: int = Field(gt=0)
    grace_model_turns: int = Field(ge=0)
    max_agent_calls: int = Field(gt=0)
    max_parallel_calls: int = Field(gt=0)
    max_transport_retries_per_call: int = Field(ge=0)
    max_provider_retries_total: int = Field(default=4, ge=0)
    max_input_tokens_total: int | None = Field(default=None, gt=0)
    max_output_tokens_total: int | None = Field(default=None, gt=0)
    max_compactions: int = Field(ge=0)
    deadline_seconds: float = Field(gt=0)

    initial_routing: Literal["explicit_agent_first", "model_select"]
    tool_execution: Literal["sequential", "parallel"]
    finalization: Literal["pass_through", "light", "synthesize"]

    @model_validator(mode="after")
    def _parallelism_fits_call_budget(self) -> OrchestratorProfile:
        if self.max_parallel_calls > self.max_agent_calls:
            raise ValueError("max_parallel_calls cannot exceed max_agent_calls")
        if (
            self.thinking_level is not None
            and self.thinking_level not in self.model.supported_thinking_levels
        ):
            raise ValueError("thinking_level is unsupported by the frozen model route")
        return self


class TextPart(ContractModel):
    kind: Literal["text"] = "text"
    text: str


class DataPart(ContractModel):
    kind: Literal["data"] = "data"
    data: dict[str, object] | list[object]
    mime_type: str = "application/json"


class ArtifactRefPart(ContractModel):
    kind: Literal["artifact_ref"] = "artifact_ref"
    artifact_ref: str
    mime_type: str | None = None


ContentPart = Annotated[
    TextPart | DataPart | ArtifactRefPart, Field(discriminator="kind")
]


class UsageRecord(ContractModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)


class ToolDefinition(ContractModel):
    name: str
    label: str
    description: str
    input_schema: dict[str, object]
    execution_mode: Literal["sequential", "parallel"]
    side_effect_level: Literal["read", "write", "external"]


class ToolCall(ContractModel):
    call_id: str
    tool_name: str
    arguments: dict[str, object]


TOOL_RESULT_STATUSES = frozenset(
    {"completed", "failed", "canceled", "rejected", "expired"}
)
ToolResultStatus = Literal["completed", "failed", "canceled", "rejected", "expired"]


class ToolResult(ContractModel):
    call_id: str
    tool_name: str
    status: ToolResultStatus
    content: list[ContentPart]
    artifact_refs: list[str]
    error_code: str | None = None
    error_message: str | None = None


class ToolBindingRef(ContractModel):
    binding_id: str
    binding_digest: str


class ResolvedTool(ContractModel):
    definition: ToolDefinition
    binding: ToolBindingRef


class ToolInvocation(ContractModel):
    invocation_id: str
    run_id: str
    expected_run_version: int = Field(ge=0)
    assistant_message_id: str
    source_index: int = Field(ge=0)
    causation_id: str
    idempotency_key: str
    tool: ResolvedTool
    arguments: dict[str, object]
    deadline_at: datetime


class ToolAcceptance(ContractModel):
    acceptance_id: str
    invocation_id: str
    idempotency_key: str
    accepted_at: datetime


ToolSuspensionStatus = Literal["waiting_external", "input_required", "auth_required"]


class ToolSuspension(ContractModel):
    invocation_id: str
    status: ToolSuspensionStatus
    observation_cursor: str | None = None


ToolExecutionOutcome = ToolResult | ToolSuspension


class ToolObservation(ContractModel):
    observation_id: str
    invocation_id: str
    outcome: ToolExecutionOutcome
    observed_at: datetime


ToolBatchEntryState = Literal[
    "pending",
    "invalid",
    "acceptance_failed",
    "accepted",
    "executing",
    "waiting_external",
    "input_required",
    "auth_required",
    "terminal",
]


class ToolBatchEntry(ContractModel):
    call_id: str
    assistant_message_id: str
    source_index: int = Field(ge=0)
    tool_name: str
    state: ToolBatchEntryState = "pending"
    invocation: ToolInvocation | None = None
    acceptance: ToolAcceptance | None = None
    buffered_terminal_result: ToolResult | None = None
    processed_observation_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _entry_state_is_consistent(self) -> ToolBatchEntry:
        if len(self.processed_observation_ids) != len(
            set(self.processed_observation_ids)
        ):
            raise ValueError("processed observation IDs must be unique")
        if self.state == "terminal" and self.buffered_terminal_result is None:
            raise ValueError("terminal tool entry requires buffered result")
        if self.acceptance is not None and self.invocation is None:
            raise ValueError("tool acceptance requires an invocation")
        return self


class ToolCallBatch(ContractModel):
    assistant_message_id: str
    entries: list[ToolBatchEntry]
    results_flushed: bool = False

    @model_validator(mode="after")
    def _call_ids_are_unique(self) -> ToolCallBatch:
        call_ids = [entry.call_id for entry in self.entries]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("tool batch call IDs must be unique")
        if any(
            entry.assistant_message_id != self.assistant_message_id
            for entry in self.entries
        ):
            raise ValueError("tool batch entries must belong to the assistant message")
        return self


class UserMessage(ContractModel):
    kind: Literal["user"] = "user"
    message_id: str
    content: list[ContentPart]
    created_at: datetime


FinishReason = Literal[
    "stop", "tool_calls", "length", "content_filter", "error", "aborted"
]


class AssistantMessage(ContractModel):
    kind: Literal["assistant"] = "assistant"
    message_id: str
    content: list[ContentPart]
    tool_calls: list[ToolCall]
    finish_reason: FinishReason
    usage: UsageRecord | None
    created_at: datetime


class ToolResultMessage(ContractModel):
    kind: Literal["tool_result"] = "tool_result"
    message_id: str
    call_id: str
    tool_name: str
    status: ToolResultStatus
    content: list[ContentPart]
    artifact_refs: list[str]
    is_error: bool
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime


class SessionNotice(ContractModel):
    kind: Literal["session_notice"] = "session_notice"
    notice_id: str
    code: str
    content: str
    related_call_id: str | None = None
    created_at: datetime


AgentMessage = Annotated[
    UserMessage | AssistantMessage | ToolResultMessage | SessionNotice,
    Field(discriminator="kind"),
]


class ModelTextPart(ContractModel):
    kind: Literal["text"] = "text"
    text: str


class ModelToolCallPart(ContractModel):
    kind: Literal["tool_call"] = "tool_call"
    call_id: str
    tool_name: str
    arguments: dict[str, object]


class ModelToolResultPart(ContractModel):
    kind: Literal["tool_result"] = "tool_result"
    call_id: str
    tool_name: str
    content: list[ModelTextPart]
    is_error: bool


ModelContentPart = Annotated[
    ModelTextPart | ModelToolCallPart | ModelToolResultPart,
    Field(discriminator="kind"),
]


class ModelMessage(ContractModel):
    role: Literal["user", "assistant", "tool"]
    content: list[ModelContentPart]


class ModelTurnRequest(ContractModel):
    turn_id: str
    model: ResolvedModelSnapshot
    system_prompt: str
    messages: list[ModelMessage]
    tools: list[ToolDefinition]
    tool_choice: Literal["auto", "none", "required"] = "auto"
    purpose: Literal["agent_turn", "compaction"] = "agent_turn"
    thinking_level: str | None = None
    remaining_provider_retries: int = Field(default=0, ge=0)
    absolute_deadline_at: datetime | None = None


class ModelTurnResult(ContractModel):
    assistant: AssistantMessage
    provider_request_id: str | None = None


class CompactionResult(ContractModel):
    summary: str
    provider_attempts: int = Field(default=0, ge=0)
    usage: UsageRecord | None = None


class ModelStreamEvent(ContractModel):
    kind: Literal[
        "attempt_started",
        "retry_scheduled",
        "attempt_failed",
        "text_delta",
        "reasoning_delta",
        "tool_call_start",
        "tool_call_arguments_delta",
        "tool_call_end",
        "usage",
        "finish",
        "error",
    ]
    attempt: int | None = Field(default=None, ge=1)
    provider_request_id: str | None = None
    error_class: (
        Literal[
            "authentication",
            "rate_limit",
            "timeout",
            "network",
            "provider_5xx",
            "context_overflow",
            "invalid_request",
            "content_filter",
            "aborted",
            "unknown",
        ]
        | None
    ) = None
    retryable: bool | None = None
    retry_delay_ms: int | None = Field(default=None, ge=0)
    error_code: str | None = None
    error_message: str | None = None
    call_id: str | None = None
    tool_name: str | None = None
    delta: str | None = None
    usage: UsageRecord | None = None
    finish_reason: str | None = None

    @model_validator(mode="after")
    def _retry_events_have_durable_accounting_metadata(self) -> ModelStreamEvent:
        if self.kind == "attempt_started" and self.attempt is None:
            raise ValueError("attempt_started requires an attempt number")
        if self.kind in {"attempt_failed", "retry_scheduled"}:
            missing = [
                name
                for name, value in (
                    ("attempt", self.attempt),
                    ("error_class", self.error_class),
                    ("retryable", self.retryable),
                )
                if value is None
            ]
            if missing:
                raise ValueError(f"{self.kind} requires " + ", ".join(missing))
        if self.kind == "retry_scheduled" and self.retryable is not True:
            raise ValueError("retry_scheduled requires retryable=true")
        if self.kind == "error" and self.error_class is None:
            raise ValueError("error requires error_class")
        return self


class AgentToolInput(ContractModel):
    """Platform-owned arguments for one privately bound Agent tool."""

    task: str = Field(min_length=1, max_length=20_000)
    context_refs: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    attachment_refs: list[str] = Field(default_factory=list)


class PreparedResourceRef(ContractModel):
    ref_id: str
    kind: Literal["context", "artifact", "attachment"]
    source_message_id: str
    mime_type: str | None = None
    size_bytes: int = Field(default=0, ge=0)
    content_digest: str


class RunResourceManifestSnapshot(ContractModel):
    schema_version: Literal[1] = 1
    manifest_id: str
    refs: list[PreparedResourceRef] = Field(default_factory=list)
    content_digest: str


class FrozenToolCatalogEntry(ContractModel):
    definition: ToolDefinition
    binding: ToolBindingRef


class FrozenToolCatalogSnapshot(ContractModel):
    schema_version: Literal[1] = 1
    catalog_id: str
    entries: list[FrozenToolCatalogEntry]
    created_at: datetime

    @model_validator(mode="after")
    def _tool_names_and_bindings_are_unique(self) -> FrozenToolCatalogSnapshot:
        names = [entry.definition.name for entry in self.entries]
        bindings = [entry.binding.binding_id for entry in self.entries]
        if len(names) != len(set(names)):
            raise ValueError("frozen tool names must be unique")
        if len(bindings) != len(set(bindings)):
            raise ValueError("frozen tool binding IDs must be unique")
        return self


class RunRequestSnapshot(ContractModel):
    request_fingerprint: str
    room_epoch: int = Field(ge=1)
    requesting_subject_id: str = Field(min_length=1, max_length=256)
    user_message_id: str
    quoted_message_id: str | None = None
    attachment_refs: list[str] = Field(default_factory=list)


class RecoveryClaim(ContractModel):
    owner_id: str | None = None
    lease_expires_at: datetime | None = None
    next_attempt_at: datetime | None = None


ProjectionIntentStatus = Literal["pending", "claimed", "completed", "blocked"]


class ProjectionIntent(ContractModel):
    intent_id: str
    kind: str
    target: str
    dedupe_key: str
    required: bool
    event_id: str
    event_sequence: int = Field(gt=0)
    causation_id: str
    payload: dict[str, object]
    status: ProjectionIntentStatus
    blocked_reason: str | None = None
    claim_owner: str | None = None
    claim_expires_at: datetime | None = None
    attempt_count: int = Field(default=0, ge=0)
    next_attempt_at: datetime | None = None


class BudgetState(ContractModel):
    model_turns_used: int = Field(default=0, ge=0)
    grace_turns_used: int = Field(default=0, ge=0)
    agent_calls_used: int = Field(default=0, ge=0)
    parallel_calls_active: int = Field(default=0, ge=0)
    provider_retries_used: int = Field(default=0, ge=0)
    provider_attempt_keys: list[str] = Field(default_factory=list)
    usage_by_attempt: dict[str, UsageRecord] = Field(default_factory=dict)
    transport_retries_used: int = Field(default=0, ge=0)
    compactions_used: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    deadline_at: datetime
    wrap_up_requested: bool = False

    @model_validator(mode="after")
    def _attempt_inventory_is_unique(self) -> BudgetState:
        if len(self.provider_attempt_keys) != len(set(self.provider_attempt_keys)):
            raise ValueError("provider attempt keys must be unique")
        if not set(self.usage_by_attempt).issubset(self.provider_attempt_keys):
            raise ValueError("usage ledger keys must identify recorded attempts")
        return self


class AuthorizationBasis(ContractModel):
    kind: Literal[
        "room_member",
        "saved_group_member",
        "explicit_selection",
        "mention",
        "all_active_agents",
    ]
    room_id: str | None = None
    group_id: str | None = None
    selected_by_user_id: str | None = None
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CandidateAgentSnapshot(ContractModel):
    agent_id: str
    name: str | None = None
    role: str | None = None
    capability_summary: str = ""
    status: str | None = None
    source: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    input_modes: list[str] = Field(default_factory=lambda: ["text"])
    output_modes: list[str] = Field(default_factory=list)
    supports_file_upload: bool = False
    success_rate: float | None = None


class CandidateScopeSnapshot(ContractModel):
    snapshot_id: str
    revision: int = Field(default=1, ge=1)
    source: str
    room_id: str
    group_id: str | None = None
    agent_ids: list[str]
    agents: list[CandidateAgentSnapshot] = Field(default_factory=list)
    room_membership_version: str | None = None
    group_version: str | None = None
    resolved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    authorization_basis: AuthorizationBasis | None = None


RunStatus = Literal[
    "queued",
    "running",
    "waiting_external",
    "awaiting_user",
    "finalizing",
    "completed",
    "failed",
    "canceled",
    "budget_exhausted",
]


class OrchestratorRunState(ContractModel):
    schema_version: Literal[5] = 5
    run_id: str
    # Explicit persisted execution-engine ownership, fixed at Run creation and
    # never re-evaluated afterwards (production cutover invariant). Runs owned by
    # the legacy executor are identified by their absence from this store, so
    # this schema only ever persists the "orchestrator" generation today; the
    # discriminator exists so future runtime generations can be routed the same
    # way without guessing from store identity.
    runtime_generation: Literal["orchestrator"] = "orchestrator"
    session_id: str
    room_id: str
    client_request_id: str | None
    request: RunRequestSnapshot
    profile: OrchestratorProfile
    candidate_scope: CandidateScopeSnapshot
    status: RunStatus
    transcript: list[AgentMessage]
    tool_catalog: FrozenToolCatalogSnapshot | None = None
    resource_manifest: RunResourceManifestSnapshot | None = None
    tool_batches: list[ToolCallBatch] = Field(default_factory=list)
    artifact_refs: list[str]
    budget: BudgetState
    compaction_summary: str | None = None
    compaction_baseline_tokens: int | None = Field(default=None, ge=0)
    proposed_final_message_id: str | None
    terminal_reason: str | None
    projection_state: Literal["pending", "settled", "blocked"]
    recovery_claim: RecoveryClaim
    projection_outbox: list[ProjectionIntent]
    processed_command_ids: list[str]
    state_version: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _aggregate_identity_and_uniqueness(self) -> OrchestratorRunState:
        batch_message_ids = [batch.assistant_message_id for batch in self.tool_batches]
        if len(batch_message_ids) != len(set(batch_message_ids)):
            raise ValueError("tool batch assistant message IDs must be unique")
        batch_call_ids = [
            entry.call_id for batch in self.tool_batches for entry in batch.entries
        ]
        inventories = (
            ("tool batch call IDs", batch_call_ids),
            ("artifact refs", self.artifact_refs),
            ("processed command IDs", self.processed_command_ids),
            (
                "projection intent IDs",
                [item.intent_id for item in self.projection_outbox],
            ),
        )
        for label, values in inventories:
            if len(values) != len(set(values)):
                raise ValueError(f"{label} must be unique")
        return self


EVENT_TYPES = frozenset(
    {
        "run_started",
        "turn_started",
        "message_started",
        "message_updated",
        "message_completed",
        "tool_call_accepted",
        "tool_call_updated",
        "tool_call_completed",
        "turn_completed",
        "run_waiting_external",
        "run_awaiting_user",
        "run_finalizing",
        "run_completed",
        "run_failed",
        "run_canceled",
        "run_budget_exhausted",
    }
)
EventType = Literal[
    "run_started",
    "turn_started",
    "message_started",
    "message_updated",
    "message_completed",
    "tool_call_accepted",
    "tool_call_updated",
    "tool_call_completed",
    "turn_completed",
    "run_waiting_external",
    "run_awaiting_user",
    "run_finalizing",
    "run_completed",
    "run_failed",
    "run_canceled",
    "run_budget_exhausted",
]


class OrchestratorEvent(ContractModel):
    schema_version: Literal[2] = 2
    event_id: str
    event_type: EventType
    session_id: str
    run_id: str
    room_id: str
    room_epoch: int = Field(ge=1)
    sequence: int = Field(gt=0)
    state_version: int = Field(ge=0)
    causation_id: str
    correlation_id: str | None = None
    payload: dict[str, object]
    created_at: datetime
