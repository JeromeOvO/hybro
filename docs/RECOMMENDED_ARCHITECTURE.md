# Hybro Recommended Architecture
> Synthesized from design reviews, industry research (Hermes Agent, Claude Code, OpenClaw, Codex CLI), and PMF-stage strategic considerations.
> Last updated: April 2026

---

## Executive Summary

The PR #127 design is technically sound but over-engineered for the current stage. It solves the right *eventual* problems — execution reliability, module isolation, pluggable orchestration — but builds everything from scratch at a point when:

1. Product-market fit is not yet found
2. Interaction modes beyond chat are unaddressed
3. Better open-source foundations exist (DBOS, AG-UI)
4. arq (the planned task queue) is in maintenance-only mode

This document proposes an architecture that:
- Delivers the same reliability guarantees with less custom code
- Adopts proven open protocols (AG-UI, A2A) instead of reinventing them
- Leaves real extensibility headroom for interaction modes and workflow authoring
- Stays lean enough to pivot as the business model evolves

---

## The Protocol Stack (Where Hybro Fits)

The industry has converged on a four-layer agent protocol stack. Hybro should be a **consumer and integrator** of the bottom three layers, and own the top one:

```
┌─────────────────────────────────────────────────────────┐
│                     AG-UI Protocol                      │
│         Agents ↔ User-facing applications               │
│   (adopt this — don't reinvent your SSE schema)         │
├─────────────────────────────────────────────────────────┤
│                      A2UI Protocol                      │
│         Generative UI payload format                    │
│   Agents describe rich UI surfaces; client renders      │
│   natively using its own component library              │
│   (AG-UI is the pipe — A2UI is the content)             │
├─────────────────────────────────────────────────────────┤
│                     A2A Protocol                        │
│              Agent ↔ Agent communication                │
│       (already adopted — keep and extend)               │
├─────────────────────────────────────────────────────────┤
│                    MCP Protocol                         │
│                  Agents ↔ Tools                         │
│           (support as agent capability)                 │
└─────────────────────────────────────────────────────────┘

Hybro's unique value sits ABOVE this stack:
  Multi-agent orchestration  →  who coordinates multiple agents
  Agent marketplace          →  discovery, trust, billing
  Workflow authoring         →  user-defined agent pipelines
  Runtime environments       →  Hub (local), Cloud, Edge
```

---

## Target Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                        Frontend                              │
│              AG-UI Client (CopilotKit or custom)             │
│   Chat · A2UI Surfaces · Workflow Editor · Task Dashboard    │
│   @a2ui/react renderer · State Sync UI                       │
└──────────────────────────┬───────────────────────────────────┘
                           │  AG-UI events (SSE / WebSocket)
                           │  A2UI surface payloads (via AG-UI CUSTOM)
                           │  RunAgentInput (threadId, state, tools, context)
┌──────────────────────────▼───────────────────────────────────┐
│                     API Surface (thin)                       │
│         Auth · Validation · Rate Limiting · Routing          │
└──────┬──────────┬──────────┬──────────┬──────────┬───────────┘
       │          │          │          │          │
┌──────▼──┐  ┌───▼────┐  ┌──▼──────┐  ┌▼──────┐  ┌▼──────────┐
│ Agent   │  │Context │  │Workflow │  │ Dev   │  │ Room /    │
│Intelli- │  │& Memo- │  │Engine   │  │Plat-  │  │ Thread    │
│ gence   │  │  ry    │  │(DBOS)   │  │ form  │  │           │
└──────┬──┘  └───┬────┘  └──┬──────┘  └┬──────┘  └┬──────────┘
       │          │          │           │           │
┌──────▼──────────▼──────────▼───────────▼───────────▼─────────┐
│                    AG-UI Interaction Adapter                   │
│  Single point for all frontend-visible events                 │
│  Translates domain facts → AG-UI event stream                 │
│  STATE_SNAPSHOT · STATE_DELTA · TEXT_MESSAGE_* · TOOL_CALL_*  │
│  STEP_STARTED · RUN_STARTED · CUSTOM · ACTIVITY_*             │
│  emit_a2ui_surface() → CUSTOM event carrying A2UI payload     │
└──────────────────────────────┬────────────────────────────────┘
                               │
┌──────────────────────────────▼────────────────────────────────┐
│                   A2A Protocol Adapter                        │
│           Direct · Relay (Hub) · Webhook transports           │
│   A2UI DataParts detected by mimeType application/json+a2ui   │
│   and forwarded through InteractionAdapter to browser         │
└──────────────────────────────┬────────────────────────────────┘
                               │
┌──────────────────────────────▼────────────────────────────────┐
│                       LLM Gateway                             │
│             OpenAI · Bedrock · Gemini · Router                │
└───────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────┐
│                      Infrastructure                           │
│   MongoDB   (conversations · agents · rooms · messages · artifacts) │
│   Postgres  (DBOS: execution state · HITL · scheduling)       │
│   Redis     (SSE fan-out · Hub relay · cache · rate limiting) │
│   Pinecone · S3                                               │
└───────────────────────────────────────────────────────────────┘
```

---

## The Three Key Changes vs PR #127

### Change 1: Replace Custom SSE Schema with AG-UI

**Current / PR #127**: 11 custom SSE event types in a closed enum.

**Proposed**: Adopt AG-UI as the frontend protocol.

AG-UI gives you for free:
- `STATE_SNAPSHOT` / `STATE_DELTA` (JSON Patch) — bi-directional state sync, enabling generative UI, document collaboration, dashboards
- `REASONING_START/END` — expose chain-of-thought to users
- `ACTIVITY_SNAPSHOT/DELTA` — structured agent activity logs (not just text messages)
- `CUSTOM` — escape hatch for any new interaction pattern without protocol changes
- `STEP_STARTED/FINISHED` — step-level visibility already defined
- `parentRunId` — sub-agent nesting tracked in the same stream

Migration is additive: map each existing SSE event to its AG-UI equivalent. The `InteractionAdapter` in PR #127 becomes an AG-UI event emitter. Frontend adopts an AG-UI client library (CopilotKit, or the TypeScript SDK).

```
Current 11 events → AG-UI equivalents:
  connected         → RUN_STARTED (transport-level)
  heartbeat         → transport heartbeat (unchanged)
  user_message      → TEXT_MESSAGE_START/CONTENT/END (role=user)
  agent_response    → TEXT_MESSAGE_START/CONTENT/END (role=assistant)
  processing_status → STEP_STARTED / STEP_FINISHED / RUN_FINISHED
  task_submitted    → RUN_STARTED with invocation metadata in state
  task_update       → ACTIVITY_DELTA (activityType="agent_invocation")
  artifact_update   → TOOL_CALL_RESULT or CUSTOM("artifact")
  hitl_input_req    → RUN_FINISHED { outcome: "interrupt", interrupt: { id, reason, payload } }  ← native AG-UI (draft spec)
  hitl_status_upd   → STATE_DELTA on interrupt status field
  error             → RUN_ERROR
```

#### AG-UI HITL Support (Draft Spec)

AG-UI has a native interrupt/resume protocol defined in `docs/drafts/interrupts.mdx` — it is in
draft status but already implemented in the ADK middleware integration. The pattern is:

**Agent pauses for HITL (sent as `RUN_FINISHED` with `outcome: "interrupt"`):**
```json
{
  "type": "RUN_FINISHED",
  "threadId": "t1",
  "runId": "r1",
  "outcome": "interrupt",
  "interrupt": {
    "id": "hitl-abc123",
    "reason": "human_approval",
    "payload": { "prompt": "...", "options": [...] }
  }
}
```

**User resumes (sent as the next `RunAgentInput`):**
```json
{
  "threadId": "t1",
  "runId": "r2",
  "resume": {
    "interruptId": "hitl-abc123",
    "payload": { "approved": true }
  }
}
```

This is cleaner than Hybro's current `hitl_input_requested` / `hitl_status_update` pair because:
- The interrupt is self-describing (`reason`, `payload` carry full context)
- Resume is a standard run input, not a separate API endpoint
- The `interruptId` binds response to prompt without custom routing logic
- Works with DBOS: `DBOS.set_event()` fires when `RUN_FINISHED(interrupt)` is sent; `DBOS.recv()` resolves when the next run arrives with `resume`

Until the AG-UI interrupt spec is stable, the `CUSTOM("hitl_prompt")` fallback remains valid.
The architecture document should be updated when the spec graduates from draft.

### Change 2: Replace Custom Execution Runtime with DBOS

**Current / PR #127**: ~1,200 lines of runtime consistency spec to build from scratch — leases, fenced writes, durable inbox/outbox, event ordering, admissibility filters.

**Proposed**: DBOS (open source, MIT, Postgres-backed) handles all of this.

What DBOS provides:
- `@DBOS.workflow()` — durable function execution; survives crashes, resumes from last completed step
- `@DBOS.step()` — atomic retryable step with configurable retry policy
- `@DBOS.scheduled()` — cron-triggered workflows natively
- `DBOS.send()` / `DBOS.recv()` — durable messaging between workflows (this IS the HITL model)
- `DBOS.set_event()` / `DBOS.get_event()` — wait-for-external-input pattern
- Time-travel debugging — replay any past execution from Postgres state

**What the Orchestration Engine becomes:**

```python
from dbos import DBOS

@DBOS.workflow()
async def supervisor_run(run_id: str, request: OrchestrationRequest):
    trajectory = []
    
    for step in range(request.config.max_steps):
        # Supervisor decides next action — pure business logic
        action = await decide_next(request, trajectory)
        
        match action:
            case Delegate(targets=targets):
                # Dispatch agents — each is a durable step
                # invocation_id generated here (in the workflow) for DBOS retry idempotency
                results = await asyncio.gather(*[
                    invoke_agent(run_id, agent_id, f"{run_id}:{agent_id}:{step}", request)
                    for step, agent_id in enumerate(targets)
                ])
                trajectory.append((action, results))
                
            case Clarify(prompt=prompt):
                # HITL — pause workflow, wait for user response
                # Workflow state is persisted in Postgres; worker is freed
                await DBOS.set_event(f"hitl_prompt:{run_id}", {
                    "prompt": prompt, "run_id": run_id
                })
                user_response = await DBOS.recv(
                    f"hitl_response:{run_id}",
                    timeout_seconds=3600
                )
                trajectory.append((action, user_response))
                
            case Done():
                return OrchestrationResult(status="completed", trajectory=trajectory)

@DBOS.step()
async def invoke_agent(run_id: str, agent_id: str, invocation_id: str, request: OrchestrationRequest):
    # invocation_id passed in from workflow — deterministic across DBOS retries
    # (see PERSISTENCE_UNIFICATION_DESIGN.md §6 for idempotency details)
    return await a2a_protocol.dispatch(agent_id, request)

# Adding a new workflow mode is just a new DBOS workflow function:
@DBOS.workflow()
async def voting_run(run_id: str, request: OrchestrationRequest):
    ...

# Scheduled workflows — no custom cron infrastructure:
@DBOS.scheduled("0 9 * * MON")
@DBOS.workflow()
async def weekly_agent_health_report():
    ...
```

HITL response (from API endpoint):
```python
# User responds to interruption:
await DBOS.send(run_id, user_response, f"hitl_response:{run_id}")
# The paused workflow automatically resumes
```

This replaces:
- `ExecutionRun` + lease model → DBOS workflow ownership
- `ExecutionStep` + DAG progression → DBOS steps
- `AgentInvocation` tracking → DBOS step state
- `HumanInterruption` + inbox queue → `DBOS.send/recv`
- Run inbox/outbox/checkpoint → DBOS internal Postgres tables
- arq (dying) → DBOS workflows
- Leader election for background jobs → `@DBOS.scheduled()`

#### Supervisor Actions as Tool Calls (P0 — Informed by Codex CLI + Claude Code)

The `decide_next()` function above hides a critical design choice: **how the supervisor LLM selects its next action**. Both Codex CLI and Claude Code model every orchestration action — delegate to agent, request clarification, synthesize, terminate — as a **tool call** rather than free-text classification.

This matters because:
- **Structured output** — the LLM returns typed JSON for each action, not a string to parse
- **Prompt cache stability** — the system message + tool definitions form a stable prefix; only the trajectory (conversation history) grows. This keeps prompt cache hit rates high (Codex CLI achieves ~90% cache hits by keeping its system prompt immutable)
- **Auditability** — every supervisor decision is a tool call with a name and arguments, directly traceable in DBOS step history and AG-UI `TOOL_CALL_*` events
- **Extensibility** — adding a new supervisor action (e.g., `search_memory`, `escalate_to_human`) is adding a tool definition, not editing a prompt

The supervisor loop should expose its actions as LLM tool calls:

```python
SUPERVISOR_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "delegate",
            "description": "Dispatch task to one or more agents",
            "parameters": {
                "type": "object",
                "properties": {
                    "targets": {"type": "array", "items": {"type": "string"}},
                    "instructions": {"type": "string"},
                },
                "required": ["targets", "instructions"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clarify",
            "description": "Ask the user for clarification before proceeding",
            "parameters": {
                "type": "object",
                "properties": {"prompt": {"type": "string"}},
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "Finalize the run with a synthesis of results",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
        },
    },
]

@DBOS.workflow()
async def supervisor_run(run_id: str, request: OrchestrationRequest):
    trajectory: list[dict] = []

    for step in range(request.config.max_steps):
        response = await llm_gateway.chat(
            model=request.config.supervisor_model,
            messages=[SUPERVISOR_SYSTEM_MSG] + trajectory,
            tools=SUPERVISOR_TOOLS,
        )

        tool_call = response.choices[0].message.tool_calls[0]
        args = json.loads(tool_call.function.arguments)

        match tool_call.function.name:
            case "delegate":
                results = await asyncio.gather(*[
                    invoke_agent(run_id, aid, f"{run_id}:{aid}:{step}", request)
                    for step, aid in enumerate(args["targets"])
                ])
                trajectory.extend(format_results(tool_call, results))
            case "clarify":
                await DBOS.set_event(f"hitl_prompt:{run_id}", {"prompt": args["prompt"]})
                user_response = await DBOS.recv(f"hitl_response:{run_id}", timeout_seconds=3600)
                trajectory.append(format_clarification(tool_call, user_response))
            case "done":
                return OrchestrationResult(status="completed", summary=args["summary"])
```

This is a Phase 2 design decision: when the orchestration module moves to DBOS workflows, the supervisor prompt should be restructured to use tool calls from the start.

The `ExecutionRun`, `ExecutionStep`, `AgentInvocation`, `HumanInterruption` entity model from PR #127 remains valid as the **domain model** for business logic — DBOS is just the execution substrate underneath it.

### Change 3: Add Workflow Authoring Layer (the Missing Piece)

PR #127 doesn't address user-defined workflows at all. This is an additive layer on top of DBOS:

```python
# A WorkflowTemplate is a storable artifact:
class WorkflowTemplate:
    template_id: str
    name: str
    owner_id: str
    graph: WorkflowGraph    # JSON: nodes (agents/tools) + edges (conditions)
    input_schema: dict      # JSON Schema for input parameters
    created_at: datetime
    version: int

class WorkflowGraph:
    nodes: list[WorkflowNode]    # agent_id | llm_call | condition | human_input
    edges: list[WorkflowEdge]    # source_node → target_node, optional condition

# Executing a template compiles it into a DBOS workflow:
@DBOS.workflow()
async def run_workflow_template(template_id: str, inputs: dict):
    template = await load_template(template_id)
    executor = WorkflowTemplateExecutor(template)
    return await executor.execute(inputs)
```

The drag-and-drop editor (ReactFlow-based) edits `WorkflowTemplate.graph` and stores it as a document. Execution compiles the graph into DBOS steps at runtime.

---

## Local & Remote Hybrid (Hub)

### What's Already Implemented

The Hub/relay architecture is the **strongest and most complete** part of the design — and largely pre-dates PR #127. Phases 1, 2a, 2b, and 2c of `HYBRO_HUB_DESIGN.md` are all ✅ shipped:

- **Gateway API** — local hub code can discover and call cloud agents via `api.hybro.ai/v1/gateway`. Auth, access control, URL masking, rate limiting: done.
- **Hub daemon** — `pip install hybro-hub`, connects outbound-only via SSE relay, never accepts inbound connections. Works behind NAT/firewalls.
- **Auto-discovery** — hub finds local A2A agents by scanning all listening TCP ports via `psutil`, not a fixed port range.
- **`DispatchMiddleware` architecture** — `AgentMessageProcessor` selects transport per agent based on `agent.source`: `"hub"` → relay, `"cloud"` → direct A2A. The orchestration layer (supervisor, workflow executor) is completely unaware of agent locality.
- **`RELAY_DISPATCHED` status** — both `SupervisorExecutor` and `QueueExecutor` treat relay dispatch like `PAUSED`, persisting continuation state. A supervisor can mix local and cloud agents in a single run.
- **Offline queue** — messages to offline hub agents queue and deliver FIFO when hub reconnects. 100 message cap, 24h TTL.
- **Frontend** — hub/cloud badges, offline dimming, privacy indicators (🏠 Local / ☁️ Cloud) per message. All live.

The core product thesis — one web portal, local and cloud agents side-by-side, routing transparent to the user — works today.

### Gap 1: Relay Offline Queue Needs DBOS

The current offline queue is in-memory in `relay_service.py` with a periodic sweep job in `stale_task_checker.py`. This is the same pattern arq/leader-election uses — fragile across crashes and instance restarts.

Under DBOS, this becomes durable with no custom machinery:

```python
@DBOS.workflow()
async def relay_to_hub(hub_id: str, event: RelayToHubEvent):
    delivered = await try_push_to_hub_sse(hub_id, event)
    if not delivered:
        # Workflow state is persisted; DBOS.recv waits durably for hub reconnect
        await DBOS.recv(f"hub_reconnected:{hub_id}", timeout_seconds=86400)
        await push_to_hub_sse(hub_id, event)

# When hub reconnects, signal all waiting relay workflows:
await DBOS.send(hub_id, {}, f"hub_reconnected:{hub_id}")
```

The current offline queue's 100-message cap and sweep job become unnecessary. DBOS persists the state in Postgres and resumes the relay workflow whenever the hub comes back. This is included in Phase 2.

### Gap 2: DBOS Step Retry Semantics for Hub Agents

When DBOS becomes the execution substrate, `@DBOS.step()` wraps each agent invocation. DBOS's default retry policy assumes transient failures (network error, timeout). A relay dispatch returning `RELAY_DISPATCHED` is not a transient failure — it means the hub is offline and the workflow should durably wait, not retry.

The `invoke_agent` step needs explicit handling:

```python
@DBOS.step(retries_allowed=0)   # Relay dispatch is not retryable
async def invoke_agent(run_id: str, agent_id: str, request: OrchestrationRequest):
    result = await agent_message_processor.process_single_message(agent_id, request)
    if result.status == ProcessingStatus.RELAY_DISPATCHED:
        # Hub offline — wait durably for the hub to publish its response
        # The relay workflow (Gap 1) will deliver the response and send it here
        response = await DBOS.recv(
            f"hub_response:{result.agent_message_id}",
            timeout_seconds=86400
        )
        return response
    return result
```

This interaction between `DispatchMiddleware` and DBOS step semantics must be explicitly designed during Phase 2.

### Gap 3: Streaming Token Latency Through the Relay

The current relay token streaming path has 4 network hops:

```
Local agent → Hub dispatcher → Hub relay client
  → POST /api/v1/relay/.../publish → Cloud relay service
  → SSEManager.broadcast_to_room() → Browser
```

Each HTTP `POST /publish` call adds ~50–100ms of round-trip overhead. For a local Ollama model generating 30+ tokens/second, this makes local agent streaming visibly choppier than cloud agents — which is ironic given the user's agents are literally on their own machine.

**Phase 3 fix**: replace the per-batch REST publish with a WebSocket or long-lived chunked HTTP stream from hub to relay. Tokens flow without round-trip overhead. The relay streams them directly into `SSEManager`.

This is a Phase 3 optimization, but important to name now: **local agents should feel at least as snappy as cloud agents**.

### Gap 4: Hybrid Orchestration — the Unresolved Hard Problem

The current model: the **cloud supervisor** orchestrates all agents, and locality is a transport detail. This is correct and sufficient for most cases.

It breaks for privacy-sensitive hybrid tasks:

> User: "Research public data about cancer treatments, then apply the findings to my private medical records."

The cloud supervisor receives the full message — including the sensitive part. The hub's privacy router classifies it *after* the sensitive text has already transited the relay. The correct behavior is for the **hub orchestrator** to split the task: the research step dispatches to cloud agents, the private-data application step runs entirely locally. But today the hub has no orchestration capability — it is a relay, not an orchestrator.

This is explicitly deferred to Phase 3 in `HYBRO_HUB_DESIGN.md §14.1`. The design space when it becomes a priority:

| Option | Mechanism | Trade-off |
|---|---|---|
| Hub-side task splitting | Hub classifies → sends cloud sub-task to gateway, runs local sub-task locally | Hub needs orchestration logic; complex to coordinate results |
| Cloud orchestrator with privacy-aware routing | Cloud supervisor knows sensitivity metadata per agent | Sensitive task description still reaches cloud |
| User-explicit routing | User tags each message or agent as "local only" / "cloud OK" | Simplest; puts burden on user |
| Workflow templates with privacy annotations | Nodes tagged local/cloud; template executor routes per node | Clean but requires workflow authoring layer first |

**Recommendation**: Implement user-explicit routing (`local_only` flag on messages) as the Phase 3 MVP. Workflow-template-level routing follows once the `WorkflowTemplate` data model exists (Phase 3 backend, Phase 4 editor).

### Gap 5: Multi-Hub Coordination (Future / Enterprise)

A user with multiple hubs (laptop + work desktop). Both online. A room has agents from both. Per-agent routing by `agent_id` handles dispatch correctly, but the following are not yet designed:

- Cancellation signal propagation when agents from two hubs are in-flight simultaneously
- Partial result handling when Hub A's agent completes but Hub B's times out
- `processing_message_id` clearance when multiple hub agents respond at different times

This is low priority at current scale. For enterprise fleet deployments (Hub Phase 3), it becomes a first-class concern.

### Gap 6: Typed Relay Protocol Schema (P1 — Informed by OpenClaw)

OpenClaw defines a **typed JSON schema** for every message between its core and plugin subsystems — each message has a `kind` discriminator, typed payload, and versioned schema. The Hub relay currently uses loosely-typed dicts for relay events (`RelayToHubEvent`, `RelayFromHubEvent`), making it fragile to schema drift between cloud and hub versions.

**Recommendation**: define a versioned, discriminated-union schema for all relay messages:

```python
from pydantic import BaseModel, Field
from typing import Literal, Union
from datetime import datetime

class RelayMessageBase(BaseModel):
    version: Literal["1.0"] = "1.0"
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    correlation_id: str

class RelayDispatch(RelayMessageBase):
    kind: Literal["dispatch"] = "dispatch"
    agent_id: str
    task_id: str
    payload: dict  # A2A SendMessageRequest

class RelayResponse(RelayMessageBase):
    kind: Literal["response"] = "response"
    task_id: str
    status: Literal["completed", "failed", "streaming"]
    payload: dict  # A2A Task response

class RelayCancel(RelayMessageBase):
    kind: Literal["cancel"] = "cancel"
    task_id: str

class RelayHeartbeat(RelayMessageBase):
    kind: Literal["heartbeat"] = "heartbeat"
    hub_id: str
    agent_count: int

RelayMessage = Union[RelayDispatch, RelayResponse, RelayCancel, RelayHeartbeat]
```

Benefits:
- **Version negotiation**: hub and cloud can detect schema mismatches on connect
- **Exhaustive handling**: `match msg.kind` ensures all message types are handled
- **Backward compatibility**: new `kind` values are additive; old hubs ignore unknown kinds

This is a Phase 2 addition: formalize when building DBOS step semantics for hub agents (Gap 2), since the relay message format directly affects how `invoke_hub_agent` interprets responses.

### Hub Architecture Summary

| Capability | Status | Notes |
|---|---|---|
| Gateway API (hub calls cloud agents) | ✅ Shipped | Phase 1 |
| Hub daemon + Ollama adapter | ✅ Shipped | Phase 2b |
| Relay: cloud → hub message delivery | ✅ Shipped | Phase 2a |
| DispatchMiddleware per-agent routing | ✅ Shipped | Phase 2a |
| Frontend hub badges + privacy indicators | ✅ Shipped | Phase 2c |
| Offline message queue | ✅ Shipped (in-memory) | DBOS upgrade in Phase 2 |
| DBOS step retry semantics for relay | ⚠️ Needs design | Phase 2 |
| Typed relay protocol schema | ⚠️ Needs design | Phase 2 |
| Streaming token latency (relay overhead) | ⚠️ Known issue | Phase 3 fix |
| Hybrid orchestration (privacy-aware routing) | ❌ Not implemented | Phase 3 |
| Multi-hub coordination | ❌ Not designed | Phase 3 / Enterprise |

---

## Error Recovery, Cancellation, and Observability

### When DBOS Steps Exhaust Retries

When a `@DBOS.step()` fails all retries (default: 3 for cloud agents), DBOS marks the step `ERROR` and propagates the exception to the calling `@DBOS.workflow()`. The workflow catches it and the orchestration layer is responsible for producing the user-facing AG-UI event:

```python
@DBOS.workflow()
async def supervisor_run(run_id: str, request: OrchestrationRequest):
    try:
        for agent_id in action.targets:
            invocation_id = f"{run_id}:{agent_id}:{step}"
            result = await invoke_cloud_agent(run_id, agent_id, invocation_id, request)
    except Exception as exc:
        # Step exhausted retries — emit error event to browser and mark run failed
        await interaction_adapter.emit(RunFinished(
            run_id=run_id,
            thread_id=request.thread_id,
            outcome="error",
            error=str(exc),
        ))
        await postgres.execute(
            "UPDATE agent_invocations SET status='failed', error=:err WHERE invocation_id=:id",
            {"id": invocation_id, "err": str(exc)},
        )
        return OrchestrationResult(status="error", error=str(exc))
```

**User-facing AG-UI event**: `RUN_FINISHED { outcome: "error", error: { message, code } }`. The browser receives this and displays an error state. No partial MongoDB writes remain — the Redis buffer TTL (60 min) expires and the buffer is silently garbage-collected.

**Hub agent step failure** (`retries_allowed=0`): when the DBOS step waiting on `DBOS.recv(f"hub_response:{agent_message_id}")` times out (24h timeout), the same pattern applies — the workflow catches the `TimeoutError` and emits `RUN_FINISHED(outcome="error")`.

### Cancellation Design

User-initiated cancellation works through a two-step signal:

**Step 1 — Cancel signal (Redis, unchanged from current design):**
```python
# API endpoint: POST /api/v1/runs/{run_id}/cancel
await redis.set(f"cancel:{run_id}", "1", ex=3600)  # existing mechanism, unchanged
```

**Step 2 — DBOS workflow cooperation (new):**

The supervisor workflow must poll the cancel signal at step boundaries:

```python
@DBOS.workflow()
async def supervisor_run(run_id: str, request: OrchestrationRequest):
    for step in range(request.config.max_steps):
        # Check cancellation before dispatching each step
        if await redis.exists(f"cancel:{run_id}"):
            await interaction_adapter.emit(RunFinished(
                run_id=run_id, thread_id=request.thread_id, outcome="cancelled"
            ))
            return OrchestrationResult(status="cancelled")

        action = await decide_next(request, trajectory)
        ...
```

For steps that are already in-flight when cancel arrives, the pattern is:
- **Cloud agents**: the A2A protocol call is interrupted when the DBOS step's network call returns an error or is abandoned on the next retry check. The Redis `cancel:{run_id}` key is checked at the top of each step retry.
- **Hub agents**: `DBOS.send(run_id, {"cancelled": True}, f"cancel_hub:{run_id}")` wakes the waiting `DBOS.recv()` in `invoke_hub_agent` immediately.

**Browser receives**: `RUN_FINISHED { outcome: "cancelled" }` via the `interaction_adapter.emit()` above.

#### Cascading Cancellation for Sub-Agents (P1 — Informed by Claude Code)

Claude Code implements **hierarchical cancellation**: when a parent agent is cancelled, all sub-agents and their descendants receive the cancellation signal, with a grace period for cleanup. The current Hybro design cancels at the run level but doesn't cascade to individual in-flight agent invocations cleanly.

When the supervisor dispatches multiple agents in parallel (`asyncio.gather`), cancellation should propagate to each:

```python
@DBOS.workflow()
async def supervisor_run(run_id: str, request: OrchestrationRequest):
    for step in range(request.config.max_steps):
        if await redis.exists(f"cancel:{run_id}"):
            # Cancel all in-flight invocations for this run
            invocation_ids = await get_active_invocations(run_id)
            await asyncio.gather(*[
                cancel_invocation(inv_id) for inv_id in invocation_ids
            ])
            await interaction_adapter.emit(RunFinished(
                run_id=run_id, thread_id=request.thread_id, outcome="cancelled"
            ))
            return OrchestrationResult(status="cancelled")
        ...

async def cancel_invocation(invocation_id: str):
    """Signal a specific agent invocation to cancel."""
    await redis.set(f"cancel_invocation:{invocation_id}", "1", ex=3600)
    # For A2A agents that support cancellation:
    # POST /tasks/{task_id}/cancel (A2A protocol)
```

Key design points:
- **Grace period**: give in-flight agents 5s to complete before force-terminating the DBOS step
- **A2A cancel propagation**: for agents that support it, send the A2A `tasks/cancel` request
- **Hub agents**: `DBOS.send(f"cancel_hub:{invocation_id}")` wakes the waiting `DBOS.recv()`
- **Trace cleanup**: cancelled invocations are marked `cancelled` in the invocation table, not `failed`

This is a Phase 2 design requirement: wire it when building the DBOS-based supervisor workflow.

### Operational Observability

DBOS ships a built-in dashboard that shows all workflow executions, step statuses, and inputs/outputs in Postgres. No custom monitoring infrastructure is needed for execution state.

For Hybro-owned alerting:

| Signal | Detection | Action |
|---|---|---|
| Workflow stuck > N minutes | Query `dbos.workflow_status WHERE status='PENDING' AND created_at < now - interval` | Alert + auto-cancel via `DBOS.cancel_workflow(workflow_id)` |
| High step retry rate | Query `dbos.operation_outputs WHERE error IS NOT NULL` count per window | Alert for agent reliability issues |
| Redis buffer TTL expiry without MongoDB write | Compare `agent_invocations WHERE status='running'` age vs buffer TTL | Indicates crashed workflow; DBOS recovery handles it |
| HITL timeout | `DBOS.recv()` timeout fires; workflow emits `RUN_FINISHED(outcome="error")` | Frontend shows "Request timed out" |

---



### What A2UI Is

**A2UI** (Agent-to-User Interface, Google / Apache 2.0, v0.8 stable) is an open **generative UI payload format** that lets agents describe rich, interactive UI surfaces — forms, cards, approval dialogs, dashboards — as declarative JSON. The client renders them using its own native components; no iframes, no arbitrary code execution.

The relationship to the other protocols:

```
AG-UI  ← the pipe   (transport; events flow here)
A2UI   ← the content (payload format; what the UI looks like)
A2A    ← the carrier (A2UI DataParts ride inside A2A messages)
```

### Why A2UI Fits Hybro's Architecture

**1. It directly solves the interaction-modes gap.**

Chat forces multi-turn Q&A for tasks that should be single-step:

```
Text-only:         Agent-driven form (A2UI):
  Agent: Date?       ┌─────────────────────────────┐
  User: Tomorrow     │  Book a Table               │
  Agent: Time?       │  Date: [ 2026-04-01     ▼ ] │
  User: 7pm          │  Guests: [ 2           ▾ ] │
  Agent: Party?      │  Time: [ 7:00 PM       ▼ ] │
  ...                │          [ Confirm ]         │
                     └─────────────────────────────┘
```

**2. A2UI surfaces are already carried by A2A.**

An agent signals it supports A2UI via its A2A Agent Card extension (`https://a2ui.org/a2a-extension/a2ui/v0.8`). Responses with UI intent come back as A2A `DataPart`s with `mimeType: application/json+a2ui`. The `invoke_agent` step already receives and reassembles these parts — detecting them is a small addition.

**3. The Surface Ownership Pattern maps to Hybro's supervisor.**

A2UI defines exactly the multi-agent routing Hybro needs: when a sub-agent emits a `createSurface`, the supervisor records `surfaceId → agentId` in session state, then routes `action` messages back to the owning agent. This is the same session-state mechanism Hybro's supervisor already maintains for routing.

**4. The Catalog system is the extension point for the marketplace.**

Each marketplace agent advertises supported `catalogId`s. Hybro defines a `hybro-standard-catalog` that agents implement to participate in the marketplace UI layer. Premium agents can extend it with their own components.

### How It Plugs Into the Hybro Stack

**Backend (`invoke_agent` step):**

> **Note on retries and hub agents**: The snippet below shows the cloud-agent path (`retries_allowed=3`). Hub (relay) agents must use `retries_allowed=0` because `RELAY_DISPATCHED` is not a transient failure. In practice `invoke_agent` should be split into `invoke_cloud_agent` and `invoke_hub_agent` with different retry policies (see `§ Gap 2` and `PERSISTENCE_UNIFICATION_DESIGN.md §6`). The A2UI DataPart detection logic below applies to both variants identically.
>
> **Note on `invocation_id`**: It must be generated by the **workflow** (caller) and passed into the step — not generated inside the step — so the buffer key and MongoDB message ID are stable across DBOS retries (see `PERSISTENCE_UNIFICATION_DESIGN.md §6`).

```python
from a2ui.a2a import is_a2ui_part, get_a2ui_datapart
from a2ui.core.parser.parser import parse_response

@DBOS.step(retries_allowed=3)   # cloud agents; use retries_allowed=0 for hub agents
async def invoke_cloud_agent(run_id, agent_id, invocation_id, request):
    # invocation_id is passed in from the workflow (deterministic, stable across retries)
    ...
    # After assembling the A2A response parts:
    for part in a2a_response.parts:
        if is_a2ui_part(part):
            a2ui_messages = get_a2ui_datapart(part).data  # list of A2UI messages
            await interaction_adapter.emit_a2ui_surface(
                invocation_id=invocation_id,
                surface_id=a2ui_messages[0].get("createSurface", {}).get("surfaceId"),
                messages=a2ui_messages,
            )
        else:
            # existing text streaming path (Redis buffer → single MongoDB write on completion)
            ...
```

**`InteractionAdapter` — new `emit_a2ui_surface()` method (Phase 4):**

```python
async def emit_a2ui_surface(
    self,
    invocation_id: str,
    surface_id: str,
    messages: list[dict],
) -> None:
    """Emit an A2UI surface payload to the browser via AG-UI CUSTOM event."""
    await self.emit(CustomEvent(
        name="a2ui_surface",
        value={
            "invocation_id": invocation_id,
            "surface_id": surface_id,
            "messages": messages,   # list of A2UI JSON messages (createSurface, updateComponents, etc.)
        },
    ))
```

**Frontend (`@a2ui/react` renderer, Phase 4):**

```tsx
import { A2UIProvider, A2UIRenderer, useA2UI } from '@a2ui/react';

// In the message bubble, when a message contains an a2ui_surface custom event:
function A2UISurfaceMessage({ messages, surfaceId, onAction }) {
  const { processMessages } = useA2UI();
  useEffect(() => { processMessages(messages); }, [messages]);

  return (
    <A2UIProvider onAction={onAction}>
      <A2UIRenderer surfaceId={surfaceId} />
    </A2UIProvider>
  );
}

// User actions (button clicks, form submits) route back to the agent
// via the same SSE connection as text messages.
function handleA2UIAction(action) {
  sendToBackend({
    type: "a2ui_action",
    surface_id: action.surfaceId,
    action_name: action.name,
    context: action.context,
  });
}
```

**Action routing (supervisor):**

```python
# surface_owners is stored in the DBOS workflow's operation outputs (not in-memory dict)
# so it survives crash-and-replay correctly. The supervisor passes it as a mutable
# step parameter or reads/writes it via a dedicated @DBOS.step() to ensure DBOS
# records the mutation in operation_outputs before proceeding.

@DBOS.step()
async def record_surface_owner(run_id: str, surface_id: str, agent_id: str):
    """Durably record surface ownership so it survives workflow replay."""
    await postgres.execute(
        "INSERT INTO surface_owners (run_id, surface_id, agent_id) VALUES (:run_id, :sid, :aid)"
        " ON CONFLICT (run_id, surface_id) DO NOTHING",
        {"run_id": run_id, "sid": surface_id, "aid": agent_id},
    )

# Supervisor calls this step when a sub-agent creates a surface:
if event.name == "a2ui_surface":
    await record_surface_owner(run_id, event.value["surface_id"], agent_id)

# Incoming a2ui_action messages route to the owning agent:
if request.type == "a2ui_action":
    owner = await postgres.fetchone(
        "SELECT agent_id FROM surface_owners WHERE run_id=:run_id AND surface_id=:sid",
        {"run_id": run_id, "sid": request.surface_id},
    )
    await invoke_cloud_agent(run_id, owner.agent_id, new_invocation_id, ActionRequest(
        text=f"User submitted action '{request.action_name}' with context: {request.context}"
    ))
```

> **Why a Postgres table, not in-memory dict**: `@DBOS.workflow()` is replayed on crash. Any in-memory mutation inside the workflow (e.g. `session_state[key] = value`) is re-executed on replay — which is fine for idempotent assignments. But for correct behavior after a crash-and-resume where the browser reconnects mid-session, the supervisor needs to know which surfaces already exist. Storing ownership in Postgres (via a `@DBOS.step()`) means DBOS records the mutation in `operation_outputs` and the step is not re-executed on replay, making the ownership map durable. The `surface_owners: dict[str, str]` field in the supervisor session model (Phase 3 prep hook) is still useful as an in-memory cache for non-crash paths.

### What A2UI Enables (Use Cases for Hybro)

| Use Case | A2UI Component | Replaces |
|---|---|---|
| HITL confirmation dialog | `Button` (Confirm/Reject) + data card | Text "do you approve?" + text reply |
| Agent configuration form | `TextField`, `Slider`, `DateTimeInput` | Multi-turn Q&A setup flow |
| Structured agent results | `Card`, `List`, `Tabs`, `Image` | Markdown-only response |
| Booking / scheduling flow | `DateTimeInput`, `MultipleChoice` | Back-and-forth date parsing |
| Approval workflow step | `Button` with `checks` (validation) + data display | Custom HITL UI |
| Marketplace agent UI | Agent-defined custom catalog | Hardcoded per-agent frontend code |
| Workflow editor canvas (Phase 4) | Custom catalog with `WorkflowNode`, `EdgeConnector` | ReactFlow from scratch |

### Hybro Catalog Strategy

Start with the **A2UI Basic Catalog** (pre-built, open-source renderer already in `@a2ui/react`) — it covers Button, TextField, Card, List, DateTimeInput, Slider, Tabs, Modal, Image, and more. Define a **`hybro-standard-catalog`** in Phase 4 that extends Basic with Hybro-specific components (agent card, run status, approval widget). Agents advertise catalog support in their A2A Agent Card.

### Integration Status

| Layer | Status | Phase |
|---|---|---|
| A2A DataPart detection in `invoke_agent` | ⬜ Not started | Phase 3 prep |
| `emit_a2ui_surface()` on `InteractionAdapter` | ⬜ Not started | Phase 3 prep |
| `Message.parts` schema supports DataPart (`application/json+a2ui`) | ⬜ Not started | Phase 3 prep |
| `@a2ui/react` renderer in frontend | ⬜ Not started | Phase 4 |
| `a2ui_surface` CUSTOM event handling in AG-UI client | ⬜ Not started | Phase 4 |
| `a2ui_action` routing in supervisor | ⬜ Not started | Phase 4 |
| `hybro-standard-catalog` definition | ⬜ Not started | Phase 4 |
| Agent marketplace catalog negotiation | ⬜ Not started | Phase 4+ |

### Phase 3 Extension Points to Preserve (No-Regret Hooks)

Three small additions during Phase 3 cost almost nothing but prevent a painful retrofit when A2UI lands in Phase 4:

1. **`emit_a2ui_surface()` stub on `InteractionAdapter`** — add the method signature (raises `NotImplementedError`), so the interface is declared and Phase 4 wiring is a one-line fill-in.
2. **`Message.parts` accepts `DataPart`** — the `parts: list[MessagePart]` field in the new MongoDB `messages` schema (see `PERSISTENCE_UNIFICATION_DESIGN.md §Layer 3`) should include a `DataPart` variant alongside `TextPart` and `FilePart`:
   ```python
   class DataPart(BaseModel):
       type: Literal["data"] = "data"
       mime_type: str              # "application/json+a2ui" for A2UI surfaces
       data: dict[str, Any]        # the raw A2UI JSON payload
   ```
3. **Surface ownership map in supervisor session state** — add a `surface_owners: dict[str, str]` field to the supervisor's session model now (defaulting to `{}`). This is a schema addition, not a behavioral change, and avoids a migration when action routing is wired up.

---

## Infrastructure

### Right Tool for the Right Job

The key principle: **Postgres is the execution store, Redis is the delivery and operational store.**

```
MongoDB   — agents, rooms, marketplace, conversation records
           (document model; `messages` + `artifacts` collections introduced in Phase 3
            replacing `room_agent_messages` — see PERSISTENCE_UNIFICATION_DESIGN.md)

Postgres  — workflow execution ONLY (via DBOS)
           • DBOS workflow state + durability
           • HITL coordination (send/recv)
           • Scheduled job execution
           • Step/invocation tracking

Redis     — real-time delivery + operational layer
           • SSE fan-out across instances (Pub/Sub)
           • Hub relay events (Streams)
           • Cancellation tokens + cross-instance signaling
           • Terminal dedup (SET NX)
           • Agent catalog + room cache
           • Rate limiting counters

Pinecone  — vector search for agent recommendation
S3        — artifact storage
```

Redis is genuinely better than Postgres for pub/sub (sub-millisecond vs 1-5ms), streaming (Redis Streams are built for it), and cache TTL management. Forcing Postgres to do SSE fan-out via LISTEN/NOTIFY would introduce a throughput ceiling on high-frequency artifact streaming. Redis stays for what it's actually good at.

### What Gets Added
- **PostgreSQL** — DBOS execution store only. Any managed Postgres works (Neon free tier for dev, RDS/Supabase/Railway for production).

### What Gets Removed
- **arq** — removed entirely (maintenance-only since Oct 2025). Replaced by DBOS workflows.
- **Redis leader election** (`SETNX` per-job) — replaced by `@DBOS.scheduled()` which handles exactly-once execution natively.
- **Custom lease/inbox/outbox model** (PR #127) — replaced by DBOS.

### What Stays (Unchanged)
- **MongoDB** — all conversation data, agent catalog, rooms. Unchanged.
- **Redis** — SSE fan-out, Hub relay, cache, rate limiting. The `HORIZONTAL_SCALING_DESIGN.md` spec applies as written for these.
- **Pinecone** — vector search. Unchanged.
- **S3** — artifact storage. Unchanged.

### How DBOS Changes What Redis Does

Redis loses the responsibilities that DBOS absorbs, but keeps all the delivery/operational ones:

| Redis Responsibility | After DBOS |
|---|---|
| Task queue (arq) | ✅ Replaced by DBOS workflows |
| Distributed run locks | ✅ Replaced by DBOS workflow ownership |
| Leader election for background jobs | ✅ Replaced by `@DBOS.scheduled()` |
| HITL state + continuation blobs | ✅ Replaced by `DBOS.send/recv` |
| SSE fan-out (Pub/Sub) | **Stays in Redis** — built for this |
| Hub relay events (Streams) | **Stays in Redis** — built for this |
| Cancellation tokens | **Stays in Redis** — cross-instance signaling |
| Terminal dedup (SET NX) | **Stays in Redis** — atomic, fast |
| Agent catalog / room cache | **Stays in Redis** — TTL-native |
| Rate limiting (INCR) | **Stays in Redis** — zero contention |

---

## Module Architecture (Simplified from PR #127)

Keep PR #127's module boundaries — they are correct. Simplify the internal implementation:

```
api/                          ← Thin routing, AG-UI endpoint
agent_intelligence/           ← Who answers? (multi-factor scoring)
  __init__.py                 ← AgentIntelligence facade (unchanged)
  catalog.py                  ← Agent CRUD + MongoDB/Redis cache
  recommender.py              ← Multi-factor scoring pipeline
  scoring/                    ← Scoring factors (pluggable)
  health_checker.py
  target_resolver.py

context_memory/               ← What context? (unchanged from PR #127)
  __init__.py                 ← ContextMemory facade
  context_assembly.py
  memory/
  compaction/
  search/

orchestration/                ← How to coordinate? (DBOS-powered)
  __init__.py                 ← OrchestratorFactory facade
  plugin_contract.py          ← StepHandler protocol, StepContext, StepResult, StepHandlerMetadata
  registry.py                 ← register_step_type() + discover_patterns()
  workflows/                  ← One file per workflow mode (pluggable StepHandler implementations)
    supervisor.py             ← @DBOS.workflow() supervisor loop (implements StepHandler)
    debate.py                 ← @DBOS.workflow() debate mode (implements StepHandler)
    direct.py                 ← @DBOS.workflow() direct dispatch (implements StepHandler)
    # Adding new mode = new StepHandler implementation + register_step_type() call
  steps/
    invoke_agent.py           ← @DBOS.step() agent dispatch
    synthesize.py             ← @DBOS.step() synthesis
  factory.py                  ← OrchestratorFactory.register()

workflow_authoring/           ← NEW: user-defined workflow templates
  __init__.py                 ← WorkflowTemplating facade
  template.py                 ← WorkflowTemplate data model
  executor.py                 ← Compile template → DBOS workflow at runtime
  api.py                      ← CRUD endpoints for templates

interaction/                  ← AG-UI event emission (replaces custom SSE schema)
  __init__.py                 ← InteractionService facade
  ag_ui_adapter.py            ← Domain facts → AG-UI events
  sse_manager.py              ← SSE connections + Redis Pub/Sub fan-out
  hitl.py                     ← DBOS.send/recv wrapper with UX projection

a2a_protocol/                 ← Pure protocol (unchanged from PR #127)
llm/                          ← LLM Gateway (see § LLM Gateway Enhancements below)
developer_platform/           ← API keys, discovery, gateway (unchanged)
room/                         ← Room/Thread management
infrastructure/
common/
container.py                  ← Dependency wiring
main.py
```

### Pluggability Guarantee

Adding a new workflow mode requires exactly:
1. One new file `orchestration/workflows/my_workflow.py` with `@DBOS.workflow()`
2. One line in `factory.py`: `factory.register("my_workflow", my_workflow_fn)`

No schema migrations. No changes to execution infrastructure. No changes to other modules.

---

## LLM Gateway Enhancements

### Prompt Cache Stability (P1 — Informed by Codex CLI)

Codex CLI achieves ~90% prompt cache hit rates by keeping its system message immutable across turns. The same principle applies to Hybro's supervisor and to user-facing agent calls.

**Design rule**: the LLM Gateway should structure every call so that the **system message + tool definitions** form a stable prefix that does not change between steps in the same run. The growing part (conversation history / trajectory) is appended after the prefix. This maximizes prompt cache hit rates on providers that support it (OpenAI, Anthropic, Gemini).

Concretely:
- Supervisor system prompt and `SUPERVISOR_TOOLS` are frozen per run (not regenerated per step)
- Agent-specific context (room history, user preferences) goes in a `user` message after the system message, not interpolated into the system message
- The `llm/` module should track and log cache hit rates per model provider so degradation is detectable

This is a Phase 2 design constraint: enforce it when restructuring the supervisor prompt for tool calls.

### LLM Credential Pool and Failover Chain (P1 — Informed by Hermes + OpenClaw)

Both Hermes Agent and OpenClaw implement multi-provider credential rotation: when one LLM provider returns a rate limit or error, the gateway transparently fails over to the next provider in a configured chain.

Hybro already has multi-provider support (`OpenAI · Bedrock · Gemini · Router`), but the current router is selection-based (user or supervisor picks a model), not failover-based. The LLM Gateway should add:

```python
class LLMFailoverChain:
    """Try providers in order; fail over on rate limit, timeout, or 5xx."""
    def __init__(self, providers: list[LLMProvider]):
        self.providers = providers

    async def chat(self, request: ChatRequest) -> ChatResponse:
        last_error = None
        for provider in self.providers:
            try:
                return await provider.chat(request)
            except (RateLimitError, TimeoutError, ServerError) as e:
                last_error = e
                logger.warning(f"Provider {provider.name} failed, trying next: {e}")
                continue
        raise AllProvidersExhaustedError(last_error)
```

Configuration per deployment:
- **Cloud**: OpenAI → Gemini → Bedrock (cost-optimized ordering)
- **Hub (local)**: Ollama → LM Studio → cloud fallback (if user opts in)
- **Supervisor-specific**: can override chain to prefer lower-latency models

This is a Phase 2 addition: the `llm/` module already has per-provider adapters; adding the failover chain is ~100 lines wrapping them.

---

## Future Extensibility Foundations

These are **cheap hedges to build now** (Phase 1–2) that unlock significant future flexibility without over-engineering. Each is a small addition to the data model or factory pattern that avoids a costly retrofit later.

### 1. `parent_invocation_id` on Every Agent Invocation

The current and PR #127 designs have no way to trace a nested call chain: if supervisor calls agent A, and A internally calls B, there is no parent link. Add one field:

```python
class AgentInvocation:
    invocation_id: str
    run_id: str
    agent_id: str
    parent_invocation_id: str | None  # ← ADD THIS (null for top-level)
    status: InvocationStatus
    ...
```

Cost: one nullable FK on the invocation table. Benefit: enables hierarchical trace trees in the dashboard, sub-agent nesting in AG-UI (`parentRunId`), and a path toward composable multi-agent graphs later.

### 2. Open `WorkflowStepType` Registry (Plugin Contract for Collaboration Patterns)

Today the workflow engine has a closed set of step types (`agent`, `fan_out`, `approval`). Add an explicit registry with a **formal plugin contract** — a `Protocol` class that any collaboration pattern must implement to participate in the orchestration engine:

```python
# orchestration/plugin_contract.py
from typing import Protocol, Any
from dataclasses import dataclass

@dataclass
class StepContext:
    """Immutable context passed to every step handler."""
    run_id: str
    step_id: str
    room_id: str
    agents: list[AgentCard]           # available agents for this step
    message_history: list[Message]    # conversation context
    user_preferences: dict[str, Any]  # room-level routing prefs
    parent_invocation_id: str | None  # for trace tree nesting

@dataclass
class StepResult:
    """Typed output contract — every pattern must produce this."""
    output_text: str                  # human-readable synthesis
    structured_data: dict[str, Any]   # machine-readable output (ranked list, consensus, etc.)
    agent_invocations: list[str]      # invocation IDs created by this step
    status: Literal["completed", "failed", "needs_human_input"]

class StepHandler(Protocol):
    """The interface every collaboration pattern plugin must implement."""

    @property
    def metadata(self) -> StepHandlerMetadata:
        """Declare pattern name, version, author, required capabilities."""
        ...

    async def run(self, ctx: StepContext, params: dict[str, Any]) -> StepResult:
        """Execute the collaboration pattern. Must be idempotent for DBOS retry."""
        ...

    async def cancel(self, ctx: StepContext) -> None:
        """Handle cancellation. Called when parent workflow is cancelled."""
        ...

@dataclass
class StepHandlerMetadata:
    name: str                         # e.g. "debate", "voting", "map_reduce"
    version: str                      # semver, e.g. "1.0.0"
    author: str                       # e.g. "hybro-core" or "community/username"
    description: str
    required_agent_capabilities: list[str]  # capabilities agents must declare
    min_agents: int = 1
    max_agents: int | None = None
    params_schema: dict[str, Any] = field(default_factory=dict)  # JSON Schema for params
```

The registry itself is straightforward:

```python
# orchestration/registry.py
_step_handlers: dict[str, StepHandler] = {}

def register_step_type(name: str, handler: StepHandler) -> None:
    if not isinstance(handler.metadata, StepHandlerMetadata):
        raise TypeError(f"Handler must implement StepHandler protocol")
    _step_handlers[name] = handler

def get_step_handler(name: str) -> StepHandler:
    if name not in _step_handlers:
        raise KeyError(f"Unknown step type: {name}. Registered: {list(_step_handlers)}")
    return _step_handlers[name]

# Built-in registrations:
register_step_type("agent", AgentStepHandler())
register_step_type("fan_out", FanOutStepHandler())
register_step_type("approval", ApprovalStepHandler())

# Adding debate/voting later = one new file + one register call:
register_step_type("debate", DebateStepHandler())
```

The `StepHandler` protocol is the critical contract. It defines:
- **`StepContext`** — the immutable environment a pattern operates in (what agents are available, what the conversation history is, what room-level preferences exist)
- **`StepResult`** — the typed output every pattern must produce, enabling composability (a supervisor step can consume a debate step's `structured_data.consensus`)
- **`StepHandlerMetadata`** — self-describing metadata for discovery, validation, and future catalog UI
- **`params_schema`** — JSON Schema describing what `params` the pattern accepts, enabling WorkflowTemplate editors to render configuration UI

This is the difference between multi-agent collaboration patterns being top-level modes (current) vs. composable step types (future). The contract costs ~60 lines now and avoids a significant refactor when patterns need to be composed, discovered, or contributed externally.

### 3. Domain Event Bus Placeholder

The current architecture has no internal event bus — modules communicate by direct function calls. As modules multiply, this creates coupling. Add a no-op bus now, wire it in Phase 2, fill it out in Phase 4:

```python
# common/events.py
class DomainEventBus:
    def publish(self, event: DomainEvent) -> None:
        pass  # Phase 1: no-op. Phase 2: Redis Pub/Sub or in-process queue.

# Emit from orchestration layer (Phase 1):
event_bus.publish(AgentInvocationCompleted(invocation_id=..., run_id=...))

# Consume in billing, governance, monitoring (Phase 4+):
@event_bus.subscribe(AgentInvocationCompleted)
async def record_billable_event(event): ...
```

The interface is the investment. The implementation can start as a no-op dict and be wired to Redis Streams or Kafka later without touching callers.

### 4. `scope` Field for Multi-Tenancy

Every resource that will eventually need tenant isolation (rooms, runs, templates, agents) should have a `scope` field now, even if enforcement is a no-op today:

```python
class ExecutionRun:
    run_id: str
    scope: str  # "user:{user_id}" | "org:{org_id}" | "workspace:{ws_id}"
    ...
```

Adding this to a live schema later requires a data migration. Adding it to a new schema during Path C costs zero.

### 5. A2A Extension-Based Agent Capability Declarations (P2 — Informed by OpenClaw + A2A Spec)

OpenClaw's plugin system declares structured capabilities per plugin (supported input types, output types, interaction modes). Hybro's supervisor currently infers agent capabilities from limited fields (`capabilities.streaming`, `default_input_modes`, `default_output_modes`), which provides no structured signal about what an agent can actually do (e.g., "supports file upload," "can generate charts," "requires approval for purchases > $100").

**Do NOT extend the `AgentCard` schema directly** — this would break A2A protocol compatibility. Instead, use the A2A protocol's designed extension mechanism: `AgentCapabilities.extensions`.

Define a Hybro-specific A2A extension that agents declare in their Agent Card:

```python
# Extension URI — Hybro owns this namespace
HYBRO_CAPABILITIES_URI = "https://hybro.ai/a2a-extension/capabilities/v1"

# Example: agent declares structured capabilities in its Agent Card
agent_card = AgentCard(
    name="Financial Analyst",
    capabilities=AgentCapabilities(
        streaming=True,
        extensions=[
            AgentExtension(
                uri=HYBRO_CAPABILITIES_URI,
                description="Hybro structured capability declaration",
                required=False,  # non-Hybro consumers can ignore this
                params={
                    "interaction_modes": ["chat", "form", "background_task"],
                    "input_types": ["text", "file/csv", "file/xlsx"],
                    "output_types": ["text", "chart", "file/pdf"],
                    "cost_tier": "standard",
                    "requires_approval_above": 1000,  # dollars
                    "max_concurrent_tasks": 5,
                    "estimated_latency_ms": {"p50": 2000, "p95": 8000},
                },
            ),
        ],
    ),
    ...
)
```

The supervisor reads this extension for smarter routing:

```python
from a2a.extensions.common import find_extension_by_uri

def get_hybro_capabilities(agent_card: AgentCard) -> dict | None:
    """Extract Hybro capabilities from an agent's A2A extensions."""
    ext = find_extension_by_uri(
        agent_card.capabilities.extensions or [],
        HYBRO_CAPABILITIES_URI,
    )
    return ext.params if ext else None

async def select_agents(request: OrchestrationRequest, candidates: list[AgentCard]):
    for card in candidates:
        caps = get_hybro_capabilities(card)
        if caps:
            # Structured routing: check input_type compatibility, cost tier, latency
            if request.has_file and "file/csv" not in caps.get("input_types", []):
                continue
        else:
            # Fallback: infer from standard A2A fields
            # (capabilities.streaming, default_input_modes, skills, etc.)
            ...
```

Benefits:
- **Full A2A compatibility** — the extension is ignored by non-Hybro consumers (it's `required: False`)
- **Graceful degradation** — agents without the extension still work; supervisor falls back to inference
- **Marketplace differentiation** — agents advertising richer capabilities rank higher in recommendations
- **No schema migration** — capability data lives in the existing `extensions` field, not custom DB columns

This is a Phase 2 addition: implement when upgrading to A2A v1.0 (Phase 2 item 9), since the extension mechanism is already defined in the A2A spec.

### 6. Room-Level Orchestration Learning (P2 — Informed by Hermes Agent)

Hermes Agent's self-improving architecture learns from past interactions: it tracks which tools/agents succeeded for which types of tasks and adjusts its strategy. Hybro should apply a lightweight version at the room level.

Add a `room_strategy` field to the room model that captures learned routing preferences:

```python
class RoomStrategy(BaseModel):
    """Learned routing preferences for a room, updated after each run."""
    agent_success_rates: dict[str, float] = {}     # agent_id → success rate (0-1)
    preferred_agents: dict[str, list[str]] = {}     # task_category → [agent_ids]
    avg_latency_ms: dict[str, float] = {}           # agent_id → p50 latency
    last_updated: datetime | None = None
```

The supervisor consults `room_strategy` when scoring candidates, weighted alongside the existing multi-factor recommender. After each run, the orchestration layer updates the strategy with the run outcome.

This is a Phase 3 addition: requires enough run history per room to produce meaningful signals. The `scope` field (item 4) enables per-user/per-org strategy isolation.

### 7. User Preference Tracking (P2 — Informed by Hermes Agent)

Hermes Agent maintains a per-user preference model that influences how the agent formats responses and selects interaction strategies. Hybro's `context_memory/` module already handles conversation context, but lacks explicit preference tracking.

Add a lightweight preference store that the supervisor and agents can consult:

```python
class UserPreferences(BaseModel):
    """Learned and explicit user preferences."""
    user_id: str
    response_style: str | None = None           # "concise" | "detailed" | "technical"
    preferred_output_modes: list[str] = []       # ["text", "chart", "table"]
    timezone: str | None = None
    language: str | None = None
    custom: dict[str, Any] = {}                  # agent-specific preferences
```

Stored in MongoDB alongside user profile data. Passed to the supervisor as part of `OrchestrationRequest` context. Agents that support it can read user preferences from the A2A message metadata.

This is a Phase 3 addition: useful once room-level learning produces enough signal and user engagement patterns stabilize.

### Summary: What to Do in Phase 1–2 vs What to Wait For

| Item | When | Reason |
|---|---|---|
| `parent_invocation_id` nullable field | Phase 1 (data model) | Schema migration is cheap now, painful later |
| `StepHandler` protocol + registry | Phase 2 | Natural refactor as debate/voting get extracted to step types; formal plugin contract |
| `DomainEventBus` no-op placeholder | Phase 1 | Interface is the investment; implementation evolves |
| `scope` field on all major entities | Phase 1 (data model) | Migration cost grows with user count |
| A2A extension-based capability declarations | Phase 2 (with A2A v1.0 upgrade) | Uses protocol-native extension mechanism; enables structured agent routing |
| Migrate built-in patterns to `StepHandler` | Phase 2 | Prove the contract works with supervisor, debate, direct before opening to external |
| Room-level orchestration learning | Phase 3 | Requires run history; complements multi-factor recommender |
| User preference tracking | Phase 3 | Requires stable engagement patterns |
| Full debate/voting as composable step types | Wait for user signal | Only if users actually create templates with them |
| Python entrypoint-based pattern discovery | Phase 4 | Only after `StepHandler` contract is stable and external developers show interest |
| Pattern catalog UI / dev portal | Phase 4+ | Only after external developers are a real customer segment |
| Community pattern sandboxing / trust model | Phase 5 | Only after community patterns exist; depends on agent sandbox model |
| Observable reasoning / telemetry hooks | Phase 3 | When users ask for it |

---

## Multi-Agent Collaboration: Primitives Gap

### Current State

Multi-agent collaboration modes (supervisor, debate, voting) are **top-level orchestration strategies**, not composable primitives. The current design treats them as parallel sibling workflows:

```
OrchestratorFactory.get("supervisor") → SupervisorWorkflow
OrchestratorFactory.get("debate")     → DebateWorkflow
OrchestratorFactory.get("direct")     → DirectWorkflow
```

A `WorkflowTemplate` user **cannot** embed a debate round inside a supervisor step, or nest a supervisor inside a fan-out. The two systems (Supervisor and Workflow Engine) are not composed — they are two separate entry points.

### Why This Is Intentional for Now

At PMF stage, forcing all collaboration modes through a unified primitive model would require:
- Formal typed output contracts for each mode (what does `debate` return? a consensus string? a ranked list?)
- A UI that understands nested collaboration
- Cross-mode continuation semantics (HITL inside a debate inside a supervisor)

This is expensive to build correctly and the user demand for nested collaboration is unproven.

### The Forward Path: Collaboration Patterns as Plugins

The `StepHandler` protocol (§ Future Extensibility Foundations, item 2) is the key enabler. Once collaboration modes implement the `StepHandler` contract, they become composable step types **and** pluggable units that can be contributed externally:

```yaml
# User-defined WorkflowTemplate (future):
steps:
  - id: gather_perspectives
    type: debate              # ← collaboration mode as a step type
    agents: [researcher, analyst, critic]
    rounds: 2

  - id: final_decision
    type: supervisor          # ← orchestration mode as a step type
    depends_on: [gather_perspectives]
    agents: [decision_maker]
    context: "{{ steps.gather_perspectives.consensus }}"
```

The `parent_invocation_id` field (§ Future Extensibility Foundations, item 1) enables the trace tree needed to reason about nested collaboration.

#### Industrial Precedent

This approach is grounded in proven patterns from four systems:

| System | What's pluggable | Interface | Community contribution model |
|---|---|---|---|
| **AutoGen** (Microsoft) | `GroupChat.speaker_selection_method` — custom function controlling which agent speaks next | `(last_speaker, groupchat) → Agent` | Custom functions, not packaged plugins |
| **Apache Airflow** | Custom `Operator` classes — arbitrary execution primitives | `BaseOperator.execute(context)` | `plugins/` directory + PyPI provider packages; thousands of community operators |
| **Semantic Kernel** (Microsoft) | Pluggable planners — different orchestration strategies | `Planner` interface with custom discovery/loading | Custom planner classes |
| **CrewAI** | `Process` types (sequential, hierarchical, consensual) | Built-in enum; not yet open to external plugins | Closed set; "Flows" add composability |
| **LangGraph** | The graph definition itself is the collaboration pattern | `StateGraph` with typed state + conditional edges | Shared as code; no marketplace |

The key lesson: **every successful plugin ecosystem (Airflow, VSCode, Kubernetes operators) launched with a rich built-in library before opening to community contributions.** AutoGen keeps its interface narrow (`speaker → Agent`), which limits expressiveness but makes community patterns easy. Airflow's `BaseOperator.execute(context)` is broader but requires more documentation.

Hybro's `StepHandler` protocol sits between these — broader than AutoGen's function (supports typed results, cancellation, metadata) but narrower than a full workflow engine (operates within the DBOS execution substrate).

#### Plugin Packaging and Distribution (Phase 4+)

When the `StepHandler` contract is stable and Hybro has built 5-10 internal patterns, the packaging model opens to external contributors:

```toml
# pyproject.toml of a community-contributed pattern package
[project]
name = "hybro-pattern-map-reduce"
version = "1.0.0"

[project.entry-points."hybro.orchestration_patterns"]
map_reduce = "hybro_pattern_map_reduce:MapReduceStepHandler"
```

```python
# Discovery at startup
from importlib.metadata import entry_points

def discover_patterns():
    """Auto-discover installed pattern plugins via Python entrypoints."""
    for ep in entry_points(group="hybro.orchestration_patterns"):
        handler = ep.load()()
        register_step_type(ep.name, handler)
```

A Git-based registry (similar to Airflow provider packages) provides discoverability:

```yaml
# hybro-pattern-registry/catalog.yaml
patterns:
  - name: map_reduce
    pypi: hybro-pattern-map-reduce
    version: ">=1.0.0"
    author: community/alice
    description: "Fan-out to N agents, reduce results with configurable reducer"
    tags: [parallel, aggregation]
    verified: false  # vs. true for Hybro-reviewed patterns

  - name: tournament
    pypi: hybro-pattern-tournament
    version: ">=0.2.0"
    author: community/bob
    description: "Bracket-style elimination debate between agent pairs"
    tags: [debate, competitive]
    verified: false
```

#### Trust and Sandboxing (Phase 5+)

Community-contributed patterns run within the DBOS execution substrate, which provides:
- **Automatic retry and idempotency** — patterns that crash mid-execution are retried safely
- **Cancellation propagation** — parent workflow cancel cascades to pattern steps
- **Execution traces** — every agent invocation, LLM call, and HITL interaction is logged

Additional sandboxing for untrusted community patterns:
- **Resource limits** — max agent invocations per step, max LLM tokens per step, execution timeout
- **Capability restrictions** — untrusted patterns cannot access raw credentials, modify room state, or invoke agents outside the provided `StepContext.agents` list
- **Output validation** — `StepResult` schema validation before results propagate to downstream steps

This maps to the "Agent execution sandbox / trust model" item in § What We're Explicitly NOT Building Yet.

### What to Do Now

1. **Build the `StepHandler` protocol and registry** in Phase 2 (see above). This is the prerequisite.
2. **Implement supervisor, debate, and direct as `StepHandler` implementations** — prove the contract works by migrating existing patterns to it.
3. **Track usage of existing collaboration modes** — add analytics on which modes users actually invoke and how often. This is the signal to watch before investing in composability.
4. **Defer nested collaboration design** until at least one user explicitly asks for it or a WorkflowTemplate user tries to combine modes.
5. **Defer community plugin submission** until the `StepHandler` contract has been stable for at least two internal patterns beyond the initial three, and until there is external developer interest.

---

## Interaction Modes — A Roadmap

The architecture supports these modes without redesign:

| Mode | How It Works | When |
|---|---|---|
| **Chat (current)** | AG-UI stream from `sendMessage` | Now |
| **Background task** | DBOS workflow + notification delivery when done | Phase 2 |
| **Scheduled task** | `@DBOS.scheduled("cron")` workflow | Phase 2 |
| **Webhook-triggered** | HTTP endpoint → `DBOS.start_workflow()` | Phase 2 |
| **User-defined workflow** | `WorkflowTemplate` + `run_workflow_template()` | Phase 3 |
| **Generative UI (text)** | `STATE_SNAPSHOT/DELTA` from AG-UI | Phase 3 |
| **Generative UI (rich surfaces)** | A2UI surfaces via `emit_a2ui_surface()` → `@a2ui/react` renderer | Phase 4 |
| **HITL (structured)** | A2UI confirmation surface + `action` event routing | Phase 4 |
| **Voice** | STT preprocessing → existing chat path | Phase 3 |
| **Document canvas** | `STATE_SNAPSHOT` of document object + agent edits | Phase 4 |

---

## Migration Phasing

### Phase 1: Fix the Foundation (4–6 weeks)
*Goal: stop the bleeding, establish safe refactoring base*

1. **Replace arq with SAQ** (1 day) — arq is in maintenance-only mode; SAQ is a drop-in replacement with the same Redis-based API. SAQ runs the *same jobs* arq was running (background task processing, scheduled sweeps). It is a stopgap: SAQ itself is replaced by DBOS workflows in Phase 2, so no new jobs should be added to SAQ — they should wait for DBOS. The only thing SAQ provides over arq is that it is actively maintained.
2. **Contract tests** — write integration tests for all high-risk paths (V2 resume, HITL, cancel, hub relay) before touching any code. This is the safety net.
3. **Centralize SSE via InteractionAdapter** — stop emitting SSE from 3+ scattered locations. Single adapter. This is in PR #127 Phase 1d and is the right call.
4. **Freeze frontend contract** — document and test the AG-UI-compatible event shapes. Planning the AG-UI migration now means the contract test shapes AG-UI-compatible events from day one.
5. **Complete `BEHAVIORAL_DECISIONS.md`** — resolve all TBD items before Phase 2 code is written (see `BEHAVIORAL_DECISIONS.md §Open Questions`).

### Phase 2: DBOS Introduction (6–10 weeks)
*Goal: replace custom execution runtime with proven infrastructure*

1. **Add Postgres** — managed Postgres (Neon free tier for dev, RDS/Supabase for production).
2. **Install DBOS** — add DBOS Python SDK alongside existing code.
3. **Migrate new runs to DBOS workflows** (strangler pattern) — new `ExecutionRun`s go through DBOS; legacy paths continue on old code.
4. **Migrate HITL to `DBOS.send/recv`** — replace `HITLRequest` collection; simpler and correct.
5. **Migrate background jobs to `@DBOS.scheduled()`** — remove `StaleTaskChecker`, `CompactionSweep`, etc. from the custom Redis leader-election pattern.
6. **Migrate arq/SAQ task queue to DBOS workflows** — Redis Pub/Sub (SSE fan-out), Streams (Hub relay), and all other Redis uses remain as-is per `HORIZONTAL_SCALING_DESIGN.md`.
7. **Migrate relay offline queue to DBOS** — replace the in-memory offline queue in `relay_service.py` with a `@DBOS.workflow()` that durably waits for hub reconnect via `DBOS.recv()`. Removes the periodic sweep job and the 100-message in-memory cap.
8. **Explicit hub agent step semantics** — design `invoke_agent` DBOS step retry policy for `RELAY_DISPATCHED` status: `retries_allowed=0` + durable wait via `DBOS.recv(f"hub_response:{agent_message_id}")` instead of retry.
9. **A2A v1.0 upgrade** — consolidate the dual type-system (`common/types.py` legacy path + `a2a-sdk` path) into a single SDK-backed module. Upgrade `a2a-sdk` and `a2a-server` to latest. See `A2A_UPGRADE_ROADMAP.md` for the full change list. Phase 2 is the right time: DBOS has stabilized the execution substrate, but before Phase 3 adds AG-UI event remapping that touches the same transport layer.
10. **Supervisor tool-call refactor** — restructure `decide_next()` to use LLM tool calls for all supervisor actions (delegate, clarify, done). Keep system prompt + tool definitions as stable prefix for prompt cache optimization. See `§ Supervisor Actions as Tool Calls`.
11. **Cascading cancellation** — implement hierarchical cancel propagation from run → invocations → A2A agents/hub agents. See `§ Cascading Cancellation for Sub-Agents`.
12. **LLM failover chain** — add `LLMFailoverChain` to `llm/` module for transparent provider failover on rate limit/timeout. See `§ LLM Credential Pool and Failover Chain`.
13. **Typed relay protocol schema** — define versioned, discriminated-union `RelayMessage` types for all hub↔cloud relay communication. See `§ Gap 6: Typed Relay Protocol Schema`.
14. **Hybro A2A capabilities extension** — define `https://hybro.ai/a2a-extension/capabilities/v1` extension URI; implement `get_hybro_capabilities()` in supervisor for structured agent routing. See `§ A2A Extension-Based Agent Capability Declarations`.
15. **`StepHandler` protocol + registry** — define the formal plugin contract (`StepHandler`, `StepContext`, `StepResult`, `StepHandlerMetadata`). Migrate supervisor, debate, and direct to `StepHandler` implementations. See `§ Open WorkflowStepType Registry (Plugin Contract for Collaboration Patterns)`.

### Phase 3: AG-UI + Streaming Unification + Module Extraction (8–12 weeks)
*Goal: adopt open protocol, unify streaming persistence, complete module boundaries, add state sync*

1. **Replace custom SSE schema with AG-UI** — `InteractionAdapter` emits AG-UI events; frontend adopts AG-UI client.
2. **Add `STATE_SNAPSHOT/DELTA`** — enables generative UI, structured task dashboards, future document collaboration.
3. **Streaming path unification** — introduce Redis accumulation buffer in `invoke_agent` step; replace per-chunk MongoDB writes with a single finalized write on step completion; introduce new `messages` and `artifacts` MongoDB collections (see `PERSISTENCE_UNIFICATION_DESIGN.md §Phase 2`).
4. **Remove per-chunk persistence writes** — remove `tsm.persist_message()` from DirectTransport; remove `accumulate_artifact_on_message()` from handler path.
5. **Module directory restructuring** (PR #127 Phase 3) — now that boundaries are stable and DBOS handles execution, the directory moves are safe.
6. **`WorkflowTemplate` data model** — storable user-defined workflow graphs. No editor yet; just the backend model and execution.
7. **A2UI Phase 3 prep hooks** (no-regret, low cost) — see `§ A2UI — Phase 3 Extension Points`:
   - Add `emit_a2ui_surface()` stub on `InteractionAdapter` (raises `NotImplementedError`)
   - Add `DataPart` variant to `Message.parts` schema in MongoDB
   - Add `surface_owners: dict[str, str]` field to supervisor session model

### Phase 4: Product Expansion (when PMF signals appear)
*Build when you know what users actually want*

- **A2UI renderer integration** — install `@a2ui/react`; wire `a2ui_surface` CUSTOM event to `A2UIProvider` + `A2UIRenderer` in the chat message bubble; implement `emit_a2ui_surface()` in `InteractionAdapter`
- **A2UI action routing** — handle `a2ui_action` requests in the supervisor; route to surface-owning agent via `surface_owners` session map
- **`hybro-standard-catalog`** — define Hybro's catalog extending A2UI Basic Catalog; publish so marketplace agents can advertise support
- **A2UI HITL surfaces** — replace text-based HITL confirmation with structured A2UI approval surface
- **Drag-and-drop workflow editor** (ReactFlow + `WorkflowTemplate` backend, or A2UI custom catalog `WorkflowCanvas` component)
- **Additional interaction modes** (background tasks, scheduled, event-triggered)
- **Generative UI components** (AG-UI `STATE_SNAPSHOT` + frontend component library)
- **Open collaboration pattern plugin system** — enable Python entrypoint-based discovery (`hybro.orchestration_patterns` group); publish `StepHandler` SDK as a standalone package; add pattern catalog to Git-based registry. See `§ Plugin Packaging and Distribution`.
- **Premium service modules** (identity/SSO, governance/policy, billing/metering)
- **Hub deep integration** (offline execution, data residency model)

---

## What This Architecture Enables

### Extensibility Scenarios (vs PR #127 Appendix B)

| Scenario | This Architecture | Lines Changed |
|---|---|---|
| Add new orchestration mode | New `StepHandler` implementation + `register_step_type()` | ~100 |
| Add HITL to any workflow | `DBOS.recv()` inline + API endpoint | ~20 |
| Add scheduled agent report | `@DBOS.scheduled()` decorator | ~5 |
| Add webhook-triggered run | HTTP endpoint → `DBOS.start_workflow()` | ~20 |
| Add generative UI to any agent | `STATE_SNAPSHOT` event from InteractionAdapter | ~30 |
| Add new LLM provider | `llm/providers/new_provider.py` + failover chain | ~100 |
| Add new supervisor action | New tool definition in `SUPERVISOR_TOOLS` | ~20 |
| Add LLM provider failover | New provider in `LLMFailoverChain.providers` list | ~10 (config) |
| Declare agent capabilities | Add Hybro extension to Agent Card `capabilities.extensions` | ~15 per agent |
| Community contributes new pattern | `pip install hybro-pattern-X`; auto-discovered via entrypoint | 0 (config) |
| Compose patterns in workflow | Reference step types by name in `WorkflowTemplate` YAML | ~10 per step |
| User creates custom workflow | WorkflowTemplate CRUD + executor | New module |
| A2A v1.0 protocol upgrade | `a2a_protocol/version_adapter.py` + consolidate dual type-system | ~300 (Phase 2) |
| Scale to 10x traffic | Add instances behind LB | 0 (config) |
| Add governance module | New module consuming domain events | Additive |
| Add new agent runtime (e.g. WASM, Docker) | New transport in `AgentMessageProcessor` + `agent.source` type | ~200 |
| Durable relay offline queue | `@DBOS.workflow()` wrapping relay dispatch | ~50 (replaces ~300 sweep machinery) |
| Add privacy-aware hybrid orchestration | `local_only` flag on messages + routing in `AgentMessageProcessor` | ~150 |
| Add rich UI surface to any agent | Agent returns A2UI DataPart; `emit_a2ui_surface()` in InteractionAdapter; `@a2ui/react` renders it | ~50 backend + ~100 frontend |
| Add agent with custom UI components | Agent defines custom A2UI catalog; registers in Agent Card extension; frontend adds catalog to `@a2ui/react` registry | New catalog file + renderer registration |
| Replace text HITL with structured form | Wrap existing `DBOS.send/recv` with A2UI surface emission | ~80 |

### PMF Flexibility

The architecture deliberately avoids committing to:
- A specific interaction mode (chat is the default, not the only)
- A specific workflow paradigm (DBOS is a substrate; workflow logic is pluggable code)
- A specific set of collaboration patterns (`StepHandler` is a protocol; patterns are pluggable implementations)
- A specific frontend framework (AG-UI is protocol-level, not framework-level)
- A specific agent runtime (Hub is one transport; others are additive)

If the business pivots from "chat with agents" to "autonomous agent pipelines," the DBOS execution model and `WorkflowTemplate` authoring layer support it. If it pivots to "embedded agent copilots in enterprise apps," AG-UI state sync + the Developer Platform support it.

---

## What We're Explicitly NOT Building Yet

These are real capabilities that should wait until product direction is clearer:

- **Full data residency / privacy-preserving execution** — requires local execution plane, not just relay transport
- **Hybrid orchestration (privacy-aware task splitting)** — hub as a first-class orchestrator that splits a task between local and cloud based on sensitivity. Phase 3 MVP is a `local_only` message flag; full workflow-level routing follows. See Hub Gap 4 above.
- **Streaming token relay optimization** — the 4-hop relay path adds ~50–100ms per token. Phase 3: WebSocket or long-lived chunked HTTP from hub to relay. See Hub Gap 3 above.
- **Multi-hub coordination** — cancellation, partial failure handling, and `processing_message_id` semantics when agents from multiple hubs are in-flight simultaneously.
- **Multi-tenant governance / policy engine** — real capability; additive module when there are enterprise customers paying for it
- **Billing / metering hooks** — `AgentInvocation` records are the raw material; the billing module comes when the pricing model is stable
- **Voice / audio pipeline** — real interaction mode; additive when there's user demand
- **Observable reasoning / explainability** — AG-UI `REASONING_*` events + UI components when users ask for it
- **Cross-room memory search** (informed by Hermes Agent) — Hermes persists all conversations in SQLite with FTS5 full-text search, enabling cross-session memory retrieval. Hybro's Pinecone vector search covers semantic similarity, but lacks structured cross-room search (e.g., "what did agent X say about topic Y in any room?"). Additive: a search endpoint over the `messages` MongoDB collection with text indexes. Build when users report losing context across rooms.
- **Agent execution sandbox / trust model** (informed by Codex CLI + OpenClaw) — Codex CLI runs agent-generated shell commands in a sandboxed environment with configurable permission levels. OpenClaw applies layered policy (per-tool, per-user, per-session). Hybro currently trusts all marketplace agents equally. A trust/sandboxing model (rate limits per agent, output validation, cost caps) becomes important when third-party agents join the marketplace. Build when marketplace opens to external developers.
- **Community collaboration pattern marketplace** (informed by Airflow provider ecosystem + LobeHub) — full community submission of `StepHandler` plugins with discoverability catalog, verification pipeline, and sandboxed execution. Requires: stable `StepHandler` contract (Phase 2), 5-10 internal patterns proving the interface (Phase 3), external developer interest (Phase 4). The plugin packaging mechanism (Python entrypoints + Git-based registry) is designed in § Multi-Agent Collaboration but should not be built until Phase 4 at earliest. No industrial multi-agent framework has successfully launched a community orchestration pattern marketplace — Airflow's operator ecosystem took years and millions of users to reach critical mass.
- **A2UI renderer in frontend** — `@a2ui/react` integration; `a2ui_surface` CUSTOM event handling; `a2ui_action` routing in supervisor. Phase 3 adds the three no-regret hooks (stub method, `DataPart` schema, session field). Full integration waits for Phase 4 when there is user demand for rich interaction surfaces.
- **`hybro-standard-catalog`** — Hybro's custom A2UI catalog extending Basic Catalog. Build when agents in the marketplace actually need differentiated UI components.

---

## Decision Summary

| Decision | Choice | Rationale |
|---|---|---|
| Frontend protocol | AG-UI | Open standard, avoids custom SSE schema lock-in, unlocks state sync |
| Agent protocol | A2A (keep) | Already adopted, community momentum |
| Execution durability | DBOS (Postgres-backed) | Replaces arq (dying) + custom inbox/outbox with one open-source library |
| Task queue | DBOS workflows | arq maintenance-only; DBOS is more capable |
| HITL model | `DBOS.send/recv` | 5 lines vs 400-line spec |
| Supervisor decision model | Tool calls (not free-text) | Structured output, prompt cache stability, auditability (Codex CLI + Claude Code pattern) |
| LLM failover | Credential pool with provider chain | Transparent failover on rate limit/timeout (Hermes + OpenClaw pattern) |
| Cancellation model | Hierarchical cascading | Parent cancel propagates to all in-flight invocations with grace period (Claude Code pattern) |
| Agent capability declaration | A2A `extensions` field | Protocol-native; no schema changes; graceful degradation for non-Hybro consumers |
| Relay protocol | Typed discriminated-union schema | Version negotiation, exhaustive handling, backward compatible (OpenClaw pattern) |
| Collaboration pattern model | `StepHandler` plugin protocol | Formal contract enables composability, internal reuse, and future community contribution (Airflow + AutoGen pattern) |
| SSE fan-out | Redis Pub/Sub (unchanged) | Built for this; better latency than Postgres LISTEN/NOTIFY |
| Hub relay | Redis Streams (unchanged) | Already designed; not worth changing |
| Postgres scope | DBOS execution only | One store per concern; avoids Postgres fan-out bottleneck |
| Redis scope | SSE fan-out · Hub relay · cache · rate limiting | Keeps everything Redis is genuinely better at |
| Workflow authoring | `WorkflowTemplate` + DBOS executor | Enables drag-and-drop editor; built in Phase 3 |
| Hub/relay architecture | Keep as-is (already shipped) | Portal-first hybrid is correct; Phases 1–2c complete |
| Hub offline queue | Migrate to DBOS `@workflow()` in Phase 2 | Replaces fragile in-memory queue + sweep job |
| Hub streaming latency | WebSocket relay in Phase 3 | 4-hop relay adds ~50–100ms/token; fixable |
| Hybrid orchestration | `local_only` flag MVP in Phase 3 | Hub cannot split tasks today; full solution deferred |
| Module architecture | PR #127 boundaries (keep) | The module decomposition is correct |
| Migration strategy | Strangler pattern (keep) | PR #127's approach is right |
| What to build now | Phase 1 only | Pre-PMF; avoid over-engineering |
|| Persistence model | Three-layer (DBOS + Redis buffer + MongoDB) | See `PERSISTENCE_UNIFICATION_DESIGN.md` |
| Generative UI format | A2UI (Phase 4, hooks in Phase 3) | Declarative surfaces over A2A DataParts; `@a2ui/react` renderer; avoids per-agent custom UI code |
