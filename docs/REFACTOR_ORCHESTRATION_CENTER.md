# Refactoring OrchestrationCenter — Design & Implementation Plan

## 1. Problem Statement

`modules/OrchestrationCenter.py` is **3,292 lines** with **35 methods** in a single class and
**12 injected service dependencies**. Its own docstring lists 6 "Key Responsibilities" — a
clear signal that it violates the **Single Responsibility Principle**.

The file is difficult to navigate, reason about, review, and test. Two fundamentally
independent orchestration domains are interleaved in the same class.

---

## 2. Current Structure Analysis

### 2.1 Method Inventory

Every method in `OrchestrationCenter`, grouped by logical concern:

#### Group A — MetaTask Workflow Orchestration (~1,500 lines)

| Method | Line | Visibility | Purpose |
|--------|------|------------|---------|
| `decompose_task()` | 164 | public | AI-powered task decomposition into MetaTasks |
| `assign_agents_metatasks_by_parent_task_id()` | 474 | public | Batch agent assignment |
| `assign_agent_to_meta_task()` | 605 | public | Single agent assignment to a meta task |
| `run_workflow()` | 726 | public | Sequential workflow execution |
| `process_meta_task()` | 970 | public | Execute a single meta task via A2A |
| `summarize_meta_task_for_base_task()` | 1325 | public | Result synthesis across MetaTasks |
| `_build_task_description_with_context()` | 1283 | private | Build context string for a task |
| `_extract_task_result()` | 1217 | private | Extract completed task result |

#### Group B — Room Message Orchestration (~1,800 lines)

| Method | Line | Visibility | Purpose |
|--------|------|------------|---------|
| `process_room_user_message()` | 2294 | public | Entry point for room messages |
| `resume_queue_from_continuation()` | 2689 | public | Resume queue after webhook |
| `_validate_room_message_request()` | 2413 | private | Validate request params |
| `_get_room_memory_context()` | 2433 | private | Get room memory |
| `_process_agent_message_queue()` | 2462 | private | Process message queue |
| `_save_queue_continuation()` | 2647 | private | Persist queue for push notifications |
| `_assign_agent()` | 2773 | private | Agent assignment for room messages |
| `_process_single_agent_message()` | 2898 | private | Process one agent message |
| `_handle_streaming_response_for_room()` | 1760 | private | Streaming handler (**~470 lines**) |
| `_handle_sync_response_for_room()` | 3018 | private | Sync response handler |
| `_handle_a2a_response_for_room()` | 2232 | private | Handle A2A response for room |
| `_setup_task_tracking()` | 1551 | private | Create task record for tracking |
| `_send_task_update()` | 1612 | private | Send SSE task updates |
| `_poll_task_until_complete()` | 1647 | private | Poll agent for task completion |
| `_queue_next_messages()` | 3232 | private | Queue follow-up messages |
| `_update_room_memory_after_processing()` | 3295 | private | Update memory after processing |

#### Group C — Shared A2A Response Helpers

| Method | Line | Visibility | Purpose |
|--------|------|------------|---------|
| `_get_text_from_a2a_response()` | 1199 | private | Extract text from A2A response |
| `_get_task_from_agent()` | 1498 | private | Get task from agent (one-liner) |
| `_get_message_from_task()` | 1503 | private | Extract message from Task |
| `_get_text_from_message()` | 1543 | private | Extract text from Message |

#### Module-Level Types (only used by Group B)

| Class | Line | Purpose |
|-------|------|---------|
| `ProcessingStatus` | 61 | Enum for message processing outcomes |
| `ProcessingResult` | 70 | Result container with status + metadata |

### 2.2 External Callers

| Caller file | Methods used | Domain |
|-------------|-------------|--------|
| `api/orchestration_center.py` | `decompose_task`, `assign_agents_metatasks_by_parent_task_id`, `assign_agent_to_meta_task`, `run_workflow`, `process_meta_task`, `summarize_meta_task_for_base_task`, `process_room_user_message` | Both |
| `api/room_center.py` | `process_room_user_message` | Room only |
| `api/webhooks.py` | `resume_queue_from_continuation` (lazy import) | Room only |
| `modules/HostAgent.py` | `decompose_task`, `assign_agent_to_meta_task`, `process_meta_task`, `summarize_meta_task_for_base_task` | Workflow only |
| `jobs/stale_task_checker.py` | `process_room_user_message` (lazy import) | Room only |

### 2.3 Service Dependencies by Domain

| Service | Workflow | Room |
|---------|----------|------|
| `task_service` | Yes | No (uses `task_service.get_task_from_agent` only — replaceable with direct call) |
| `openai_service` | Yes | No |
| `agent_service` | Yes | No |
| `a2a_service` | Yes | Yes |
| `chat_memory_service` | Yes | No |
| `database_service` | Yes | Yes |
| `sse_manager` | Yes (cancellation in `run_workflow`) | Yes |
| `room_services` | No | Yes |
| `room_memory_service` | No | Yes |
| `debate_service` | No | Yes |
| `room_coordinator_service` | No | Yes |
| `rate_limit_service` | No | Yes |

---

## 3. Refactoring Design

### 3.1 Target File Structure

```
common/utils/
├── a2a_helpers.py                   # ~130 lines  (NEW — shared A2A utilities)

services/
├── notification_service.py          # ~90 lines   (NEW — SSE notification layer)

modules/
├── __init__.py
├── WorkflowCenter.py                # ~1,350 lines (NEW — Group A)
├── RoomMessageCenter.py             # ~2,100 lines (NEW — Group B + types)
├── OrchestrationCenter.py           # DELETED (no facade)
├── HostAgent.py                     # DELETED (unused dead code)
├── AgentCenter.py
├── DebationCenter.py
├── InspectionCenter.py
├── MemoryCenter.py
├── RoomCenter.py
└── TaskCenter.py
```

### 3.2 `common/utils/a2a_helpers.py` — Shared A2A Utilities

Stateless helper functions for extracting content from A2A `Task` / `Message` objects.
No class needed — these are pure functions.

```python
"""Shared utilities for extracting content from A2A Task/Message objects."""

from a2a.types import Message, Part, Role, Task


def get_text_from_a2a_response(result: Task | Message) -> str: ...
def get_message_from_task(task: Task) -> Message | None: ...
def get_text_from_message(message: Message | None) -> str: ...
```

**Rationale:** Both orchestrators need these. Extracting them as free functions
eliminates the need for a mixin or shared base class, keeping the design simple.

### 3.3 `modules/WorkflowCenter.py` — MetaTask Workflow

```python
class WorkflowCenter:
    """Task decomposition, agent assignment, workflow execution,
    and result summarization for MetaTask-based workflows."""

    def __init__(self):
        self.task_service = task_service
        self.openai_service = openai_service
        self.agent_service = agent_service
        self.a2a_service = a2a_service
        self.chat_memory_service = chat_memory_service
        self.database_service = db_service        # for query_similar_agents, get_agent_by_agent_id
        self.sse_manager = sse_manager            # for cancellation checks in run_workflow

    # === Public API ===
    async def decompose_task(self, request) -> OrchestrationCenterResponse: ...
    async def assign_agents_metatasks_by_parent_task_id(self, request) -> OrchestrationCenterResponse: ...
    async def assign_agent_to_meta_task(self, request) -> OrchestrationCenterResponse: ...
    async def run_workflow(self, request) -> OrchestrationCenterResponse: ...
    async def process_meta_task(self, request) -> OrchestrationCenterResponse: ...
    async def summarize_meta_task_for_base_task(self, request) -> OrchestrationCenterResponse: ...

    # === Private Helpers ===
    async def _build_task_description_with_context(self, meta_task) -> str: ...
    async def _extract_task_result(self, meta_task_id) -> dict: ...
```

**Dependencies:** 7 services (`task_service`, `openai_service`, `agent_service`,
`a2a_service`, `chat_memory_service`, `database_service`, `sse_manager`).

**Note:** `_get_task_from_agent` was a one-liner delegating to `task_service`. It is
inlined as `self.task_service.get_task_from_agent()` rather than kept as a method.
A2A response parsing uses the free functions from `a2a_helpers.py`.

### 3.4 `modules/RoomMessageCenter.py` — Room Message Processing

```python
class ProcessingStatus(Enum): ...   # Moved from OrchestrationCenter.py
class ProcessingResult: ...          # Moved from OrchestrationCenter.py

class RoomMessageCenter:
    """Room user message processing: agent communication,
    streaming/sync responses, queue management, and memory updates."""

    def __init__(self):
        self.a2a_service = a2a_service
        self.room_services = room_services
        self.room_memory_service = room_memory_service
        self.database_service = db_service
        self.debate_service = debate_service
        self.sse_manager = sse_manager
        self.room_coordinator_service = room_coordinator_service
        self.rate_limit_service = rate_limit_service
        self.task_service = task_service            # for get_task_from_agent

    # === Public API ===
    async def process_room_user_message(self, request) -> OrchestrationCenterResponse: ...
    async def resume_queue_from_continuation(self, message_id, ...) -> bool: ...

    # === Queue Management ===
    async def _process_agent_message_queue(self, ...): ...
    async def _save_queue_continuation(self, ...): ...
    async def _queue_next_messages(self, ...): ...

    # === Agent Communication ===
    async def _process_single_agent_message(self, ...): ...
    async def _assign_agent(self, current_message) -> Agent | None: ...
    async def _handle_streaming_response_for_room(self, ...): ...
    async def _handle_sync_response_for_room(self, ...): ...
    async def _handle_a2a_response_for_room(self, ...): ...

    # === Task Tracking & SSE ===
    async def _setup_task_tracking(self, ...): ...
    async def _send_task_update(self, ...): ...
    async def _poll_task_until_complete(self, ...): ...

    # === Memory & Validation ===
    def _validate_room_message_request(self, ...): ...
    async def _get_room_memory_context(self, ...): ...
    async def _update_room_memory_after_processing(self, ...): ...
```

**Dependencies:** 9 services + `notification_service`.

### 3.5 `services/notification_service.py` — SSE Notification Layer

```python
class NotificationService:
    """Pure 'format and send' layer for SSE task-update notifications.
    Does NOT perform idempotency checks — callers must guard before calling."""

    def __init__(self):
        self.sse_manager = sse_manager

    async def send_task_update(self, *, room_id, message_id, status, ...): ...
```

**Dependencies:** 1 service (`sse_manager`).

**Rationale:** Both `RoomMessageCenter` and `api/webhooks.py` need to send
`task_update` SSE events with consistent formatting (agent name resolution,
null-`message_id` guard, etc.). Extracting this into a shared service
eliminates duplication while keeping the service thin.

**Idempotency design:** `NotificationService` is intentionally *not*
responsible for idempotency. The webhook path (`api/webhooks.notify_task_update`)
performs an early idempotency check via `db_service.update_last_notified_state`
that also gates expensive DB persistence work — this cannot be moved into
`NotificationService` without expanding its scope. `RoomMessageCenter` never
needs idempotency because it is the authoritative first-time processor.

### 3.6 No Facade — Direct Imports

Instead of keeping `OrchestrationCenter` as a thin facade (which adds indirection and
ongoing maintenance burden), update the 5 caller sites directly:

| Caller | Before | After |
|--------|--------|-------|
| `api/orchestration_center.py` | `from modules.OrchestrationCenter import OrchestrationCenter` | `from modules.WorkflowCenter import WorkflowCenter` + `from modules.RoomMessageCenter import RoomMessageCenter` |
| `api/room_center.py` | `from modules.OrchestrationCenter import OrchestrationCenter` | `from modules.RoomMessageCenter import RoomMessageCenter` |
| `api/webhooks.py` | lazy `from api.orchestration_center import orchestration_center` | `from modules.RoomMessageCenter import room_message_center` |
| `modules/HostAgent.py` | N/A (deleted — was unused dead code) | — |
| `jobs/stale_task_checker.py` | lazy `from modules.OrchestrationCenter import OrchestrationCenter` | `from modules.RoomMessageCenter import room_message_center` |

**Rationale:** Each caller only uses one domain. Direct imports make dependencies
explicit, enable better IDE navigation, and eliminate the facade maintenance tax.

### 3.7 `_handle_streaming_response_for_room` Decomposition

This method is ~470 lines — too large even after the file split. Break it into
per-event-type handlers:

```python
async def _handle_streaming_response_for_room(self, ...):
    """Orchestrates streaming — delegates each event type to a focused handler."""
    ...
    async for a2a_response in self.a2a_service.send_message_streaming(...):
        if self.sse_manager.is_cancelled(user_message_id):
            return await self._handle_streaming_cancellation(...)

        if isinstance(a2a_response.root, JSONRPCErrorResponse):
            return await self._handle_streaming_error(...)

        result = a2a_response.root.result
        match result.kind:
            case "message":
                await self._handle_stream_message_chunk(result, ...)
            case "task":
                await self._handle_stream_task_event(result, ...)
            case "status-update":
                await self._handle_stream_status_update(result, ...)
            case "artifact-update":
                await self._handle_stream_artifact_update(result, ...)

    return await self._finalize_streaming(...)
```

New private methods (~80-120 lines each):

| Method | Responsibility |
|--------|---------------|
| `_handle_stream_message_chunk()` | Accumulate parts, save to DB incrementally, send SSE tokens |
| `_handle_stream_task_event()` | Log task status during streaming |
| `_handle_stream_status_update()` | Update task status in DB, send SSE status, handle terminal states |
| `_handle_stream_artifact_update()` | Update artifacts in DB, send SSE artifact events |
| `_handle_streaming_cancellation()` | Send cancel SSE, clear flag, return cancelled status |
| `_handle_streaming_error()` | Send error SSE, return failed status |
| `_finalize_streaming()` | Final DB save, update message_text, send task_update SSE |

---

## 4. Risks and Mitigations

### 4.1 No Test Suite

The repository has **zero tests**. A structural refactoring (moving methods between
files) is low-risk, but any logic changes carry regression risk.

**Mitigation:** Execute in two phases (see Section 5) — Phase 1 is a pure structural
move with no logic changes; Phase 2 decomposes the large method.

### 4.2 Circular Imports

`api/webhooks.py` already uses a lazy import to avoid circular dependencies:
```python
from api.orchestration_center import orchestration_center
```

**Mitigation:** Preserve the lazy import pattern. After refactoring,
`api/webhooks.py` will lazy-import from whichever module exposes
`RoomMessageCenter`. Verify the import graph at runtime after each phase.

### 4.3 Dependency Claim is Weaker Than Ideal

Both classes still share `database_service`, `a2a_service`, and `sse_manager`.
The split reduces dependencies from 12 to 7 / 9, not to fully disjoint sets.

**Mitigation:** This is acceptable. The primary benefit is **method-level cohesion**
(each class only contains methods for one domain), not dependency elimination.

### 4.4 `_get_task_from_agent` Cross-Domain Usage

This one-liner wraps `self.task_service.get_task_from_agent()`. It was used in both
domains.

**Mitigation:** Inline it. Both classes that need it can call
`self.task_service.get_task_from_agent()` directly. No shared method needed.

### 4.5 `ProcessingStatus` / `ProcessingResult` Move

These module-level types are only used within the Room domain (verified via grep).

**Mitigation:** Move them into `RoomMessageCenter.py`. No external imports
to fix.

---

## 5. Implementation Plan

### Phase 1 — Pure Structural Split (no logic changes)

**Goal:** Split `OrchestrationCenter.py` into 3 files. Zero behavior change.

| Step | Action | Risk |
|------|--------|------|
| 1.1 | Create `common/utils/a2a_helpers.py` with the 3 shared helper functions extracted as free functions. | Low |
| 1.2 | Create `modules/WorkflowCenter.py` — move Group A methods into new class. Replace `self._get_text_from_*` / `self._get_message_from_task` calls with imports from `a2a_helpers`. Inline `_get_task_from_agent` as `self.task_service.get_task_from_agent()`. | Low |
| 1.3 | Create `modules/RoomMessageCenter.py` — move Group B methods + `ProcessingStatus` + `ProcessingResult` into new class. Same helper replacements as 1.2. Add `task_service` dependency. | Low |
| 1.4 | Delete `modules/OrchestrationCenter.py`. | — |
| 1.5 | Update `api/orchestration_center.py` — import both classes, create two singleton instances, wire each route to the correct class. | Low |
| 1.6 | Update `api/room_center.py` — import `RoomMessageCenter`. | Low |
| 1.7 | Update `api/webhooks.py` — change lazy import to use `RoomMessageCenter`. | Low |
| 1.8 | Delete `modules/HostAgent.py` — confirmed unused (no API route, no imports, no frontend references). | Low |
| 1.9 | Create `services/notification_service.py` — extract SSE `send_task_update` formatting into a pure send layer shared by `RoomMessageCenter` and `api/webhooks.py`. | Low |
| 1.10 | Update `jobs/stale_task_checker.py` — change lazy import to use `RoomMessageCenter`. | Low |
| 1.11 | Manual smoke test — start the server, verify all API routes respond (decompose, assign, workflow, room message, webhook). | — |

**Estimated effort:** 1-2 hours.

### Phase 2 — Decompose `_handle_streaming_response_for_room`

**Goal:** Break the ~470-line method into 6 focused sub-methods within
`RoomMessageCenter`. This involves logic restructuring (extracting the
`async for` loop body into `match`/dispatch), so it carries slightly higher risk.

| Step | Action | Risk |
|------|--------|------|
| 2.1 | Extract `_handle_streaming_cancellation()` — move cancellation block. | Low |
| 2.2 | Extract `_handle_streaming_error()` — move JSON-RPC error block. | Low |
| 2.3 | Extract `_handle_stream_message_chunk()` — move `"message"` case (~100 lines). | Medium |
| 2.4 | Extract `_handle_stream_task_event()` — move `"task"` case. | Low |
| 2.5 | Extract `_handle_stream_status_update()` — move `"status-update"` case (~100 lines). | Medium |
| 2.6 | Extract `_handle_stream_artifact_update()` — move `"artifact-update"` case. | Low |
| 2.7 | Extract `_finalize_streaming()` — move post-loop cleanup. | Low |
| 2.8 | Refactor main method to use `match` dispatch calling the above. | Medium |
| 2.9 | Manual smoke test — test streaming agent response end-to-end. | — |

**Estimated effort:** 1-2 hours.

### Phase 3 (Optional, Recommended) — Add Tests

| Step | Action |
|------|--------|
| 3.1 | Add unit tests for `a2a_helpers.py` functions (pure, easy to test). |
| 3.2 | Add integration tests for `WorkflowCenter` public methods with mocked services. |
| 3.3 | Add integration tests for `RoomMessageCenter` public methods with mocked services. |

---

## 6. Verification Checklist

After each phase, verify:

- [ ] Server starts without import errors
- [ ] `POST /orchestrationCenter/decomposeTask` works
- [ ] `POST /orchestrationCenter/assignAgentsToMetaTasks` works
- [ ] `POST /orchestrationCenter/assignAgentToMetaTask` works
- [ ] `POST /orchestrationCenter/runWorkflow` works
- [ ] `POST /orchestrationCenter/retryMetaTask` works
- [ ] `POST /orchestrationCenter/summarizeMetaTaskForBaseTask` works
- [ ] `POST /orchestrationCenter/processRoomUserMessage` works
- [ ] `POST /roomCenter/sendMessageToRoom` triggers background processing
- [ ] Webhook callbacks resume queue correctly
- [ ] Stale task checker recovery works
- [ ] No circular import errors at startup
- [ ] Streaming responses work (SSE tokens arrive in real-time)
- [ ] Cancellation during streaming works
- [ ] Push notification task → webhook → queue resume works
