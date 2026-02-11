# System Design Review: Hybro Frontend + Multi-Agents Backend

**Date**: February 10, 2026
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
