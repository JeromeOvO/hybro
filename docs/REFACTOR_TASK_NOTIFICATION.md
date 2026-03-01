# Refactoring Task State Notification — Design & Implementation Plan

## 1. Problem Statement

The backend has two independent code paths that send `task_update` SSE events to
the frontend. They share the same destination (`sse_manager.send_task_update`) but
follow completely different routing logic:

**Inline execution path** (used by `ResponseProcessor`, `QueueExecutor`,
`AgentMessageProcessor`):
```
transition_task() / fail_task_and_notify()
  → notification_service.send_task_update()
    → sse_manager.broadcast("task_update", ...)
```

**Webhook path** (used by `api/webhooks.py`, `jobs/stale_task_checker.py`):
```
notify_task_update()                   ← idempotent, reads from DB
  → notification_service.send_task_update()
    → sse_manager.broadcast("task_update", ...)
```

This split creates several failure modes that cause task bubbles in the UI to
get permanently stuck in a non-terminal state (spinning indefinitely, timer
frozen) until the user refreshes the page:

1. **Polling timeout hole** — `_process_sync_response` silently returns `(True, None,
   None)` when `_poll_task_until_complete` times out, sending no SSE at all.

2. **`processing_status: failed` does not transition task bubbles** — when
   `QueueResult.FAILED` is returned, `RoomMessageCenter` sends `processing_status:
   failed` (clearing the room-level spinner) but never sends a per-task
   `task_update: failed`. If the individual inline notify in `ResponseProcessor`
   succeeded, both events arrive and everything is fine. If it didn't (e.g., exception
   swallowed, `task_info` was `None`), the task bubble has no recovery path.

3. **`fail_task_and_notify` DB-SSE coupling** — `transition_task` is called with
   `notify=False`, then `notification_service.send_task_update` is called directly.
   If `transition_task` raises (e.g., malformed task object), the SSE call is skipped.

4. **`ctx=None` silences the notification** — every `transition_task` call in
   `ResponseProcessor` passes `ctx=ctx if task_info else None`. When `task_info` is
   `None` (degraded mode after `_setup_task_tracking` fails), `notify` is effectively
   disabled even though `notify=True` is the default — because
   `transition_task` only calls `notify_task` when `ctx` is truthy (line 153).

The underlying design tension is: **the inline path notifies using in-memory state
without idempotency, while the webhook path is robust, DB-backed, and idempotent**.
The fix is to make the inline path use the same robust function as the webhook path.

---

## 2. Current Architecture

### 2.1 Notification Call Graph

```
ResponseProcessor.handle_sync_response()
  ├── happy path  → tsm.transition_task(completed, ctx=ctx)
  │                   └── notify_task(ctx) → notification_service.send_task_update()
  ├── error path  → tsm.transition_task(failed, ctx=ctx if task_info else None)
  │                   └── notify_task(ctx)  [skipped if ctx is None]
  │                   sse_manager.send_error()   [separate error event, no task_update]
  └── poll timeout → return (True, None, None)   ← NO SSE SENT

ResponseProcessor._handle_streaming_error()
  └── tsm.transition_task(failed, ctx=ctx) → notify_task(ctx) → ...

AgentMessageProcessor.process_single_message() streaming exception handler
  └── tsm.fail_task_and_notify()
        ├── transition_task(failed, notify=False) → persist only
        └── notification_service.send_task_update()  [skipped if transition_task raises]

QueueExecutor._process_single_message_inline() exception handler  [fallback when AgentMessageProcessor not injected]
  └── tsm.fail_task_and_notify()
        ├── transition_task(failed, notify=False) → persist only
        └── notification_service.send_task_update()  [skipped if transition_task raises]

QueueExecutor._resolve_agent_for_message()  [3 call sites: no agent, agent not found, agent inactive]
  └── tsm.fail_task_and_notify()
        ├── transition_task(failed, notify=False) → persist only
        └── notification_service.send_task_update()  [skipped if transition_task raises]

api/webhooks.notify_task_update()         ← idempotent, DB-backed, used by webhook + stale checker
  ├── db_service.update_last_notified_state()  [idempotency]
  ├── db_service.get_room_agent_message_by_message_id()  [DB round-trip]
  └── notification_service.send_task_update()
```

### 2.2 `ProcessingContext` as Notification Carrier

`ProcessingContext` currently carries notification-specific fields solely so the
inline path can call `notify_task(ctx)`:

```python
@dataclass
class ProcessingContext:
    room_id: str
    current_message: RoomAgentMessage
    agent_card: AgentCard          # ← used only for agent name resolution in notify
    user_message_id: str
    token: CancellationToken | None
    task_info: dict | None
    created_at: str | None         # ← used only in notify
    step_number: int | None        # ← used only in notify
    total_steps: int | None        # ← used only in notify
    send_sse: bool

    @property
    def tracked_message_id(self) -> str | None:
        return self.current_message.message_id if self.task_info else None
```

`agent_card`, `created_at`, `step_number`, `total_steps` are passed into `ctx`
specifically to power the inline notification. After the refactor, `created_at`,
`step_number`, and `total_steps` become unnecessary because `notify_task_update`
reads them from the DB. `agent_card` is retained because `notify_task(ctx, ...)`
is kept for non-terminal streaming progress updates (see Section 5.2).

### 2.3 Frontend Behavior (for reference)

The frontend (`useRoomWebhook.ts`) handles `task_update` and `processing_status` as
separate SSE event types. `processing_status: failed` only shows an error banner and
calls `setProcessing(false)` — it does **not** transition individual task bubbles to a
terminal state. Only a `task_update` with a terminal status (`failed`, `completed`,
`canceled`, `rejected`) causes a task bubble to stop its timer and render the final
state. A stuck bubble therefore requires either a `task_update` terminal event or a
page refresh (which triggers the 10-minute stale-task hydration check).

---

## 3. Proposed Architecture

### 3.1 Design Principle

**The DB is the source of truth. SSE is a fan-out side-effect of DB writes.**

All code paths that transition a task to a terminal state should:
1. Write the new state to the DB (`transition_task` with `persist=True`).
2. Call `notify_task_update()` — the same idempotent, DB-backed function already
   used by the webhook and stale-task checker paths.

This eliminates the inline notification path entirely and makes all terminal
notifications go through one function with deduplication, DB round-trip validation,
and consistent field resolution.

### 3.2 Key Function: `notify_task_update`

`api/webhooks.py::notify_task_update` already implements the correct pattern:

- **Idempotent** via `db_service.update_last_notified_state(message_id, state_value)`
- **DB-backed** — reads `room_agent_message` to resolve `agent_name`, `created_at`,
  `step_number`, `total_steps`, `task_content`, `related_message_id`
- **Self-contained** — no `ProcessingContext` or in-memory state required
- **Replayable** — calling it twice with the same state is a no-op

After the refactor, the inline path calls this function directly instead of going
through `notify_task(ctx)` / `fail_task_and_notify`.

**Signature change:** The current signature takes a `Task` object as its second
argument. The function extracts significantly more than just the state from it:

| Field accessed on `task` | Purpose |
|---|---|
| `task.status.state` | Core state enum |
| `task.artifacts` + parts | Extract response content for completed tasks |
| `task.status.message.parts` | Extract error/status messages for failed, rejected, input_required, auth_required |
| `task.id`, `context_id`, `history`, `metadata` | Reconstruct Task when artifacts are missing (lines 167-180) |
| Full `task` object | Persisted to `room_agent_message.message_content.message_task` (line 185) |

A naive change to `state: TaskState` is **insufficient** — it would lose access to
artifacts, error messages, and the full task object needed for DB persistence.

**Recommended approach:** Remove the `task` parameter but have the function read the
full `Task` from `room_agent_message.message_content.message_task` in the DB. This
requires that callers **persist the task to the DB before calling
`notify_task_update`** — which is already the case for all paths where
`transition_task(persist=True)` or `update_task_on_message` runs first.

```python
async def notify_task_update(
    message_id: str,
    state: TaskState,
    room_id: str,
    user_id: str,
    error: str | None = None,
    send_processing_status: bool = False,
) -> None:
    """Idempotent, DB-backed SSE notification for task state changes.

    Single canonical entry point for all task_update SSE events.
    Safe to call multiple times with the same state (no-op on duplicate).

    PREREQUISITE: The task must already be persisted to the DB with its
    current state, artifacts, and status message BEFORE calling this
    function. The function reads the full Task object from the DB to
    extract content, error messages, and status messages.
    """
    ...
```

The function internally:
1. Calls `update_last_notified_state` for idempotency
2. Reads `room_agent_message` from DB (including `.message_content.message_task`)
3. Extracts `content` from `task.artifacts`, `error` from `task.status.message`,
   `requires_input`/`requires_auth` from `task.status.state`
4. Calls `notification_service.send_task_update`

The existing webhook call site passes `state=updated_task.status.state`. The stale
task checker passes `state=current_task.status.state`. Both already persist the task
before calling.

**IMPORTANT — Write-side logic in current `notify_task_update` that must be preserved:**

The current implementation (lines 157–203 of `webhooks.py`) is **not** read-only — it
performs three write-side operations that the refactored function must account for:

| Operation | Lines | What it does |
|---|---|---|
| Artifact backfill | 162–183 | If `completed` and task has no artifacts but `message_text` exists, reconstructs a new `Task` with synthetic artifacts from `message_text` (A2A compliance for agents that send `statusUpdate` without artifacts) |
| `message_task` persistence | 185 | `room_agent_message.message_content.message_task = task` — persists the (possibly backfilled) task onto the message |
| `message_text` backfill | 188–194 | Backfills `message_text` from content/error/status_message if empty |

After the refactor, these writes can follow one of two strategies:

**Option A (recommended):** Keep the write-side logic inside `notify_task_update`.
The function reads the task from DB, performs artifact/text backfill if needed,
persists the enriched message, then sends the SSE. This preserves current behavior
with zero impact on callers. The "read from DB" prerequisite still applies (callers
must persist the task before calling), but `notify_task_update` may enrich and
re-persist it.

**Option B:** Move the write-side logic to each caller. The webhook handler would
perform artifact backfill before calling `notify_task_update`; the stale task checker
would persist its synthetic failed task before calling. This makes `notify_task_update`
purely a read-then-notify function but scatters the backfill logic across callers.

Option A is recommended because it keeps the backfill logic centralized and avoids
regression risk in the webhook path.

**`agent_name` resolution:** The current function receives `agent_name` as a parameter
(resolved by the webhook handler from `room.room_agent_set[agent_id]`). The proposed
signature drops this parameter, but `agent_name` is **not stored on the
`room_agent_message`**. The refactored function must either:
- Resolve `agent_name` internally by reading `room_agent_message.agent_id` and
  looking up `room.room_agent_set` (adds one DB call: `get_room_by_room_id`), or
- Keep `agent_name` as an optional parameter for callers that already have it.

Recommended: resolve internally. The extra DB call is acceptable for terminal-only
notifications, and it eliminates a parameter that most new callers cannot supply.

### 3.3 Target Call Graph

```
ResponseProcessor / QueueExecutor / AgentMessageProcessor (all paths)
  ├── tsm.transition_task(new_state, persist=True, notify=False)
  │     └── persist_message()   [DB write only; notify param removed or always False]
  └── notify_task_update(message_id, state, room_id, ...)
        ├── db_service.update_last_notified_state()   [idempotency]
        ├── db_service.get_room_agent_message_by_message_id()
        └── notification_service.send_task_update()
              └── sse_manager.broadcast("task_update", ...)

api/webhooks.handle_a2a_webhook()   [updated to new signature]
  └── notify_task_update(message_id, state=task.status.state, ...)

jobs/stale_task_checker              [unchanged]
  └── notify_task_update(...)

RoomMessageCenter (on QueueResult.FAILED — process_room_user_message)
  ├── send_processing_status(FAILED, ...)   [unchanged]
  └── NEW: _notify_all_non_terminal_tasks_failed(room_id, user_message_id)
        └── for each non-terminal message under user_message_id → notify_task_update(failed)
              [idempotency check skips messages already notified as terminal]

QueueExecutor (on QueueResult.FAILED — resume_from_continuation)
  ├── send_processing_status(ERROR, ...)    [pre-existing; see Section 3.7 note]
  └── NEW: _notify_all_non_terminal_tasks_failed(room_id, user_message_id)
        └── for each non-terminal message under user_message_id → notify_task_update(failed)
```

### 3.4 What `transition_task` Becomes

`transition_task` loses its `notify` parameter and its `ctx` parameter. It becomes a
pure DB write with a terminal-state guard:

```python
async def transition_task(
    self,
    message: RoomAgentMessage,
    new_state: TaskState,
    *,
    error: str | None = None,
    content: str | None = None,
    persist: bool = True,
) -> None:
    """Persist a task state transition. Does NOT send SSE — callers
    call notify_task_update() separately after this returns."""
    task = get_task(message)
    if not task:
        return
    if task.status and is_terminal_state(task.status.state):
        logger.warning("Attempted to overwrite terminal state ...")
        return
    task.status = TaskStatus(state=new_state)
    if error:
        task.status.message = Message(...)
    message.task_updated_at = utcnow()
    if persist:
        await self.persist_message(message)
```

`fail_task_and_notify` is removed. Its call sites are replaced with
`transition_task` + `notify_task_update`.

### 3.5 `ProcessingContext` After Refactor

With notification decoupled from the processing context, most notification-specific
fields can be removed. However, `agent_card` **must be retained** because
`_handle_stream_status_update` uses `tsm.notify_task(ctx, ...)` for non-terminal
streaming progress updates (e.g., status messages during a stream), which stays on
the inline path (see Section 5.2). These calls need `agent_card` for agent name
resolution.

| Field | Used for | After refactor |
|---|---|---|
| `agent_card` | `notify_task(ctx)` agent name resolution | **Retained** — still needed for non-terminal inline streaming notifies |
| `created_at` | `notify_task(ctx)` | Removed; `notify_task_update` reads from DB |
| `step_number` | `notify_task(ctx)` | Removed; `notify_task_update` reads from DB |
| `total_steps` | `notify_task(ctx)` | Removed; `notify_task_update` reads from DB |
| `tracked_message_id` property | `notify_task(ctx)` message_id | Removed |

`ProcessingContext` slims down to:

```python
@dataclass
class ProcessingContext:
    room_id: str
    current_message: RoomAgentMessage
    agent_card: AgentCard              # retained: used by inline notify_task for non-terminal streaming states
    user_message_id: str
    token: CancellationToken | None = None
    task_info: dict | None = None
    send_sse: bool = False
```

### 3.6 `notify_task_update` Module Location

Currently `notify_task_update` lives in `api/webhooks.py`. It should be moved to
a new `services/task_notification_service.py` so that `ResponseProcessor` and
`QueueExecutor` can import it without creating an `api` → `modules` circular
dependency.

`api/webhooks.py` then imports it from the new location. The function signature
changes as described in Section 3.2 (takes `state: TaskState` instead of `task:
Task`).

Proposed location: `services/task_notification_service.py`

```python
# services/task_notification_service.py

async def notify_task_update(
    message_id: str,
    state: TaskState,
    room_id: str,
    user_id: str,
    error: str | None = None,
    send_processing_status: bool = False,
) -> None:
    """Idempotent, DB-backed SSE notification for task state changes.

    Single canonical entry point for all task_update SSE events.
    Safe to call multiple times with the same state (no-op on duplicate).
    Reads agent_name, created_at, step_number, total_steps from DB.

    send_processing_status: when True, also broadcasts a processing_status
    SSE for terminal states. Set to True only from the webhook path, which
    lacks a queue-level processing_status sender. Must be False from the
    inline execution path to avoid duplicating the queue-level event that
    RoomMessageCenter / QueueExecutor already sends.
    """
    ...
```

**`send_processing_status` flag:** The current `notify_task_update` implementation
ends with `sse_manager.send_processing_status(room_id, state, message_id)` for
terminal states (line 233–234 of `webhooks.py`). This is appropriate for the webhook
path, which has no other sender of that event. However, calling the function from the
inline execution path would send a duplicate `processing_status` alongside the one
already sent by `RoomMessageCenter`/`QueueExecutor`. The flag defaults to `False` so
inline callers get only the `task_update` event; the webhook path passes `True`.

### 3.7 `RoomMessageCenter`: Safety Net on `QueueResult.FAILED`

After the inline notification path is consolidated, `processing_status: failed`
becomes an additional safety net rather than a gap. When `queue_result ==
QueueResult.FAILED`, `RoomMessageCenter` will:

1. Send `processing_status: failed` (unchanged).
2. Query the DB for **all** non-terminal agent messages associated with
   `user_message_id` — this includes both the message that failed and any sibling
   messages that were canceled by `_managed_queue`'s RAII cleanup with
   `notify=False`. Those siblings are persisted as `canceled` in the DB but no
   SSE was sent for them; the safety net covers them too.
3. For each, call `notify_task_update(state)` where `state` matches the DB record
   (`failed` for the active message, `canceled` for siblings). The idempotency
   check in `notify_task_update` ensures that messages already notified as terminal
   are skipped (no double-notification).

This means even if the individual per-message notification inside `ResponseProcessor`
fails for any reason, the workflow-level failure event flushes all stuck bubbles,
including sibling steps.

**All entry points must wire this in.** The safety net must be called in every
code path where `processing_status: FAILED` is sent and agent messages may exist:

**V1 paths (2 locations):**

- `RoomMessageCenter.process_room_user_message` (line ~259) — V1 processing path.
  Sends `SSEProcessingStatus.FAILED`.
- `QueueExecutor.resume_from_continuation` (line ~741) — V1 push-notification resume.
  **Note:** this path currently sends `SSEProcessingStatus.ERROR`, not `FAILED` — a
  pre-existing inconsistency that should be fixed to `FAILED` alongside this refactor.

**V2 Supervisor paths (4 locations with agent messages):**

- `RoomMessageCenter._handle_v2_run_result` case `RunStatus.FAILED` (line ~1232) —
  **the main V2 failure exit**. Cancels DB records via `cancel_agent_messages_by_ids`
  and `cancel_descendants` but sends NO per-task `task_update`. This is the highest-
  priority V2 gap.
- `RoomMessageCenter._handle_v2_run_result` case `RunStatus.CANCELED` (line ~1214) —
  sends `processing_status: CANCELED` but no per-task `task_update`. Same gap as
  the FAILED case.
- `RoomMessageCenter._process_supervisor_v2` exception handler (line ~556) — may have
  agent messages from partial execution.
- `RoomMessageCenter._resume_supervisor_v2` corrupted trajectory (line ~642), room
  not found (line ~764), and executor exception (line ~911) — prior agent messages
  from earlier steps exist.

**V2 paths that can be skipped** (no agent messages exist):
- `process_room_user_message` V2 guard (line ~177) — fires before any dispatch.
- `_process_supervisor_v2` corrupted extend_info (line ~361) — fires before dispatch.

All locations with agent messages must call `_notify_all_non_terminal_tasks_failed`
after `send_processing_status`. The helper lives on `RoomMessageCenter` and is
called from both V1 and V2 paths.

---

## 4. Bugs Fixed by This Refactor

| Bug | Root Cause | Fixed by |
|---|---|---|
| Polling timeout — no SSE sent | `_process_sync_response` returns `(True, None, None)` without transitioning task | Call `transition_task(failed)` + `notify_task_update` in the timeout branch |
| `processing_status: failed` doesn't unstick bubbles | `RoomMessageCenter` only sends room-level event | Section 3.7: safety-net scan of non-terminal tasks |
| `fail_task_and_notify` skips SSE if `transition_task` raises | Separate DB write and SSE call with no error boundary | `transition_task` is now DB-only; SSE call is independent |
| `ctx=None` silences notification in degraded mode | `tracked_message_id` returns `None` when `task_info` is `None` | `notify_task_update` takes `message_id` directly; no `ctx` needed |
| Duplicate notifications possible | Inline path has no idempotency | `notify_task_update` deduplicates via `update_last_notified_state` |
| Notification uses stale in-memory state | `ctx` carries `agent_card`, `created_at` etc. from before the call | `notify_task_update` reads from DB; always reflects persisted state. **Caveat:** `_finalize_streaming` already-terminal branch must persist `streaming_state.full_response_text` to `message_text` before calling `notify_task_update` — see Section 5.11 |

---

## 5. Risks and Mitigations

### 5.1 Extra DB Read Per Notification

`notify_task_update` calls `get_room_agent_message_by_message_id` on every
invocation. The inline path currently skips this (uses in-memory `ctx`). This adds
one DB read per terminal task notification.

**Mitigation:** Terminal notifications are infrequent (one per completed task, not
per chunk). The existing webhook path already pays this cost. Acceptable.

### 5.2 Non-terminal State Notifications (Streaming Progress Updates)

The inline path currently sends `task_update` SSE for intermediate states too
(e.g., `working`, `input-required`, `auth-required`) via `notify_task(ctx, ...)`.
The webhook path only notifies on terminal and interactive states (line 353 of
`webhooks.py`: `is_terminal_state(new_state) or new_state in INTERACTIVE_STATES`).

Routing all intermediate notifications through `notify_task_update` would add one
DB read per streaming chunk, which is too expensive.

**Mitigation:** Keep the inline `notify_task(ctx, ...)` path for non-terminal /
intermediate state updates. Only terminal-state and interactive-state transitions go
through `notify_task_update`. Add a `is_terminal_or_interactive(state)` guard:

```python
if is_terminal_state(new_state) or new_state in INTERACTIVE_STATES:
    await notify_task_update(message_id, state=new_state, room_id=room_id, ...)
else:
    # Lightweight inline notify for intermediate states (working, etc.)
    await tsm.notify_task(ctx, new_state, ...)
```

Because `notify_task(ctx, ...)` is kept for this path, `ctx.agent_card` must remain
in `ProcessingContext` (see Section 3.5).

### 5.3 Duplicate `processing_status` Event from Inline Path

`notify_task_update` currently sends an additional `processing_status` SSE for
terminal states (via `sse_manager.send_processing_status`). This is correct for the
webhook path, which is the sole sender in that flow. However, when called from the
inline execution path, `RoomMessageCenter` and `QueueExecutor` already send the
queue-level `processing_status` separately. Calling `notify_task_update` from the
inline path would emit a duplicate.

**Mitigation:** Add a `send_processing_status: bool = False` parameter to
`notify_task_update` (see Section 3.6). The webhook path passes `True`; all inline
call sites pass `False` (the default).

### 5.4 Circular Import (`api/webhooks.py` ↔ `modules/`)

Moving `notify_task_update` out of `api/webhooks.py` into a `services/` module
breaks the current module boundary.

**Mitigation:** `services/task_notification_service.py` is at the `services` layer,
which is already imported by `modules/`. `api/webhooks.py` imports from `services/`
(it already imports `notification_service`, `db_service`, `sse_manager` from there).
No circular import is introduced.

### 5.5 `notify_task_update` Retry Logic for Race Conditions

The current implementation in `api/webhooks.py` has a 3-attempt retry loop waiting
for task tracking to be present (lines 148–155). This guards a race where a webhook
arrives before `RoomMessageCenter` has finished persisting the initial task record.

When called from `ResponseProcessor`, the task record always exists already (the
inline path is called *after* `_setup_task_tracking`). The retry loop is therefore
a no-op in the new call sites but does no harm.

When called from pre-tracking call sites (e.g., `_resolve_agent_for_message` agent
resolution failures), the retry loop will exhaust its attempts, find no task tracking,
and exit early without sending an SSE. This is correct — see Section 5.7.

### 5.6 No Test Suite

The repository has no automated tests. A refactor touching the notification critical
path carries regression risk.

**Mitigation:** Execute in two phases. Phase 1 moves `notify_task_update` to a shared
module and adds the safety net without changing any existing call sites. Phase 2
replaces inline notifications one call site at a time, verifying end-to-end after
each. See Section 8.

### 5.7 Pre-Tracking Messages and `notify_task_update` Early Exit

Agent-resolution failures in `QueueExecutor._resolve_agent_for_message` (and
`AgentMessageProcessor` for the supervisor path) fire **before** `_setup_task_tracking`
completes. At these call sites, the `RoomAgentMessage` may have no `Task` object and
`has_task_tracking` is `False`.

When `notify_task_update` is called for such a message, its retry loop (3 attempts ×
500ms) checks `room_agent_message.has_task_tracking` and **returns early if not set**
— no SSE is sent. This is the same end result as today (`fail_task_and_notify` also
produces no visible frontend effect for pre-tracking messages because no task bubble
exists). There is no regression, but `notify_task_update` does not "still fire" in the
sense of sending an SSE — it fires and exits early via the idempotency/tracking guard.

**Mitigation:** This is a non-issue in practice. If task tracking was never set up,
no task bubble exists in the frontend, so there is nothing to unstick. The safety net
in Section 3.7 (`_notify_all_non_terminal_tasks_failed`) also handles this correctly:
it queries messages from the DB and skips those without task tracking.

### 5.8 `_finalize_polled_task` Does Not Use `transition_task`

`ResponseProcessor._finalize_polled_task` is a special case: it does NOT call
`tsm.transition_task` for DB persistence. Instead, it calls
`database_service.update_task_on_message(message_id, completed_task.model_dump(...))`
directly — a full task replacement with the polled remote task object, not a
state-only transition.

After the refactor, `_finalize_polled_task` should become:
1. `database_service.update_task_on_message(...)` — unchanged (full task replacement)
2. `notify_task_update(message_id, state, ...)` — replaces `tsm.notify_task(ctx, ...)`

The key difference from other call sites is that step 1 is NOT `transition_task` —
it's a broader DB update that writes the entire task object (artifacts, status,
history) received from the remote agent. `transition_task`'s terminal-state guard
would incorrectly block legitimate state writes here (e.g., the remote agent may
report a different terminal state than what was locally recorded during streaming).

### 5.9 HITL `AWAITING_INPUT` Interaction with Safety Net

`HITL_DESIGN.md` introduces `ProcessingStatus.AWAITING_INPUT` and
`SSEProcessingStatus.AWAITING_INPUT` as non-terminal states. The safety net in
Section 3.7 (`_notify_all_non_terminal_tasks_failed`) scans for all non-terminal
agent messages on `QueueResult.FAILED` and calls `notify_task_update(state=<DB
state>)` for each.

An `AWAITING_INPUT` task is non-terminal, so the safety net would catch it. This is
**correct behavior** — if the overall queue fails, a task awaiting user input should
also be notified as failed. However, `notify_task_update` reads the DB state and
sends it as-is. If the DB state is still `input-required` (not yet transitioned to
`failed` by the queue-level cleanup), the SSE would carry the wrong terminal intent.

**Mitigation:** The `_managed_queue` RAII cleanup in `QueueExecutor` cancels all
remaining queue items with `transition_task(canceled, persist=True, notify=False)`
before `QueueResult.FAILED` reaches the safety net. So the DB state for sibling
messages (including `AWAITING_INPUT` ones) will already be `canceled` by the time
the safety net reads them. The active message that caused the failure will be
transitioned to `failed` by `ResponseProcessor` or `AgentMessageProcessor` before
the queue result propagates. Edge cases should be verified in the smoke test.

### 5.10 `update_last_notified_state` Failure Silently Drops Notifications

If the MongoDB call to `update_last_notified_state` fails (connection error, timeout),
`database_service` catches the exception and returns `False`. Back in
`notify_task_update`, `False` is treated as "duplicate notification" and the function
**silently returns** with only a debug-level log. A transient DB error causes a
legitimate state change notification to be permanently lost.

This is a pre-existing bug (not introduced by this refactor), but the refactor makes
it more impactful because `notify_task_update` becomes the **sole** notification path
for terminal states.

**Mitigation:** Change the error-handling in `update_last_notified_state` (or in
`notify_task_update` itself) so that a DB failure is treated as "proceed with
notification" rather than "skip as duplicate." The worst case becomes a duplicate SSE
(harmless — the frontend upserts idempotently) rather than a missed SSE (stuck
bubble). Concretely: on exception, return `True` instead of `False`, or catch the
exception at the `notify_task_update` level and proceed.

### 5.11 `_finalize_streaming` Already-Terminal Branch: Stale `message_text`

In `_finalize_streaming`, when `already_terminal` is True (terminal status arrived
during streaming via `_handle_stream_status_update`), the current code passes
`content=streaming_state.full_response_text` directly to `notify_task`. After the
refactor, `notify_task_update` reads content from the DB.

But `message_content.message_text` is only set in `_finalize_streaming` for the
**not-yet-terminal** path (line ~610). In the `already_terminal` branch, the
streaming text exists only in-memory (`streaming_state.full_response_text`) and was
never persisted to `message_text`. After the refactor, `notify_task_update` would
read `message_text` from the DB and find it empty or stale.

**Mitigation:** In the `already_terminal` branch, set
`ctx.current_message.message_content.message_text = streaming_state.full_response_text`
and call `tsm.persist_message(ctx.current_message)` **before** calling
`notify_task_update`. This ensures the DB contains the correct content when the
notification function reads it.

### 5.12 V2 Supervisor Failure Paths Not Covered by Original Safety Net Scope

The original design scoped the safety net to two V1 locations. However, the V2
Supervisor path has **7 additional failure exits** that send `processing_status: FAILED`
without any per-task `task_update`:

| Location | Line | Agent messages exist? |
|---|---|---|
| `_handle_v2_run_result` RunStatus.FAILED | ~1232 | Yes — cancels DB records but no per-task SSE |
| `_handle_v2_run_result` RunStatus.CANCELED | ~1214 | Yes — sends `processing_status: CANCELED` but no per-task SSE |
| `_process_supervisor_v2` planning error | ~529 | Maybe (depends on prior steps) |
| `_process_supervisor_v2` unhandled exception | ~556 | Maybe (partial execution) |
| `_resume_supervisor_v2` corrupted trajectory | ~642 | Yes (prior steps) |
| `_resume_supervisor_v2` room not found | ~764 | Yes (prior steps) |
| `_resume_supervisor_v2` executor exception | ~911 | Yes (prior steps) |
| `process_room_user_message` V2 guard | ~177 | No (before dispatch) |
| `_process_supervisor_v2` corrupted extend_info | ~361 | No (before dispatch) |

The most critical is `_handle_v2_run_result` at line ~1232 — it calls
`cancel_agent_messages_by_ids` and `cancel_descendants` to update DB states, but
never sends `task_update` SSE events. This is the **exact same bug** as the V1
`QueueResult.FAILED` path, just in V2.

**Mitigation:** Wire `_notify_all_non_terminal_tasks_failed` into all V2 failure
paths that may have agent messages (see updated Section 3.7 and Phase 1 steps).

### 5.13 Process Crash Between `transition_task` and `notify_task_update`

If the process crashes between the DB write (`transition_task`) and the SSE send
(`notify_task_update`), the task is terminal in DB but the frontend never learns.
Neither the safety net (in-process only) nor the stale task checker (queries
non-terminal tasks only) can recover this.

**This is NOT a regression.** The current `fail_task_and_notify` has the identical
two-step structure: `transition_task` at line 181, `send_task_update` at line 184.
A crash between those lines has the identical gap. The refactor makes it more visible
but does not change the risk profile.

**Recovery mechanisms:**
- Page refresh (re-hydrates from DB — always works)
- `reconcileWithDb` (runs after SSE disconnect — partially covers reconnections)
- Phase 3 client-side watchdog (30s `setInterval` — covers all cases but not yet built)

### 5.14 `agent_name` Not Stored on `room_agent_message`

The current `notify_task_update` receives `agent_name` as a parameter (line 70 of
`webhooks.py`) and passes it to `notification_service.send_task_update` (line 219).
The webhook handler resolves it from `room.room_agent_set[agent_id]` before calling
(lines 369–372). The stale task checker does **not** pass `agent_name` at all.

The proposed signature (Section 3.2) drops `agent_name`, intending the function to
read everything from `room_agent_message`. But `agent_name` is **not** a field on
`RoomAgentMessage` — it is resolved at call time from the room's agent registry.

**Mitigation:** The refactored `notify_task_update` should resolve `agent_name`
internally: read `agent_id` from `room_agent_message.agent_id`, then call
`get_room_by_room_id` and look up `room.room_agent_set.get(agent_id)`. This adds
one small DB read (room lookup) per terminal notification — acceptable given rooms
are frequently cached and terminal notifications are infrequent. See also the
updated internal resolution steps in Section 3.2.

### 5.15 `stale_task_checker` Synthetic Failed Task Not Persisted Before Notify

The `stale_task_checker` has **two** call sites for `notify_task_update`, each with
different semantics:

| Call site | Line | What it passes |
|---|---|---|
| Polling re-check (happy path) | ~250 | `task=current_task` — real task from remote agent, already persisted via `update_task_on_message` |
| Force-fail stale task | ~415 | `task=failed_task` — **synthetic** `Task(status=TaskStatus(state=TaskState.failed))` constructed in-memory |

The line-415 call site constructs a synthetic failed `Task` object and passes it
directly. The current `notify_task_update` persists it to the DB at line 185
(`room_agent_message.message_content.message_task = task`). After the refactor, if
the function reads the task from DB instead, it would read the **stale** (non-failed)
task from DB, not the synthetic failed one.

**Mitigation:** The stale task checker's force-fail path must **persist** the failed
task to the DB before calling `notify_task_update`. Add a
`db_service.update_task_on_message(message_id, failed_task.model_dump(mode="json"))`
call before the `notify_task_update` call. Alternatively, if Option A from Section 3.2
is used (write-side logic preserved inside `notify_task_update`), the function would
still need the task as a parameter for this path — contradicting the proposed
signature change. This strengthens the case for **Option A** or for keeping an
optional `task: Task | None = None` parameter for callers that supply a task not yet
in the DB.

---

## 6. Design Alternatives Considered

### 6.1 Alternative A: Event-Driven Notification via Internal Event Bus

**Idea:** Replace all `notify_task_update` calls with an internal event bus. Each
call to `transition_task` publishes a `TaskStateChanged` event. A single subscriber
handles SSE broadcasting.

**Pros:**
- Perfect single-responsibility: `transition_task` only changes state; notification
  is completely decoupled.
- Adding new side effects (logging, webhooks, metrics) requires only a new subscriber.
- Eliminates "forgot to call `notify_task_update`" bugs by construction.

**Cons:**
- Significant infrastructure overhead — requires an event bus (in-process asyncio
  pubsub or Redis Streams) and event schema definitions.
- Eventual consistency: events are async, so SSE delivery timing becomes less
  predictable. The current system guarantees in-order SSE within a synchronous path.
- Error handling is harder — a failed subscriber must not block state transitions,
  but must still retry notifications.
- Overkill for a system with ~15 notification call sites and a single consumer (SSE).

**Verdict:** Architecturally elegant but disproportionate to current complexity. If
the system grows to need webhooks, audit logs, or multi-channel notifications, this
becomes the right choice. For now, the refactored `notify_task_update` approach is
simpler and sufficient.

### 6.2 Alternative B: DB Polling Instead of In-Process SSE

**Idea:** Frontend polls a `GET /tasks/{room_id}/updates?since={timestamp}` endpoint
at 2-3s intervals. Remove all SSE notification logic from the processing pipeline.

**Pros:**
- Eliminates the entire "process crash between DB write and SSE send" class of bugs.
- Trivially multi-instance safe — no need for cross-instance SSE coordination.
- Simplifies backend code substantially (~200 lines of notification logic removed).

**Cons:**
- 2-3s polling latency is unacceptable for token-by-token streaming (the primary
  user-facing feature). Streaming tokens would still need SSE or WebSocket.
- Higher DB load — `N_users × N_rooms × poll_frequency` queries vs. event-driven.
- The hybrid approach (SSE for streaming, polling for state) doubles complexity.

**Verdict:** Inappropriate for a streaming-first system. Could be viable as a
**fallback reconciliation** mechanism (which already exists as `reconcileWithDb` on
reconnection).

### 6.3 Alternative C: Merge `notify_task_update` Into `transition_task`

**Idea:** Make `transition_task` itself call `notify_task_update` unconditionally
after every successful state transition. No separate notification step.

**Pros:**
- Impossible to "forget" to notify after a state change.
- Reduces the API surface — callers only deal with `transition_task`.
- Exactly mirrors the intent: "state change → notification" is always paired.

**Cons:**
- `transition_task` already has complex logic (guards, persistence, error handling).
  Adding SSE notification introduces a circular dependency on `SSEManager`, which is
  a service-layer concern, not a domain concern.
- Some callers intentionally batch transitions and notify once at the end. Making
  `transition_task` always notify breaks this pattern.
- `_finalize_polled_task` uses `update_task_on_message` (not `transition_task`), so
  this alternative still misses that path.
- Testing `transition_task` now requires mocking SSE infrastructure.

**Verdict:** Tempting but creates unwanted coupling between domain logic
(`TaskStateManager`) and infrastructure (SSE). The refactored `notify_task_update`
keeps these concerns separate while still reducing call sites to a single function.

### 6.4 Recommendation

**Proceed with the refactored `notify_task_update` approach** (the current design)
with the fixes from Sections 5.10–5.13. It provides the best balance of:

- Simplicity (no new infrastructure)
- Correctness (idempotency, full coverage of V1 + V2 paths)
- Maintainability (single canonical notification function)
- Testability (no circular dependencies)

Revisit Alternative A (event bus) if the system gains additional notification
channels (webhooks, email, push notifications) beyond SSE.

---

## 7. Files Changed

| File | Change |
|---|---|
| `services/task_notification_service.py` | **New** — move `notify_task_update` here from `api/webhooks.py`; refactor signature to accept `state: TaskState` instead of `task: Task`; function reads full Task from DB to extract artifacts/errors/content; add `send_processing_status` flag; fix `update_last_notified_state` failure to proceed (not skip) notification |
| `api/webhooks.py` | Import `notify_task_update` from new location; remove definition; update call site to pass `state=updated_task.status.state` and `send_processing_status=True` |
| `jobs/stale_task_checker.py` | Update import from `api.webhooks` to `services.task_notification_service`; at force-fail call site (line ~415), add `db_service.update_task_on_message` before `notify_task_update` to persist synthetic failed task |
| `modules/TaskStateManager.py` | Remove `notify` + `ctx` params from `transition_task`; remove `fail_task_and_notify` method; **keep** `notify_task` method (still used for non-terminal streaming notifies) |
| `modules/AgentMessageProcessor.py` | Replace `tsm.fail_task_and_notify(...)` streaming exception handler (line ~127) with `transition_task` + `notify_task_update`. This is the **primary** streaming error path used by both `QueueExecutor` and `SupervisorExecutor`. |
| `modules/ResponseProcessor.py` | Replace terminal `tsm.notify_task(ctx, ...)` / `tsm.transition_task(ctx=ctx...)` call sites with `transition_task` + `notify_task_update`; keep non-terminal `notify_task` calls for streaming progress; fix polling timeout to emit `failed`; in `_finalize_streaming` already-terminal branch: persist `streaming_state.full_response_text` to `message_text` before calling `notify_task_update` |
| `modules/QueueExecutor.py` | Replace `tsm.fail_task_and_notify(...)` call sites: 3 in `_resolve_agent_for_message` (lines ~365, ~383, ~409) + 1 in `_process_single_message_inline` fallback exception handler (line ~565); fix `resume_from_continuation` to send `FAILED` instead of `ERROR`; add `_notify_all_non_terminal_tasks_failed` call after `send_processing_status` in `resume_from_continuation` |
| `modules/RoomMessageCenter.py` | Add `_notify_all_non_terminal_tasks_failed()` helper; call it on `QueueResult.FAILED` in `process_room_user_message` (line ~259); **also** call it in V2 failure paths: `_handle_v2_run_result` RunStatus.FAILED (~1232) and RunStatus.CANCELED (~1214), `_process_supervisor_v2` exception (~556), `_resume_supervisor_v2` corrupted trajectory (~642), room not found (~764), executor exception (~911) |
| `models/processing.py` | Remove `created_at`, `step_number`, `total_steps`, `tracked_message_id` from `ProcessingContext`; retain `agent_card` |

No frontend changes required.

---

## 8. Implementation Plan

### Phase 1 — Extract and Add Safety Net (no call-site changes)

**Goal:** Move `notify_task_update` to a shared module and add the
`QueueResult.FAILED` safety net without touching any existing notification call
sites. Zero behavior change on the happy path.

| Step | Action | Risk |
|------|--------|------|
| 1.1 | Create `services/task_notification_service.py`. Move `notify_task_update` into it; refactor signature to take `state: TaskState` + `error: str | None` instead of `task: Task`; add `send_processing_status: bool = False` flag; remove the inline `send_processing_status` call (guard it behind the flag). **Preserve write-side logic** (artifact backfill, `message_task` persistence, `message_text` backfill) inside the function — see Section 3.2, Option A. Add internal `agent_name` resolution via `room.room_agent_set` lookup — see Section 5.14. | Medium |
| 1.2 | Update `api/webhooks.py` to import from new location; update call site to new signature (`state=updated_task.status.state`, `send_processing_status=True`). Remove the `agent_name` lookup from the webhook handler (now done internally). | Low |
| 1.3 | Update `jobs/stale_task_checker.py`: (a) change import from `api.webhooks` to `services.task_notification_service`; (b) at the force-fail call site (line ~415), persist the synthetic `failed_task` to DB via `db_service.update_task_on_message(message_id, failed_task.model_dump(mode="json"))` **before** calling `notify_task_update` — see Section 5.15. | Low |
| 1.4 | Add `_notify_all_non_terminal_tasks_failed(room_id, user_message_id)` to `RoomMessageCenter`. It queries **all** agent messages for `user_message_id` that are in a non-terminal state (including siblings canceled by `_managed_queue` with `notify=False`, and any HITL `AWAITING_INPUT` tasks) and calls `notify_task_update(state=<their DB state>)` for each. | Medium |
| 1.5 | Call `_notify_all_non_terminal_tasks_failed` in `RoomMessageCenter.process_room_user_message` on `QueueResult.FAILED` (line ~259), after the existing `send_processing_status` call. | Low |
| 1.6 | Call `_notify_all_non_terminal_tasks_failed` in `QueueExecutor.resume_from_continuation` on `QueueResult.FAILED` (line ~741), after `send_processing_status`. Also fix `SSEProcessingStatus.ERROR` → `SSEProcessingStatus.FAILED` for consistency with the primary path. | Low |
| 1.7 | Call `_notify_all_non_terminal_tasks_failed` in V2 failure paths: `_handle_v2_run_result` RunStatus.FAILED (~1232) and RunStatus.CANCELED (~1214), `_process_supervisor_v2` exception (~556), `_resume_supervisor_v2` corrupted trajectory (~642), room not found (~764), executor exception (~911). Each location already sends `processing_status: FAILED/CANCELED`; add the safety net call after it. | Medium |
| 1.8 | In `notify_task_update`, change `update_last_notified_state` failure handling: if the MongoDB call raises an exception or returns `False` due to a DB error (not a true duplicate), proceed with the notification instead of silently dropping it. Worst case is a duplicate SSE (harmless) instead of a missed SSE (stuck bubble). | Low |
| 1.9 | Smoke test: start server; trigger a failing agent; confirm task bubble transitions to failed without page refresh. | — |

**Estimated effort:** 2–3 hours.

### Phase 2 — Replace Inline Notification Call Sites

**Goal:** Replace all `notify_task(ctx)` / `fail_task_and_notify` calls with
`transition_task` (persist-only) + `notify_task_update`. Do one call site at a time.

| Step | Action | Risk |
|------|--------|------|
| 2.1 | Fix polling timeout in `ResponseProcessor._process_sync_response` (line ~952): replace `return True, None, None` with `transition_task(failed)` + `notify_task_update(state=TaskState.failed)`. | Low |
| 2.2 | Replace the `tsm.fail_task_and_notify` call in `AgentMessageProcessor.process_single_message` streaming exception handler (line ~127) with `transition_task` + `notify_task_update`. This is the **primary** streaming error path used by both `QueueExecutor` (via delegation) and `SupervisorExecutor`. | Medium |
| 2.3 | Replace the 3 `tsm.fail_task_and_notify` call sites in `QueueExecutor._resolve_agent_for_message` (lines ~365, ~383, ~409) with `transition_task` + `notify_task_update`. Note: these fire before `_setup_task_tracking`, so the message may have no `Task` object — `transition_task`'s `get_task()` guard will silently skip the persist, and `notify_task_update` will also exit early (its retry loop checks `has_task_tracking` and returns if not set). This is correct: no task bubble exists in the frontend for pre-tracking messages, so there is nothing to notify. The `processing_status: failed` event from the queue level handles the room-wide error banner. | Medium |
| 2.4 | Replace the `tsm.fail_task_and_notify` in `QueueExecutor._process_single_message_inline` exception handler (line ~565) — this is the **fallback** path when `AgentMessageProcessor` is not injected. Same pattern as 2.2. | Low |
| 2.5 | Replace `tsm.transition_task(canceled/failed, ctx=ctx...)` in `ResponseProcessor.handle_sync_response` error/cancellation handlers: pre-call cancellation (line ~771, `canceled`), `CancellationError` (line ~806, `canceled`), generic exception (line ~813, `failed`), post-call cancellation (line ~828, `canceled`). All use `ctx=ctx if task_info else None`. Replace with `transition_task` + `notify_task_update`. | Medium |
| 2.6 | Replace `tsm.transition_task(completed/canceled, ctx=ctx...)` in `ResponseProcessor._process_sync_response`: completed happy path (line ~867) and poll-cancelled branch (line ~934). Keep non-terminal inline `notify_task` calls (streaming progress) unchanged. | Medium |
| 2.7 | Replace terminal/interactive `tsm.notify_task(ctx, ...)` / `tsm.transition_task(ctx=ctx...)` in streaming path (`_handle_streaming_error` line ~412, `_finalize_streaming` lines ~613, ~634) with `notify_task_update`. Keep the non-terminal `notify_task` in `_handle_stream_status_update` (line ~544) on the inline path. **Critical:** in `_finalize_streaming` already-terminal branch, persist `streaming_state.full_response_text` to `message_content.message_text` and call `tsm.persist_message(ctx.current_message)` **before** calling `notify_task_update`, so the DB contains the correct content when the notification reads it. | High |
| 2.8 | Replace `tsm.notify_task(ctx, ...)` in `_finalize_polled_task` (line ~992) with `notify_task_update`. Note: `_finalize_polled_task` uses `database_service.update_task_on_message` for DB persistence (full task replacement from polled remote agent), NOT `transition_task`. This DB write is kept as-is; only the notification changes (see Section 5.8). | Low |
| 2.9 | Remove `notify` and `ctx` params from `TaskStateManager.transition_task`. Remove `fail_task_and_notify` method. Keep `notify_task` method (still used for non-terminal streaming notifies). | Low |
| 2.10 | Slim down `ProcessingContext`: remove `created_at`, `step_number`, `total_steps`, `tracked_message_id`; retain `agent_card`. | Low |
| 2.11 | Smoke test: verify end-to-end for streaming, sync, error, agent-resolution failure, polling timeout, cancellation, and supervisor V2 paths. | — |

**Estimated effort:** 3–5 hours.

### Phase 3 (Optional) — Client-Side Watchdog

**Goal:** Add a periodic stale-task check in the frontend as a last-resort recovery
for network failures that prevent any SSE from arriving.

| Step | Action |
|------|--------|
| 3.1 | Move `detectAndMarkStaleTasks` call in `hybro-frontend` from hydration-only to a `useEffect` with a 30-second `setInterval`, scoped to the current room. |
| 3.2 | Trigger `reconcileWithDb(roomId)` when stale tasks are found (not just mark them locally). |

This is decoupled from the backend refactor and can be done independently.

---

## 9. Verification Checklist

After each phase, verify:

- [ ] Server starts without import errors
- [ ] Failing agent (e.g., JSON parse error from openclaw adapter) → task bubble transitions to failed within a few seconds, no page refresh needed
- [ ] Polling timeout (non-push agent, >120s) → task bubble transitions to failed
- [ ] `processing_status: failed` → all in-flight task bubbles transition to failed
- [ ] Successful streaming response → task bubble completes correctly
- [ ] Successful sync response → task bubble completes correctly
- [ ] User cancellation → task bubble transitions to canceled
- [ ] Push notification (webhook) path → task bubble updates correctly
- [ ] Stale task checker → still recovers stuck tasks from DB
- [ ] Agent resolution failure (agent not found / inactive) → task bubble transitions to failed
- [ ] Multi-step workflow failure mid-queue → all sibling task bubbles (canceled by RAII cleanup) also transition to canceled
- [ ] Push-notification resume path failure → task bubble transitions to failed (via `resume_from_continuation` safety net)
- [ ] No duplicate `task_update` SSE events for the same terminal state
- [ ] Webhook idempotency still works (duplicate webhook POST → no double notification)
- [ ] Supervisor V2 path: streaming error in `AgentMessageProcessor` → task bubble transitions to failed (verifies the line-127 call site migration)
- [ ] HITL task in `AWAITING_INPUT` state when overall queue fails → task bubble transitions to failed (safety net correctly catches non-terminal HITL states)
- [ ] `_finalize_polled_task` path: polled remote agent → task bubble completes correctly (verifies `update_task_on_message` + `notify_task_update` path)
- [ ] Resume path sends `processing_status: FAILED` (not `ERROR`) after the fix in step 1.6
- [ ] Supervisor V2 `RunStatus.FAILED` → all agent message task bubbles transition to failed (verifies the `_handle_v2_run_result` safety net wiring from step 1.7)
- [ ] Supervisor V2 executor exception mid-steps → prior step task bubbles transition to failed
- [ ] `_finalize_streaming` already-terminal branch → task bubble shows full streaming content (not stale `message_text`), verifies DB persist before notify
- [ ] `update_last_notified_state` transient failure (simulate with mock) → notification still sent (not silently dropped)
