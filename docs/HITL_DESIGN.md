# Human-in-the-Loop (HITL) Design

**Date**: February 22, 2026 (updated from Feb 21)
**Status**: Proposal — updated for Supervisor V2 adaptive loop; unified interrupt mechanism replaces separate pause/resume paths
**Scope**: Add event-driven human-in-the-loop support to the Supervisor V2 Pattern for multi-agent chat rooms
**Depends on**: [Supervisor V2 Design](./SUPERVISOR_V2_DESIGN.md), [Context & Memory System Design](./CONTEXT_MEMORY_SYSTEM_DESIGN.md)

> **Design note (Unified Interrupt):** All supervisor-level pauses — push-notification waits, agent
> `input_required`, and supervisor `CLARIFY` (whether pre-plan or mid-loop) — share a single
> `_save_interrupted_state(kind=...)` save method and a single `_resume_supervisor_v2()` resume path.
> The `interrupt_kind` field in the continuation payload drives routing on resume. `CLARIFY` is the
> single supervisor question action: it replaces the previous `ASK_USER` / `CLARIFY` split and the
> `pending_clarification_message_id` chat-input path. All supervisor questions use `HITLService` and
> the inline reply form regardless of when in the loop they fire.

---

## 1. Problem Statement

The system correctly **detects** when an A2A agent needs user input (`input_required` state) and **displays** it to the user, but the chain breaks after display. The user sees "The agent needs additional information to continue" and has no way to respond.

### What Works Today (End-to-End)

| Layer | Component | What It Does | Reference |
|---|---|---|---|
| Backend | `a2a_constants.py` line 26 | `INTERACTIVE_STATES = {TaskState.input_required, TaskState.auth_required}` — recognized as non-terminal | `services/a2a_constants.py` |
| Backend | `webhooks.py` line 136 | Detects `input_required`, sets `requires_input = True`, extracts `status_message` | `api/webhooks.py` |
| Backend | `sse_services.py` line 384 | Forwards `requires_input: true` and `status_message` to the frontend via SSE | `services/sse_services.py` |
| Backend | `stale_task_checker.py` line 152 | Intentionally excludes `input_required` from non-tracked task auto-fail | `jobs/stale_task_checker.py` |
| Frontend | `useRoomWebhook.ts` line 695 | Maps `requires_input` onto `MessageData.task_requires_input` | `src/hooks/useRoomWebhook.ts` |
| Frontend | `task-status-message.tsx` line 338 | Renders an amber "Input required" card with the agent's `status_message` | `src/components/task-status-message.tsx` |

### What's Missing

1. **No way to reply to a specific agent/task** — the user's next message goes through `sendMessage` → `send_message_to_room`, which creates a brand new task decomposition. It doesn't know it should be a continuation of the `input_required` task.

2. **No `message/send` back to the original A2A task** — the A2A protocol supports sending follow-up messages to an existing task (same `task_id` / `context_id`), but `a2a_service.py` has no code path for this.

3. **Queue is stuck** — when an agent returns `input_required` during the message queue, the system either treats it as a push-notification pause or a failure. There's no "pause queue, wait for user reply, then resume with the user's answer" flow.

4. **No Supervisor involvement** — the Supervisor has no mechanism to ask the user questions mid-execution (between agent steps) or to request plan approval before starting.

---

## 2. Design Goals

1. **Event-driven** — HITL interactions use a dedicated event channel (SSE events + REST endpoint), completely separate from the normal chat message flow
2. **Explicit identity** — every HITL interaction has a unique `request_id`, eliminating ambiguity about what the user is replying to
3. **Zero impact on normal chat** — `send_message_to_room` is never modified for HITL replies; HITL replies go through a dedicated endpoint
4. **Unified interrupt mechanism** — all supervisor-level pauses (push-notification, agent `input_required`, supervisor `CLARIFY`) use a single `_save_interrupted_state(kind=...)` / `_resume_supervisor_v2()` pair; adding a new pause kind in the future requires one enum value and one branch, not a new save+resume pair
5. **Support both HITL scenarios** — agent-initiated (`input_required`) and supervisor-initiated (`CLARIFY`, whether pre-plan or mid-loop); these are the only two semantic cases
6. **Resilient to disconnection** — HITL state is persisted to MongoDB; prompts survive SSE drops and page refreshes
7. **Single supervisor question type** — `CLARIFY` replaces the previous `CLARIFY` + `ASK_USER` split; supervisor questions use the same `HITLService` path and inline reply form regardless of when in the loop they fire; the `pending_clarification_message_id` chat-input path is retired

---

## 3. Architecture Overview

> **V2 Note**: All supervisor-enabled rooms use the **Supervisor V2 adaptive loop**
> (`SupervisorExecutor` → `decide_next` → `dispatch` → `record`). The queue-based
> execution path (`QueueExecutor`) is only used for non-supervisor rooms. Architecture
> and integration points below reflect the V2 path.

```
                    INTERRUPT TRIGGER
                    (Agent returns input_required
                     OR Supervisor action == CLARIFY
                     OR Agent returns push-notification PAUSED)
                            │
                            ▼
            ┌───────────────────────────────┐
            │  SupervisorExecutor detects    │
            │  pause condition               │
            │  _save_interrupted_state(      │
            │    kind=InterruptKind.*)       │
            │  (trajectory serialized with   │
            │   interrupt_kind, status,      │
            │   and kind-specific fields)    │
            └──────────────┬────────────────┘
                           │
              ┌────────────┴─────────────┐
              │ HITL kinds only          │
              ▼                          ▼ PUSH_NOTIFICATION
            ┌─────────────────────┐     (webhook resumes directly)
            │ HITLService         │
            │ .request_input()    │
            │ 1. Create record    │
            │ 2. Persist to DB    │
            │ 3. Emit SSE:        │
            │   hitl_input_req'd  │
            └──────────┬──────────┘
                       │
              (SSE to frontend)
                       │
                       ▼
            ┌─────────────────────┐
            │ Frontend renders    │
            │ inline reply form   │
            └──────────┬──────────┘
                       │ (User types reply)
                       ▼
            ┌─────────────────────┐
            │ POST /hitl/respond  │
            │ { request_id,       │
            │   user_input }      │
            └──────────┬──────────┘
                       │
                       ▼
            ┌─────────────────────────────────────────────┐
            │  HITLService.handle_response()               │
            │                                              │
            │  HITL_AGENT:                                 │
            │    → a2a_service.reply_to_task()             │
            │    → Agent webhook fires on terminal state   │
            │    → resume_queue_from_continuation()        │
            │                                              │
            │  HITL_SUPERVISOR:                            │
            │    → patch trajectory.hitl_user_reply        │
            │    → resume_queue_from_continuation()        │
            └──────────────────────┬──────────────────────┘
                                   │
                    ┌──────────────┘
                    │  (all interrupt kinds converge here)
                    ▼
            ┌─────────────────────────────────────────────┐
            │  _resume_supervisor_v2(continuation)         │
            │                                              │
            │  1. Deserialize trajectory                   │
            │  2. Branch on interrupt_kind:                │
            │     PUSH_NOTIFICATION → append webhook result│
            │     HITL_AGENT        → append webhook result│
            │     HITL_SUPERVISOR   → inject hitl_user_reply│
            │  3. Re-run SupervisorExecutor.run(resumed=…) │
            └─────────────────────────────────────────────┘
```

### Unified Interrupt: All Pauses, One Resume Path

The core insight is that all three interrupt kinds share the same underlying mechanics:
**serialize trajectory → persist → wait → deserialize → re-run loop**. The only
differences are (a) what triggers the resume and (b) what gets injected into the
trajectory before re-running.

| Interrupt Kind | Trigger | What Gets Injected | Where Saved |
|---|---|---|---|
| `PUSH_NOTIFICATION` | A2A agent webhook on terminal state | Agent result text → `StepResult.response_text` | Agent message (`paused_message_id`) |
| `HITL_AGENT` | User `POST /hitl/respond` → agent webhook | Agent result text → `StepResult.response_text` | Agent message (`paused_message_id`) |
| `HITL_SUPERVISOR` | User `POST /hitl/respond` directly | `trajectory.hitl_user_reply` | User message (`user_message_id`) |

All three converge on `resume_queue_from_continuation()` → `_resume_supervisor_v2()`.
Adding a new interrupt kind in the future only requires one new enum value and one
new branch in `_resume_supervisor_v2()`.

`HITL_SUPERVISOR` covers **both** pre-plan clarification (supervisor asks before dispatching
any agents) and mid-loop questions (supervisor asks between dispatch rounds). The flow is
identical in both cases — the only difference is whether the trajectory's `entries` list is
empty or already has prior steps.

### Backward Compatibility: Legacy `"clarifying"` Trajectory Status

Existing room documents may have trajectories serialized with `status == "clarifying"` from
the previous CLARIFY implementation. Two compatibility rules apply:

1. **Crash-recovery guard**: Treat `"clarifying"` the same as `AWAITING_INPUT` — do NOT
   auto-resume it; only a user reply via `POST /hitl/respond` is valid.
2. **`send_message_to_room` shim**: If a room document still has
   `pending_clarification_message_id` set (in-flight legacy CLARIFY), route that specific
   reply via the old clarify-resume path so in-flight sessions are not broken. Remove this
   shim after one full `task_expiry_hours` cycle (all legacy CLARIFY requests will have
   timed out by then).

---

## 4. Two HITL Scenarios

### Scenario 1: Supervisor CLARIFY (Pre-Plan or Mid-Loop)

The Supervisor decides it needs user input before proceeding — either before dispatching any agents
(pre-plan) or between dispatch rounds (mid-loop). Both cases are handled by `ActionType.CLARIFY`
and follow the identical flow through `HITLService`. The only difference is whether the trajectory's
`entries` list is empty or contains prior steps.

**Flow:**

```
Step 1: SupervisorExecutor calls decide_next()
        → LLM returns SupervisorAction(action=CLARIFY,
              clarification_question="...",
              prompt_type="text" | "choice" | "confirmation",
              choices=[...] | None)

Step 2: SupervisorExecutor handles CLARIFY case
        → Records TrajectoryEntry(action=CLARIFY)
        → Sets trajectory.status = "awaiting_input"
        → Creates HITL request first so we have request_id for the continuation:
              request = await hitl_service.request_input(
                  source="supervisor",
                  prompt=action.clarification_question,
                  prompt_type=action.prompt_type,
                  choices=action.choices,
                  continuation_message_id=user_message_id,
              )
        → Calls _save_interrupted_state(kind=HITL_SUPERVISOR,
                message_id=user_message_id, hitl_request_id=request.request_id, ...)
        → Returns SupervisorRunResult(status=RunStatus.AWAITING_INPUT)

Step 3: RoomMessageCenter._handle_v2_run_result()
        → Handles RunStatus.AWAITING_INPUT
          (persists trajectory; does NOT emit COMPLETED)

Step 4: HITLService emits SSE: hitl_input_requested
        → Frontend renders inline reply form with the question

Step 5: User submits reply
        → POST /rooms/{room_id}/hitl/respond { request_id, user_input }

Step 6: HITLService.handle_response()
        → Loads HITLRequest, sees source == "supervisor"
        → Calls _handle_supervisor_response():
            - Loads continuation from DB
            - Sets trajectory.hitl_user_reply = user_input
            - Calls resume_queue_from_continuation()

Step 7: _resume_supervisor_v2() re-runs loop
        → interrupt_kind == "hitl_supervisor"
        → trajectory.hitl_user_reply is injected into conversation_context
        → Supervisor decides next action with user's answer in context
```

No webhook involved — the user's API call in step 5 is the event that triggers resume.
This flow is identical whether the CLARIFY fires at step 0 (pre-plan) or step N (mid-loop).

### Scenario 2: Agent Returns `input_required` (Mid-Execution)

An A2A agent says "I need more info from the user to continue." In **Supervisor V2**, agents are dispatched by `SupervisorExecutor._dispatch_targets()` via `AgentMessageProcessor.process_single_message()`.

**Detailed flow:**

```
Step 1: SupervisorExecutor._dispatch_targets() dispatches agent
        → AgentMessageProcessor.process_single_message()
        → a2a_service.send_message_to_tracked_agent()
        → Agent returns task with status = input_required

Step 2: AgentMessageProcessor detects input_required
        → Returns ProcessingStatus.AWAITING_INPUT  (NEW — distinct from PAUSED)
        → dispatch_one() maps to V2StepResult(status=StepStatus.AWAITING_INPUT) (NEW)

Step 3: SupervisorExecutor sees AWAITING_INPUT results
        → Calls _save_interrupted_state(kind=HITL_AGENT) (unified with push-notification pause)
        → Saves trajectory with status="awaiting_input" to pending_continuation
        → Supervisor_v2=True flag included, plus hitl_awaiting_input=True
        → Returns SupervisorRunResult(status=RunStatus.AWAITING_INPUT) (NEW)

Step 4: HITLService creates request
        → Persists HITLRequest to MongoDB (with a2a_task_id, context_id)
        → Emits SSE: hitl_input_requested

Step 5: Frontend renders inline reply form
        → User sees agent's question inside amber card with text input

Step 6: User submits reply
        → POST /rooms/{room_id}/hitl/respond { request_id, user_input }

Step 7: HITLService.handle_response()
        → Loads HITLRequest, sees source == "agent"
        → Calls a2a_service.reply_to_task(task_id, context_id, user_input)
        → Marks HITLRequest as "responded"

Step 8: Agent processes reply
        → Task transitions: input_required → working → completed
        → Agent sends webhook on terminal state

Step 9: Webhook handler (EXISTING)
        → Calls resume_queue_from_continuation(message_id, task_result_text)
        → RoomMessageCenter detects supervisor_v2=True in continuation
        → _resume_supervisor_v2() appends result to trajectory
        → SupervisorExecutor.run(resumed_trajectory=...) continues loop
```

Steps 8-9 reuse the **existing** webhook → `_resume_supervisor_v2()` resume path, extended to handle `AWAITING_INPUT` results the same way it handles `PAUSED` results.

---

## 5. Data Models

### 5.0 InterruptKind

The `interrupt_kind` field in every continuation payload is the single routing signal
for `_resume_supervisor_v2()`. Backward compatibility: if the field is absent (legacy
push-notification continuations saved before this design), assume `PUSH_NOTIFICATION`.

```python
class InterruptKind(str, Enum):
    PUSH_NOTIFICATION = "push_notification"
    # Agent returned input_required — waits for user reply via HITLService,
    # then the A2A agent's webhook re-triggers _resume_supervisor_v2().
    HITL_AGENT        = "hitl_agent"
    # Supervisor issued ASK_USER — waits for user reply via HITLService,
    # which patches hitl_user_reply onto the trajectory and calls resume directly.
    HITL_SUPERVISOR   = "hitl_supervisor"
```

### 5.1 HITLRequest

Persisted to MongoDB. Represents a pending request for human input.

```python
class HITLEventType(str, Enum):
    """Events in the human-in-the-loop lifecycle."""
    INPUT_REQUESTED = "hitl_input_requested"
    INPUT_RECEIVED  = "hitl_input_received"
    INPUT_EXPIRED   = "hitl_input_expired"
    INPUT_CANCELED  = "hitl_input_canceled"
    ERROR           = "hitl_error"


class HITLPromptType(str, Enum):
    TEXT = "text"                # Free-form text input
    CHOICE = "choice"            # Select from predefined options
    CONFIRMATION = "confirmation" # Yes/No or Approve/Reject


class HITLStatus(str, Enum):
    PENDING = "pending"
    RESPONDED = "responded"
    EXPIRED = "expired"
    CANCELED = "canceled"


class HITLRequest(BaseModel):
    """A request for human input, emitted as an event and persisted to DB."""
    request_id: str = Field(default_factory=lambda: uuid4().hex)
    room_id: str
    user_message_id: str                    # The original user message that started the plan

    # What triggered this
    source: Literal["agent", "supervisor"]
    source_step_id: str | None = None       # Which plan step triggered this
    agent_id: str | None = None             # Which agent is waiting (if source == "agent")
    agent_name: str | None = None           # For display

    # A2A continuation context (for agent-sourced requests)
    a2a_task_id: str | None = None
    a2a_context_id: str | None = None
    continuation_message_id: str | None = None  # Links to the paused RoomAgentMessage

    # The question
    prompt: str                              # What to show the user
    prompt_type: HITLPromptType = HITLPromptType.TEXT
    choices: list[str] | None = None         # For prompt_type == "choice"

    # Lifecycle
    status: HITLStatus = HITLStatus.PENDING
    expires_at: datetime | None = None       # When this request auto-expires
    created_at: datetime = Field(default_factory=utcnow)

    # Response (populated when status == "responded")
    user_input: str | None = None
    responded_at: datetime | None = None
```

### 5.2 HITLResponse

The payload for the user's reply (REST request body, not persisted separately).

```python
class HITLResponseRequest(BaseModel):
    """REST request body for POST /rooms/{room_id}/hitl/respond."""
    request_id: str
    user_input: str
```

### 5.3 Supervisor V2 Model Extensions

> **V2 Note**: The V1 `SupervisorReview` model does not exist in V2. The Supervisor V2
> adaptive loop (`models/supervisor_v2.py`) uses `SupervisorAction` / `ActionType` and
> `SupervisorTrajectory`. HITL requires changes to these existing V2 models.

```python
# models/supervisor_v2.py — changes

class ActionType(StrEnum):
    DELEGATE = "delegate"
    SYNTHESIZE = "synthesize"
    DONE = "done"
    # CLARIFY now covers both pre-plan clarification and mid-loop supervisor questions.
    # ASK_USER is removed — it was a duplicate of CLARIFY with different routing.
    CLARIFY = "clarify"


class SupervisorAction(BaseModel):
    """Single next-action decision produced by the Supervisor LLM."""
    action: ActionType
    reasoning: str

    # DELEGATE fields
    targets: list[DelegateTarget] = Field(default_factory=list)

    # SYNTHESIZE fields
    synthesis_instruction: str | None = None

    # CLARIFY fields (now used for all supervisor questions, pre-plan or mid-loop)
    clarification_question: str | None = None

    # NEW: prompt options (previously only on ASK_USER; now on CLARIFY too)
    prompt_type: HITLPromptType = HITLPromptType.TEXT
    choices: list[str] | None = None


class RunStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    PAUSED = "paused"
    # CLARIFYING is removed — replaced by AWAITING_INPUT.
    # Legacy "clarifying" trajectory status is handled by the backward-compat guard
    # in the crash-recovery block (treat it the same as AWAITING_INPUT — do not auto-resume).
    AWAITING_INPUT = "awaiting_input"  # NEW: paused for any HITL (agent or supervisor)


class SupervisorTrajectory(BaseModel):
    # ... existing fields ...

    hitl_user_reply: str | None = None
    """The user's reply to a CLARIFY question (pre-plan or mid-loop).
    Set by _handle_supervisor_response() before calling resume_queue_from_continuation().
    The supervisor prompt formatter includes this so the LLM sees the user's answer
    on resume."""

    hitl_original_message_id: str | None = None
    """The user_message_id of the message whose loop was paused by CLARIFY.
    Replaces clarify_original_message_id."""

    # REMOVED: clarify_user_reply — replaced by hitl_user_reply
    # REMOVED: clarify_original_message_id — replaced by hitl_original_message_id
    #
    # Backward compat: resume code must check both hitl_user_reply AND the legacy
    # clarify_user_reply field so that in-flight trajectories serialized before
    # the rename still resume correctly:
    #   effective_reply = trajectory.hitl_user_reply or trajectory.clarify_user_reply
```

The `status` literal on `SupervisorTrajectory` gains `"awaiting_input"` and **removes** `"clarifying"`:

```python
status: Literal[
    "running", "completed", "failed", "canceled", "awaiting_input"
] = "running"
# Note: "clarifying" is a legacy value only — new code never sets it.
# The crash-recovery guard treats "clarifying" == "awaiting_input" (skip auto-resume).
```

`TrajectoryStatus` (the `StrEnum` used by the DB crash-recovery path in
`RoomMessageCenter`) must gain `AWAITING_INPUT` and the backward-compat handling of
the legacy `CLARIFYING` value:

```python
class TrajectoryStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    # CLARIFYING removed — use AWAITING_INPUT instead.
    # Legacy "clarifying" value is handled by the crash-recovery guard (see below).
    RECOVERING = "recovering"       # existing — used by stale-task crash recovery
    AWAITING_INPUT = "awaiting_input"  # NEW: paused for HITL (agent or supervisor)
```

**Crash-recovery exclusion (important):** `RoomMessageCenter._process_supervisor_v2()` checks
the checkpointed trajectory status and auto-resumes if it is `RUNNING` or `RECOVERING`.
An `AWAITING_INPUT` trajectory must NOT be auto-resumed — the user's reply is the only
valid resume trigger. The legacy `"clarifying"` value must also be excluded:

```python
# In RoomMessageCenter._process_supervisor_v2(), crash-recovery resume block:
RESUMABLE_STATUSES = {TrajectoryStatus.RUNNING, TrajectoryStatus.RECOVERING}
# "clarifying" is a legacy string value (no longer in the enum) — exclude it explicitly.
LEGACY_NON_RESUMABLE = {"clarifying"}

if isinstance(checkpoint_data, dict):
    raw_status = checkpoint_data.get("status")
    if (
        raw_status not in LEGACY_NON_RESUMABLE
        and raw_status in RESUMABLE_STATUSES
        # NOTE: TrajectoryStatus.AWAITING_INPUT is intentionally excluded here.
    ):
        # ... resume ...
```

### 5.4 Executor Model Extensions

> **V2 Note**: V1's `QueueResult.AWAITING_INPUT` in `QueueExecutor` and
> `ProcessingStatus.AWAITING_INPUT` in the queue loop are **only needed for
> non-supervisor (legacy) rooms**. Supervisor V2 rooms use `StepStatus` and
> `RunStatus` from `models/supervisor_v2.py` instead.

**For V2 supervisor rooms** — additions to `models/supervisor_v2.py`:

```python
class StepStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    PAUSED = "paused"                 # Push notification task (existing)
    AWAITING_INPUT = "awaiting_input" # NEW: agent returned input_required


class RunStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    PAUSED = "paused"
    CLARIFYING = "clarifying"
    AWAITING_INPUT = "awaiting_input" # NEW: see §5.3
```

**For V1 non-supervisor rooms** — additions to `modules/ResponseProcessor.py` and `modules/QueueExecutor.py` (unchanged from original design):

```python
class ProcessingStatus(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    CANCELED = "canceled"
    PAUSED = "paused"
    AWAITING_INPUT = "awaiting_input"  # NEW (V1 queue only)


class QueueResult(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELED = "canceled"
    AWAITING_INPUT = "awaiting_input"  # NEW (V1 queue only)
```

### 5.5 SSE Event Types

```python
# New SSE event types (added to sse_services.py)
# Emitted when HITL input is needed:
{
    "type": "hitl_input_requested",
    "room_id": "room_123",
    "timestamp": "2026-02-12T00:00:00Z",
    "data": {
        "request_id": "abc123",
        "message_id": "msg_456",        # The agent message that triggered this
        "source": "agent",               # or "supervisor"
        "agent_id": "agent_42",
        "agent_name": "Research Agent",
        "prompt": "Which date range should I search?",
        "prompt_type": "text",           # or "choice", "confirmation"
        "choices": null,                 # or ["2023-2025", "2024-2026", "All time"]
        "step_number": 2,
        "total_steps": 5,
    }
}

# Emitted when HITL request is resolved/canceled/expired:
{
    "type": "hitl_status_update",
    "room_id": "room_123",
    "timestamp": "2026-02-12T00:05:00Z",
    "data": {
        "request_id": "abc123",
        "status": "responded",           # or "expired", "canceled"
    }
}
```

### 5.6 SSEProcessingStatus Extension

`SSEProcessingStatus` in `services/a2a_constants.py` needs a new value:

```python
class SSEProcessingStatus(str, Enum):
    PROCESSING    = "processing"
    COMPLETED     = "completed"
    CANCELED      = "canceled"
    FAILED        = "failed"
    REJECTED      = "rejected"
    RATE_LIMITED  = "rate_limited"
    AWAITING_INPUT = "awaiting_input"  # NEW: loop paused for HITL
```

**`AWAITING_INPUT` must NOT be added to `PROCESSING_DONE_STATUSES`.**

`PROCESSING_DONE_STATUSES` controls whether `sse_services.py` clears
`room.processing_message_id`. Keeping the message ID on the room is intentional:
1. It lets the cancel button remain functional (cancellation checks `processing_message_id`).
2. On page refresh, `useRoomWebhook.ts` reads `room.processing_message_id` to restore
   state. The 2-minute staleness check will skip the generic placeholder, and the
   pending HITL catch-up endpoint provides the correct HITL prompt instead.

```python
# services/a2a_constants.py — unchanged set (AWAITING_INPUT intentionally absent)
PROCESSING_DONE_STATUSES = {
    SSEProcessingStatus.COMPLETED,
    SSEProcessingStatus.CANCELED,
    SSEProcessingStatus.FAILED,
    SSEProcessingStatus.REJECTED,
    SSEProcessingStatus.RATE_LIMITED,
    # AWAITING_INPUT is NOT here — keep processing_message_id on the room
}
```

### 5.7 Storage

HITLRequest records are stored in a new `hitl_requests` MongoDB collection, indexed by:
- `request_id` (unique)
- `room_id` + `status` (for pending request lookup)
- `expires_at` + `status` (for expiry job)

#### Unified Continuation Payload

All three interrupt kinds use the **same continuation schema** stored on a MongoDB
message document. The `interrupt_kind` field is the routing key on resume.
Backward-compatibility rule: if `interrupt_kind` is absent, treat as `PUSH_NOTIFICATION`.

```python
# Unified continuation payload (stored on RoomAgentMessage.pending_continuation
# for PUSH_NOTIFICATION and HITL_AGENT, or on RoomUserMessage.pending_continuation
# for HITL_SUPERVISOR)
interrupted_state = {
    # ── Routing ──────────────────────────────────────────────────────────────
    "supervisor_v2": True,           # tells RoomMessageCenter → _resume_supervisor_v2()
    "interrupt_kind": "hitl_agent",  # InterruptKind value; absent → push_notification

    # ── Full trajectory snapshot ──────────────────────────────────────────────
    "trajectory": trajectory.model_dump(mode="json"),
    # status == "awaiting_input" for HITL kinds; "running" for PUSH_NOTIFICATION

    # ── Inputs needed to re-run SupervisorExecutor.run() ─────────────────────
    "room_id": room_id,
    "user_message_id": user_message_id,
    "message_text": message_text,
    "agent_registry": [p.model_dump(mode="json") for p in agent_registry],
    "room_config": room_config.model_dump(mode="json"),
    "conversation_context": conversation_context,
    "request_user_id": request_user_id,
    "quoted_text": quoted_text,

    # ── HITL-only fields (absent for PUSH_NOTIFICATION) ───────────────────────
    "hitl_request_id": request.request_id,  # links to HITLRequest document
}
```

**Where the continuation is saved:**

| Interrupt Kind | Saved on | Key |
|---|---|---|
| `PUSH_NOTIFICATION` | `RoomAgentMessage` | `paused_message_id` (the paused agent message) |
| `HITL_AGENT` | `RoomAgentMessage` | `paused_message_id` (same as push-notification) |
| `HITL_SUPERVISOR` | `RoomUserMessage` | `user_message_id` (no agent message to resume from) |

> **V1 non-supervisor rooms** still use the old continuation schema with
> `remaining_queue`, `current_agent_id`, etc. That schema gains the same two new
> HITL fields (`hitl_request_id`, `interrupt_kind`) described above.

---

## 6. Service Design: `HITLService`

### 6.1 Class Overview

```python
class HITLService:
    """
    Manages the human-in-the-loop interaction lifecycle.

    Responsibilities:
    1. Create HITL requests (triggered by queue loop or Supervisor review)
    2. Persist requests to MongoDB
    3. Emit SSE events to notify the frontend
    4. Handle user responses (route to A2A agent or Supervisor context)
    5. Clean up expired/canceled requests
    """

    def __init__(self):
        self.database_service = db_service
        self.sse_manager = sse_manager
        self.a2a_service = a2a_service

    async def request_input(
        self,
        room_id: str,
        user_message_id: str,
        source: Literal["agent", "supervisor"],
        prompt: str,
        prompt_type: HITLPromptType = HITLPromptType.TEXT,
        choices: list[str] | None = None,
        agent_id: str | None = None,
        agent_name: str | None = None,
        source_step_id: str | None = None,
        a2a_task_id: str | None = None,
        a2a_context_id: str | None = None,
        continuation_message_id: str | None = None,
        expires_in_hours: float = 24.0,
    ) -> HITLRequest:
        """Create and emit an HITL request."""
        request = HITLRequest(
            room_id=room_id,
            user_message_id=user_message_id,
            source=source,
            prompt=prompt,
            prompt_type=prompt_type,
            choices=choices,
            agent_id=agent_id,
            agent_name=agent_name,
            source_step_id=source_step_id,
            a2a_task_id=a2a_task_id,
            a2a_context_id=a2a_context_id,
            continuation_message_id=continuation_message_id,
            expires_at=utcnow() + timedelta(hours=expires_in_hours),
        )

        # 1. Persist FIRST (so it survives SSE drops)
        await self.database_service.create_hitl_request(request)

        # 2. Then emit SSE event
        await self.sse_manager.send_hitl_event(
            room_id=room_id,
            event_type=HITLEventType.INPUT_REQUESTED,
            request=request,
        )

        return request

    async def handle_response(
        self,
        room_id: str,
        request_id: str,
        user_input: str,
        user_id: str,
    ) -> dict:
        """Handle user's reply to an HITL request."""

        # 1. Load and validate
        request = await self.database_service.get_hitl_request(request_id)
        if not request:
            raise HTTPException(404, "HITL request not found")
        if request.room_id != room_id:
            raise HTTPException(403, "Room mismatch")
        if request.status != HITLStatus.PENDING:
            raise HTTPException(409, f"Request already {request.status}")

        # 2. Route based on source — status stays PENDING until routing succeeds
        #    so that the user can retry if the downstream call fails.
        try:
            if request.source == "agent":
                await self._handle_agent_response(request, user_input)
            elif request.source == "supervisor":
                await self._handle_supervisor_response(request, user_input)
        except Exception as exc:
            logger.error(
                "HITL routing failed for request %s: %s",
                request_id, exc, exc_info=True,
            )
            # Emit error SSE so the frontend can surface a retry prompt
            await self.sse_manager.send_hitl_event(
                room_id=room_id,
                event_type=HITLEventType.ERROR,
                request=request,
                error=str(exc),
            )
            raise HTTPException(
                502,
                f"Failed to deliver response to {request.source}: {exc}",
            )

        # 3. Mark as responded only after routing succeeds
        await self.database_service.update_hitl_request(
            request_id,
            status=HITLStatus.RESPONDED,
            user_input=user_input,
            responded_at=utcnow(),
        )

        # 4. Emit status update SSE
        await self.sse_manager.send_hitl_event(
            room_id=room_id,
            event_type=HITLEventType.INPUT_RECEIVED,
            request=request,
        )

        return {"status": "ok", "request_id": request_id}

    async def _handle_agent_response(
        self, request: HITLRequest, user_input: str
    ) -> None:
        """Send user's reply to the waiting A2A agent."""
        # Reset last_notified_state to allow re-notification
        # (fixes multi-round input_required idempotency issue)
        await self.database_service.reset_last_notified_state(
            request.continuation_message_id
        )

        # Send reply to agent via A2A protocol
        await self.a2a_service.reply_to_task(
            message_id=request.continuation_message_id,
            task_id=request.a2a_task_id,
            context_id=request.a2a_context_id,
            user_input=user_input,
        )
        # Agent will process and send webhook → resume_queue_from_continuation
        # which routes to _resume_supervisor_v2(kind=HITL_AGENT)

    async def _handle_supervisor_response(
        self, request: HITLRequest, user_input: str
    ) -> None:
        """Resume V2 supervisor loop with user's answer injected into trajectory.

        Patches hitl_user_reply onto the serialized trajectory before calling
        resume_queue_from_continuation(). _resume_supervisor_v2() detects
        interrupt_kind == HITL_SUPERVISOR and injects the reply into the
        conversation context for the next decide_next() call.
        """
        # Load the continuation so we can patch hitl_user_reply before resume
        # clears it. (get_pending_continuation is a non-destructive peek.)
        continuation = (
            await self.database_service.get_pending_continuation_on_message(
                request.continuation_message_id
            )
        )
        if continuation and continuation.get("supervisor_v2"):
            traj = continuation.get("trajectory", {})
            traj["hitl_user_reply"] = user_input
            traj["hitl_original_message_id"] = continuation.get("user_message_id")
            continuation["trajectory"] = traj
            await self.database_service.save_continuation_on_message(
                request.continuation_message_id, continuation
            )

        # Single resume path — _resume_supervisor_v2 branches on interrupt_kind
        await room_message_center.resume_queue_from_continuation(
            message_id=request.continuation_message_id,
            task_result_text=None,
        )

    async def get_pending_requests(self, room_id: str) -> list[HITLRequest]:
        """Get all pending HITL requests for a room (for SSE reconnect catch-up)."""
        return await self.database_service.get_pending_hitl_requests(room_id)

    async def cancel_request(self, request_id: str, room_id: str | None = None) -> None:
        """Cancel a pending HITL request.

        Args:
            request_id: The HITL request to cancel.
            room_id: If provided, validates the request belongs to this room
                     (required when called from user-facing endpoints).
        """
        request = await self.database_service.get_hitl_request(request_id)
        if not request:
            raise HTTPException(404, "HITL request not found")
        if room_id is not None and request.room_id != room_id:
            raise HTTPException(403, "Room mismatch")
        if request.status == HITLStatus.PENDING:
            await self.database_service.update_hitl_request(
                request_id, status=HITLStatus.CANCELED
            )
            # Clear the orphaned continuation
            if request.continuation_message_id:
                await self.database_service.get_and_clear_continuation_on_message(
                    request.continuation_message_id
                )
            # Notify frontend
            await self.sse_manager.send_hitl_event(
                room_id=request.room_id,
                event_type=HITLEventType.INPUT_CANCELED,
                request=request,
            )
```

### 6.2 A2A Reply Method

New method on `A2AService`:

```python
async def reply_to_task(
    self,
    message_id: str,
    task_id: str,
    context_id: str,
    user_input: str,
) -> dict:
    """Send a follow-up message to an existing A2A task (for HITL replies).

    Uses the same task_id and context_id to continue the conversation
    rather than starting a new task.
    """
    from services.database_service import db_service

    # 1. Load the existing RoomAgentMessage for webhook config
    msg = await self.database_service.get_room_agent_message_by_message_id(message_id)
    agent_url = msg.agent_url

    # 2. Generate a NEW webhook token for the reply.
    #    The original plaintext token was never stored (only its hash),
    #    so we cannot reuse it.  We must mint a fresh token, update the
    #    stored hash, and send the new plaintext to the agent.  When the
    #    agent calls back with this token our webhook handler will hash
    #    it and match against the newly stored hash.
    webhook_token = db_service.generate_webhook_token()
    webhook_token_hash = db_service.hash_webhook_token(webhook_token)
    await db_service.update_task_tracking_on_message(
        message_id, webhook_token_hash=webhook_token_hash,
    )

    webhook_url = settings.WEBHOOK_BASE_URL
    push_config = PushNotificationConfig(
        id=message_id,
        url=f"{webhook_url}/api/v1/webhooks/a2a/{message_id}",
        token=webhook_token,  # Plaintext token — agent sends this back in the callback
    )

    # 3. Build message with EXISTING task_id and context_id
    reply_message = Message(
        role="user",
        parts=[TextPart(text=user_input)],
        task_id=task_id,        # Continue existing task
        context_id=context_id,  # Same conversation context
        # referenceTaskIds tells hybrid-mode agents (which model continuations as
        # new tasks rather than resuming the same task) that this message is a
        # direct reply to the original task.  Compliant agents that support
        # in-place continuation ignore it; agents that start a new task per round
        # use it to pull in the prior task's context.  Including it is safe for all.
        reference_task_ids=[task_id],
    )

    params = MessageSendParams(
        message=reply_message,
        configuration=MessageSendConfiguration(
            push_notification_config=push_config,
        ),
    )

    # 4. Send via A2A client
    client = await self._get_a2a_client(agent_url)
    request = SendMessageRequest(params=params)
    response = await client.send_message(request)

    # 5. Update task status locally
    if hasattr(response, "root") and hasattr(response.root, "status"):
        await self.database_service.update_task_on_message(
            message_id, response.root.model_dump(mode="json")
        )

    return {"status": "sent"}
```

---

## 7. Integration Points

### 7.0 Unified `_save_interrupted_state()` — Replaces `_save_pause_state()` and `_save_hitl_pause_state()`

The previous design had two nearly identical save methods:
- `_save_pause_state()` for push-notification pauses
- `_save_hitl_pause_state()` for HITL pauses

These are replaced by a single method. The `interrupt_kind` parameter is the only
meaningful difference between all three interrupt scenarios.

```python
# In SupervisorExecutor — replaces both _save_pause_state() and _save_hitl_pause_state()

async def _save_interrupted_state(
    self,
    kind: InterruptKind,
    *,
    trajectory: SupervisorTrajectory,
    message_id: str,          # agent message for PUSH_NOTIFICATION/HITL_AGENT;
                               # user message for HITL_SUPERVISOR
    room_id: str,
    user_message_id: str,
    message_text: str,
    agent_registry: list[AgentProfile],
    room_config: RoomConfig,
    conversation_context: str | None,
    request_user_id: str | None,
    quoted_text: str | None = None,
    hitl_request_id: str | None = None,  # populated for HITL kinds only
) -> bool:
    """Serialize trajectory + run inputs for any interrupt kind.

    Saves on message_id (agent message for PUSH_NOTIFICATION/HITL_AGENT,
    user message for HITL_SUPERVISOR).  Returns True if saved successfully.
    """
    interrupted_state = {
        "supervisor_v2": True,
        "interrupt_kind": kind.value,
        "trajectory": trajectory.model_dump(mode="json"),
        "room_id": room_id,
        "user_message_id": user_message_id,
        "message_text": message_text,
        "agent_registry": [p.model_dump(mode="json") for p in agent_registry],
        "room_config": room_config.model_dump(mode="json"),
        "conversation_context": conversation_context,
        "request_user_id": request_user_id,
        "quoted_text": quoted_text,
    }
    if hitl_request_id is not None:
        interrupted_state["hitl_request_id"] = hitl_request_id

    success = await self.database_service.save_continuation_on_message(
        message_id, interrupted_state
    )
    if success:
        logger.info(
            "supervisor_interrupted_state_saved",
            extra={
                "room_id": room_id,
                "message_id": message_id,
                "interrupt_kind": kind.value,
                "trajectory_id": trajectory.trajectory_id,
            },
        )
    else:
        logger.error(
            "SupervisorExecutor: Failed to save interrupted state "
            "(kind=%s, message_id=%s)",
            kind.value,
            message_id,
        )
    return success
```

**Call sites:**

| Caller | Kind | `message_id` |
|---|---|---|
| DELEGATE case, PAUSED results | `PUSH_NOTIFICATION` | `pr.paused_message_id` (per paused agent) |
| DELEGATE case, AWAITING_INPUT results | `HITL_AGENT` | `ar.paused_message_id` (per awaiting agent) |
| ASK_USER case | `HITL_SUPERVISOR` | `user_message_id` |

### 7.1 Unified Resume Path — `_resume_supervisor_v2()` Branches on `interrupt_kind`

`_resume_supervisor_v2()` is the **single resume entry point** for all three interrupt
kinds. It reads `interrupt_kind` from the continuation and applies the appropriate
pre-run injection before calling `SupervisorExecutor.run(resumed_trajectory=...)`.

```python
# In RoomMessageCenter._resume_supervisor_v2():
# (called from resume_queue_from_continuation for all V2 continuations)

interrupt_kind = continuation.get("interrupt_kind", "push_notification")

if interrupt_kind in ("push_notification", "hitl_agent"):
    # A webhook result (task_result_text) is available — append it to the
    # trajectory entry that was waiting for this agent.
    self._append_paused_result_to_trajectory(
        trajectory,
        paused_message_id=paused_message_id,
        task_result_text=task_result_text,
    )
    if task_result_text and paused_agent_id:
        await room_memory_service.add_agent_response_to_memory(...)

elif interrupt_kind == "hitl_supervisor":
    # No webhook result — the user's reply is already patched onto the
    # trajectory by HITLService._handle_supervisor_response() before
    # calling resume_queue_from_continuation().
    # trajectory.hitl_user_reply is populated; include it in conversation_context:
    if trajectory.hitl_user_reply:
        conversation_context = (
            f"{conversation_context or ''}\n\n"
            f"[User replied to your question]: {trajectory.hitl_user_reply}"
        ).strip()
```

This replaces the previous `_handle_supervisor_response()` logic that patched the
trajectory dict before calling resume — now the resume path reads the already-patched
trajectory directly from the continuation.

### 7.2 V2: Detecting `input_required` in AgentMessageProcessor and SupervisorExecutor

> **V2 Note**: In supervisor rooms the queue loop (`_process_agent_message_queue`) is
> never invoked. The integration points are `AgentMessageProcessor` and
> `SupervisorExecutor._dispatch_targets()` instead.

**Step 1 — `AgentMessageProcessor.process_single_message()` returns a new status:**

```python
# In AgentMessageProcessor._handle_sync_response_for_room (or equivalent),
# after a2a_service.send_message_to_tracked_agent():
if response_type == "task" and response.get("status") == "input_required":
    task_data = response.get("task", {})
    return ProcessingResult(
        status=ProcessingStatus.AWAITING_INPUT,   # NEW (distinct from PAUSED)
        message_id=message_id,
        a2a_task_id=task_data.get("id"),
        a2a_context_id=task_data.get("context_id"),
        status_message=extract_status_message(task_data),
    )
```

**Step 2 — `SupervisorExecutor._dispatch_targets()` maps to `StepStatus.AWAITING_INPUT`:**

```python
# In dispatch_one(), after process_single_message() returns:
if result.status == ProcessingStatus.AWAITING_INPUT:
    return V2StepResult(
        step_number=step_number,
        agent_id=target.agent_id,
        agent_name=target.agent_name,
        task=target.task,
        response_text="",
        success=True,
        status=StepStatus.AWAITING_INPUT,        # NEW — distinguished from PAUSED
        paused_message_id=result.message_id,
        agent_message_id=message.message_id,
        a2a_task_id=result.a2a_task_id,          # NEW field on V2StepResult
        a2a_context_id=result.a2a_context_id,    # NEW field on V2StepResult
        status_message=result.status_message,    # The agent's question text
    )
```

**Step 3 — `SupervisorExecutor.run()` handles `AWAITING_INPUT` results:**

```python
# In the DELEGATE case, after _dispatch_targets():
awaiting = [r for r in results if r.status == StepStatus.AWAITING_INPUT]
if awaiting:
    entry.results = results
    trajectory.status = "awaiting_input"

    # Unified: save one interrupted state per awaiting agent (same as push-notification,
    # but with kind=HITL_AGENT and hitl_request_id added after HITLService creates it).
    # We save first (without hitl_request_id), create the HITL request, then update.
    # Alternatively: create HITL requests first, then save with request IDs.
    for ar in awaiting:
        request = await hitl_service.request_input(
            room_id=room_id,
            user_message_id=user_message_id,
            source="agent",
            prompt=ar.status_message or "The agent needs additional information.",
            agent_id=ar.agent_id,
            agent_name=ar.agent_name,
            a2a_task_id=ar.a2a_task_id,
            a2a_context_id=ar.a2a_context_id,
            continuation_message_id=ar.paused_message_id,
        )
        # Unified save — InterruptKind.HITL_AGENT
        saved = await self._save_interrupted_state(
            kind=InterruptKind.HITL_AGENT,
            trajectory=trajectory,
            message_id=ar.paused_message_id,
            room_id=room_id,
            user_message_id=user_message_id,
            message_text=message_text,
            agent_registry=agent_registry,
            room_config=room_config,
            conversation_context=conversation_context,
            request_user_id=request_user_id,
            quoted_text=quoted_text,
            hitl_request_id=request.request_id if request else None,
        )
        if not saved:
            trajectory.status = "failed"
            return self._log_and_return(
                room_id, trajectory,
                SupervisorRunResult(status=RunStatus.FAILED, trajectory=trajectory),
            )

    await self.sse_manager.send_processing_status(
        room_id, SSEProcessingStatus.AWAITING_INPUT, user_message_id
    )
    return self._log_and_return(
        room_id, trajectory,
        SupervisorRunResult(status=RunStatus.AWAITING_INPUT, trajectory=trajectory),
    )
```

**Step 4 — `_handle_v2_run_result()` handles `RunStatus.AWAITING_INPUT`:**

```python
# In RoomMessageCenter._handle_v2_run_result(), add to the match block:
case RunStatus.AWAITING_INPUT:
    pass  # Continuation already saved; HITLService emits the SSE event.
          # Token stays alive — resume path creates/reuses it.
```

> **V1 non-supervisor rooms**: The original `_process_agent_message_queue` integration
> (calling `QueueResult.AWAITING_INPUT`) still applies and is unchanged from the
> original design.

### 7.3 V2: Supervisor `CLARIFY` Action in `SupervisorExecutor.run()`

> **V2 Note**: There is no `supervisor_service.review_step()` method. The V2 supervisor
> produces one `SupervisorAction` per loop iteration via `decide_next()`. The `CLARIFY`
> integration is the unified handler for all supervisor questions — it fires whether the
> supervisor asks before dispatching any agents (pre-plan) or between dispatch rounds
> (mid-loop). It is a `case ActionType.CLARIFY` in the `match action.action` block inside
> `SupervisorExecutor.run()`, **replacing** the previous `CLARIFY` case that emitted a
> synthesis message and set `pending_clarification_message_id`.

```python
# In SupervisorExecutor.run(), inside the while loop's match block:
# (Replaces the old CLARIFY case that used pending_clarification_message_id)

case ActionType.CLARIFY:
    entry = TrajectoryEntry(
        step_number=step_number + 1,
        action=action,
        started_at=utcnow(),
        completed_at=utcnow(),
    )
    trajectory.entries.append(entry)
    trajectory.status = "awaiting_input"

    # Create HITL request first so we have the request_id for the continuation
    request = await hitl_service.request_input(
        room_id=room_id,
        user_message_id=user_message_id,
        source="supervisor",
        prompt=action.clarification_question or "The supervisor needs your input.",
        prompt_type=action.prompt_type,
        choices=action.choices,
        source_step_id=str(step_number + 1),
        continuation_message_id=user_message_id,
    )

    # Unified save — InterruptKind.HITL_SUPERVISOR
    # Saves on user_message_id (no agent message to resume from)
    saved = await self._save_interrupted_state(
        kind=InterruptKind.HITL_SUPERVISOR,
        trajectory=trajectory,
        message_id=user_message_id,
        room_id=room_id,
        user_message_id=user_message_id,
        message_text=message_text,
        agent_registry=agent_registry,
        room_config=room_config,
        conversation_context=conversation_context,
        request_user_id=request_user_id,
        quoted_text=quoted_text,
        hitl_request_id=request.request_id if request else None,
    )
    if not saved:
        trajectory.status = "failed"
        return self._log_and_return(
            room_id, trajectory,
            SupervisorRunResult(status=RunStatus.FAILED, trajectory=trajectory),
        )

    await self.sse_manager.send_processing_status(
        room_id, SSEProcessingStatus.AWAITING_INPUT, user_message_id
    )
    return self._log_and_return(
        room_id, trajectory,
        SupervisorRunResult(
            status=RunStatus.AWAITING_INPUT,
            trajectory=trajectory,
        ),
    )
```

**Resume path — `_resume_supervisor_v2()` picks up `hitl_user_reply`:**

When `_handle_supervisor_response()` calls `resume_queue_from_continuation()`, the
continuation already has `trajectory.hitl_user_reply` set (patched before the call).
`_resume_supervisor_v2()` detects `interrupt_kind == "hitl_supervisor"` and injects
the reply into `conversation_context` before calling `SupervisorExecutor.run()`:

```python
# In RoomMessageCenter._resume_supervisor_v2():
if interrupt_kind == "hitl_supervisor":
    # Support both new hitl_user_reply and legacy clarify_user_reply field names
    user_reply = trajectory.hitl_user_reply or getattr(trajectory, "clarify_user_reply", None)
    if user_reply:
        conversation_context = (
            f"{conversation_context or ''}\n\n"
            f"[User replied to your question]: {user_reply}"
        ).strip()
```

No separate `_save_hitl_pause_state()` method exists — this is handled entirely by
`_save_interrupted_state(kind=InterruptKind.HITL_SUPERVISOR, ...)`.

**Backward compatibility with the old `CLARIFY` path:**

The old `CLARIFY` case in `SupervisorExecutor.run()` emitted a synthesis message and set
`pending_clarification_message_id` on the room. That path is **removed**. For rooms that
have `pending_clarification_message_id` still set (in-flight legacy sessions), keep a shim
in `send_message_to_room` that detects the legacy field and routes the next message as a
clarify-resume using the old `clarify_user_reply` field. Remove the shim after one full
`task_expiry_hours` cycle.

```python
# In send_message_to_room — LEGACY SHIM (remove after migration window):
if legacy_clarify_id := room.extend_info.get("pending_clarification_message_id"):
    # Route via old clarify path for sessions that were in-flight before unification.
    # New CLARIFY requests go through HITLService and never set this field.
    ...
```

### 7.4 REST Endpoint

```python
# api/hitl.py

from fastapi import APIRouter, Depends, HTTPException

from api.room_center import verify_room_ownership
from common.auth import ClerkUser, get_current_user

router = APIRouter(prefix="/rooms/{room_id}/hitl", tags=["hitl"])

@router.post("/respond")
async def respond_to_hitl_request(
    room_id: str,
    body: HITLResponseRequest,
    user: ClerkUser = Depends(get_current_user),
):
    """User responds to an HITL prompt."""
    await verify_room_ownership(room_id, user)

    result = await hitl_service.handle_response(
        room_id=room_id,
        request_id=body.request_id,
        user_input=body.user_input,
        user_id=user.user_id,
    )
    return result

@router.get("/pending")
async def get_pending_hitl_requests(
    room_id: str,
    user: ClerkUser = Depends(get_current_user),
):
    """Get pending HITL requests for a room (SSE reconnect catch-up)."""
    await verify_room_ownership(room_id, user)

    requests = await hitl_service.get_pending_requests(room_id)
    return {"requests": [r.model_dump(mode="json") for r in requests]}

@router.post("/{request_id}/cancel")
async def cancel_hitl_request(
    room_id: str,
    request_id: str,
    user: ClerkUser = Depends(get_current_user),
):
    """Cancel a pending HITL request."""
    await verify_room_ownership(room_id, user)

    await hitl_service.cancel_request(request_id, room_id=room_id)
    return {"status": "canceled"}
```

### 7.5 Cancellation Integration

The existing `cancelMessage` endpoint in `sse_services.py` must also cancel any associated HITL request:

```python
# In the cancel message handler, add:
pending_hitl = await hitl_service.get_pending_requests_for_message(user_message_id)
for req in pending_hitl:
    await hitl_service.cancel_request(req.request_id)
```

---

## 8. Frontend Design

### 8.1 Current State

The `input_required` card in `task-status-message.tsx` (lines 337-377) is purely informational:

```
┌─────────────────────────────────────────────────┐
│  ⚠ Research Agent    Step 2/3     Input required │
│                                                  │
│  The agent needs additional information to       │
│  continue.                                       │
│                                                  │
│  🕐 2m 30s elapsed                               │
└──────────────────────────────────────────────────┘
```

No text field, no button, no way for the user to respond.

### 8.2 New SSE Event Handling

`useRoomWebhook.ts` gains a handler for the `hitl_input_requested` event:

```typescript
case 'hitl_input_requested': {
    const { request_id, message_id, prompt, prompt_type, choices,
            agent_name, step_number, total_steps } = sseMessage.data

    // Map HITL request data onto the existing task message
    const existingMessages = liveMessagesByRoom[roomId] || []
    const existingMessage = existingMessages.find(m => m.id === message_id)

    if (existingMessage) {
        replaceLiveMessage(roomId, message_id, {
            ...existingMessage,
            hitl_request_id: request_id,
            hitl_prompt: prompt,
            hitl_prompt_type: prompt_type,
            hitl_choices: choices,
        })
    }
    break
}
```

The `MessageData` interface in `room-messages.tsx` extends with:

```typescript
export interface MessageData {
    // ... existing fields ...

    // HITL fields (populated by hitl_input_requested SSE event)
    hitl_request_id?: string
    hitl_prompt?: string
    hitl_prompt_type?: 'text' | 'choice' | 'confirmation'
    hitl_choices?: string[]
    hitl_responded?: boolean    // True after user submits reply
    hitl_user_input?: string   // The user's reply text (for display)
}
```

### 8.3 SSE Reconnect Catch-Up

When the SSE connection re-establishes, the hook fetches pending HITL requests:

```typescript
// In useRoomSSE reconnect handler:
const onReconnect = async () => {
    // Re-fetch messages to catch any missed updates
    await messagesQuery.refetch()

    // Fetch pending HITL requests (may have been missed during disconnect)
    const pending = await fetch(`/api/rooms/${roomId}/hitl/pending`)
    const { requests } = await pending.json()
    for (const req of requests) {
        // Merge HITL data onto the corresponding task message
        replaceLiveMessage(roomId, req.continuation_message_id, {
            hitl_request_id: req.request_id,
            hitl_prompt: req.prompt,
            hitl_prompt_type: req.prompt_type,
            hitl_choices: req.choices,
        })
    }
}
```

### 8.4 Inline Reply Form

The `TaskStatusMessage` component gains two new props:

```typescript
interface TaskStatusMessageProps {
    // ... existing props ...
    hitlRequestId?: string               // From HITL SSE event
    onHitlReply?: (requestId: string, userInput: string) => Promise<void>
}
```

The `input_required` branch evolves from display-only to interactive:

**State: Awaiting Input (form visible)**

```
┌─────────────────────────────────────────────────┐
│  ⚠ Research Agent    Step 2/3     Input required │
│                                                  │
│  Which date range should I search?               │
│  The dataset covers 2020-2026.                   │
│                                                  │
│  ┌───────────────────────────────────────┐       │
│  │ Type your reply...                    │       │
│  └───────────────────────────────────────┘       │
│                                      [ Submit ]  │
│                                                  │
│  🕐 2m 30s elapsed                               │
└──────────────────────────────────────────────────┘
```

**State: Submitting (form disabled)**

```
┌─────────────────────────────────────────────────┐
│  ⚠ Research Agent    Step 2/3    Sending reply...│
│                                                  │
│  Which date range should I search?               │
│                                                  │
│  ┌───────────────────────────────────────┐       │
│  │ 2023-2025                      [grayed]       │
│  └───────────────────────────────────────┘       │
│                                      [ ⏳ ... ]  │
└──────────────────────────────────────────────────┘
```

**State: Waiting for Agent (after submission)**

```
┌─────────────────────────────────────────────────┐
│  ⚠ Research Agent    Step 2/3   Waiting for agent│
│                                                  │
│  Which date range should I search?               │
│                                                  │
│  You replied: "2023-2025"                        │
│                                                  │
│  ⏳ Processing your reply...                     │
│                                                  │
│  🕐 3m 10s elapsed                               │
└──────────────────────────────────────────────────┘
```

**State: Resolved (normal terminal card)**

When the agent completes, the standard `task_update` SSE event arrives and the card transitions to the completed state (or becomes a `MessageBubble` via the existing `shouldRenderTaskAsAgent` logic).

### 8.5 Prompt Type Variants

| `prompt_type` | UI Control | Submit Behavior |
|---|---|---|
| `text` | `<textarea>` + Submit button | User types free text, clicks Submit |
| `choice` | Radio buttons or button group | Clicking an option auto-submits |
| `confirmation` | "Yes" / "No" button pair | Clicking either button auto-submits |

### 8.6 Why Inline (Not the Chat Input Bar)

1. **Clarity of intent** — the submit button is physically attached to the question. No ambiguity about what the user is replying to.
2. **Multiple concurrent HITL** — if two agents both return `input_required`, the timeline shows two cards with independent reply forms.
3. **Separate channel** — the reply hits `POST /hitl/respond`, not `POST /sendMessage`. No Supervisor planning, no new agent messages created.
4. **Semantic difference** — an HITL reply is a scoped interaction with one agent, not a top-level chat message. Keeping it inline reflects that.

### 8.7 Staleness Fix

The `useRoomWebhook.ts` processing-placeholder logic has a 2-minute stale check that
skips showing the generic "AI Agents Processing..." placeholder if the triggering user
message is older than 2 minutes. This check does **not** affect rendered `TaskStatusMessage`
cards — those manage their own state independently.

> **Verify before implementing**: There is currently no confirmed 10-minute task-card
> auto-fail timer in `useRoomWebhook.ts`. The existing staleness logic applies only to
> the processing placeholder, not to rendered task cards. If a task-card auto-fail timer
> is found during implementation, apply the exemption below.

If a timer is found that converts non-terminal task-status messages to `failed` locally,
it must be updated to exempt interactive states:

```typescript
// If found: exempt input-required (and auth-required) from any local auto-fail timer
if (!isTerminalState(taskStatus)
    && !isInteractiveState(taskStatus)  // don't auto-fail HITL tasks
    && elapsedMinutes > STALE_THRESHOLD) {
    taskStatus = 'failed'
}
```

Additionally, the frontend's `SSEMessage` type union and `ProcessingStatus` type must
gain the new HITL event names:

```typescript
// src/lib/types/sse.ts

// Add to SSEMessage.type union:
type: '...' | 'hitl_input_requested' | 'hitl_status_update'

// Add to ProcessingStatus:
export type ProcessingStatus =
  | "processing"
  | "completed"
  | "canceled"
  | "failed"
  | "rejected"
  | "rate_limited"
  | "awaiting_input"   // NEW: loop paused for HITL

// Add to PROCESSING_STATUS constant:
export const PROCESSING_STATUS = {
  ...
  AWAITING_INPUT: "awaiting_input",
} as const

// PROCESSING_DONE_STATUSES: do NOT add awaiting_input here.
// When the frontend sees awaiting_input, it should show the HITL prompt, not clear the spinner.
```

---

## 9. Risk Assessment and Mitigations

### Risk 1: Stale Task Checker Auto-Fails HITL Requests

**Severity: CRITICAL**

HITL pauses can last minutes to hours. Two independent timeout mechanisms will kill the task:

- **Backend**: Stale task checker queries `NON_TERMINAL_STATES` (includes `input_required`) and auto-fails tasks after `task_expiry_hours` (default 4h). When `_mark_task_failed` runs, it updates the task to `failed` and sends SSE — but does NOT clear `pending_continuation`. This creates split-brain: task is `failed` but continuation still exists.
- **Frontend**: Non-terminal tasks older than 10 minutes are locally converted to `failed` on page refresh.

**Mitigations:**

```python
# 1. Backend: Exempt input_required from auto-fail in stale_task_checker.py
#    Add to _should_auto_fail_task():
if task_state in INTERACTIVE_STATES:
    # Check HITL-specific timeout (24h) instead of standard task_expiry_hours (4h)
    hitl_expiry = timedelta(hours=self.hitl_expiry_hours)  # configurable, default 24h
    if utcnow() - task_updated_at < hitl_expiry:
        return False  # Still waiting for user, don't auto-fail

# 2. Backend: If stale checker DOES expire an HITL task, also clear continuation
async def _mark_task_failed(self, message_id, msg, error):
    # ... existing logic ...
    # NEW: Also clear any orphaned continuation
    await db_service.get_and_clear_continuation_on_message(message_id)
    # NEW: Also cancel any pending HITL request
    await hitl_service.cancel_requests_for_message(message_id)

# 3. Frontend: Skip staleness conversion for interactive states
#    (See section 8.7 above)
```

### Risk 2: Parallel Queue Corruption

**Severity: HIGH**

No room-level lock. If user sends a new message while HITL loop is paused, a second
supervisor loop starts independently. Two problems:
- `add_agent_response_to_memory` does non-atomic read-modify-write — second write overwrites the first
- Resumed loop's agents see context from both conversations, producing confused responses

**Mitigations:**

```python
# 1. Add room processing state check in send_message_to_room
async def send_message_to_room(self, request, target_group="room_team"):
    # Check for active HITL pause via HITLService (agent-sourced HITL)
    pending_hitl = await hitl_service.get_pending_requests(request.room_id)
    if pending_hitl:
        # Block the message with a user-facing error
        return RoomCenterUserMessageResponse(
            success=False,
            error="An agent is waiting for your input. "
                  "Please reply to the pending request before sending a new message.",
            pending_hitl_request_id=pending_hitl[0].request_id,
        )

    # 2. Also check the existing CLARIFY guard (covers supervisor-sourced pause
    #    for CLARIFY and, once HITLService is in place, belt-and-suspenders for
    #    supervisor ASK_USER — the two patterns are unified here).
    #
    #    NOTE on unification: CLARIFY sets room.extend_info["pending_clarification_message_id"].
    #    Supervisor ASK_USER (HITL) uses HITLRequest records in MongoDB instead — the
    #    check above via hitl_service.get_pending_requests() covers it.  The two guards
    #    are intentionally separate because they serve different flows:
    #      - pending_clarification_message_id  → user's NEXT free-text message IS the reply
    #      - HITLRequest                       → reply goes through POST /hitl/respond only
    #    A message blocked by the HITL guard must NOT be routed as a clarify-resume.
    room = await db_service.get_room_by_room_id(request.room_id)
    if room:
        pending_clarify_msg_id = (
            room.extend_info.get("pending_clarification_message_id")
            if isinstance(room.extend_info, dict)
            else None
        )
        if pending_clarify_msg_id:
            pending_msg = await db_service.get_room_user_message_by_message_id(
                pending_clarify_msg_id
            )
            if pending_msg and isinstance(pending_msg.extend_info, dict):
                traj = pending_msg.extend_info.get("supervisor_trajectory", {})
                if isinstance(traj, dict) and traj.get("status") == "awaiting_input":
                    return RoomCenterUserMessageResponse(
                        success=False,
                        error="The supervisor is waiting for your input on a pending request.",
                    )
```

### Risk 3: SSE Connection Loss Drops HITL Prompts

**Severity: HIGH**

SSE is fire-and-forget with no catch-up. If connection drops when `hitl_input_requested` fires, the user never sees the prompt.

**Mitigations:**

1. **Persist before emit** — `HITLService.request_input()` saves the `HITLRequest` to MongoDB BEFORE emitting the SSE event (already in the design above)
2. **Catch-up endpoint** — `GET /rooms/{room_id}/hitl/pending` returns all pending requests (already in the design above)
3. **SSE reconnect refetch** — `useRoomSSE` hook calls the catch-up endpoint on reconnection (see section 8.3)
4. **Message query includes HITL state** — when `inquiryRoomMessagesByRoomId` returns messages, the backend joins pending `HITLRequest` data onto `input_required` messages

### Risk 4: Multi-Round `input_required` Suppressed by Webhook Idempotency

**Severity: MEDIUM-HIGH**

The webhook handler tracks `last_notified_state` per message. If an agent returns `input_required` twice (multi-round), the second notification is suppressed because `last_notified_state` already equals `"input_required"`.

**Mitigation:**

```python
# In HITLService._handle_agent_response(), before sending the reply:
await self.database_service.reset_last_notified_state(
    request.continuation_message_id
)
# This clears last_notified_state to None, so the next input_required
# webhook will be treated as a fresh notification
```

### Risk 5: No A2A Reply-to-Task Code Path

**Severity: MEDIUM**

The A2A protocol supports continuing a task via `task_id` + `context_id`, but `a2a_service.py` has no method for this.

**Mitigation:** The `reply_to_task` method is fully specified in section 6.2. Key implementation details:
- Set `message.task_id` and `message.context_id` (never done today)
- Generate a **new** plaintext webhook token, update the stored hash in MongoDB, and include the new plaintext token in `PushNotificationConfig` (the original plaintext token is never stored — only its hash — so it cannot be reused)
- Reuse the existing `message_id` so the webhook handler maps to the correct `RoomAgentMessage`

### Risk 6: Processing Indicator Conflicts with HITL

**Severity: MEDIUM**

When the queue pauses for HITL, the "AI Agents Processing..." indicator stays active while the amber "Input required" card also appears.

**Mitigation:**

```python
# Emit a distinct processing status when entering HITL
await self.sse_manager.send_processing_status(
    room_id, "awaiting_input", user_message_id
)
```

Frontend maps `awaiting_input` to a different indicator: "Waiting for your input to continue" or hides the processing indicator entirely while the HITL prompt is active.

### Risk 7: Cancellation Doesn't Reach Paused HITL

**Severity: MEDIUM**

The `cancelMessage` flag is only checked by the active queue loop. A paused queue never checks it.

**Mitigation:**

```python
# Extend the cancel message handler to also cancel HITL
async def cancel_message(message_id: str):
    # ... existing cancellation flag logic ...

    # NEW: Cancel pending HITL requests associated with this message
    pending = await hitl_service.get_pending_requests_for_message(message_id)
    for req in pending:
        await hitl_service.cancel_request(req.request_id)
    # cancel_request already clears pending_continuation and emits SSE
```

### Risk 8: External Agent May Not Support HITL Properly

**Severity: LOW-MEDIUM**

A2A agents are external services. They might not handle multi-turn `input_required` correctly, or might loop indefinitely.

**Mitigations:**

```python
# 1. Max rounds per task
MAX_HITL_ROUNDS = 3

async def request_input(self, ...):
    # Count existing HITL requests for this continuation
    existing = await self.database_service.count_hitl_requests_for_message(
        continuation_message_id
    )
    if existing >= MAX_HITL_ROUNDS:
        logger.warning("Max HITL rounds exceeded, failing task")
        await self._fail_hitl_task(continuation_message_id)
        return None

# 2. Per-round timeout (separate from overall task expiry)
#    expires_in_hours=1.0 per round, not 24h
```

### Risk 9: IDOR — Missing Room Ownership Check on HITL Endpoints

**Severity: HIGH**

All HITL endpoints are scoped by `room_id` in the URL path. Without an ownership check, any authenticated user who can guess or enumerate a room ID can read pending HITL prompts, submit responses, or cancel requests belonging to another user's room. This is a classic Insecure Direct Object Reference (IDOR) vulnerability.

**Mitigations:**

Every HITL endpoint must call `verify_room_ownership(room_id, user)` — the same guard used by all `room_center.py` endpoints — before performing any business logic. This fetches the room from MongoDB, confirms it exists (404 if not), and verifies `room.room_owner_id == user.user_id` (403 if not). See Section 7.3 for the corrected endpoint code.

```python
# Applied to all three HITL endpoints: /respond, /pending, /cancel
await verify_room_ownership(room_id, user)
```

Additionally, the `user` parameter must be typed as `ClerkUser` (not `str`) to match `get_current_user`'s actual return type and to enable the ownership comparison.

### Risk 10: `auth_required` Requires a Different Protocol Flow

**Severity: HIGH**

The A2A spec defines two interactive task states:
- `input_required` — agent needs textual input from the user; client replies via `message.send` with `task_id`/`context_id`
- `auth_required` — agent needs out-of-band authentication ("Authentication is expected to come out-of-band." — A2A proto spec)

The `reply_to_task()` method (§6.2) that sends a text reply is **correct for
`input_required` but wrong for `auth_required`**.  `auth_required` is not satisfied
by sending a message — the agent is waiting for an OAuth redirect, token injection,
or some other external auth event, after which it transitions itself (or the client
polls until it does).

**Decision: Scope HITL Phase 1 to `input_required` only.**

`auth_required` requires a separate design covering:
- How the frontend surfaces the auth flow (e.g., embedded OAuth popup, "copy-paste token" field)
- How completion is signalled (agent self-transitions after polling, or client calls a separate auth-complete endpoint)
- Whether `HITLService` handles it via a distinct `source == "auth"` branch or a separate service

**Mitigation for Phase 1:**

```python
# In AgentMessageProcessor, only create HITL requests for input_required:
if response_type == "task" and response.get("status") == "input_required":
    return ProcessingResult(
        status=ProcessingStatus.AWAITING_INPUT,
        ...
    )

# auth_required falls through to the existing PAUSED path (treated as a
# push-notification pause — the stale checker will eventually time it out or
# the agent will self-resolve when auth is provided externally).
```

`HITLRequest.source` is typed as `Literal["agent", "supervisor"]` — do **not** add
`"auth"` until the auth flow is designed.

### Risk 11: `interrupt_kind` Is a Load-Bearing Routing Field

**Severity: MEDIUM**

In the unified interrupt design, `interrupt_kind` in the continuation payload is the
sole signal that tells `_resume_supervisor_v2()` how to re-enter the supervisor loop.
If the field is absent, wrong, or corrupted, the wrong resume branch fires:
- A `HITL_SUPERVISOR` continuation resumed as `PUSH_NOTIFICATION` would try to append
  a webhook result that doesn't exist, leaving `hitl_user_reply` unused and the LLM
  without the user's answer.
- A `PUSH_NOTIFICATION` continuation resumed as `HITL_SUPERVISOR` would inject a
  `None` `hitl_user_reply`, producing a confusing "[User replied to your question]: None"
  in the context.

**Mitigations:**

```python
# 1. Validate interrupt_kind on read, not just on write:
raw_kind = continuation.get("interrupt_kind", "push_notification")
try:
    interrupt_kind = InterruptKind(raw_kind)
except ValueError:
    logger.error(
        "Unknown interrupt_kind=%r in continuation for message %s — "
        "defaulting to PUSH_NOTIFICATION",
        raw_kind, paused_message_id,
    )
    interrupt_kind = InterruptKind.PUSH_NOTIFICATION

# 2. Add interrupt_kind to structured logging on every save and resume so
#    mismatches are immediately visible in logs.

# 3. The backward-compatibility default (absent → PUSH_NOTIFICATION) must be
#    explicitly tested: a legacy continuation without the field must resume correctly.
```

### Risk Summary

| # | Risk | Severity | Status |
|---|------|----------|--------|
| 1 | Stale checker auto-fails HITL tasks | CRITICAL | Mitigated: HITL-specific timeout + cleanup |
| 1a | `TrajectoryStatus.AWAITING_INPUT` absent — crash recovery re-runs HITL-paused trajectories | HIGH | Mitigated: new enum value + exclusion guard |
| 2 | Parallel queue corrupts memory | HIGH | Mitigated: unified HITL+CLARIFY block in `send_message_to_room` |
| 3 | SSE drop loses HITL prompt | HIGH | Mitigated: Persist-first + catch-up endpoint |
| 4 | Multi-round notification suppressed | MEDIUM-HIGH | Mitigated: Reset `last_notified_state` on reply |
| 5 | No A2A reply-to-task method | MEDIUM | Mitigated: New `reply_to_task` method |
| 6 | Processing indicator overlap | MEDIUM | Mitigated: New `awaiting_input` status; `processing_message_id` kept set |
| 7 | Cancel doesn't reach paused HITL | MEDIUM | Mitigated: Cancel handler clears HITL |
| 8 | External agent HITL misbehavior | LOW-MEDIUM | Mitigated: Max rounds + per-round timeout |
| 9 | IDOR on HITL endpoints (no room ownership check) | HIGH | Mitigated: `verify_room_ownership()` on all HITL endpoints |
| 10 | `auth_required` uses wrong reply mechanism | HIGH | Mitigated: Phase 1 scoped to `input_required` only; `auth_required` stays on existing push-notification/PAUSED path |
| 11 | `interrupt_kind` is load-bearing routing field | MEDIUM | Mitigated: Validate on read, default to `PUSH_NOTIFICATION`, structured logging |

---

## 10. Migration Plan

> **Unified CLARIFY note:** Phase 1 now also removes the `pending_clarification_message_id`
> chat-input path as part of the refactor. `CLARIFY` cases use `HITLService` and the inline
> form. A backward-compat shim in `send_message_to_room` handles rooms with in-flight legacy
> CLARIFY sessions; the shim is removed after one `task_expiry_hours` cycle.

### Phase 1: Unified Interrupt Foundation + Backend Models (Non-Breaking Refactor)

1. Add `InterruptKind` enum to `models/supervisor_v2.py`
2. Rename `SupervisorExecutor._save_pause_state()` → `_save_interrupted_state(kind=InterruptKind.PUSH_NOTIFICATION, ...)`, adding `interrupt_kind` to the continuation payload. **Backward compat:** if `interrupt_kind` absent on read, default to `PUSH_NOTIFICATION`.
3. Update `_resume_supervisor_v2()` to read `interrupt_kind` and branch (initially only the `PUSH_NOTIFICATION` branch exists — all existing behavior preserved)
4. Remove `ActionType.ASK_USER` (never shipped); remove `RunStatus.CLARIFYING` and `TrajectoryStatus.CLARIFYING` from new code. Add `RunStatus.AWAITING_INPUT` and `TrajectoryStatus.AWAITING_INPUT`.
5. Add `prompt_type` and `choices` fields to `SupervisorAction` (now used by `CLARIFY`, replacing the ASK_USER-only plan).
6. Replace `clarify_user_reply` / `clarify_original_message_id` on `SupervisorTrajectory` with `hitl_user_reply` / `hitl_original_message_id`. Resume code reads both field names for backward compat (see §5.3).
7. Create `models/hitl.py` with `HITLRequest`, `HITLResponse`, `HITLEventType`, `HITLStatus`, `HITLPromptType`
8. Create `hitl_requests` MongoDB collection with indexes
9. Create `services/hitl_service.py` with `HITLService` class (persistence + SSE emission)
10. Add `a2a_service.reply_to_task()` method (with `reference_task_ids`)
11. Add `StepStatus.AWAITING_INPUT` to `models/supervisor_v2.py`
12. Add `SSEProcessingStatus.AWAITING_INPUT`; confirm it is **not** in `PROCESSING_DONE_STATUSES`
13. Add `ProcessingStatus.AWAITING_INPUT` and `QueueResult.AWAITING_INPUT` to V1 modules
14. Add database methods: `create_hitl_request`, `get_hitl_request`, `update_hitl_request`, `get_pending_hitl_requests`, `get_pending_continuation_on_message`
15. Update crash-recovery guard to exclude both `AWAITING_INPUT` and the legacy `"clarifying"` string (see §5.3)
16. Add backward-compat shim in `send_message_to_room` for rooms with legacy `pending_clarification_message_id`
17. No existing supervisor behavior changed yet — pure refactor + new models

### Phase 2: Unified `CLARIFY` via HITLService (Replaces Old CLARIFY + Implements ASK_USER)

1. Replace the existing `case ActionType.CLARIFY` in `SupervisorExecutor.run()` with the unified HITLService path (§7.3) — removes the synthesis-message emission and `pending_clarification_message_id` set
2. Add `HITL_SUPERVISOR` branch to `_resume_supervisor_v2()` — reads `hitl_user_reply` (or legacy `clarify_user_reply`) from trajectory and injects into conversation context
3. Add `case RunStatus.AWAITING_INPUT: pass` to `RoomMessageCenter._handle_v2_run_result()`
4. Test: Supervisor `decide_next` → `CLARIFY` (pre-plan OR mid-loop) → HITLRequest created → SSE emitted → user replies via form → loop resumes with reply in context

### Phase 3: V2 Queue Integration (Agent `input_required`)

1. In `ResponseProcessor.handle_sync_response()`, detect `input_required` task state
2. In `AgentMessageProcessor.process_single_message`, propagate `AWAITING_INPUT` with `a2a_task_id`, `a2a_context_id`, `status_message`
3. Add `a2a_task_id`, `a2a_context_id`, `status_message` fields to `V2StepResult`
4. In `SupervisorExecutor._dispatch_targets()`, map `ProcessingStatus.AWAITING_INPUT` → `V2StepResult(status=StepStatus.AWAITING_INPUT)` — skip for `auth_required`
5. In `SupervisorExecutor.run()` DELEGATE case, detect `StepStatus.AWAITING_INPUT` results:
   - Call `hitl_service.request_input()` to get `HITLRequest` (and `request_id`)
   - Call `_save_interrupted_state(kind=InterruptKind.HITL_AGENT, hitl_request_id=...)` per awaiting agent
   - Return `RunStatus.AWAITING_INPUT`
6. In `RoomMessageCenter._resume_supervisor_v2()`, add `HITL_AGENT` branch (same as `PUSH_NOTIFICATION` — appends webhook result)
7. Test: Agent returns `input_required` → state saved with `HITL_AGENT` kind → HITLRequest created → SSE emitted

### Phase 4: HITL Response Endpoint

1. Create `api/hitl.py` with `POST /respond`, `GET /pending`, `POST /{request_id}/cancel`
2. Wire `handle_response` → `a2a_service.reply_to_task()` for agent source
3. Test end-to-end: Agent `input_required` → user replies via endpoint → agent completes → webhook → `_resume_supervisor_v2(kind=HITL_AGENT)` resumes loop

### Phase 5: Risk Mitigations (Backend)

1. Update stale task checker: HITL-specific timeout; clear continuation + cancel HITL request on auto-fail
2. Reset `last_notified_state` when sending HITL reply (multi-round fix)
3. Add unified room processing state check in `send_message_to_room` to block new messages during HITL
4. Extend `cancelMessage` handler to also cancel pending HITL requests
5. Add HITL expiry job (or extend stale task checker) to clean up unanswered requests; expiry must also clear `pending_continuation` and emit `hitl_input_expired` SSE
6. Add `interrupt_kind` validation on read with structured logging (Risk 11 mitigation)

### Phase 6: Frontend

1. Add `hitl_input_requested` and `hitl_status_update` SSE event handlers in `useRoomWebhook.ts`
2. Add `awaiting_input` to `ProcessingStatus` type; update `SSEMessage.type` union (NOT in `PROCESSING_DONE_STATUSES`)
3. Add HITL fields to `MessageEntity` and `IncomingMessage` in `src/stores/message-store/types.ts` (the actual normalized store types — not the abstract `MessageData` interface)
4. Add `hitlRequestId` and `onHitlReply` props to `TaskStatusMessage`
5. Build inline reply form in the `input_required` branch of `TaskStatusMessage` — this now also handles supervisor `CLARIFY` prompts (same component, same form, `source` field tells the display which label to use)
6. Add `prompt_type` variants (text, choice, confirmation)
7. Add SSE reconnect catch-up via `GET /hitl/pending`
8. Verify any staleness auto-fail timer; exempt interactive states (see §8.7)
9. Add HITL reply API call function in `src/lib/api/room.ts`
10. Map `awaiting_input` processing status to "Waiting for your input to continue" UI
11. Remove the chat-input placeholder that previously told users to "reply in the chat box" for CLARIFY questions — the inline form replaces it

### Phase 7: HITL Turn Recording in Room Memory

1. After `HITLService.handle_response()` marks the request as responded, write `ConversationTurn` pair to room memory (see §10.1)
2. Add `add_hitl_exchange_to_memory()` helper
3. Ensure HITL exchange appears in future `conversation_context` snapshots

### Phase 8: Legacy Shim Removal

1. Remove the `pending_clarification_message_id` shim from `send_message_to_room` (after one `task_expiry_hours` cycle has passed since Phase 2 deployment — all in-flight legacy CLARIFY sessions will have timed out)
2. Remove `clarify_user_reply` / `clarify_original_message_id` backward-compat read fallback from resume code

---

## 10.1 HITL Turn Recording in Room Memory

**Context:** `CONTEXT_MEMORY_SYSTEM_DESIGN.md` defines room memory as a series of
`ConversationTurn` records.  HITL exchanges (the agent's question and the user's
reply) are not synthesis boundaries, so they currently bypass room memory entirely.
Future supervisor loops and agents would have no record of what was asked or answered.

**Required:** After `HITLService.handle_response()` marks the request as `responded`,
write two turns to `RoomMemory.conversation_history`:

```python
# In HITLService.handle_response(), after successful routing:

# 1. The agent's question (already displayed; record as assistant turn)
hitl_question_turn = ConversationTurn(
    turn_id=f"hitl_q_{request.request_id}",
    role="assistant",
    content=request.prompt,
    agent_id=request.agent_id,         # None for supervisor-sourced
    agent_name=request.agent_name,     # "Supervisor" for supervisor-sourced
    turn_type="hitl_question",         # custom type marker
    created_at=request.created_at,
    was_successful=True,
)

# 2. The user's reply (record as user turn)
hitl_reply_turn = ConversationTurn(
    turn_id=f"hitl_r_{request.request_id}",
    role="user",
    content=user_input,
    turn_type="hitl_reply",
    created_at=utcnow(),
    was_successful=True,
)

await room_memory_service.add_turns_to_memory(
    room_id=request.room_id,
    turns=[hitl_question_turn, hitl_reply_turn],
)
```

**Why this matters:**
- Future `conversation_context` snapshots passed to the supervisor LLM will include
  the HITL exchange, giving the supervisor visibility into what information was requested
  and provided.
- The `room_summary` (Knowledge Block) is updated at synthesis boundaries; it will
  absorb the HITL exchange as context on the next SYNTHESIZE or DONE action.
- The `CONTEXT_MEMORY_SYSTEM_DESIGN.md` should be updated to document `hitl_question`
  and `hitl_reply` as valid `turn_type` values.

---

## 11. What This Does NOT Change

| Component | Status |
|---|---|
| `Room` model | Unchanged (though `pending_clarification_message_id` field is retired from active use) |
| `RoomAgentMessage` model | Unchanged (continuation data extended, not restructured) |
| `RoomUserMessage` model | Unchanged |
| `SupervisorExecutor` main loop structure | Minimal change — `CLARIFY` case replaces old chat-path behavior; `AWAITING_INPUT` detection in DELEGATE case added |
| `SupervisorExecutor._save_pause_state()` | **Renamed/refactored** → `_save_interrupted_state(kind=PUSH_NOTIFICATION, ...)` (same behavior, unified signature) |
| `SupervisorExecutor._checkpoint_trajectory()` | Unchanged — best-effort per-step crash-recovery checkpoint; separate from interrupt state |
| `RoomMessageCenter._resume_supervisor_v2()` | Minimal change — reads `interrupt_kind` and branches; `PUSH_NOTIFICATION` branch is existing code |
| `QueueExecutor` / V1 queue loop | Unchanged for supervisor rooms; gains V1-only `AWAITING_INPUT` case for non-supervisor rooms |
| SSE streaming infrastructure | Unchanged (new event types use existing broadcast mechanism) |
| Push notification / webhook flow | Unchanged (HITL_AGENT resumes via same webhook → `_resume_supervisor_v2()` path as PUSH_NOTIFICATION) |
| `a2a_service` (existing methods) | Unchanged (new `reply_to_task()` method added, existing methods untouched) |
| `send_message_to_room` | Minimal change (room-level HITL block check; legacy CLARIFY shim during migration window) |
| `rate_limit_service` | Unchanged |
| Frontend chat input / `SendMessage` | Unchanged (HITL replies use separate endpoint) |
| `MessageBubble` component | Unchanged |
| `CLARIFY` action name | Unchanged — the action name stays `CLARIFY`; only the routing changes (HITLService instead of chat-input path) |

---

## 12. Summary

The HITL design adds an **event-driven human interaction channel** to the Supervisor V2
Pattern, enabling two scenarios:

1. **Supervisor `CLARIFY`** — Supervisor asks the user a question, whether before dispatching any agents (pre-plan) or between dispatch rounds (mid-loop). New unified routing: `ActionType.CLARIFY` → `_save_interrupted_state(kind=HITL_SUPERVISOR)` → `HITLService` → inline reply form → `_handle_supervisor_response()` patches `hitl_user_reply` on trajectory → `_resume_supervisor_v2(kind=HITL_SUPERVISOR)` re-runs loop with user's answer in context. Replaces the previous `CLARIFY` + `ASK_USER` split and the `pending_clarification_message_id` chat-input path.
2. **Agent `input_required`** — A2A agent needs user input mid-execution. New `ProcessingStatus.AWAITING_INPUT` → `StepStatus.AWAITING_INPUT` → `_save_interrupted_state(kind=HITL_AGENT)` → `HITLService` → `reply_to_task()` → webhook → `_resume_supervisor_v2(kind=HITL_AGENT)`.

### Key Design Decisions

| Decision | Rationale |
|---|---|
| Dedicated HITL endpoint, not `sendMessage` | Eliminates ambiguity; supports multiple concurrent HITL requests |
| Persist-first, emit-second | Survives SSE connection drops |
| Unified `_save_interrupted_state(kind=...)` | Single save method for all interrupt kinds; adding a new kind requires one enum value + one branch |
| Single `_resume_supervisor_v2()` for all interrupt kinds | All pauses share the same serialize→persist→wait→deserialize→re-run mechanics; only the pre-run injection differs |
| `interrupt_kind` in continuation payload | Clean routing without flag proliferation; backward compat: absent → `PUSH_NOTIFICATION` |
| `CLARIFY` unified — same path for pre-plan and mid-loop | Both cases are identical mechanically; the split was accidental complexity. One `ActionType`, one resume path, one `HITLRequest` record, one frontend component. |
| `ASK_USER` removed | `CLARIFY` subsumes it. Having two action types for the same semantic concept (supervisor asking the user) was unnecessary duplication. |
| `pending_clarification_message_id` chat-input path retired | One persistence mechanism (HITLRequest), one blocking guard (`get_pending_requests()`), one frontend component. Backward-compat shim handles in-flight sessions during migration. |
| `HITLService` for request lifecycle | Persistence, SSE emission, expiry, cancel — separate from routing which lives in `_resume_supervisor_v2()` |
| `TrajectoryStatus.AWAITING_INPUT` excluded from crash-recovery resume | HITL-paused trajectories must only resume via user reply, not server restart. Legacy `"clarifying"` status is also excluded for the same reason. |
| `SSEProcessingStatus.AWAITING_INPUT` excluded from `PROCESSING_DONE_STATUSES` | Keeps `processing_message_id` on the room so cancel still works and page-refresh can show pending HITL prompt |
| Phase 1 scoped to `input_required` only | `auth_required` requires a different (out-of-band) protocol flow; designing it separately avoids baking wrong assumptions into `HITLRequest` |
| HITL exchanges recorded in room memory | Future supervisor loops see the full HITL Q&A in `conversation_context`; prevents agents from being confused about information they already obtained |
| `referenceTaskIds` included in `reply_to_task` | Hybrid-mode agents that model continuations as new tasks use this to pull prior context; harmless for agents that do in-place continuation |

### New Components

| Component | Type | Purpose |
|---|---|---|
| `services/hitl_service.py` | Service | Manages HITL request/response lifecycle |
| `api/hitl.py` | REST API | `POST /respond`, `GET /pending`, `POST /cancel` |
| `models/hitl.py` | Models | `HITLRequest`, `HITLResponse`, event types |
| `a2a_service.reply_to_task()` | Method | Sends follow-up message to existing A2A task (with `referenceTaskIds`) |
| `hitl_requests` collection | MongoDB | Stores pending/responded/expired HITL requests |
| `InterruptKind` | Enum | `PUSH_NOTIFICATION` / `HITL_AGENT` / `HITL_SUPERVISOR` — routing key in continuation payload |
| `SupervisorExecutor._save_interrupted_state()` | Method | **Replaces** `_save_pause_state()` — single save method for all interrupt kinds |
| `RunStatus.AWAITING_INPUT` | Enum value | V2 run paused for HITL (replaces `RunStatus.CLARIFYING`) |
| `StepStatus.AWAITING_INPUT` | Enum value | Agent step paused for `input_required` |
| `TrajectoryStatus.AWAITING_INPUT` | Enum value | Trajectory status for HITL pause (excluded from crash-recovery resume) |
| `SSEProcessingStatus.AWAITING_INPUT` | Enum value | Room-level processing status when HITL is active (NOT in `PROCESSING_DONE_STATUSES`) |
| `SupervisorTrajectory.hitl_user_reply` | Field | User's answer for `HITL_SUPERVISOR` kind (replaces `clarify_user_reply`) |
| `add_hitl_exchange_to_memory()` | Method | Records HITL Q&A as `ConversationTurn` entries in room memory |
| Inline reply form | Frontend | Interactive form inside `TaskStatusMessage` amber card — used for both agent and supervisor HITL prompts |
| `awaiting_input` processing status | Frontend | New `ProcessingStatus` value; maps to "Waiting for your input" UI; excluded from done-statuses |

### Removed / Retired

| Component | Reason |
|---|---|
| `ActionType.ASK_USER` | Merged into `ActionType.CLARIFY` |
| `RunStatus.CLARIFYING` | Replaced by `RunStatus.AWAITING_INPUT` |
| `TrajectoryStatus.CLARIFYING` | Replaced by `TrajectoryStatus.AWAITING_INPUT` |
| `SupervisorTrajectory.clarify_user_reply` | Replaced by `hitl_user_reply` (read fallback kept for backward compat) |
| `SupervisorTrajectory.clarify_original_message_id` | Replaced by `hitl_original_message_id` |
| `pending_clarification_message_id` on Room | Retired; HITLRequest in `hitl_requests` is the single source of truth for pending supervisor questions |
| `supervisor_v2_clarify_resume=True` extend_info routing | Retired; all supervisor question replies go through `POST /hitl/respond` |
