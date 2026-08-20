# Orchestrator v3 — Plan 2: Agent Core, Agent Session, and Model Tool Calling

Status: Draft

Scope: Backend agent runtime, LLM gateway, and provider adapters

Implementation sequence: Plan 2 of 5

## 1. Purpose

Plan 1 introduced the provider-neutral Orchestrator v3 contracts under
`execution.orchestrator` without binding them to production. Plan 2 implements a
working Pi-style agent loop on top of those contracts:

```text
User message
  -> model turn
  -> tool calls
  -> tool execution
  -> tool results
  -> next model turn
  -> final answer
```

Plan 2 also extends the real LLM gateway for exactly two credential-backed
providers: the official OpenAI API (`OPENAI_API_KEY`) and official DeepSeek API
(`DEEPSEEK_API_KEY`). Models produce native tool calls or a validated structured-
action fallback. The loop is exercised through the production Tool Call
contracts with an agent-shaped fake runtime. External A2A dispatch, callbacks,
HITL resume, artifacts, and production execution cutover remain Plan 3 and Plan
4 work.

The gateway aligns with pi-ai's separation of provider-neutral messages, stream
events, tools, usage, and provider adapters. Plan 2 does not claim pi-ai provider
coverage or API compatibility.

The architectural objective is to establish these responsibility layers:

```text
llm_gateway provider adapters
  -> execution-owned ModelRuntime adapter
  -> OrchestratorKernel
  -> RoomAgentSession
  -> fake ToolRuntime
```

Plan 2 must prove the complete model/tool feedback loop against OpenAI and
DeepSeek adapter contracts without changing current Fast/Ultimate executor
control flow. Retirement of the currently selectable Gemini gateway capability
is an intentional provider-scope migration.

## 2. Inputs from Plan 1

Plan 2 treats the following merged contracts as authoritative:

- `ResolvedModelSnapshot`
- `PromptSnapshot`
- `OrchestratorProfile`
- `ModelTurnRequest`
- `ModelStreamEvent`
- `ModelTurnResult`
- `ModelRuntime`
- `ToolDefinition`, `ToolCall`, and `ToolResult`
- `AgentMessage` and `ModelMessage`
- `BudgetState`
- `OrchestratorRunState`
- `ModelStreamAssembler`
- AgentCall transitions
- durable terminal-decision and projection-settlement contracts
- provider-neutral conformance harness

A contract may change only when implementation demonstrates missing provider data
or an internal contradiction. Any change requires updating Plan 1 tests and the
architecture document in the same commit.

## 3. Decisions

1. `llm_gateway` remains independent from `execution` and `room`.
2. Gateway-owned turn request/event types represent one provider attempt.
3. `GatewayModelRuntime` lives under `execution.orchestrator` and translates
   between gateway turn events and Plan 1 `ModelStreamEvent` contracts.
4. Provider retries occur in `GatewayModelRuntime`, not invisibly inside the new
   gateway turn API.
5. Plan 2 accepts exactly two provider credentials: `OPENAI_API_KEY` and
   `DEEPSEEK_API_KEY`. It does not add generic OpenAI-compatible endpoints.
6. Gateway contracts follow pi-ai-style provider neutrality, but provider breadth
   and source/API compatibility with pi-ai are not Plan 2 goals.
7. OpenAI uses native tool calling through the official API.
8. DeepSeek uses the official API and defaults to locally validated structured-
   action fallback unless the exact configured route is fixture-proven for
   native tool calling.
9. Gemini, Bedrock, Anthropic, Azure OpenAI, and other providers are deferred.
   Unsupported provider selection fails with a typed routing error.
10. Plan 2 intentionally retires the selectable Gemini gateway adapter,
    registration, model routes, dependency, and provider-specific behavior.
    Gemini-only configuration fails fast with a typed migration error and never
    falls through to OpenAI. Surviving generic API tests remain mandatory.
11. Existing text, structured-generation, embedding, and legacy streaming API
    shapes remain supported for OpenAI and DeepSeek where the provider advertises
    the capability.
12. Plan 1 capability contracts are split into provider-enforced strict schema
    support and local structured-action eligibility; DeepSeek is never falsely
    marked provider-strict.
13. Tool arguments are validated locally against `ToolDefinition.input_schema`
    before execution.
14. Complete calls with invalid tool names/schema arguments become correlated
    `ToolResult` errors. Malformed or truncated calls without an executable
    argument object become typed `SessionNotice` recovery observations with a
    stable synthetic observation ID; neither category is executed.
15. `OrchestratorKernel` is provider-, transport-, database-, Room-, A2A-, and
    UI-agnostic.
16. `RoomAgentSession` is an unbound session/runtime facade in Plan 2. Production
    Room/run persistence and request routing remain unchanged.
17. Plan 2 defines the real generic Tool Call contracts and complete tool loop;
    only the ToolCatalog/ToolRuntime implementation is fake.
18. The fake tools model multi-agent outcomes such as parallel delegation,
    external wait, input-required, cancellation, and failure without importing
    A2A protocol types.
19. A real model-visible Agent tool, A2A dispatch runtime, and Agent Card
    projection belong to Plan 3. Plan 2 does not add `call_agent(agent_id, ...)`.
20. The intended architecture is A2A-aware but Kernel-level A2A-agnostic. No A2A
    adapter imports are permitted in the new kernel, session, context, budget, or
    fake-tool modules.
21. The full private transcript remains lossless. Compaction creates a bounded
    model-context view and does not destructively rewrite the transcript.
22. Model attempt output is buffered within the kernel until an attempt succeeds.
    Partial output from a failed/retried attempt is never projected as durable or
    public assistant output.
23. Fast/Ultimate remain on legacy production execution until Plan 4.
24. The confirmed Plan 1 projection-settlement bug from PR #158 is fixed before
    gateway/kernel work: mandatory terminal batches are identified by their
    required kinds and terminal event identity, never the global maximum outbox
    sequence.

## 4. Goals

Plan 2 must deliver:

- a provider-neutral single-attempt turn API in `llm_gateway`;
- native tool-call adapter for the official OpenAI API;
- official DeepSeek API adapter with locally validated structured-action fallback
  and optional route-proven native tools;
- provider route capability metadata;
- typed error classification;
- visible retry and abort behavior through `ModelStreamEvent`;
- a concrete `GatewayModelRuntime`;
- a functioning `OrchestratorKernel`;
- fake sequential and parallel tools;
- lossless transcript conversion;
- bounded context compilation and non-destructive compaction;
- turn, retry, token, call, concurrency, deadline, and compaction budgets;
- wrap-up and grace-turn behavior;
- an unbound `RoomAgentSession`;
- lifecycle events suitable for later persistence and delivery;
- offline OpenAI/DeepSeek fixtures and provider-neutral conformance tests;
- proof that current production execution is still not bound to v3;
- correction and regression coverage for the confirmed PR #158 settlement edge
  case.

## 5. Non-Goals

Plan 2 does not:

- dispatch a real A2A Agent or implement a real `call_agent` tool;
- create or resume an A2A task/context ID;
- implement A2A ownership persistence;
- handle remote callbacks or webhooks;
- implement silent A2A continuation;
- create production HITL interactions;
- materialize Room attachments or artifacts into Agent calls;
- bind v3 Run/Event Mongo repositories to production;
- publish v3 lifecycle events through production SSE;
- replace `QueueExecutor` or `SupervisorExecutor`;
- change the public API from `mode` to `profile`;
- modify the frontend;
- delete legacy LLM workflow services;
- implement verifier Agents;
- implement production Room-history compaction or retention migration;
- support Gemini, Bedrock, Anthropic, Azure OpenAI, arbitrary OpenAI-compatible
  endpoints, or provider plug-in discovery.

### 5.1 Confirmed Plan 1 follow-up from PR #158

The review comment is valid. `_has_mandatory_terminal_intents()` currently selects
the global maximum `event_sequence` across the whole outbox. A later optional
intent with a higher sequence hides the earlier mandatory terminal batch. All
required terminal intents may be completed while projection settlement remains
incorrectly `pending`.

Plan 2 Stage 0 must:

1. group intents by terminal event identity, at minimum
   `(event_id, event_sequence, causation_id)`;
2. locate the unique group containing the required terminal kinds for the
   durable Run status;
3. verify terminal status payload and final message ID within that group;
4. ignore unrelated later optional intents when checking mandatory inventory;
5. preserve `evaluate_projection_settlement()` across all required intents;
6. add regression tests where a completed terminal batch is followed by a
   higher-sequence optional intent in pending, completed, and blocked states.

The fix belongs in Plan 2 because the package is still production-unbound, but it
must land before Plan 2 builds on settlement behavior.

## 6. Target Runtime Flow

```text
RoomAgentSession.prompt(user message)
  -> append UserMessage to unbound Run state
  -> OrchestratorKernel.run(run)
     -> BudgetPolicy.before_model_turn
     -> ContextCompiler.compile(run transcript)
     -> GatewayModelRuntime.stream_turn(request)
        -> LLMGateway single-attempt provider stream
        -> provider-native/raw turn events
        -> visible attempt/retry/error events
     -> ModelStreamAssembler.build_outcome()
     -> append AssistantMessage
     -> no tool calls: terminal evaluation / final answer
     -> tool calls:
        -> tool lookup, private binding, and JSON Schema validation
        -> idempotent ToolRuntime.accept
        -> CAS-checkpoint ToolCallBatch/acceptance receipts
        -> sequential or bounded-parallel ToolRuntime.execute
        -> terminal batch: source-ordered ToolResultMessage append
        -> suspended batch: stop with no ToolResultMessage
        -> terminal ToolObservation later completes/flushes suspended batch
        -> BudgetPolicy.after_tool_batch
        -> next model turn
```

A provider stream, retry, or fake tool error is an observation in the loop. It is
not converted into the old planner-recovery state.

## 7. Dependency Direction

```text
execution.orchestrator.session
  -> execution.orchestrator.kernel
  -> execution.orchestrator.model_runtime
  -> llm_gateway.turn API
  -> llm_gateway provider adapter
```

The kernel also depends on injected ports and pure policies:

```text
ContextCompiler
BudgetPolicy
ToolRuntime
ToolCatalog
OrchestratorRunStore (in-memory implementation in Plan 2)
ProjectionDriver (in-memory/no-side-effect in Plan 2)
Clock
IDFactory
CancellationSignal
```

Forbidden dependencies:

- `llm_gateway` importing `execution`, `room`, `models.orchestration`, or A2A
  orchestration types;
- `OrchestratorKernel` importing provider SDKs, FastAPI, Mongo, Redis, Room,
  delivery, or A2A adapters;
- context/compaction importing current Supervisor planner or outcome evaluator;
- fake tools importing `execution.dispatch` or `a2a_adapter`;
- production `container.py`, routes, jobs, or stale-recovery code importing or
  constructing the Plan 2 kernel/session.

## 8. LLM Gateway Turn Boundary

### 8.1 Why a new boundary is required

Current gateway streaming yields text strings. OpenAI/DeepSeek tool-call deltas,
finish reasons, reasoning, usage, and request IDs are discarded. The existing
gateway retry loop hides attempt metadata and is incompatible with durable retry
accounting.

The existing API shapes remain for OpenAI/DeepSeek legacy consumers. Plan 2 adds
a separate turn API for agent runtimes and retires the Gemini provider surface
described in Section 10.3.

### 8.2 Gateway-owned types

Add gateway-owned provider-neutral types, for example:

```text
llm_gateway/
  turn_types.py
  error_classification.py
```

These types must not import `execution.orchestrator.models`.

Suggested logical contract:

```python
class GatewayTurnMessage:
    role: user | assistant | tool
    parts: list[GatewayTurnPart]


class GatewayToolDefinition:
    name: str
    description: str
    input_schema: dict


class GatewayTurnRequest:
    provider: str
    model_id: str
    api: str
    system_prompt: str
    messages: list[GatewayTurnMessage]
    tools: list[GatewayToolDefinition]
    tool_choice: auto | none | required
    tool_strategy: native | structured_action
    temperature: float | None
    max_output_tokens: int
    timeout_seconds: float


class GatewayTurnEvent:
    kind: text_delta | reasoning_delta |
          tool_call_start | tool_call_arguments_delta | tool_call_end |
          usage | finish
    provider_request_id: str | None
    tool_index: int | None
    call_id: str | None
    tool_name: str | None
    delta: str | None
    usage: GatewayUsage | None
    finish_reason: str | None
```

This stream represents exactly one provider attempt. It does not retry.

### 8.3 Gateway API

Add an operation equivalent to:

```python
class LLMTurnGateway(Protocol):
    def stream_turn_once(
        self,
        request: GatewayTurnRequest,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[GatewayTurnEvent]: ...
```

`LLMGatewayImpl` resolves the requested provider/model but does not replace the
frozen route with current global defaults. Provider selection, API shape, model
ID, temperature, max output, timeout, and strategy come from the Run snapshot.

The turn method delegates one attempt to the selected provider adapter. Timeout,
retry, and durable attempt accounting belong to `GatewayModelRuntime`.

Provider choice is explicit from the frozen logical route and
`LLM_GATEWAY_GENERATION_PROVIDER`, not inferred from which key happens to be
present. OpenAI and DeepSeek keys may coexist. Missing the
selected provider's key raises a typed configuration/authentication error without
falling back to the other provider. Keys and base credentials never enter the
Run snapshot, transcript, lifecycle events, or logs.

## 9. Provider Capability Registry

Current `ModelInfo.capabilities` is insufficient to reconstruct the Plan 1 model
snapshot. Plan 2 adds route-specific metadata while preserving surviving
OpenAI/DeepSeek service behavior.

Required data:

```text
logical route
provider
model_id
api
native tool support
provider-enforced strict structured-output support
locally validated structured-action eligibility
selected structured-action validation mode
context window
max output tokens
supported thinking levels/mode
default temperature
single-attempt timeout
maximum provider retries
```

Capabilities are model/route-specific, not provider-wide assumptions.

Plan 2 changes the Plan 1 snapshot contract so safety facts are not conflated:

```python
supports_native_tools: bool
supports_provider_strict_schema: bool
supports_local_structured_action: bool
structured_action_validation: Literal[
    "provider_strict",
    "local",
    "unsupported",
]
tool_strategy: Literal["native", "structured_action"]
```

`structured_action` is valid when validation mode is `provider_strict` or
`local`. DeepSeek uses `local`; it must not set provider strictness to true.
Plan 1 models, resolver, persisted-profile validation, fixtures, and tests are
updated together.

A new resolver returns route-specific `ModelRouteConfiguration`, which is
converted by `resolve_model_snapshot()`. Existing `get_model()` and
`supports_capability()` remain for legacy consumers.

Supported Plan 2 API matrix:

| Provider | Credential | `api` value | Strategy |
| --- | --- | --- | --- |
| OpenAI | `OPENAI_API_KEY` | `chat_completions` | native |
| DeepSeek | `DEEPSEEK_API_KEY` | `chat_completions` | structured action/local |

Only the official provider endpoints are accepted in Plan 2. `responses`,
Gemini `generate_content`, Bedrock Converse, and arbitrary OpenAI-compatible base
URLs are not advertised until a later provider adapter is implemented and
fixture-proven. V3 route lookup uses logical route identity only; the legacy
model-ID alias map must not choose capabilities with `setdefault` when two routes
share a model ID.

Invalid combinations fail at profile resolution and persisted deserialization:

- native strategy without verified native-tool support;
- structured action with `unsupported` validation mode;
- `provider_strict` without provider strict capability;
- `local` without local structured-action eligibility;
- unsupported provider/API pair;
- unsupported required thinking mode;
- non-positive context/output/timeout values.

## 10. Provider Implementations

### 10.1 OpenAI

Use native tool calling through the existing async OpenAI client.

Implementation requirements:

- convert normalized user/assistant/tool messages without flattening tool parts;
- pass JSON Schema tools and `tool_choice`;
- enable streamed usage when supported;
- parse text and reasoning deltas separately;
- buffer tool calls by provider call index;
- tolerate initial chunks that omit ID or name;
- emit stable start only once identity is known;
- emit argument deltas in source order;
- emit call end, finish reason, usage, and provider request ID;
- close/cancel SDK streams when the runtime is aborted;
- preserve existing text/structured/legacy-stream APIs.

Tests use recorded/fake SDK chunks and require no network credentials.

### 10.2 DeepSeek

DeepSeek route behavior is model-specific.

Default Plan 2 policy:

- use `structured_action` fallback for current unverified DeepSeek routes;
- keep thinking disabled by default unless the frozen model snapshot explicitly
  enables a supported mode;
- permit native tools only when a route fixture and conformance test prove the
  exact endpoint/model behavior;
- never label prompt + JSON object mode as provider-enforced strict schema.

The structured fallback must validate locally before creating executable tool
calls.

### 10.3 Deferred-provider retirement

Plan 2 intentionally retires the current selectable Gemini surface from
`llm_gateway` rather than maintaining a third provider:

- remove `GeminiProvider` and provider export/registration;
- remove Gemini generation/embedding model routes;
- remove Gemini as a valid selected/default provider;
- remove Gemini model-name settings and active `.env.example` configuration;
- retain deprecated Gemini API-key inputs only as migration sentinels that raise
  typed `unsupported_configured_provider` when no supported provider is selected;
- remove `google-genai` after repository-wide import verification;
- delete Gemini functional gateway fixtures/assertions, while adding a Gemini-
  only configuration rejection test.

A Gemini-only deployment must fail before constructing or calling an OpenAI
adapter; it must never silently enter zero-config/degraded OpenAI mode. When a
valid explicit OpenAI or DeepSeek route is selected, stale Gemini credentials do
not override it. The migration error names the unsupported provider but never
contains credential values.

Stale Bedrock cases used only to demonstrate a generic provider override are also
removed when provider selection becomes the closed `openai | deepseek` set.
Tests of generic routing, error handling, and surviving API shapes are rewritten
against OpenAI/DeepSeek, not deleted. Unrelated non-gateway Gemini code is outside
scope unless the import/dependency inventory proves it is dead.

A test may be deleted only when its provider behavior, contract, configuration,
and implementation are removed in the same change. Failing tests for surviving
behavior must be fixed, never deleted merely to make the suite green.

## 11. Structured-Action Fallback

The fallback returns exactly one discriminated action:

```json
{
  "action": "final",
  "content": "..."
}
```

or:

```json
{
  "action": "tool_calls",
  "calls": [
    {
      "tool_name": "example_tool",
      "arguments": {}
    }
  ]
}
```

Rules:

1. The response is parsed only after complete generation.
2. The top-level action is locally JSON-Schema validated.
3. Every tool name must exist in the current request.
4. Every argument object is validated against that tool's input schema.
5. Stable call IDs are generated deterministically from request/turn position.
6. Complete calls with stable identity and argument objects are normalized even
   when the tool is unknown or its schema is invalid; they receive correlated
   ToolResult errors and are not executed. Malformed, truncated, duplicate-
   identity, or otherwise uncorrelatable calls produce a typed SessionNotice
   observation and are not executed.
7. Partial JSON never creates a tool event.
8. A final action becomes assistant text with `finish_reason=stop`.
9. A tool action is synthesized into normalized tool start/argument/end events
   followed by `finish_reason=tool_calls`.
10. Only a complete response that passes the top-level schema, tool-name lookup,
    and per-tool argument schema may create executable calls; prior content has
    no alternative execution path around these checks.

If Plan 2 uses `jsonschema` directly, it must be declared as a direct backend
dependency rather than relying on a transitive dependency.

### 11.1 Invalid and incomplete call recovery

Plan 2 updates the Plan 1 error contracts so non-executable model output can be
fed back safely:

```python
class SessionNotice:
    notice_id: str
    code: str
    content: str
    related_call_id: str | None


class ToolResultMessage:
    # existing fields
    error_code: str | None
    error_message: str | None
```

`ModelStreamAssemblyError` also carries machine-readable metadata when known:

```text
code
provider_call_id
tool_name
tool_index
raw_arguments_digest
```

Recovery rules:

- A complete parsed call with a stable identity but unknown tool or schema-
  invalid arguments becomes a correlated `ToolResultMessage(is_error=True)`.
- Malformed JSON, truncated provider output, or structured-action failure that
  cannot yield a valid executable argument object does not create a fake
  ToolCall. The partial assistant output is retained only as private audit data,
  and the next model turn receives a bounded `SessionNotice` with a deterministic
  observation ID.
- The notice ID is derived from Run ID, model-turn number, attempt number, and
  tool index/provider call ID where available, making retries idempotent.
- A repeated identical invalid observation is deduplicated and still consumes
  the normal model-turn budget.
- No malformed/truncated call reaches ToolRuntime.

Plan 1 assembler, conformance harness, message models, and tests are updated to
match this distinction.

## 12. GatewayModelRuntime

Add an execution-owned adapter, for example:

```text
execution/orchestrator/model_runtime.py
```

It imports gateway turn types and Plan 1 orchestrator contracts and is the only
translation layer between them.

Plan 2 extends the ephemeral `ModelTurnRequest` with execution bounds computed
from the durable Run budget:

```python
purpose: Literal["agent_turn", "compaction"]
remaining_provider_retries: int
absolute_deadline_at: datetime
```

It also adds cumulative limits to `OrchestratorProfile`:

```python
max_provider_retries_total: int
max_input_tokens_total: int | None
max_output_tokens_total: int | None
```

The initial provider attempt is always allowed when the deadline permits.
Additional retries are limited to the minimum of route-level retry allowance and
remaining cumulative Run retries. Therefore the maximum attempts are
`1 + min(route_retries, remaining_run_retries)`, further bounded by the absolute
deadline. A compaction call consumes provider retry, token, and deadline budget
plus `compactions_used`, but not normal `model_turns_used`.

Responsibilities:

- translate `ModelTurnRequest` into `GatewayTurnRequest`;
- translate gateway attempt events into `ModelStreamEvent`;
- emit `attempt_started` before each provider attempt;
- classify provider exceptions;
- emit `attempt_failed` with stable attempt number and error class;
- retry only retryable classes within the frozen model snapshot budget;
- emit `retry_scheduled` with delay and next attempt;
- use bounded exponential backoff plus jitter;
- preserve `Retry-After` when valid and bounded;
- classify context overflow separately and return control for compaction;
- race request, stream iteration, and backoff against `CancellationSignal`;
- close the provider stream on cancel/timeout;
- preserve Python task cancellation;
- emit one terminal typed error event for non-retryable provider errors;
- redact credentials and unsafe provider error bodies;
- never read mutable gateway retry defaults after the Run begins.

### 12.1 Error classes

Required normalized classes remain those from Plan 1:

```text
authentication
rate_limit
timeout
network
provider_5xx
context_overflow
invalid_request
content_filter
aborted
unknown
```

Retryable by default:

```text
rate_limit
timeout
network
selected provider_5xx
```

Terminal by default:

```text
authentication
invalid_request
content_filter
context_overflow
aborted
unknown
```

Context overflow enters compaction policy rather than transport retry.

### 12.2 Terminal attempt event sequences

The event contract is updated and tested with these exact sequences:

Successful attempt:

```text
attempt_started
(text/reasoning/tool events)*
usage?                 # at most one cumulative snapshot for this attempt
finish
```

Retryable failure:

```text
attempt_started
(partial events)*
attempt_failed         # clears all partial content/calls for that attempt
usage?                  # cumulative failed-attempt usage when provider reports it
retry_scheduled
attempt_started ...
```

Terminal provider error, context overflow, or abort:

```text
attempt_started
(partial events)*
attempt_failed         # clears partial buffers
usage?
error                   # typed terminal error_class; stream ends, no finish
```

`ModelStreamAssembler` gains a discriminated `build_outcome()` result:

```text
assistant
context_overflow
provider_error
aborted
```

Only `assistant` contains an `AssistantMessage`. Context overflow, provider error,
and abort never append a synthetic assistant message. Overflow invokes compaction;
non-retryable failure becomes a bounded session observation; abort maps to durable
Run status `canceled`.

Usage is a cumulative snapshot per provider attempt, never an incremental delta.
Budget accounting stores the latest snapshot for each attempt and sums attempts,
including failed attempts when the provider reports usage.

### 12.3 Partial-attempt output

Plan 2 does not project provider deltas to public SSE. Kernel/session buffering is
attempt-aware:

- output from a failed attempt is discarded by `ModelStreamAssembler`;
- retry metadata remains in the private lifecycle stream;
- only the successful assembled assistant message is appended durably;
- final provider streaming UX is deferred to the Plan 4 projection contract.

## 13. OrchestratorKernel

Add:

```text
execution/orchestrator/kernel.py
```

The kernel is the generic agent loop. It receives injected ports and pure
policies; it does not construct providers, tools, stores, clocks, or IDs itself.

Required dependencies:

```python
OrchestratorKernel(
    run_store: OrchestratorRunStore,
    model_runtime: ModelRuntime,
    tool_runtime: ToolRuntime,
    tool_catalog: ToolCatalog,
    context_compiler: ContextCompiler,
    budget_policy: BudgetPolicy,
    projection_driver: ProjectionDriver,
    clock: Clock,
    id_factory: IDFactory,
)
```

`ToolCatalog.list_tools(run)` returns the immutable tool inventory for the next
turn. Plan 2 provides a static fake catalog; Plan 3 replaces it with authorized
Agent Card projection. `ProjectionDriver` is an in-memory no-side-effect driver
that claims/completes Plan 1 outbox intents so terminal ordering is exercised
without Room/SSE writes.

The kernel owns CAS checkpoints through `OrchestratorRunStore`. Session creates a
Run and invokes the kernel by Run ID; it does not append around the store behind
the kernel's back.

### 13.1 Generic invocation acceptance boundary

Plan 2 replaces the Run-owned A2A-shaped call inventory with a generic tool-batch
inventory. Every complete correlatable model call receives a durable batch entry
before validation or runtime acceptance. Existing `AcceptedAgentCall` and its
transitions remain the Plan 3 A2A adapter's durable external-call record, keyed by
invocation ID; the Kernel does not inspect it.

Required generic contracts:

```python
class ToolBindingRef:
    binding_id: str
    binding_digest: str


class ResolvedTool:
    definition: ToolDefinition
    binding: ToolBindingRef  # private; never sent to the model


class ToolInvocation:
    invocation_id: str       # equal to the model call_id
    run_id: str
    expected_run_version: int
    assistant_message_id: str
    source_index: int
    causation_id: str
    idempotency_key: str
    tool: ResolvedTool
    arguments: dict[str, object]
    deadline_at: datetime


class ToolAcceptance:
    acceptance_id: str
    invocation_id: str
    idempotency_key: str
    accepted_at: datetime


class ToolSuspension:
    invocation_id: str
    status: Literal["waiting_external", "input_required", "auth_required"]
    observation_cursor: str | None


ToolExecutionOutcome = ToolResult | ToolSuspension


class ToolObservation:
    observation_id: str
    invocation_id: str
    outcome: ToolExecutionOutcome
    observed_at: datetime


class ToolBatchEntry:
    call_id: str
    assistant_message_id: str
    source_index: int
    tool_name: str
    state: Literal[
        "pending", "invalid", "acceptance_failed", "accepted", "executing",
        "waiting_external", "input_required", "auth_required", "terminal",
    ]
    invocation: ToolInvocation | None
    acceptance: ToolAcceptance | None
    buffered_terminal_result: ToolResult | None
    processed_observation_ids: list[str]


class ToolCallBatch:
    assistant_message_id: str
    entries: list[ToolBatchEntry]
```

Plan 1 `ToolResult` becomes terminal-only (`completed`, `failed`, `canceled`,
`rejected`, or `expired`). `waiting_external`, `input_required`, and
`auth_required` move to `ToolSuspension`/ToolCallBatch lifecycle state and
never masquerade as provider tool results.

`ToolCatalog.resolve()` returns `ResolvedTool`; the model receives only its
public `ToolDefinition`. The private binding resolves inside the catalog/runtime
and cannot be selected through an arbitrary model argument.

`ToolRuntime` is a two-phase idempotent boundary:

```python
class ToolRuntime(Protocol):
    async def accept(self, invocation: ToolInvocation) -> ToolAcceptance: ...
    async def execute(
        self,
        invocation: ToolInvocation,
        acceptance: ToolAcceptance,
        cancellation: CancellationSignal,
    ) -> ToolExecutionOutcome: ...
```

The invariant sequence is:

1. After appending the AssistantMessage, Kernel CAS-creates a `ToolCallBatch` with
   one ordered entry for every complete correlatable call.
2. Unknown/schema-invalid calls become terminal error results buffered on their
   entries without invoking ToolRuntime.
3. For each executable entry, Kernel resolves the binding and creates a
   deterministic `ToolInvocation`.
4. `accept()` durably records or replays the idempotent acceptance but performs no
   external dispatch. Plan 2 uses an in-memory acceptance store; Plan 3 persists
   `AcceptedAgentCall` here.
5. Kernel CAS-checkpoints acceptance on that batch entry. On CAS conflict it
   reloads and reconciles by idempotency key.
6. `execute()` is permitted only after the Run entry contains that receipt. Plan
   3 may then dispatch A2A; crash recovery can safely replay the accepted
   invocation.
7. Acceptance failure is buffered as a terminal error on the pre-existing batch
   entry, so a suspended sibling cannot hide or lose it.

The Run-owned ToolCallBatch stores generic lifecycle state, source position,
optional invocation/acceptance identity, buffered terminal result, and processed
observation IDs. It contains no Agent Card or A2A task/context fields.

The runtime never mutates the Run. The Kernel never reads Agent Card, agent ID,
A2A task/context ID, callback, or transport fields. A generic
`observe_tool(run_id, ToolObservation)` entry point deduplicates observations,
CAS-updates the invocation, and resumes only when the whole assistant call batch
is terminal.

### 13.2 Turn algorithm

For each model turn:

1. Validate Run is non-terminal and not awaiting external/user input.
2. Recalculate active counts from persisted state.
3. Enforce deadline and model-turn budget.
4. If the normal turn limit is reached, append one bounded wrap-up notice,
   disable tools, and enter grace-turn mode.
5. Resolve the turn's tool inventory from `ToolCatalog`.
6. Compile a bounded context view including those tool schemas.
7. Build `ModelTurnRequest` from frozen profile, prompt, context, remaining retry
   budget, absolute deadline, and active tool definitions.
8. Consume `GatewayModelRuntime.stream_turn()` and checkpoint retry/usage budget
   from typed events.
9. Build the discriminated model outcome.
10. On overflow, compact and retry within budget; on abort/error, checkpoint the
    typed observation/terminal transition without an assistant message.
11. On assistant outcome, CAS-append the assistant before any tool side effect.
12. If there are no tool calls, call Plan 1 terminal evaluation and
    `commit_terminal_decision()`; never mutate Run status directly.
13. CAS-create an ordered ToolCallBatch entry for every complete correlatable
    call.
14. Resolve every tool binding and validate arguments locally; buffer terminal
    errors on invalid entries.
15. Construct deterministic ToolInvocations and call `accept()` idempotently.
16. CAS-checkpoint every acceptance receipt before external execution is allowed;
    buffer acceptance failures on their existing entries.
17. Execute accepted entries sequentially or bounded-parallel.
18. Convert execution errors into terminal ToolResults.
19. Validate call/result correlation and checkpoint every terminal or suspended
    outcome on its batch entry.
20. If any call is suspended, stop without appending a model-facing ToolResult.
21. When the whole batch is terminal, CAS-append exactly one ToolResult message
    per call in assistant source order.
22. Update call/concurrency budget.
23. Continue unless the result requires external wait, user input, cancellation,
    deadline handling, or budget termination.

### 13.3 Tool execution ordering and suspension

- Profile `sequential` executes all calls in order.
- Profile `parallel` allows bounded concurrency.
- Any selected tool with `execution_mode=sequential` makes the whole batch
  sequential.
- Completion events may occur in actual completion order internally.
- Terminal outcomes are checkpointed on ToolCallBatch entries. ToolResult
  messages are appended exactly once, in assistant source order, only when every
  call in that assistant batch is terminal.
- Cancellation is propagated to every active tool execution.
- One tool failure does not discard successful sibling results.
- Unknown, schema-invalid, or acceptance-failed calls do not execute, but their
  batch entries retain correlated terminal error results.
- `waiting_external`, `input_required`, and `auth_required` are invocation
  lifecycle states, not model-facing ToolResult messages.
- A suspension leaves the assistant ToolCall unresolved and stops the Run. A
  generic, deduplicated `ToolObservation` later updates the invocation.
- When all calls in a suspended mixed batch become terminal, the Kernel flushes
  exactly one ToolResult per call in original source order and resumes the next
  model turn.

### 13.4 Stop outcomes

The kernel returns a typed outcome:

```text
final_answer
waiting_external
awaiting_user
budget_exhausted
aborted
failed
```

A provider/tool error is normally fed back into the model. Completed final text
uses Plan 1's authoritative terminal CAS, producing unexecuted outbox intents.
The Plan 2 in-memory ProjectionDriver completes those intents and transitions
`projection_state` without public side effects.

Plan 2 adds pure terminal commit transitions for `failed`, `canceled`, and
`budget_exhausted`, with mandatory event/public-status intents but no final-
message intent. Kernel `aborted` maps to durable `canceled`; it must not invent a
new Run status. The Run becomes `failed` only for an unrecoverable runtime
contract error, exhausted error loop, or inability to produce a final response
within hard bounds.

## 14. Agent-Shaped Fake ToolRuntime

Plan 2 defines the production-grade generic Tool Call contract and proves the
Kernel against a deterministic recording runtime. Only transport/side effects
are fake; message conversion, schema validation, correlation, ordering, budgets,
CAS checkpoints, stop outcomes, and transcript feedback use the same contracts
Plan 3 will use.

Use five small model-visible fake tools:

```text
fake_agent_echo             # text or structured completed result
fake_agent_fail             # explicit failed result
fake_agent_delay_parallel   # delayed, cancelable, parallel-safe
fake_agent_delay_sequential # delayed, cancelable, sequential-only
fake_agent_pause            # suspend, then complete through ToolObservation
```

A malformed-result runtime is injected only for negative tests and is not
registered as a model-visible tool. The fake names intentionally communicate
agent-delegation semantics without importing A2A types.

`waiting_external` and `input_required` stop the Plan 2 loop but do not create
real AgentCall/HITL records or transcript ToolResults. The fake observation
harness later supplies a terminal result, after which the Kernel flushes the
batch and resumes. Plan 3 replaces only the catalog/runtime/observation adapters
with durable A2A implementations.

Plan 2 must not register a real `call_agent(agent_id, ...)` tool. In Plan 3, each
authorized candidate Agent is projected from its Agent Card into a distinct
model-visible ToolDefinition (or stable advertised-skill tool). The private
catalog entry binds that tool to the Agent identity; the model cannot supply an
arbitrary `agent_id`. A single internal `A2AAgentToolRuntime.execute()` may serve
all projected tools without making A2A identity part of the Kernel contract.

## 15. RoomAgentSession

Add an unbound session facade, for example:

```text
execution/orchestrator/session.py
```

Responsibilities:

- accept a user `AgentMessage`;
- maintain one active Run;
- append transcript entries through the injected state store/harness;
- invoke the kernel;
- expose generic tool-observation ingestion, abort, and wait-for-idle behavior;
- expose private lifecycle events to tests;
- reject a new normal prompt while a Run is active;
- keep HITL/steering/follow-up unsupported until their later contracts are
  implemented;
- never write public Room messages or SSE in Plan 2.

Session construction is explicit:

```python
class RoomAgentSessionConfig:
    session_id: str
    room_id: str
    profile: OrchestratorProfile
    candidate_scope: CandidateScopeSnapshot
    tool_catalog: ToolCatalog


class RunFactory(Protocol):
    def create_run(
        self,
        *,
        config: RoomAgentSessionConfig,
        message: UserMessage,
        client_request_id: str | None,
    ) -> OrchestratorRunState: ...


class RoomAgentSession:
    async def prompt(
        self,
        message: UserMessage,
        *,
        client_request_id: str | None = None,
    ) -> SessionRunResult: ...
    async def continue_run(self) -> SessionRunResult: ...
    async def observe_tool(
        self,
        observation: ToolObservation,
    ) -> SessionRunResult: ...
    async def abort(self) -> None: ...
    async def wait_for_idle(self) -> None: ...
    def subscribe(self, listener: SessionEventListener) -> Unsubscribe: ...
```

`RunFactory` supplies Run/session/Room IDs, request fingerprint, profile and
candidate snapshots, deadline, empty transcript/invocations/outbox, budget, version,
and timestamps. The session persists the initial Run with
`OrchestratorRunStore.create()` and then invokes the kernel by Run ID. Duplicate
client request IDs replay through the in-memory store contract. `continue_run()`
rejects a suspended Run until a new deduplicated ToolObservation is applied.

Listeners receive a separate private `SessionEvent`, not `OrchestratorEvent`.
Exceptions are isolated and reported to a bounded error hook. Non-terminal
listeners have a per-listener timeout and cannot block the kernel. Terminal/idle
listeners are awaited in registration order within a total settlement timeout;
timeout records a private diagnostic and releases idle. Unsubscribe prevents new
callbacks but does not cancel an already running callback. Listener cancellation
never mutates Run state.

## 16. Transcript Conversion

Add a pure transcript module:

```text
execution/orchestrator/transcript.py
```

It converts `AgentMessage` to `ModelMessage` without losing call/result
correlation.

Rules:

- user text/data becomes model user content;
- assistant text and tool calls remain one assistant turn;
- tool results preserve call IDs, tool names, and error status;
- DataPart uses stable, bounded JSON serialization;
- artifact references become bounded descriptive model content, never invented
  file data;
- SessionNotice is included only through explicit notice policy;
- UI/private projection records are excluded;
- a suspension adds no tool-result transcript entry;
- each call receives at most one model-facing terminal ToolResultMessage;
- a suspended mixed batch remains unresolved until all calls are terminal, then
  results are appended together in assistant source order;
- assistant tool call and matching terminal result cannot be separated by
  truncation;
- no model turn is compiled while a call batch remains unresolved;
- orphan or duplicate tool results are rejected as context corruption;
- unknown custom transcript messages are excluded or fail according to an
  explicit policy, never silently converted into user instructions.

## 17. Context Compiler and Compaction

Add:

```text
execution/orchestrator/context.py
execution/orchestrator/compaction.py
```

### 17.1 ContextCompiler

Inputs:

- immutable system prompt snapshot;
- complete private Run transcript;
- optional admitted Room/background context supplied by tests;
- tool definitions;
- context-window and output-token reserves;
- current budget/wrap-up state.

Output:

- bounded provider-neutral `ModelMessage` list;
- token estimate and reserved output budget;
- compaction metadata;
- inventory of retained transcript entry IDs.

### 17.2 Token accounting

- provider usage events are authoritative for completed calls;
- preflight context size uses a deterministic estimator;
- OpenAI-compatible routes may use `tiktoken` where supported;
- unknown routes use a conservative deterministic fallback;
- estimator output is not presented as billing usage;
- output reserve and provider context limits come from the frozen model snapshot;
- provider framing, system prompt, all tool schemas, messages, and output reserve
  are included in preflight accounting.

If mandatory components alone exceed the context window, compaction is not
attempted indefinitely. `ContextCompiler` returns typed `context_unfit`, the
kernel appends a bounded operational notice, and the Run commits `failed` or
`budget_exhausted` according to whether the cause is invalid configuration or
exhausted compaction budget. No provider request is sent.

### 17.3 Non-destructive compaction

The full transcript remains unchanged. The context view may contain:

```text
first user request
+ compacted summary of older completed spans
+ recent complete turns
+ every unresolved tool call/result dependency
+ current budget/runtime notice
```

Compaction boundaries never split an assistant tool-call batch from its results.
Unresolved calls and active error/recovery context are retained verbatim.

Plan 2 defines an injected `ContextCompactor` protocol and provides:

- deterministic fake compactor for unit tests;
- model-backed compactor using the execution-owned ModelRuntime with
  `tool_choice=none`, no tools, and a dedicated bounded prompt;
- fallback recent-tail policy if summarization fails;
- separate compaction counter and hard maximum;
- proof that compaction reduces estimated context size before retrying overflow.

A model-backed compaction call uses `purpose=compaction`, the current absolute
Run deadline and remaining cumulative provider-retry/token budgets, and no tools.
Every attempt/usage event is CAS-checkpointed through the same accounting path as
an agent turn before compaction completes. It increments `compactions_used`; it
does not increment normal agent `model_turns_used`.

Current `context_memory` remains a Room-level persistence capability and is not
used as the Run transcript compactor.

## 18. Budget Policy

Add pure budget functions under:

```text
execution/orchestrator/budget.py
```

Counters and limits:

- normal model turns;
- grace model turns;
- cumulative provider retries across agent and compaction calls;
- tool calls;
- active parallel tools;
- input/output tokens;
- compactions;
- deadline.

Semantics:

1. One assembled assistant response consumes one model turn.
2. Provider retry attempts consume the cumulative Run provider-retry budget, not
   extra normal model turns.
3. Every requested executable tool call consumes call budget before execution.
4. Invalid/unknown calls do not consume external-call budget but remain model
   observations.
5. Parallel active count is derived from runtime tasks and reconciled to state.
6. Context overflow may compact only within `max_compactions`.
7. At the normal turn limit, append one wrap-up notice and disable all tools.
8. Grace turns may only produce final text or a terminal error.
9. Starting a new tool during grace is rejected as a tool result and cannot
   extend the loop indefinitely.
10. Deadline wins before a new provider attempt or tool side effect.
11. Reported usage is one cumulative snapshot per attempt; accounting replaces
    duplicate snapshots and sums distinct attempts without double-counting.
12. Model-backed compaction consumes compaction, retry, token, and deadline
    budgets but not normal model-turn budget.
13. Configured cumulative input/output-token limits are enforced before new
    turns when known and after usage events; absence of a limit means observe-only.
14. Budget exhaustion preserves the full transcript and completed fake-tool
    results.

No hard monetary budget is added until gateway usage exposes reliable normalized
cost.

## 19. Private Lifecycle Events

Plan 2 emits private in-process events suitable for tests and later projection:

```text
session_started
run_started
turn_started
model_attempt_started
model_retry_scheduled
model_attempt_failed
message_completed
tool_execution_started
tool_execution_completed
turn_completed
run_waiting_external
run_awaiting_user
run_final_answer_ready
run_budget_exhausted
run_failed
run_canceled
session_idle
```

Every event carries session ID, Run ID, causation ID, sequence, timestamp, and a
bounded typed payload. Raw prompts, credentials, provider bodies, and tool secrets
are excluded.

Events are not written to production Run/Event stores or SSE in Plan 2. A typed
mapping is nevertheless fixed for later projection:

```text
run_final_answer_ready -> durable terminal decision -> run_completed
run_canceled -> run_canceled
run_budget_exhausted -> run_budget_exhausted
run_failed -> run_failed
tool_execution_started/completed -> tool_call_accepted/completed
```

Private events never substitute for Plan 1 durable event/outbox facts.

## 20. Proposed File Changes

### Changed Plan 1 contract modules

```text
backend/execution/orchestrator/models.py       # generic invocation/suspension
backend/execution/orchestrator/ports.py        # catalog + two-phase runtime
backend/execution/orchestrator/transitions.py  # tool-batch/observation CAS
backend/execution/orchestrator/conformance.py      # updated runtime scenarios
backend/execution/orchestrator/contract_harness.py # acceptance/observation harness
```

Protocol inventory tests are updated with every public port change.

### New execution modules

```text
backend/execution/orchestrator/
  model_runtime.py
  kernel.py
  session.py
  transcript.py
  context.py
  compaction.py
  budget.py
  lifecycle.py
  fake_tools.py
```

The final implementation may combine very small modules, but responsibilities
must remain independently testable.

### New/changed LLM gateway modules

```text
backend/llm_gateway/
  gateway.py                         # additive single-attempt turn API
  turn_types.py                      # new gateway-owned neutral types
  error_classification.py            # new typed provider error mapping
  model_registry.py                  # route-specific capabilities
  structured_generation.py           # structured-action schema/validation
  providers/openai_provider.py       # native streamed tools
  providers/deepseek_provider.py     # fallback/native route support
```

### Removed provider surface

```text
backend/llm_gateway/providers/gemini_provider.py
Gemini exports/registration/model routes/model-name settings
Gemini functional gateway tests and fixtures
(active API-key configuration becomes rejection-only migration input)
stale Bedrock/generic-provider override cases
`google-genai` dependency when the import inventory is empty
```

Deletion follows the rule in Section 10.3: remove tests only with the behavior
and contract they covered; rewrite shared routing/API tests for OpenAI/DeepSeek.

### Expected test files

```text
backend/tests/test_llm_gateway_turn_openai.py
backend/tests/test_llm_gateway_turn_deepseek.py
backend/tests/test_llm_gateway_error_classification.py
backend/tests/test_orchestrator_v3_model_runtime.py
backend/tests/test_orchestrator_v3_kernel.py
backend/tests/test_orchestrator_v3_session.py
backend/tests/test_orchestrator_v3_transcript.py
backend/tests/test_orchestrator_v3_context.py
backend/tests/test_orchestrator_v3_budget.py
```

Existing Plan 1 and applicable OpenAI/DeepSeek legacy gateway tests remain
mandatory.

## 21. Implementation Stages

### Stage 0: Plan 1 contract corrections

1. Fix PR #158 mandatory-terminal-batch detection and add optional-later-intent
   regression tests.
2. Split provider strictness from locally validated structured-action support.
3. Add cumulative retry/token limits and per-turn remaining deadline/retry input.
4. Add typed invalid-call recovery metadata and SessionNotice identity.
5. Add discriminated model stream outcomes for overflow/error/abort.
6. Add non-success terminal commit transitions and Plan 2 in-memory projection
   settlement.
7. Add ResolvedTool, ToolInvocation, ToolAcceptance, ToolSuspension,
   ToolObservation, ToolBatchEntry, and ToolCallBatch contracts; split terminal
   ToolResult status from suspension lifecycle status.
8. Add ToolCatalog, two-phase ToolRuntime, RunFactory, and private SessionEvent
   contracts.

Exit criteria: updated Plan 1 tests pass and no v3 production binding is added.

### Stage A: Focused Gateway turn primitives

1. Inventory and retire the Gemini gateway adapter, registration, model routes,
   model settings, dependency, and functional provider tests; add fail-fast
   migration detection for Gemini-only configuration.
2. Remove stale Bedrock/generic-provider override cases that conflict with the
   closed OpenAI/DeepSeek provider set.
3. Add gateway turn types and provider-turn protocol.
4. Add error classification.
5. Add route-specific capability metadata and enforce the explicit provider/API
   matrix.
6. Implement OpenAI native tool stream.
7. Implement DeepSeek structured-action fallback and optional native fixture.
8. Preserve applicable generic API shapes and rewrite their tests against the
   two supported providers.

Exit criteria: OpenAI/DeepSeek fixtures pass the one-attempt raw turn contract,
unsupported providers fail typed routing, no production Gemini import remains,
and all applicable gateway tests are green.

### Stage B: Concrete ModelRuntime

1. Translate Plan 1 request/messages/tools to gateway turn types.
2. Translate provider events to Plan 1 events.
3. Implement visible retry/backoff/error/abort.
4. Run provider-neutral conformance against native and fallback fixtures.

Exit criteria: the concrete runtime passes every offline conformance scenario.

### Stage C: Agent Core

1. Implement transcript conversion.
2. Implement the five agent-shaped fake tools, in-memory acceptance/observation
   harness, and JSON Schema validation.
3. Implement `OrchestratorKernel` loop and two-phase accept-before-execute
   handshake.
4. Implement sequential/bounded-parallel batches, suspension, terminal
   observation ingestion, and source-ordered result flush.
5. Implement cancellation and typed stop outcomes.

Exit criteria: fake model + fake tool loops cover final text, tool feedback,
parallelism, errors, cancellation, and wait → terminal observation → resumed
model turn without transcript duplication.

### Stage D: Agent Session and context

1. Implement unbound RoomAgentSession including generic `observe_tool()`.
2. Implement ContextCompiler and token estimator.
3. Implement non-destructive compaction view.
4. Implement budget/wrap-up/grace policy.
5. Implement private lifecycle event barrier.

Exit criteria: long transcripts compact safely, overflow recovers, and sessions
become idle deterministically.

### Stage E: Hardening

1. Run all Plan 1 and Plan 2 tests.
2. Run all existing LLM gateway tests.
3. Run full backend suite.
4. Verify production dependency graph remains unbound.
5. Run formatting, lint, diff check, and Docker rebuild.
6. Update architecture documentation for the additive gateway/runtime layers.

## 22. Required Tests

### 22.1 Provider adapter fixtures

Required finish-reason mapping:

| Provider raw reason | Normalized reason |
| --- | --- |
| OpenAI/DeepSeek `stop` | `stop` |
| OpenAI/DeepSeek `tool_calls` | `tool_calls` |
| OpenAI/DeepSeek `length` | `length` |
| OpenAI/DeepSeek `content_filter` | `content_filter` |

Configuration/routing:

- explicit frozen OpenAI route uses only `OPENAI_API_KEY`;
- explicit frozen DeepSeek route uses only `DEEPSEEK_API_KEY` and the official
  endpoint;
- both keys may coexist without changing the selected route;
- a missing selected key fails typed and never falls back across providers;
- Gemini/Bedrock/unknown routes fail typed before an SDK request;
- no credential value appears in snapshots, events, errors, or logs.

OpenAI:
- reasoning + text;
- one tool call;
- interleaved parallel call deltas;
- delayed call ID/name chunks;
- usage-only terminal chunk;
- every OpenAI finish-reason mapping listed above;
- SDK stream close/cancel.

DeepSeek:

- structured final action;
- structured tool calls;
- local schema validation;
- unknown/invalid tool rejection;
- thinking default and explicit override;
- native route only when explicitly configured and fixture-proven;
- the offline fixture route is explicitly named (for example
  `test_deepseek_structured_action`) and never inferred from developer
  environment settings.

### 22.2 ModelRuntime tests

- visible attempt sequence;
- retryable and terminal error classification;
- bounded Retry-After and deadline recheck before attempt start and backoff sleep;
- partial failed-attempt output discarded;
- context overflow returns to compaction;
- abort during request, stream iteration, and backoff;
- frozen snapshot overrides mutable gateway defaults;
- no retry beyond profile budget;
- exact conformance error code matching.

### 22.3 Kernel tests

- final answer without tools;
- one tool then final answer;
- multiple sequential turns;
- parallel-safe batch;
- sequential override;
- unknown tool;
- complete schema-invalid call creates correlated ToolResult error;
- malformed/truncated call creates stable deduplicated SessionNotice and never
  invokes ToolRuntime;
- malformed ToolResult correlation;
- `accept()` is idempotent and always precedes `execute()`;
- CAS conflict after acceptance reconciles without duplicate execution;
- `execute()` rejects a missing/mismatched acceptance receipt;
- tool failure observed by next model turn;
- sibling success preserved when one tool fails;
- awaiting-external stop;
- input-required stop;
- cancellation;
- provider terminal error;
- repeated invalid-call loop reaches hard bound;
- every one of the five agent-shaped fake tools completes its intended path;
- fake `waiting_external`/`input_required` outcomes stop without A2A records or
  transcript ToolResults;
- wait → terminal ToolObservation → exactly one ToolResult → resumed final turn;
- duplicate observations are ignored idempotently;
- mixed parallel batch buffers completed siblings until the suspended call is
  terminal, then flushes all results once in source order;
- invalid sibling + suspended sibling retains the correlated invalid result until
  terminal flush;
- acceptance-failed sibling + suspended sibling retains both durable batch entries
  and flushes exactly once after observation;
- no arbitrary model-supplied `agent_id` is accepted by the fake catalog.

### 22.4 Session/context/budget tests

- one active prompt per session;
- prompt while active returns typed conflict;
- abort, generic `observe_tool`, and wait-for-idle;
- awaited terminal listener barrier and exactly-once settlement when abort races
  with a completed kernel task;
- lossless transcript conversion;
- complete tool-call/result pairs;
- orphan result rejection;
- pair-safe recent-tail selection;
- unresolved call retention and recovery execution for persisted accepted/
  executing entries;
- deterministic compaction;
- compaction must reduce size;
- context overflow with exhausted compaction budget;
- wrap-up notice exactly once;
- no tools in grace turns;
- turn/call/cumulative-retry/token/deadline limits;
- cumulative usage snapshots are not double-counted;
- model-backed compaction consumes retry/token/deadline/compaction budgets but
  not normal turn budget;
- mandatory context alone exceeding the window returns `context_unfit` without
  calling ModelRuntime;
- full transcript preserved after context compaction.

### 22.5 Architecture and regression tests

- PR #158 regression: a completed mandatory terminal batch followed by a higher-
  sequence optional intent still settles;
- terminal batch matching rejects ambiguous or payload-mismatched groups;
- `llm_gateway` imports no execution/Room models;
- kernel imports no provider SDK/A2A/Room/DB/SSE modules;
- production container/routes/jobs do not construct v3 runtime;
- surviving OpenAI/DeepSeek legacy gateway APIs continue passing applicable
  tests;
- all Plan 1 contracts and persistence tests remain green;
- Fast/Ultimate executor control flow remains unchanged;
- provider registration accepts only OpenAI/DeepSeek and rejects deferred
  providers with a typed routing error;
- no production Gemini gateway import or `google-genai` dependency remains;
- Gemini-only credentials trigger typed migration rejection before any OpenAI
  adapter construction/request, while an explicit supported route wins when both
  supported and stale credentials coexist;
- deleted tests have a matching deleted behavior/contract inventory.

## 23. Verification Commands

Focused tests during development:

```bash
cd backend
uv run pytest \
  tests/test_llm_gateway_turn_openai.py \
  tests/test_llm_gateway_turn_deepseek.py \
  tests/test_llm_gateway_error_classification.py \
  tests/test_orchestrator_v3_model_runtime.py \
  tests/test_orchestrator_v3_kernel.py \
  tests/test_orchestrator_v3_session.py \
  tests/test_orchestrator_v3_transcript.py \
  tests/test_orchestrator_v3_context.py \
  tests/test_orchestrator_v3_budget.py
```

Required regression tests:

```bash
cd backend
uv run pytest \
  tests/test_orchestrator_v3_contracts.py \
  tests/test_orchestrator_v3_transitions.py \
  tests/test_orchestrator_v3_store_contract.py \
  tests/test_orchestrator_v3_architecture.py \
  tests/test_llm_gateway_runtime.py \
  tests/test_llm_gateway_provider_streaming.py \
  tests/test_common_foundation.py::test_protocol_methods_match_design_doc
```

Final validation:

```bash
cd backend
uv run pytest
uv run ruff format --check .
uv run ruff check .
cd ..
git diff --check
docker compose up -d --build
docker compose ps
```

Live provider credentials are not required for unit acceptance. Optional smoke
tests may use only `OPENAI_API_KEY` and `DEEPSEEK_API_KEY`; they must be explicitly
marked `integration`, skipped without credentials, and never replace recorded
fixture conformance.

## 24. Acceptance Criteria

Plan 2 is complete only when:

- gateway-owned single-attempt turn types and protocols exist;
- OpenAI native tool-call streaming passes recorded fixtures;
- the named offline DeepSeek structured-action fixture passes local validation;
- the gateway accepts only official OpenAI/DeepSeek routes and credentials;
- Gemini provider code/model routes/dependency/functional tests and stale
  Bedrock provider-override cases are removed with an audited deletion inventory;
- deprecated Gemini credential input exists only for fail-fast migration
  detection and cannot select a provider;
- route capability metadata produces valid frozen snapshots;
- concrete `GatewayModelRuntime` passes the full offline conformance harness;
- Plan 1 PR #158 settlement regression and all Stage 0 contract changes pass;
- retries, errors, usage, finish reasons, and abort are visible and typed;
- `OrchestratorKernel` completes fake model/tool feedback loops;
- the five agent-shaped fake tools cover success, structured data, failure,
  sequential/parallel delay, cancellation, external wait, and input-required;
- no model-visible real `call_agent` or A2A protocol dependency exists;
- every correlatable call has a durable ToolCallBatch entry before validation or
  acceptance, and accept-before-execute is idempotent/CAS-checkpointed before
  side effects;
- suspended calls create no model result; terminal observation flushes exactly
  one result per batch call and resumes, including mixed parallel batches;
- `RoomAgentSession` supports prompt, continue, observe_tool, abort, subscribe,
  and idle barrier;
- transcript/context conversion preserves tool-call/result integrity;
- compaction is non-destructive, bounded, and proven to reduce context size;
- wrap-up/grace behavior cannot start new tools or loop indefinitely;
- mandatory context overflow produces typed `context_unfit` without a provider
  request;
- Run factory, static ToolCatalog, CAS checkpoints, and in-memory outbox driver
  are covered behaviorally;
- remaining OpenAI/DeepSeek gateway consumers remain green and deferred provider
  selection fails explicitly;
- all Plan 1 and Plan 2 focused tests pass;
- the full backend test suite passes;
- Ruff and diff checks pass;
- Docker rebuild succeeds with healthy backend and Mongo;
- architecture tests prove v3 remains production-unbound;
- architecture documentation reflects the additive model-runtime and kernel
  layers.

## 25. Plan 3 Boundary

Plan 3 replaces fake tools with durable external A2A tools. It owns:

- Agent Card to authorized dynamic ToolDefinition projection, preferably one
  model-visible tool per candidate Agent or stable advertised skill;
- private catalog binding from each projected tool to Agent identity, rather than
  a model-supplied arbitrary `agent_id`;
- the internal `A2AAgentToolRuntime` (the concrete equivalent of `call_agent`);
- candidate authorization and scope refresh;
- `ToolRuntime.accept()` implementation that durably persists/replays an
  AcceptedAgentCall before `execute()` may dispatch;
- A2A direct/relay/webhook transport integration;
- task/context ownership;
- callback translation into generic deduplicated ToolObservation ingress;
- input/auth continuation and HITL application;
- resource, attachment, and artifact materialization;
- transport idempotency and retries;
- external-call cancellation;
- restart recovery workers;
- real waiting-external/resume behavior.

Plan 3 must not redesign provider tool calling, `GatewayModelRuntime`, the core
ResolvedTool/ToolInvocation/ToolAcceptance/ToolSuspension/ToolObservation/
ToolResult loop, transcript conversion, or budget semantics established here. It
replaces the fake ToolCatalog/ToolRuntime/observation adapters and adds durable
A2A lifecycle behavior behind those boundaries.

## 26. Risks and Mitigations

### Provider API variation

Risk: streamed tool-call shapes vary by OpenAI/DeepSeek SDK/model version.

Mitigation: two focused provider parsers, recorded fixtures, conservative field
access, and provider-neutral conformance. New providers require a later explicit
adapter and capability matrix entry.

### DeepSeek capability uncertainty

Risk: native tool behavior differs by model and endpoint.

Mitigation: structured-action default; native tools require exact route fixtures
and explicit capability configuration.

### Retry after partial output

Risk: failed-attempt deltas could leak into durable/public output.

Mitigation: attempt-aware buffering and no public projection in Plan 2.

### Transcript growth

Risk: long model/tool loops exceed context limits.

Mitigation: immutable full transcript plus bounded context view, pair-safe
compaction, hard compaction count, and measurable size reduction.

### Kernel scope expansion

Risk: A2A or Room concerns leak into Plan 2.

Mitigation: fake tools, unbound session, architecture import tests, and explicit
Plan 3 boundary.

### Gateway cleanup regressions

Risk: provider parser changes or Gemini removal break existing
summary/memory/supervisor calls.

Mitigation: repository-wide consumer inventory, rewrite surviving tests against
OpenAI/DeepSeek, additive turn API, preserved generic method shapes, typed failure
for deferred provider selection, and full backend validation.

## 27. Follow-Up Plans

- **Plan 3:** Durable external A2A tools, callbacks, HITL, artifacts,
  cancellation, and recovery.
- **Plan 4:** Fast/Ultimate profiles, API/frontend cutover, production binding,
  and public lifecycle projection.
- **Plan 5:** Delete legacy executors and semantic orchestration machinery; move
  remaining workflow-specific LLM services and finish architecture cleanup.
