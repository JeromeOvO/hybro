# Hybro Recommended Architecture
> Synthesized from design reviews, industry research, and PMF-stage strategic considerations.
> Last updated: March 2026

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

The industry has converged on a three-layer agent protocol stack. Hybro should be a **consumer and integrator** of the bottom two layers, and own the top one:

```
┌─────────────────────────────────────────────────────────┐
│                     AG-UI Protocol                      │
│         Agents ↔ User-facing applications               │
│   (adopt this — don't reinvent your SSE schema)         │
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
│   Chat · Workflow Editor · Task Dashboard · State Sync UI    │
└──────────────────────────┬───────────────────────────────────┘
                           │  AG-UI events (SSE / WebSocket)
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
└──────────────────────────────┬────────────────────────────────┘
                               │
┌──────────────────────────────▼────────────────────────────────┐
│                   A2A Protocol Adapter                        │
│           Direct · Relay (Hub) · Webhook transports           │
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
                results = await asyncio.gather(*[
                    invoke_agent(run_id, agent_id, request)
                    for agent_id in targets
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
async def invoke_agent(run_id: str, agent_id: str, request: OrchestrationRequest):
    # Each agent invocation is a durable, retryable step
    # If this step fails and retries, it won't re-run completed work
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
| Streaming token latency (relay overhead) | ⚠️ Known issue | Phase 3 fix |
| Hybrid orchestration (privacy-aware routing) | ❌ Not implemented | Phase 3 |
| Multi-hub coordination | ❌ Not designed | Phase 3 / Enterprise |

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
  workflows/                  ← One file per workflow mode (pluggable)
    supervisor.py             ← @DBOS.workflow() supervisor loop
    debate.py                 ← @DBOS.workflow() debate mode
    direct.py                 ← @DBOS.workflow() direct dispatch
    # Adding new mode = new file here + factory registration
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
llm/                          ← LLM Gateway (unchanged from PR #127)
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

### 2. Open `WorkflowStepType` Registry

Today the workflow engine has a closed set of step types (`agent`, `fan_out`, `approval`). Add an explicit registry instead of an enum:

```python
# registry.py
_step_handlers: dict[str, StepHandler] = {}

def register_step_type(name: str, handler: StepHandler) -> None:
    _step_handlers[name] = handler

# Built-in registrations (supervisor.py, fan_out.py, etc.):
register_step_type("agent", AgentStepHandler())
register_step_type("fan_out", FanOutStepHandler())
register_step_type("approval", ApprovalStepHandler())

# Adding debate/voting later = one new file + one register call:
register_step_type("debate", DebateStepHandler())
```

This is the difference between multi-agent collaboration patterns being top-level modes (current) vs. composable step types (future). The registry costs ~20 lines now and avoids a significant refactor when debate/voting need to be embedded inside a `WorkflowTemplate`.

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

### Summary: What to Do in Phase 1–2 vs What to Wait For

| Item | When | Reason |
|---|---|---|
| `parent_invocation_id` nullable field | Phase 1 (data model) | Schema migration is cheap now, painful later |
| Open `WorkflowStepType` registry | Phase 2 | Natural refactor as debate/voting get extracted to workflow steps |
| `DomainEventBus` no-op placeholder | Phase 1 | Interface is the investment; implementation evolves |
| `scope` field on all major entities | Phase 1 (data model) | Migration cost grows with user count |
| Full debate/voting as composable step types | Wait for user signal | Only if users actually create templates with them |
| Plugin registry UI / dev portal | Phase 4 | Only after external developers are a real customer segment |
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

### The Forward Path (When Signal Appears)

The `WorkflowStepType` registry (§ Future Extensibility Foundations, item 2) is the key enabler. Once collaboration modes are registered as step types, they become composable:

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

### What to Do Now

1. **Build the `WorkflowStepType` registry** in Phase 2 (see above). This is the prerequisite.
2. **Track usage of existing collaboration modes** — add analytics on which modes users actually invoke and how often. This is the signal to watch before investing in composability.
3. **Defer nested collaboration design** until at least one user explicitly asks for it or a WorkflowTemplate user tries to combine modes.

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
| **Generative UI** | `STATE_SNAPSHOT/DELTA` from AG-UI | Phase 3 |
| **Voice** | STT preprocessing → existing chat path | Phase 3 |
| **Document canvas** | `STATE_SNAPSHOT` of document object + agent edits | Phase 4 |

---

## Migration Phasing

### Phase 1: Fix the Foundation (4–6 weeks)
*Goal: stop the bleeding, establish safe refactoring base*

1. **Replace arq with SAQ** (1 day) — arq is in maintenance-only mode; SAQ is a drop-in replacement with the same Redis-based API. This is a prerequisite before any other work.
2. **Contract tests** — write integration tests for all high-risk paths (V2 resume, HITL, cancel, hub relay) before touching any code. This is the safety net.
3. **Centralize SSE via InteractionAdapter** — stop emitting SSE from 3+ scattered locations. Single adapter. This is in PR #127 Phase 1d and is the right call.
4. **Freeze frontend contract** — document and test the AG-UI-compatible event shapes. Planning the AG-UI migration now means the contract test shapes AG-UI-compatible events from day one.

### Phase 2: DBOS Introduction (6–10 weeks)
*Goal: replace custom execution runtime with proven infrastructure*

1. **Add Postgres** — managed Postgres (Neon free tier for dev, RDS/Supabase for production).
2. **Install DBOS** — add DBOS Python SDK alongside existing code.
3. **Migrate new runs to DBOS workflows** (strangler pattern) — new `ExecutionRun`s go through DBOS; legacy paths continue on old code.
4. **Migrate HITL to `DBOS.send/recv`** — replace `HITLRequest` collection; simpler and correct.
5. **Migrate background jobs to `@DBOS.scheduled()`** — remove `StaleTaskChecker`, `CompactionSweep`, etc. from the custom Redis leader-election pattern.
6. **Migrate arq task queue to DBOS workflows** — Redis Pub/Sub (SSE fan-out), Streams (Hub relay), and all other Redis uses remain as-is per `HORIZONTAL_SCALING_DESIGN.md`.
7. **Migrate relay offline queue to DBOS** — replace the in-memory offline queue in `relay_service.py` with a `@DBOS.workflow()` that durably waits for hub reconnect via `DBOS.recv()`. Removes the periodic sweep job and the 100-message in-memory cap.
8. **Explicit hub agent step semantics** — design `invoke_agent` DBOS step retry policy for `RELAY_DISPATCHED` status: `retries_allowed=0` + durable wait via `DBOS.recv(f"hub_response:{agent_message_id}")` instead of retry.

### Phase 3: AG-UI + Streaming Unification + Module Extraction (8–12 weeks)
*Goal: adopt open protocol, unify streaming persistence, complete module boundaries, add state sync*

1. **Replace custom SSE schema with AG-UI** — `InteractionAdapter` emits AG-UI events; frontend adopts AG-UI client.
2. **Add `STATE_SNAPSHOT/DELTA`** — enables generative UI, structured task dashboards, future document collaboration.
3. **Streaming path unification** — introduce Redis accumulation buffer in `invoke_agent` step; replace per-chunk MongoDB writes with a single finalized write on step completion; introduce new `messages` and `artifacts` MongoDB collections (see `PERSISTENCE_UNIFICATION_DESIGN.md §Phase 2`).
4. **Remove per-chunk persistence writes** — remove `tsm.persist_message()` from DirectTransport; remove `accumulate_artifact_on_message()` from handler path.
5. **Module directory restructuring** (PR #127 Phase 3) — now that boundaries are stable and DBOS handles execution, the directory moves are safe.
6. **`WorkflowTemplate` data model** — storable user-defined workflow graphs. No editor yet; just the backend model and execution.

### Phase 4: Product Expansion (when PMF signals appear)
*Build when you know what users actually want*

- Drag-and-drop workflow editor (ReactFlow + `WorkflowTemplate` backend)
- Additional interaction modes (background tasks, scheduled, event-triggered)
- Generative UI components (AG-UI `STATE_SNAPSHOT` + frontend component library)
- Premium service modules (identity/SSO, governance/policy, billing/metering)
- Hub deep integration (offline execution, data residency model)

---

## What This Architecture Enables

### Extensibility Scenarios (vs PR #127 Appendix B)

| Scenario | This Architecture | Lines Changed |
|---|---|---|
| Add new orchestration mode | New `@DBOS.workflow()` + factory.register | ~100 |
| Add HITL to any workflow | `DBOS.recv()` inline + API endpoint | ~20 |
| Add scheduled agent report | `@DBOS.scheduled()` decorator | ~5 |
| Add webhook-triggered run | HTTP endpoint → `DBOS.start_workflow()` | ~20 |
| Add generative UI to any agent | `STATE_SNAPSHOT` event from InteractionAdapter | ~30 |
| Add new LLM provider | `llm/providers/new_provider.py` + router | ~100 |
| User creates custom workflow | WorkflowTemplate CRUD + executor | New module |
| A2A v1.0 protocol upgrade | `a2a_protocol/version_adapter.py` | ~300 |
| Scale to 10x traffic | Add instances behind LB | 0 (config) |
| Add governance module | New module consuming domain events | Additive |
| Add new agent runtime (e.g. WASM, Docker) | New transport in `AgentMessageProcessor` + `agent.source` type | ~200 |
| Durable relay offline queue | `@DBOS.workflow()` wrapping relay dispatch | ~50 (replaces ~300 sweep machinery) |
| Add privacy-aware hybrid orchestration | `local_only` flag on messages + routing in `AgentMessageProcessor` | ~150 |

### PMF Flexibility

The architecture deliberately avoids committing to:
- A specific interaction mode (chat is the default, not the only)
- A specific workflow paradigm (DBOS is a substrate; workflow logic is pluggable code)
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

---

## Decision Summary

| Decision | Choice | Rationale |
|---|---|---|
| Frontend protocol | AG-UI | Open standard, avoids custom SSE schema lock-in, unlocks state sync |
| Agent protocol | A2A (keep) | Already adopted, community momentum |
| Execution durability | DBOS (Postgres-backed) | Replaces arq (dying) + custom inbox/outbox with one open-source library |
| Task queue | DBOS workflows | arq maintenance-only; DBOS is more capable |
| HITL model | `DBOS.send/recv` | 5 lines vs 400-line spec |
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
