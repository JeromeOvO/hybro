# Long-Running A2A Tasks Design Document

## 1. Overview

### Problem Statement

When a client sends a message to an A2A agent, the agent may return either:
- **Message**: Direct response (fast, seconds)
- **Task**: Async task that may take seconds to hours

Currently, the backend doesn't properly handle `Task` responses for long-running operations. When an agent returns a Task with `state=working`, the backend tries to extract content immediately, resulting in "No message content" on the frontend.

### Goals

1. Support A2A agents that run tasks from seconds to hours
2. Provide real-time updates to users when tasks complete
3. Handle network failures gracefully with fallback mechanisms
4. Use A2A native models as single source of truth
5. Secure webhook callbacks to prevent spoofing
6. Handle all A2A task states including interactive states (input_required, auth_required)
7. Prevent resource exhaustion with quotas and TTLs

### Non-Goals

- Real-time streaming of task progress (future enhancement)
- Task cancellation from frontend (future enhancement)
- Multi-tenant webhook routing (single deployment assumed)

---

## 2. Architecture

### System Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              FRONTEND                                        │
│                                                                              │
│  1. User sends message                                                       │
│  2. Receives task_id (if long-running)                                      │
│  3. Shows "Task in progress..." UI                                          │
│  4. Receives SSE when task completes                                        │
│  5. Displays result                                                          │
└─────────────────────────────────────────────────────────────────────────────┘
                              │ SSE ▲
                              ▼     │
┌─────────────────────────────────────────────────────────────────────────────┐
│                              BACKEND                                         │
│                                                                              │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐  │
│  │ Message API │    │ Task Store  │    │ Webhook     │    │ Stale Task  │  │
│  │             │───▶│ (MongoDB)   │◀───│ Handler     │    │ Checker     │  │
│  └─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘  │
│         │                                     ▲                   │         │
│         │                                     │                   │         │
│         ▼                                     │                   ▼         │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         A2A Client                                   │   │
│  │  - Sends message with push_notification_config                      │   │
│  │  - Polls stale tasks as fallback                                    │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                              │                 ▲
                              ▼                 │ Webhook callback
┌─────────────────────────────────────────────────────────────────────────────┐
│                           A2A AGENT (e.g., OpenClaw)                         │
│                                                                              │
│  1. Receives message + webhook config                                       │
│  2. Returns Task with state=working                                         │
│  3. Processes (seconds to hours)                                            │
│  4. POSTs to webhook URL when done                                          │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Key Design Decisions

1. **Webhook-based**: Agent pushes updates to backend (not polling)
2. **Fallback polling**: Background job polls stale tasks if webhook fails
3. **A2A native models**: Store Task as-is in MongoDB, single source of truth
4. **SSE for frontend**: Push updates when webhook arrives

---

## 3. Data Model (MongoDB)

### Collection: `a2a_tasks`

The `task` field stores the complete A2A `Task` object as-is using `task.model_dump()`. 
This ensures the A2A SDK types remain the single source of truth.

```javascript
{
  _id: ObjectId,                      // MongoDB internal ID (used as internal_id)
  
  // Our metadata (not part of A2A spec)
  room_id: String,                    // Room this task belongs to
  user_id: String,                    // User who initiated the task
  agent_url: String,                  // Agent URL (for fallback polling)
  webhook_token_hash: String,         // HMAC-SHA256 hash of webhook token (not plaintext!)
  
  // Notification tracking (prevent duplicate SSE)
  last_notified_state: String,        // Last state we sent SSE for (idempotency)
  
  // A2A Task - stored as-is from a2a.types.Task, SINGLE SOURCE OF TRUTH
  // Serialized via task.model_dump(), deserialized via Task.model_validate()
  task: {
    id: String,                       // Agent's task ID (a2a.types.Task.id)
    context_id: String,               // a2a.types.Task.context_id
    status: {                         // a2a.types.TaskStatus
      state: String,                  // a2a.types.TaskState enum value
      message: Object,                // Optional a2a.types.Message
      timestamp: String               // ISO 8601
    },
    artifacts: [                      // a2a.types.Artifact[] - present when completed
      {
        artifact_id: String,
        name: String,
        description: String,
        parts: [...]                  // a2a.types.Part[]
      }
    ],
    history: [...],                   // a2a.types.Message[] - message history
    metadata: {...}                   // Agent-specific metadata
  },
  
  // Timestamps
  created_at: ISODate,
  updated_at: ISODate
}
```

### Task State Categories

Tasks are categorized by state for different handling:

| Category | States | Description |
|----------|--------|-------------|
| **Pending** | `submitted`, `working` | Task is being processed |
| **Interactive** | `input_required`, `auth_required` | Task needs user action |
| **Terminal** | `completed`, `failed`, `canceled`, `rejected` | Task is done |

### Indexes

```javascript
// Primary queries
db.a2a_tasks.createIndex({ room_id: 1 })
db.a2a_tasks.createIndex({ user_id: 1 })
db.a2a_tasks.createIndex({ "task.status.state": 1 })

// Prevent duplicate tasks from same agent
db.a2a_tasks.createIndex(
  { agent_url: 1, "task.id": 1 }, 
  { unique: true, sparse: true }
)

// Stale task detection (includes interactive states that may timeout)
db.a2a_tasks.createIndex(
  { updated_at: 1, "task.status.state": 1 },
  { partialFilterExpression: { 
    "task.status.state": { $in: ["submitted", "working", "input_required", "auth_required"] } 
  }}
)

// TTL index: Auto-delete completed tasks after 30 days
db.a2a_tasks.createIndex(
  { updated_at: 1 },
  { 
    expireAfterSeconds: 2592000,  // 30 days
    partialFilterExpression: { 
      "task.status.state": { $in: ["completed", "failed", "canceled", "rejected"] } 
    }
  }
)
```

---

## 4. API Design

### 4.1 Send Message (Existing, Modified)

**Endpoint:** `POST /api/rooms/{room_id}/messages`

**Response (when agent returns Task):**

```json
{
  "type": "task",
  "internal_id": "507f1f77bcf86cd799439011",
  "task_id": "agent-task-123",
  "status": "working",
  "message": "Task submitted. You'll be notified when complete."
}
```

### 4.2 Get Task Status

**Endpoint:** `GET /api/tasks/{internal_id}`

**Response:**

```json
{
  "internal_id": "507f1f77bcf86cd799439011",
  "status": "completed",
  "task": {
    "id": "agent-task-123",
    "status": { "state": "completed", "timestamp": "..." },
    "artifacts": [...]
  },
  "created_at": "2024-01-31T10:00:00Z",
  "updated_at": "2024-01-31T10:05:00Z",
  "retry_after_seconds": null
}
```

**Note:** `retry_after_seconds` is returned for pending tasks to hint optimal polling interval:
- `submitted`/`working`: 30 seconds
- `input_required`/`auth_required`: 60 seconds (user action needed)
- Terminal states: `null`
```

### 4.3 List Room Tasks

**Endpoint:** `GET /api/rooms/{room_id}/tasks`

**Response:**

```json
{
  "tasks": [
    {
      "internal_id": "...",
      "agent_name": "OpenClaw Agent",
      "status": "working",
      "created_at": "..."
    }
  ]
}
```

### 4.4 Webhook Callback

**Endpoint:** `POST /webhooks/a2a/{internal_id}`

**Headers:**
```
Authorization: Bearer <webhook_token>
Content-Type: application/json
```

**Request Body (A2A Task or StreamResponse):**

```json
{
  "id": "agent-task-123",
  "context_id": "...",
  "status": {
    "state": "completed",
    "timestamp": "2024-01-31T10:05:00Z"
  },
  "artifacts": [
    {
      "artifact_id": "...",
      "name": "response",
      "parts": [{ "text": "Here is the result..." }]
    }
  ]
}
```

**Response:**
```json
{ "status": "accepted" }
```

---

## 5. Backend Implementation

### 5.0 Constants and State Definitions

```python
# services/a2a_constants.py

from enum import Enum
from a2a.types import TaskState

class TaskStateCategory(Enum):
    """Helper enum for categorizing A2A task states."""
    PENDING = "pending"
    INTERACTIVE = "interactive"
    TERMINAL = "terminal"

# Use A2A TaskState enum values for state sets
PENDING_STATES = {TaskState.submitted, TaskState.working}
INTERACTIVE_STATES = {TaskState.input_required, TaskState.auth_required}
TERMINAL_STATES = {TaskState.completed, TaskState.failed, TaskState.canceled, TaskState.rejected}

# States that need monitoring/polling
NON_TERMINAL_STATES = PENDING_STATES | INTERACTIVE_STATES

def get_state_category(state: TaskState) -> TaskStateCategory:
    """Get the category for an A2A task state."""
    if state in PENDING_STATES:
        return TaskStateCategory.PENDING
    if state in INTERACTIVE_STATES:
        return TaskStateCategory.INTERACTIVE
    return TaskStateCategory.TERMINAL

def is_terminal_state(state: TaskState) -> bool:
    """Check if a task state is terminal (task is done)."""
    return state in TERMINAL_STATES
```

### 5.1 Task Service

```python
# services/a2a_task_service.py

from a2a.types import Task, TaskState
from bson import ObjectId
from datetime import datetime, timezone, timedelta
from typing import Optional
import secrets
import hashlib
import hmac

from .a2a_constants import NON_TERMINAL_STATES, TERMINAL_STATES


class A2ATaskService:
    # Configurable limits
    MAX_TASKS_PER_USER = 100       # Max concurrent non-terminal tasks per user
    MAX_TASKS_PER_ROOM = 50        # Max concurrent non-terminal tasks per room
    
    def __init__(self, db, webhook_signing_key: str):
        self.collection = db.a2a_tasks
        self.webhook_signing_key = webhook_signing_key.encode()
    
    def _hash_token(self, token: str) -> str:
        """Hash webhook token for storage (never store plaintext)."""
        return hmac.new(
            self.webhook_signing_key, 
            token.encode(), 
            hashlib.sha256
        ).hexdigest()
    
    def _verify_token(self, token: str, stored_hash: str) -> bool:
        """Verify token against stored hash (constant-time comparison)."""
        computed_hash = self._hash_token(token)
        return hmac.compare_digest(computed_hash, stored_hash)
    
    async def check_task_limits(self, user_id: str, room_id: str) -> None:
        """
        Check if user/room can create more tasks.
        Raises ValueError if limits exceeded.
        """
        # Convert TaskState enums to strings for MongoDB query
        non_terminal_state_values = [s.value for s in NON_TERMINAL_STATES]
        
        user_count = await self.collection.count_documents({
            "user_id": user_id,
            "task.status.state": {"$in": non_terminal_state_values}
        })
        if user_count >= self.MAX_TASKS_PER_USER:
            raise ValueError(f"User has too many pending tasks ({user_count}). Please wait for some to complete.")
        
        room_count = await self.collection.count_documents({
            "room_id": room_id,
            "task.status.state": {"$in": non_terminal_state_values}
        })
        if room_count >= self.MAX_TASKS_PER_ROOM:
            raise ValueError(f"Room has too many pending tasks ({room_count}). Please wait for some to complete.")
    
    async def create_task(
        self,
        room_id: str,
        user_id: str,
        agent_url: str,
        task: Task,
    ) -> tuple[str, str]:
        """
        Create new task record.
        Returns (internal_id, webhook_token) - token is returned once for sending to agent.
        """
        # Check limits before creating
        await self.check_task_limits(user_id, room_id)
        
        # Generate token, store only hash
        webhook_token = secrets.token_urlsafe(32)
        
        doc = {
            "room_id": room_id,
            "user_id": user_id,
            "agent_url": agent_url,
            "webhook_token_hash": self._hash_token(webhook_token),
            "last_notified_state": None,
            "task": task.model_dump(),
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        result = await self.collection.insert_one(doc)
        return str(result.inserted_id), webhook_token
    
    async def update_task(self, internal_id: str, task: Task) -> bool:
        """
        Update task from webhook or polling.
        Returns True if updated, False if not found.
        """
        result = await self.collection.update_one(
            {"_id": ObjectId(internal_id)},
            {
                "$set": {
                    "task": task.model_dump(),
                    "updated_at": datetime.now(timezone.utc),
                }
            }
        )
        return result.modified_count > 0
    
    async def update_notified_state(self, internal_id: str, state: str) -> bool:
        """
        Mark that we've sent SSE for this state.
        Returns True if this is a new notification (state changed).
        """
        result = await self.collection.update_one(
            {
                "_id": ObjectId(internal_id),
                "last_notified_state": {"$ne": state}  # Only update if different
            },
            {"$set": {"last_notified_state": state}}
        )
        return result.modified_count > 0
    
    async def get_task(self, internal_id: str) -> Optional[dict]:
        """Get task document by internal ID."""
        try:
            doc = await self.collection.find_one({"_id": ObjectId(internal_id)})
            if doc:
                doc["internal_id"] = str(doc.pop("_id"))
                doc["task"] = Task.model_validate(doc["task"])
            return doc
        except Exception:
            return None
    
    async def verify_webhook_token(self, internal_id: str, token: str) -> bool:
        """Verify webhook token for a task."""
        doc = await self.collection.find_one(
            {"_id": ObjectId(internal_id)},
            {"webhook_token_hash": 1}
        )
        if not doc or not doc.get("webhook_token_hash"):
            return False
        return self._verify_token(token, doc["webhook_token_hash"])
    
    async def get_tasks_for_room(self, room_id: str, limit: int = 50) -> list[dict]:
        """Get tasks for a room, newest first."""
        cursor = self.collection.find(
            {"room_id": room_id},
            {"webhook_token_hash": 0}  # Don't expose token hash
        ).sort("created_at", -1).limit(limit)
        
        tasks = []
        async for doc in cursor:
            doc["internal_id"] = str(doc.pop("_id"))
            doc["task"] = Task.model_validate(doc["task"])
            tasks.append(doc)
        return tasks
    
    async def get_stale_tasks(self, stale_minutes: int = 30) -> list[dict]:
        """Get tasks that haven't been updated recently (includes interactive states)."""
        threshold = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
        non_terminal_state_values = [s.value for s in NON_TERMINAL_STATES]
        cursor = self.collection.find({
            "task.status.state": {"$in": non_terminal_state_values},
            "updated_at": {"$lt": threshold}
        })
        
        tasks = []
        async for doc in cursor:
            doc["internal_id"] = str(doc.pop("_id"))
            doc["task"] = Task.model_validate(doc["task"])
            tasks.append(doc)
        return tasks
    
    async def get_expired_tasks(self, max_age_hours: int = 4) -> list[dict]:
        """Get tasks that have been non-terminal for too long (auto-fail candidates)."""
        threshold = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        non_terminal_state_values = [s.value for s in NON_TERMINAL_STATES]
        cursor = self.collection.find({
            "task.status.state": {"$in": non_terminal_state_values},
            "created_at": {"$lt": threshold}
        })
        
        tasks = []
        async for doc in cursor:
            doc["internal_id"] = str(doc.pop("_id"))
            doc["task"] = Task.model_validate(doc["task"])
            tasks.append(doc)
        return tasks
    
    async def touch_task(self, internal_id: str) -> None:
        """Update timestamp without changing task (for stale detection)."""
        await self.collection.update_one(
            {"_id": ObjectId(internal_id)},
            {"$set": {"updated_at": datetime.now(timezone.utc)}}
        )
```

### 5.2 A2A Service (Updated)

```python
# services/a2a_service.py (additions)

from a2a.types import (
    AgentCard,
    Message,
    MessageSendParams,
    MessageSendConfiguration,
    PushNotificationConfig,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)
from urllib.parse import urlparse

from .a2a_constants import TERMINAL_STATES, INTERACTIVE_STATES, is_terminal_state


class A2AService:
    def __init__(
        self, 
        task_service: A2ATaskService, 
        webhook_base_url: str,
        allowed_agent_hosts: set[str] = None,  # Optional allowlist
    ):
        self.task_service = task_service
        self.webhook_base_url = webhook_base_url
        self.allowed_agent_hosts = allowed_agent_hosts
        
        # Validate webhook_base_url on init
        self._validate_webhook_url()
    
    def _validate_webhook_url(self) -> None:
        """Validate webhook base URL is properly configured."""
        parsed = urlparse(self.webhook_base_url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"Invalid webhook_base_url: {self.webhook_base_url}")
        if parsed.netloc in ("localhost", "127.0.0.1", "0.0.0.0"):
            logger.warning(
                f"webhook_base_url points to localhost ({self.webhook_base_url}). "
                "Agents may not be able to reach this URL."
            )
    
    def _validate_agent_url(self, agent_url: str) -> None:
        """Validate agent URL is trusted (optional allowlist)."""
        if not self.allowed_agent_hosts:
            return  # No allowlist configured
        parsed = urlparse(agent_url)
        if parsed.netloc not in self.allowed_agent_hosts:
            raise ValueError(f"Agent URL not in allowlist: {agent_url}")
    
    async def send_message_with_task_tracking(
        self,
        room_id: str,
        user_id: str,
        agent_card: AgentCard,
        message: Message,
    ) -> dict:
        """
        Send message to agent with task tracking for long-running operations.
        
        Returns:
            For Message response: {"type": "message", "content": "..."}
            For Task response: {"type": "task", "internal_id": "...", "status": "..."}
            For Interactive states: {"type": "task", "status": "input_required", ...}
        """
        
        # Validate agent URL before proceeding
        self._validate_agent_url(agent_card.url)
        
        # 1. Create placeholder task record to get internal_id and webhook token
        placeholder_task = Task(
            id="pending",
            context_id=message.context_id or str(uuid4()),
            status=TaskStatus(state=TaskState.submitted),
        )
        
        try:
            internal_id, webhook_token = await self.task_service.create_task(
                room_id=room_id,
                user_id=user_id,
                agent_url=agent_card.url,
                task=placeholder_task,
            )
        except ValueError as e:
            # Task limit exceeded
            raise HTTPException(status_code=429, detail=str(e))
        
        # 2. Build request with push notification config
        push_config = None
        if self._supports_push_notifications(agent_card):
            push_config = PushNotificationConfig(
                id=internal_id,
                url=f"{self.webhook_base_url}/webhooks/a2a/{internal_id}",
                token=webhook_token,
            )
        
        payload = MessageSendParams(
            message=message,
            configuration=MessageSendConfiguration(
                acceptedOutputModes=["text/plain"],
                push_notification_config=push_config,
            ),
        )
        
        # 3. Send to agent
        try:
            a2a_client = await self.create_a2a_client(agent_card)
            response = await a2a_client.send_message(...)
        except Exception as e:
            # Mark task as failed IMMEDIATELY (don't wait for stale checker)
            failed_task = Task(
                id="failed",
                context_id=placeholder_task.context_id,
                status=TaskStatus(
                    state=TaskState.failed,
                    message=Message(
                        role="agent",
                        parts=[TextPart(text=f"Failed to contact agent: {str(e)}")]
                    ),
                ),
            )
            await self.task_service.update_task(internal_id, failed_task)
            raise
        
        result = response.root.result
        
        # 4. Handle Message response (fast path)
        if result.kind == "message":
            # Create completed task with message as artifact
            completed_task = self._message_to_completed_task(result)
            await self.task_service.update_task(internal_id, completed_task)
            
            return {
                "type": "message",
                "internal_id": internal_id,
                "content": self._extract_text(result),
            }
        
        # 5. Handle Task response (async path)
        if result.kind == "task":
            # Update with real task from agent
            await self.task_service.update_task(internal_id, result)
            
            state = result.status.state
            
            # If already terminal, return content
            if is_terminal_state(state):
                return {
                    "type": "message",
                    "internal_id": internal_id,
                    "content": self._extract_text_from_task(result),
                    "status": state,
                }
            
            # Handle interactive states
            if state in INTERACTIVE_STATES:
                return {
                    "type": "task",
                    "internal_id": internal_id,
                    "task_id": result.id,
                    "status": state,
                    "requires_input": state == "input_required",
                    "requires_auth": state == "auth_required",
                    "message": self._extract_status_message(result),
                }
            
            # Still processing - client should wait for webhook/SSE
            return {
                "type": "task",
                "internal_id": internal_id,
                "task_id": result.id,
                "status": state,
            }
        
        raise ValueError(f"Unexpected response kind: {result.kind}")
    
    def _supports_push_notifications(self, agent_card: AgentCard) -> bool:
        return (
            agent_card.capabilities 
            and getattr(agent_card.capabilities, "pushNotifications", False)
        )
    
    def _extract_status_message(self, task: Task) -> Optional[str]:
        """Extract human-readable message from task status."""
        if task.status.message and task.status.message.parts:
            for part in task.status.message.parts:
                if hasattr(part, "text"):
                    return part.text
        return None
```

### 5.3 Webhook Handler

```python
# api/webhooks.py

from fastapi import APIRouter, HTTPException, Header, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from a2a.types import Task, TaskState
from slowapi import Limiter
from slowapi.util import get_remote_address

from .a2a_constants import TERMINAL_STATES, INTERACTIVE_STATES, is_terminal_state

router = APIRouter()

# Rate limiting: 100 requests/minute per IP
limiter = Limiter(key_func=get_remote_address)


@router.post("/webhooks/a2a/{internal_id}")
@limiter.limit("100/minute")
async def handle_a2a_webhook(
    request: Request,
    internal_id: str,
    payload: dict,
    background_tasks: BackgroundTasks,
    authorization: str = Header(default=""),
    task_service: A2ATaskService = Depends(get_task_service),
    sse_manager: SSEManager = Depends(get_sse_manager),
):
    """
    Receive task updates from A2A agents.
    
    Security: Validates Bearer token against stored hash.
    Idempotency: Safe to call multiple times with same status.
    Rate Limited: 100 requests/minute per IP.
    """
    
    # 1. Extract and validate token (hash-based, not plaintext comparison)
    token = authorization.replace("Bearer ", "") if authorization else ""
    
    if not token:
        raise HTTPException(status_code=401, detail="Missing authorization token")
    
    if not await task_service.verify_webhook_token(internal_id, token):
        raise HTTPException(status_code=401, detail="Invalid token")
    
    # 2. Parse and validate A2A Task
    try:
        updated_task = Task.model_validate(payload)
    except Exception as e:
        logger.warning(f"Invalid webhook payload for task {internal_id}: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")
    
    # 3. Get current task to check state
    current = await task_service.get_task(internal_id)
    if not current:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # 4. Don't update if already terminal (idempotency)
    current_state = current["task"].status.state
    if is_terminal_state(current_state):
        return {"status": "already_terminal", "state": current_state}
    
    # 5. Update task
    await task_service.update_task(internal_id, updated_task)
    
    # 6. Notify frontend via SSE (with idempotency check)
    new_state = updated_task.status.state
    should_notify = (
        is_terminal_state(new_state) or 
        new_state in INTERACTIVE_STATES
    )
    
    if should_notify:
        background_tasks.add_task(
            notify_task_update,
            internal_id=internal_id,
            task=updated_task,
            room_id=current["room_id"],
            user_id=current["user_id"],
            task_service=task_service,
            sse_manager=sse_manager,
        )
    
    return {"status": "accepted"}


async def notify_task_update(
    internal_id: str,
    task: Task,
    room_id: str,
    user_id: str,
    task_service: A2ATaskService,
    sse_manager: SSEManager,
):
    """
    Send SSE notification when task state changes.
    Uses idempotency tracking to prevent duplicate notifications.
    """
    
    state = task.status.state
    
    # Check if we already notified for this state (prevents duplicates from webhook + stale checker)
    is_new_notification = await task_service.update_notified_state(internal_id, state)
    if not is_new_notification:
        logger.debug(f"Skipping duplicate notification for task {internal_id} state {state}")
        return
    
    content = None
    error = None
    requires_input = False
    requires_auth = False
    status_message = None
    
    if state == "completed" and task.artifacts:
        content = extract_text_from_artifacts(task.artifacts)
    
    elif state == "failed":
        error = extract_error_message(task) or "Task failed"
    
    elif state == "rejected":
        error = extract_error_message(task) or "Task was rejected by the agent"
    
    elif state == "canceled":
        error = "Task was canceled"
    
    elif state == "input_required":
        requires_input = True
        status_message = extract_status_message(task)
    
    elif state == "auth_required":
        requires_auth = True
        status_message = extract_status_message(task) or "Authentication required"
    
    await sse_manager.send_task_update(
        room_id=room_id,
        data={
            "type": "task_update",
            "internal_id": internal_id,
            "status": state,
            "content": content,
            "error": error,
            "requires_input": requires_input,
            "requires_auth": requires_auth,
            "status_message": status_message,
        }
    )


def extract_text_from_artifacts(artifacts: list) -> str:
    """Extract text content from A2A artifacts with robust type handling."""
    texts = []
    for artifact in artifacts:
        if not artifact.parts:
            continue
        for part in artifact.parts:
            # Handle different part type structures
            text = None
            if hasattr(part, "text") and part.text:
                text = part.text
            elif hasattr(part, "root"):
                # Discriminated union wrapper
                root = part.root
                if hasattr(root, "text") and root.text:
                    text = root.text
            if text:
                texts.append(text)
    return "".join(texts) if texts else None


def extract_error_message(task: Task) -> Optional[str]:
    """Extract error message from task status."""
    if not task.status.message:
        return None
    if not task.status.message.parts:
        return None
    for part in task.status.message.parts:
        if hasattr(part, "text") and part.text:
            return part.text
        if hasattr(part, "root") and hasattr(part.root, "text"):
            return part.root.text
    return None


def extract_status_message(task: Task) -> Optional[str]:
    """Extract human-readable status message."""
    return extract_error_message(task)  # Same extraction logic
```

### 5.4 Stale Task Checker (Background Job)

```python
# jobs/stale_task_checker.py

import asyncio
from datetime import datetime, timezone

from a2a.types import Message, Task, TaskState, TaskStatus, TextPart

from .a2a_constants import is_terminal_state, INTERACTIVE_STATES


# Configurable timeouts
STALE_CHECK_MINUTES = 10          # Check tasks not updated in this time
TASK_EXPIRY_HOURS = 4             # Auto-fail tasks older than this
PENDING_TASK_WARNING_HOURS = 1    # Warn (log) after this time


async def check_stale_tasks(
    task_service: A2ATaskService,
    a2a_service: A2AService,
    sse_manager: SSEManager,
):
    """
    Fallback mechanism: Poll agents for tasks that haven't received webhook updates.
    
    Run this every 5 minutes via scheduler (APScheduler, Celery Beat, etc.)
    
    Handles:
    1. Stale tasks: Poll agent for current status
    2. Expired tasks: Auto-fail tasks that have been pending too long
    3. Never-acknowledged tasks: Fail tasks where agent never responded
    """
    
    # 1. Check stale tasks (not updated recently)
    stale_tasks = await task_service.get_stale_tasks(STALE_CHECK_MINUTES)
    logger.info(f"Found {len(stale_tasks)} stale tasks to check")
    
    for stored_task in stale_tasks:
        await _process_stale_task(
            stored_task, task_service, a2a_service, sse_manager
        )
    
    # 2. Auto-fail expired tasks (been pending too long)
    expired_tasks = await task_service.get_expired_tasks(TASK_EXPIRY_HOURS)
    logger.info(f"Found {len(expired_tasks)} expired tasks to auto-fail")
    
    for stored_task in expired_tasks:
        await _auto_fail_expired_task(
            stored_task, task_service, sse_manager
        )


async def _process_stale_task(
    stored_task: dict,
    task_service: A2ATaskService,
    a2a_service: A2AService,
    sse_manager: SSEManager,
):
    """Process a single stale task."""
    internal_id = stored_task["internal_id"]
    agent_url = stored_task["agent_url"]
    agent_task_id = stored_task["task"].id
    created_at = stored_task["created_at"]
    
    # Log warning for long-running tasks
    age_hours = (datetime.now(timezone.utc) - created_at).total_seconds() / 3600
    if age_hours > PENDING_TASK_WARNING_HOURS:
        logger.warning(
            f"Task {internal_id} has been pending for {age_hours:.1f} hours"
        )
    
    # Task was never acknowledged by agent
    if agent_task_id == "pending":
        logger.warning(f"Task {internal_id} never acknowledged, marking failed")
        await _mark_task_failed(
            internal_id=internal_id,
            stored_task=stored_task,
            error="Agent did not acknowledge the task",
            task_service=task_service,
            sse_manager=sse_manager,
        )
        return
    
    try:
        # Poll agent for current status
        a2a_client = await a2a_service.create_a2a_client_for_url(agent_url)
        current_task = await a2a_client.get_task(agent_task_id)
        
        # Update our record
        await task_service.update_task(internal_id, current_task)
        
        # Notify if terminal or interactive state changed
        new_state = current_task.status.state
        if is_terminal_state(new_state) or new_state in INTERACTIVE_STATES:
            await notify_task_update(
                internal_id=internal_id,
                task=current_task,
                room_id=stored_task["room_id"],
                user_id=stored_task["user_id"],
                task_service=task_service,
                sse_manager=sse_manager,
            )
        else:
            # Still working - timestamp already touched by update_task
            logger.debug(f"Task {internal_id} still in state: {new_state}")
            
    except Exception as e:
        logger.warning(f"Failed to poll stale task {internal_id}: {e}")
        # Don't fail the task yet - might be transient network issue
        # Touch timestamp to prevent immediate re-check
        await task_service.touch_task(internal_id)


async def _auto_fail_expired_task(
    stored_task: dict,
    task_service: A2ATaskService,
    sse_manager: SSEManager,
):
    """Auto-fail a task that has been pending too long."""
    internal_id = stored_task["internal_id"]
    created_at = stored_task["created_at"]
    age_hours = (datetime.now(timezone.utc) - created_at).total_seconds() / 3600
    
    logger.error(
        f"Auto-failing task {internal_id} after {age_hours:.1f} hours "
        f"(threshold: {TASK_EXPIRY_HOURS}h)"
    )
    
    await _mark_task_failed(
        internal_id=internal_id,
        stored_task=stored_task,
        error=f"Task expired after {TASK_EXPIRY_HOURS} hours without completion. "
              "The agent may be unresponsive.",
        task_service=task_service,
        sse_manager=sse_manager,
    )


async def _mark_task_failed(
    internal_id: str,
    stored_task: dict,
    error: str,
    task_service: A2ATaskService,
    sse_manager: SSEManager,
):
    """Mark a task as failed and notify the user."""
    failed_task = Task(
        id=stored_task["task"].id,
        context_id=stored_task["task"].context_id,
        status=TaskStatus(
            state=TaskState.failed,
            message=Message(
                role="agent",
                parts=[TextPart(text=error)]
            ),
            timestamp=datetime.now(timezone.utc).isoformat(),
        ),
    )
    
    await task_service.update_task(internal_id, failed_task)
    
    await notify_task_update(
        internal_id=internal_id,
        task=failed_task,
        room_id=stored_task["room_id"],
        user_id=stored_task["user_id"],
        task_service=task_service,
        sse_manager=sse_manager,
    )


# Scheduler setup (example with APScheduler)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()
scheduler.add_job(
    check_stale_tasks,
    'interval',
    minutes=5,
    args=[task_service, a2a_service, sse_manager],
    id='stale_task_checker',
    replace_existing=True,
)
scheduler.start()
```

---

## 6. Frontend Implementation

### 6.1 SSE Types

```typescript
// lib/types/sse.ts
// Note: These TypeScript types mirror the A2A spec for frontend use.
// The backend uses a2a.types directly; these are client-side equivalents.

export type SSEEventType = 
  | "agent_response"      // Direct message from agent
  | "task_submitted"      // Long-running task started
  | "task_update"         // Task status changed
  | "error"
  | "processing_status";

// All possible A2A task states (mirrors a2a.types.TaskState)
export type TaskState = 
  | "submitted" 
  | "working" 
  | "completed" 
  | "failed" 
  | "canceled"
  | "input_required"
  | "rejected"
  | "auth_required";

// States that are still in progress
export const PENDING_STATES: TaskState[] = ["submitted", "working"];

// States that require user action
export const INTERACTIVE_STATES: TaskState[] = ["input_required", "auth_required"];

// States that indicate task is done
export const TERMINAL_STATES: TaskState[] = ["completed", "failed", "canceled", "rejected"];

export function isTerminalState(state: TaskState): boolean {
  return TERMINAL_STATES.includes(state);
}

export function isInteractiveState(state: TaskState): boolean {
  return INTERACTIVE_STATES.includes(state);
}

export interface TaskSubmittedEvent {
  type: "task_submitted";
  data: {
    internal_id: string;
    task_id: string;
    agent_name: string;
    status: "submitted" | "working";
  };
}

export interface TaskUpdateEvent {
  type: "task_update";
  data: {
    internal_id: string;
    status: TaskState;
    content?: string;          // Present if completed
    error?: string;            // Present if failed/rejected/canceled
    requires_input?: boolean;  // True if input_required
    requires_auth?: boolean;   // True if auth_required
    status_message?: string;   // Human-readable status from agent
  };
}
```

### 6.2 Task Status Component

```tsx
// components/task-status-message.tsx

import { useState, useEffect, useCallback, useRef } from "react";
import { Loader2, CheckCircle, XCircle, Clock, AlertTriangle, KeyRound } from "lucide-react";
import { TaskState, isTerminalState, PENDING_STATES, INTERACTIVE_STATES } from "@/lib/types/sse";

interface TaskStatusMessageProps {
  internalId: string;
  agentName: string;
  initialStatus: TaskState;
  onComplete?: (content: string) => void;
  onError?: (error: string) => void;
}

export function TaskStatusMessage({
  internalId,
  agentName,
  initialStatus,
  onComplete,
  onError,
}: TaskStatusMessageProps) {
  const [status, setStatus] = useState<TaskState>(initialStatus);
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [retryAfter, setRetryAfter] = useState<number>(30); // Default 30s
  
  // Track if we've already processed this update (deduplication)
  const processedStates = useRef<Set<string>>(new Set());

  // Handle SSE or poll updates with deduplication
  const handleUpdate = useCallback((data: {
    status: TaskState;
    content?: string;
    error?: string;
    status_message?: string;
  }) => {
    // Deduplicate by status (don't re-render for same state)
    const stateKey = `${data.status}-${data.content || ""}-${data.error || ""}`;
    if (processedStates.current.has(stateKey)) {
      return;
    }
    processedStates.current.add(stateKey);
    
    setStatus(data.status);
    if (data.content) {
      setContent(data.content);
      onComplete?.(data.content);
    }
    if (data.error) {
      setError(data.error);
      onError?.(data.error);
    }
    if (data.status_message) {
      setStatusMessage(data.status_message);
    }
  }, [onComplete, onError]);

  // Elapsed time counter (only for non-terminal states)
  useEffect(() => {
    if (isTerminalState(status)) return;
    
    const interval = setInterval(() => setElapsed(e => e + 1), 1000);
    return () => clearInterval(interval);
  }, [status]);

  // Listen for SSE updates
  useEffect(() => {
    // Subscribe to SSE for this task (implementation depends on your SSE hook)
    // When task_update event arrives for this internalId, call handleUpdate
  }, [internalId, handleUpdate]);

  // Fallback polling with dynamic interval from server
  useEffect(() => {
    if (isTerminalState(status)) return;
    
    const poll = async () => {
      try {
        const res = await fetch(`/api/tasks/${internalId}`);
        const data = await res.json();
        
        // Update retry interval from server hint
        if (data.retry_after_seconds) {
          setRetryAfter(data.retry_after_seconds);
        }
        
        if (data.task.status.state !== status) {
          handleUpdate({
            status: data.task.status.state,
            content: extractContent(data.task),
            error: extractError(data.task),
            status_message: data.task.status?.message?.parts?.[0]?.text,
          });
        }
      } catch (e) {
        console.error("Poll failed:", e);
      }
    };
    
    // Use dynamic interval from server
    const interval = setInterval(poll, retryAfter * 1000);
    return () => clearInterval(interval);
  }, [internalId, status, retryAfter, handleUpdate]);

  const formatTime = (s: number) => {
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s/60)}m ${s%60}s`;
    return `${Math.floor(s/3600)}h ${Math.floor((s%3600)/60)}m`;
  };

  // Completed state
  if (status === "completed" && content) {
    return (
      <div className="space-y-2">
        <div className="flex items-center gap-2 text-green-600 text-sm">
          <CheckCircle className="w-4 h-4" />
          <span>Completed in {formatTime(elapsed)}</span>
        </div>
        <div className="prose prose-sm">{content}</div>
      </div>
    );
  }

  // Failed/Rejected/Canceled states
  if (status === "failed" || status === "rejected" || status === "canceled") {
    const titles = {
      failed: "Task failed",
      rejected: "Task was rejected",
      canceled: "Task was canceled",
    };
    return (
      <div className="p-3 bg-red-50 rounded-lg">
        <div className="flex items-center gap-2 text-red-600">
          <XCircle className="w-4 h-4" />
          <span>{titles[status]}</span>
        </div>
        {error && <p className="text-sm text-red-700 mt-1">{error}</p>}
      </div>
    );
  }

  // Input required state
  if (status === "input_required") {
    return (
      <div className="p-3 bg-yellow-50 rounded-lg border border-yellow-200">
        <div className="flex items-center gap-2 text-yellow-700">
          <AlertTriangle className="w-4 h-4" />
          <span className="font-medium">Input required</span>
        </div>
        <p className="text-sm text-yellow-600 mt-1">
          {statusMessage || "The agent needs additional information to continue."}
        </p>
        <p className="text-xs text-yellow-500 mt-2 flex items-center gap-1">
          <Clock className="w-3 h-3" />
          {formatTime(elapsed)} elapsed
        </p>
      </div>
    );
  }

  // Auth required state
  if (status === "auth_required") {
    return (
      <div className="p-3 bg-orange-50 rounded-lg border border-orange-200">
        <div className="flex items-center gap-2 text-orange-700">
          <KeyRound className="w-4 h-4" />
          <span className="font-medium">Authentication required</span>
        </div>
        <p className="text-sm text-orange-600 mt-1">
          {statusMessage || "Please authenticate to continue."}
        </p>
        <p className="text-xs text-orange-500 mt-2 flex items-center gap-1">
          <Clock className="w-3 h-3" />
          {formatTime(elapsed)} elapsed
        </p>
      </div>
    );
  }

  // Working/Submitted states (default)
  return (
    <div className="flex items-center gap-3 p-3 bg-blue-50 rounded-lg">
      <Loader2 className="w-5 h-5 animate-spin text-blue-600" />
      <div>
        <p className="text-sm font-medium text-blue-800">
          {agentName} is working...
        </p>
        <p className="text-xs text-blue-600 flex items-center gap-1">
          <Clock className="w-3 h-3" />
          {formatTime(elapsed)} elapsed
        </p>
      </div>
    </div>
  );
}

// Helper functions
function extractContent(task: any): string | undefined {
  if (!task.artifacts) return undefined;
  const texts: string[] = [];
  for (const artifact of task.artifacts) {
    for (const part of artifact.parts || []) {
      if (part.text) texts.push(part.text);
      else if (part.root?.text) texts.push(part.root.text);
    }
  }
  return texts.length > 0 ? texts.join("") : undefined;
}

function extractError(task: any): string | undefined {
  const parts = task.status?.message?.parts;
  if (!parts || parts.length === 0) return undefined;
  return parts[0].text || parts[0].root?.text;
}
```

### 6.3 Message Handler Update

```typescript
// hooks/useRoomMessages.ts

const handleSSEEvent = (event: SSEEvent) => {
  switch (event.type) {
    case "agent_response":
      // Existing: direct response
      addMessage({
        id: generateId(),
        role: "agent",
        content: event.data.content,
      });
      break;

    case "task_submitted":
      // New: long-running task
      addMessage({
        id: generateId(),
        role: "agent",
        type: "task",
        internalId: event.data.internal_id,
        agentName: event.data.agent_name,
        status: event.data.status,
      });
      break;

    case "task_update":
      // Update existing task message
      updateMessageByTaskId(event.data.internal_id, {
        status: event.data.status,
        content: event.data.content,
        error: event.data.error,
      });
      break;
  }
};
```

---

## 7. OpenClaw Adapter Changes

The OpenClaw adapter needs to send webhook notifications when tasks complete.

```python
# a2a_adapter/integrations/openclaw.py (additions)

import httpx

async def _execute_command_background(self, task_id, context_id, params):
    """Execute command and send webhook on completion."""
    try:
        # ... existing execution code ...
        
        # On success: send webhook
        await self._send_webhook(
            params=params,
            task_id=task_id,
            status="completed",
            artifacts=[response_artifact],
        )
        
    except Exception as e:
        # On failure: send webhook
        await self._send_webhook(
            params=params,
            task_id=task_id,
            status="failed",
            error=str(e),
        )
        raise


async def _send_webhook(
    self,
    params: MessageSendParams,
    task_id: str,
    status: str,
    artifacts: list = None,
    error: str = None,
):
    """Send webhook notification if configured."""
    config = getattr(params, "configuration", None)
    if not config:
        return
    
    push_config = getattr(config, "push_notification_config", None)
    if not push_config or not push_config.url:
        return
    
    # Build A2A Task payload
    payload = {
        "id": task_id,
        "context_id": self._extract_context_id(params),
        "status": {
            "state": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }
    
    if artifacts:
        payload["artifacts"] = [a.model_dump() for a in artifacts]
    
    if error:
        payload["status"]["message"] = {
            "role": "agent",
            "parts": [{"text": error}],
        }
    
    # Send with retries
    headers = {}
    if push_config.token:
        headers["Authorization"] = f"Bearer {push_config.token}"
    
    async with httpx.AsyncClient() as client:
        for attempt in range(3):
            try:
                response = await client.post(
                    push_config.url,
                    json=payload,
                    headers=headers,
                    timeout=10.0,
                )
                if response.status_code < 300:
                    logger.info(f"Webhook sent for task {task_id}")
                    return
                logger.warning(f"Webhook returned {response.status_code}")
            except Exception as e:
                logger.warning(f"Webhook attempt {attempt+1} failed: {e}")
            
            await asyncio.sleep(2 ** attempt)  # Exponential backoff
        
        logger.error(f"Webhook failed after 3 attempts for task {task_id}")
```

Also update the AgentCard to advertise push notification support:

```python
# examples/08_openclaw_agent.py

agent_card = AgentCard(
    name="OpenClaw Agent",
    # ...
    capabilities=AgentCapabilities(
        streaming=False,
        pushNotifications=True,  # Enable webhook support
    ),
)
```

---

## 8. Security Considerations

### 8.1 Webhook Token Validation

- Each task gets a unique `webhook_token` (32 bytes, URL-safe)
- Token is **hashed** (HMAC-SHA256) before storage - never stored in plaintext
- Token sent to agent in `PushNotificationConfig.token` (returned once at creation)
- Agent must return token in `Authorization: Bearer <token>` header
- Backend validates by hashing received token and comparing to stored hash
- Uses constant-time comparison (`hmac.compare_digest`) to prevent timing attacks

### 8.2 Rate Limiting

- Webhook endpoint rate-limited: **100 requests/minute per IP**
- Task creation rate-limited: **10 tasks/minute per user**
- Implemented via SlowAPI or similar middleware

### 8.3 Input Validation

- Validate all webhook payloads against A2A schema
- Reject malformed requests with 400
- Log but don't expose internal errors to callers

### 8.4 Authorization

- Task status endpoint checks `user_id` matches authenticated user
- Room tasks endpoint checks user has access to room

### 8.5 Agent URL Validation (Optional)

- Configurable allowlist of trusted agent hosts
- Prevents backend from polling arbitrary URLs during stale task checks
- Recommended for production deployments

### 8.6 Webhook URL Configuration

- `webhook_base_url` validated on service initialization
- Warning logged if URL points to localhost (agents can't reach it)
- Should be a publicly accessible URL in production

---

## 9. Failure Handling

### 9.1 Webhook Delivery Failures

| Failure Mode | Mitigation |
|--------------|------------|
| Network error | Agent retries with exponential backoff (3 attempts) |
| Backend down | Agent retries; stale task checker picks up later |
| Invalid token | Agent logs error; task stays "working" until stale check |
| Timeout | Agent retries with shorter timeout |

### 9.2 Stale Task Recovery

- Background job runs every **5 minutes**
- Tasks not updated for >**10 minutes** are polled directly
- Tasks pending for >**1 hour** generate warning logs
- Tasks pending for >**4 hours** are auto-failed (configurable)

### 9.3 Agent Crashes

- If agent crashes mid-task, webhook never arrives
- Stale checker will poll and either get result or timeout
- After **4 hours**, task auto-fails with "Task expired" error
- Error message explains the timeout for user visibility

### 9.4 Duplicate Notifications

- Idempotency tracking via `last_notified_state` field
- Both webhook handler and stale checker use same notification function
- SSE only sent when state actually changes
- Frontend deduplicates by state+content hash

### 9.5 Race Conditions

| Scenario | Handling |
|----------|----------|
| Agent fails to contact | Task immediately marked failed (no orphaned "pending" tasks) |
| Webhook + stale checker fire | Idempotency check prevents duplicate SSE |
| Task limit exceeded | 429 error returned, task not created |

### 9.6 Interactive State Timeouts

- `input_required` and `auth_required` states also subject to expiry
- After 4 hours without user action, auto-failed
- Status message explains what was needed

---

## 10. Implementation Plan

### Phase 1: Core Infrastructure (Required)

1. [x] Create MongoDB collection and indexes (including TTL index)
2. [x] Implement `A2ATaskService` with:
   - [x] HMAC-based token hashing
   - [x] Task quota enforcement
   - [x] Idempotency tracking
3. [x] Add webhook endpoint with rate limiting
4. [x] Update `A2AService` to track tasks
5. [x] Add SSE event types for all A2A states
6. [x] Add configuration for:
   - [x] `WEBHOOK_BASE_URL` (required, validated)
   - [x] `WEBHOOK_SIGNING_KEY` (required, for token hashing)
   - [x] `ALLOWED_AGENT_HOSTS` (optional, for security)

### Phase 2: Frontend (Required)

7. [x] Create `TaskStatusMessage` component with all state support
8. [x] Update message handler for task events
9. [x] Add fallback polling with dynamic `retry_after` interval
10. [x] Add deduplication for SSE events

### Phase 3: Agent Support (Required for OpenClaw)

11. [x] Add webhook support to OpenClaw adapter
12. [x] Update agent card with `pushNotifications=true`
13. [ ] Test end-to-end with long-running task
14. [ ] Test interactive states (input_required, auth_required)

### Phase 4: Reliability (Required for Production)

15. [x] Implement stale task checker job
16. [x] Implement expired task auto-failure
17. [x] Add rate limiting to task creation
18. [ ] Add monitoring/alerts:
    - [ ] Tasks pending > 1 hour
    - [ ] Webhook delivery failures
    - [ ] Task quota approaching limits

### Phase 5: Enhancements (Future)

19. [ ] Email notifications on completion
20. [ ] Task cancellation from frontend
21. [ ] Progress updates during execution
22. [ ] SSE reconnection with event replay

---

## 11. Configuration Reference

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `WEBHOOK_BASE_URL` | Yes | - | Public URL where agents send webhooks (e.g., `https://api.example.com`) |
| `WEBHOOK_SIGNING_KEY` | Yes | - | Secret key for HMAC token hashing (min 32 chars) |
| `ALLOWED_AGENT_HOSTS` | No | - | Comma-separated allowlist of trusted agent hosts |
| `MAX_TASKS_PER_USER` | No | 100 | Max concurrent non-terminal tasks per user |
| `MAX_TASKS_PER_ROOM` | No | 50 | Max concurrent non-terminal tasks per room |
| `STALE_CHECK_MINUTES` | No | 10 | Poll tasks not updated in this time |
| `TASK_EXPIRY_HOURS` | No | 4 | Auto-fail tasks older than this |
| `TASK_TTL_DAYS` | No | 30 | Delete completed tasks after this time |

### Timeout Configuration

```python
# Recommended settings for different use cases

# Quick tasks (API calls, simple operations): 
TASK_EXPIRY_HOURS = 1

# Medium tasks (file processing, analysis):
TASK_EXPIRY_HOURS = 4  # Default

# Long tasks (ML training, large data processing):
TASK_EXPIRY_HOURS = 24
```

---

## Appendix A: Risk Analysis and Mitigations

This section documents identified risks and their mitigations.

### A.1 Race Conditions

| Risk | Severity | Mitigation |
|------|----------|------------|
| Orphaned "pending" tasks when agent send fails | Medium | Task marked failed immediately in exception handler |
| Duplicate SSE when webhook + stale checker fire | Low | Idempotency tracking via `last_notified_state` |

### A.2 State Machine Completeness

| Risk | Severity | Mitigation |
|------|----------|------------|
| Missing A2A states (input_required, etc.) | High | All 8 A2A states now handled |
| Interactive states never resolve | Medium | Subject to same expiry timeout as pending states |

### A.3 Security

| Risk | Severity | Mitigation |
|------|----------|------------|
| Plaintext token in database | Medium | HMAC-SHA256 hashing, token never stored |
| Webhook URL spoofing | Low | Bearer token required, hash-verified |
| Unreachable webhook URL | Medium | Validation on service init, warning for localhost |
| Untrusted agent URLs polled | Low | Optional allowlist configuration |

### A.4 Resource Management

| Risk | Severity | Mitigation |
|------|----------|------------|
| Unbounded task creation | High | Per-user and per-room quotas |
| Completed tasks accumulate | Medium | TTL index auto-deletes after 30 days |
| Long auto-fail timeout (was 24h) | Medium | Reduced to 4 hours, configurable |

### A.5 Operational

| Risk | Severity | Mitigation |
|------|----------|------------|
| SSE connection lost during task | Medium | Frontend fallback polling with server-hinted interval |
| Frontend/backend polling mismatch | Low | `retry_after_seconds` in API response |
| No visibility into stuck tasks | Medium | Warning logs at 1h, monitoring recommended |

---

## Appendix B: A2A Types Reference

```protobuf
message Task {
  string id = 1;
  string context_id = 2;
  TaskStatus status = 3;
  repeated Artifact artifacts = 4;
  repeated Message history = 5;
  google.protobuf.Struct metadata = 6;
}

message TaskStatus {
  TaskState state = 1;
  Message message = 2;
  google.protobuf.Timestamp timestamp = 3;
}

enum TaskState {
  TASK_STATE_UNSPECIFIED = 0;
  TASK_STATE_SUBMITTED = 1;
  TASK_STATE_WORKING = 2;
  TASK_STATE_COMPLETED = 3;
  TASK_STATE_FAILED = 4;
  TASK_STATE_CANCELED = 5;
  TASK_STATE_INPUT_REQUIRED = 6;
  TASK_STATE_REJECTED = 7;
  TASK_STATE_AUTH_REQUIRED = 8;
}

message PushNotificationConfig {
  string id = 1;
  string url = 2;
  string token = 3;
  AuthenticationInfo authentication = 4;
}
```

### Task State Categories

| Category | States | Description | Handling |
|----------|--------|-------------|----------|
| **Pending** | `submitted`, `working` | Task is being processed | Show spinner, poll |
| **Interactive** | `input_required`, `auth_required` | User action needed | Show prompt, longer poll interval |
| **Terminal** | `completed`, `failed`, `canceled`, `rejected` | Task is done | Show result/error, stop polling |

---

## Appendix C: Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | Initial | Original design |
| 1.1 | - | Added all A2A states support |
| | | HMAC token hashing (no plaintext storage) |
| | | Task quotas (per-user, per-room) |
| | | TTL index for automatic cleanup |
| | | Reduced auto-fail timeout (24h → 4h) |
| | | Idempotency for SSE notifications |
| | | Rate limiting on webhooks |
| | | Dynamic polling interval (`retry_after`) |
| | | Webhook URL validation |
| | | Optional agent URL allowlist |
| | | Risk analysis appendix |
