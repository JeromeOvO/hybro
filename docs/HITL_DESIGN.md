# Human-in-the-Loop (HITL) Design

**Date**: February 12, 2026
**Status**: Proposal
**Scope**: Add event-driven human-in-the-loop support to the Supervisor Pattern for multi-agent chat rooms
**Depends on**: [Supervisor Pattern Design](./SUPERVISOR_PATTERN_DESIGN.md)

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
3. **Zero impact on normal chat** — `send_message_to_room` is never modified; HITL replies go through a dedicated endpoint
4. **Reuse existing infrastructure** — queue pause/resume (`_save_queue_continuation` / `resume_queue_from_continuation`), SSE broadcasting, webhook-triggered resume
5. **Support all three HITL scenarios** — agent-initiated (`input_required`), Supervisor-initiated (mid-plan question), and pre-plan clarification
6. **Resilient to disconnection** — HITL state is persisted to MongoDB; prompts survive SSE drops and page refreshes

---

## 3. Architecture Overview

```
                    HITL TRIGGER
                    (Agent returns input_required
                     OR Supervisor asks user)
                            │
                            ▼
            ┌───────────────────────────────┐
            │  Queue loop detects pause      │
            │  _save_queue_continuation()    │
            │  (includes a2a_task_id,        │
            │   a2a_context_id, request_id)  │
            └──────────────┬────────────────┘
                           │
                           ▼
            ┌───────────────────────────────┐
            │  HITLService.request_input()   │
            │                                │
            │  1. Create HITLRequest record   │
            │  2. Persist to MongoDB          │
            │  3. Emit SSE event:             │
            │     hitl_input_requested        │
            └──────────────┬────────────────┘
                           │
                  (SSE to frontend)
                           │
                           ▼
            ┌───────────────────────────────┐
            │  Frontend renders inline       │
            │  reply form inside amber card  │
            └──────────────┬────────────────┘
                           │
                  (User types reply)
                           │
                           ▼
            ┌───────────────────────────────┐
            │  POST /rooms/{room_id}/hitl/   │
            │       respond                  │
            │  { request_id, user_input }    │
            └──────────────┬────────────────┘
                           │
                           ▼
            ┌───────────────────────────────┐
            │  HITLService.handle_response() │
            │                                │
            │  Branch on source:             │
            │                                │
            │  source == "agent":            │
            │    → a2a_service.reply_to_task │
            │    → Agent processes reply     │
            │    → Webhook fires on complete │
            │    → resume_queue_from_        │
            │      continuation() [EXISTING] │
            │                                │
            │  source == "supervisor":       │
            │    → Inject user answer into   │
            │      supervisor context        │
            │    → Resume queue directly     │
            └───────────────────────────────┘
```

### Key Insight: Different Resume Triggers by Source

| HITL Source | What Resumes the Queue | New Code Needed |
|---|---|---|
| **Agent** (`input_required`) | The A2A webhook, when the agent reaches a terminal state after processing the user's reply | Only `a2a_service.reply_to_task()` — the existing webhook → `resume_queue_from_continuation` path handles the rest |
| **Supervisor** (`ask_user`) | The HITL response handler directly calls `resume_queue_from_continuation()` | The resume call + context injection |
| **Clarification** (pre-plan) | No queue to resume — the user's reply starts normal Supervisor planning | No queue changes; just a clarification message in conversation history |

---

## 4. Three HITL Scenarios

### Scenario 1: Pre-Plan Clarification (Supervisor Asks Before Planning)

The Supervisor decides it can't route the message without more info. This is the `strategy="clarify"` path in the Supervisor Pattern design.

**Flow:**
1. Supervisor returns `SupervisorPlan(strategy="clarify", steps=[])`
2. No agent messages are created, no queue starts
3. The Supervisor's clarification question is emitted as a pseudo-agent message (similar to coordinator summary)
4. The system goes back to IDLE immediately
5. When the user replies, the normal `send_message_to_room` flow starts — the Supervisor sees the original message + clarification in conversation history and can plan properly

**Why this is easy:** No paused state, no queue interruption, no HITL endpoint needed. It's just a round-trip through existing chat. The Supervisor's memory of why it asked is captured naturally in conversation history.

**No changes to the HITL system needed** — this scenario is fully handled by the Supervisor Pattern design.

### Scenario 2: Agent Returns `input_required` (Mid-Execution)

An A2A agent says "I need more info from the user to continue." This is the hard case.

**Detailed flow:**

```
Step 1: Queue processes agent step
        → a2a_service.send_message_to_tracked_agent()
        → Agent returns task with status = input_required

Step 2: Queue detects input_required
        → Returns ProcessingResult(ProcessingStatus.AWAITING_INPUT)
        → Queue loop saves continuation via _save_queue_continuation()
        → Returns QueueResult.AWAITING_INPUT

Step 3: HITLService creates request
        → Persists HITLRequest to MongoDB (with a2a_task_id, context_id)
        → Emits SSE: hitl_input_requested

Step 4: Frontend renders inline reply form
        → User sees agent's question inside amber card with text input

Step 5: User submits reply
        → POST /rooms/{room_id}/hitl/respond { request_id, user_input }

Step 6: HITLService.handle_response()
        → Loads HITLRequest, sees source == "agent"
        → Calls a2a_service.reply_to_task(task_id, context_id, user_input)
        → Marks HITLRequest as "responded"

Step 7: Agent processes reply
        → Task transitions: input_required → working → completed
        → Agent sends webhook on terminal state

Step 8: Webhook handler (EXISTING)
        → Detects terminal state
        → Calls resume_queue_from_continuation()
        → Queue resumes with remaining steps
```

Steps 7-8 are the **existing** push notification resume path — zero new queue code.

### Scenario 3: Supervisor Asks User Mid-Execution (Between Steps)

The Supervisor Review after step N decides: "This result is confusing. Before proceeding to step N+1, I should ask the user."

**Detailed flow:**

```
Step 1: Agent completes step N
        → Supervisor review runs

Step 2: Supervisor review returns action="ask_user"
        with user_question="The research agent found conflicting data.
              Should I use the 2025 or 2026 dataset?"

Step 3: Queue saves continuation
        → _save_queue_continuation() with source="supervisor"

Step 4: HITLService creates request
        → Persists HITLRequest (source="supervisor", no a2a_task_id)
        → Emits SSE: hitl_input_requested

Step 5: User replies via inline form
        → POST /hitl/respond

Step 6: HITLService.handle_response()
        → Loads HITLRequest, sees source == "supervisor"
        → Injects user_input into context for step N+1
        → Calls resume_queue_from_continuation() DIRECTLY
        → Queue resumes
```

No webhook involved — the user's API call is the event that triggers resume.

---

## 5. Data Models

### 5.1 HITLRequest

Persisted to MongoDB. Represents a pending request for human input.

```python
class HITLEventType(str, Enum):
    """Events in the human-in-the-loop lifecycle."""
    INPUT_REQUESTED = "hitl_input_requested"
    INPUT_RECEIVED  = "hitl_input_received"
    INPUT_EXPIRED   = "hitl_input_expired"
    INPUT_CANCELED  = "hitl_input_canceled"


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

### 5.3 Supervisor Model Extensions

```python
class SupervisorReview(BaseModel):
    """Result of the Supervisor reviewing a completed step."""
    action: Literal["continue", "revise", "retry", "skip", "ask_user"]  # NEW: ask_user
    reasoning: str
    revised_steps: list[SupervisorStep] | None = None
    retry_with_refinement: str | None = None
    user_question: str | None = None   # NEW: question to ask if action == "ask_user"
    prompt_type: HITLPromptType = HITLPromptType.TEXT  # NEW
    choices: list[str] | None = None   # NEW: for prompt_type == "choice"
```

### 5.4 Queue Extensions

```python
class ProcessingStatus(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    CANCELED = "canceled"
    PAUSED = "paused"              # Queue paused for push notification task
    AWAITING_INPUT = "awaiting_input"  # NEW: Queue paused for HITL


class QueueResult(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"              # Webhook will resume
    CANCELED = "canceled"
    AWAITING_INPUT = "awaiting_input"  # NEW: HITL reply will resume
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

### 5.6 Storage

HITLRequest records are stored in a new `hitl_requests` MongoDB collection, indexed by:
- `request_id` (unique)
- `room_id` + `status` (for pending request lookup)
- `expires_at` + `status` (for expiry job)

The continuation data in `pending_continuation` is extended with HITL context:

```python
continuation_data = {
    "remaining_queue": serialized_queue,
    "room_id": room_id,
    "user_message_id": user_message_id,
    "request_user_id": request_user_id,
    "current_agent_id": current_agent.agent_id,
    "current_agent_name": current_agent.agent_card.name,
    # NEW fields for HITL:
    "hitl_request_id": request.request_id,     # Links to HITLRequest
    "awaiting_user_input": True,                # Distinguishes from push notification pause
}
```

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

        # 2. Mark as responded
        await self.database_service.update_hitl_request(
            request_id,
            status=HITLStatus.RESPONDED,
            user_input=user_input,
            responded_at=utcnow(),
        )

        # 3. Emit status update SSE
        await self.sse_manager.send_hitl_event(
            room_id=room_id,
            event_type=HITLEventType.INPUT_RECEIVED,
            request=request,
        )

        # 4. Route based on source
        if request.source == "agent":
            await self._handle_agent_response(request, user_input)
        elif request.source == "supervisor":
            await self._handle_supervisor_response(request, user_input)

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

    async def _handle_supervisor_response(
        self, request: HITLRequest, user_input: str
    ) -> None:
        """Resume queue with user's answer injected into Supervisor context."""
        await room_message_center.resume_queue_from_continuation(
            message_id=request.continuation_message_id,
            task_result_text=None,
            hitl_user_input=user_input,  # NEW parameter
        )

    async def get_pending_requests(self, room_id: str) -> list[HITLRequest]:
        """Get all pending HITL requests for a room (for SSE reconnect catch-up)."""
        return await self.database_service.get_pending_hitl_requests(room_id)

    async def cancel_request(self, request_id: str) -> None:
        """Cancel a pending HITL request."""
        request = await self.database_service.get_hitl_request(request_id)
        if request and request.status == HITLStatus.PENDING:
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
    # 1. Load the existing RoomAgentMessage for webhook config
    msg = await self.database_service.get_room_agent_message_by_message_id(message_id)
    agent_url = msg.agent_url

    # 2. Reconstruct push notification config (reuse same webhook URL/token)
    webhook_url = settings.WEBHOOK_BASE_URL
    push_config = PushNotificationConfig(
        id=message_id,
        url=f"{webhook_url}/api/v1/webhooks/a2a/{message_id}",
        token=msg.webhook_token_hash,  # Already stored
    )

    # 3. Build message with EXISTING task_id and context_id
    reply_message = Message(
        role="user",
        parts=[TextPart(text=user_input)],
        task_id=task_id,        # Continue existing task
        context_id=context_id,  # Same conversation context
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

### 7.1 Queue Loop: Detecting `input_required`

In `_process_single_agent_message`, after the agent call returns:

```python
# In _handle_sync_response_for_room, after a2a_service.send_message_to_tracked_agent:
if response_type == "task" and response.get("status") == "input_required":
    # Extract HITL context from the response
    task_data = response.get("task", {})
    return (
        True,                           # success
        None,                           # no response text yet
        message_id,                     # for continuation
        "input_required",               # NEW: signal to queue loop
        task_data.get("id"),            # a2a_task_id
        task_data.get("context_id"),    # a2a_context_id
        extract_status_message(task_data),  # agent's question
    )
```

In `_process_agent_message_queue`:

```python
elif result.status == ProcessingStatus.AWAITING_INPUT:
    # HITL: Agent needs user input — save continuation and request input
    if not is_direct_chat:
        await self._queue_next_messages(current_message, message_queue, room_id)

    if result.message_id:
        # 1. Save queue continuation (same as push notification)
        await self._save_queue_continuation(
            message_id=result.message_id,
            message_queue=message_queue,
            room_id=room_id,
            user_message_id=user_message_id,
            request_user_id=request_user_id,
            current_agent=agent,
        )

        # 2. Create HITL request (NEW)
        await hitl_service.request_input(
            room_id=room_id,
            user_message_id=user_message_id,
            source="agent",
            prompt=result.status_message or "The agent needs additional information.",
            agent_id=current_message.agent_id,
            agent_name=agent.agent_card.name if agent else "Agent",
            a2a_task_id=result.a2a_task_id,
            a2a_context_id=result.a2a_context_id,
            continuation_message_id=result.message_id,
        )

    # 3. Emit AWAITING_INPUT processing status (replaces "processing" indicator)
    await self.sse_manager.send_processing_status(
        room_id, "awaiting_input", user_message_id
    )

    return QueueResult.AWAITING_INPUT
```

### 7.2 Supervisor Review: `ask_user` Action

In the Supervisor review handler (after each agent step):

```python
review = await supervisor_service.review_step(plan, step, result, remaining)

if review.action == "ask_user":
    # Save continuation
    await self._save_queue_continuation(...)

    # Create HITL request from Supervisor
    await hitl_service.request_input(
        room_id=room_id,
        user_message_id=user_message_id,
        source="supervisor",
        prompt=review.user_question,
        prompt_type=review.prompt_type,
        choices=review.choices,
        source_step_id=step.step_id,
        continuation_message_id=current_message.message_id,
    )

    await self.sse_manager.send_processing_status(
        room_id, "awaiting_input", user_message_id
    )

    return QueueResult.AWAITING_INPUT
```

### 7.3 REST Endpoint

```python
# api/hitl.py

from fastapi import APIRouter, Depends, HTTPException

router = APIRouter(prefix="/rooms/{room_id}/hitl", tags=["hitl"])

@router.post("/respond")
async def respond_to_hitl_request(
    room_id: str,
    body: HITLResponseRequest,
    user_id: str = Depends(get_current_user),
):
    """User responds to an HITL prompt."""
    result = await hitl_service.handle_response(
        room_id=room_id,
        request_id=body.request_id,
        user_input=body.user_input,
        user_id=user_id,
    )
    return result

@router.get("/pending")
async def get_pending_hitl_requests(
    room_id: str,
    user_id: str = Depends(get_current_user),
):
    """Get pending HITL requests for a room (SSE reconnect catch-up)."""
    requests = await hitl_service.get_pending_requests(room_id)
    return {"requests": [r.model_dump(mode="json") for r in requests]}

@router.post("/{request_id}/cancel")
async def cancel_hitl_request(
    room_id: str,
    request_id: str,
    user_id: str = Depends(get_current_user),
):
    """Cancel a pending HITL request."""
    await hitl_service.cancel_request(request_id)
    return {"status": "canceled"}
```

### 7.4 Cancellation Integration

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

The frontend post-processing logic in `useRoomWebhook.ts` currently converts non-terminal tasks older than 10 minutes to "failed". This must be updated:

```typescript
// BEFORE: All non-terminal tasks older than 10 min → failed
if (!isTerminalState(taskStatus) && elapsedMinutes > STALE_THRESHOLD) {
    taskStatus = 'failed'
}

// AFTER: Exempt input_required (it's a valid waiting state, not stuck)
if (!isTerminalState(taskStatus)
    && !isInteractiveState(taskStatus)  // NEW: don't auto-fail HITL tasks
    && elapsedMinutes > STALE_THRESHOLD) {
    taskStatus = 'failed'
}
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

No room-level lock. If user sends a new message while HITL queue is paused, a second queue starts independently. Two problems:
- `add_agent_response_to_memory` does non-atomic read-modify-write — second write overwrites the first
- Resumed queue's agents see context from both conversations, producing confused responses

**Mitigations:**

```python
# 1. Add room processing state check in send_message_to_room
async def send_message_to_room(self, request, target_group="room_team"):
    # Check for active HITL pause
    pending_hitl = await hitl_service.get_pending_requests(request.room_id)
    if pending_hitl:
        # Option A: Block the message with a user-facing error
        return RoomCenterUserMessageResponse(
            success=False,
            error="An agent is waiting for your input. "
                  "Please reply to the pending request before sending a new message.",
            pending_hitl_request_id=pending_hitl[0].request_id,
        )
        # Option B: Queue the message for processing after HITL completes
        # (more complex, requires a room-level message queue)

# 2. Persist room-level processing state
#    Extend the existing processing_message_id mechanism:
await db_service.update_room_processing_status(
    room_id,
    message_id,
    processing_state="awaiting_input",  # NEW state
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
- Re-include `PushNotificationConfig` with the same webhook URL
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

### Risk Summary

| # | Risk | Severity | Status |
|---|------|----------|--------|
| 1 | Stale checker auto-fails HITL tasks | CRITICAL | Mitigated: HITL-specific timeout + cleanup |
| 2 | Parallel queue corrupts memory | HIGH | Mitigated: Block new messages during HITL |
| 3 | SSE drop loses HITL prompt | HIGH | Mitigated: Persist-first + catch-up endpoint |
| 4 | Multi-round notification suppressed | MEDIUM-HIGH | Mitigated: Reset `last_notified_state` on reply |
| 5 | No A2A reply-to-task method | MEDIUM | Mitigated: New `reply_to_task` method |
| 6 | Processing indicator overlap | MEDIUM | Mitigated: New `awaiting_input` status |
| 7 | Cancel doesn't reach paused HITL | MEDIUM | Mitigated: Cancel handler clears HITL |
| 8 | External agent HITL misbehavior | LOW-MEDIUM | Mitigated: Max rounds + per-round timeout |

---

## 10. Migration Plan

### Phase 1: Backend Foundation (Non-Breaking)

1. Create `models/hitl.py` with `HITLRequest`, `HITLResponse`, `HITLEventType`, `HITLStatus`, `HITLPromptType`
2. Create `hitl_requests` MongoDB collection with indexes
3. Create `services/hitl_service.py` with `HITLService` class (persistence + SSE emission)
4. Add `a2a_service.reply_to_task()` method
5. Add new `ProcessingStatus.AWAITING_INPUT` and `QueueResult.AWAITING_INPUT` values
6. Add database methods: `create_hitl_request`, `get_hitl_request`, `update_hitl_request`, `get_pending_hitl_requests`
7. No existing code modified yet

### Phase 2: Queue Integration (Agent `input_required`)

1. In `_handle_sync_response_for_room`, detect `input_required` status and return it to the queue loop
2. In `_process_agent_message_queue`, handle `ProcessingStatus.AWAITING_INPUT` — save continuation + call `hitl_service.request_input()`
3. In `process_room_user_message`, handle `QueueResult.AWAITING_INPUT` (similar to `PAUSED`)
4. Add SSE processing status `"awaiting_input"`
5. Test: Agent returns `input_required` → queue pauses → HITLRequest created → SSE emitted

### Phase 3: HITL Response Endpoint

1. Create `api/hitl.py` with `POST /respond`, `GET /pending`, `POST /{request_id}/cancel`
2. Wire `handle_response` → `a2a_service.reply_to_task()` for agent source
3. Test end-to-end: Agent `input_required` → user replies via endpoint → agent completes → webhook resumes queue

### Phase 4: Risk Mitigations (Backend)

1. Update stale task checker: HITL-specific timeout, clear continuation on auto-fail
2. Reset `last_notified_state` when sending HITL reply (multi-round fix)
3. Add room processing state check in `send_message_to_room` to block new messages during HITL
4. Extend `cancelMessage` handler to also cancel pending HITL requests
5. Add HITL expiry job (or extend stale task checker) to clean up unanswered requests

### Phase 5: Supervisor `ask_user` Action

1. Add `ask_user` to `SupervisorReview.action` enum
2. In Supervisor review handler, create HITL request when `action == "ask_user"`
3. Wire `handle_response` → `resume_queue_from_continuation()` for supervisor source
4. Test: Supervisor review → ask_user → user replies → queue resumes

### Phase 6: Frontend

1. Add `hitl_input_requested` and `hitl_status_update` SSE event handlers in `useRoomWebhook.ts`
2. Extend `MessageData` interface with HITL fields
3. Add `hitlRequestId` and `onHitlReply` props to `TaskStatusMessage`
4. Build inline reply form in the `input_required` branch of `TaskStatusMessage`
5. Add `prompt_type` variants (text, choice, confirmation)
6. Add SSE reconnect catch-up via `GET /hitl/pending`
7. Fix staleness logic to exempt `input_required` from auto-fail
8. Add HITL reply API call function in `src/lib/api/room.ts`
9. Map `awaiting_input` processing status to appropriate UI indicator

---

## 11. What This Does NOT Change

| Component | Status |
|---|---|
| `Room` model | Unchanged |
| `RoomAgentMessage` model | Unchanged (continuation data extended, not restructured) |
| `RoomUserMessage` model | Unchanged |
| Message queue execution loop | Unchanged (new `AWAITING_INPUT` case alongside existing `PAUSED`) |
| SSE streaming infrastructure | Unchanged (new event types use existing broadcast mechanism) |
| Push notification / webhook flow | Unchanged (HITL reuses the same webhook resume path) |
| `a2a_service` (existing methods) | Unchanged (new `reply_to_task` method added, existing methods untouched) |
| `send_message_to_room` | Minimal change (room-level HITL block check only) |
| `rate_limit_service` | Unchanged |
| Frontend chat input / `SendMessage` | Unchanged (HITL replies use separate endpoint) |
| `MessageBubble` component | Unchanged |

---

## 12. Summary

The HITL design adds an **event-driven human interaction channel** to the Supervisor Pattern, enabling three scenarios:

1. **Pre-plan clarification** — Supervisor asks the user before planning (handled by existing `strategy="clarify"`, no new infrastructure)
2. **Agent `input_required`** — A2A agent needs user input mid-execution (new `HITLService` + `reply_to_task` + queue `AWAITING_INPUT`)
3. **Supervisor `ask_user`** — Supervisor pauses between steps to ask the user (new `SupervisorReview.action` + direct queue resume)

### Key Design Decisions

| Decision | Rationale |
|---|---|
| Dedicated HITL endpoint, not `sendMessage` | Eliminates ambiguity; supports multiple concurrent HITL requests |
| Persist-first, emit-second | Survives SSE connection drops |
| Reuse webhook resume for agent HITL | Zero new queue resume code; agent webhook handles it |
| Direct resume for supervisor HITL | No agent involved; user's reply is the resume trigger |
| Inline reply form, not chat input | Clear intent; no ambiguity; supports parallel HITL prompts |
| `hitl_requests` collection | Queryable lifecycle; clean expiry; reconnect catch-up |

### New Components

| Component | Type | Purpose |
|---|---|---|
| `services/hitl_service.py` | Service | Manages HITL request/response lifecycle |
| `api/hitl.py` | REST API | `POST /respond`, `GET /pending`, `POST /cancel` |
| `models/hitl.py` | Models | `HITLRequest`, `HITLResponse`, event types |
| `a2a_service.reply_to_task()` | Method | Sends follow-up message to existing A2A task |
| `hitl_requests` collection | MongoDB | Stores pending/responded/expired HITL requests |
| Inline reply form | Frontend | Interactive form inside `TaskStatusMessage` amber card |
