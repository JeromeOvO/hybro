# Task Cancellation Workflow — Issues & Findings

> **Date:** 2025-02-15
> **Scope:** User-initiated message cancellation (`POST /sse/message/{message_id}/cancel`)

---

## Overview

The system uses a **cooperative cancellation** pattern:

1. Frontend calls `POST /sse/message/{message_id}/cancel`.
2. API layer adds the message ID to an in-memory `cancelled_messages` cache **and** persists it to MongoDB.
3. MongoDB change streams propagate the cancellation to other backend instances.
4. The processing pipeline checks `sse_manager.is_cancelled()` at four checkpoints:
   - Before message parsing (`room_services.py` — `parse_user_message`)
   - Before the agent queue starts (`RoomMessageCenter.py` — `process_room_user_message`)
   - Before each agent message in the queue (`RoomMessageCenter.py` — `_process_agent_message_queue`)
   - During streaming, between each chunk (`ResponseProcessor.py` — `handle_streaming_response`)
5. On detection the pipeline sends `SSEProcessingStatus.CANCELED` to the frontend, clears the in-memory flag, and stops further processing.

---

## Issues

### 1. `COMPLETED` Sent After `CANCELED` in `process_room_user_message`

| | |
|---|---|
| **Severity** | High |
| **Status** | **Fixed** |
| **File** | `modules/RoomMessageCenter.py` |

When `_process_agent_message_queue` returned `QueueResult.CANCELED`, the handler had
*already* sent `SSEProcessingStatus.CANCELED` to the frontend (inside the queue method).
Control then fell through to the shared `COMPLETED`/`CANCELED` branch, sending a
contradictory `COMPLETED` status.

**Fix:** Added an early return for `QueueResult.CANCELED` in `process_room_user_message`,
alongside the existing checks for `FAILED` and `PAUSED`. The `CANCELED` case now returns
immediately, skipping summary generation and the `COMPLETED` status send.

---

### 2. Same Double-Status Bug in Webhook Resume Path

| | |
|---|---|
| **Severity** | High |
| **Status** | **Fixed** |
| **File** | `modules/RoomMessageCenter.py` |

The webhook resume handler (`resume_queue_from_continuation`) had the identical
fall-through pattern — `CANCELED` then `COMPLETED` sent to the frontend.

**Fix:** Added an early return for `QueueResult.CANCELED` in `resume_queue_from_continuation`,
returning `True` immediately to skip summary generation and the `COMPLETED` status send.

---

### 3. Rate-Limiting Returns `QueueResult.CANCELED`, Triggering the Same Bug

| | |
|---|---|
| **Severity** | Medium |
| **Status** | **Fixed** |
| **File** | `modules/RoomMessageCenter.py`, `services/a2a_constants.py` |

When rate limiting was hit, the code sent `SSEProcessingStatus.RATE_LIMITED` and returned
`QueueResult.CANCELED`, which fell through to the `COMPLETED` send. Additionally,
`RATE_LIMITED` was not in `PROCESSING_DONE_STATUSES`, so the room's `processing_message_id`
was never cleared, leaving the room stuck in a "processing" state.

**Fix (two parts):**
1. The Issue 1 fix (early return on `QueueResult.CANCELED`) prevents the `COMPLETED`
   status from being sent after `RATE_LIMITED`.
2. Added `SSEProcessingStatus.RATE_LIMITED` to `PROCESSING_DONE_STATUSES` in
   `services/a2a_constants.py`, so `send_processing_status` correctly clears
   `processing_message_id` when rate limiting stops processing.

---

### 4. No Ownership / Authorization Check on Cancel Endpoint

| | |
|---|---|
| **Severity** | Medium |
| **Status** | **Fixed** |
| **File** | `api/sse.py` |

The endpoint authenticated the user via `get_current_user` but never verified that the
message belonged to a room the user has access to. Any authenticated user who knew a
message ID could cancel another user's active workflow.

**Fix:** Added ownership verification before cancellation proceeds:
1. Look up the message by ID — return **404** if not found.
2. Look up the room the message belongs to — return **404** if not found.
3. Verify the authenticated user is the `room_owner_id` — return **403** if not.

This follows the same `verify_room_ownership` pattern used by all protected endpoints in
`api/room_center.py`.

---

### 5. `MongoDB.clear_message_cancellation()` Is Dead Code

| | |
|---|---|
| **Severity** | Low |
| **Status** | Won't fix |
| **File** | `database/mongodb.py` |

The method exists and is documented with "should be called after workflow completes to
clean up", but it is never called anywhere in the codebase. Only the in-memory
`clear_cancellation()` is used at checkpoints.

MongoDB cancellation records linger until the 3-day TTL expires.

**Decision:** Not a functional issue. The change stream watcher only listens for `insert`
operations, so stale records won't re-trigger on process restart. The `$setOnInsert` upsert
prevents duplicate inserts. The 3-day TTL handles cleanup automatically with negligible
storage cost. The method can be kept for potential future use or removed to reduce
confusion.

---

### 6. Unbounded Growth of In-Memory `cancelled_messages` Set

| | |
|---|---|
| **Severity** | Low |
| **Status** | **Fixed** |
| **File** | `services/sse_services.py` |

The `cancelled_messages` was a plain `set[str]` with no eviction. If a user cancelled a
message that had already finished processing (or was never started), the entry would stay
in memory permanently.

**Fix:** Replaced `set[str]` with `cachetools.TTLCache(maxsize=10_000, ttl=3600)`.
Entries that are never cleared by a processing checkpoint automatically evict after
1 hour. The 1-hour TTL provides ~5x headroom over the worst-case processing duration
(10-minute HTTP client timeout) and is well within the system's 4-hour task expiry
ceiling.

---

### 7. Migration Comment / TTL Mismatch

| | |
|---|---|
| **Severity** | Trivial |
| **Status** | **Fixed** |
| **File** | `database/migration/add_cancelled_messages_indexes.py` |

The file-level docstring says:

> TTL index on cancelled_at (auto-cleanup after 1 hour)

But the actual value is `expireAfterSeconds=3600 * 24 * 3` (3 days), and the inline print
on line 40 correctly says "3 days expiration". The docstring is stale.

---

### 8. Canceled Task/Step Statuses Not Persisted to Database

| | |
|---|---|
| **Severity** | High |
| **Status** | **Fixed** |
| **File** | `modules/RoomMessageCenter.py` |

When the user canceled a multi-step workflow, the backend sent SSE events
(`task_update` with `TaskState.canceled` and `SSEProcessingStatus.CANCELED`) to
the frontend but **never persisted** the `canceled` status to the
`RoomAgentMessage` documents in MongoDB. The individual step messages retained
their original `submitted` or `working` status in the database.

**Symptoms after page refresh:**

1. The "Task was canceled" red bubble disappeared (the frontend's in-memory
   `liveMessages` overlay was lost on refresh).
2. All canceled steps reappeared as blue "Working..." bubbles because the API
   returned messages with non-terminal `task_status` values (`submitted` /
   `working`), and the frontend's staleness threshold (10 minutes) had not yet
   elapsed.
3. No further responses arrived because the backend had already stopped
   processing.

The steps would eventually be cleaned up by the `StaleTaskChecker` background
job after 4 hours, but marked as `failed` (not `canceled`) with an incorrect
timeout error message.

**Root cause:** `_handle_streaming_cancellation` (line 535) called `_notify_task`
(SSE-only) without persisting to the database. Compare with `_fail_task_and_notify`
which correctly persists `TaskState.failed` to MongoDB **before** sending the SSE
notification. The queue-level cancellation checkpoints (pre-queue and pre-step)
also returned immediately without updating any message documents.

**Fix (four parts):**

1. **New helper `_cancel_task_and_persist`** — Persist-only counterpart of
   `_fail_task_and_notify`. Sets `TaskState.canceled` on the message's
   `message_content.message_task.status`, updates `task_updated_at`, and calls
   `_persist_message` to write to MongoDB. No SSE notification is sent — the
   caller handles that separately.

2. **New helper `_cancel_remaining_queue`** — Iterates a deque of
   `RoomAgentMessage` objects (plus an optional `current_message`) and calls
   `_cancel_task_and_persist` on each. Already-terminal messages (`completed`,
   `failed`, `canceled`, `rejected`) are skipped to avoid overwriting a
   legitimate final status.

3. **`_handle_streaming_cancellation`** — Added
   `await self._cancel_task_and_persist(ctx.current_message)` before the
   existing `_notify_task` call. The currently-streaming step's `canceled`
   status is now written to MongoDB before the SSE event is sent.

4. **Three queue-level cancellation sites** — Added
   `await self._cancel_remaining_queue(...)` at each point where the queue
   is abandoned:

   | Site | What it cancels |
   |------|----------------|
   | Pre-queue check in `process_room_user_message` | All queued steps (none started yet) |
   | Pre-step check in `_process_agent_message_queue` | The just-popped step + all remaining |
   | After `ProcessingStatus.CANCELED` from streaming | Remaining steps (current step was already persisted by `_handle_streaming_cancellation`) |

**Result:** After cancellation, every `RoomAgentMessage` document in MongoDB has
a terminal `task_status` (`canceled` for stopped steps, `completed` for any
previously-finished steps). On page refresh the frontend correctly renders red
"Task was canceled" bubbles instead of blue "Working..." spinners.

---

### 9. Failure Path Orphans Remaining Queue Items (Same Class as Issue 8)

| | |
|---|---|
| **Severity** | High |
| **Status** | **Fixed** |
| **File** | `modules/RoomMessageCenter.py` |

When a step **fails** (not canceled), `_process_agent_message_queue` returns
`QueueResult.FAILED` and abandons the remaining queue without updating any of the
unprocessed steps' statuses. They stay at `submitted` in MongoDB indefinitely,
eventually cleaned up by `StaleTaskChecker` after 4+ hours with a misleading
timeout error message.

The Issue 8 fix added `_cancel_remaining_queue` calls for the `CANCELED` path,
but the `FAILED` path has no equivalent cleanup. Affected `return QueueResult.FAILED`
sites:

| Site | Current step persisted? | Remaining queue cleaned up? |
|------|------------------------|----------------------------|
| Agent assignment failed (line ~1066) | Yes (`_fail_task_and_notify`) | **No** |
| Assigned agent not found in DB (line ~1078) | **No** (bare return) | **No** |
| Agent re-assignment failed after inactive (line ~1110) | Yes (`_fail_task_and_notify`) | **No** |
| `_process_single_agent_message` returned FAILED (line ~1163) | Depends on sub-path | **No** |

**Fix:** Added `_cancel_remaining_queue(message_queue)` before each
`return QueueResult.FAILED` in `_process_agent_message_queue`. The helper
(now delegating to `_transition_task`) skips terminal messages via the
terminal-state guard, so reusing it for the failure path is safe.
Additionally, the bare return at line ~1078 (assigned agent not found in DB)
now calls `_fail_task_and_notify` to persist `failed` on the current step
before cleaning up the remaining queue and returning.

---

### 10. `_handle_streaming_error` Does Not Persist Failure to Database

| | |
|---|---|
| **Severity** | Medium |
| **Status** | **Fixed** |
| **File** | `modules/RoomMessageCenter.py` |

`_handle_streaming_error` (line ~553) calls `_notify_task(ctx, TaskState.failed)`
which is SSE-only — the `failed` status is **not written to MongoDB**. This is the
same pattern that Issue 8 fixed for cancellation.

On page refresh, the step appears as `submitted`/`working` instead of `failed`.

Compare:
- `_handle_streaming_cancellation` now correctly calls `_cancel_task_and_persist`
  before `_notify_task` (Issue 8 fix).
- `_handle_streaming_error` still only calls `_notify_task` without persisting.

Note: the `except Exception` handler in `_process_single_agent_message` (line ~1553)
correctly calls `_fail_task_and_notify` (which does persist). But the JSON-RPC error
path through `_handle_streaming_error` does not.

**Fix:** Replaced the bare `_notify_task` call in `_handle_streaming_error` with
`_transition_task(ctx.current_message, TaskState.failed, error=..., ctx=ctx)`.
Because `_transition_task` persists by default, the `failed` status is now written
to MongoDB before the SSE notification is sent.

The same fix was applied to the sync exception handler in
`_handle_sync_response_for_room`, which had the identical SSE-only pattern.

---

### 11. No Cancellation Check in Sync (Non-Streaming) Response Path

| | |
|---|---|
| **Severity** | Medium |
| **Status** | **Fixed** |
| **File** | `modules/RoomMessageCenter.py` |

`_handle_sync_response_for_room` (line ~1675) has **zero** calls to
`is_cancelled()`. If a non-streaming agent takes a long time to respond, the
user's cancel request won't be detected until after the full response is
processed and control returns to the next queue loop iteration. The completed
step would be marked `completed` even though the user wanted to cancel.

Similarly, `_poll_task_until_complete` (lines ~368–479) does not check
`is_cancelled()` during its polling loop, which can block for up to 120 seconds.

**Fix:** Added `is_cancelled()` checks at three points:
1. Before the sync agent call in `_handle_sync_response_for_room`.
2. After the sync agent call returns (before processing the response).
3. Inside `_poll_task_until_complete`'s loop, between polls.

On detection, the task is transitioned to `canceled` via `_transition_task`.
The sync handler no longer sends `SSEProcessingStatus.CANCELED` directly —
instead, `_process_single_agent_message` detects the cancellation and returns
`ProcessingStatus.CANCELED`, which propagates through the queue handler to
avoid double-status sends.

The `user_message_id` parameter was added to both `_handle_sync_response_for_room`
and `_poll_task_until_complete` to enable cancellation lookups against the
correct message key.

---

### 12. Double Cancel Banner — No Deduplication on Frontend

| | |
|---|---|
| **Severity** | Low |
| **Status** | **Fixed** |
| **File** | `hybro-frontend/src/hooks/useRoomWebhook.ts` |

When the backend cancels a workflow, the frontend receives two separate SSE events
that each independently show a banner:

1. `processing_status` handler (line ~606): `banner.info('Processing stopped by user')`
2. `task_update` handler (line ~751): `banner.info('Task was canceled')`

There is no deduplication logic. The user sees two banners with slightly different
wording — matching the original bug report of "2 banners saying task cancelled
successfully."

**Recommended fix:** Remove the banner from the `task_update` handler (keep only
the `processing_status` one, which represents the overall workflow status). Or add
a `cancelBannerShownRef` that gets set on the first banner and checked by the
second.

---

### 13. Cancel Timeout Race Condition with SSE Events

| | |
|---|---|
| **Severity** | Low |
| **Status** | **Fixed** |
| **File** | `hybro-frontend/src/hooks/useRoomWebhook.ts` |

If the backend takes close to 15 seconds to respond, the cancel timeout callback
(line ~971) may already be queued in the JS event loop when the SSE event arrives.
In that case:

1. Timeout fires → shows "Cancellation timed out — the agent may still be running"
2. SSE `processing_status` arrives → shows "Processing stopped by user"
3. SSE `task_update` arrives → shows "Task was canceled"

The user sees up to **three banners**. The `clearTimeout` in the SSE handlers is
a no-op once the timeout callback is already in the event loop.

This matches the original bug report: "initially I saw banner saying task
cancellation time out" followed by "2 banners saying task cancelled successfully."

**Recommended fix:** When the timeout fires, set a ref (`cancelTimedOutRef.current = true`).
In the SSE handlers, check that ref and skip showing banners if the timeout already
fired. This ensures the user sees at most one banner per cancellation. Additionally,
addressing Issue 12 (double banner dedup) would reduce the worst case from 3 to 2
even without this fix.

---

### 14. No Cancellation Propagation to Remote A2A Agents

| | |
|---|---|
| **Severity** | Medium |
| **Status** | **Fixed** |
| **Files** | `modules/ResponseProcessor.py`, `services/a2a_service.py` |

When the user cancels, the backend stops processing at the next checkpoint but **never
sends a `CancelTaskRequest` to the remote A2A agent**. The agent continues executing
(burning compute and tokens) on a task nobody wants.

The infrastructure already exists — `common/client/client.py` has a `cancel_task` method,
and `common/types.py` defines `CancelTaskRequest` / `CancelTaskResponse`. However:

1. No call site invokes `cancel_task` during user-initiated cancellation.
2. The default `InMemoryTaskManager.on_cancel_task` returns `TaskNotCancelableError` — so
   even if the client called it, the server would reject it.

**Fix (three parts):**

1. **`cancel_remote_task` on `A2AService`** — New method that creates an A2A client,
   sends a `tasks/cancel` JSON-RPC request to the agent's URL, and logs the result.
   Errors are caught and logged at DEBUG level (expected for agents that don't support
   cancel). Returns `True` on acknowledgment, `False` on any failure.

2. **`_try_cancel_remote_task` on `ResponseProcessor`** — New helper that extracts
   the remote task ID from the message's stored task object. Skips if no task or
   if the task ID is still a placeholder (`"pending-<uuid>"`). Otherwise calls
   `a2a_service.cancel_remote_task`.
   *(Originally added to `RoomMessageCenter`; moved to `ResponseProcessor` during
   the A-4 decomposition.)*

3. **Integrated into `_handle_streaming_cancellation`** — After persisting the
   canceled status locally, a best-effort cancel is sent to the remote agent
   before the CANCELED SSE event is dispatched.

---

### 15. WorkflowCenter Cancellation Incomplete — No Persist, No SSE

| | |
|---|---|
| **Severity** | Medium |
| **Status** | **Fixed** |
| **File** | `modules/WorkflowCenter.py` |

`WorkflowCenter.run_workflow` checks `is_cancelled()` before each meta task but:

1. **Does not persist** `TaskState.canceled` on remaining meta tasks (same class as Issues
   8 and 9 — orphaned queue items stay at `submitted` in MongoDB).
2. **Does not send** `SSEProcessingStatus.CANCELED` to the frontend — it only returns an
   `OrchestrationResponse` with an error string. It is unclear which caller (if any) sends
   the terminal SSE event in the workflow path.

**Fix:**
1. **New helper `_cancel_remaining_meta_tasks`** — Iterates the remaining (unprocessed)
   meta tasks and sets `TaskState.canceled` on each task's status via
   `task_service.update_task_of_meta_task`. Already-terminal tasks are skipped.
2. **Cancellation block** in `run_workflow` now calls `_cancel_remaining_meta_tasks`
   for all meta tasks from the current index onwards before returning.
3. **`SSEProcessingStatus.CANCELED`** is now sent to the frontend when the workflow
   is cancelled (using the `message_id` from the base task's `extend_info`).

---

### 16. `_last_resolve_failure` Is Shared Mutable State on a Singleton

| | |
|---|---|
| **Severity** | Medium |
| **Status** | **Fixed** |
| **File** | `modules/RoomMessageCenter.py` |

`RoomMessageCenter` is a module-level singleton. The instance variable
`self._last_resolve_failure` is set in `_assign_agent` and read in
`_process_agent_message_queue`. If two asyncio tasks process different rooms
concurrently, one request's failure reason can leak into another request's error
message sent to the frontend.

Although Python's GIL prevents data corruption, asyncio task interleaving between
`_assign_agent` (which sets the value) and the caller (which reads it) is possible
whenever an `await` occurs in between.

**Fix:** Replaced the instance variable pattern with a return value. `_assign_agent`
now returns an `AssignResult` dataclass (defined at module level) with `agent: Agent | None`
and `failure_reason: str | None`. Both call sites in `_process_agent_message_queue` now
read `failure_reason` from the returned `AssignResult` instead of from `self._last_resolve_failure`.
The instance variable has been removed from `__init__`.

---

### 17. Change Stream Watcher Has No Reconnection Resilience

| | |
|---|---|
| **Severity** | Low |
| **Status** | **Fixed** |
| **File** | `services/sse_services.py` |

The change stream watcher (`_watch_cancellations`) is the cross-instance cancellation
propagation mechanism. The current implementation has no documented behavior for:

1. **MongoDB connection drop** — Does the `async for` over the change stream raise an
   exception or silently stop iterating? If the latter, cancellations from other instances
   would silently stop being received.
2. **Automatic reconnection** — There is no retry loop wrapping the change stream. A
   transient network blip would permanently disable cross-instance cancellation for the
   lifetime of the process.
3. **Replica set unavailability** — The startup code in `main.py` catches the exception and
   logs a warning, but does not retry. If the replica set becomes available later, the
   watcher is never started.

**Degraded behavior when watcher is down:** Cancellation only works if the cancel POST
and the processing happen on the **same** backend instance. Cross-instance cancellation
fails silently.

**Recommended fix:** Wrap the change stream iteration in a retry loop with exponential
backoff. Log reconnection attempts at WARNING level. Consider a health-check endpoint
that reports whether the watcher is connected.

**Fix (three parts):**

1. **Exponential backoff** — Replaced the fixed 5-second sleep with exponential backoff
   (1s → 2s → 4s → 8s → 16s → 30s max) with ±25% random jitter. The delay resets to
   1s on successful reconnection. Reconnection attempts are logged at WARNING level
   (previously ERROR).

2. **Resume token** — After each successfully processed change event, the resume token
   (`change["_id"]`) is saved. On reconnection, `resume_after=token` is passed to
   `collection.watch()`, so the watcher resumes from the last processed event instead
   of starting from "now". This prevents missed cancellation events during the
   reconnection gap.

3. **Health-check flag** — Added `_change_stream_connected` boolean flag, updated to
   `True` when the change stream cursor is open and `False` when disconnected or
   reconnecting. Exposed as a `change_stream_connected` property. The `/health`
   endpoint now includes `"change_stream_connected": true/false` in its response,
   allowing operators to detect degraded cross-instance cancellation propagation.

---

### 18. Race Window Between Cancellation Checkpoint and Agent HTTP Call

| | |
|---|---|
| **Severity** | Low |
| **Status** | **Fixed** (sync/poll paths; streaming first-chunk window remains — see below) |
| **Files** | `common/utils/cancellation.py`, `modules/ResponseProcessor.py`, `services/sse_services.py` |

Between the `is_cancelled()` check (e.g., line 508 in the streaming loop, or the
pre-step check in `_process_agent_message_queue`) and the point where the next agent
response is actually received, there is a time window where a newly-arrived cancellation
will not be detected.

- **Streaming path:** Bounded by chunk arrival time — typically sub-second, but can be
  several seconds if the agent is slow to produce the first chunk. The `async for` loop
  over `send_message_streaming` is **not** wrapped with `token.race()`, so cancellation
  between the streaming HTTP connection being opened and the first chunk arriving is not
  instant. This is an **accepted residual risk** — wrapping an `async for` generator
  with `token.race()` is structurally complex, and the window is typically brief.
- **Sync path:** The entire duration of the blocking HTTP call (up to the 10-minute client
  timeout). This is the more serious case and overlaps with Issue 11.

**Fix:** Implemented the `CancellationToken` pattern (A-3). The token wraps an
`asyncio.Event` that is signalled immediately when the cancel endpoint or change-stream
watcher receives a cancellation. The `token.race(coro)` method runs the work coroutine
concurrently with the cancellation event via `asyncio.wait(FIRST_COMPLETED)` — if the
cancel fires first, the work coroutine is cancelled and `CancellationError` is raised.

In the sync path (`ResponseProcessor.handle_sync_response`), the agent HTTP
call is now wrapped: `response = await token.race(agent_coro)`. In the poll loop
(`_poll_task_until_complete`), the sleep is wrapped: `await token.race(asyncio.sleep(delay))`.
Both sites now abort instantly on cancellation instead of waiting for the next polling
checkpoint.

**Not covered:** The streaming path's first-chunk window remains. To fully close this,
the `async for` in `handle_streaming_response` would need to be converted to manual
`__anext__()` calls each wrapped in `token.race()`, which adds significant complexity
for a typically sub-second window.

---

### 19. In-Flight HTTP Connections Not Aborted on Cancellation

| | |
|---|---|
| **Severity** | Low |
| **Status** | **Fixed** |
| **Files** | `common/utils/cancellation.py`, `modules/ResponseProcessor.py` |

When cancellation is detected during streaming, the code breaks out of the `async for`
loop but does not explicitly close or abort the underlying HTTP connection. The connection
may linger in the connection pool until the server closes it or the client-side timeout
(10 minutes) fires, tying up resources.

For the sync path, the blocked `await` on the HTTP call cannot be interrupted at all
without the `CancellationToken` pattern (A-3).

**Fix:** The `CancellationToken.race()` method (A-3) now wraps blocking HTTP calls. When
cancellation fires, `asyncio.wait(FIRST_COMPLETED)` resolves the cancel event first and
the work task is explicitly cancelled via `task.cancel()`, which interrupts the HTTP call
and releases the connection. For the sync path, `token.race(agent_coro)` in
`ResponseProcessor._handle_sync_response_for_room` cancels the pending coroutine
immediately. For the poll path, `token.race(asyncio.sleep(delay))` in
`_poll_task_until_complete` aborts the sleep.

---

### 20. `parse_user_message` Sends `CANCELED` Independently — Potential Double-Send

| | |
|---|---|
| **Severity** | Low |
| **Status** | **Fixed** |
| **Files** | `services/room_services.py`, `modules/RoomMessageCenter.py` |

In `room_services.parse_user_message` (line ~1011), when `is_cancelled()` returns true,
the method sends `SSEProcessingStatus.CANCELED` **and** clears the cancellation flag on
its own, then returns `False` to the caller.

The caller (`process_room_user_message` or its upstream in `api/room_center.py`) may not
know that `CANCELED` was already sent. If the caller has its own cancellation handling
that also sends a terminal status, the frontend could receive a double status — the same
class of bug as Issues 1–2.

Currently this does not appear to cause a visible problem because the callers check the
return value and stop processing. But it is fragile — any future caller that adds
post-failure handling could trigger the bug.

**Recommended fix:** Have `parse_user_message` return a structured result (e.g.,
`ParseResult` with a `canceled` flag) instead of sending SSE events directly. Let the
top-level orchestrator be the single place that sends terminal processing statuses. This
aligns with the "single responsibility for terminal status" principle from A-5.

---

### 21. Double `task_update` Notification in `_finalize_streaming`

| | |
|---|---|
| **Severity** | Medium |
| **Status** | **Fixed** |
| **File** | `modules/ResponseProcessor.py` |

When a streaming session completes normally (the common case), the frontend receives
**two identical** `task_update` SSE events for `TaskState.completed`.

The bug is in `_finalize_streaming` (line ~594). The method evaluates
`already_terminal` once (line ~607) **before** calling `transition_task`. The flow:

1. `already_terminal = False` (task status is `working` during streaming).
2. `if task and not already_terminal:` → **True** → calls
   `transition_task(msg, TaskState.completed, ctx=ctx, content=...)`.
   Since `notify=True` by default and `ctx` is provided, `transition_task`
   calls `notify_task(ctx, TaskState.completed, ...)` internally → **first
   `task_update` SSE sent**.
3. `if already_terminal:` → still **False** (the variable was captured before
   the transition) → falls to the `else` clause.
4. `else:` → calls `notify_task(ctx, TaskState.completed, ...)` →
   **second identical `task_update` SSE sent**.

The A-5 deduplication layer (`_terminal_status_sent`) only covers
`send_processing_status`, **not** `send_task_update`, so this double-send
reaches the frontend.

**Impact:** The frontend receives two `task_update` events with
`status=completed` for the same message. Depending on the frontend handler,
this may cause:
- A brief visual flicker as the message UI re-renders.
- Duplicate state transitions in the `liveMessages` overlay.
- No user-visible issue if the handler is idempotent for `completed`.

**Recommended fix:** Remove the `else` clause (lines ~646–651) that calls
`notify_task(ctx, TaskState.completed, ...)`. The notification is already
handled by `transition_task` in the non-terminal path. The `else` clause
was a holdover from before A-1 when `transition_task` didn't exist and
`_finalize_streaming` was responsible for both persisting and notifying.

Alternatively, pass `notify=False` to `transition_task` in `_finalize_streaming`
and let the explicit `notify_task` call on line ~647 be the single notification
site — but this inverts the A-1 "safe default" principle.

---

### 22. `_handle_stream_status_update` Bypasses `transition_task` Terminal Guard

| | |
|---|---|
| **Severity** | Low |
| **Status** | **Fixed** |
| **File** | `modules/ResponseProcessor.py` |

`_handle_stream_status_update` (line ~484) sets the task state directly:

```python
task.status.state = state
await self.tsm.persist_message(ctx.current_message)
```

This bypasses the `transition_task` terminal-state guard introduced in A-1.
If an agent erroneously sends a second terminal status update (e.g., two
`completed` events, or `completed` followed by `failed`), the direct
assignment would overwrite the first terminal state. The `transition_task`
guard was specifically designed to make this harmless — but it is not used
here.

Additionally, the direct assignment skips the `task_updated_at` timestamp
update that `transition_task` provides.

**Impact:** Low in practice — well-behaved agents do not send duplicate
terminal statuses. But this is the exact pattern that A-1 was designed to
eliminate, and leaving it creates an inconsistency where *some* status
transitions are guarded and *some* are not.

**Recommended fix:** For terminal states, delegate to `transition_task`
instead of direct assignment. Non-terminal states (e.g., `working` with a
progress message) can continue to use direct assignment since the terminal
guard doesn't apply to them. Example:

```python
if is_terminal_state(state):
    await self.tsm.transition_task(
        ctx.current_message, state, persist=True, notify=False
    )
else:
    task.status.state = state
    await self.tsm.persist_message(ctx.current_message)
```

The `notify=False` avoids interference with the existing notification logic
further down in the method.

---

## Summary Table

| # | Issue | Severity | Type | Status |
|---|-------|----------|------|--------|
| 1 | `COMPLETED` sent after `CANCELED` in main processing path | **High** | Bug | **Fixed** |
| 2 | Same double-status in webhook resume path | **High** | Bug | **Fixed** |
| 3 | Rate-limiting reuses `QueueResult.CANCELED`, triggering Issue 1 | **Medium** | Bug | **Fixed** |
| 4 | No ownership check on cancel endpoint | **Medium** | Security | **Fixed** |
| 5 | `clear_message_cancellation()` never called (dead code) | **Low** | Cleanup | Won't fix |
| 6 | Unbounded `cancelled_messages` in-memory set | **Low** | Memory leak | **Fixed** |
| 7 | Migration docstring says "1 hour" but TTL is 3 days | **Trivial** | Documentation | **Fixed** |
| 8 | Canceled task/step statuses not persisted to DB | **High** | Bug | **Fixed** |
| 9 | Failure path orphans remaining queue items | **High** | Bug | **Fixed** |
| 10 | `_handle_streaming_error` doesn't persist failure to DB | **Medium** | Bug | **Fixed** |
| 11 | No cancellation check in sync (non-streaming) path | **Medium** | Bug | **Fixed** |
| 12 | Double cancel banner — no deduplication (frontend) | **Low** | UX | **Fixed** |
| 13 | Cancel timeout race condition with SSE events (frontend) | **Low** | UX | **Fixed** |
| 14 | No cancellation propagation to remote A2A agents | **Medium** | Missing feature | **Fixed** |
| 15 | WorkflowCenter cancellation incomplete — no persist, no SSE | **Medium** | Bug | **Fixed** |
| 16 | `_last_resolve_failure` singleton shared state — concurrency bug | **Medium** | Concurrency | **Fixed** |
| 17 | Change stream watcher has no reconnection resilience | **Low** | Robustness | **Fixed** |
| 18 | Race window between checkpoint and agent HTTP call | **Low** | Design gap | **Fixed** (sync/poll only) |
| 19 | In-flight HTTP connections not aborted on cancellation | **Low** | Resource leak | **Fixed** |
| 20 | `parse_user_message` sends `CANCELED` independently — potential double-send | **Low** | Bug | **Fixed** |
| 21 | Double `task_update` notification in `_finalize_streaming` | **Medium** | Bug | **Fixed** |
| 22 | `_handle_stream_status_update` bypasses `transition_task` guard | **Low** | Design gap | **Fixed** |

---

## Architectural Improvements

> **Date:** 2026-02-15
> **Scope:** Backend refactoring to prevent recurring bug classes and improve cancellation robustness

### A-1. Unified State Transition Method

| | |
|---|---|
| **Impact** | Prevents Issues 8, 9, 10 class of bugs entirely |
| **Effort** | Medium |
| **Risk** | Low — consolidates existing logic without changing control flow |
| **Status** | **Implemented** (Phase 1) |

**Problem:** Task state transitions are scattered across four inconsistent patterns:

| Pattern | Persists? | Notifies SSE? | Used by | Status |
|---------|-----------|---------------|---------|--------|
| `_fail_task_and_notify` | Yes | Yes | Failure paths | Kept as thin wrapper (delegates persist to `_transition_task`) |
| `_cancel_task_and_persist` | Yes | No | Cancellation | **Removed** — replaced by `_transition_task(notify=False)` |
| `_notify_task` | **No** | Yes | Streaming error/cancel (the buggy ones) | Kept for non-terminal notifications; **buggy call sites migrated** to `_transition_task` |
| Direct `task.status = ...` | Varies | No | Status updates during streaming | Some migrated to `_transition_task` |

Every "forgot to persist" or "forgot to notify" bug in this document exists because the
developer had to remember which combination of persist + notify to call. The pattern
invites mistakes.

**Solution:** Replace all four patterns with a single entry point:

```python
async def _transition_task(
    self,
    message: RoomAgentMessage,
    new_state: TaskState,
    *,
    ctx: ProcessingContext | None = None,
    error: str | None = None,
    content: str | None = None,
    notify: bool = True,
    persist: bool = True,
) -> None:
    """Single entry point for all task state transitions.

    Always persists by default. Always notifies by default.
    Callers opt out explicitly (e.g., notify=False for batch queue cleanup).
    """
    task = _get_task(message)
    if not task:
        return

    # Guard: never overwrite a terminal state
    if task.status and is_terminal_state(task.status.state):
        logger.warning(
            "Attempted to transition already-terminal task %s from %s to %s",
            message.message_id,
            task.status.state,
            new_state,
        )
        return

    # Update state
    task.status = TaskStatus(state=new_state)
    if error:
        task.status.message = Message(
            message_id=uuid4().hex,
            role=Role.agent,
            parts=[TextPart(text=error)],
        )
    message.task_updated_at = utcnow()

    if persist:
        await self._persist_message(message)

    if notify and ctx:
        await self._notify_task(
            ctx, new_state, content=content, error=error
        )
```

**Migration path:**
1. Add `_transition_task` alongside existing helpers.
2. ~~Replace `_fail_task_and_notify` call sites with `_transition_task(msg, TaskState.failed, ...)`.~~
   Kept `_fail_task_and_notify` as a thin wrapper: delegates persistence to
   `_transition_task(notify=False)` and retains its own richer notification call
   (with `agent_id`, `step_number`, `task_content` display params not available
   through `ProcessingContext`).
3. Replace `_cancel_task_and_persist` call sites with `_transition_task(msg, TaskState.canceled, notify=False, ...)`.
4. Replace bare `_notify_task` calls (the buggy pattern) with `_transition_task(msg, state, ...)`.
5. ~~Remove the old helpers once all call sites are migrated.~~ Removed
   `_cancel_task_and_persist` (no remaining callers). Kept `_notify_task` (still
   used for non-terminal status notifications and internally by `_transition_task`).
   Kept `_fail_task_and_notify` as convenience wrapper (see step 2).

**Key property:** Because `_transition_task` persists by default, a developer must
*actively opt out* of persistence (`persist=False`). This inverts the current failure mode
— the "safe" default is now correct, and bugs require deliberate action.

---

### A-2. Context Manager for Queue Cleanup (RAII Pattern)

| | |
|---|---|
| **Impact** | Prevents Issue 9 class of bugs (orphaned queue items on any exit path) |
| **Effort** | Small |
| **Risk** | Low — wraps existing loop without changing logic |
| **Status** | **Implemented** (Phase 1b) |

**Problem:** `_process_agent_message_queue` has multiple `return QueueResult.FAILED`
sites. Each site must manually add cleanup code for remaining queue items. Issue 9 exists
because some sites forgot. Any future `return` statement will also need to remember.

**Solution:** Wrap the queue in a context manager that guarantees cleanup:

```python
@asynccontextmanager
async def _managed_queue(self, message_queue: deque):
    """Context manager that cancels remaining items on non-COMPLETED exit.

    Usage:
        async with self._managed_queue(queue) as q:
            # process items from q...
            if something_failed:
                return QueueResult.FAILED  # cleanup runs automatically
        # cleanup also runs on unhandled exceptions
    """
    try:
        yield message_queue
    finally:
        # Cancel any items still in the queue (skips already-terminal)
        if len(message_queue) > 0:
            await self._cancel_remaining_queue(message_queue)
```

The queue loop becomes:

```python
async with self._managed_queue(message_queue) as queue:
    while len(queue) > 0:
        current = queue.popleft()
        # ... process current ...
        if result.status == ProcessingStatus.FAILED:
            return QueueResult.FAILED  # remaining items auto-canceled
```

**Key property:** Cleanup is guaranteed by the language runtime, not by developer memory.
Adding a new failure path in the future cannot orphan queue items.

---

### A-3. CancellationToken Pattern

| | |
|---|---|
| **Impact** | Addresses Issues 11, 18, 19 — eliminates checkpoint gaps |
| **Effort** | Large |
| **Risk** | Medium — requires threading the token through the entire call chain |
| **Status** | **Implemented** |

**Problem:** Cancellation is detected at discrete checkpoints. Between any two checkpoints,
the system is blind to cancellation. For the sync path this window can be up to 10 minutes
(the HTTP client timeout). Even for streaming, the first chunk may take several seconds.

**Solution:** Replace `is_cancelled(message_id)` checks with a `CancellationToken` that
is threaded through the processing pipeline:

```python
@dataclass
class CancellationToken:
    """Cooperative cancellation with instant notification via asyncio.Event."""

    message_id: str
    _event: asyncio.Event = field(default_factory=asyncio.Event)

    def cancel(self) -> None:
        """Signal cancellation (called by the cancel endpoint / change stream)."""
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        """Raise CancellationError if cancelled. Use at checkpoints."""
        if self._event.is_set():
            raise CancellationError(self.message_id)

    async def race(self, coro: Awaitable[T]) -> T:
        """Run *coro*, but abort immediately if cancelled.

        This eliminates the race window between checkpoint and HTTP call.
        """
        cancel_task = asyncio.create_task(self._event.wait())
        work_task = asyncio.create_task(coro)
        done, pending = await asyncio.wait(
            {cancel_task, work_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        if cancel_task in done:
            raise CancellationError(self.message_id)
        return work_task.result()
```

**Usage in sync path (eliminates Issue 11):**

```python
# Instead of:
response = await self.a2a_service.send_message_sync(agent_card, message)

# Do:
response = await token.race(
    self.a2a_service.send_message_sync(agent_card, message)
)
```

**Usage in poll loop (eliminates Issue 18):**

```python
# Instead of:
await asyncio.sleep(delay)

# Do:
try:
    await token.race(asyncio.sleep(delay))
except CancellationError:
    return None  # poll aborted
```

**Migration path:** ~~This is a larger refactoring because the token must be created by the
cancel endpoint, stored in the SSE manager (keyed by message_id), and passed through
`ProcessingContext` to every method in the chain. Implement after A-1 and A-2 stabilize.~~

**Implementation (four parts):**

1. **`CancellationToken` class** (`common/utils/cancellation.py`) — Dataclass wrapping
   an `asyncio.Event`. Provides `cancel()` (signal), `is_cancelled` (non-raising check),
   `check()` (raises `CancellationError`), and `race(coro)` (runs a coroutine
   concurrently with the cancellation event via `asyncio.wait(FIRST_COMPLETED)` — if
   cancellation fires first, the coroutine is cancelled and `CancellationError` raised).

2. **SSEManager token storage** (`services/sse_services.py`) — `create_token(message_id)`
   creates and stores a token in a TTL-cached dict. If a cancellation was already
   requested (present in `cancelled_messages`), the token is pre-signalled so the very
   first `token.check()` raises immediately. `cancel_message()` now also signals the
   token if one exists, unblocking any `token.race()` instantly. The change-stream
   watcher signals tokens on cross-instance cancellation propagation.

3. **Token threading** — Tokens are created at pipeline entry points
   (`room_services.send_message_to_room`, `RoomMessageCenter.process_room_user_message`,
   `RoomMessageCenter.resume_queue_from_continuation`, `WorkflowCenter.run_workflow`)
   and threaded through `ProcessingContext` to all sub-handlers.

4. **`token.race()` at blocking call sites** — The sync agent HTTP call in
   `ResponseProcessor._handle_sync_response_for_room` and the poll sleep in
   `ResponseProcessor._poll_task_until_complete` are wrapped with `token.race()`,
   eliminating the TOCTOU gap entirely. Legacy `is_cancelled()` checks are retained
   as a fallback for paths where a token may not yet be available.

---

### A-4. Decompose RoomMessageCenter

| | |
|---|---|
| **Impact** | Long-term maintainability, testability, and auditability |
| **Effort** | Large |
| **Risk** | Medium-high — wide-reaching structural change |
| **Status** | **Completed** (TaskStateManager + ResponseProcessor + QueueExecutor + AgentDispatcher extracted) |

**Problem:** `RoomMessageCenter.py` is ~~1,941~~ ~~460~~ 305 lines (down from 2,050 after A-1/A-2)
handling ~~8+~~ ~~3~~ 2 concerns: top-level orchestration and webhook
continuation. Queue management, streaming/sync response handling, task state transitions,
agent assignment, and cancellation/error handling have been extracted.

**Proposed decomposition:**

| New Module | Responsibility | Approximate Lines | Status |
|------------|---------------|-------------------|--------|
| `TaskStateManager` | All state transitions (persist + notify) — A-1 | ~242 | **Extracted** |
| `ResponseProcessor` | Streaming/sync response handling, polling, cancellation/error handling, artifact accumulation, finalization | ~1,004 | **Extracted** |
| `QueueExecutor` | Queue loop, RAII cleanup, continuation save/resume, per-item dispatch, queue chaining | ~745 | **Extracted** |
| `AgentDispatcher` | Agent assignment, resolution, allowed-ID expansion | ~200 | **Extracted** |
| `RoomMessageCenter` | Top-level orchestrator — thin wrapper that composes the above | ~305 | **Complete** (down from 2,050) |

**Migration strategy:**
1. ~~Extract `TaskStateManager` first (A-1) — smallest, most self-contained.~~ **Done** — `modules/TaskStateManager.py`
2. ~~Extract `ResponseProcessor` — pure data processing, few dependencies.~~ **Done** — `modules/ResponseProcessor.py`
   (1,004 lines: streaming/sync response handling, polling, cancellation/error handling
   during agent communication, artifact accumulation, and stream finalization).
   Methods renamed from private (`_handle_streaming_response_for_room`) to public
   (`handle_streaming_response`) at the API boundary.
3. ~~Extract `QueueExecutor` with RAII (A-2).~~ **Done** — `modules/QueueExecutor.py`
   (745 lines: queue loop with RAII cleanup, continuation save/resume, per-item dispatch
   to ResponseProcessor, queue chaining, agent resolution/rate-limit helpers).
   Agent assignment injected as a callable (`AssignAgentFn`) so RoomMessageCenter retains
   ownership of the resolution logic.  `resume_queue_from_continuation` in RMC is now a
   thin wrapper that delegates to `QueueExecutor.resume_from_continuation` and handles
   the post-completion coordinator/SSE logic.
4. ~~Extract `AgentDispatcher` last — most entangled with other services.~~
   **Done** — `modules/AgentDispatcher.py` now owns agent assignment, resolution,
   allowed-ID expansion (including group membership), and user-input extraction
   (~200 lines).  `RoomMessageCenter` no longer contains any agent-resolution logic;
   it creates an `AgentDispatcher` and passes it to `QueueExecutor`.
   The callback-based `AssignAgentFn` pattern in `QueueExecutor` has been replaced
   with direct injection of `AgentDispatcher` — the executor calls
   `self.agent_dispatcher.assign_agent_for_queue(message)` directly.
5. ~~`RoomMessageCenter` becomes a facade that delegates to the extracted modules.~~
   **Done** — `RoomMessageCenter` is now ~305 lines: request validation,
   cancellation-token creation, queue-result dispatch, coordinator/SSE
   completion logic, webhook resume delegation, and memory-stats logging.

**Key property:** Each module has a single concern, making it straightforward to verify
that every exit path handles cleanup correctly. Code review can focus on one module at a
time rather than scanning 1,941 lines.

**Recommendation:** Defer this until after A-1 and A-2 are complete and stable. The
immediate bug fixes (Issues 9, 10, 11) should not wait for a full decomposition.

---

### A-5. Idempotent Terminal Status Sends

| | |
|---|---|
| **Impact** | Prevents Issues 1, 2, 12, 13 class of bugs (double/triple sends) |
| **Effort** | Small |
| **Risk** | Low — additive defensive layer |
| **Status** | **Implemented** (Phase 3) |

**Problem:** Multiple issues (1, 2, 12, 13) stem from the same root: sending a terminal
status is not idempotent. Once `CANCELED` is sent, sending `COMPLETED` afterward should
be a no-op, but the current code has no such guard.

**Solution:** Add a deduplication layer in `send_processing_status`:

```python
async def send_processing_status(self, room_id, status, message_id, **kwargs):
    if status in PROCESSING_DONE_STATUSES:
        key = f"{room_id}:{message_id}"
        if key in self._terminal_status_sent:
            logger.warning(
                "Suppressing duplicate terminal status %s for %s (already sent %s)",
                status, key, self._terminal_status_sent[key],
            )
            return
        self._terminal_status_sent[key] = status
    # ... actual send logic ...
```

Use a TTL cache (`TTLCache(maxsize=10_000, ttl=300)`) for `_terminal_status_sent` so
entries auto-evict after 5 minutes (well beyond any reasonable processing duration).

**Key property:** Even if higher-level code has a control-flow bug that sends two terminal
statuses, the frontend only ever sees one. This is a safety net, not a replacement for
fixing the control flow — but it makes the entire class of double-send bugs harmless.

---

## Implementation Plan

> **Date:** 2026-02-15
> **Goal:** Fix all open bugs and apply targeted architectural improvements in a safe,
> incremental order

### Guiding Principles

1. **A-1 is both refactoring and bug fix.** Issues 9 and 10 are symptoms of the scattered
   persist/notify pattern. Fixing them individually adds more scattered code. Introducing
   `_transition_task` fixes them *and* prevents the pattern from recurring.
2. **Refactor before patching** — but only the targeted refactoring (A-1, A-2, A-5).
   Larger structural changes (A-3, A-4) should wait until the bugs are resolved.
3. **Each phase is independently shippable.** If any phase is delayed, earlier phases still
   provide value.

---

### Phase 1 — Unified State Transitions (A-1 + Issues 9, 10)

| | |
|---|---|
| **Effort** | 1–2 days |
| **Risk** | Low |
| **Prerequisite** | None |
| **Status** | **Completed** |

**Steps:**

1. **Add `_transition_task` method** to `RoomMessageCenter` alongside existing helpers.
   Include the terminal-state guard that prevents overwriting completed/failed/canceled.
   **Done.**

2. **Migrate `_fail_task_and_notify` call sites** to use
   `_transition_task(msg, TaskState.failed, error=..., ctx=...)`.
   Verify each call site preserves its existing persist + notify behavior.
   **Done** — kept `_fail_task_and_notify` as a thin wrapper that delegates
   persistence to `_transition_task(notify=False)` and retains its own richer
   notification call (required for queue-level failures where no
   `ProcessingContext` is available).

3. **Migrate `_cancel_task_and_persist` call sites** to use
   `_transition_task(msg, TaskState.canceled, notify=False)`.
   **Done** — `_cancel_remaining_queue` now delegates to `_transition_task`.
   `_handle_streaming_cancellation` uses `_transition_task(ctx=ctx)` for
   combined persist+notify.

4. **Fix Issue 10** — Replace the bare `_notify_task` call in `_handle_streaming_error`
   with `_transition_task(ctx.current_message, TaskState.failed, error=..., ctx=ctx)`.
   This now persists automatically.
   **Done** — also fixed the same pattern in the sync exception handler in
   `_handle_sync_response_for_room`.

5. **Fix Issue 9** — At each `return QueueResult.FAILED` site in
   `_process_agent_message_queue`, add explicit `_cancel_remaining_queue` calls.
   Also fix the bare return at line ~1078 (assigned agent not found in DB) to
   transition the current step to `failed` before returning.
   **Done** — all four `return QueueResult.FAILED` sites now clean up the
   remaining queue. The bare return now calls `_fail_task_and_notify` +
   `_cancel_remaining_queue`.

6. **Remove old helpers** (`_fail_task_and_notify`, `_cancel_task_and_persist`,
   `_notify_task`) once all call sites are migrated. If any call site has a reason to
   keep a specific helper, document why.
   **Done** — removed `_cancel_task_and_persist` (no remaining callers). Kept
   `_notify_task` (used for non-terminal status notifications and internally by
   `_transition_task`). Kept `_fail_task_and_notify` as thin wrapper (see step 2).

7. **Test:** Verify through manual testing that:
   - Canceling a multi-step workflow persists `canceled` on all steps (Issue 8 regression).
   - A step failure persists `failed` on the current step and `canceled` on remaining steps.
   - Page refresh after failure/cancel shows correct status bubbles.

---

### Phase 1b — RAII Queue Cleanup (A-2)

| | |
|---|---|
| **Effort** | Half day |
| **Risk** | Low |
| **Prerequisite** | Phase 1 (uses `_transition_task` internally) |
| **Status** | **Completed** |

**Steps:**

1. **Add `_managed_queue` context manager** that calls `_cancel_remaining_queue` in its
   `finally` block. **Done.**

2. **Wrap the `while` loop** in `_process_agent_message_queue` with
   `async with self._managed_queue(message_queue)`. **Done.**

3. **Remove explicit `_cancel_remaining_queue` calls** at each failure/cancel return site —
   the context manager now handles them. **Done** — removed all 6 explicit calls
   inside `_process_agent_message_queue`. The pre-step cancellation check now
   directly calls `_transition_task` on the popped `current_message` (since it's
   no longer in the deque), and the context manager handles the rest.

4. **Test:** Verify that remaining queue items are canceled on every exit path (cancel,
   fail, exception).

**Post-review fixes (two edge cases found during review):**

5. **PAUSED path** — `return QueueResult.PAUSED` triggered the context manager's
   `finally` block, which would cancel the remaining items that had just been
   serialized by `_save_queue_continuation` for later webhook resumption.
   **Fix:** Added `message_queue.clear()` after serialization so the context
   manager sees an empty deque and does nothing.

6. **Rate-limit path** — `current_message` was already popped from the deque but
   never transitioned to a terminal state, so it would orphan at `submitted` in
   MongoDB. **Fix:** Added `_transition_task(current_message, TaskState.canceled)`
   before `return QueueResult.CANCELED`.

---

### Phase 2 — Additive Bug Fixes (Issues 11, 14, 15, 16)

| | |
|---|---|
| **Effort** | 1–2 days |
| **Risk** | Low — additive changes, no structural modifications |
| **Prerequisite** | Phase 1 (uses `_transition_task` for state changes) |
| **Status** | **Completed** |

**Steps:**

1. **Issue 11 — Sync/poll cancellation checks:**
   - Add `is_cancelled()` check before the sync agent call in
     `_handle_sync_response_for_room`.
   - Add `is_cancelled()` check after the sync call returns (before processing response).
   - Add `is_cancelled()` check inside `_poll_task_until_complete`'s loop, between polls.
   - On detection, call `_transition_task(msg, TaskState.canceled)` and return the
     appropriate canceled result.
   **Done** — Added `user_message_id` parameter to both `_handle_sync_response_for_room`
   and `_poll_task_until_complete`. Cancellation detected in the sync handler transitions
   the task to `canceled` and returns `False` (failure). The caller
   (`_process_single_agent_message`) now distinguishes cancellation from failure by
   checking the task state, returning `ProcessingStatus.CANCELED` to avoid double-status
   sends at the queue level.

2. **Issue 14 — Remote agent cancel propagation:**
   - In `_handle_streaming_cancellation` and queue-level cancellation sites, add a
     best-effort `a2a_client.cancel_task(task_id)` call wrapped in try/except.
   - Log success at INFO, failure at DEBUG (expected for agents that don't support cancel).
   **Done** — Added `cancel_remote_task` to `A2AService` and
   `_try_cancel_remote_task` to `RoomMessageCenter`. Integrated into
   `_handle_streaming_cancellation`. Skips placeholder task IDs (`"pending-*"`).

3. **Issue 15 — WorkflowCenter cancellation:**
   - Add `_cancel_remaining_queue` equivalent for meta tasks in `WorkflowCenter.run_workflow`.
   - Ensure `SSEProcessingStatus.CANCELED` is sent to the frontend when the workflow is
     cancelled.
   **Done** — Added `_cancel_remaining_meta_tasks` helper. The cancellation block in
   `run_workflow` now persists canceled status on remaining meta tasks and sends
   `SSEProcessingStatus.CANCELED` to the frontend.

4. **Issue 16 — Eliminate `_last_resolve_failure` singleton state:**
   - Change `_assign_agent` to return an `AssignResult` dataclass:
     `@dataclass class AssignResult: agent: Agent | None; failure_reason: str | None`
   - Update all call sites to use the returned `failure_reason` instead of reading
     `self._last_resolve_failure`.
   - Remove `self._last_resolve_failure` from the class.
   **Done** — Added `AssignResult` at module level. Both call sites in
   `_process_agent_message_queue` now use the returned dataclass.

5. **Test:** Verify each fix in isolation — cancel during sync agent call, cancel
   propagation to a test agent, workflow cancellation persists correctly.

---

### Phase 3 — Defensive Layers (A-5, Issues 7, 12, 13, 20)

| | |
|---|---|
| **Effort** | 1 day |
| **Risk** | Low — additive safety nets |
| **Prerequisite** | None (can run in parallel with Phase 2) |
| **Status** | **Completed** |

**Steps:**

1. **A-5 — Idempotent terminal status sends:**
   - Add `_terminal_status_sent` TTL cache to `SSEManager`.
   - Add deduplication check at the top of `send_processing_status` for terminal statuses.
   - Log suppressed duplicates at WARNING level.
   **Done** — Added `TTLCache(maxsize=10_000, ttl=300)` to `SSEManager.__init__`. The
   deduplication check runs before any persistence or broadcast, so a suppressed duplicate
   is completely invisible to both the database and connected clients.

2. **Issue 7 — Fix migration docstring** to say "3 days" instead of "1 hour".
   **Done** — Updated both the file-level docstring and the inline comment on line 34–35.

3. **Issue 12 — Frontend double banner dedup:**
   - Remove the banner from the `task_update` handler in `useRoomWebhook.ts`.
   - Keep only the `processing_status` banner (workflow-level).
   **Done** — Removed `banner.info('Task was canceled')` from the `task_update` terminal
   handler. The `processing_status` handler's "Processing stopped by user" is the single
   source of cancel feedback.

4. **Issue 13 — Frontend timeout race:**
   - Add `cancelTimedOutRef` — set when timeout fires, checked by SSE handlers.
   - SSE handlers skip banners if timeout already fired.
   **Done** — Added `cancelTimedOutRef` ref. When the 15-second timeout fires it sets the
   ref to `true`. Both the `processing_status` and `task_update` SSE handlers check this
   ref and skip banners if the timeout already fired. The ref is reset when starting a new
   cancellation (`cancelProcessing`) or a new message send (`sendUserMessage`).

5. **Issue 20 — `parse_user_message` terminal status responsibility:**
   - Refactor `parse_user_message` to return a `ParseResult` with a `canceled: bool` flag
     instead of sending SSE events directly.
   - Move the `SSEProcessingStatus.CANCELED` send to the caller.
   **Done** — Added `ParseResult` dataclass with `success` and `canceled` fields.
   `parse_user_message` no longer calls `send_processing_status`; instead, `send_message_to_room`
   inspects `ParseResult.canceled` and sends either `CANCELED` or `FAILED` as appropriate.

---

### Phase 4 — Long-Term Architecture (A-3, A-4, Issues 17, 18, 19)

| | |
|---|---|
| **Effort** | 1–2 weeks |
| **Risk** | Medium — structural changes |
| **Prerequisite** | Phases 1–3 stable |
| **Status** | **Completed** |

~~This phase is optional and should be scheduled when there is breathing room. It addresses
robustness and maintainability, not user-facing bugs.~~

All Issues (17, 18, 19) in this phase are now resolved. The A-4
decomposition is complete (all four modules extracted from `RoomMessageCenter`).

**Steps:**

1. **A-3 — CancellationToken:**
   - ~~Define `CancellationToken` class with `cancel()`, `check()`, `race()`.~~
   - ~~Store tokens in `SSEManager` keyed by message_id.~~
   - ~~Thread the token through `ProcessingContext`.~~
   - ~~Replace `is_cancelled()` checks with `token.check()`.~~
   - ~~Wrap blocking HTTP calls with `token.race()`.~~
   - ~~This subsumes Issues 18 and 19.~~
   **Done** — `common/utils/cancellation.py` defines `CancellationToken` and
   `CancellationError`. `SSEManager` creates/stores/signals tokens. Token threaded
   through `ProcessingContext`. `token.race()` wraps sync agent calls and poll sleeps.
   Issues 18 and 19 resolved.

2. **Issue 17 — Change stream reconnection:**
   - ~~Wrap the change stream iteration in a retry loop with exponential backoff.~~
   - ~~Store and use a resume token so reconnections don't miss events.~~
   - ~~Add a health-check flag that the `/health` endpoint can report.~~
   **Done** — `_watch_cancellations` now uses exponential backoff (1s–30s) with
   ±25% jitter, saves and restores the resume token on each event/reconnection,
   and maintains a `_change_stream_connected` flag exposed via a property. The
   `/health` endpoint reports `"change_stream_connected": true/false`.

3. **A-4 — Decompose RoomMessageCenter:**
   - ~~Extract `TaskStateManager`~~, ~~`ResponseProcessor`~~, ~~`QueueExecutor`~~,
     ~~`AgentDispatcher`~~ as described in A-4.
   - ~~This is best done as a series of small PRs, one extraction at a time.~~
   - **All four modules extracted.** See A-4 section above for details.
   - **`TaskStateManager` extracted** — `modules/TaskStateManager.py` now owns
     all state transition logic (`transition_task`, `persist_message`,
     `notify_task`, `fail_task_and_notify`, `cancel_remaining_queue`) plus the
     `ProcessingContext` dataclass and `get_task`/`state_str` helpers (242 lines).
     `RoomMessageCenter` delegates to `self.tsm` (a `TaskStateManager` instance).
   - **`ResponseProcessor` extracted** — `modules/ResponseProcessor.py` now owns
     streaming/sync response handling, polling, cancellation/error handling during
     agent communication, artifact accumulation, and stream finalization (1,004 lines).
     `RoomMessageCenter` delegates to `self.response_processor`.
   - **`QueueExecutor` extracted** — `modules/QueueExecutor.py` now owns the
     queue loop with RAII cleanup (`_managed_queue`), continuation save/resume,
     per-item dispatch to `ResponseProcessor`, agent resolution/rate-limit
     helpers, and queue chaining (745 lines).  Agent assignment is injected
     via the `AgentDispatcher` instance passed at construction time.
     `RoomMessageCenter` delegates to `self.queue_executor`.
   - **`AgentDispatcher` extracted** — `modules/AgentDispatcher.py` now owns
     agent assignment and resolution: allowed-ID expansion (including group
     membership lookup), user-input extraction from message history, delegation
     to `AgentResolverService`, and persistence of the assignment (~200 lines).
     The `AssignResult` dataclass moved from `RoomMessageCenter` to
     `AgentDispatcher`.  The callback-based `AssignAgentFn` pattern in
     `QueueExecutor` was replaced with direct `AgentDispatcher` injection.
   - **Complete:** All planned modules have been extracted.  `RoomMessageCenter`
     is now ~305 lines (down from 2,050) — a thin facade composing
     `TaskStateManager`, `ResponseProcessor`, `QueueExecutor`, and
     `AgentDispatcher`.

---

### Implementation Plan Summary

| Phase | Scope | Effort | Issues Resolved | Status |
|-------|-------|--------|-----------------|--------|
| **1** | Unified `_transition_task` | 1–2 days | 9, 10 (+ prevents recurrence of 8) | **Completed** |
| **1b** | RAII queue cleanup | ½ day | 9 (structural guarantee) | **Completed** |
| **2** | Additive bug fixes | 1–2 days | 11, 14, 15, 16 | **Completed** |
| **3** | Defensive layers | 1 day | 7, 12, 13, 20, A-5 | **Completed** |
| **4** | Long-term architecture | 1–2 weeks | 17, 18, 19, A-3, A-4 | **Completed** |

**Total for Phases 1–3 (all user-facing bugs):** ~4–5 days — **completed**.

**Phase 4 progress:** All items complete. A-3 (CancellationToken) and Issue 17 (change
stream resilience) were completed previously. Issues 18 and 19 are resolved by A-3. A-4
decomposition is now fully complete — all four modules (`TaskStateManager`,
`ResponseProcessor`, `QueueExecutor`, `AgentDispatcher`) have been extracted.
`RoomMessageCenter` is now ~305 lines (down from 2,050), serving as a thin facade
that composes the extracted modules.

**Newly discovered (post-review):** Issues 21 and 22 are in `ResponseProcessor`
(extracted from `RoomMessageCenter` during A-4). They should be resolved as a small
follow-up.

---

### Note on Method Name Changes (A-4)

The A-4 decomposition renamed several methods at their API boundaries. Issue
descriptions above reference the **original** names from the monolithic
`RoomMessageCenter.py`. Current names after extraction:

| Original (RoomMessageCenter) | Current location | Current name |
|------------------------------|-----------------|--------------|
| `_handle_streaming_response_for_room` | `ResponseProcessor` | `handle_streaming_response` |
| `_handle_sync_response_for_room` | `ResponseProcessor` | `handle_sync_response` |
| `_poll_task_until_complete` | `ResponseProcessor` | `_poll_task_until_complete` |
| `_handle_streaming_cancellation` | `ResponseProcessor` | `_handle_streaming_cancellation` |
| `_handle_streaming_error` | `ResponseProcessor` | `_handle_streaming_error` |
| `_process_agent_message_queue` | `QueueExecutor` | `process_queue` |
| `_process_single_agent_message` | `QueueExecutor` | `_process_single_message` |
| `_transition_task` | `TaskStateManager` | `transition_task` |
| `_fail_task_and_notify` | `TaskStateManager` | `fail_task_and_notify` |
| `_cancel_remaining_queue` | `TaskStateManager` | `cancel_remaining_queue` |
| `_notify_task` | `TaskStateManager` | `notify_task` |
| `_persist_message` | `TaskStateManager` | `persist_message` |
| `_try_cancel_remote_task` | `ResponseProcessor` | `_try_cancel_remote_task` |
| `_assign_agent` | `AgentDispatcher` | `assign_agent` |
| `_assign_agent_for_queue` | `AgentDispatcher` | `assign_agent_for_queue` |
| `_resolve_allowed_agent_ids` | `AgentDispatcher` | `_resolve_allowed_agent_ids` |
| `_extract_user_input` | `AgentDispatcher` | `_extract_user_input` |
