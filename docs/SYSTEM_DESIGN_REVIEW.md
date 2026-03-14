# System Design Review: Multi-Agents Backend

**Date**: March 9, 2026 (updated from Feb 25; Hub/HITL/Backend overhaul pass Mar 9)
**Scope**: Backend architecture review for `hybro-multi-agents-backend`

> **Frontend review**: See `hybro-frontend/docs/FRONTEND_DESIGN_REVIEW.md` for frontend-specific issues and architecture.

---

## 1. Architecture Overview

### Backend (`multi-agents-backend`)

| Layer            | Technology                               |
| ---------------- | ---------------------------------------- |
| Framework        | FastAPI (async Python, Uvicorn)           |
| Database         | MongoDB (Motor async driver)              |
| Vector DB        | Pinecone (agent matching + memory search) |
| Auth             | Clerk JWT + API Key (SHA-256)             |
| Agent Protocol   | A2A (Agent-to-Agent) via `a2a-sdk`        |
| Real-time        | SSE via `sse-starlette`                   |
| LLMs             | OpenAI (GPT-5-mini), Google Gemini 2.0, AWS Bedrock (Claude Opus 4.6) |
| Hub/Relay        | Hybro Hub A2A relay (in-process SSE, heartbeat, offline queue) |
| Config           | pydantic-settings                         |
| Observability    | OpenTelemetry + Loguru                    |
| Caching/TTL      | `cachetools.TTLCache`                     |

### Core Data Flow

```
POST /api/v1/roomCenter/sendMessage
    ├─→ Creates user message + N agent messages in DB
    └─→ background_tasks.add_task(process_room_user_message)
            ├─→ V2 Supervisor path (if room.extend_info.use_supervisor)
            │       └─→ SupervisorExecutor.run() → decide → dispatch → synthesis
            └─→ V1 Legacy path (QueueExecutor)
                    └─→ Sequential agent processing + coordinator summary

SSE stream delivers events to connected clients:
    ├─→ task_submitted   (agent task created)
    ├─→ task_update      (working → completed/failed/input-required)
    ├─→ agent_token      (streaming token chunks)
    ├─→ artifact_update  (multimodal content chunks)
    ├─→ hitl_input_requested / hitl_status_update (HITL lifecycle)
    └─→ processing_status=completed (all agents done)
```

### Hub & Gateway Architecture

The platform now supports **Hybro Hubs** — self-hosted agent runtime environments that connect to the cloud backend via a relay service:

```
External Client (Python SDK)
    └─→ POST /api/v1/gateway/sendTask (API key auth)
        └─→ GatewayService resolves agent → dispatch middleware
            ├─→ Cloud agent: direct A2A call
            └─→ Hub agent: RelayService SSE queue → Hub polls events
                └─→ Hub processes task, publishes result via POST /api/v1/relay/publish

Hub ←→ Backend:
    ├─→ GET /api/v1/relay/events (long-lived SSE, heartbeat every 30s)
    ├─→ POST /api/v1/relay/publish (task results back to backend)
    └─→ POST /api/v1/hub/sync (agent catalog sync)
```

- **Gateway API** (`api/gateway.py`): External agent access via API keys with MongoDB-backed sliding-window rate limiting
- **Relay Service** (`services/relay_service.py`): In-memory `asyncio.Queue` per hub, offline queue with TTL for brief disconnects
- **Trust Layer**: Hub agents are sandboxed; see [HYBRO_TRUST_LAYER_DESIGN.md](./HYBRO_TRUST_LAYER_DESIGN.md)
- **Full design**: [GATEWAY_API.md](./GATEWAY_API.md), [HYBRO_HUB_DESIGN.md](./HYBRO_HUB_DESIGN.md)

---

## 2. Identified Issues & Risks

### 2.1 CRITICAL: SSE State is In-Memory Only — No Horizontal Scaling

**Location**: `services/sse_services.py` (`SSEManager`)

The `SSEManager` is a process-level singleton holding all SSE connections in a plain Python dict:

```python
class SSEManager:
    def __init__(self):
        self.room_connections: dict[str, dict[str, SSEConnection]] = {}
        self.lock = asyncio.Lock()
        self.cancelled_messages: set[str] = set()
```

**Impact**:
- Cannot deploy multiple backend instances behind a load balancer. An agent response processed by Instance A cannot broadcast SSE events to a client connected to Instance B.
- A single-process failure drops all SSE connections. Clients reconnect via `EventSource` auto-reconnect, but any in-flight events emitted during the disconnect window are permanently lost.
- The `cancelled_messages` set is also in-memory; while the MongoDB change stream propagates cancellations cross-instance, the core `room_connections` dict has no such mechanism.

**Downstream dependency**: The HITL (Human-in-the-Loop) design (`HITL_DESIGN.md`) adds new SSE event types (`hitl_input_requested`, `hitl_status_update`) and relies on SSE for the full HITL interaction lifecycle. In a multi-instance deployment, HITL prompts may never reach users if the request is created on a different instance than the user's SSE connection. HITL should either block on this fix or implement a polling fallback.

**Partial Mitigation (Mar 9)**: A MongoDB Change Stream now propagates **cancellation events** cross-instance (`sse_services.py` lines 457-596) with exponential backoff, resume token persistence, and health flag reporting. However, this only covers the `cancelled_messages` collection. Core SSE event fan-out for room messages, agent tokens, HITL prompts, and task updates still requires a message broker.

**Recommendation**: Introduce Redis Pub/Sub, NATS, or a similar message broker for cross-instance SSE event fan-out. Each backend instance subscribes to room-level channels and relays events to its local SSE connections. This decouples event production from event delivery.

---

### 2.2 CRITICAL: No Durable Task Queue — All Background Work is In-Process

**Location**: `api/orchestration_center.py`, `modules/RoomMessageCenter.py`, `jobs/stale_task_checker.py`

All background processing uses FastAPI's `BackgroundTasks` (in-process) and `asyncio.create_task`:

```python
# orchestration_center.py
background_tasks.add_task(
    room_message_center.process_room_user_message, orchestration_request
)
```

**Impact**:
- **Process crash = lost work**: If the process dies mid-processing, the work is gone. The `StaleTaskChecker` recovers orphaned messages, but only after a 2-minute delay, and with potential duplicate processing.
- **No backpressure**: There is no queue depth limit. A burst of user messages creates unbounded concurrent background tasks. With sequential agent processing per message and 120-second polling timeouts, tasks can pile up without bound.
- **No retry guarantees**: `BackgroundTasks` does not retry on failure. A transient agent error means permanent failure for that message queue.
- **Event loop starvation**: Long-running agent polling (`_poll_task_until_complete` with 120s timeout) runs on the main asyncio event loop. Multiple concurrent polls can starve HTTP request handling.

**Recommendation**: Introduce a durable task queue (Celery + Redis, Dramatiq, or arq). This provides work persistence across restarts, configurable retries, backpressure via queue depth limits, and the ability to run workers on separate processes/machines.

---

### 2.3 ~~HIGH~~ ~~PARTIAL~~ RESOLVED: httpx Client Leak — All Methods Now Properly Close Connections

**Location**: `services/a2a_service.py` (`A2AService.create_a2a_client`, `get_agent_card_from_url`)

**Previous state**: Every A2A interaction created a new `httpx.AsyncClient` with a 600-second timeout that was never explicitly closed.

**Current state (Mar 13)**: All methods now properly manage httpx client lifecycle:

- `create_a2a_client()` — `@asynccontextmanager` with `await httpx_client.aclose()` in `finally`. Used by `send_message_streaming`, `send_message_sync`, and `cancel_task` via `async with self.create_a2a_client()`. `reply_to_task()` uses its own scoped `async with httpx.AsyncClient()` (skips agent card resolution since it already has the URL).
- `get_agent_card_from_url()` — now uses `async with httpx.AsyncClient()` so the transport is closed after the card fetch completes.
- `get_a2a_client()` — **removed** (dead code with zero callers).

**Downstream dependency**: The HITL `reply_to_task()` method properly uses `async with httpx.AsyncClient()` for cleanup — this blocker is resolved.

---

### 2.4 HIGH: Sequential Agent Processing — Unnecessary Latency

**Location**: `modules/RoomMessageCenter.py` (`_process_agent_message_queue`)

The agent message queue processes agents strictly one at a time:

```python
while len(message_queue) > 0:
    current_message = message_queue.popleft()
    # ... process one agent, wait for response, then next
```

**Impact**:
- If a room has 5 agents and each takes 30 seconds, the user waits 2.5 minutes.
- For non-push agents, the 120-second polling timeout makes worst case 120s × N agents.
- Agents that are independent (no cross-dependencies) gain nothing from sequential execution.

**Recommendation**:
- Introduce **parallel execution** for independent agents using `asyncio.gather` or `asyncio.TaskGroup`.
- Keep sequential execution only for agents with explicit data dependencies (e.g., debate mode where Agent B needs Agent A's output).
- The `step_number` / `total_steps` metadata already exists on messages — use it to identify dependency chains vs. independent tasks.

---

### 2.5 ~~HIGH~~ RESOLVED: Potential Double-Processing Race Condition

**Location**: `api/room_center.py` (`sendMessage`), `api/orchestration_center.py` (`processRoomUserMessage`)

**Status**: ✅ **RESOLVED**

**Resolution (Mar 13)**:
- Legacy `processRoomUserMessage` endpoint now returns HTTP 410 Gone (deprecated).
- Atomic idempotency guard via `processing_claimed_at` field on `RoomUserMessage`. Normal path uses `claim_user_message_for_processing` (only claims unclaimed). Recovery path uses `claim_or_reclaim_user_message` (claims unclaimed or stale >30min).
- CAS release for room-level `processing_message_id` (only clears if it matches the completing message).
- Migration script: `database/migration/add_user_message_id_unique_index.py` (must run before deploying claim logic).

---

### 2.6 HIGH: JWT Token Exposed in SSE Query Parameter

**Location**: `api/sse.py` (`get_current_user_with_query_token`), frontend `sse.ts`

The SSE endpoint accepts the Clerk JWT as a URL query parameter because `EventSource` cannot send custom HTTP headers:

```
GET /api/v1/sse/room/{roomId}/stream?token=<clerk-jwt>
```

**Impact**:
- Tokens appear in **server access logs**, **CDN/proxy logs**, and can be cached by intermediate proxies.
- If an attacker obtains the URL, they can replay the SSE connection and receive all room events.

**Recommendation**:
- Add a backend endpoint (e.g., `POST /api/v1/sse/token`) that exchanges a Clerk JWT for a **short-lived, single-use SSE nonce** (30-second TTL). The SSE stream then validates the nonce instead of the raw JWT.
- At minimum, ensure server logs redact the `token` query parameter.

> **Frontend side**: See `hybro-frontend/docs/FRONTEND_DESIGN_REVIEW.md` §2.1 and `hybro-frontend/docs/architecture.md` §15.2 for the client-side perspective.

---

### 2.7 ~~MEDIUM~~ RESOLVED: Unbounded Memory Growth — Cancelled Messages Set

**Location**: `services/sse_services.py` (`SSEManager.cancelled_messages`)

**Previous state**: `self.cancelled_messages: set[str] = set()` — unbounded set.

**Current state (Feb 25)**: Migrated to `cachetools.TTLCache`:

```python
self.cancelled_messages: TTLCache[str, bool] = TTLCache(maxsize=10_000, ttl=3600)
self._terminal_status_sent: TTLCache[str, str] = TTLCache(maxsize=10_000, ttl=300)
self._cancellation_tokens: TTLCache[str, CancellationToken] = TTLCache(maxsize=10_000, ttl=3600)
```

All three in-memory stores now have TTL-based auto-eviction (1 hour for cancellation data, 5 minutes for terminal status dedup) and capped sizes. `CancellationToken` objects are also TTL-managed.

**Status**: ✅ **RESOLVED**

---

### 2.8 MEDIUM: No MongoDB Transactions for Multi-Step Operations

**Location**: `api/room_center.py` (`sendMessage` endpoint), `services/room_services.py`

The `SendMessage` flow creates a user message plus N agent messages across separate MongoDB write operations with no transaction wrapping:

1. Insert user message document
2. Insert N agent message documents (one per target agent)
3. Trigger background processing

**Impact**:
- If the process crashes between step 1 and step 2, an orphaned user message exists with no associated agent messages, and no agents will process it.
- The `StaleTaskChecker` recovers orphaned **agent messages** but does not handle the case of **missing** agent messages.
- Similarly, if the process crashes between step 2 and step 3, agent messages exist but processing never starts (the orphan recovery handles this case, but only after a delay).

**Recommendation**: Use MongoDB multi-document transactions (available with replica sets, which are already required for change streams) to make the user message + agent messages creation atomic.

---

### 2.9 MEDIUM: Frontend Optimistic Update ID Mismatch Window

> **This is a frontend-only issue.** See `hybro-frontend/docs/FRONTEND_DESIGN_REVIEW.md` §2.2 for full details.

**Summary**: If the SSE `user_message` event arrives before the frontend replaces its optimistic temp ID with the real server-assigned ID, duplicate user messages briefly appear. The backend could help by supporting a `client_request_id` field on `sendMessage` that is echoed in the SSE `user_message` event, allowing the frontend to correlate without relying on timing.

---

### 2.10 ~~MEDIUM~~ RESOLVED: No Input Validation or Size Limits on User Messages

**Location**: `api/room_center.py` (`sendMessage` endpoint)

**Status**: ✅ **RESOLVED**

**Resolution (Mar 13)**:
- Added `MAX_MESSAGE_LENGTH = 10_000` constant in `models/room.py`.
- Service-level `_check_message_text_length()` validation in `room_services.py`, wired into both `_validate_send_message_request()` and `create_and_parse_user_message()` paths.
- Returns clean 400 error for oversized messages.

---

### 2.11 MEDIUM: Conversation Memory Grows Without Bound

**Location**: `services/memory_service.py`, `database/mongodb.py` (`room_memories` collection)

Room memory accumulates the full conversation history. As rooms have longer conversations, the context passed to agents grows indefinitely.

**Impact**:
- Increasing LLM token costs and response latency.
- Risk of hitting MongoDB's 16MB document size limit for very long conversations.
- Degraded agent response quality as context windows overflow and early messages are silently truncated by the LLM.

**Recommendation**: Implement a sliding-window strategy (keep last N turns) combined with periodic summarization. The `MemoryContent` model already has a `summary` field — ensure it is actively used to replace older conversation history.

> **Cross-reference**: See [CONTEXT_MEMORY_SYSTEM_DESIGN.md](./CONTEXT_MEMORY_SYSTEM_DESIGN.md) for the comprehensive memory architecture design, including lossless compaction (§6), rolling room summaries (§4.2), and token budget strategies (§5.2).

---

### 2.12 ~~LOW-MEDIUM~~ RESOLVED: Overly Permissive CORS Configuration

**Location**: `main.py`

**Status**: ✅ **RESOLVED**

**Resolution (Mar 13)**:
Main CORS now uses explicit allow lists:
```python
allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
allow_headers=["Authorization", "Content-Type", "X-API-Key", "Cache-Control", "sentry-trace", "baggage"]
```

**Note**: `DiscoveryCORSMiddleware` for `/api/v1/discovery/*`, `/api/v1/gateway/*`, and `/api/v1/relay/*` remains intentionally permissive for external API/Hub access, protected by API key auth and rate limiting.

---

### 2.13 ~~LOW~~ RESOLVED: Stale Task Checker Creates Unbounded Background Tasks

**Location**: `jobs/stale_task_checker.py` (`_recover_orphaned_messages`)

**Status**: ✅ **RESOLVED**

**Resolution (Mar 13)**:
- Added `MAX_CONCURRENT_RECOVERIES = 5` and `_recovery_semaphore = asyncio.Semaphore(5)` to `StaleTaskChecker`.
- Both `_recover_orphaned_messages` and `_recover_stuck_supervisor_trajectories` now acquire the semaphore before `create_task`, with guarded wrappers that release in `finally`. The scheduling loop blocks when all slots are full, providing natural backpressure.

---

### 2.14 LOW: No Circuit Breaker for External Agent Calls

**Location**: `services/a2a_service.py`, `modules/RoomMessageCenter.py`

When an external A2A agent is down or slow, every user message targeting that agent waits up to 600 seconds (the httpx timeout) before failing. There is no circuit breaker to quickly fail requests after detecting repeated failures from the same agent.

**Impact**:
- A single unresponsive agent can block message processing for all users in rooms that include that agent.
- The `AgentHealthService` runs periodic health checks and marks agents as `unreachable`, but it is unclear whether the processing pipeline actually skips unreachable agents before attempting to call them.

**Recommendation**:
- Implement a circuit breaker pattern (e.g., using `aiobreaker` or a simple in-memory state machine) per agent URL.
- Check agent health status in `_process_single_agent_message` before making the A2A call, and skip/fail-fast for agents marked as unreachable.

---

### 2.15 MEDIUM: Hub Relay Single-Point-of-Failure — In-Memory SSE Queue

**Location**: `services/relay_service.py`

The relay service holds per-hub SSE connections in an in-memory `asyncio.Queue` dict:

```python
self._hub_queues: dict[str, asyncio.Queue] = {}
self._offline_queues: dict[str, deque] = {}  # bounded deque with TTL
```

**Impact**:
- If the relay instance goes down, all hub agent connections drop. Hubs must reconnect to a (potentially different) instance, losing any in-flight events.
- The offline queue (bounded deque with TTL) mitigates brief outages by buffering events, but extended downtime permanently loses events.
- Cannot horizontally scale relay — a hub is bound to one backend instance via its SSE connection.

**Recommendation**: Share relay state via Redis Streams or a database-backed queue for hub events. Alternatively, implement hub-side reconnect with event replay using monotonic sequence numbers.

---

### 2.16 ~~LOW-MEDIUM~~ RESOLVED: Remaining httpx Leak in Agent Card Fetching

**Location**: `services/a2a_service.py` (`get_agent_card_from_url`; `get_a2a_client` removed)

**Previous state**: While `create_a2a_client()` was fixed (see 2.3), two methods still created `httpx.AsyncClient` instances without closing them.

**Current state (Mar 13)**: Both methods are resolved:
- `get_agent_card_from_url()` — now uses `async with httpx.AsyncClient()` for proper cleanup.
- `get_a2a_client()` — **removed** as dead code (zero callers).

---

## 3. Summary

| #    | Severity   | Issue                                            | Impact                     |
| ---- | ---------- | ------------------------------------------------ | -------------------------- |
| 2.1  | Critical   | In-memory SSE state prevents horizontal scaling   | Cannot scale backend       |
| 2.2  | Critical   | No durable task queue, all in-process             | Lost work on crash         |
| 2.3  | ~~High~~   | httpx client leak — largely fixed, 2 methods remain | Mostly resolved            |
| 2.4  | High       | Sequential agent processing                       | Poor latency (V1 only)    |
| 2.5  | High       | Potential double-processing race condition         | Duplicate agent calls      |
| 2.6  | High       | JWT token in SSE query parameter                   | Token exposure             |
| 2.7  | ~~Medium~~ | ~~Unbounded `cancelled_messages` set~~             | ✅ Resolved (TTLCache)     |
| 2.8  | Medium     | No MongoDB transactions for multi-step ops         | Inconsistent state         |
| 2.9  | Medium     | Optimistic update ID mismatch window               | Duplicate UI messages      |
| 2.10 | Medium     | No message size validation                         | DoS / OOM risk             |
| 2.11 | ~~Medium~~ | ~~Unbounded conversation memory~~                  | ✅ Resolved (CM Phases 1–5)|
| 2.12 | Low-Medium | Overly permissive CORS                             | Attack surface             |
| 2.13 | Low        | Unbounded orphan recovery tasks                    | Event loop saturation      |
| 2.14 | Low        | No circuit breaker for external agents             | Cascading failures         |
| 2.15 | Medium     | Hub relay in-memory SSE queue (SPOF)               | Hub disconnect on crash    |
| 2.16 | ~~Low-Medium~~ | ~~`get_agent_card_from_url`/`get_a2a_client` httpx leak~~ | ✅ Resolved            |

### Priority Recommendations

**Phase 1 — Production Blockers** (Issues 2.1, 2.2):
- Add Redis Pub/Sub for SSE event fan-out across instances.
- Introduce a durable task queue (Celery/Dramatiq/arq) for agent message processing.

**Phase 2 — Reliability** (Issues 2.3, 2.5, 2.6, 2.8):
- ~~Complete httpx client lifecycle fix (2 remaining methods: `get_agent_card_from_url`, `get_a2a_client`).~~ ✅ Resolved
- Remove duplicate `processRoomUserMessage` call or add backend idempotency.
- Replace SSE JWT query param with short-lived nonce.
- Wrap multi-document writes in MongoDB transactions.

**Phase 3 — Performance & Scalability** (Issues 2.4, 2.10, 2.14, 2.15):
- Parallelize independent agent execution.
- Add message size limits.
- Add circuit breakers for external agent calls.
- Add cross-instance relay for Hub HA (2.15).

**Phase 4 — Hardening** (Issues 2.9, 2.12, 2.13, 2.16):
- ~~TTL-based cleanup for cancellation set.~~ ✅ Resolved
- Improve optimistic update deduplication.
- Tighten CORS configuration.
- Cap concurrent orphan recovery tasks.
- ~~Fix remaining httpx leak in card fetching (2.16).~~ ✅ Resolved

---

## 4. Issue Status Tracking

This section tracks the resolution status of each identified issue. Updated as fixes are implemented.

| #    | Issue                                            | Status       | Resolution Notes                                                                 |
| ---- | ------------------------------------------------ | ------------ | -------------------------------------------------------------------------------- |
| 2.1  | In-memory SSE state prevents horizontal scaling   | 🔴 Open      | **Blocker for HITL** — MongoDB Change Stream covers cancellation cross-instance; core SSE fan-out still needs Redis/NATS |
| 2.2  | No durable task queue, all in-process             | 🔴 Open      | Production blocker; no work started                                              |
| 2.3  | httpx client leak (connections never closed)       | 🟢 Resolved  | All methods fixed: `create_a2a_client()` uses `@asynccontextmanager`; `get_agent_card_from_url()` uses `async with`; `get_a2a_client()` removed (dead code). |
| 2.4  | Sequential agent processing                       | 🟡 Partial   | V2 Supervisor supports parallel dispatch via `asyncio.gather`; V1 queue still sequential |
| 2.5  | Potential double-processing race condition         | 🟡 Partial   | `sendMessage` now auto-triggers processing; legacy `processRoomUserMessage` endpoint still exists |
| 2.6  | JWT token in SSE query parameter                   | 🔴 Open      | Security risk; no work started                                                   |
| 2.7  | Unbounded `cancelled_messages` set                 | 🟢 Resolved  | Migrated to `cachetools.TTLCache(maxsize=10_000, ttl=3600)` + CancellationToken TTL cache |
| 2.8  | No MongoDB transactions for multi-step ops         | 🔴 Open      | Consistency risk; no work started                                                |
| 2.9  | Optimistic update ID mismatch window               | 🔴 Open      | Frontend deduplication issue; no work started                                    |
| 2.10 | No message size validation                         | 🔴 Open      | DoS risk; no validation implemented                                              |
| 2.11 | Unbounded conversation memory                      | 🟢 Resolved  | Context Memory Phases 1–5 complete (token budgets, lossless compaction, memory search) |
| 2.12 | Overly permissive CORS                             | 🔴 Open      | `DiscoveryCORSMiddleware` adds permissive CORS for gateway/relay paths; main CORS still `["*"]` methods/headers |
| 2.13 | Unbounded orphan recovery tasks                    | 🔴 Open      | No semaphore implemented                                                         |
| 2.14 | No circuit breaker for external agents             | 🔴 Open      | No circuit breaker implemented                                                   |
| 2.15 | Hub relay in-memory SSE queue (SPOF)               | 🔴 Open      | New issue (Mar 9) — relay cannot scale horizontally; hub bound to single instance |
| 2.16 | Remaining httpx leak in card fetching              | 🟢 Resolved  | Both methods fixed: `get_agent_card_from_url()` uses `async with`; `get_a2a_client()` removed (dead code) |

**Legend:**
- 🔴 Open — Not started or blocked
- 🟡 Partial — Work in progress or partially addressed
- 🟢 Resolved — Fix implemented and verified

---

## 5. Unified Implementation Dependency Graph

This section maps dependencies across all design documents:
- **SYSTEM_DESIGN_REVIEW.md** (this document) — Infrastructure issues
- **CONTEXT_MEMORY_SYSTEM_DESIGN.md** — Memory and context architecture
- **HITL_DESIGN.md** — Human-in-the-loop interactions
- **GATEWAY_API.md** / **HYBRO_HUB_DESIGN.md** — Hub and gateway architecture

### 5.1 Cross-Document Dependency Map

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    UNIFIED IMPLEMENTATION DEPENDENCY GRAPH                       │
│                                                                                  │
│  Legend:                                                                         │
│    ───▶  Hard dependency (must complete before)                                  │
│    - - ▶ Soft dependency (benefits from, not blocked)                            │
│    [SDR] SYSTEM_DESIGN_REVIEW.md issue                                           │
│    [CM]  CONTEXT_MEMORY_SYSTEM_DESIGN.md phase                                   │
│    [HITL] HITL_DESIGN.md phase                                                   │
│    [HUB]  GATEWAY_API.md / HYBRO_HUB_DESIGN.md                                  │
│                                                                                  │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  LAYER 0: INFRASTRUCTURE BLOCKERS (must fix first)                               │
│  ════════════════════════════════════════════════                                │
│                                                                                  │
│  ┌──────────────────────┐         ┌──────────────────────┐                       │
│  │ [SDR 2.1] Redis      │         │ [SDR 2.3] httpx      │                       │
│  │ Pub/Sub for SSE      │         │ Client Lifecycle     │                       │
│  │ (horizontal scaling) │         │ (PARTIAL — 2 remain) │                       │
│  └──────────┬───────────┘         └──────────┬───────────┘                       │
│             │                                │                                   │
│             │ ◀─────────────────────────────▶│                                   │
│             │      (independent, parallel)   │                                   │
│             │                                │                                   │
│             ▼                                ▼                                   │
│  ┌──────────────────────────────────────────────────────────────────┐            │
│  │                    HITL PHASE 1-4 UNBLOCKED                       │            │
│  │  (HITL backend models, HITLService, reply_to_task, endpoints)     │            │
│  └──────────────────────────────────────────────────────────────────┘            │
│                                                                                  │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  LAYER 1: CONTEXT MEMORY FOUNDATION                                              │
│  ═══════════════════════════════════                                             │
│                                                                                  │
│  ┌──────────────────────┐                                                        │
│  │ [CM Phase 1]         │                                                        │
│  │ Data Models &        │◀─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─│
│  │ Storage              │         (no hard blockers; can start immediately)      │
│  │ • ConversationTurn   │                                                        │
│  │ • ContentReference   │                                                        │
│  │ • RoomSummary        │                                                        │
│  │ • turn_notes         │                                                        │
│  └──────────┬───────────┘                                                        │
│             │                                                                    │
│             ▼                                                                    │
│  ┌──────────────────────┐         ┌──────────────────────┐                       │
│  │ [CM Phase 2]         │         │ [SDR 2.11] Unbounded │                       │
│  │ Context Assembly     │───────▶ │ Memory (PARTIAL)     │                       │
│  │ Engine               │         │ Token budget +       │                       │
│  │ • TokenBudget        │         │ windowing            │                       │
│  │ • build_supervisor_  │         └──────────────────────┘                       │
│  │   context()          │                                                        │
│  └──────────┬───────────┘                                                        │
│             │                                                                    │
│             ▼                                                                    │
│  ┌──────────────────────┐                                                        │
│  │ [CM Phase 3]         │                                                        │
│  │ Lossless Compaction  │                                                        │
│  │ • conversation_      │                                                        │
│  │   content collection │                                                        │
│  │ • expand_turn_       │                                                        │
│  │   content()          │                                                        │
│  └──────────┬───────────┘                                                        │
│             │                                                                    │
│             ▼                                                                    │
│  ┌──────────────────────┐                                                        │
│  │ [CM Phase 4]         │                                                        │
│  │ Memory Search        │                                                        │
│  │ • Pinecone index     │                                                        │
│  │ • Hybrid search      │                                                        │
│  │ • Temporal decay     │                                                        │
│  └──────────┬───────────┘                                                        │
│             │                                                                    │
│             ▼                                                                    │
│  ┌──────────────────────┐                                                        │
│  │ [CM Phase 5]         │                                                        │
│  │ Supervisor V2        │                                                        │
│  │ Integration          │                                                        │
│  │ • Wire context       │                                                        │
│  │   assembly           │                                                        │
│  │ • room_summary       │                                                        │
│  │   updates            │                                                        │
│  └──────────────────────┘                                                        │
│                                                                                  │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  LAYER 2: HITL IMPLEMENTATION                                                    │
│  ════════════════════════════                                                    │
│                                                                                  │
│  ┌──────────────────────┐                                                        │
│  │ [HITL Phase 1]       │                                                        │
│  │ Unified Interrupt    │◀───────── [SDR 2.3] httpx fix (for reply_to_task)      │
│  │ Foundation           │                                                        │
│  │ • InterruptKind      │                                                        │
│  │ • HITLRequest model  │                                                        │
│  │ • hitl_requests      │                                                        │
│  │   collection         │                                                        │
│  └──────────┬───────────┘                                                        │
│             │                                                                    │
│             ▼                                                                    │
│  ┌──────────────────────┐                                                        │
│  │ [HITL Phase 2]       │                                                        │
│  │ Unified CLARIFY      │                                                        │
│  │ via HITLService      │                                                        │
│  │ • Replaces old       │                                                        │
│  │   CLARIFY path       │                                                        │
│  └──────────┬───────────┘                                                        │
│             │                                                                    │
│             ▼                                                                    │
│  ┌──────────────────────┐                                                        │
│  │ [HITL Phase 3]       │                                                        │
│  │ V2 Queue Integration │                                                        │
│  │ • Agent input_       │                                                        │
│  │   required handling  │                                                        │
│  │ • AWAITING_INPUT     │                                                        │
│  │   status             │                                                        │
│  └──────────┬───────────┘                                                        │
│             │                                                                    │
│             ▼                                                                    │
│  ┌──────────────────────┐                                                        │
│  │ [HITL Phase 4]       │                                                        │
│  │ Response Endpoint    │                                                        │
│  │ • POST /hitl/respond │                                                        │
│  │ • reply_to_task()    │                                                        │
│  └──────────┬───────────┘                                                        │
│             │                                                                    │
│             ▼                                                                    │
│  ┌──────────────────────┐         ┌──────────────────────┐                       │
│  │ [HITL Phase 5]       │         │ [SDR 2.1] Redis      │                       │
│  │ Risk Mitigations     │◀────────│ Pub/Sub             │                       │
│  │ (Backend)            │         │ (for SSE delivery)   │                       │
│  │ • Stale task checker │         └──────────────────────┘                       │
│  │ • HITL expiry job    │                                                        │
│  └──────────┬───────────┘                                                        │
│             │                                                                    │
│             ▼                                                                    │
│  ┌──────────────────────┐                                                        │
│  │ [HITL Phase 6]       │                                                        │
│  │ Frontend             │                                                        │
│  │ • SSE handlers       │                                                        │
│  │ • Inline reply form  │                                                        │
│  │ • awaiting_input UI  │                                                        │
│  └──────────┬───────────┘                                                        │
│             │                                                                    │
│             ▼                                                                    │
│  ┌──────────────────────┐         ┌──────────────────────┐                       │
│  │ [HITL Phase 7]       │         │ [CM Phase 1]         │                       │
│  │ HITL Turn Recording  │◀────────│ ConversationTurn     │                       │
│  │ in Room Memory       │         │ model updates        │                       │
│  │ • hitl_question turn │         └──────────────────────┘                       │
│  │ • hitl_reply turn    │                                                        │
│  └──────────┬───────────┘                                                        │
│             │                                                                    │
│             ▼                                                                    │
│  ┌──────────────────────┐                                                        │
│  │ [HITL Phase 8]       │                                                        │
│  │ Legacy Shim Removal  │                                                        │
│  │ (7 days post-Phase 2)│                                                        │
│  └──────────────────────┘                                                        │
│                                                                                  │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  LAYER 3: RELIABILITY & HARDENING (can proceed in parallel)                      │
│  ══════════════════════════════════════════════════════════                      │
│                                                                                  │
│  ┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐    │
│  │ [SDR 2.2] Durable    │  │ [SDR 2.5] Double-    │  │ [SDR 2.6] SSE JWT    │    │
│  │ Task Queue           │  │ Processing Guard     │  │ Token Security       │    │
│  │ (Celery/Dramatiq)    │  │ (Idempotency)        │  │ (Short-lived nonce)  │    │
│  └──────────────────────┘  └──────────────────────┘  └──────────────────────┘    │
│                                                                                  │
│  ┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐    │
│  │ [SDR 2.7] TTL for    │  │ [SDR 2.8] MongoDB    │  │ [SDR 2.14] Circuit   │    │
│  │ cancelled_messages   │  │ Transactions         │  │ Breaker              │    │
│  │ ✅ RESOLVED          │  │                      │  │                      │    │
│  └──────────────────────┘  └──────────────────────┘  └──────────────────────┘    │
│                                                                                  │
│  ┌──────────────────────┐  ┌──────────────────────┐                              │
│  │ [HUB] Gateway API    │  │ [HUB] Relay Service   │                             │
│  │ (API key auth,       │  │ (in-memory SSE queue, │                             │
│  │  rate limiting)      │  │  heartbeat, offline Q)│                             │
│  │ ✅ Phase 2a DONE     │  │ ✅ Phase 2a DONE      │                             │
│  └──────────┬───────────┘  └──────────┬───────────┘                              │
│             └──────────┬───────────────┘                                         │
│                        ▼                                                         │
│  ┌──────────────────────────────────────────┐                                    │
│  │ [SDR 2.15] Hub Relay HA                   │                                   │
│  │ (soft dep on [SDR 2.1] Redis for          │                                   │
│  │  cross-instance relay fan-out)            │                                   │
│  └──────────────────────────────────────────┘                                    │
│                                                                                  │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  LAYER 4: FUTURE EVOLUTION                                                       │
│  ═════════════════════════                                                       │
│                                                                                  │
│  ┌──────────────────────┐         ┌──────────────────────┐                       │
│  │ [CM Phase 4B]        │◀────────│ [CM Phase 4]         │                       │
│  │ Graph-Based          │         │ Memory Search        │                       │
│  │ Retrieval            │         │ (prerequisite)       │                       │
│  │ • Entity graph       │         └──────────────────────┘                       │
│  │ • Dual-route search  │                                                        │
│  └──────────────────────┘                                                        │
│                                                                                  │
│  ┌──────────────────────┐                                                        │
│  │ Agent-Driven         │                                                        │
│  │ Compaction Tool      │                                                        │
│  │ (post-Phase 4)       │                                                        │
│  └──────────────────────┘                                                        │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 5.2 Critical Path Analysis

The **critical path** for full HITL functionality:

```
[SDR 2.1] Redis Pub/Sub ──▶ [HITL Phase 5] Risk Mitigations ──▶ [HITL Phase 6] Frontend
         │
         └──▶ (unblocks multi-instance SSE delivery for HITL prompts)

[SDR 2.3] httpx fix ──▶ [HITL Phase 1] Foundation ──▶ [HITL Phase 4] Response Endpoint
         │
         └──▶ ✅ UNBLOCKED (create_a2a_client fixed; reply_to_task uses async with)
```

The **critical path** for full Context Memory functionality:

```
[CM Phase 1] Data Models ──▶ [CM Phase 2] Context Assembly ──▶ [CM Phase 3] Compaction
         │                           │                               │
         │                           └──▶ [SDR 2.11] Unbounded Memory (RESOLVED)
         │                                                           │
         │                           ┌──────────────────────────────┘
         │                           ▼
[CM Phase 4] Memory Search ──▶ [CM Phase 5] Supervisor V2 Integration
         │
         └──▶ ALL 5 PHASES COMPLETE (Feb 25 2026)
         │
         └──▶ [HITL Phase 7] HITL Turn Recording (depends on ConversationTurn model ✅)
```

### 5.3 Recommended Implementation Order

Based on dependency analysis and impact:

| Priority | Item | Rationale |
|----------|------|-----------|
| **P0** | [SDR 2.1] Redis Pub/Sub | Blocks HITL multi-instance delivery; production blocker |
| **P0** | ~~[SDR 2.3] httpx client fix~~ | ✅ Largely resolved (Mar 9); 2 minor methods remain (see 2.16) |
| **P1** | [CM Phase 1] Data Models | Foundation for all memory work; no blockers |
| **P1** | [HITL Phase 1-4] Core HITL | High user value; blocked only by P0 items |
| **P2** | [CM Phase 2] Context Assembly | Enables token budget enforcement |
| **P2** | [HITL Phase 5-6] Risk Mitigations + Frontend | Completes HITL feature |
| **P3** | [CM Phase 3] Lossless Compaction | Correctness improvement |
| **P3** | [HITL Phase 7] Turn Recording | Memory integration |
| **P4** | [SDR 2.2] Durable Task Queue | Production hardening |
| **P4** | [CM Phase 4] Memory Search | Long-term recall |
| **P5** | Remaining SDR issues | Hardening and optimization |

### 5.4 Parallel Work Streams

These work streams can proceed independently:

| Stream | Items | Team |
|--------|-------|------|
| **Infrastructure** | [SDR 2.1], [SDR 2.2], [SDR 2.15] | Platform/DevOps |
| **Context Memory** | [CM Phase 1-5] ✅ ALL COMPLETE | Backend |
| **HITL Backend** | [HITL Phase 1-5, 7-8] | Backend |
| **HITL Frontend** | [HITL Phase 6] 🟡 IN PROGRESS | Frontend |
| **Hub & Gateway** | [HUB Phase 2a] ✅, [HUB Phase 2b+] | Backend + Platform |
| **Security** | [SDR 2.6], [SDR 2.12], [SDR 2.16] | Security |

---

## 6. Implementation Progress

This section tracks the implementation status of the design items identified above.

### 6.1 Context Memory System (CM Phases)

**Reference**: `CONTEXT_MEMORY_SYSTEM_DESIGN.md`

| Phase | Status | Date | Notes |
|-------|--------|------|-------|
| **CM Phase 1: Data Models & Storage** | ✅ COMPLETED | 2026-02-23 | See details below |
| **CM Phase 2: Context Assembly Engine** | ✅ COMPLETED | 2026-02-23 | See details below |
| **CM Phase 3: Lossless Compaction** | ✅ COMPLETED | 2026-02-23 | See details below |
| **CM Phase 4: Memory Search** | ✅ COMPLETED | 2026-02-25 | See details below |
| CM Phase 5: Supervisor V2 Integration | ✅ COMPLETED | 2026-02-25 | See details below |

> **Full design compliance audit completed 2026-02-25**: 33/35 §18 checklist items implemented (94.3%). See §6.4 below for item-by-item verification against every section of `CONTEXT_MEMORY_SYSTEM_DESIGN.md`.

#### CM Phase 1 Details (Completed 2026-02-23)

**Files Created:**
- `models/compaction.py` — `StorageType` enum, `ContentReference`, `StoredContent`, `CompactionResult`
- `models/context.py` — `SessionContext`, `TokenBudget`
- `models/search.py` — `MemorySourceType` enum, `MemorySearchConfig`, `MemorySearchResult`, `MemorySearchResponse`
- `scripts/migrate_room_memories.py` — Migration script for existing data

**Files Modified:**
- `models/memory.py`:
  - Added `TurnRole`, `TurnRepresentation`, `ContentType`, `TurnType` enums
  - Updated `ConversationTurn` to §6.2 canonical shape (added `turn_id`, `representation`, `content_ref`, `content_type`, `turn_type`, `estimated_tokens_full`, `estimated_tokens_compact`, `brief_summary`, `turn_notes`, `was_successful`)
  - Added `RoomSummary` (Knowledge Block)
  - Added `RoomFact`, `AgentSuccessRecord`
  - Added `UserMemory`, `AgentMemory`, `TaskTypeMetrics`, `FailurePattern`
  - Updated `RoomMemory` with new fields (`conversation_history`, `room_summary`, `room_facts`, `agent_success_history`, `last_activity_at`, `total_messages`, `total_compactions`)

- `common/utils/context_utils.py`:
  - Added `estimate_tokens(text)` function (tiktoken with char/4 fallback)
  - Added `extract_turn_notes(content)` function (heuristic extraction with `tags` placeholder)
  - Updated `add_turn_to_history()` to populate `estimated_tokens_full`, `turn_notes`, and `was_successful` at write time
  - Updated context building functions to handle compact turns via `to_context_string()`

- `config/settings.py`:
  - Added token budget settings (`context_model_window`, `context_system_prompt_tokens`, etc.)
  - Added compaction settings (`compaction_enabled`, `compaction_max_full_turns`, etc.)
  - Added memory search settings (`memory_search_enabled`, `memory_search_vector_weight`, etc.)

- `database/mongodb.py`:
  - Added `conversation_content_collection` property
  - Added `user_memories_collection` property
  - Added `agent_memories_collection` property
  - Added `create_context_memory_indexes()` method

**Design Principles Compliance:**
- §2.1 Principle 4 (Preserve Errors): `was_successful` field added to `ConversationTurn`
- §6.2 `turn_notes` schema: Now includes `tags: []` placeholder for future LLM extraction

**Migration:**
Run `python scripts/migrate_room_memories.py --execute` to migrate existing room_memories to the new schema.

#### CM Phase 2 Details (Completed 2026-02-23)

**Files Created:**
- `services/context_assembly_service.py` — `ContextAssemblyService` class with:
  - `build_supervisor_context()` — Budget-aware context for Supervisor LLM (decide_next calls)
  - `build_agent_execution_context()` — Budget-aware context for individual agent execution
  - `TruncationReason` enum for tracking truncation causes
  - `ContextAssemblyResult` dataclass with metrics (tokens, occupancy, truncation info)
  - `ContextMetrics` dataclass for monitoring
- `tests/__init__.py` — Tests package initialization
- `tests/test_context_assembly_service.py` — Unit tests for context assembly (§18 requirement)

**Files Modified:**
- `common/utils/context_utils.py`:
  - Updated `build_context_for_agent()` to enforce `MAX_CONTEXT_CHARS` (§17.2 gap fix)
  - Added `context_occupancy_pct` logging to every context build call (§15 requirement)
  - Added budget-aware turn selection (removes oldest turns first when over budget)
  - Added `max_tokens` parameter for explicit token limit control
  - Fixed: History budget now uses 0.6 (60%) to match §5.2 `conversation_history_pct`

**Key Features Implemented:**
- **Token Budget Allocation** (§5.2): Uses `TokenBudget` model with configurable allocations
- **Hard Cap Enforcement** (§17.2): Truncates oldest turns when budget exceeded, logs warning
- **Stable Prefix / Dynamic Suffix** (§12): KV-cache optimization structure
  - Stable prefix: Room summary + agent roster + room facts (rarely changes)
  - Dynamic suffix: Conversation history + current task (changes each request)
  - Agent registry sorted by name for deterministic ordering (§12.1)
- **Context Occupancy Monitoring** (§15.1): Full threshold-based logging:
  - < 70%: Healthy (debug level)
  - 70-85%: Soft warning (info level, "approaching limit")
  - 85-90%: Hard cap zone (warning level, truncation)
  - > 90%: Emergency (error level, "EMERGENCY" tag)
- **Truncation Metrics**: Tracks `truncation_count`, `turns_truncated`, `truncation_reason`
- **Cache Prefix Logging** (§15.2): `cache_prefix_tokens` (stable_prefix_tokens) included in all log messages

**Bug Fixes Applied (2026-02-23 Review):**
1. **Agent registry sorting** (§12.1): Now sorted by `agent_id` (with `name` fallback) for KV-cache stability
2. **Budget percentage consistency**: Changed 0.7 → 0.6 in `context_utils.py` to match §5.2
3. **Summary token caching**: Optimized `_select_turns_within_budget` to avoid recalculating summary tokens in loop
4. **Over-budget edge case**: Added error logging when stable prefix alone exceeds budget
5. **Cache prefix logging**: Added `stable_prefix_tokens` parameter to `_log_context_metrics`
6. **Defensive room_facts handling**: Added null check for `room_facts` before iteration

**Bug Fixes Applied (2026-02-23 Third Review):**
7. **Duplicate warning logs** (Bug #1): Removed explicit `logger.warning` in `build_supervisor_context` — `_log_context_metrics` now handles all truncation logging
8. **Hard cap enforcement in agent context** (Bug #2, §17.2): Added final hard cap check in `build_agent_execution_context` with turn truncation loop and critical error logging when stable prefix exceeds budget
9. **Task budget enforcement** (Bug #3, §5.2): `_build_agent_dynamic_suffix` now accepts `task_budget` parameter and truncates task content if it exceeds allocation

**Unit Tests:**
- `TestTokenBudget`: Budget calculation and allocation tests
- `TestContextAssemblyService`: Supervisor and agent context building tests
- `TestTurnSelection`: Turn selection within budget tests
- `TestOccupancyThresholds`: Logging level verification for each threshold
- `TestHardCapEnforcement`: Hard cap enforcement and critical error logging tests
- `TestTaskBudgetEnforcement`: Task budget parameter passing and truncation tests

**Integration:**
- `ContextAssemblyService` is a new service that can be used alongside existing `build_context_for_agent()`
- Phase 5 will wire `ContextAssemblyService` into Supervisor V2 loop
- Existing callers of `build_context_for_agent()` now get budget enforcement automatically

**Known Limitations (Deferred to Future Phases):**
- User Memory and Agent Memory loading not yet implemented (§5.1 Stage 1 partial)
  - These memory layers are not yet populated by other parts of the system
  - Will be added when Phase 4 (Memory Search) is implemented

#### CM Phase 3 Details (Completed 2026-02-23)

**Files Created:**
- `services/content_storage_service.py` — `ContentStorageService` class with:
  - `upsert_full_content()` — Idempotent storage of full content (§6.3)
  - `get_content_by_document_id()` — Retrieve content by MongoDB document ID
  - `get_content_by_turn_id()` — Retrieve content by room_id + turn_id
  - `expand_content_reference()` — Expand ContentReference to full content (§6.4)
  - `delete_content_by_turn_id()` / `delete_content_by_room_id()` — Content cleanup
  - `get_content_stats_for_room()` — Storage statistics
  - `ContentExpiredError` exception class (§6.4)
  - `hash_content()` utility for SHA-256 content hashing

- `services/compaction_service.py` — `CompactionService` class with:
  - `should_compact()` — Check if room needs compaction (§6.5)
  - `compact_room_memory()` — Lossless compaction of older turns (§6.3)
  - `expand_turn_content()` — On-demand content retrieval (§6.4)
  - `fetch_turn_content()` — Agent-callable tool for content retrieval (§6.4)
  - `expand_turns_for_context()` — Prepare turns for context window (§6.4)
  - `get_compaction_stats()` — Room compaction statistics
  - `get_compaction_config()` — Build config from settings

- `tests/test_compaction_service.py` — Comprehensive unit tests:
  - `TestHashContent` — Content hashing tests
  - `TestContentStorageService` — Storage upsert, retrieval, expansion tests
  - `TestGetCompactionConfig` — Configuration loading tests
  - `TestCompactionService` — Compaction trigger, execution, expansion tests
  - `TestCompactionRoundTrip` — Full compact → expand → verify cycle tests
  - `TestTokenSavings` — Token savings calculation tests
  - `TestErrorHandling` — Error handling and recovery tests

**Key Features Implemented:**
- **Lossless Compaction** (§6): Pointer-based compaction, NOT summarization
  - Full content stored in MongoDB `conversation_content` collection
  - Turns replaced with `ContentReference` pointers
  - Original content always retrievable on demand
- **Idempotent Upsert** (§6.3): Uses unique `(room_id, turn_id)` index
  - Crashed-and-retried compaction never creates duplicate documents
  - Safe to call within per-room processing lock
- **Compaction Triggers** (§6.5):
  - Turn count threshold: `compaction_max_full_turns` (default: 20)
  - Token threshold: `compaction_max_total_tokens` (default: 80000)
  - Preserve recent: `compaction_preserve_recent` (default: 10)
- **On-Demand Expansion** (§6.4):
  - Compact turns render as pointer strings via `to_context_string()`
  - Agents request full content via `fetch_turn_content` tool
  - `ContentExpiredError` raised if content missing (TTL, deletion)
- **Token Savings Tracking**: `CompactionResult` includes `tokens_saved`
- **Error Resilience**: Compaction continues even if individual turns fail

**Design Compliance:**
- §6.2 `ContentReference.to_compact_string()`: Renders pointer for context
- §6.3 Idempotent upsert: Uses `$setOnInsert` with unique index
- §6.3 `content_hash` in ContentReference: Populated for cache validation
- §6.4 `ContentExpiredError`: Proper exception with turn_id and document_id
- §6.5 Trigger thresholds: Configurable via settings
- §6.6 Storage schema: `StoredContent` model with TTL support
- §6.7 Compaction vs Summarization: Clear separation (compaction only)

**Bug Fixes Applied (2026-02-23 Review):**
1. **content_hash not populated** (§6.3): `ContentReference.content_hash` was set to `None` instead of the actual hash. Fixed by importing `hash_content` and calculating hash before creating ContentReference.

**Integration Points:**
- `CompactionService` uses `ContentStorageService` for storage operations
- `CompactionService` uses `db_service` for room memory operations
- Settings from `config/settings.py` control all thresholds
- MongoDB indexes created by `create_context_memory_indexes()` (Phase 1)

**Known Limitations:**
- `brief_summary` generation for very old turns (>50) not yet implemented
  - Design allows for this but deferred to future enhancement
- S3 storage for binary content not yet implemented (§6.8 future extension)
- ~~Background compaction job not yet implemented (§6.9)~~ → ✅ Implemented as `jobs/compaction_sweep.py` (Mar 9)

#### CM Phase 4 Details (Completed 2026-02-25)

**Created Files:**
- `services/memory_search_service.py` — Hybrid search service (vector + keyword + merge + temporal decay + MMR)
- `models/context_config.py` — Property-based runtime config classes (TokenBudget, CompactionConfig, MemorySearchConfig) per §14.3
- `tests/test_memory_search_service.py` — Comprehensive unit tests (cosine similarity, merging, temporal decay, MMR, indexing, pipeline, graceful degradation)

**Modified Files:**
- `database/pinecone_db.py` — Extended `PineconeDB` with `get_index(name)` for multi-index support and Pinecone client caching
- `services/compaction_service.py` — Wired `index_turn_for_search()` into `_compact_single_turn()` for write path; migrated to property-based `CompactionConfig` from `models/context_config.py`
- `tests/test_compaction_service.py` — Updated mock targets for new config import path; added auto-mock for `memory_search_service` in compaction test classes

**Key Features Implemented:**
- **Vector search**: Pinecone-based semantic similarity search scoped by `room_id` metadata filter
- **Keyword search**: MongoDB `$text` search on `conversation_content` collection (turn_notes.keywords, entities, one_liner)
- **Weighted merge**: Configurable vector/keyword weights with score normalization
- **Temporal decay**: Exponential decay with configurable half-life (2^(-age/half_life))
- **MMR re-ranking**: Maximal Marginal Relevance for diversity using score-profile proxy vectors
- **Write path**: `index_turn_for_search()` embeds and upserts turns to Pinecone at compaction time
- **Graceful degradation**: Parallel search with `asyncio.gather()`, log-and-return-empty on any failure
- **Property-based config**: All three config classes (TokenBudget, CompactionConfig, MemorySearchConfig) per §14.3

**Design Compliance:**
- §8.1 Search Architecture: ✅ (vector + keyword + merge + decay + MMR pipeline)
- §8.2 Search Configuration: ✅ (all parameters loaded from env via `models/context_config.py`)
- §8.3 turn_notes Integration: ✅ (keyword search uses MongoDB text index on turn_notes)
- §14.3 Configuration Classes: ✅ (property-based singletons reading from settings)
- §18 Phase 4 Checklist: ✅ (all 5 items complete)

**Known Limitations:**
- Pinecone `room-memory` index must be created manually (not auto-provisioned by code)
- Graph-based retrieval (§8.4) deferred to Phase 4B
- Cross-room search not yet supported (scoped to single room_id)

#### CM Phase 5 Details (Completed 2026-02-25)

**Created Files:**
- `tests/test_phase5_supervisor_integration.py` — 16 unit tests covering synthesis history, room summary extraction, prompt cache optimization, compaction triggers, and MAX_CONTEXT_CHARS enforcement

**Modified Files:**
- `services/room_services.py` — `_prepare_for_supervisor_v2()` now accepts `room_memory` and builds context via `ContextAssemblyService.build_supervisor_context()` (§11.1); `process_agent_message()` now accepts `RoomMemory` and uses `ContextAssemblyService.build_agent_execution_context()` (§11.2); `_prepare_clarify_resume_v2()` also uses `ContextAssemblyService`; room memory always loaded when `use_supervisor=True`
- `services/memory_service.py` — Added `add_synthesis_to_history()` (SUPERVISOR-role turn creation + trajectory agent contributions in turn_notes) and `update_room_summary()` (LLM-based structured extraction from synthesis text with "never lose data" semantics)
- `services/room_supervisor_service.py` — Moved `{conversation_context}` from `SUPERVISOR_V2_USER_PROMPT` to `SUPERVISOR_V2_SYSTEM_PROMPT` for OpenAI prompt cache optimization (§12.3); updated `decide_next()` to format context into system prompt
- `services/context_assembly_service.py` — Added `MAX_CONTEXT_CHARS` hard cap enforcement to both `build_supervisor_context()` and `build_agent_execution_context()` (§17.2)
- `modules/RoomMessageCenter.py` — Added post-loop integration in `_handle_v2_run_result()`: synthesis history (§11.3), fire-and-forget room summary update (§9), fire-and-forget compaction trigger for all terminal statuses (§6.5); added `_update_room_summary_safe()` and `_trigger_compaction_safe()` helpers
- `modules/AgentMessageProcessor.py` — Updated `process_single_message()` to pass full `RoomMemory` to `process_agent_message()` instead of just `MemoryContent`
- `modules/QueueExecutor.py` — Updated fallback `_process_single_message_inline()` to pass `RoomMemory` consistently
- `common/utils/context_utils.py` — Added `from __future__ import annotations` to fix `str | "TurnRole"` union type syntax

**Key Integration Points:**
- **§11.1 Pre-Loop**: `ContextAssemblyService.build_supervisor_context()` replaces `build_minimal_context(max_turns=5)` with budget-aware, truncation-tracked context assembly
- **§11.2 During Loop**: `ContextAssemblyService.build_agent_execution_context()` replaces `build_context_for_agent()` for per-agent context with room summary, facts, and priority-based truncation
- **§11.3 Post-Loop**: Synthesis text added to room history as SUPERVISOR turn; room summary updated via fast LLM extraction; compaction triggered as fire-and-forget for all terminal statuses
- **§12.3 Prompt Cache**: `conversation_context` moved to system prompt — shared across all `decide_next` iterations (50% token cost reduction on iterations 2–8)
- **§17.2 MAX_CONTEXT_CHARS**: Hard char-based cap as safety net in both assembly methods, logging truncation events
- **§15 Observability**: `context_occupancy_pct` logged at threshold-appropriate levels (debug/info/warning/error) for both supervisor and agent contexts

**Performance Decisions:**
- `update_room_summary()` runs as inline `await` (NOT fire-and-forget) — summary must complete before compaction to prevent last-writer-wins race on the RoomMemory document (both do full `$set` saves)
- `compaction_service.compact_if_needed()` runs as inline `await` within the per-room lock — fire-and-forget would race with the next message's writes
- Context is built once per supervisor loop invocation (§5.1), not per iteration
- `agent_dicts` serialized once and reused for both ContextAssemblyService and extend_info

**Bug Fixes Applied (2026-02-25 Post-Phase-5 Reviews — 4 rounds, 12 bugs):**

*Round 1 — Race Conditions & Data Loss:*
1. **CRITICAL: Data race between summary update and compaction** — `_update_room_summary_safe` changed from fire-and-forget `asyncio.create_task` to `await` (RoomMessageCenter.py:1114-1122)
2. **HIGH: Silent data loss in compact turn eviction** — `_format_turns_for_summary` rewritten to use `turn.to_context_string()` so compact turns render `brief_summary + pointer` instead of `"[content unavailable]"` (context_utils.py:324-330)
3. **MEDIUM: Redundant DB load in compaction trigger** — `_trigger_compaction_safe` now calls `compact_if_needed()` instead of separate `should_compact()` + `compact_room_memory()` (RoomMessageCenter.py:1154-1159)

*Round 2 — Budget & Correctness:*
4. **MEDIUM: Negative token savings in compaction stats** — Added `max(0, ...)` guard to `compact_tokens_saved` calculation (compaction_service.py:468)
5. **MEDIUM: Legacy context builder missing hard cap** — Added `MAX_CONTEXT_CHARS` truncation to `build_context_for_agent()` (context_utils.py:493-498)
6. **MEDIUM: LLM extraction merging bug** — `update_room_summary` changed to `is not None` checks for list fields so empty lists from LLM correctly clear fields (memory_service.py:813-822)
7. **LOW: Unbounded summary growth** — Introduced `MAX_SUMMARY_CHARS = 4000` and front-trim in `add_turn_to_history` (context_utils.py:305-311)

*Round 3 — Missing Rendering & False Errors:*
8. **MEDIUM: recent_agent_contributions never rendered** — `_build_stable_prefix` now includes `recent_agent_contributions` in output (context_assembly_service.py:455-457)
9. **LOW: False-positive error logs on idempotent saves** — Changed `update_room_memory_by_room_id` and `update_room_memory_by_memory_id` to return `matched_count > 0` instead of `modified_count > 0` (mongodb.py:1294, 1315)

*Round 4 — Context Completeness & Persistence:*
10. **MEDIUM: Agent context suppressed plaintext summary** — Removed conditional `if turns_truncated > 0` when passing `memory_content.summary`, ensuring it's always included (context_assembly_service.py:300-302)
11. **MEDIUM: Compaction persistence failure silently ignored** — Added explicit `save_success` check in `compact_room_memory`, logs WARNING and adds error to `CompactionResult.errors` on failure (compaction_service.py:258-273)
12. **LOW: `_has_room_summary_content` didn't check contributions** — Added `recent_agent_contributions` to the check (context_assembly_service.py:489)

**Design Compliance:**
- §11.1 Pre-Loop Context: ✅
- §11.2 During-Loop Agent Context: ✅
- §11.3 Post-Loop Synthesis/Compaction: ✅
- §12.3 Prompt Cache Optimization: ✅
- §15 Observability (context_occupancy_pct): ✅
- §17.2 MAX_CONTEXT_CHARS Enforcement: ✅
- §18 Phase 5 Checklist: 8/10 items complete (end-to-end tests and performance benchmarks deferred to integration testing)

---

### 6.4 Context Memory System — Full Design Compliance Audit (2026-02-25)

Comprehensive item-by-item verification of the entire Context Memory System implementation against `CONTEXT_MEMORY_SYSTEM_DESIGN.md`.

#### §2: Design Principles

| Principle | Status | Evidence |
|---|---|---|
| §2.1.1 KV-Cache Optimization (stable prefix) | ✅ | `context_assembly_service.py:_build_stable_prefix()`, sorted agent registry (§12.1) |
| §2.1.2 File System as Memory (MongoDB) | ✅ | `conversation_content` collection, `content_storage_service.py` |
| §2.1.3 Attention Manipulation (recency) | ✅ | Recent turns always at context end, `_build_dynamic_suffix()` |
| §2.1.4 Preserve Errors | ✅ | `ConversationTurn.was_successful` field, populated at write time |
| §2.2.1 Multi-Layer Memory | ✅ | Session (`models/context.py`), Room/User/Agent (`models/memory.py`) |
| §2.2.2 Lossless Compaction | ✅ | Pointer-based via `compaction_service.py`, original always in `conversation_content` |
| §2.2.3 Hybrid Search | ✅ | Vector (Pinecone) + Keyword (MongoDB $text) in `memory_search_service.py` |
| §2.2.4 Temporal Decay | ✅ | `2^(-age/half_life)` formula in `_apply_temporal_decay()` |
| §2.4.1 Write-time Note Generation (A-MEM) | ✅ | `turn_notes` populated via `extract_turn_notes()` in `add_turn_to_history()` |
| §2.4.2 Rolling Room Summary (Focus) | ✅ | `RoomSummary` model, `update_room_summary()` at synthesis boundary |
| §2.4.4 Context Occupancy Metric | ✅ | 4-tier logging in `_log_context_metrics()`: <70%, 70-85%, 85-90%, >90% |

#### §3-§4: Data Models & Memory Layers

| Model | Status | Location |
|---|---|---|
| `ConversationTurn` (§6.2 canonical) | ✅ | `models/memory.py:103-157` — all 17 fields present |
| `ContentReference` | ✅ | `models/compaction.py:35-72` — `to_compact_string()` matches design |
| `StoredContent` | ✅ | `models/compaction.py:75-98` — includes `turn_notes` for keyword search |
| `RoomSummary` | ✅ | `models/memory.py:216-240` — 5 named slots + 2 metadata fields |
| `RoomFact` | ✅ | `models/memory.py:243-256` — includes `confidence`, `category` (as `expires_at`) |
| `RoomMemory` | ✅ | `models/memory.py:293-351` — all fields including `room_summary`, `room_facts`, `total_compactions` |
| `SessionContext` | ✅ | `models/context.py:22-56` |
| `TokenBudget` | ✅ | `models/context.py:59-114` — `available_for_content` property |
| `UserMemory` | ✅ | `models/memory.py:373-395` |
| `AgentMemory` | ✅ | `models/memory.py:398-419` |
| `MemorySearchResult` | ✅ | `models/search.py:34-64` |
| `MemorySearchResponse` | ✅ | `models/search.py:67-83` |
| `CompactionResult` | ✅ | `models/compaction.py:101-108` |
| `CompactionConfig` | ✅ | `models/context_config.py:79-111` (property-based, §14.3) |

#### §5: Context Assembly Engine

| Requirement | Status | Evidence |
|---|---|---|
| `ContextAssemblyService` class | ✅ | `services/context_assembly_service.py:75-785` |
| `build_supervisor_context()` | ✅ | Lines 113-233 — budget-aware, truncation-tracked |
| `build_agent_execution_context()` | ✅ | Lines 235-401 — separate history/room/task budgets |
| Token budget allocation (§5.2) | ✅ | Uses `TokenBudget` with 15%/60%/25% split |
| Stable prefix / dynamic suffix (§12.1) | ✅ | `_build_stable_prefix()` / `_build_dynamic_suffix()` |
| Turn selection within budget | ✅ | `_select_turns_within_budget()` — removes oldest first |
| Task budget enforcement | ✅ | `_build_agent_dynamic_suffix()` accepts `task_budget` |
| `MAX_CONTEXT_CHARS` hard cap (§17.2) | ✅ | Applied in both build methods as safety net |

#### §6: Compaction System (Lossless)

| Requirement | Status | Evidence |
|---|---|---|
| `CompactionService` | ✅ | `services/compaction_service.py:30-441` |
| `ContentStorageService` | ✅ | `services/content_storage_service.py:54-326` |
| Idempotent upsert (`$setOnInsert` + unique index) | ✅ | `content_storage_service.py:123-127`, `mongodb.py:1660-1663` |
| `content_hash` populated on compaction | ✅ | `compaction_service.py:286` |
| `ContentExpiredError` exception | ✅ | `content_storage_service.py:22-38` |
| `expand_turn_content()` (on-demand) | ✅ | `compaction_service.py:299-329` |
| `fetch_turn_content()` (agent tool) | ✅ | `compaction_service.py:331-365` |
| `expand_turns_for_context()` (recency-only) | ✅ | `compaction_service.py:367-393` — returns unchanged per Manus design |
| `should_compact()` triggers (§6.5) | ✅ | Turn count + token threshold checks |
| `preserve_count=0` edge case | ✅ | `compaction_service.py:163` |
| Turn indexed in Pinecone before compaction | ✅ | `compaction_service.py:271-279` — aborts if indexing fails |

#### §8: Memory Search (Hybrid)

| Requirement | Status | Evidence |
|---|---|---|
| `MemorySearchService` | ✅ | `services/memory_search_service.py:50-559` |
| Vector search (Pinecone) | ✅ | `_vector_search()` with `room_id` metadata filter |
| Keyword search (MongoDB `$text`) | ✅ | `_keyword_search()` on `conversation_content` collection |
| Weighted merge (configurable) | ✅ | `_merge_results()` with score normalization |
| Temporal decay: `2^(-age/half_life)` | ✅ | `_apply_temporal_decay()` — exact formula |
| MMR re-ranking | ✅ | `_apply_mmr()` with cosine similarity on score profiles |
| `index_turn_for_search()` write path | ✅ | Embedding + Pinecone upsert at compaction time |
| `delete_room_index()` cleanup | ✅ | `memory_search_service.py:256-277` |
| Graceful degradation (parallel + log-and-return-empty) | ✅ | `asyncio.gather(return_exceptions=True)` |
| Config from env (§14) | ✅ | `models/context_config.py:86-128` |

#### §11: Supervisor V2 Integration

| Requirement | Status | Evidence |
|---|---|---|
| §11.1 Pre-loop: `build_supervisor_context()` wired | ✅ | `room_services.py:1082-1086` |
| §11.2 During-loop: `build_agent_execution_context()` wired | ✅ | `room_services.py:2164-2168` |
| §11.3 Post-loop: `add_synthesis_to_history()` | ✅ | `RoomMessageCenter.py:1109-1112` |
| §11.3 Post-loop: compaction trigger (all terminal statuses) | ✅ | `RoomMessageCenter.py:1132-1134` |
| `update_room_summary()` at synthesis boundary | ✅ | `RoomMessageCenter.py:1118-1122` (fire-and-forget) |
| §12.3 Prompt cache: `conversation_context` in system prompt | ✅ | `room_supervisor_service.py:82` |

#### §14: Configuration

| Requirement | Status | Evidence |
|---|---|---|
| All 7 token budget env vars | ✅ | `settings.py:83-89` |
| All 5 compaction env vars | ✅ | `settings.py:92-96` |
| All 9 memory search env vars | ✅ | `settings.py:99-107` |
| Property-based config classes (§14.3) | ✅ | `models/context_config.py` — 3 singletons |

#### §15: Observability

| Requirement | Status | Evidence |
|---|---|---|
| `context_occupancy_pct` logging | ✅ | `context_assembly_service.py:_log_context_metrics()` + `context_utils.py:478-483` |
| 4-tier occupancy thresholds | ✅ | <70% debug, 70-85% info, 85-90% warning, >90% error |
| `cache_prefix_tokens` in logs | ✅ | `stable_prefix_tokens` parameter in all log messages |
| Compaction logging | ✅ | `compaction_service.py:222-225` |
| Truncation events tracked | ✅ | `_truncation_count` counter, `TruncationReason` enum |

#### §18: Implementation Checklist (All Phases)

| Phase | Checklist Items | Implemented | Deferred | Notes |
|---|---|---|---|---|
| Phase 1 | 10 items | 10/10 | 0 | All models, wiring, settings, indexes, migration |
| Phase 2 | 5 items | 5/5 | 0 | Service, budget, prefix/suffix, tests, integration |
| Phase 3 | 5 items | 5/5 | 0 | Compaction, storage, expansion, triggers, tests |
| Phase 4 | 5 items | 5/5 | 0 | Search, Pinecone, hybrid, MMR, tests |
| Phase 5 | 10 items | 8/10 | 2 | E2E tests + benchmarks require real infrastructure |
| **Total** | **35 items** | **33/35** | **2** | 94.3% complete |

#### Test Coverage

| Test File | Test Count | Covers |
|---|---|---|
| `test_context_assembly_service.py` | ~25 | Phase 2: budget, turns, occupancy, hard cap, task budget |
| `test_compaction_service.py` | ~20 | Phase 3: hash, storage, config, compaction, round-trip, errors |
| `test_memory_search_service.py` | ~15 | Phase 4: cosine sim, merge, decay, MMR, indexing, pipeline |
| `test_phase5_supervisor_integration.py` | 16 | Phase 5: synthesis, summary, prompt cache, compaction triggers |

#### Known Deferred Items (By Design)

| Item | Design Section | Reason |
|---|---|---|
| User Memory / Agent Memory population | §4.3, §4.4 | Models exist; not yet populated by other system components |
| S3 storage for binary content | §6.8 | Future extension — design placeholder only |
| ~~Background compaction job~~ | §6.9 | ✅ Implemented as `jobs/compaction_sweep.py` (Mar 9) |
| Graph-based retrieval (dual-route) | §8.4 | Phase 4B — after hybrid search is production-stable |
| Agent-driven compaction tool | §2.4.5 | Post-Phase 4 evolution |
| `brief_summary` for very old turns | §6.7 | Optional enhancement, deferred |
| E2E tests with real infrastructure | §18 Phase 5 | Requires MongoDB + Pinecone + LLM integration |
| Performance benchmarks | §18 Phase 5 | Requires production traffic patterns |

### 6.5 Architecture Improvements (Implemented, Not in Original Review)

These improvements were implemented after the original review and are documented here for completeness.

#### A-3: Cooperative Cancellation — CancellationToken

**Files**: `common/utils/cancellation.py`, `models/processing.py`, `services/sse_services.py`

Replaces the old polling-based `SSEManager.is_cancelled(message_id)` checkpoint pattern with an event-driven `CancellationToken` threaded through the processing pipeline:

- `CancellationToken` wraps an `asyncio.Event`; cancellation is instant (no poll interval)
- `token.race(awaitable)` lets any I/O call be transparently interruptible
- `token.check()` provides a lightweight synchronous checkpoint
- `CancellationError` exception propagates cleanly up the call stack
- SSE manager creates tokens on processing start and pre-signals if cancel arrived first
- All three TTL caches (`cancelled_messages`, `_terminal_status_sent`, `_cancellation_tokens`) use `cachetools.TTLCache`

#### A-4: Module Decomposition — RoomMessageCenter Refactoring

**Files**: `modules/SupervisorExecutor.py`, `modules/QueueExecutor.py`, `modules/AgentMessageProcessor.py`, `modules/AgentDispatcher.py`, `modules/ResponseProcessor.py`, `modules/TaskStateManager.py`

The monolithic `RoomMessageCenter` was decomposed into focused modules:

| Module | Responsibility |
|--------|----------------|
| `RoomMessageCenter` | Entry point, orchestrates V1/V2 routing, post-loop lifecycle |
| `SupervisorExecutor` | V2 decide → dispatch → record cycle, push-pause/resume, step budget |
| `QueueExecutor` | V1 sequential queue processing (non-supervisor rooms, fast paths) |
| `AgentMessageProcessor` | Single-agent message processing (streaming, sync, task tracking) |
| `AgentDispatcher` | Agent assignment resolution (group expansion, @mention routing) |
| `ResponseProcessor` | Streaming/sync response handling, SSE event emission |
| `TaskStateManager` | Task state machine (submitted → working → completed/failed) |

#### Discovery CORS Middleware

**File**: `common/middleware/discovery_cors_middleware.py`

Separate permissive CORS middleware applied to `/api/v1/discovery/*`, `/api/v1/gateway/*`, and `/api/v1/relay/*` paths, allowing external API and Hub access from any origin while keeping the main CORS policy restricted to frontend origins.

#### Rate Limiting

**Files**: `services/rate_limit_service.py`, `services/discovery_rate_limit_service.py`

Per-room and per-endpoint rate limiting to prevent abuse. Discovery API has separate limits from authenticated endpoints.

### 6.2 HITL Phases

| Phase | Status | Date | Notes |
|-------|--------|------|-------|
| HITL Phase 1: Foundation | 🔲 NOT STARTED | - | |
| HITL Phase 2: V1 Shim | 🔲 NOT STARTED | - | |
| HITL Phase 3: V2 Queue Integration | 🔲 NOT STARTED | - | |
| HITL Phase 4: Response Endpoint | 🔲 NOT STARTED | - | |
| HITL Phase 5: Risk Mitigations | 🔲 NOT STARTED | - | |
| HITL Phase 6: Frontend | 🟡 IN PROGRESS | 2026-03-09 | See `hybro-frontend/docs/FRONTEND_DESIGN_REVIEW.md` §3.1 for details |
| HITL Phase 7: Turn Recording | 🔲 NOT STARTED | - | Depends on CM Phase 1 ✅ |
| HITL Phase 8: Legacy Shim Removal | 🔲 NOT STARTED | - | |

### 6.3 SDR Issues

| Issue | Status | Date | Notes |
|-------|--------|------|-------|
| SDR 2.1: Redis Pub/Sub | 🔲 NOT STARTED | - | P0 blocker; MongoDB Change Stream covers cancellation only |
| SDR 2.2: Durable Task Queue | 🔲 NOT STARTED | - | |
| SDR 2.3: httpx client fix | ✅ RESOLVED | 2026-03-13 | All methods fixed: `create_a2a_client()` uses `@asynccontextmanager`; `get_agent_card_from_url()` uses `async with`; `get_a2a_client()` removed (dead code) |
| SDR 2.5: Double-Processing Guard | 🟡 PARTIAL | 2026-02-25 | `sendMessage` auto-triggers; legacy endpoint still exists |
| SDR 2.6: SSE JWT Token Security | 🔲 NOT STARTED | - | |
| SDR 2.7: TTL for cancelled_messages | ✅ RESOLVED | 2026-02-25 | `TTLCache(maxsize=10_000, ttl=3600)` + CancellationToken TTL cache |
| SDR 2.8: MongoDB Transactions | 🔲 NOT STARTED | - | |
| SDR 2.11: Unbounded Memory | ✅ RESOLVED | 2026-02-25 | Via CM Phases 1–5 (token budgets + lossless compaction + memory search) |
| SDR 2.14: Circuit Breaker | 🔲 NOT STARTED | - | |
| SDR 2.15: Hub Relay SPOF | 🔴 Open | 2026-03-09 | New issue — in-memory SSE queue per hub; no cross-instance fan-out |
| SDR 2.16: Remaining httpx leak | ✅ RESOLVED | 2026-03-13 | Both methods fixed: `get_agent_card_from_url()` uses `async with`; `get_a2a_client()` removed (dead code) |

### 6.6 Hybro Hub Integration (Phase 2a — Completed 2026-03-09)

**Reference**: `GATEWAY_API.md`, `HYBRO_HUB_DESIGN.md`, `HYBRO_TRUST_LAYER_DESIGN.md`

**New Models:**
- `models/hub.py` — `HubConfig`, `HubAgentSync`, `HubPublishRequest`, `HubStatus`, `RelayToHubEvent`
- `models/gateway.py` — Gateway API request/response models for Python SDK integration

**New Endpoints:**
- `api/gateway.py` — External agent access via API keys (`POST /gateway/sendTask`, `GET /gateway/getTask`)
- `api/relay.py` — Hub-to-backend relay (`GET /relay/events` SSE, `POST /relay/publish`)
- `api/hub.py` — Hub management and agent catalog sync

**New Services:**
- `services/relay_service.py` — In-memory `asyncio.Queue` per hub, offline queue with TTL for brief disconnects, heartbeat monitoring (30s interval)
- `services/gateway_service.py` — Gateway orchestration, agent resolution, dispatch middleware routing
- `services/gateway_rate_limit_service.py` — MongoDB-backed sliding-window rate limiting with TTL indexes (2-hour window)

**New Middleware:**
- `common/middleware/discovery_cors_middleware.py` — Permissive CORS for `/discovery/*`, `/gateway/*`, `/relay/*` paths

**Database Migrations:**
- `database/migration/add_gateway_api_requests_indexes.py` — TTL indexes for gateway rate limiting
- `database/migration/add_hub_indexes.py` — Unique indexes on `hubs.hub_id`, compound index on `agents.(hub_id, local_agent_id)`
- `database/migration/deduplicate_agents.py` — Agent URL normalization and deduplication with `normalized_url` backfill

**Known Risks:**
- Hub Relay SPOF (SDR 2.15) — relay state is in-memory; cannot scale horizontally
- Permissive CORS surface (SDR 2.12 note) — gateway/relay paths open to all origins

### 6.7 Additional Backend Additions (Since Feb 25)

**Agent Response Handler** (`modules/agent_response_handler.py`):
- Transport layer for agent response handling (Phases 1-2 of response refactoring)
- Separates transport concerns from business logic in `AgentMessageProcessor`

**Bedrock Claude Support** (`services/bedrock_service.py`):
- AWS Bedrock integration for Claude Opus 4.6 as supervisor LLM
- Feature-flagged; configurable alongside existing OpenAI/Gemini options

**Processing Pipeline Models** (`models/processing.py`):
- `ProcessingStatus` enum: `SUCCESS`, `FAILED`, `CANCELED`, `PAUSED`, `RELAY_DISPATCHED`, `AWAITING_INPUT`
- `ProcessingResult` and `ProcessingContext` dataclasses for structured pipeline state

**Agent Liveness & Health:**
- `services/agent_liveness_service.py` — On-demand probes + hub heartbeat monitoring
- `services/agent_health_service.py` — Periodic health checks with status tracking

**Background Jobs:**
- `jobs/cleanup_orphaned_uploads.py` — S3 orphaned upload cleanup
- `jobs/compaction_sweep.py` — Background memory compaction (supplements on-demand trigger from CM Phase 3)
- `jobs/stale_task_checker.py` — Expanded with `_recover_stuck_supervisor_trajectories()` and `_process_recovered_supervisor_message()` for V2 supervisor recovery
