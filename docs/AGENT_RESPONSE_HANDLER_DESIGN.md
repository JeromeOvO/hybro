# Agent Response Handler — Unified Result Processing

**Date**: March 8, 2026
**Status**: Phases 1–3 implemented, Phase 4 not started
**Scope**: Extract shared agent-result processing into a single `AgentResponseHandler` used by all three entry points, and organize transport-specific logic into three dedicated `AgentTransport` classes
**Depends on**: Relay parity fixes (completed), A2A spec compliance fixes (completed)

---

## 1. Problem Statement

Agent responses enter the cloud backend through **three independent code paths** that each reimplement the same post-processing logic (persist to DB, send SSE, resume orchestration) with subtle differences. This duplication has caused repeated parity bugs:

| Bug | Root cause | Path affected |
|---|---|---|
| `message_text` not persisted (Pydantic validation error) | Relay handler used `$set` on full object instead of dot-notation | Relay |
| "No content message" after page refresh | `if message_text:` truthiness guard skipped empty strings | Both `update_task_on_message` + `update_task_state_on_message` |
| Missing real-time streaming tokens | Relay handler had no `agent_token` forwarding (since added) | Relay |
| No task re-fetch fallback | Relay path didn't re-fetch completed task when streaming text was empty | Relay (hub side) |
| Hardcoded terminal-state strings | Cloud path used string literals instead of `TERMINAL_STATES` constant | Cloud |
| Missing `final` field handling | Cloud streaming didn't read `TaskStatusUpdateEvent.final` | Cloud |

Every one of these bugs existed because the same logical operation was written in two or three places and one copy diverged.

### Current architecture

```
                    ┌─────────────────┐
                    │   A2A Agent      │
                    └──┬─────┬─────┬──┘
                       │     │     │
          ┌────────────┘     │     └────────────┐
          ▼                  ▼                   ▼
   SSE / Sync          Push webhook         Hub relay
   (in-process)        (async POST)         (SSE → hub → publish)
          │                  │                   │
          ▼                  ▼                   ▼
  ResponseProcessor    webhooks.py       relay_service.py
  (1,429 lines)        (317 lines)       _process_single_publish_event
          │                  │                   │
          ├──persist─────────┼──persist──────────┤   ← duplicated
          ├──SSE─────────────┼──SSE──────────────┤   ← duplicated
          ├──orchestration───┼──orchestration─────┤   ← duplicated
          ▼                  ▼                   ▼
       Database          Database            Database
```

### Duplication by the numbers

| Operation | ResponseProcessor | relay_service | webhooks | Total call sites |
|---|---|---|---|---|
| DB persist (task state + message_text) | ~15 | 6 | 2 | 23 |
| SSE emission (token/response/error/artifact) | ~12 | 8 | 0 (via notify_task_update) | 20 |
| Orchestration resume | 0 (done by QueueExecutor) | 3 | 1 | 4 |
| notify_task_update | 16 | 2 | 1 | 19 |

---

## 2. Design

### Core idea

> **The three paths differ in *how they talk to agents* (transport) but are identical in *what they do with results* (persistence + notification + orchestration).**

Extract the "what to do with results" into a shared `AgentResponseHandler` class. Each transport adapter normalizes its events into a common `AgentEvent` dataclass and delegates to the handler.

### 2.1. `AgentEvent` — Normalized event dataclass

```python
# modules/agent_event.py

@dataclass
class AgentEvent:
    """Transport-agnostic agent event. All three entry points normalize into this."""

    kind: Literal[
        "token",              # streaming text chunk
        "artifact_update",    # artifact data (streaming or final)
        "response",           # final complete response (terminal — completed)
        "error",              # agent error (terminal — failed/rejected)
        "canceled",           # task was canceled
        "task_submitted",     # task acknowledged by agent
        "status_update",      # non-terminal state change (working, etc.)
        "interactive",        # input-required / auth-required
        "processing_status",  # UI progress indicator (pass-through)
    ]

    # Required context
    message_id: str
    room_id: str
    agent_id: str

    # Content (populated per kind)
    text: str = ""
    state: str | None = None        # TaskState value string
    parts: list[dict] | None = None # non-text multimodal parts
    artifacts: list[dict] | None = None
    task_id: str | None = None
    context_id: str | None = None
    error_text: str | None = None
    related_message_id: str | None = None
    user_id: str | None = None

    # Metadata
    is_final: bool = False          # from A2A `final` field
    agent_name: str | None = None
    step_number: int | None = None
    total_steps: int | None = None

    # Flow control
    send_processing_status: bool = False  # webhook path needs this
    skip_persist: bool = False            # DirectTransport uses this (see §2.8)
    details: str | None = None            # processing_status details text
```

### 2.2. `AgentResponseHandler` — Shared processing

#### Key design decision: relationship with `notify_task_update`

The existing `notify_task_update` function (289 lines in `task_notification_service.py`) is already a shared terminal-notification entry point with significant logic:
- **Idempotency** via `db_service.update_last_notified_state`
- **Re-fetch** of full `RoomAgentMessage` from DB (with 3 retry attempts)
- **Content extraction** from artifacts (text, file parts, data parts)
- **Artifact backfill**: synthesizes an `Artifact` when `completed` with `message_text` but no artifacts
- **`message_text` backfill**: populates from content/error/status_message if empty
- **Agent name resolution** from room's agent set
- **Metadata resolution**: `created_at`, `task_content`, `step_number`, `total_steps`
- **S3 conversion** of inline base64 file parts
- **SSE emission** via `notification_service.send_task_update` (not `sse_manager` directly)
- **Optional `processing_status` SSE** for webhook path

**Decision: `AgentResponseHandler` wraps `notify_task_update`, not replaces it.**

`notify_task_update` is the canonical SSE emitter that the frontend depends on. Its idempotency, backfill, and metadata resolution logic is valuable and well-tested. `AgentResponseHandler` calls it at terminal points (response, error, canceled, interactive) and does not duplicate its internals.

What `AgentResponseHandler` owns directly:
- **DB persist** (`update_task_state_on_message`) — always runs before `notify_task_update`
- **Orchestration resume** (`resume_queue_from_continuation`) — runs after persist
- **Streaming SSE** (`send_agent_token`, `send_agent_response` for mid-stream/non-text) — not terminal
- **Terminal notification** — delegates to `notify_task_update`

```python
# modules/agent_response_handler.py

class AgentResponseHandler:
    """Single source of truth for processing agent results.

    Terminal events delegate to notify_task_update for SSE emission.
    Streaming events (token, artifact_update) use sse_manager directly.
    """

    def __init__(
        self,
        db: DatabaseService,
        sse: SSEManager,
        room_message_center: RoomMessageCenter,
    ):
        self._db = db
        self._sse = sse
        self._rmc = room_message_center

    async def handle(self, event: AgentEvent) -> None:
        match event.kind:
            case "token":              await self._on_token(event)
            case "artifact_update":    await self._on_artifact(event)
            case "response":           await self._on_response(event)
            case "error":              await self._on_error(event)
            case "canceled":           await self._on_canceled(event)
            case "task_submitted":     await self._on_submitted(event)
            case "status_update":      await self._on_status(event)
            case "interactive":        await self._on_interactive(event)
            case "processing_status":  await self._on_processing_status(event)

    # --- Streaming events (direct SSE, no DB persist) ---

    async def _on_token(self, e: AgentEvent) -> None:
        await self._sse.send_agent_token(
            room_id=e.room_id, message_id=e.message_id,
            agent_id=e.agent_id, token=e.text,
        )

    async def _on_artifact(self, e: AgentEvent) -> None:
        if not e.skip_persist:
            await self._db.update_task_state_on_message(
                e.message_id, "working",
                message_text=e.text or None,
                artifacts=e.artifacts,
            )
        await self._sse.send_agent_token(
            room_id=e.room_id, message_id=e.message_id,
            agent_id=e.agent_id, token=e.text or "",
        )

    # --- Terminal events (DB persist → notify_task_update → orchestration) ---

    async def _on_response(self, e: AgentEvent) -> None:
        if not e.skip_persist:
            await self._db.update_task_state_on_message(
                e.message_id, "completed", message_text=e.text,
            )
        await self._notify(e, TaskState.completed)
        if e.parts:
            await self._sse.send_agent_response(
                room_id=e.room_id, message_id=e.message_id,
                agent_id=e.agent_id, content=e.text,
                related_message_id=e.related_message_id,
                parts=e.parts,
            )
        await self._resume_orchestration(e.message_id, e.text)

    async def _on_error(self, e: AgentEvent) -> None:
        error = e.error_text or e.text or "Unknown agent error"
        state = e.state or "failed"   # preserves rejected vs failed
        if not e.skip_persist:
            await self._db.update_task_state_on_message(
                e.message_id, state, message_text=error,
            )
        await self._notify(e, TaskState(state), error=error)
        await self._resume_orchestration(e.message_id, "", failed=True)

    async def _on_canceled(self, e: AgentEvent) -> None:
        if not e.skip_persist:
            await self._db.update_task_state_on_message(
                e.message_id, "canceled", message_text=e.text or "Task was canceled",
            )
        await self._notify(e, TaskState.canceled)
        await self._resume_orchestration(e.message_id, "", failed=True)

    async def _on_interactive(self, e: AgentEvent) -> None:
        state = e.state or "input-required"
        if not e.skip_persist:
            await self._db.update_task_state_on_message(
                e.message_id, state,
                message_text=e.text or None,
                task_id=e.task_id,
                context_id=e.context_id,
            )
        await self._notify(e, TaskState(state))

    # --- Non-terminal events ---

    async def _on_submitted(self, e: AgentEvent) -> None:
        await self._sse.send_task_submitted(
            room_id=e.room_id, message_id=e.message_id,
            task_id=e.task_id, agent_name=e.agent_name,
            agent_id=e.agent_id, status="working",
            related_message_id=e.related_message_id,
            step_number=e.step_number, total_steps=e.total_steps,
        )

    async def _on_status(self, e: AgentEvent) -> None:
        if e.text:
            await self._sse.send_agent_token(
                room_id=e.room_id, message_id=e.message_id,
                agent_id=e.agent_id, token=e.text,
            )

    async def _on_processing_status(self, e: AgentEvent) -> None:
        await self._sse.send_processing_status(
            e.room_id, e.state, message_id=e.message_id,
            details=e.details,
        )

    # --- Helpers ---

    async def _notify(
        self, e: AgentEvent, state: TaskState, error: str | None = None,
    ) -> None:
        from services.task_notification_service import notify_task_update
        await notify_task_update(
            message_id=e.message_id,
            state=state,
            room_id=e.room_id,
            user_id=e.user_id or "",
            error=error,
            send_processing_status=e.send_processing_status,
            parts=e.parts,
        )

    async def _resume_orchestration(
        self, message_id: str, response_text: str, *, failed: bool = False,
    ) -> None:
        try:
            await self._rmc.resume_queue_from_continuation(
                message_id=message_id,
                task_result_text=response_text if not failed else None,
                failed=failed,
            )
        except Exception:
            logger.exception("Failed to resume orchestration for %s", message_id)
```

### 2.3. Transport classes — `AgentTransport` hierarchy

Rather than keeping transport logic scattered across `ResponseProcessor`, `relay_service`, and `webhooks.py`, each path gets its own class implementing a shared abstract interface. Each class owns **only** the transport-specific logic (talking to agents + normalizing events) and delegates result processing to `AgentResponseHandler`.

#### Abstract base class

```python
# modules/transports/base.py

class AgentTransport(ABC):
    """Base class for all agent transport mechanisms.

    Subclasses own the *how* of talking to agents.
    AgentResponseHandler owns the *what* of processing results.
    """

    def __init__(self, response_handler: AgentResponseHandler):
        self.response_handler = response_handler

    @abstractmethod
    async def dispatch(
        self,
        ctx: DispatchContext,
        message: RoomAgentMessage,
    ) -> ProcessingResult:
        """Send message to agent and process results via response_handler."""
        ...
```

#### `DirectTransport` — cloud agents (streaming + sync)

Extracted from the current `ResponseProcessor` + `AgentMessageProcessor._dispatch_direct`.

**Owns:** A2A HTTP calls, SSE stream iteration, in-memory `Task` model, `TaskStateManager`, `CancellationToken`, S3 inline-to-URI conversion, polling for non-streaming agents.

```python
# modules/transports/direct.py

class DirectTransport(AgentTransport):
    """Direct HTTP/SSE transport to cloud-reachable A2A agents."""

    def __init__(self, response_handler, tsm, a2a_service, task_service,
                 sse_manager, database_service, ...):
        super().__init__(response_handler)
        self.tsm = tsm
        self.a2a_service = a2a_service
        self.task_service = task_service
        self.sse_manager = sse_manager  # for mid-stream SSE only (see §2.8)
        self.database_service = database_service

    async def dispatch(self, ctx, message) -> ProcessingResult:
        # 1. Setup task tracking
        # 2. Call agent (streaming or sync)
        # 3. Iterate SSE stream / handle sync response
        #    - Mid-stream: send agent_token SSE via self.sse_manager (transport-specific)
        #    - Accumulate in-memory task model, S3 conversion
        # 4. On terminal: emit AgentEvent(skip_persist=True) → response_handler.handle()
        #    (skip_persist because tsm.persist_message already wrote the full doc)
        ...
```

**What moves here from current codebase:**
- `ResponseProcessor.handle_streaming_response` and all `_handle_stream_*` sub-handlers
- `ResponseProcessor.handle_sync_response` and `_process_sync_response`
- `ResponseProcessor._finalize_streaming`
- `ResponseProcessor._poll_task_until_complete` and `_finalize_polled_task`
- `ResponseProcessor._setup_task_tracking` and `_setup_tracking_context`
- S3 conversion methods (`_convert_inline_bytes_to_s3`, `_convert_streaming_parts_to_s3`)
- Cancellation handling (`_handle_streaming_cancellation`, `_try_cancel_remote_task`)

**Note on `sse_manager` dual injection:** `DirectTransport` takes `sse_manager` for mid-stream `send_agent_token` calls during SSE iteration. This is an accepted asymmetry — mid-stream tokens in the cloud path are tightly coupled with in-memory state accumulation and must happen synchronously within the streaming loop. The relay and webhook paths don't have this constraint, so their tokens go through `AgentResponseHandler._on_token`. This asymmetry is isolated and well-understood.

**Approximate size:** ~1,000 lines (bulk of the current 1,429-line `ResponseProcessor`, minus result-handling that moves to `AgentResponseHandler`).

#### `RelayTransport` — hub/local agents

Extracted from `AgentMessageProcessor._dispatch_via_relay` (outbound) and the inbound half of `relay_service._process_single_publish_event`.

**Owns:** Relay dispatch (push event to hub), receive publish events back, normalize hub events into `AgentEvent`s, cancel/reply control events.

```python
# modules/transports/relay.py

class RelayTransport(AgentTransport):
    """Relay transport for hub-connected local A2A agents."""

    def __init__(self, response_handler, relay_service, db):
        super().__init__(response_handler)
        self.relay_service = relay_service
        self._db = db

    async def dispatch(self, ctx, message) -> ProcessingResult:
        # 1. Enable lightweight task tracking
        # 2. Push RelayToHubEvent to hub via relay_service
        # 3. Return RELAY_DISPATCHED (hub publishes results asynchronously)
        ...

    async def handle_publish_event(self, event_dict: dict, msg: RoomAgentMessage):
        """Called when hub publishes a result back. Normalize and delegate."""
        agent_event = self._normalize(event_dict, msg)
        await self.response_handler.handle(agent_event)

    async def cancel_task(
        self, hub_id: str, agent_message_id: str,
        local_agent_id: str, task_id: str | None = None,
    ) -> bool:
        """Forward cancellation to the hub."""
        return await self.relay_service.cancel_relay_task(
            hub_id, agent_message_id, local_agent_id, task_id,
        )

    async def reply_to_task(
        self, hub_id: str, agent_message_id: str,
        local_agent_id: str, reply_text: str, room_id: str,
        task_id: str | None = None, context_id: str | None = None,
    ) -> bool:
        """Forward HITL reply to the hub."""
        return await self.relay_service.reply_to_relay_task(
            hub_id, agent_message_id, local_agent_id,
            reply_text, room_id, task_id, context_id,
        )

    def _normalize(self, event: dict, msg: RoomAgentMessage) -> AgentEvent:
        """Convert hub publish dict → AgentEvent."""
        event_type = event.get("type")
        data = event.get("data", {})
        kind = _EVENT_TYPE_MAP[event_type]  # "agent_response" → "response", etc.
        return AgentEvent(
            kind=kind,
            message_id=event.get("agent_message_id"),
            room_id=msg.room_id,
            agent_id=msg.agent_id or "",
            text=data.get("content", data.get("token", data.get("text", ""))),
            related_message_id=msg.related_message_id,
            user_id=msg.user_id,
            # ... remaining field mapping per event type ...
        )
```

**What moves here from current codebase:**
- `AgentMessageProcessor._dispatch_via_relay` (outbound dispatch)
- The event normalization + if/elif chain from `relay_service._process_single_publish_event` (inbound)
- The cancellation guard (`is_message_cancelled` check)
- `cancel_relay_task` and `reply_to_relay_task` (outbound control events)

**What stays in `RelayService`:** Hub connection management (`connect_hub`, `_disconnect_hub`), SSE subscription, agent sync (`sync_agents`), offline queue management (`push_to_hub`, `_fail_offline_message`, `sweep_offline_queues`), heartbeat loop, hub status queries. These are infrastructure concerns.

**Approximate size:** ~200 lines (up from 150 due to cancel/reply methods).

#### `WebhookTransport` — push-notification agents

Extracted from `webhooks.handle_a2a_webhook`.

**Owns:** Webhook auth/token validation, `StreamResponse` parsing, `Task` → `AgentEvent` normalization.

```python
# modules/transports/webhook.py

class WebhookTransport(AgentTransport):
    """Push-notification transport for async A2A agents.

    Unlike DirectTransport and RelayTransport, this is inbound-only:
    the agent initiates the call, not the user.
    """

    def __init__(self, response_handler, db):
        super().__init__(response_handler)
        self._db = db

    async def dispatch(self, ctx, message) -> ProcessingResult:
        raise NotImplementedError("Webhooks are inbound-only")

    async def handle_webhook(
        self, message_id: str, payload: dict, token: str,
    ) -> dict:
        """Called by the FastAPI route. Validate, parse, delegate."""
        # 1. Validate webhook token (hash-based)
        # 2. Parse StreamResponse → Task (via parse_stream_response)
        # 3. Check idempotency (already terminal?)
        # 4. Normalize Task → AgentEvent
        event = self._task_to_event(task, msg)
        # 5. Delegate to shared handler
        await self.response_handler.handle(event)
        return {"status": "accepted"}

    def _task_to_event(self, task: Task, msg: RoomAgentMessage) -> AgentEvent:
        """Convert A2A Task → AgentEvent."""
        state = task.status.state
        text = extract_text_from_artifacts(task.artifacts) if task.artifacts else None
        if state == TaskState.canceled:
            return AgentEvent(kind="canceled", ..., state=state.value)
        if is_failure_state(state):
            return AgentEvent(kind="error", ..., error_text=extract_error_message(task),
                              state=state.value)
        if state in INTERACTIVE_STATES:
            return AgentEvent(kind="interactive", ..., state=state.value)
        return AgentEvent(kind="response", ..., text=text or "", state=state.value,
                          send_processing_status=True)
```

**What moves here from current codebase:**
- `webhooks.handle_a2a_webhook` steps 1–8
- `webhooks.parse_stream_response`
- `webhooks.resume_queue_continuation`

**What stays in `api/webhooks.py`:** The FastAPI route definition (thin wrapper that delegates to `WebhookTransport.handle_webhook`).

**Migration note — `update_task_on_message` → `update_task_state_on_message`:** The webhook path currently uses `update_task_on_message` (full-document write with the complete Pydantic `Task` model). After refactoring, `AgentResponseHandler` uses `update_task_state_on_message` (partial dot-notation update of state + message_text only). This means the full `Task` model (with all artifacts, status message, etc.) will no longer be persisted as a single write. Instead, artifact data must flow through `AgentEvent.artifacts` / `AgentEvent.parts` to `notify_task_update`, which already has artifact backfill and S3 conversion logic. This is a deliberate improvement (partial updates avoid clobbering nested fields — the original cause of the Pydantic validation bug), but the webhook `_task_to_event` normalizer must extract and pass all relevant fields to `AgentEvent`.

**Approximate size:** ~200 lines.

### 2.4. `AgentMessageProcessor` becomes a router

With transport classes extracted, `AgentMessageProcessor` becomes a lightweight router that picks the right transport and delegates.

```python
# modules/AgentMessageProcessor.py (simplified)

class AgentMessageProcessor:
    def __init__(self, ...):
        handler = AgentResponseHandler(db, sse, room_message_center)
        self.transports = {
            "direct": DirectTransport(handler, tsm, a2a_service, task_service, ...),
            "relay":  RelayTransport(handler, relay_service, db),
            # webhook is inbound-only, registered separately
        }

    async def process_agent_message(self, ...):
        # ... preparation, middleware ...
        transport = self.transports[ctx.transport]
        result = await transport.dispatch(ctx, current_message)
        return await self.dispatch_chain.run_post_dispatch(ctx, result)
```

### 2.5. What stays path-specific (per transport class)

| Concern | Why it can't be shared | Transport class |
|---|---|---|
| Agent HTTP/SSE call | Direct network I/O | `DirectTransport` |
| In-memory Pydantic task model | Only cloud has it in-process | `DirectTransport` |
| `TaskStateManager` transitions | Operates on in-memory model | `DirectTransport` |
| S3 inline-bytes-to-URI conversion | Only cloud sees raw base64 | `DirectTransport` |
| `CancellationToken` racing | Only cloud has cooperative cancellation | `DirectTransport` |
| Mid-stream `send_agent_token` | Coupled with in-memory state accumulation | `DirectTransport` |
| Hub event normalization | Relay publish dict → AgentEvent | `RelayTransport` |
| Relay dispatch (push to hub) | Relay-specific outbound flow | `RelayTransport` |
| Cancel/reply control events | Hub-specific outbound control | `RelayTransport` |
| Webhook auth/token validation | Only webhooks need this | `WebhookTransport` |
| `StreamResponse` parsing | Webhook-specific A2A format | `WebhookTransport` |
| Hub re-fetch of final task | Hub-side, runs in `hybro-hub` process | `Dispatcher._refetch_final_task` (unchanged) |

### 2.6. File layout

```
modules/
├── agent_event.py                  # AgentEvent dataclass
├── agent_response_handler.py       # Shared result processing (single implementation)
├── transports/
│   ├── __init__.py
│   ├── base.py                     # AgentTransport ABC
│   ├── direct.py                   # Cloud SSE/sync (~1,000 lines, from ResponseProcessor)
│   ├── relay.py                    # Hub relay (~150 lines, from relay_service)
│   └── webhook.py                  # Push notifications (~200 lines, from webhooks.py)
├── AgentMessageProcessor.py        # Router — picks transport, delegates
├── ResponseProcessor.py            # DELETED (absorbed into direct.py)
└── ...
```

### 2.7. Target architecture

```
                      ┌─────────────────┐
                      │   A2A Agent      │
                      └──┬─────┬─────┬──┘
                         │     │     │
            ┌────────────┘     │     └────────────┐
            ▼                  ▼                   ▼
     SSE / Sync          Push webhook         Hub relay
     (in-process)        (async POST)         (SSE → hub → publish)
            │                  │                   │
            ▼                  ▼                   ▼
   ┌────────────────┐ ┌───────────────┐ ┌──────────────────┐
   │DirectTransport │ │WebhookTransport│ │ RelayTransport   │
   │ • stream iter  │ │ • auth/token  │ │ • dispatch to hub│
   │ • sync + poll  │ │ • parse resp  │ │ • normalize events│
   │ • S3 convert   │ │ • Task→Event  │ │ • cancel guard   │
   │ • cancel token │ │               │ │                  │
   └───────┬────────┘ └───────┬───────┘ └────────┬─────────┘
           │                  │                   │
           └───── AgentEvent ─┼── AgentEvent ─────┘
                              │
                              ▼
                    ┌─────────────────────┐
                    │ AgentResponseHandler │  ← SINGLE implementation
                    │  • persist to DB    │
                    │  • send SSE         │
                    │  • resume orch.     │
                    │  • notify_task_upd. │
                    └─────────────────────┘
                              │
                              ▼
                           Database
```

### 2.8. Key design decisions

#### `AgentTransport` ABC is a loose contract

The `dispatch()` method is the outbound interface: "send a message to an agent." However, 2 of 3 transports have distinct inbound paths:
- `RelayTransport.handle_publish_event()` — hub pushes results asynchronously
- `WebhookTransport.handle_webhook()` — agent pushes results asynchronously
- `DirectTransport.dispatch()` is the only truly synchronous request→response path

The ABC therefore defines the minimum common interface. Each subclass has additional public methods for its specific inbound flow. This is an accepted pragmatic compromise — forcing all three into a single `dispatch()` shape would require artificial constructs.

#### `skip_persist` flag for `DirectTransport`

`DirectTransport` manages an in-memory `Task` model and persists it via `tsm.persist_message()` (full-document write). `AgentResponseHandler` also persists via `update_task_state_on_message()` (partial dot-notation update). To avoid double-writing, `DirectTransport` sets `skip_persist=True` on the `AgentEvent` when it has already persisted. `AgentResponseHandler` checks this flag and skips its DB call. Relay and webhook transports never set this flag.

#### `send_agent_response` SSE vs `notify_task_update`

The relay path currently uses `send_agent_response` (direct SSE) for `agent_response` events and `notify_task_update` for `task_status`/`task_interactive` events. This mixed approach is itself a parity issue.

After refactoring, all terminal events go through `AgentResponseHandler` which calls `notify_task_update` for the canonical `task_update` SSE (what the frontend depends on). `send_agent_response` is reserved for non-text multimodal content that needs to be sent alongside the terminal notification. This eliminates the mixed approach.

#### `canceled` as a separate `AgentEvent.kind`

The A2A spec treats `canceled` as a terminal state distinct from `failed`/`rejected`. The cloud path has dedicated cancellation handling (`_handle_streaming_cancellation`) that transitions to `canceled` without sending an error SSE. Giving `canceled` its own kind ensures the correct state string reaches the DB and frontend, rather than being conflated with `failed` through `_on_error`.

**Orchestration resume for `canceled`:** `_on_canceled` calls `_resume_orchestration(failed=True)` because the relay path needs explicit orchestration resume (it has no `QueueExecutor` managing the lifecycle). The cloud path (`DirectTransport`) handles cancellation entirely within its own streaming loop and delegates to `QueueExecutor` — it never emits a `"canceled"` `AgentEvent` to the handler, so this code path only fires for relay/webhook cancellations. `FAILURE_STATES` in `a2a_constants.py` includes `canceled`, so treating it as a failed resume is consistent.

#### `processing_status` as an `AgentEvent.kind`

The relay path handles a `"processing_status"` event that is a UI progress indicator pass-through. Rather than leaving this as a special case outside the transport system, it's included as a lightweight event kind that goes through `AgentResponseHandler` for consistency. No DB persistence occurs for this kind.

---

## 3. Phased Implementation Plan

### Phase 1: Foundation — `AgentEvent` + `AgentResponseHandler` + `RelayTransport`

Migrate the relay path first because it's already event-based (publish events → handler) — the if/elif chain maps 1:1 onto `AgentEvent` kinds.

**Changes:**
- Create `modules/agent_event.py` with the `AgentEvent` dataclass
- Create `modules/agent_response_handler.py` with the shared handler
- Create `modules/transports/base.py` with `AgentTransport` ABC
- Create `modules/transports/relay.py` with `RelayTransport`:
  - Move outbound dispatch from `AgentMessageProcessor._dispatch_via_relay`
  - Move inbound normalization from `relay_service._process_single_publish_event`
  - Move cancellation guard (`is_message_cancelled` check)
- `relay_service.py` retains only hub connection management and delegates event handling to `RelayTransport.handle_publish_event`
- Delete the 150+ lines of duplicated DB/SSE/orchestration logic from `relay_service.py`
- Update `tests/test_api_relay.py`

**Files touched:** 5 new + 3 modified in `multi-agents-backend`
**Estimated effort:** 4–5 hours
**Risk:** Low — relay path is self-contained, easy to test in isolation

### Phase 2: `WebhookTransport`

**Changes:**
- Create `modules/transports/webhook.py` with `WebhookTransport`:
  - Move `handle_a2a_webhook` steps 1–8 (auth, parse, normalize, delegate)
  - Move `parse_stream_response` into the transport class
  - Move `resume_queue_continuation` logic
- Thin down `api/webhooks.py` to a ~20-line FastAPI route that delegates to `WebhookTransport.handle_webhook()`
- Delete ~150 lines of inline processing logic from `webhooks.py`
- Update `tests/test_api_webhooks.py`

**Files touched:** 1 new + 2 modified
**Estimated effort:** 2–3 hours
**Risk:** Low — webhook handler is simple and well-tested

### Phase 3: `DirectTransport` — cloud `ResponseProcessor` migration

This is the most delicate phase because `ResponseProcessor` has complex in-memory state management. The entire class is restructured into `DirectTransport`.

**Changes:**
- Create `modules/transports/direct.py` with `DirectTransport`:
  - Move streaming logic: `handle_streaming_response`, all `_handle_stream_*` sub-handlers, `_finalize_streaming`
  - Move sync logic: `handle_sync_response`, `_process_sync_response`, `_poll_task_until_complete`, `_finalize_polled_task`
  - Move setup: `_setup_task_tracking`, `_setup_tracking_context`
  - Move S3 conversion: `_convert_inline_bytes_to_s3`, `_convert_streaming_parts_to_s3`
  - Move cancellation: `_handle_streaming_cancellation`, `_try_cancel_remote_task`
- At terminal emission points (`_finalize_streaming`, `_handle_streaming_error`, `_handle_streaming_cancellation`, sync finalization), emit `AgentEvent(skip_persist=True)` → `self.response_handler.handle(event)` (skip_persist because `tsm.persist_message` has already written)
- Mid-stream SSE (`send_agent_token` during streaming) stays inside `DirectTransport` via its own `sse_manager` reference
- Replace all 16 `notify_task_update` call sites in `ResponseProcessor` with `AgentEvent` emission — this is the bulk of the effort
- Delete `modules/ResponseProcessor.py` once migration is complete
- Update `AgentMessageProcessor` to use `self.transports["direct"]` instead of `ResponseProcessor`
- Update `tests/test_module_response_processor.py`

**Files touched:** 1 new + 3 modified + 1 deleted
**Estimated effort:** 7–9 hours
**Risk:** Medium-High — `ResponseProcessor` is 1,429 lines with 16 terminal emission sites, complex branching in `_finalize_streaming`, and the `tsm.persist_message` vs `skip_persist` interaction needs careful testing

### Phase 4: Router refactor + cleanup

**Changes:**
- Refactor `AgentMessageProcessor` into a thin router that picks the right transport class and delegates
- Remove `update_task_on_message` from `database_service.py` if all callers have migrated to `update_task_state_on_message` (or keep both but ensure the shared handler uses a single method)
- Audit all remaining direct `sse_manager.send_*` calls to confirm they're only used for transport-specific streaming (not for terminal/result events)
- Add integration-level test that feeds the same `AgentEvent` sequence through all three transport adapters and asserts identical DB + SSE outcomes

**Files touched:** 3–4 modified
**Estimated effort:** 2–3 hours
**Risk:** Low

---

## 4. Effort Summary

| Phase | Description | Effort | Risk | Status |
|---|---|---|---|---|
| 1 | Foundation + `RelayTransport` | 4–5 hrs | Low | **COMPLETED** |
| 2 | `WebhookTransport` | 2–3 hrs | Low | **COMPLETED** |
| 3 | `DirectTransport` (ResponseProcessor) | 7–9 hrs | Medium-High | **COMPLETED** |
| 4 | Router refactor + cleanup + parity test | 2–3 hrs | Low | Not started |

Each phase is independently shippable and testable. Phases 1–2 can be done together. Phase 3 can be deferred if needed; the parity benefit is already significant after phase 2 because the relay and webhook paths (the two most bug-prone) are unified.

---

## 5. Success Criteria

1. **Zero duplicated result-handling logic** — DB persist, SSE emit, and orchestration resume each have exactly one implementation in `AgentResponseHandler`
2. **Clear transport separation** — each transport class is self-contained; no cross-path imports between `DirectTransport`, `RelayTransport`, and `WebhookTransport`
3. **Existing tests pass** — all three test suites (`test_api_relay`, `test_api_webhooks`, `test_module_response_processor`) green
4. **Parity test** — new test asserts that feeding the same `AgentEvent` through all three transport adapters produces identical DB state
5. **Line count reduction** — `relay_service.py` drops by ~100–150 lines, `webhooks.py` drops by ~150 lines, `ResponseProcessor.py` is deleted (replaced by `DirectTransport`)
6. **Router simplicity** — `AgentMessageProcessor` becomes < 200 lines, focused on routing and middleware

---

## 6. Implementation Status

*Last updated: March 8, 2026*

### Phase 1: Foundation + RelayTransport — COMPLETED

All Phase 1 deliverables are implemented, integrated, and tested.

| Deliverable | File | Status |
|---|---|---|
| `AgentEvent` dataclass | `modules/agent_event.py` | Implemented — matches spec exactly (all 9 kinds, all fields) |
| `AgentResponseHandler` | `modules/agent_response_handler.py` | Implemented — full `match` dispatch, DB persist, SSE emit, `notify_task_update` delegation, orchestration resume with error isolation |
| `AgentTransport` ABC | `modules/transports/base.py` | Implemented — `dispatch()` abstract method |
| `RelayTransport` | `modules/transports/relay.py` (~350 lines) | Implemented — outbound dispatch, inbound `handle_publish_event` with cancellation guard, `_normalize` with full event type mapping including `task_status`/`task_interactive`, cancel/reply control methods |
| `relay_service.py` delegation | `services/relay_service.py` | Integrated — `_process_single_publish_event` delegates to `RelayTransport.handle_publish_event()` via `self._relay_transport` |
| `AgentMessageProcessor` wiring | `modules/AgentMessageProcessor.py` | Integrated — accepts `relay_transport` and `direct_transport` constructor params, lazy-constructs `RelayTransport` if needed |
| Unit tests | `tests/test_agent_response_handler.py` | 13 test cases covering all event kinds, `skip_persist` flag, orchestration resume error isolation |

### Phase 2: WebhookTransport — COMPLETED

| Deliverable | File | Status |
|---|---|---|
| `WebhookTransport` | `modules/transports/webhook.py` (~260 lines) | Implemented — `handle_webhook` with token validation, `parse_stream_response` (4 StreamResponse formats + raw Task fallback), `_task_to_event` normalization, idempotency check |
| `api/webhooks.py` thinned | `api/webhooks.py` | Integrated — thin FastAPI route delegates to `WebhookTransport.handle_webhook()` via `_get_webhook_transport()` factory |

### Phase 3: DirectTransport — COMPLETED

All `ResponseProcessor` logic has been absorbed into `DirectTransport`. All 16 `notify_task_update` call sites replaced with `_emit_terminal()` → `AgentEvent(skip_persist=True)` → `AgentResponseHandler.handle()`. `AgentMessageProcessor` updated to use `DirectTransport` directly. `ResponseProcessor.py` retained as dead code (removed in Phase 4).

| Deliverable | File | Status |
|---|---|---|
| `DirectTransport` full implementation | `modules/transports/direct.py` (~1,405 lines) | All `ResponseProcessor` methods absorbed: streaming (`handle_streaming_response`, all `_handle_stream_*` sub-handlers, `_finalize_streaming`), sync (`handle_sync_response`, `_process_sync_response`, `_poll_task_until_complete`, `_finalize_polled_task`), setup (`_setup_task_tracking`, `_setup_tracking_context`), S3 conversion (`_convert_inline_bytes_to_s3`, `_convert_streaming_parts_to_s3`), cancellation (`_handle_streaming_cancellation`, `_try_cancel_remote_task`). `MessageStreamingState` dataclass moved to module level. |
| `_emit_terminal` helper | `modules/transports/direct.py` | New method: maps `TaskState` → `AgentEvent.kind` (`canceled` / `error` / `interactive` / `response`), sets `skip_persist=True`, delegates to `self.response_handler.handle()`. 16 call sites replaced. |
| `AgentMessageProcessor` updated | `modules/AgentMessageProcessor.py` (~340 lines) | Removed `response_processor` parameter (now required `direct_transport`). Exception handler builds `ProcessingContext` fallback and calls `dt._emit_terminal()`. Removed `notify_task_update` import. `_dispatch_via_relay` simplified (legacy fallback removed — requires `relay_transport`). |
| `RoomMessageCenter` updated | `modules/RoomMessageCenter.py` | Removed `ResponseProcessor` instantiation. `AgentMessageProcessor` no longer receives `response_processor=`. `QueueExecutor` receives `direct_transport` as `response_processor`. |
| `QueueExecutor` updated | `modules/QueueExecutor.py` | Import changed from `ResponseProcessor` to `DirectTransport`. Type annotation updated. Legacy `_process_single_message_inline` still works (same method signatures). |
| Tests updated | `tests/test_module_response_processor.py` | All imports → `DirectTransport`. `_make_processor` creates `DirectTransport` via `object.__new__`. All `patch("...notify_task_update")` replaced with `response_handler.handle = AsyncMock()` assertions. |
| Tests updated | `tests/test_dispatch_middleware.py` | Removed `response_processor=` from constructor calls. Added `direct_transport=`. Relay test uses `relay_transport` mock. |
| Tests updated | `tests/test_multimodal_errors.py` | All imports → `DirectTransport` with `response_handler=MagicMock()`. |
| Bug fixes during review | `modules/transports/direct.py` | Fixed operator precedence in `_emit_terminal` `text=` field (added explicit parentheses). Removed duplicate `s3_service` property definition. |

**Test results:** 97 tests pass (11 response processor + 10 dispatch middleware + 6 multimodal + 13 handler + 20 webhooks + 37 relay).

### Phase 4: Router refactor + cleanup — COMPLETE

Phase 4 completed. Changes made:
- Deleted `modules/ResponseProcessor.py` (1,430-line dead file, zero live importers)
- Extended `DispatchContext` with optional `token`, `step_number`, `total_steps` fields
- Implemented `DirectTransport.dispatch(ctx, message)` with logic from `AgentMessageProcessor._dispatch_direct`
- Refactored `AgentMessageProcessor` into a thin router with `self.transports[ctx.transport].dispatch(ctx, message)` dict lookup
- Removed `QueueExecutor._process_single_message_inline` (130-line legacy fallback) and `response_processor` param entirely
- Made `agent_message_processor` a required param in `QueueExecutor`
- Updated `RoomMessageCenter` wiring: `transports={"direct": ...}` dict, removed `response_processor=`
- Updated `test_dispatch_middleware.py` integration tests to mock `dt.dispatch()` instead of `dt.handle_sync_response`
- Added `tests/test_transport_parity.py` — multi-event sequence parity tests asserting identical DB+SSE outcomes for `skip_persist=True` (direct) vs `skip_persist=False` (relay/webhook)
- Renamed `tests/test_module_response_processor.py` → `tests/test_direct_transport.py`
- `update_task_on_message` kept for now (single caller in `_finalize_polled_task` polling path) with Phase 5 TODO comment
- `SupervisorExecutor` required zero changes (public API of `process_single_message` unchanged)

### Hub-side streaming improvements (related)

In addition to the backend refactoring, the `hybro-hub` dispatcher was updated to support incremental event streaming:

| Change | File | Description |
|---|---|---|
| AsyncIterator dispatch | `hub/dispatcher.py` | `dispatch()` converted from `async def -> list[dict]` to `async def -> AsyncIterator[list[dict]]` — streaming events (`agent_token`, `artifact_update`, `task_status`) are yielded individually as they arrive; terminal events yielded as a final batch |
| Pre-publish `task_submitted` | `hub/main.py` | `_handle_user_message` and `_handle_user_reply` publish `task_submitted` event immediately via `relay.publish()` before iterating `dispatch()`, providing instant UI feedback |
| Caller iteration | `hub/main.py` | Both callers use `async for batch in dispatcher.dispatch(...)` with per-batch `relay.publish()` |
| Empty exception fix | `hub/dispatcher.py` | `result.error = str(exc) or repr(exc) or "Unknown dispatch error"` ensures exceptions with empty `str()` (e.g., `TimeoutError()`) still produce proper `agent_error` events |
| Tests updated | `hub/tests/test_dispatcher.py` | All 3 dispatch tests updated to collect via `async for batch`, plus new `test_dispatch_error_with_empty_str` test |

---

## 7. Alternatives Considered

### A. Do nothing — keep fixing parity bugs as they appear

**Pros:** No refactoring risk.
**Cons:** Every new feature (new A2A event type, new state, new multimodal format) needs 3 implementations. Bug rate will continue.
**Verdict:** Rejected — the velocity cost compounds.

### B. Make `ResponseProcessor` the only path (relay feeds into it)

**Pros:** Maximum code reuse — relay events would be injected into `ResponseProcessor`'s streaming loop.
**Cons:** `ResponseProcessor` manages complex in-memory state (`TaskStateManager`, `CancellationToken`, accumulated `Part` lists) that don't apply to relay. Forcing relay events through it would require many no-op branches and make the already large class harder to reason about.
**Verdict:** Rejected — mixing transport concerns into one class creates worse coupling.

### C. Event bus / pub-sub

**Pros:** Maximum decoupling — each handler subscribes to event topics.
**Cons:** Over-engineered for 3 producers and 1 consumer. Adds infrastructure overhead (async queue, error handling, ordering guarantees) for no real benefit.
**Verdict:** Rejected — direct method calls are simpler and sufficient.

---

## 8. Open Questions

1. ~~**Double-write avoidance in Phase 3**~~ **RESOLVED**: `DirectTransport` sets `skip_persist=True` on `AgentEvent`; `AgentResponseHandler` checks this flag and skips `update_task_state_on_message` when set. See §2.8.

2. ~~**Streaming SSE during cloud path**~~ **RESOLVED**: Mid-stream `send_agent_token` stays in `DirectTransport` via its own `sse_manager` reference. Only terminal events go through `AgentResponseHandler`. See §2.8.

3. ~~**`RelayService` vs `RelayTransport` boundary**~~ **RESOLVED**: `RelayService` retains hub connection management (connect/disconnect, agent sync, offline queues, heartbeat). `RelayTransport` owns all business logic (event normalization, delegation to `AgentResponseHandler`, cancel/reply). `RelayService._process_single_publish_event` is replaced by a thin call to `RelayTransport.handle_publish_event()`.

4. ~~**Transport selection at runtime**~~ **RESOLVED (Phase 4)**: `AgentMessageProcessor` uses a `transports` dict keyed by name (`"direct"` / `"relay"`) and looks up `ctx.transport` (defaults to `"direct"`). `HubTransportMiddleware` mutates `ctx.transport = "relay"` for hub-connected agents via the composable `DispatchChain`. A formal `AgentRoutingPolicy` is not warranted — the middleware chain already fulfils that role and is extensible to new transports.

5. ~~**Webhook path `failed` kwarg**~~ **RESOLVED (Phase 4)**: `WebhookTransport` no longer calls `resume_queue_from_continuation` directly. It normalizes the task to an `AgentEvent` and delegates to `AgentResponseHandler.handle()`. `_on_error` and `_on_canceled` both call `_resume_orchestration(..., failed=True)`, guaranteeing correct behavior for all transports. Covered by `test_transport_parity.py`.

---

## 9. Audit Findings

The following issues were identified during the design review and are addressed in this version:

| # | Finding | Severity | Resolution |
|---|---|---|---|
| 1 | `notify_task_update` is 289 lines with idempotency, backfill, metadata resolution — cannot be trivially absorbed | High | `AgentResponseHandler` wraps `notify_task_update`, doesn't replace it (§2.2) |
| 2 | `_on_error` hardcoded `"failed"` — would persist wrong state for `canceled`/`rejected` | Medium | Added `"canceled"` kind; `_on_error` uses `e.state` for DB persist (§2.1, §2.2, §2.8) |
| 3 | Relay uses `send_agent_response` for some terminals, `notify_task_update` for others — mixed SSE | High | All terminals go through `notify_task_update` via `AgentResponseHandler._notify()` (§2.2, §2.8) |
| 4 | `processing_status` relay event missing from `AgentEvent.kind` | Medium | Added `"processing_status"` kind (§2.1, §2.8) |
| 5 | `cancel_relay_task` / `reply_to_relay_task` placement unspecified | Low | Added to `RelayTransport` as public methods (§2.3) |
| 6 | `AgentResponseHandler` dependency list incomplete (missing `room_message_center`) | Medium | Constructor now takes `db`, `sse`, `room_message_center` (§2.2) |
| 7 | ABC `dispatch()` doesn't fit relay/webhook inbound-only patterns | Medium | Documented as accepted pragmatic compromise (§2.8) |
| 8 | `sse_manager` injected in both `DirectTransport` and `AgentResponseHandler` | Low | Documented as accepted asymmetry with clear rationale (§2.3, §2.8) |
| 9 | `task_service` dependency missing from `DirectTransport` | Low | Added to `DirectTransport.__init__` (§2.3) |
| 10 | Phase 3 effort underestimated (15+ `notify_task_update` call sites to replace) | Low | Revised from 5–6 hrs to 7–9 hrs (§4) |
| 11 | Webhook `resume_queue_continuation` doesn't pass `failed=True` for errors | Low | **Resolved**: all transports now delegate to `AgentResponseHandler`, which passes `failed=True` in `_on_error` and `_on_canceled` (§7 item 5) |
| 12 | `_on_canceled` missing orchestration resume — relay path calls `_resume_orchestration(failed=True)` for canceled tasks; omitting it would leave relay queues stuck | **Critical** | `_on_canceled` now calls `_resume_orchestration(failed=True)` (§2.2). `DirectTransport` handles cancellation internally and never emits a `"canceled"` `AgentEvent`, so this only affects relay/webhook paths |
| 13 | `_on_submitted` missing `message_id` — `send_task_submitted` requires `message_id` as 2nd positional arg; also missing `step_number`, `total_steps` | Medium | Added `message_id`, `step_number`, `total_steps` to `_on_submitted` call (§2.2) |
| 14 | Duplication table `notify_task_update` counts wrong — ResponseProcessor has 16 calls (not ~8), relay has 2 (not 1) | Low | Corrected table counts (§1) |
| 15 | `_on_processing_status` missing `details` parameter — relay path passes `details=data.get("details")` to `send_processing_status` | Low | Added `details` field to `AgentEvent` dataclass, passed through in `_on_processing_status` (§2.1, §2.2) |
| 16 | Webhook `update_task_on_message` → `update_task_state_on_message` migration: full `Task` model persistence switches to partial dot-notation updates; artifact data must flow through `AgentEvent` fields to `notify_task_update` for backfill | Medium | Documented migration note in `WebhookTransport` section (§2.3) |
