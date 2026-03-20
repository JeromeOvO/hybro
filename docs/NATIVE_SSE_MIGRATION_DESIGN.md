# SSE Transport Layer — Design & Strategic Assessment

## 1. Problem Statement

The frontend-facing SSE system in `api/sse.py` and `services/sse_services.py` uses a
**hand-rolled implementation** built on `StreamingResponse` with manual `data: {json}\n\n`
formatting, manual `Cache-Control`/`Connection` headers, and a custom heartbeat mechanism.

FastAPI 0.135.0+ provides **native SSE support** via `fastapi.sse.EventSourceResponse` and
`ServerSentEvent`, which handles keep-alive pings, proxy-busting headers, and Pydantic
serialization automatically.

**Current FastAPI version: 0.116.1** — upgrade required before any native SSE changes.

---

## 2. Strategic Assessment

> **Verdict: The native SSE migration is not the highest-value use of engineering time.**
> The current hand-rolled SSE works. The system's real reliability problems are in the
> notification layer above SSE, not the transport layer itself. This section explains
> why and recommends an alternative priority ordering.

### 2.1 The System Is Designed to Not Trust SSE

Every other design doc is moving reliability **upstream** — into the DB write and
notification layer. SSE is being reduced to a best-effort push channel with multiple
fallback mechanisms:

| Design Doc | Principle | Implication for SSE |
|---|---|---|
| `REFACTOR_TASK_NOTIFICATION.md` | "The DB is the source of truth. SSE is a fan-out side-effect." | SSE delivery failures are tolerated; DB + idempotent retry handles correctness |
| `CANCELLATION_WORKFLOW_ISSUES.md` (A-5) | Idempotent terminal status sends via TTL cache | Double-sends at the SSE layer are made harmless upstream |
| `LONG_RUNNING_TASKS_DESIGN.md` | Webhook + stale-task-checker + frontend polling fallback | SSE is one of three delivery paths; the others compensate for SSE failures |
| Frontend | 10-minute stale-task hydration check on page refresh | Client-side recovery for any missed SSE events |

Investing in SSE transport polish (native framing, auto-headers, Pydantic serialization)
has diminishing returns when the layers above and below SSE are designed to tolerate
its failures.

### 2.2 Phase-by-Phase Value Assessment

| Phase | Real User-Facing Value | Risk | Opportunity Cost |
|-------|----------------------|------|-----------------|
| Phase 1: FastAPI upgrade | None (prerequisite) | Medium — ~20 minor version jump | Blocks other work if it breaks things |
| Phase 2: Native EventSourceResponse | `X-Accel-Buffering: no` header (fixable in 1 line without migration) | Low-medium — `raw_data` fragility | 0.5–1.5 days vs. higher-priority work |
| Phase 3: Pydantic event models | Type safety (no user impact) | Medium — conflicts with REFACTOR_TASK_NOTIFICATION | 1 day; directly conflicts with higher-priority refactor |
| Phase 4: SSE event field routing | Marginally cleaner frontend dispatch | High — coordinated breaking change | 1–2 days for cosmetic improvement |
| Phase 5: Last-Event-ID | **Real value** — reconnection resilience | Medium | 2–3 days; does NOT require Phases 1–4 |

Phase 5 is the only phase with genuine user-facing impact. Everything else is developer
ergonomics for a transport layer that the system is designed to not fully trust.

### 2.3 Phase 3 Fights REFACTOR_TASK_NOTIFICATION

Both refactors want to change what flows through the same pipe:

```
SSEManager.broadcast_to_room() → SSEConnection.send_message() → queue
```

The notification refactor makes this path DB-backed and idempotent, fixing real bugs
(stuck task bubbles, polling timeout silence, `ctx=None` silenced notifications). Phase 3
changes the queue payload from JSON strings to dicts. These cannot be independently
refactored without coordination — and the notification refactor fixes real bugs while
Phase 3 fixes nothing user-visible.

### 2.4 Phase 4 Creates Fragility for an Expanding Event Surface

The system is actively adding new SSE event types:
- HITL: `hitl_input_required`, `hitl_response`
- Workflow engine: `workflow_progress`, `workflow_completed`, `workflow_approval_required`
- Supervisor V2: `supervisor_progress`

Phase 4 (named SSE events with `addEventListener`) means every new event type requires
a coordinated frontend handler addition. A missing handler causes **silent data loss** —
named events not caught by any listener are dropped without error. The current
`onmessage` + JSON `type` dispatch is more resilient: unknown event types are received
and can be logged or ignored explicitly. The current design is extensible by default;
Phase 4 makes it fragile by default.

### 2.5 Phase 5 Does Not Depend on Native SSE

The design originally presented a linear dependency:

```
Phase 1 → Phase 2 → Phase 3/4/5
```

But `Last-Event-ID` can be implemented with the current `StreamingResponse`:

```python
yield f"id: {seq}\ndata: {message}\n\n"
```

The SSE `id:` field is just a line in the text protocol. `StreamingResponse` can emit it.
The browser `EventSource` sends `Last-Event-ID` on reconnect regardless of whether the
server uses native `EventSourceResponse` or hand-rolled framing. The Phase 1→2→5
dependency chain is artificial.

### 2.6 Long-Term Transport Considerations

The system is heading toward requirements that SSE cannot fully serve:

| Future Requirement | SSE Limitation | Alternative |
|---|---|---|
| Multi-instance (CONCURRENCY_ROADMAP Layer C) | No shared event bus; each instance manages its own connections | Redis pub/sub or MongoDB change streams as event fan-out |
| Bidirectional interaction (HITL replies, cancel) | Unidirectional; cancel requires separate HTTP endpoint | WebSocket or hybrid |
| Auth without URL tokens | `EventSource` API cannot send custom headers; auth token goes in query param (logged in URLs, server logs) | WebSocket handshake supports headers |
| Durable event delivery (Workflow Engine) | No built-in persistence or replay | DB-backed notification layer (already being built) |

This does not mean "switch to WebSockets now." It means: don't over-invest in polishing
SSE framing when the transport protocol itself may need to evolve. The right long-term
investment is in the notification layer (REFACTOR_TASK_NOTIFICATION), which is
transport-agnostic.

---

## 3. Recommended Priority Ordering

### Immediate (this week)

**3.1 Add `X-Accel-Buffering: no` header — 1 line, no migration required.**

This is the only real bug the migration doc identifies. Fix it directly:

```python
# api/sse.py — add to existing headers dict
return StreamingResponse(
    event_generator(),
    media_type="text/event-stream",
    headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Content-Type": "text/event-stream",
        "X-Accel-Buffering": "no",
    },
)
```

### Short-term (next 2–4 weeks)

**3.2 Ship REFACTOR_TASK_NOTIFICATION** — fixes real user-facing bugs (stuck task
bubbles, polling timeout silence, `ctx=None` silenced notifications). This is the
highest-value SSE-adjacent work. See `REFACTOR_TASK_NOTIFICATION.md`.

**3.3 Upgrade FastAPI independently** — as a maintenance task on its own schedule,
not gated on the SSE migration. Pin to the latest version, test all endpoints, ship.
This unblocks native SSE as a future option without committing to using it.

### Medium-term (1–3 months)

**3.4 Implement event buffering + `Last-Event-ID` on the current `StreamingResponse`.**

This is the only phase from the original migration plan with real user value. It can be
implemented without native SSE:

```python
# api/sse.py — works with current StreamingResponse
async def event_generator():
    connection = await sse_manager.add_connection(room_id)

    # Replay missed events on reconnect
    if last_event_id is not None:
        for seq, event_data in sse_manager.get_events_since(room_id, last_event_id):
            yield f"id: {seq}\ndata: {event_data}\n\n"

    while connection.is_active:
        message, seq = await connection.get_message_with_seq(timeout=30.0)
        if message:
            yield f"id: {seq}\ndata: {message}\n\n"
        elif message is None:
            # Timeout — send heartbeat (keep-alive)
            yield f"data: {json.dumps({'type': 'heartbeat', ...})}\n\n"
```

Infrastructure requirements (same as the original Phase 5):

| Component | Change |
|-----------|--------|
| `SSEManager` | Add per-room atomic counter and ring buffer |
| `SSEConnection` | Return `(message, seq_num)` tuples from `get_message_with_seq()` |
| `broadcast_to_room()` | Assign seq num, store in buffer, then broadcast |
| `api/sse.py` | Accept `Last-Event-ID` header, replay from buffer, emit `id:` fields |
| Next.js proxy | Verify `Last-Event-ID` header passes through (standard fetch does) |
| Frontend | No changes — browser `EventSource` sends `Last-Event-ID` automatically |

For multi-instance deployments, a shared store (Redis) would be needed for cross-instance
replay. Single-instance replay covers the common case (brief network blip) and can ship
first.

### Long-term / opportunistic

**3.5 Evaluate native SSE migration** — after the FastAPI upgrade has been stable for
a few weeks and the notification refactor has landed, the migration becomes lower-risk
and the `raw_data` vs `data` decision is clearer (because the queue payload format will
have been settled by the notification refactor). At that point, Phase 2 (replace
`StreamingResponse` with `EventSourceResponse`) is a small, safe cleanup.

**3.6 Evaluate transport protocol** — when the Workflow Engine or HITL ships, evaluate
whether SSE remains the right transport or whether a hybrid SSE + WebSocket
architecture better serves the system's needs. The notification layer
(REFACTOR_TASK_NOTIFICATION) is transport-agnostic by design, so this decision
does not block any current work.

---

## 4. Current Architecture

### 4.1 Two Separate SSE Systems

The codebase has two independent SSE mechanisms:

| System | Purpose | Library | Location |
|--------|---------|---------|----------|
| **Frontend SSE** | Push events to browser (tokens, status, tasks) | `StreamingResponse` (manual) | `api/sse.py`, `services/sse_services.py` |
| **A2A Inter-Agent SSE** | Stream A2A protocol responses between agents | `sse-starlette` | `common/server/server.py` |

This doc covers **Frontend SSE only**. The A2A system is a standalone `Starlette`
app and will continue using `sse-starlette`.

### 4.2 Frontend SSE Data Flow

```
Browser (EventSource)
  │
  │  GET /api/sse/room/{room_id}/stream?token=...
  ▼
Next.js SSE Proxy (src/app/api/sse/[...endpoint]/route.ts)
  │
  │  Proxies raw bytes to/from backend
  ▼
api/sse.py  →  StreamingResponse(event_generator())
  │
  │  SSEConnection.get_message() ← asyncio.Queue
  ▼
SSEManager.broadcast_to_room()
  ▲         ▲               ▲
  │         │               │
RoomMessage ResponseProcessor  Webhooks/Notifications
Center      (tokens, artifacts) (task updates)
```

### 4.3 Current SSE Event Envelope

All events use a single format, sent as `data: {json}\n\n`:

```json
{
  "type": "agent_token",
  "timestamp": "2025-...",
  "room_id": "room_123",
  "data": { "message_id": "...", "token": "Hello", ... }
}
```

The `type` field is the discriminator. The frontend parses every message via `onmessage`
and dispatches on `message.type`.

### 4.4 Event Types

| Event Type | Source | Frequency |
|------------|--------|-----------|
| `connected` | `api/sse.py` (inline) | Once per connection |
| `heartbeat` | `SSEConnection.get_message()` | Every 30s idle |
| `user_message` | `SSEManager.send_user_message()` | Per user input |
| `agent_response` | `SSEManager.send_agent_response()` | Per complete response |
| `agent_token` | `SSEManager.send_agent_token()` | **High frequency** — per token |
| `artifact_update` | `SSEManager.send_artifact_update()` | Per artifact chunk |
| `processing_status` | `SSEManager.send_processing_status()` | Per status change |
| `task_submitted` | `SSEManager.send_task_submitted()` | Per task submission |
| `task_update` | `SSEManager.send_task_update()` | Per task state change |
| `error` | `SSEManager.send_error/send_rate_limit_error()` | On errors |

**Planned additions** (not yet implemented):
- `hitl_input_required`, `hitl_response` (HITL_DESIGN.md)
- `workflow_progress`, `workflow_completed`, `workflow_approval_required` (WORKFLOW_ENGINE_ROADMAP.md)
- `supervisor_progress` (SUPERVISOR_V2_DESIGN.md)

### 4.5 Frontend Consumption

- **`src/lib/api/sse.ts`** — `SSEConnection` class wraps browser `EventSource`
- Uses `onmessage` (catches all unnamed events), parses JSON, dispatches on `message.type`
- Silently drops `heartbeat` events
- Has its own reconnect logic (up to 5 attempts with linear backoff)
- **`src/lib/types/sse.ts`** — `SSEMessage` interface with `type` discriminator union
- **`src/app/api/sse/[...endpoint]/route.ts`** — Next.js proxy that pipes raw SSE bytes

---

## 5. Native SSE Migration (Deferred)

> **Status: Deferred** — not recommended as immediate work. Preserved here as a
> reference for when the FastAPI upgrade has shipped independently and the
> notification refactor has settled the queue payload format.

### 5.1 Prerequisite: Upgrade FastAPI

**Goal:** Get to FastAPI >= 0.135.0 without behavior changes.

**Steps:**

1. Pin `fastapi>=0.135.0` in `pyproject.toml`
2. Check Starlette compatibility — `starlette>=0.46.2` is already pinned, but
   FastAPI 0.135+ may bundle a newer Starlette. Verify no middleware or routing
   breakage.
3. Check for deprecated APIs:
   - `jsonable_encoder` usage (still supported but check for changes)
   - Response class imports
   - Dependency injection edge cases
4. Run full test suite and manual smoke test of all SSE event types
5. Verify OpenTelemetry instrumentation (`opentelemetry-instrumentation-fastapi`)
   is compatible with the new version. Note: `EventSourceResponse` may keep
   request spans open for the entire SSE connection duration (hours), which can
   cause span buffer overflow. Verify whether the OTEL middleware closes the span
   on response header send or response body completion.

**Risk:** Medium. ~20 minor version jump. Should be tested against all endpoints,
not just SSE. Ship this as its own task independent of the SSE migration.

### 5.2 Replace `StreamingResponse` with native `EventSourceResponse`

**Goal:** Replace `StreamingResponse` with `fastapi.sse.EventSourceResponse` in
`api/sse.py` while maintaining **wire-compatible** output (no frontend changes).

**Key constraint:** The frontend `onmessage` handler expects every event to arrive
as unnamed SSE data (`data: {...}\n\n`). Must NOT introduce `event:` fields,
otherwise `onmessage` silently drops those events.

**Before:**

```python
from fastapi.responses import StreamingResponse

@router.get("/sse/room/{room_id}/stream")
async def stream_room_messages(...):
    async def event_generator():
        connection = await sse_manager.add_connection(room_id)
        yield f"data: {json.dumps(connected_message)}\n\n"
        while connection.is_active:
            message = await connection.get_message(timeout=30.0)
            if message:
                yield f"data: {message}\n\n"
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "Content-Type": "text/event-stream"},
    )
```

**After:**

```python
from collections.abc import AsyncIterable
from fastapi.sse import EventSourceResponse, ServerSentEvent

@router.get("/sse/room/{room_id}/stream", response_class=EventSourceResponse)
async def stream_room_messages(
    room_id: str = Path(..., description="room ID"),
    user: ClerkUser = Depends(get_current_user_with_query_token),
) -> AsyncIterable[ServerSentEvent]:
    connection = None
    try:
        connection = await sse_manager.add_connection(room_id)
        logger.info(f"SSE stream started for room {room_id}")

        connected_message = json.dumps({
            "type": "connected",
            "room_id": room_id,
            "connection_id": connection.connection_id,
            "timestamp": utcnow().isoformat(),
        })
        yield ServerSentEvent(raw_data=connected_message)

        while connection.is_active:
            try:
                message = await connection.get_message()
                if message:
                    yield ServerSentEvent(raw_data=message)
            except Exception as e:
                logger.error(f"Error in SSE stream for room {room_id}: {e}")
                break

    except Exception as e:
        logger.error(f"SSE connection error for room {room_id}: {e}")
    finally:
        if connection:
            await sse_manager.remove_connection(room_id, connection.connection_id)
            logger.info(f"SSE stream closed for room {room_id}")
```

**Critical detail: `raw_data` not `data`.**

`SSEConnection.get_message()` returns a pre-serialized JSON string. Using
`ServerSentEvent(data=json_string)` would double-encode it (wrapping the string
in quotes). `raw_data` passes the string through as-is, preserving the current
wire format.

**What native SSE provides automatically:**

| Feature | Before (manual) | After (native) |
|---------|------------------|-----------------|
| Keep-alive ping | Custom 30s heartbeat in `get_message()` | Built-in `: ping` comment every 15s |
| `Cache-Control: no-cache` | Manual header | Automatic |
| `X-Accel-Buffering: no` | **Missing** (fix independently, see Section 3.1) | Automatic |
| `Content-Type` | Manual header (redundant with `media_type`) | Automatic |
| SSE framing (`data: ...\n\n`) | Manual f-string formatting | Handled by `ServerSentEvent` |

**Heartbeat changes:** With native 15s keep-alive pings, the heartbeat becomes
redundant. Keep the 30s timeout for dead-connection detection but return `None`
instead of heartbeat JSON:

```python
async def get_message(self, timeout: float = 30.0) -> str | None:
    try:
        return await asyncio.wait_for(self.queue.get(), timeout=timeout)
    except TimeoutError:
        return None
```

The 30s value must be preserved (not increased) to maintain the current disconnect
detection latency. The `CancellationToken.race()` pattern does not wrap
`connection.get_message()`, so the timeout is the only mechanism for the endpoint
loop to detect that a client has gone away.

### 5.3 Refactor `SSEManager` internals (NOT RECOMMENDED)

> **Status: Not recommended.** This phase conflicts with REFACTOR_TASK_NOTIFICATION,
> which touches the same code path and has higher priority. If both refactors are
> desired, they must be done together — the notification refactor should drive the
> queue payload format decision.

**Original goal:** Replace pre-serialized JSON strings in the queue with typed
Pydantic models, enabling the native SSE `data` parameter (with Rust-side
serialization) instead of `raw_data`.

**Why not recommended:**

1. **Conflicts with REFACTOR_TASK_NOTIFICATION.** Both change
   `SSEManager.broadcast_to_room()` → `SSEConnection.send_message()` → queue.
   The notification refactor is fixing real bugs; this phase fixes none.
2. **Marginal performance gain.** The highest-throughput path (`send_agent_token`)
   serializes a 4-field dict. Python's `json.dumps` on that is microseconds. The
   async queue put/get and network I/O dwarf serialization cost.
3. **Under-scoped.** Changing the queue payload from `str` to `dict`/`BaseModel`
   requires updating all 10+ `send_*` methods, `SSEConnection.send_message()`,
   `get_message()`, and the endpoint. The `connected` event (constructed inline
   in `api/sse.py`, the only event not going through `SSEManager`) would also
   need to be formalized or explicitly excluded.

**If eventually pursued:** Let REFACTOR_TASK_NOTIFICATION ship first and settle the
queue payload format. Then evaluate whether Pydantic models add enough type-safety
value to justify the refactor.

### 5.4 Typed SSE event routing (NOT RECOMMENDED)

> **Status: Not recommended.** Creates fragility for an expanding event surface with
> no user-facing benefit.

**Original goal:** Use SSE's native `event:` field so the frontend can use typed
`addEventListener` instead of parsing `type` from JSON.

**Why not recommended:**

1. **Silent data loss on missing handlers.** Named SSE events not caught by any
   `addEventListener` are silently dropped. The current `onmessage` + JSON `type`
   dispatch receives all events and can log unknowns. As the system adds new event
   types (HITL, workflow engine, supervisor), the current pattern is more resilient.
2. **Coordinated breaking change.** Requires big-bang frontend + backend deploy.
   Every event type must have a handler; missing one = silent data loss.
3. **Marginal benefit.** Saves a `JSON.parse` + property check that takes
   microseconds. The frontend already works correctly.

**Wire format reference (if ever revisited):**

```
# Current (unnamed event — caught by onmessage)
data: {"type":"agent_token","room_id":"...","data":{...}}

# Named event (NOT caught by onmessage — requires addEventListener)
event: agent_token
data: {"room_id":"...","data":{...}}
```

---

## 6. Cross-Cutting Concerns

### 6.1 REFACTOR_TASK_NOTIFICATION — Notification Path Dependency

`REFACTOR_TASK_NOTIFICATION.md` proposes routing all terminal task notifications
through a single idempotent `notify_task_update()` function that calls
`sse_manager.send_task_update()` → `broadcast_to_room()` →
`SSEConnection.send_message()` → `json.dumps(message)` → queue.

- **Section 5.2 (native EventSourceResponse) is compatible.** It only changes how the
  endpoint consumes the queue, not the producer side.
- **Section 5.3 (Pydantic models) conflicts.** It changes the queue payload format.
  The two refactors cannot be done independently.

### 6.2 A-5 Deduplication Layer (CANCELLATION_WORKFLOW_ISSUES)

The `_terminal_status_sent` TTL cache in `send_processing_status()` prevents
double-sending terminal statuses. This operates at the `SSEManager` level, before
events enter the queue. All proposed changes operate at or below the queue level.
The A-5 dedup continues to function correctly through any of the migration phases.

### 6.3 CancellationToken and Heartbeat Timeout (CANCELLATION_WORKFLOW_ISSUES A-3)

The `CancellationToken.race()` pattern wraps blocking HTTP calls in
`ResponseProcessor`, but does *not* wrap `connection.get_message()` in the
endpoint's `while` loop. The 30s timeout on `get_message()` is the endpoint loop's
only mechanism for detecting client disconnection. Any change to the heartbeat
mechanism must preserve this timeout value.

### 6.4 Multi-Instance Deployment (CONCURRENCY_ROADMAP Layer C)

Single-instance event buffering (Section 3.4) covers the common case of brief
network blips. For multi-instance deployments, a shared event store (Redis or
MongoDB) would be needed. The concurrency roadmap's Layer C distributed locking
does not interact with SSE changes.

**Next.js SSE proxy note:** The proxy at `src/app/api/sse/[...endpoint]/route.ts`
is a single-hop raw byte pipe with no backend-health timeout. If the backend dies
without closing the TCP connection, the proxy's `reader.read()` may block
indefinitely. Consider adding a read timeout to the proxy's fetch call in a future
hardening pass.

### 6.5 OpenTelemetry Span Lifetime

`opentelemetry-instrumentation-fastapi` instruments request spans. Both
`StreamingResponse` and `EventSourceResponse` may keep the request span open for
the entire SSE connection (hours). Long-lived spans can cause buffer overflow and
distort latency metrics. This is a pre-existing concern independent of the
migration — verify behavior and consider excluding the SSE endpoint from automatic
instrumentation.

---

## 7. Files Affected

### Immediate (Section 3.1 — `X-Accel-Buffering` fix)

| File | Change |
|------|--------|
| `api/sse.py` | Add `"X-Accel-Buffering": "no"` to `StreamingResponse` headers |

### Short-term (Section 3.2 — notification refactor)

See `REFACTOR_TASK_NOTIFICATION.md` for complete file list.

### Medium-term (Section 3.4 — event buffering + `Last-Event-ID`)

| File | Change |
|------|--------|
| `services/sse_services.py` | Add per-room sequence counter and ring buffer to `SSEManager`; add `get_events_since()` method; add `get_message_with_seq()` to `SSEConnection` |
| `api/sse.py` | Accept `Last-Event-ID` header, replay from buffer, emit `id:` fields in SSE output |

### Deferred (Section 5.2 — native `EventSourceResponse`)

| File | Change |
|------|--------|
| `pyproject.toml` | Pin `fastapi>=0.135.0` |
| `api/sse.py` | Replace `StreamingResponse` with `EventSourceResponse`, use `ServerSentEvent(raw_data=...)` |
| `services/sse_services.py` | Simplify `SSEConnection.get_message()` — return `None` on timeout instead of heartbeat JSON |

---

## 8. Testing Strategy

### `X-Accel-Buffering` fix
- Verify header appears in SSE response via `curl -I` or browser dev tools
- If behind Nginx, confirm Nginx is not buffering the SSE stream

### Event buffering + `Last-Event-ID`
- **Replay correctness:** Disconnect mid-stream, reconnect, verify no missed or
  duplicate events
- **Buffer overflow:** Send more events than buffer capacity, verify oldest events
  are evicted and replay starts from earliest available
- **Sequence gap handling:** Frontend receives events with seq gap, verify no crash
- **Proxy passthrough:** Verify Next.js proxy forwards `Last-Event-ID` header
  from the browser to the backend

### Native `EventSourceResponse` (if/when pursued)
- **Wire format verification:** Capture SSE output before and after migration,
  diff to confirm identical `data:` lines (only difference should be `: ping`
  comments appearing between events)
- **Heartbeat removal:** Verify 30s idle connections stay alive (native ping at 15s)
- **Proxy passthrough:** Verify Next.js proxy forwards `: ping` comments without
  stripping or buffering them
- **Error paths:** Connection abort, auth failure, room not found

---

## 9. Estimated Effort

| Item | Effort | Dependencies | Priority |
|------|--------|-------------|----------|
| `X-Accel-Buffering` header fix | 5 minutes | None | **Immediate** |
| REFACTOR_TASK_NOTIFICATION | 3–6 hours | None | **High** — fixes real bugs |
| FastAPI upgrade (independent) | 0.5–1 day | None | **Medium** — maintenance |
| Event buffering + `Last-Event-ID` | 2–3 days | None (works on current `StreamingResponse`) | **Medium** — user-facing value |
| Native `EventSourceResponse` | 0.5 day | FastAPI upgrade + notification refactor settled | **Low** — deferred cleanup |
