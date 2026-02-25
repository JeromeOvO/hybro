# System Design Review: Hybro Frontend + Multi-Agents Backend

**Date**: February 23, 2026 (updated from Feb 22)
**Scope**: Full-stack architecture review covering `hybro-frontend` and `multi-agents-backend`

---

## 1. Architecture Overview

### Frontend (`hybro-frontend`)

| Layer            | Technology                               |
| ---------------- | ---------------------------------------- |
| Framework        | Next.js 15 (App Router) + React 19       |
| Auth             | Clerk (`@clerk/nextjs` ^6.24.0)          |
| Server State     | TanStack React Query 5                   |
| Client State     | Zustand 5                                |
| Styling          | Tailwind CSS v4 + shadcn/ui (Radix)      |
| Real-time        | SSE via native `EventSource`             |
| Forms            | React Hook Form + Zod 4                  |
| Markdown         | react-markdown + remark-gfm + rehype     |

The frontend serves two portals via subdomain routing:
- **Consumer** (`hybro.ai` → `/c/*`): Chat rooms, agent marketplace
- **Developer** (`developer.hybro.ai` → `/d/*`): Agent registration, inspector

### Backend (`multi-agents-backend`)

| Layer            | Technology                               |
| ---------------- | ---------------------------------------- |
| Framework        | FastAPI (async Python, Uvicorn)           |
| Database         | MongoDB (Motor async driver)              |
| Vector DB        | Pinecone (semantic agent matching)        |
| Auth             | Clerk JWT + API Key (SHA-256)             |
| Agent Protocol   | A2A (Agent-to-Agent) via `a2a-sdk`        |
| Real-time        | SSE via `sse-starlette`                   |
| LLMs             | OpenAI (GPT-5-mini), Google Gemini        |
| Config           | pydantic-settings                         |
| Observability    | OpenTelemetry + Loguru                    |

### Core Data Flow

```
User types message
    └─→ RoomChatInput.onSubmit()
        └─→ useRoomWebhook.sendUserMessage()
            ├─→ Zustand: addLiveMessage (optimistic user msg + processing placeholder)
            ├─→ POST /api/v1/roomCenter/sendMessage (creates message in DB)
            ├─→ POST /api/v1/orchestrationCenter/processRoomUserMessage (fire & forget)
            └─→ SSE stream delivers events:
                ├─→ task_submitted  → addLiveMessage (task bubble)
                ├─→ task_update     → replaceLiveMessage (update task status)
                ├─→ agent_response  → addLiveMessage (final agent message)
                └─→ processing_status=completed → setProcessing(false)

Final UI = React Query cached messages ∪ Zustand live messages (deduped by ID, sorted)
```

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

### 2.3 HIGH: httpx Client Leak — Connections Never Closed

**Location**: `services/a2a_service.py` (`A2AService.create_a2a_client`, `get_a2a_client`, `get_agent_card_from_url`)

Every A2A interaction creates a new `httpx.AsyncClient` with a 600-second timeout that is never explicitly closed:

```python
async def create_a2a_client(self, agent_card: AgentCard) -> A2AClient:
    httpx_client = httpx.AsyncClient(timeout=600.0)  # never closed
    a2a_client = A2AClient(httpx_client, agent_card=agent_card)
    return a2a_client
```

**Impact**:
- Each call leaks an HTTP connection. Under load, this exhausts file descriptors and OS-level connection limits.
- With the 600-second timeout, connections remain open far longer than necessary.
- The same pattern repeats in `get_agent_card_from_url` and `get_a2a_client`.

**Downstream dependency**: The HITL design adds a new `reply_to_task()` method that calls `_get_a2a_client()`, inheriting this leak. The fix must be applied before or alongside HITL implementation.

**Recommendation**:
- The same pattern repeats in `get_agent_card_from_url` and `get_a2a_client`.

**Recommendation**:
- Use a **shared `httpx.AsyncClient` instance** (connection pool) as a class attribute, created once at startup and closed on shutdown.
- Alternatively, wrap each usage in `async with httpx.AsyncClient() as client:` to ensure automatic cleanup.
- Consider reducing the 600s timeout to a more reasonable value (30-60s) with per-operation overrides where needed.

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

### 2.5 HIGH: Potential Double-Processing Race Condition

**Location**: `hybro-frontend/src/hooks/useRoomWebhook.ts` (`sendUserMessage`)

The frontend sends two requests:

```typescript
// Step 1: Create message (backend may auto-trigger processing)
const createResponse = await SendMessage(roomId, userInput, ...)

// Step 3: Fire-and-forget processing call (kept for "redundancy")
processRoomUserMessage({...}).catch(error => {
    console.log('backend auto-processes anyway:', error)
})
```

The comment indicates the backend auto-triggers processing, but the explicit call is retained "for redundancy."

**Impact**:
- If both the auto-trigger and the explicit call succeed, the same user message is processed twice, leading to duplicate agent calls, duplicate SSE events, and confused UI state.
- The backend's `process_room_user_message` does not appear to have an idempotency guard against concurrent invocations for the same user message.

**Recommendation**:
- **Option A**: Remove the redundant `processRoomUserMessage` call entirely — trust the backend auto-trigger.
- **Option B**: Add an idempotency guard on the backend (e.g., an atomic `processing_started` flag on the user message document, checked before starting work).

---

### 2.6 HIGH: JWT Token Exposed in SSE Query Parameter

**Location**: `hybro-frontend/src/lib/api/sse.ts`, `multi-agents-backend/api/sse.py`

Because `EventSource` cannot send custom HTTP headers, the Clerk JWT is passed as a URL query parameter:

```
GET /api/v1/sse/room/{roomId}/stream?token=<clerk-jwt>
```

**Impact**:
- Tokens appear in **server access logs**, **CDN/proxy logs**, **browser history**, and **referrer headers**.
- URLs containing tokens can be cached by intermediate proxies.
- If an attacker obtains the URL, they can replay the SSE connection and receive all room events.

**Recommendation**:
- Issue a **short-lived, single-use SSE token** (e.g., a 30-second nonce exchanged via a POST endpoint) instead of exposing the main Clerk JWT.
- Alternatively, migrate to a fetch-based SSE implementation using `ReadableStream` (which supports custom headers) or use WebSockets.
- At minimum, ensure server logs redact the `token` query parameter.

---

### 2.7 MEDIUM: Unbounded Memory Growth — Cancelled Messages Set

**Location**: `services/sse_services.py` (`SSEManager.cancelled_messages`)

```python
self.cancelled_messages: set[str] = set()
```

Cancelled message IDs are added to this set but only removed when the specific workflow calls `clear_cancellation()`. If a cancellation happens after the workflow is already complete, or if `clear_cancellation()` is never called (e.g., due to an exception), the ID remains in memory forever.

**Impact**: Under heavy usage with frequent cancellations, this set grows without bound, consuming increasing memory.

**Recommendation**: Store a `(message_id, timestamp)` tuple and add periodic cleanup that prunes entries older than a threshold (e.g., 30 minutes). Alternatively, use a TTL-based cache like `cachetools.TTLCache`.

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

**Location**: `hybro-frontend/src/hooks/useRoomWebhook.ts` (`sendUserMessage`)

```typescript
const tempMessageId = `temp-${Date.now()}-...`
addLiveMessage(roomId, { id: tempMessageId, ... })
// ... API call returns real messageId ...
replaceLiveMessage(roomId, tempMessageId, { ...optimisticUserMessage, id: messageId })
```

Between adding the temp message and replacing it with the real ID, any SSE event referencing the real `messageId` (e.g., `user_message` echo) won't match the temp ID.

**Impact**: If the SSE `user_message` event arrives before `replaceLiveMessage` completes (possible with fast backends), the UI briefly shows duplicate user messages. The Zustand deduplication logic works by ID, so the temp and real IDs are treated as separate messages.

**Recommendation**:
- Deduplicate by `(content, user_id, timestamp_within_threshold)` in addition to ID.
- Or: delay adding the optimistic message until the real ID is available (sacrificing instant feedback for correctness).
- Or: use a server-assigned ID by making the message creation synchronous (wait for `SendMessage` response before adding to UI).

---

### 2.10 MEDIUM: No Input Validation or Size Limits on User Messages

**Location**: Frontend chat input, backend `SendMessage` endpoint

No explicit validation on user message content size was found in either codebase.

**Impact**:
- A malicious or accidental extremely large message can bloat MongoDB documents (which have a 16MB BSON limit).
- Large messages cause OOM when building conversation history context.
- LLM token limits can overflow when the message is passed as context, leading to unexpected errors or truncation.

**Recommendation**: Add message size validation (e.g., max 10,000 characters) on both frontend (immediate feedback) and backend (authoritative enforcement).

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

### 2.12 LOW-MEDIUM: Overly Permissive CORS Configuration

**Location**: `main.py`

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)
```

**Impact**: While `allow_origins` is configurable, `allow_methods=["*"]` and `allow_headers=["*"]` combined with `allow_credentials=True` expands the attack surface unnecessarily. Any origin in the allowed list can send any HTTP method with any header.

**Recommendation**: Restrict to the specific methods (`GET`, `POST`, `PUT`, `DELETE`, `OPTIONS`) and headers (`Authorization`, `Content-Type`, `X-API-Key`) actually used by the frontend.

---

### 2.13 LOW: Stale Task Checker Creates Unbounded Background Tasks

**Location**: `jobs/stale_task_checker.py` (`_recover_orphaned_messages`)

```python
asyncio.create_task(
    self._process_orphaned_user_message(room_message_center, request)
)
```

Orphaned message recovery spawns fire-and-forget `asyncio.create_task` calls with no concurrency limit.

**Impact**: If a large backlog of orphaned messages accumulates (e.g., after a prolonged outage), the checker spawns many concurrent processing tasks, potentially saturating the event loop and starving normal request handling.

**Recommendation**: Use an `asyncio.Semaphore` to cap concurrent recovery tasks (e.g., max 5 at a time).

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

## 3. Summary

| #    | Severity   | Issue                                            | Impact                     |
| ---- | ---------- | ------------------------------------------------ | -------------------------- |
| 2.1  | Critical   | In-memory SSE state prevents horizontal scaling   | Cannot scale backend       |
| 2.2  | Critical   | No durable task queue, all in-process             | Lost work on crash         |
| 2.3  | High       | httpx client leak (connections never closed)       | Resource exhaustion        |
| 2.4  | High       | Sequential agent processing                       | Poor latency               |
| 2.5  | High       | Potential double-processing race condition         | Duplicate agent calls      |
| 2.6  | High       | JWT token in SSE query parameter                   | Token exposure             |
| 2.7  | Medium     | Unbounded `cancelled_messages` set                 | Memory leak                |
| 2.8  | Medium     | No MongoDB transactions for multi-step ops         | Inconsistent state         |
| 2.9  | Medium     | Optimistic update ID mismatch window               | Duplicate UI messages      |
| 2.10 | Medium     | No message size validation                         | DoS / OOM risk             |
| 2.11 | Medium     | Unbounded conversation memory                      | Cost / quality degradation |
| 2.12 | Low-Medium | Overly permissive CORS                             | Attack surface             |
| 2.13 | Low        | Unbounded orphan recovery tasks                    | Event loop saturation      |
| 2.14 | Low        | No circuit breaker for external agents             | Cascading failures         |

### Priority Recommendations

**Phase 1 — Production Blockers** (Issues 2.1, 2.2):
- Add Redis Pub/Sub for SSE event fan-out across instances.
- Introduce a durable task queue (Celery/Dramatiq/arq) for agent message processing.

**Phase 2 — Reliability** (Issues 2.3, 2.5, 2.6, 2.8):
- Fix httpx client lifecycle (shared pool or context manager).
- Remove duplicate `processRoomUserMessage` call or add backend idempotency.
- Replace SSE JWT query param with short-lived nonce.
- Wrap multi-document writes in MongoDB transactions.

**Phase 3 — Performance & Scalability** (Issues 2.4, 2.10, 2.11, 2.14):
- Parallelize independent agent execution.
- Add message size limits.
- Implement conversation memory windowing/summarization.
- Add circuit breakers for external agent calls.

**Phase 4 — Hardening** (Issues 2.7, 2.9, 2.12, 2.13):
- TTL-based cleanup for cancellation set.
- Improve optimistic update deduplication.
- Tighten CORS configuration.
- Cap concurrent orphan recovery tasks.

---

## 4. Issue Status Tracking

This section tracks the resolution status of each identified issue. Updated as fixes are implemented.

| #    | Issue                                            | Status       | Resolution Notes                                                                 |
| ---- | ------------------------------------------------ | ------------ | -------------------------------------------------------------------------------- |
| 2.1  | In-memory SSE state prevents horizontal scaling   | 🔴 Open      | **Blocker for HITL** — HITL design (§3) depends on this fix; requires Redis Pub/Sub |
| 2.2  | No durable task queue, all in-process             | 🔴 Open      | Production blocker; no work started                                              |
| 2.3  | httpx client leak (connections never closed)       | 🔴 Open      | **Blocker for HITL** — `reply_to_task()` inherits this leak (HITL Risk 17)       |
| 2.4  | Sequential agent processing                       | 🟡 Partial   | Supervisor V2 supports parallel dispatch via `asyncio.gather`; V1 queue still sequential |
| 2.5  | Potential double-processing race condition         | 🔴 Open      | No idempotency guard implemented                                                 |
| 2.6  | JWT token in SSE query parameter                   | 🔴 Open      | Security risk; no work started                                                   |
| 2.7  | Unbounded `cancelled_messages` set                 | 🔴 Open      | Memory leak; no TTL cleanup implemented                                          |
| 2.8  | No MongoDB transactions for multi-step ops         | 🔴 Open      | Consistency risk; no work started                                                |
| 2.9  | Optimistic update ID mismatch window               | 🔴 Open      | Frontend deduplication issue; no work started                                    |
| 2.10 | No message size validation                         | 🔴 Open      | DoS risk; no validation implemented                                              |
| 2.11 | Unbounded conversation memory                      | 🟡 Partial   | Context Memory design (§4.2, §6) addresses this; implementation in progress      |
| 2.12 | Overly permissive CORS                             | 🔴 Open      | Low priority; no work started                                                    |
| 2.13 | Unbounded orphan recovery tasks                    | 🔴 Open      | No semaphore implemented                                                         |
| 2.14 | No circuit breaker for external agents             | 🔴 Open      | No circuit breaker implemented                                                   |

**Legend:**
- 🔴 Open — Not started or blocked
- 🟡 Partial — Work in progress or partially addressed
- 🟢 Resolved — Fix implemented and verified

---

## 5. Unified Implementation Dependency Graph

This section maps dependencies across all three design documents:
- **SYSTEM_DESIGN_REVIEW.md** (this document) — Infrastructure issues
- **CONTEXT_MEMORY_SYSTEM_DESIGN.md** — Memory and context architecture
- **HITL_DESIGN.md** — Human-in-the-loop interactions

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
│                                                                                  │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  LAYER 0: INFRASTRUCTURE BLOCKERS (must fix first)                               │
│  ════════════════════════════════════════════════                                │
│                                                                                  │
│  ┌──────────────────────┐         ┌──────────────────────┐                       │
│  │ [SDR 2.1] Redis      │         │ [SDR 2.3] httpx      │                       │
│  │ Pub/Sub for SSE      │         │ Client Lifecycle     │                       │
│  │ (horizontal scaling) │         │ (connection leak)    │                       │
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
│  └──────────────────────┘  └──────────────────────┘  └──────────────────────┘    │
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
         └──▶ (unblocks reply_to_task() without connection leak)
```

The **critical path** for full Context Memory functionality:

```
[CM Phase 1] Data Models ──▶ [CM Phase 2] Context Assembly ──▶ [CM Phase 3] Compaction
         │                           │
         │                           └──▶ [SDR 2.11] Unbounded Memory (RESOLVED)
         │
         └──▶ [HITL Phase 7] HITL Turn Recording (depends on ConversationTurn model)
```

### 5.3 Recommended Implementation Order

Based on dependency analysis and impact:

| Priority | Item | Rationale |
|----------|------|-----------|
| **P0** | [SDR 2.1] Redis Pub/Sub | Blocks HITL multi-instance delivery; production blocker |
| **P0** | [SDR 2.3] httpx client fix | Blocks HITL reply_to_task; resource exhaustion risk |
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
| **Infrastructure** | [SDR 2.1], [SDR 2.2], [SDR 2.3] | Platform/DevOps |
| **Context Memory** | [CM Phase 1-5] | Backend |
| **HITL Backend** | [HITL Phase 1-5, 7-8] | Backend |
| **HITL Frontend** | [HITL Phase 6] | Frontend |
| **Security** | [SDR 2.6], [SDR 2.12] | Security |

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
| CM Phase 5: Supervisor V2 Integration | 🔲 NOT STARTED | - | Depends on Phase 2 ✅ |

#### CM Phase 1 Details (Completed 2026-02-23)

**Files Created:**
- `models/compaction.py` — `StorageType` enum, `ContentReference`, `StoredContent`, `CompactionResult`, `CompactionConfig`
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
- Background compaction job not yet implemented (§6.9)
  - Current implementation is on-demand only

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

### 6.2 HITL Phases

| Phase | Status | Date | Notes |
|-------|--------|------|-------|
| HITL Phase 1: Foundation | 🔲 NOT STARTED | - | |
| HITL Phase 2: V1 Shim | 🔲 NOT STARTED | - | |
| HITL Phase 3: V2 Queue Integration | 🔲 NOT STARTED | - | |
| HITL Phase 4: Response Endpoint | 🔲 NOT STARTED | - | |
| HITL Phase 5: Risk Mitigations | 🔲 NOT STARTED | - | |
| HITL Phase 6: Frontend | 🔲 NOT STARTED | - | |
| HITL Phase 7: Turn Recording | 🔲 NOT STARTED | - | Depends on CM Phase 1 ✅ |
| HITL Phase 8: Legacy Shim Removal | 🔲 NOT STARTED | - | |

### 6.3 SDR Issues

| Issue | Status | Date | Notes |
|-------|--------|------|-------|
| SDR 2.1: Redis Pub/Sub | 🔲 NOT STARTED | - | P0 blocker |
| SDR 2.2: Durable Task Queue | 🔲 NOT STARTED | - | |
| SDR 2.3: httpx client fix | 🔲 NOT STARTED | - | P0 blocker |
| SDR 2.5: Double-Processing Guard | 🔲 NOT STARTED | - | |
| SDR 2.6: SSE JWT Token Security | 🔲 NOT STARTED | - | |
| SDR 2.7: TTL for cancelled_messages | 🔲 NOT STARTED | - | |
| SDR 2.8: MongoDB Transactions | 🔲 NOT STARTED | - | |
| SDR 2.11: Unbounded Memory | ✅ RESOLVED | 2026-02-23 | Via CM Phase 1 |
| SDR 2.14: Circuit Breaker | 🔲 NOT STARTED | - | |
