# Event Pipeline with Pluggable Broadcast — Design Document

**Status**: Proposed
**Author**: Kevin Lu & Cursor with Opus 4.6
**Related docs**: `SYSTEM_DESIGN_REVIEW.md` §2.1, §6.7 · `NATIVE_SSE_MIGRATION_DESIGN.md` · `CONCURRENCY_ROADMAP.md` · `A2A_UPGRADE_ROADMAP.md`

---

## Implementation Status

**Cross-instance transport (Layer C)** — COMPLETED on `feature/redis-implement`

The cross-instance event delivery layer has been implemented via the `EventBroker` Protocol, fulfilling Goal G3's "seam for future multi-instance delivery" using a different abstraction than the `EventBroadcaster` proposed here.

**Implemented:**
- `EventBroker` Protocol (`infrastructure/event_broker.py`) — generic `publish(channel, payload)` / `subscribe(channel)` interface
- `RedisBroker` (`infrastructure/brokers/redis_broker.py`) — Redis Pub/Sub with reconnect, exponential backoff
- SSEManager registers kind-based handlers (`_on_sse_event`, `_on_cancellation_event`) for message dispatch
- Factory pattern for swapping broker implementations

**Not yet implemented (future work from this document):**
- `EventBroadcaster` application-layer protocol (typed methods like `artifact_update()`, `task_submitted()`)
- `LocalBroadcaster` wrapping SSEManager for single-instance mode
- Consolidation of DirectTransport's 10 direct `sse_manager` calls through the handler (§4 main refactor)
- `AgentEvent.kind` alignment to A2A spec (§4.1)

**Relationship between EventBroker and EventBroadcaster:**
- `EventBroker` = infrastructure/transport layer (generic publish/subscribe, MQ-agnostic)
- `EventBroadcaster` = application layer (typed methods, caller-facing, decouples handler from SSEManager)
- These are **complementary layers**, not alternatives. A future `EventBroadcaster` implementation could use `EventBroker` as its cross-instance transport internally while providing typed methods to callers.

---

## 1. Motivation

`AgentResponseHandler` is documented as the "single source of truth for processing agent results," but in practice only **terminal events** from `DirectTransport` flow through it. All streaming events (artifact updates, status updates, message chunks, task submissions) bypass the handler and call `SSEManager` directly — **10 direct `sse_manager` calls** in `DirectTransport` alone.

Meanwhile `RelayTransport` and `WebhookTransport` route **every** event through the handler. This creates:

1. **Behavioral drift** — Enhancements to handler methods (S3 conversion, persistence, fallback artifact synthesis) automatically apply to relay/webhook but not to direct streaming.
2. **Duplicated logic** — S3 conversion, synthetic text-artifact creation, and SSE emission are implemented independently in both DirectTransport and the handler.
3. **Missed cross-cutting concerns** — Any future addition (metrics, rate-limiting, structured logging) must be added in two places.
4. **A2A spec misalignment** — The current `AgentEvent.kind` enum fragments `TaskStatusUpdateEvent` into four separate kinds (`response`, `error`, `canceled`, `interactive`) instead of mapping 1:1 to the A2A streaming model.

### Current Call-Site Audit

| SSE method | RelayTransport | WebhookTransport | DirectTransport |
|---|---|---|---|
| `send_artifact_update` | via handler | N/A | **direct** (×3) |
| `send_task_update` | via handler | via handler | **direct** (×2) |
| `send_task_submitted` | via handler | N/A | **direct** (×2) |
| `send_agent_response` | via handler | via handler | **direct** (×1) |
| `send_error` | N/A | N/A | **direct** (×2) |
| Terminal events | via handler | via handler | via handler (×1, `_emit_terminal`) |

---

## 2. Goals and Non-Goals

### Goals

- **G1**: Every agent event — streaming or terminal — flows through `AgentResponseHandler` for all three transports.
- **G2**: Align `AgentEvent.kind` to the A2A `message/stream` discriminated union (`Task | Message | TaskStatusUpdateEvent | TaskArtifactUpdateEvent`).
- **G3**: Introduce an `EventBroadcaster` interface so the handler does not import `SSEManager` directly, creating a seam for future multi-instance delivery (Redis pub/sub).
- **G4**: Simplify transport normalizers — transports should map wire format to `AgentEvent` without classifying states into different kinds.

### Non-Goals

- **NG1**: Multi-instance scaling (Redis broadcaster) — deferred per `CONCURRENCY_ROADMAP.md` Layer C.

> **Update (2026-03):** Cross-instance Redis Pub/Sub was implemented directly in SSEManager (see `HORIZONTAL_SCALING_DESIGN.md` Implementation Status) rather than via the `EventBroadcaster` swap proposed here. The EventBroadcaster refactor remains valuable for its other goals (handler consolidation, type safety).

- **NG2**: Event log / replay / sequence numbers — deferred per `NATIVE_SSE_MIGRATION_DESIGN.md`.

> **Note:** This also means no `id:` field in SSE events and no `Last-Event-ID` support. The frontend uses a custom fetch-based SSE client that does not send `Last-Event-ID` headers, so backend-only changes cannot enable replay. Requires frontend migration to `EventSource` API.
- **NG3**: Middleware pipeline — no concrete middlewares to write today; can be added inside the handler later.
- **NG4**: Changing the SSE event format seen by the frontend — the broadcaster emits the same SSE payloads.

---

## 3. Alternatives Considered

### Alternative A — Minimal Fix (Inline DirectTransport Cleanup)

Replace the 10 direct `sse_manager` calls in `DirectTransport` with `response_handler.handle()` calls, using the existing `AgentEvent` kinds. No new abstractions.

| Dimension | Assessment |
|---|---|
| LOE | ~1 day |
| New files | 0 |
| New abstractions | 0 |
| Fixes transport parity | Yes |
| A2A alignment | No — `AgentEvent.kind` still uses hybrid enum |
| Scaling seam | No — handler still hard-imports `SSEManager` |
| Risk | Low — purely mechanical refactor |

**Verdict**: Quick win but leaves two architectural debts in place (A2A misalignment, no broadcaster seam). Adequate if time-constrained and scaling is 6+ months away.

### Alternative B — Handler + Broadcaster Interface (Recommended)

Route all events through the handler (same as A), **plus** introduce an `EventBroadcaster` protocol. The handler delegates SSE delivery to `broadcaster.emit(...)` instead of calling `SSEManager` methods directly. A `LocalBroadcaster` wraps the existing `SSEManager`. Later, a `RedisBroadcaster` can swap in for horizontal scaling.

| Dimension | Assessment |
|---|---|
| LOE | ~2–3 days |
| New files | 1 (`modules/event_broadcaster.py`) |
| New abstractions | 1 (`EventBroadcaster` protocol + `LocalBroadcaster`) |
| Fixes transport parity | Yes |
| A2A alignment | Partial — `AgentEvent.kind` can be rationalized in the same pass |
| Scaling seam | Yes — broadcaster is the future swap point |
| Risk | Low — no new infrastructure dependencies |

**Verdict**: Best cost/benefit ratio for Hybro's current stage. Fixes the immediate problem while inserting exactly one clean seam for the future.

### Alternative C — Full Event Pipeline with Middleware

Introduce an `EventPipeline` class with a middleware chain (`List[Callable[[AgentEvent, NextFn], Awaitable[None]]]`). Each cross-cutting concern (metrics, logging, persistence, broadcast) becomes a middleware. Events flow through the chain.

| Dimension | Assessment |
|---|---|
| LOE | ~5–7 days |
| New files | 3+ (pipeline, middleware base, individual middlewares) |
| New abstractions | 3+ (Pipeline, Middleware protocol, config/registry) |
| Fixes transport parity | Yes |
| A2A alignment | Yes — pipeline can validate event schema |
| Scaling seam | Yes |
| Risk | Medium — over-engineers current needs; zero middlewares to justify today |

**Verdict**: Architecturally elegant but solves hypothetical problems. Hybro has no distinct middlewares beyond "persist" and "broadcast." The abstraction cost is not justified until there are 3+ cross-cutting concerns that genuinely compose independently.

### Alternative D — Full Event Sourcing

Persist every `AgentEvent` as an immutable log entry. Broadcast from the log via change-stream consumers. Frontend can replay from any sequence number.

| Dimension | Assessment |
|---|---|
| LOE | ~2–4 weeks |
| New files | 5+ |
| New abstractions | 5+ (event store, projections, replay consumer, sequence manager) |
| Fixes transport parity | Yes |
| A2A alignment | Yes |
| Scaling seam | Yes — intrinsically distributed |
| Risk | High — massive scope increase, requires operational maturity |

**Verdict**: Appropriate for a mature platform with replay/audit requirements. Far beyond Hybro's current needs and team size.

---

## 4. Proposed Design (Alternative B)

### 4.1 Architecture Overview

```
┌─────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│ DirectTransport  │  │  RelayTransport  │  │ WebhookTransport │
│  (cloud SSE)     │  │   (hub relay)    │  │   (push notif)   │
└────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘
         │  normalize           │  normalize           │  normalize
         ▼                      ▼                      ▼
     AgentEvent             AgentEvent             AgentEvent
         │                      │                      │
         └──────────┬───────────┘──────────────────────┘
                    ▼
       ┌────────────────────────┐
       │  AgentResponseHandler  │  ← single entry point for ALL events
       │                        │
       │  • S3 conversion       │
       │  • DB persistence      │
       │  • Orchestration resume│
       │  • broadcaster.emit()  │  ← delegates delivery
       └───────────┬────────────┘
                   ▼
       ┌────────────────────────┐
       │   EventBroadcaster     │  ← protocol (interface)
       │   (abstract)           │
       └───────────┬────────────┘
                   │
          ┌────────┴────────┐
          ▼                 ▼
   LocalBroadcaster   RedisBroadcaster   (future, Layer C)
   (wraps SSEManager)
```

### 4.2 EventBroadcaster Protocol

A new file `modules/event_broadcaster.py`:

```python
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EventBroadcaster(Protocol):
    """Delivery-agnostic broadcast interface.

    The handler calls these methods instead of SSEManager directly.
    Implementations decide HOW to deliver (local queue, Redis pub/sub, etc.).
    Every parameter mirrors the corresponding SSEManager.send_* method
    so that no data is silently dropped by future implementations.
    """

    async def artifact_update(
        self,
        room_id: str,
        message_id: str,
        agent_id: str,
        artifact: Any,
        *,
        append: bool = False,
        last_chunk: bool = False,
    ) -> None: ...

    async def task_submitted(
        self,
        room_id: str,
        message_id: str,
        task_id: str,
        agent_name: str,
        agent_id: str | None = None,
        *,
        status: Any = "working",
        related_message_id: str | None = None,
        created_at: str | None = None,
        step_number: int | None = None,
        total_steps: int | None = None,
        task_content: str | None = None,
    ) -> None: ...

    async def task_update(
        self,
        room_id: str,
        message_id: str,
        status: Any,
        *,
        content: str | None = None,
        error: str | None = None,
        requires_input: bool = False,
        requires_auth: bool = False,
        status_message: str | None = None,
        agent_name: str | None = None,
        agent_id: str | None = None,
        related_message_id: str | None = None,
        created_at: str | None = None,
        step_number: int | None = None,
        total_steps: int | None = None,
        task_content: str | None = None,
        parts: list[dict] | None = None,
    ) -> None: ...

    async def agent_response(
        self,
        room_id: str,
        message_id: str,
        agent_id: str,
        content: str,
        *,
        related_message_id: str | None = None,
        parts: list[dict] | None = None,
    ) -> None: ...

    async def error(
        self,
        room_id: str,
        error_message: str,
        *,
        message_id: str | None = None,
    ) -> None: ...

    async def processing_status(
        self,
        room_id: str,
        status: str | None,
        *,
        message_id: str | None = None,
        details: str | None = None,
        client_request_id: str | None = None,
    ) -> None: ...
```

> **Implementation note for `processing_status`**: `SSEManager.send_processing_status` is **not** a pure broadcast — it also performs terminal-status deduplication (via an in-memory `TTLCache`) and DB persistence for page-refresh recovery (`update_room_processing_status` / `clear_room_processing_status_if_matches`). `LocalBroadcaster` inherits this behavior by delegating directly. A future `RedisBroadcaster` must ensure these side effects remain on the sender side (ideally moved into the handler's `_on_processing_status`) rather than being lost in the broadcaster swap.

### 4.3 LocalBroadcaster Implementation

Same file, below the protocol:

```python
class LocalBroadcaster:
    """In-process broadcaster that delegates to SSEManager.

    Drop-in replacement wiring: where you previously passed sse_manager
    to the handler, pass LocalBroadcaster(sse_manager) instead.
    All parameters are explicit (no **kw) to ensure protocol parity.
    """

    def __init__(self, sse_manager) -> None:
        self._sse = sse_manager

    async def artifact_update(
        self, room_id, message_id, agent_id, artifact, *,
        append=False, last_chunk=False,
    ):
        await self._sse.send_artifact_update(
            room_id, message_id, agent_id, artifact,
            append=append, last_chunk=last_chunk,
        )

    async def task_submitted(
        self, room_id, message_id, task_id, agent_name, agent_id=None, *,
        status="working", related_message_id=None, created_at=None,
        step_number=None, total_steps=None, task_content=None,
    ):
        await self._sse.send_task_submitted(
            room_id=room_id, message_id=message_id, task_id=task_id,
            agent_name=agent_name, agent_id=agent_id, status=status,
            related_message_id=related_message_id, created_at=created_at,
            step_number=step_number, total_steps=total_steps,
            task_content=task_content,
        )

    async def task_update(
        self, room_id, message_id, status, *,
        content=None, error=None, requires_input=False, requires_auth=False,
        status_message=None, agent_name=None, agent_id=None,
        related_message_id=None, created_at=None,
        step_number=None, total_steps=None, task_content=None, parts=None,
    ):
        await self._sse.send_task_update(
            room_id=room_id, message_id=message_id, status=status,
            content=content, error=error, requires_input=requires_input,
            requires_auth=requires_auth, status_message=status_message,
            agent_name=agent_name, agent_id=agent_id,
            related_message_id=related_message_id, created_at=created_at,
            step_number=step_number, total_steps=total_steps,
            task_content=task_content, parts=parts,
        )

    async def agent_response(
        self, room_id, message_id, agent_id, content, *,
        related_message_id=None, parts=None,
    ):
        await self._sse.send_agent_response(
            room_id=room_id, message_id=message_id,
            agent_id=agent_id, content=content,
            related_message_id=related_message_id, parts=parts,
        )

    async def error(self, room_id, error_message, *, message_id=None):
        await self._sse.send_error(room_id, error_message, message_id=message_id)

    async def processing_status(self, room_id, status, *, message_id=None, details=None, client_request_id=None):
        await self._sse.send_processing_status(
            room_id, status, message_id=message_id, details=details,
            client_request_id=client_request_id,
        )
```

### 4.4 Updated AgentResponseHandler

Key changes to `agent_response_handler.py`:

1. **Constructor** accepts `EventBroadcaster` instead of `SSEManager`:

```python
class AgentResponseHandler:
    def __init__(
        self,
        db: DatabaseService,
        broadcaster: EventBroadcaster,    # ← was: sse: SSEManager
        room_message_center: object,
    ) -> None:
        self._db = db
        self._broadcaster = broadcaster   # ← was: self._sse = sse
        self._rmc = room_message_center
```

2. **All `self._sse.*` calls become `self._broadcaster.*` calls** — method names align 1:1, so this is a mechanical find-and-replace.

3. **Streaming handlers gain persistence/S3 logic** that was previously only in DirectTransport. The handler becomes the single place for S3 conversion and DB writes.

### 4.5 AgentEvent Additions

Two new flow-control fields on `AgentEvent` (added to `agent_event.py`):

```python
# Flow control (existing)
skip_persist: bool = False

# Flow control (new)
skip_broadcast: bool = False   # replaces ctx.send_sse conditional
```

The handler checks `skip_broadcast` before calling any `broadcaster.*` method. This allows DirectTransport to emit events that need only persistence (e.g., background sync operations) without triggering SSE.

### 4.6 AgentEvent Kind Rationalization (Optional Pass)

Current enum fragments A2A streaming events into domain-interpreted kinds:

```python
# CURRENT
kind: Literal[
    "artifact_update",    # ← TaskArtifactUpdateEvent
    "response",           # ← TaskStatusUpdateEvent (state=completed)
    "error",              # ← TaskStatusUpdateEvent (state=failed)
    "canceled",           # ← TaskStatusUpdateEvent (state=canceled)
    "interactive",        # ← TaskStatusUpdateEvent (state=input-required)
    "task_submitted",     # ← synthetic (no A2A equivalent)
    "status_update",      # ← TaskStatusUpdateEvent (state=working, message only)
    "processing_status",  # ← synthetic (orchestration status)
]
```

Proposed simplification (can be done in a follow-up PR):

```python
# PROPOSED
kind: Literal[
    "artifact_update",       # ← TaskArtifactUpdateEvent (1:1)
    "status_update",         # ← TaskStatusUpdateEvent (1:1, state in .state field)
    "task_submitted",        # ← synthetic pre-dispatch event
    "processing_status",     # ← synthetic orchestration status
]
```

The handler's `match` block would then dispatch on `(event.kind, event.state)`:

```python
async def handle(self, event: AgentEvent) -> None:
    match event.kind:
        case "artifact_update":
            await self._on_artifact(event)
        case "status_update":
            await self._on_status_update(event)   # routes by event.state
        case "task_submitted":
            await self._on_submitted(event)
        case "processing_status":
            await self._on_processing_status(event)

async def _on_status_update(self, e: AgentEvent) -> None:
    match e.state:
        case "completed":
            await self._on_completed(e)
        case "failed" | "rejected":
            await self._on_failed(e)
        case "canceled":
            await self._on_canceled(e)
        case "input_required" | "auth_required":
            await self._on_interactive(e)
        case "working" | "submitted":
            await self._on_working(e)
```

This eliminates the handler needing to know "error means failed state" — the transport just passes through what the A2A agent sent.

### 4.7 Persistence Strategy — Streaming Events

DirectTransport and the handler currently use **two different persistence APIs** for streaming artifacts:

| API | Used by | Mechanism |
|---|---|---|
| `TaskStateManager.persist_message(message)` | DirectTransport | Full-document replace via `room_services.update_agent_message_by_message_id` — writes the entire in-memory `RoomAgentMessage` |
| `DatabaseService.accumulate_artifact_on_message(msg_id, artifact, append)` | Handler (`_on_artifact`) | Atomic MongoDB `$push` / `$set` on the artifact array — no read-modify-write |

**They are not equivalent.** `persist_message` replaces the whole document (safe for single-writer DirectTransport streaming). `accumulate_artifact_on_message` does atomic array ops (safe for concurrent relay/webhook delivery).

**Decision**: For Phase 2, **DirectTransport continues owning its in-memory streaming state and persistence** (`tsm.persist_message`). The handler's `_on_artifact` is used only for relay/webhook events. Streaming artifact events from DirectTransport set `skip_persist=True` on the `AgentEvent` so the handler only handles S3 conversion and broadcast.

This avoids the risk of switching persistence strategies mid-refactor. A future phase can unify both paths onto `accumulate_artifact_on_message` once the in-memory `MessageStreamingState` is refactored.

### 4.8 Ordering Constraint — Final Chunk Before Terminal

`_finalize_streaming` currently sends events in this order:

1. `send_artifact_update(..., last_chunk=True)` — tells frontend the stream is done
2. `tsm.transition_task(...)` — persists terminal state to DB
3. `_emit_terminal(...)` — tells frontend the task is completed/failed

**This ordering must be preserved after consolidation.** The frontend relies on receiving the final artifact chunk before the terminal `task_update`; otherwise the bubble would show "completed" while the last content chunk is still in flight.

After consolidation, DirectTransport's `_finalize_streaming` emits two `AgentEvent`s sequentially:

```python
# 1. Final chunk (broadcast-only, no persistence — already persisted by tsm)
await self.response_handler.handle(AgentEvent(
    kind="artifact_update",
    ...,
    last_chunk=True,
    skip_persist=True,
    skip_broadcast=not ctx.send_sse,
))

# 2. Terminal state (persistence + broadcast via _emit_terminal)
await self.tsm.transition_task(ctx.current_message, TaskState.completed, persist=True)
await self._emit_terminal(ctx, TaskState.completed)
```

Since handler methods are `await`ed (no task queue), the final-chunk broadcast completes before the terminal event is emitted, preserving the ordering.

### 4.9 Terminal Events — `notify_task_update` Path

The handler's `_notify()` helper delegates to `notify_task_update` (`services/task_notification_service.py`), a module-level function that:
1. Reads the persisted message from DB
2. Extracts content/error/artifacts
3. Calls `notification_service.send_task_update(...)` — which is `SSEManager.send_task_update`

This means terminal `task_update` SSE emission goes through `SSEManager` **directly**, bypassing the `EventBroadcaster`.

**Decision for Phase 1–2**: Accept this asymmetry. `notify_task_update` is an idempotent, DB-backed notification function with complex read-before-send logic (retry loops, artifact backfill, agent name resolution). Refactoring it to use the broadcaster would require threading the broadcaster through `task_notification_service`, which is a module-level function with no class state.

**Decision for Phase 3+**: When `RedisBroadcaster` is needed, refactor `notify_task_update` to either:
- (a) Accept a broadcaster parameter and use `broadcaster.task_update(...)`, or
- (b) Become a method on `AgentResponseHandler` that uses `self._broadcaster`

For now, the broadcaster seam covers **all streaming events** (the hot path and the inconsistency source). Terminal events already have a single canonical path through `notify_task_update` — there is no parity gap for terminals.

---

## 5. Migration Plan

### Phase 1: Introduce EventBroadcaster (no behavior change)

**Scope**: 1 new file, 2 modified files. Zero transport changes.

1. Create `modules/event_broadcaster.py` with `EventBroadcaster` protocol and `LocalBroadcaster`.
2. Update `AgentResponseHandler.__init__` to accept `broadcaster: EventBroadcaster` instead of `sse: SSEManager`.
3. Update the wiring site — `modules/RoomMessageCenter.py` L64 (`RoomMessageCenter.__init__`) — to pass `LocalBroadcaster(sse_manager)` instead of `sse_manager`. Also update test factories in `tests/test_agent_response_handler.py` and `tests/test_transport_parity.py`.
4. Mechanically replace all `self._sse.send_*` → `self._broadcaster.*` in the handler.

**Validation**: All existing tests pass. No behavioral change — `LocalBroadcaster` delegates to the same `SSEManager` methods.

### Phase 2: Consolidate DirectTransport streaming calls

**Scope**: `direct.py` modified, handler methods may gain additional logic.

For each direct `sse_manager` call in `DirectTransport`, replace with a `response_handler.handle(AgentEvent(...))` call. The mapping:

| DirectTransport call | Replacement AgentEvent |
|---|---|
| `sse_manager.send_task_submitted(...)` (L475) | `AgentEvent(kind="task_submitted", task_id=..., agent_name=..., step_number=..., total_steps=...)` |
| `sse_manager.send_artifact_update(...)` — text fallback (L834) | `AgentEvent(kind="artifact_update", text=content, append=True)` |
| `sse_manager.send_artifact_update(...)` — real artifact (L978) | `AgentEvent(kind="artifact_update", artifacts=[artifact_dict], append=..., last_chunk=...)` |
| `sse_manager.send_artifact_update(...)` — final empty (L994) | `AgentEvent(kind="artifact_update", artifacts=[empty_marker], append=True, last_chunk=True)` |
| `sse_manager.send_error(...)` (L754) | `AgentEvent(kind="error", error_text=error_message)` |
| `sse_manager.send_agent_response(...)` — non-text parts (L1112) | `AgentEvent(kind="artifact_update", parts=non_text_parts, text=full_response_text)` |
| `sse_manager.send_task_update(...)` — degraded sync (L1438) | `AgentEvent(kind="response", text=full_response_text, parts=non_text_parts, skip_persist=True)` |
| `sse_manager.send_task_update(...)` — degraded polled (L1581) | `AgentEvent(kind="response", text=final_content, error_text=final_error, state=state, skip_persist=True)` |
| `sse_manager.send_task_submitted(...)` — sync degraded (L1263) | `AgentEvent(kind="task_submitted", task_id=DEGRADED, agent_name=..., step_number=..., total_steps=...)` |
| `sse_manager.send_error(...)` — sync exception (L1339) | `broadcaster.error(room_id, str(exc))` directly — see §6.9 for special handling |

**Key consideration**: DirectTransport currently performs S3 conversion and DB persistence *before* calling `sse_manager`. After consolidation, these responsibilities move into the handler's `_on_artifact`. DirectTransport should emit a "raw" `AgentEvent` and let the handler do conversion + persistence + broadcast.

To avoid double-persistence during the transition, each migrated call should set `skip_persist=True` on the `AgentEvent` if DirectTransport already persisted, **or** (preferred) remove the DirectTransport persistence and let the handler own it.

### Phase 3: Remove DirectTransport's `sse_manager` dependency

After Phase 2, `DirectTransport` no longer needs a direct reference to `sse_manager`. Remove the attribute and constructor parameter. The transport only knows about `response_handler`.

### Phase 4 (Optional): Rationalize AgentEvent kinds

Collapse the 8-kind enum into 4 kinds as described in §4.5. This is a separate PR that touches:
- `agent_event.py` (enum change)
- `agent_response_handler.py` (match block restructure)
- All three transports (normalizer updates)
- Tests

This phase can be deferred or done opportunistically.

---

## 6. DirectTransport Consolidation — Detailed Walkthrough

### 6.1 `_setup_task_tracking` → `task_submitted`

**Before** (lines ~460–487):
```python
# DirectTransport._setup_task_tracking
await self.sse_manager.send_task_submitted(
    room_id=room_id,
    message_id=current_message.message_id,
    task_id=SyntheticTaskId.PENDING,
    agent_name=agent_card.name,
    agent_id=current_message.agent_id,
    status=TaskState.working,
    ...
)
```

**After**:
```python
await self.response_handler.handle(AgentEvent(
    kind="task_submitted",
    message_id=current_message.message_id,
    room_id=room_id,
    agent_id=current_message.agent_id,
    task_id=str(SyntheticTaskId.PENDING),
    agent_name=agent_card.name,
    step_number=step_number,
    total_steps=total_steps,
    related_message_id=current_message.related_message_id,
))
```

### 6.2 `_handle_stream_status_event` → `artifact_update` (text fallback)

**Before** (lines ~829–841):
```python
if ctx.send_sse and content:
    artifact_dict = {
        "artifact_id": f"{ctx.current_message.message_id}-stream",
        "parts": [{"kind": "text", "text": content}],
    }
    await self.sse_manager.send_artifact_update(...)
```

**After**:
```python
if content:
    await self.response_handler.handle(AgentEvent(
        kind="artifact_update",
        message_id=ctx.current_message.message_id,
        room_id=ctx.room_id,
        agent_id=ctx.current_message.agent_id,
        text=content,
        append=True,
        skip_persist=True,  # already persisted above
    ))
```

### 6.3 `_handle_stream_artifact_event` → `artifact_update` (real artifact)

**Before** (lines ~971–985):
```python
if ctx.send_sse:
    artifact_dict = (...)
    await self.sse_manager.send_artifact_update(
        ctx.room_id, ..., artifact_dict,
        append=append, last_chunk=last_chunk,
    )
```

**After** (preferred — handler owns S3 + persistence):
```python
await self.response_handler.handle(AgentEvent(
    kind="artifact_update",
    message_id=ctx.current_message.message_id,
    room_id=ctx.room_id,
    agent_id=ctx.current_message.agent_id,
    artifacts=[artifact_dict],
    append=append,
    last_chunk=last_chunk,
))
```

S3 conversion and `tsm.persist_message` calls above this point in DirectTransport can then be removed once the handler handles them.

### 6.4 `_handle_non_streaming_error` → `error`

**Before** (line ~754):
```python
if ctx.send_sse:
    await self.sse_manager.send_error(ctx.room_id, error_message)
```

**After**:
```python
await self.response_handler.handle(AgentEvent(
    kind="error",
    message_id=ctx.current_message.message_id,
    room_id=ctx.room_id,
    agent_id=ctx.current_message.agent_id,
    error_text=error_message,
))
```

### 6.5 `_finalize_streaming` → `agent_response` (non-text parts)

**Before** (lines ~1110–1118):
```python
if streaming_state.non_text_parts and ctx.send_sse:
    await self.sse_manager.send_agent_response(
        ctx.room_id,
        ctx.current_message.message_id,
        ctx.current_message.agent_id,
        streaming_state.full_response_text,
        parts=streaming_state.non_text_parts,
    )
```

**After**:
```python
if streaming_state.non_text_parts:
    await self.response_handler.handle(AgentEvent(
        kind="artifact_update",
        message_id=ctx.current_message.message_id,
        room_id=ctx.room_id,
        agent_id=ctx.current_message.agent_id,
        text=streaming_state.full_response_text,
        parts=streaming_state.non_text_parts,
        skip_persist=True,
        skip_broadcast=not ctx.send_sse,
    ))
```

### 6.6 Degraded sync path → `task_update` (no task_info)

**Before** (lines ~1433–1448):
```python
if not task_info:
    await self.sse_manager.send_task_update(
        room_id=room_id,
        message_id=message_id,
        status=TaskState.completed,
        content=full_response_text,
        agent_name=agent_card.name if agent_card else None,
        agent_id=current_message.agent_id,
        step_number=step_number,
        total_steps=total_steps,
        parts=non_text_parts if non_text_parts else None,
    )
```

**After**:
```python
if not task_info:
    await self.response_handler.handle(AgentEvent(
        kind="response",
        message_id=message_id,
        room_id=room_id,
        agent_id=current_message.agent_id,
        text=full_response_text,
        parts=non_text_parts if non_text_parts else None,
        agent_name=agent_card.name if agent_card else None,
        step_number=step_number,
        total_steps=total_steps,
        skip_persist=True,  # already persisted above
    ))
```

**Note**: These "degraded mode" calls happen when `task_info` is None (task tracking setup failed). They are fallback paths. The handler's terminal event methods (`_on_response`) already emit `task_update` SSE via `_notify`, so this mapping works.

### 6.7 Degraded polled-task path → `task_update`

**Before** (lines ~1576–1591):
```python
if not task_info:
    await self.sse_manager.send_task_update(
        room_id=room_id,
        message_id=message_id,
        status=state,
        content=final_content,
        error=final_error,
        agent_name=agent_card.name if agent_card else None,
        agent_id=current_message.agent_id,
        step_number=step_number,
        total_steps=total_steps,
    )
```

**After**:
```python
if not task_info:
    kind = "error" if is_failure_state(state) else "response"
    await self.response_handler.handle(AgentEvent(
        kind=kind,
        message_id=message_id,
        room_id=room_id,
        agent_id=current_message.agent_id,
        text=final_content or "",
        error_text=final_error,
        state=str(state),
        agent_name=agent_card.name if agent_card else None,
        step_number=step_number,
        total_steps=total_steps,
        skip_persist=True,  # already persisted above
    ))
```

### 6.8 Sync handler degraded `task_submitted` (L1263)

**Before** (lines ~1258–1272):
```python
if not task_info:
    await self.sse_manager.send_task_submitted(
        room_id=room_id,
        message_id=current_message.message_id,
        task_id=SyntheticTaskId.DEGRADED,
        agent_name=agent_card.name,
        agent_id=current_message.agent_id,
        status=TaskState.working,
        step_number=step_number,
        total_steps=total_steps,
    )
```

**After**:
```python
if not task_info:
    await self.response_handler.handle(AgentEvent(
        kind="task_submitted",
        message_id=current_message.message_id,
        room_id=room_id,
        agent_id=current_message.agent_id,
        task_id=str(SyntheticTaskId.DEGRADED),
        agent_name=agent_card.name,
        step_number=step_number,
        total_steps=total_steps,
    ))
```

### 6.9 Sync handler exception `send_error` (L1339)

**Before** (lines ~1336–1339):
```python
if task_info:
    await self._emit_terminal(ctx, TaskState.failed, error=str(exc))
await self.sse_manager.send_error(room_id, str(exc))
```

**After**:
```python
if task_info:
    await self._emit_terminal(ctx, TaskState.failed, error=str(exc))
await self._broadcaster.error(room_id, str(exc))
```

**Important**: This `send_error` is **unconditional** — it fires regardless of whether `task_info` exists and `_emit_terminal` already ran. When `task_info` is truthy, the frontend receives *both* a terminal `task_update` (via `_emit_terminal` → `_on_error` → `_notify`) and a separate `error` SSE. This dual-send is intentional: the terminal event updates the task bubble status, while the raw error event triggers the room-level error toast.

This call must **not** be routed through `handler.handle()` because `_on_error` would trigger a second `_notify` → `_resume_orchestration` cycle. Instead, it should call `self._broadcaster.error(...)` directly (available after Phase 1 via `self.response_handler._broadcaster`, or exposed as a helper method). In Phase 3 when `self.sse_manager` is removed, this becomes the natural replacement.

---

## 7. Risks and Mitigations

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| **Double-persistence during transition** — DirectTransport persists an artifact, then the handler persists again | Duplicate DB writes, corrupt artifact lists | Medium (Phase 2) | Use `skip_persist=True` as a transitional flag; remove DirectTransport persistence in same PR |
| **SSE ordering change** — Handler processes events asynchronously, changing the order the frontend sees | UI flicker, out-of-order artifacts | Low | Handler methods are `await`ed sequentially (no task queue); SSE order matches call order |
| **Performance regression on streaming hot path** — Extra function call overhead through handler + broadcaster | Higher latency per streaming chunk | Very Low | Both are in-process async calls; overhead is ~microseconds vs. network I/O |
| **`send_sse` flag loss** — DirectTransport uses `ctx.send_sse` to conditionally skip SSE; handler always broadcasts | Unwanted SSE for background/sync operations | Medium | `skip_broadcast: bool` field on `AgentEvent` (§4.5); handler checks it before calling `broadcaster.*` |
| **Final-chunk ordering** — Final artifact chunk must reach frontend before terminal `task_update` | Bubble shows "completed" before last content arrives | Medium | DirectTransport emits two sequential `AgentEvent`s; handler `await`s each, preserving order (§4.8) |
| **Partial migration leaves system in worse state** — Half the calls go through handler, half still direct | Debugging confusion | Low | Phase 2 is one PR per call site; each PR is independently shippable |
| **Double terminal + error SSE on sync exception** — L1339 `send_error` fires unconditionally after conditional `_emit_terminal`, sending both a terminal `task_update` and a raw `error` SSE | Double `_on_error` / `_resume_orchestration` if naively routed through handler | Medium | Route L1339 through `broadcaster.error()` directly instead of `handler.handle()` (§6.9) |

---

## 8. Testing Strategy

### Unit Tests

1. **`EventBroadcaster` protocol conformance** — Verify `LocalBroadcaster` satisfies `isinstance(broadcaster, EventBroadcaster)` via `runtime_checkable`.

2. **Handler method coverage** — For each handler method (`_on_artifact`, `_on_submitted`, etc.), verify:
   - Correct `broadcaster` method is called with expected arguments.
   - DB persistence is called (or skipped when `skip_persist=True`).
   - S3 conversion is invoked for artifacts with inline bytes.

3. **DirectTransport consolidation** — For each migrated call site:
   - Verify `response_handler.handle()` is called with the correct `AgentEvent`.
   - Verify `sse_manager` is **not** called directly.

### Integration Tests

4. **End-to-end streaming** — Run a mock A2A agent that emits `TaskArtifactUpdateEvent` chunks. Verify:
   - Each chunk arrives at the frontend SSE stream in order.
   - Artifacts accumulate correctly in DB.
   - `last_chunk=True` triggers proper finalization.

5. **Transport parity** — For a fixed sequence of `AgentEvent`s, verify that DirectTransport, RelayTransport, and WebhookTransport produce identical SSE output and DB state.

### Existing Test Compatibility

The existing `test_api_relay.py` tests should pass without modification since RelayTransport already routes through the handler. DirectTransport tests will need updates to mock `response_handler.handle()` instead of `sse_manager.send_*`.

---

## 9. Future Evolution

### Layer C: RedisBroadcaster (Horizontal Scaling)

When `CONCURRENCY_ROADMAP.md` Layer C is activated:

```python
class RedisBroadcaster:
    """Publishes events to Redis Pub/Sub channels.

    Each backend instance subscribes to room channels and feeds
    events into its local SSEManager for connected clients.
    """

    def __init__(self, redis_client, local_sse_manager) -> None:
        self._redis = redis_client
        self._local = local_sse_manager

    async def artifact_update(self, room_id, message_id, agent_id, artifact, **kw):
        payload = {"type": "artifact_update", "room_id": room_id, ...}
        await self._redis.publish(f"room:{room_id}:events", json.dumps(payload))
```

A subscriber coroutine on each instance listens and dispatches to the local `SSEManager`. The `EventBroadcaster` interface ensures zero handler changes.

### Event Log (Replay Support)

If replay/audit is needed later, a `LoggingBroadcaster` decorator can wrap any broadcaster:

```python
class LoggingBroadcaster:
    def __init__(self, inner: EventBroadcaster, event_store) -> None:
        self._inner = inner
        self._store = event_store

    async def artifact_update(self, room_id, message_id, agent_id, artifact, **kw):
        await self._store.append(room_id, {"type": "artifact_update", ...})
        await self._inner.artifact_update(room_id, message_id, agent_id, artifact, **kw)
```

This composes with any broadcaster (Local or Redis) without modifying the handler.

### Middleware Pipeline

If 3+ independent cross-cutting concerns emerge (e.g., rate-limiting, schema validation, audit logging), the handler's `handle()` method can be refactored into a pipeline:

```python
async def handle(self, event: AgentEvent) -> None:
    for middleware in self._middlewares:
        event = await middleware(event)
        if event is None:
            return  # middleware filtered the event
    await self._dispatch(event)
```

This is an internal refactor of the handler, not a new abstraction visible to transports.

---

## 10. Estimated Level of Effort

| Phase | Scope | LOE | Dependencies |
|---|---|---|---|
| Phase 1 | Broadcaster interface + LocalBroadcaster + handler wiring | 0.5 day | None |
| Phase 2 | DirectTransport consolidation (10 call sites) | 1–2 days | Phase 1 |
| Phase 3 | Remove `sse_manager` from DirectTransport | 0.5 day | Phase 2 |
| Phase 4 | AgentEvent kind rationalization | 1 day | Phase 2 (optional, separate PR) |
| **Total** | | **2–3 days** (Phase 4 optional: +1 day) | |

---

## Appendix: File Change Summary

| File | Phase | Change |
|---|---|---|
| `modules/event_broadcaster.py` | 1 | **New** — `EventBroadcaster` protocol + `LocalBroadcaster` |
| `modules/agent_response_handler.py` | 1 | Swap `SSEManager` → `EventBroadcaster` in constructor; rename `self._sse` → `self._broadcaster` |
| Handler wiring site (DI / factory) | 1 | In `modules/RoomMessageCenter.py` L64: pass `LocalBroadcaster(sse_manager)` instead of `sse_manager` |
| `modules/transports/direct.py` | 2 | Replace 10 `sse_manager.*` calls with `response_handler.handle(AgentEvent(...))` or `broadcaster.*` |
| `modules/transports/direct.py` | 3 | Remove `self.sse_manager` attribute |
| `modules/agent_event.py` | 2 | Add `skip_broadcast: bool = False` field |
| `modules/agent_event.py` | 4 | Collapse 8 kinds → 4 kinds |
| `modules/agent_response_handler.py` | 4 | Restructure `match` block to dispatch on `(kind, state)` |
| Transport normalizers (all three) | 4 | Emit simplified kinds |
| Tests | 2–4 | Update mocks, add parity tests |

---

## Appendix B: Gap Resolution Log

_All gaps identified during design review have been resolved._

| Gap | Issue | Resolution | Section |
|---|---|---|---|
| 1 | Call-site audit listed 5 of 8 DirectTransport calls | Added `send_agent_response` (L1112), `send_task_update` degraded-sync (L1438), `send_task_update` degraded-polled (L1581) to §5 table and §6 walkthrough (§6.5–6.7) | §5, §6 |
| 2 | `EventBroadcaster` protocol missing params vs `SSEManager` | Rewrote protocol with full param parity: `task_submitted` gains `created_at`, `task_content`; `task_update` gains 10+ params; `error` gains `message_id`. `LocalBroadcaster` uses explicit params (no `**kw`). | §4.2, §4.3 |
| 3 | `skip_broadcast` proposed in risk table but not in AgentEvent | Added `skip_broadcast: bool = False` to AgentEvent spec in new §4.5 | §4.5 |
| 4 | `TaskStateManager.persist_message` vs `DatabaseService.accumulate_artifact_on_message` — two persistence APIs | Full-document replace (`persist_message`) vs atomic array ops (`accumulate_artifact_on_message`). Decision: DirectTransport keeps `tsm` ownership for streaming; handler artifact events from DirectTransport set `skip_persist=True`. | §4.7 |
| 5 | `_finalize_streaming` ordering: final chunk SSE must precede terminal SSE | Documented constraint and solution: two sequential `AgentEvent`s, first with `skip_persist=True` for final chunk, then terminal via `_emit_terminal`. `await` preserves ordering. | §4.8, §7 |
| 6 | Wiring site not identified | `modules/RoomMessageCenter.py` L64 (`RoomMessageCenter.__init__`). Also flagged test factories. | §5 Phase 1 step 3, Appendix A |
| 7 | `notify_task_update` bypasses broadcaster for terminal SSE | Accepted for Phase 1–2: terminal path has no parity gap (all transports use same `notify_task_update`). Deferred to Phase 3+ when `RedisBroadcaster` is needed. | §4.9 |
| 8 | `send_rate_limit_error` not in broadcaster protocol | Out of scope: called from `QueueExecutor` (queue-level, pre-dispatch), not from transport response handling. Does not flow through `AgentResponseHandler`. No protocol method needed. | This appendix |
| 9 | §1 prose said "11 direct calls" but audit table sums to 10 | Fixed to "10 direct `sse_manager` calls". Also fixed Alternative A reference. | §1, §3 |
| 10 | 2 call sites missing from §5 migration table: `send_task_submitted` degraded (L1263), `send_error` sync exception (L1339) | Added both to §5 Phase 2 table. Added walkthroughs §6.8 and §6.9. Updated Appendix A count from 8 to 10. | §5, §6, Appendix A |
| 11 | `EventBroadcaster.processing_status` missing `client_request_id` param | Added `client_request_id: str \| None = None` to protocol and `LocalBroadcaster` for full `SSEManager` parity. | §4.2, §4.3 |
| 12 | `SSEManager.send_processing_status` has non-broadcast side effects (DB persistence + terminal dedup) | Added implementation note after §4.2 protocol: `LocalBroadcaster` inherits these via delegation, but future `RedisBroadcaster` must keep side effects on the sender side. | §4.2 |
| 13 | Risk table §4.9 cross-reference should be §4.8 | Fixed "preserving order (§4.9)" → "preserving order (§4.8)" in final-chunk ordering risk row. | §7 |
| 14 | L1339 `send_error` is unconditional — fires after conditional `_emit_terminal`, causing double terminal processing if routed through handler | Documented in §6.9: route through `broadcaster.error()` directly, not `handler.handle()`. Added risk row in §7. | §6.9, §7 |
