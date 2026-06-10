# Remove Legacy DatabaseService Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate `app_shell/database_service.py` as a production runtime dependency by migrating all call sites to module-owned repositories backed by `dal/` adapters.

**Architecture:** Each logical data-access concern becomes a repository class in the owning module's `repository/` package, depending only on `MongoDAL` from `common.protocols`. Module facades and app-shell services receive repositories via constructor injection. `container.py` constructs everything from `MongoDALImpl`.

**Tech Stack:** Python 3.13, Motor (async MongoDB), Pydantic v2, FastAPI, MongoCollectionAdapter (dal/mongo/client.py)

---

## File Map

### New Repository Files

| File | Responsibility |
|------|---------------|
| `execution/repository/task_message_mongo.py` | Task-tracking fields on room_agent_messages (Category C) |
| `execution/repository/hitl_mongo.py` | HITL requests collection — CAS claims, fenced updates, group routing (Category D) |
| `execution/repository/claim_mongo.py` | User-message processing claims + supervisor trajectory claims (Category E partial) |
| `execution/repository/cancellation_mongo.py` | Cancelled messages + BFS descendant cancellation (Category E partial) |
| `agent/repository/group_mongo.py` | Agent groups CRUD (Category A partial) |
| `room/repository/memory_mongo.py` | Room memories — atomic push/trim/compact (Category F) |

### Modified Files

| File | Change |
|------|--------|
| `common/protocols/repository_protocols.py` | Add protocols: TaskMessageRepository, HITLRequestRepository, UserMessageClaimRepository, CancellationRepository, AgentGroupRepository, RoomMemoryRepository |
| `container.py` | Add factory functions for all new repositories; wire into deps |
| `main.py` | Replace `_db_svc` injection with repository-backed services; remove `database.*` imports |
| `execution/hitl/service.py` | Replace `self._db` calls with injected repositories |
| `execution/hitl/factory.py` | Accept repositories in factory function |
| `execution/dispatch/task_notifications.py` | Replace `db` calls with task message repository |
| `execution/dispatch/transports/webhook.py` | Replace `db` calls with task message + cancellation repos |
| `jobs/stale_task_checker.py` | Replace `db_service` calls with repositories |
| `app_shell/room_runtime.py` | Replace `self.database_service` with facade/repository protocols |
| `app_shell/room_coordinator_service.py` | Replace `self._database_service` with repository protocols |
| `app_shell/relay_service.py` | Replace `self._db` with repository protocols |
| `api_gateway/routes/room_routes.py` | Replace `db_service` with RoomFacade protocol |
| `api_gateway/routes/a2a_task_routes.py` | Replace `db_service` with task reader protocol |
| `api_gateway/routes/agent_group_routes.py` | Replace `db_service` with AgentGroupRepository |
| `api_gateway/routes/sse_routes.py` | Replace `db_service` with reader protocols |
| `app_shell/database_service.py` | Gut to empty compatibility stub |

---

## Phase 1: Room Messages & Agent Groups (Categories A-partial, B-partial)

These are the simplest — the room repository layer already exists and covers most methods.

---

### Task 1: Expand Room MessageRepository with missing methods

The existing `room/repository/mongo.py::MessageMongoRepository` already covers most Category B methods. However, `app_shell/room_runtime.py` and `app_shell/room_coordinator_service.py` call `get_room_agent_messages_by_related_message_id()` which is not yet on the repository.

**Files:**
- Modify: `room/repository/mongo.py`
- Modify: `common/protocols/repository_protocols.py`
- Test: `tests/test_room_message_repository.py`

- [ ] **Step 1: Write failing test for get_agent_messages_by_related_id**

```python
# tests/test_room_message_repository.py
import pytest
from unittest.mock import AsyncMock, MagicMock

from room.repository.mongo import MessageMongoRepository


@pytest.fixture
def mock_mongo():
    mongo = MagicMock()
    mongo.collection = MagicMock(return_value=AsyncMock())
    return mongo


@pytest.fixture
def repo(mock_mongo):
    return MessageMongoRepository(mongo=mock_mongo)


@pytest.mark.asyncio
async def test_get_agent_messages_by_related_id(repo, mock_mongo):
    agent_col = mock_mongo.collection.return_value
    agent_col.find = AsyncMock(return_value=[
        {"message_id": "msg-1", "related_message_id": "user-msg-1"},
        {"message_id": "msg-2", "related_message_id": "user-msg-1"},
    ])
    result = await repo.get_agent_messages_by_related_id("user-msg-1")
    assert len(result) == 2
    agent_col.find.assert_called_once_with(
        {"related_message_id": "user-msg-1"},
        sort=[("message_created_at", 1)],
    )
```

- [ ] **Step 2: Run test — expect FAIL (method not defined)**

Run: `uv run pytest tests/test_room_message_repository.py::test_get_agent_messages_by_related_id -v`
Expected: AttributeError

- [ ] **Step 3: Implement the method**

Add to `room/repository/mongo.py` in class `MessageMongoRepository`:

```python
async def get_agent_messages_by_related_id(
    self, related_message_id: str
) -> list[dict]:
    return await self._agent_messages.find(
        {"related_message_id": related_message_id},
        sort=[("message_created_at", 1)],
    )
```

- [ ] **Step 4: Add protocol method**

Add to `common/protocols/repository_protocols.py` class `MessageRepository`:

```python
async def get_agent_messages_by_related_id(
    self, related_message_id: str
) -> list[dict]: ...
```

- [ ] **Step 5: Run test — expect PASS**

Run: `uv run pytest tests/test_room_message_repository.py -v`

- [ ] **Step 6: Commit**

```bash
git add room/repository/mongo.py common/protocols/repository_protocols.py tests/test_room_message_repository.py
git commit -m "feat(room): add get_agent_messages_by_related_id to MessageRepository"
```

---

### Task 2: Create AgentGroupMongoRepository

**Files:**
- Create: `agent/repository/group_mongo.py`
- Modify: `common/protocols/repository_protocols.py`
- Test: `tests/test_agent_group_repository.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_agent_group_repository.py
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_mongo():
    mongo = MagicMock()
    col = AsyncMock()
    mongo.collection = MagicMock(return_value=col)
    return mongo, col


@pytest.mark.asyncio
async def test_create_group(mock_mongo):
    from agent.repository.group_mongo import AgentGroupMongoRepository

    mongo, col = mock_mongo
    col.insert_one = AsyncMock(return_value="inserted-id")
    repo = AgentGroupMongoRepository(mongo=mongo)
    result = await repo.create({"group_id": "g1", "owner_id": "u1", "name": "Test"})
    assert result == "g1"
    col.insert_one.assert_called_once()


@pytest.mark.asyncio
async def test_get_by_id(mock_mongo):
    from agent.repository.group_mongo import AgentGroupMongoRepository

    mongo, col = mock_mongo
    col.find_one = AsyncMock(return_value={"group_id": "g1", "name": "Test"})
    repo = AgentGroupMongoRepository(mongo=mongo)
    result = await repo.get_by_id("g1")
    assert result == {"group_id": "g1", "name": "Test"}


@pytest.mark.asyncio
async def test_get_by_owner(mock_mongo):
    from agent.repository.group_mongo import AgentGroupMongoRepository

    mongo, col = mock_mongo
    col.find = AsyncMock(return_value=[{"group_id": "g1", "owner_id": "u1"}])
    repo = AgentGroupMongoRepository(mongo=mongo)
    result = await repo.get_by_owner("u1")
    assert len(result) == 1


@pytest.mark.asyncio
async def test_update(mock_mongo):
    from agent.repository.group_mongo import AgentGroupMongoRepository

    mongo, col = mock_mongo
    col.update_one = AsyncMock(return_value=True)
    repo = AgentGroupMongoRepository(mongo=mongo)
    result = await repo.update("g1", {"name": "Updated"})
    assert result is True


@pytest.mark.asyncio
async def test_delete(mock_mongo):
    from agent.repository.group_mongo import AgentGroupMongoRepository

    mongo, col = mock_mongo
    col.delete_one = AsyncMock(return_value=True)
    repo = AgentGroupMongoRepository(mongo=mongo)
    result = await repo.delete("g1")
    assert result is True
```

- [ ] **Step 2: Run test — expect FAIL (module not found)**

Run: `uv run pytest tests/test_agent_group_repository.py -v`

- [ ] **Step 3: Implement AgentGroupMongoRepository**

Create `agent/repository/group_mongo.py`:

```python
from __future__ import annotations

from common.protocols import MongoDAL


class AgentGroupMongoRepository:
    def __init__(self, mongo: MongoDAL, collection_name: str = "agent_groups") -> None:
        self._groups = mongo.collection(collection_name)

    async def create(self, group: dict) -> str:
        await self._groups.insert_one(dict(group))
        return str(group.get("group_id") or group.get("_id", ""))

    async def get_by_id(self, group_id: str) -> dict | None:
        return await self._groups.find_one({"group_id": group_id})

    async def get_by_owner(self, owner_id: str) -> list[dict]:
        return await self._groups.find({"owner_id": owner_id})

    async def update(self, group_id: str, updates: dict) -> bool:
        return await self._groups.update_one(
            {"group_id": group_id}, {"$set": updates}
        )

    async def delete(self, group_id: str) -> bool:
        return await self._groups.delete_one({"group_id": group_id})
```

- [ ] **Step 4: Add AgentGroupRepository protocol**

Add to `common/protocols/repository_protocols.py`:

```python
@runtime_checkable
class AgentGroupRepository(Protocol):
    async def create(self, group: dict) -> str: ...
    async def get_by_id(self, group_id: str) -> dict | None: ...
    async def get_by_owner(self, owner_id: str) -> list[dict]: ...
    async def update(self, group_id: str, updates: dict) -> bool: ...
    async def delete(self, group_id: str) -> bool: ...
```

Export it from `common/protocols/__init__.py`.

- [ ] **Step 5: Run test — expect PASS**

Run: `uv run pytest tests/test_agent_group_repository.py -v`

- [ ] **Step 6: Commit**

```bash
git add agent/repository/group_mongo.py common/protocols/repository_protocols.py common/protocols/__init__.py tests/test_agent_group_repository.py
git commit -m "feat(agent): add AgentGroupMongoRepository"
```

---

### Task 3: Wire AgentGroupRepository into routes

Replace `db_service` in `api_gateway/routes/agent_group_routes.py` with the new repository.

**Files:**
- Modify: `api_gateway/routes/agent_group_routes.py`
- Modify: `container.py`
- Modify: `main.py`

- [ ] **Step 1: Update agent_group_routes.py to accept AgentGroupRepository**

The file already defines an `AgentGroupStore` protocol. The new `AgentGroupMongoRepository` satisfies it structurally, but `AgentGroupStore.delete_agent_group(group_id, owner_id)` takes two args while the repo `delete(group_id)` takes one. Need a thin adapter or check ownership in the route handler.

Replace the module-level binding to accept the repository directly. In `api_gateway/routes/agent_group_routes.py`, change:

```python
from common.protocols import AgentGroupRepository

db_service: AgentGroupRepository | None = None

def bind_agent_group_dependencies(database_service: AgentGroupRepository):
    global db_service
    db_service = database_service
```

The route handlers already call `db_service.get_agent_group_by_id()` etc. Map method names — the protocol methods are `get_by_id`, `get_by_owner`, etc. So create a thin adapter in `container.py`:

```python
def create_agent_group_store(*, mongo: MongoDAL) -> Any:
    from agent.repository.group_mongo import AgentGroupMongoRepository

    repo = AgentGroupMongoRepository(mongo=mongo)

    class _AgentGroupStoreAdapter:
        async def add_agent_group(self, group) -> bool:
            doc = group.model_dump() if hasattr(group, "model_dump") else dict(group)
            await repo.create(doc)
            return True

        async def get_agent_group_by_id(self, group_id: str):
            doc = await repo.get_by_id(group_id)
            if doc is None:
                return None
            from models.agent_group import AgentGroup
            return AgentGroup.model_validate(doc)

        async def get_agent_groups_by_owner(self, owner_id: str):
            docs = await repo.get_by_owner(owner_id)
            from models.agent_group import AgentGroup
            return [AgentGroup.model_validate(d) for d in docs]

        async def update_agent_group(self, group_id: str, data: dict) -> bool:
            return await repo.update(group_id, data)

        async def delete_agent_group(self, group_id: str) -> bool:
            return await repo.delete(group_id)

    return _AgentGroupStoreAdapter()
```

- [ ] **Step 2: Add factory to container.py**

Add `create_agent_group_store` function as shown above.

- [ ] **Step 3: Wire in main.py**

Replace the line:
```python
agent_group.bind_agent_group_dependencies(_db_svc)
```
With:
```python
from container import create_agent_group_store
_agent_group_store = create_agent_group_store(mongo=mongo_dal)
agent_group.bind_agent_group_dependencies(_agent_group_store)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add api_gateway/routes/agent_group_routes.py container.py main.py
git commit -m "refactor(routes): wire agent_group_routes to AgentGroupMongoRepository"
```

---

## Phase 2: Task Tracking Repository (Category C)

This is the largest single repository — owns all task-tracking fields on `room_agent_messages`.

---

### Task 4: Create TaskMessageMongoRepository

**Files:**
- Create: `execution/repository/task_message_mongo.py`
- Modify: `common/protocols/repository_protocols.py`
- Test: `tests/test_task_message_repository.py`

- [ ] **Step 1: Write failing test for core operations**

```python
# tests/test_task_message_repository.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone


@pytest.fixture
def mock_mongo():
    mongo = MagicMock()
    col = AsyncMock()
    mongo.collection = MagicMock(return_value=col)
    return mongo, col


@pytest.mark.asyncio
async def test_enable_task_tracking(mock_mongo):
    from execution.repository.task_message_mongo import TaskMessageMongoRepository

    mongo, col = mock_mongo
    col.update_one = AsyncMock(return_value=True)
    repo = TaskMessageMongoRepository(mongo=mongo)
    now = datetime.now(tz=timezone.utc)
    result = await repo.enable_task_tracking(
        message_id="msg-1",
        webhook_token_hash="hash123",
        agent_url="http://agent.test",
        task_created_at=now,
        task_updated_at=now,
        task_data={"task_id": "t1"},
    )
    assert result is True
    col.update_one.assert_called_once()
    call_args = col.update_one.call_args
    assert call_args[0][0] == {"message_id": "msg-1"}
    update = call_args[0][1]
    assert "$set" in update
    assert update["$set"]["has_task_tracking"] is True


@pytest.mark.asyncio
async def test_get_stale_task_messages(mock_mongo):
    from execution.repository.task_message_mongo import TaskMessageMongoRepository

    mongo, col = mock_mongo
    col.find = AsyncMock(return_value=[
        {"message_id": "msg-1", "has_task_tracking": True},
    ])
    repo = TaskMessageMongoRepository(mongo=mongo)
    result = await repo.get_stale_task_messages(
        stale_minutes=30,
        non_terminal_states=["submitted", "working"],
    )
    assert len(result) == 1


@pytest.mark.asyncio
async def test_touch_task_message(mock_mongo):
    from execution.repository.task_message_mongo import TaskMessageMongoRepository

    mongo, col = mock_mongo
    col.update_one = AsyncMock(return_value=True)
    repo = TaskMessageMongoRepository(mongo=mongo)
    result = await repo.touch(message_id="msg-1")
    assert result is True


@pytest.mark.asyncio
async def test_update_task_state(mock_mongo):
    from execution.repository.task_message_mongo import TaskMessageMongoRepository

    mongo, col = mock_mongo
    col.update_one = AsyncMock(return_value=True)
    repo = TaskMessageMongoRepository(mongo=mongo)
    result = await repo.update_task_state("msg-1", state="completed")
    assert result is True


@pytest.mark.asyncio
async def test_save_continuation(mock_mongo):
    from execution.repository.task_message_mongo import TaskMessageMongoRepository

    mongo, col = mock_mongo
    col.update_one = AsyncMock(return_value=True)
    repo = TaskMessageMongoRepository(mongo=mongo)
    result = await repo.save_continuation("msg-1", {"resume": "data"})
    assert result is True


@pytest.mark.asyncio
async def test_get_and_clear_continuation(mock_mongo):
    from execution.repository.task_message_mongo import TaskMessageMongoRepository

    mongo, col = mock_mongo
    col.find_one_and_update = AsyncMock(return_value={
        "message_id": "msg-1",
        "pending_continuation": {"resume": "data"},
    })
    repo = TaskMessageMongoRepository(mongo=mongo)
    result = await repo.get_and_clear_continuation("msg-1")
    assert result == {"resume": "data"}


@pytest.mark.asyncio
async def test_accumulate_artifact(mock_mongo):
    from execution.repository.task_message_mongo import TaskMessageMongoRepository

    mongo, col = mock_mongo
    col.find_one = AsyncMock(return_value={"message_id": "msg-1", "artifacts": []})
    col.update_one = AsyncMock(return_value=True)
    repo = TaskMessageMongoRepository(mongo=mongo)
    result = await repo.accumulate_artifact(
        message_id="msg-1",
        artifact={"artifactId": "a1", "parts": [{"type": "text", "text": "hello"}]},
    )
    assert result is True
```

- [ ] **Step 2: Run test — expect FAIL (module not found)**

Run: `uv run pytest tests/test_task_message_repository.py -v`

- [ ] **Step 3: Implement TaskMessageMongoRepository**

Create `execution/repository/task_message_mongo.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from common.protocols import MongoDAL
from common.utils.time import utcnow


class TaskMessageMongoRepository:
    """Repository for task-tracking fields on room_agent_messages."""

    def __init__(self, mongo: MongoDAL, collection_name: str = "room_agent_messages") -> None:
        self._messages = mongo.collection(collection_name)

    async def get_by_message_id(self, message_id: str) -> dict | None:
        return await self._messages.find_one({"message_id": message_id})

    async def enable_task_tracking(
        self,
        message_id: str,
        webhook_token_hash: str,
        agent_url: str,
        task_created_at: datetime,
        task_updated_at: datetime,
        task_data: dict,
    ) -> bool:
        return await self._messages.update_one(
            {"message_id": message_id},
            {
                "$set": {
                    "has_task_tracking": True,
                    "webhook_token_hash": webhook_token_hash,
                    "agent_url": agent_url,
                    "task_created_at": task_created_at,
                    "task_updated_at": task_updated_at,
                    "task_data": task_data,
                }
            },
        )

    def _terminal_state_values(self) -> list[str]:
        from common.a2a_constants import TERMINAL_STATES
        return [s.value for s in TERMINAL_STATES]

    async def update_task_state(
        self,
        message_id: str,
        state: str,
        *,
        message_text: str | None = None,
        artifacts: list[dict] | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
    ) -> bool:
        """Partial update with atomic terminal-state guard.

        Skips the update if the document already has a terminal task status
        (completed, failed, canceled, rejected) to prevent concurrent overwrites.
        """
        now = utcnow()
        update: dict = {
            "message_content.message_task.status.state": state,
            "task_updated_at": now,
        }
        if message_text is not None:
            update["message_content.message_text"] = message_text
        if artifacts is not None:
            update["message_content.message_task.artifacts"] = artifacts
        if task_id is not None:
            update["message_content.message_task.id"] = task_id
        if context_id is not None:
            update["message_content.message_task.contextId"] = context_id
        return await self._messages.update_one(
            {
                "message_id": message_id,
                "message_content.message_task.status.state": {
                    "$nin": self._terminal_state_values(),
                },
            },
            {"$set": update},
        )

    async def update_task_on_message(
        self, message_id: str, task_data: dict, message_text: str | None = None
    ) -> bool:
        """Replace entire task object with atomic terminal-state guard.

        Prevents a stale-task poller from overwriting a final state.
        """
        now = utcnow()
        update: dict = {
            "message_content.message_task": task_data,
            "task_updated_at": now,
        }
        if message_text is not None:
            update["message_content.message_text"] = message_text
        return await self._messages.update_one(
            {
                "message_id": message_id,
                "message_content.message_task.status.state": {
                    "$nin": self._terminal_state_values(),
                },
            },
            {"$set": update},
        )

    async def touch(self, message_id: str) -> bool:
        return await self._messages.update_one(
            {"message_id": message_id},
            {"$set": {"task_updated_at": utcnow()}},
        )

    async def update_last_notified_state(self, message_id: str, state: str) -> bool:
        doc = await self._messages.find_one_and_update(
            {"message_id": message_id, "last_notified_state": {"$ne": state}},
            {"$set": {"last_notified_state": state}},
        )
        return doc is not None

    async def reset_last_notified_state(self, message_id: str) -> bool:
        return await self._messages.update_one(
            {"message_id": message_id},
            {"$set": {"last_notified_state": None}},
        )

    async def update_webhook_token_hash(self, message_id: str, webhook_token_hash: str) -> bool:
        return await self._messages.update_one(
            {"message_id": message_id},
            {"$set": {"webhook_token_hash": webhook_token_hash}},
        )

    async def update_agent_message_task_state(self, message_id: str, state: str) -> bool:
        return await self._messages.update_one(
            {"message_id": message_id},
            {"$set": {"task_data.status.state": state, "task_updated_at": utcnow()}},
        )

    async def persist_hitl_user_answer(self, message_id: str, user_answer: str | None) -> bool:
        return await self._messages.update_one(
            {"message_id": message_id},
            {"$set": {"hitl_user_answer": user_answer}},
        )

    async def persist_hitl_group_metadata(
        self,
        message_id: str,
        group_id: str,
        group_total: int | None = None,
        group_index: int | None = None,
    ) -> bool:
        update: dict = {"hitl_group_id": group_id}
        if group_total is not None:
            update["hitl_group_total"] = group_total
        if group_index is not None:
            update["hitl_group_index"] = group_index
        return await self._messages.update_one(
            {"message_id": message_id}, {"$set": update}
        )

    # --- Continuation persistence ---

    async def save_continuation(self, message_id: str, continuation_data: dict) -> bool:
        return await self._messages.update_one(
            {"message_id": message_id},
            {"$set": {"pending_continuation": continuation_data}},
        )

    async def get_and_clear_continuation(self, message_id: str) -> dict | None:
        doc = await self._messages.find_one_and_update(
            {"message_id": message_id, "pending_continuation": {"$ne": None}},
            {"$set": {"pending_continuation": None}},
        )
        if doc is None:
            return None
        return doc.get("pending_continuation")

    async def has_continuation(self, message_id: str) -> bool:
        doc = await self._messages.find_one({"message_id": message_id})
        if doc is None:
            return False
        return doc.get("pending_continuation") is not None

    # --- Task message queries ---

    async def get_stale_task_messages(
        self, stale_minutes: int, non_terminal_states: list[str]
    ) -> list[dict]:
        cutoff = utcnow() - timedelta(minutes=stale_minutes)
        return await self._messages.find(
            {
                "has_task_tracking": True,
                "task_data.status.state": {"$in": non_terminal_states},
                "task_updated_at": {"$lt": cutoff},
            },
            sort=[("task_updated_at", 1)],
        )

    async def get_expired_task_messages(
        self, max_age_hours: int, non_terminal_states: list[str]
    ) -> list[dict]:
        cutoff = utcnow() - timedelta(hours=max_age_hours)
        return await self._messages.find(
            {
                "has_task_tracking": True,
                "task_data.status.state": {"$in": non_terminal_states},
                "task_created_at": {"$lt": cutoff},
            },
            sort=[("task_created_at", 1)],
        )

    async def get_orphaned_agent_messages(
        self, orphan_threshold_minutes: int
    ) -> list[dict]:
        cutoff = utcnow() - timedelta(minutes=orphan_threshold_minutes)
        return await self._messages.find(
            {
                "has_task_tracking": {"$ne": True},
                "message_created_at": {"$lt": cutoff},
                "message_content.text": {"$in": [None, ""]},
                "status": {"$nin": ["cancelled", "completed", "failed"]},
            },
            sort=[("message_created_at", 1)],
            limit=200,
        )

    async def get_non_tracked_stale_task_messages(
        self, max_age_hours: int, non_terminal_states: list[str]
    ) -> list[dict]:
        cutoff = utcnow() - timedelta(hours=max_age_hours)
        return await self._messages.find(
            {
                "has_task_tracking": {"$ne": True},
                "task_data.status.state": {"$in": non_terminal_states},
                "message_created_at": {"$lt": cutoff},
            },
            sort=[("message_created_at", 1)],
        )

    async def get_task_messages_for_room(self, room_id: str, limit: int = 50) -> list[dict]:
        return await self._messages.find(
            {"room_id": room_id, "has_task_tracking": True},
            sort=[("task_created_at", -1)],
            limit=limit,
        )

    async def get_pending_task_messages_for_user(
        self, user_id: str, non_terminal_states: list[str]
    ) -> list[dict]:
        return await self._messages.find(
            {
                "user_id": user_id,
                "has_task_tracking": True,
                "task_data.status.state": {"$in": non_terminal_states},
            },
            sort=[("task_created_at", -1)],
        )

    async def count_non_terminal_tasks_for_user(
        self, user_id: str, non_terminal_states: list[str]
    ) -> int:
        return await self._messages.count(
            {
                "user_id": user_id,
                "has_task_tracking": True,
                "task_data.status.state": {"$in": non_terminal_states},
            }
        )

    async def count_non_terminal_tasks_for_room(
        self, room_id: str, non_terminal_states: list[str]
    ) -> int:
        return await self._messages.count(
            {
                "room_id": room_id,
                "has_task_tracking": True,
                "task_data.status.state": {"$in": non_terminal_states},
            }
        )

    async def update_fields(self, message_id: str, updates: dict) -> bool:
        return await self._messages.update_one(
            {"message_id": message_id}, {"$set": updates}
        )

    # --- Artifact accumulation ---

    async def accumulate_artifact(
        self,
        message_id: str,
        artifact: dict,
        append: bool = False,
    ) -> bool:
        artifact_id = artifact.get("artifactId") or artifact.get("artifact_id")
        doc = await self._messages.find_one({"message_id": message_id})
        if doc is None:
            return False

        existing_artifacts = doc.get("artifacts") or []
        artifact_text = " ".join(
            p.get("text", "") for p in artifact.get("parts", []) if p.get("type") == "text"
        )

        if append and artifact_id:
            for i, existing in enumerate(existing_artifacts):
                eid = existing.get("artifactId") or existing.get("artifact_id")
                if eid == artifact_id:
                    existing_parts = existing.get("parts", [])
                    existing_parts.extend(artifact.get("parts", []))
                    existing_artifacts[i]["parts"] = existing_parts
                    break
            else:
                existing_artifacts.append(artifact)
        else:
            if artifact_id:
                existing_artifacts = [
                    a for a in existing_artifacts
                    if (a.get("artifactId") or a.get("artifact_id")) != artifact_id
                ]
            existing_artifacts.append(artifact)

        update: dict = {"artifacts": existing_artifacts, "task_updated_at": utcnow()}
        if artifact_text:
            update["message_content.text"] = artifact_text
        return await self._messages.update_one(
            {"message_id": message_id}, {"$set": update}
        )

    # --- Webhook token verification ---

    async def verify_webhook_token_for_task(
        self, message_id: str, token: str, hash_func, verify_func
    ) -> tuple[bool, str]:
        doc = await self._messages.find_one({"message_id": message_id})
        if doc is None:
            return False, "message_not_found"
        if not doc.get("has_task_tracking"):
            return False, "no_task_tracking"
        stored_hash = doc.get("webhook_token_hash")
        if not stored_hash:
            return False, "no_webhook_token"
        if not verify_func(token, stored_hash):
            return False, "token_mismatch"
        return True, ""
```

- [ ] **Step 4: Add TaskMessageRepository protocol**

Add to `common/protocols/repository_protocols.py`:

```python
@runtime_checkable
class TaskMessageRepository(Protocol):
    async def get_by_message_id(self, message_id: str) -> dict | None: ...
    async def enable_task_tracking(self, message_id: str, webhook_token_hash: str, agent_url: str, task_created_at: datetime, task_updated_at: datetime, task_data: dict) -> bool: ...
    async def update_task_state(self, message_id: str, state: str, *, message_text: str | None = None, artifacts: list[dict] | None = None, task_id: str | None = None, context_id: str | None = None) -> bool: ...
    async def update_task_on_message(self, message_id: str, task_data: dict, message_text: str | None = None) -> bool: ...
    async def touch(self, message_id: str) -> bool: ...
    async def update_last_notified_state(self, message_id: str, state: str) -> bool: ...
    async def save_continuation(self, message_id: str, continuation_data: dict) -> bool: ...
    async def get_and_clear_continuation(self, message_id: str) -> dict | None: ...
    async def get_stale_task_messages(self, stale_minutes: int, non_terminal_states: list[str]) -> list[dict]: ...
    async def get_expired_task_messages(self, max_age_hours: int, non_terminal_states: list[str]) -> list[dict]: ...
    async def get_task_messages_for_room(self, room_id: str, limit: int = 50) -> list[dict]: ...
    async def get_pending_task_messages_for_user(self, user_id: str, non_terminal_states: list[str]) -> list[dict]: ...
    async def accumulate_artifact(self, message_id: str, artifact: dict, append: bool = False) -> bool: ...
```

- [ ] **Step 5: Run test — expect PASS**

Run: `uv run pytest tests/test_task_message_repository.py -v`

- [ ] **Step 6: Commit**

```bash
git add execution/repository/task_message_mongo.py common/protocols/repository_protocols.py tests/test_task_message_repository.py
git commit -m "feat(execution): add TaskMessageMongoRepository for task tracking"
```

---

### Task 5: Wire TaskMessageRepository into container and consumers

**Files:**
- Modify: `container.py`
- Modify: `main.py`
- Modify: `execution/dispatch/task_notifications.py`
- Modify: `execution/dispatch/transports/webhook.py`
- Modify: `jobs/stale_task_checker.py`

- [ ] **Step 1: Add factory to container.py**

```python
def create_task_message_repository(*, mongo: MongoDAL):
    from execution.repository.task_message_mongo import TaskMessageMongoRepository
    return TaskMessageMongoRepository(mongo=mongo)
```

- [ ] **Step 2: Create repository in main.py and pass to deps**

After `_execution_repos` creation, add:

```python
_task_message_repo = create_task_message_repository(mongo=mongo_dal)
```

Pass `_task_message_repo` to consumers via their deps/bind patterns.

- [ ] **Step 3: Update StaleTaskCheckerDeps**

In `jobs/stale_task_checker.py`, add `task_message_repository` to the deps dataclass. Replace all `self._deps.db_service.get_stale_task_messages(...)` calls with `self._deps.task_message_repository.get_stale_task_messages(...)`.

- [ ] **Step 4: Update task_notifications.py**

Replace `self._db.update_last_notified_state(...)` with `self._task_repo.update_last_notified_state(...)`. Accept `task_message_repository` in constructor or bind.

- [ ] **Step 5: Update webhook.py**

Replace `self._db.verify_webhook_token_for_task(...)` with `self._task_repo.verify_webhook_token_for_task(...)`.

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add container.py main.py execution/dispatch/task_notifications.py execution/dispatch/transports/webhook.py jobs/stale_task_checker.py
git commit -m "refactor(execution): wire TaskMessageRepository into task tracking consumers"
```

---

## Phase 3: HITL Repository (Category D)

The most complex repository — atomic CAS claims, fenced writes, group routing coordination.

---

### Task 6: Create HITLMongoRepository

**Files:**
- Create: `execution/repository/hitl_mongo.py`
- Test: `tests/test_hitl_repository.py`

- [ ] **Step 1: Write failing test for CAS operations**

```python
# tests/test_hitl_repository.py
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_mongo():
    mongo = MagicMock()
    col = AsyncMock()
    mongo.collection = MagicMock(return_value=col)
    return mongo, col


@pytest.mark.asyncio
async def test_create_request(mock_mongo):
    from execution.repository.hitl_mongo import HITLMongoRepository

    mongo, col = mock_mongo
    col.insert_one = AsyncMock(return_value="inserted-id")
    repo = HITLMongoRepository(mongo=mongo)
    result = await repo.create({"request_id": "r1", "status": "pending"})
    assert result == "r1"


@pytest.mark.asyncio
async def test_cas_update_success(mock_mongo):
    from execution.repository.hitl_mongo import HITLMongoRepository

    mongo, col = mock_mongo
    col.find_one_and_update = AsyncMock(return_value={"request_id": "r1", "status": "processing"})
    repo = HITLMongoRepository(mongo=mongo)
    result = await repo.cas_update("r1", expected_status="pending", status="processing", claim_id="c1")
    assert result is True


@pytest.mark.asyncio
async def test_cas_update_conflict(mock_mongo):
    from execution.repository.hitl_mongo import HITLMongoRepository

    mongo, col = mock_mongo
    col.find_one_and_update = AsyncMock(return_value=None)
    repo = HITLMongoRepository(mongo=mongo)
    result = await repo.cas_update("r1", expected_status="pending", status="processing", claim_id="c1")
    assert result is False


@pytest.mark.asyncio
async def test_fenced_update(mock_mongo):
    from execution.repository.hitl_mongo import HITLMongoRepository

    mongo, col = mock_mongo
    col.find_one_and_update = AsyncMock(return_value={"request_id": "r1"})
    repo = HITLMongoRepository(mongo=mongo)
    result = await repo.fenced_update("r1", claim_id="c1", updates={"status": "completed"})
    assert result is True


@pytest.mark.asyncio
async def test_claim_group_routing(mock_mongo):
    from execution.repository.hitl_mongo import HITLMongoRepository

    mongo, col = mock_mongo
    col.find_one_and_update = AsyncMock(return_value={"group_id": "g1"})
    repo = HITLMongoRepository(mongo=mongo)
    result = await repo.claim_group_routing("g1", "claim-1")
    assert result is True
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `uv run pytest tests/test_hitl_repository.py -v`

- [ ] **Step 3: Implement HITLMongoRepository**

Create `execution/repository/hitl_mongo.py`:

```python
from __future__ import annotations

from common.protocols import MongoDAL
from common.utils.time import utcnow


class HITLMongoRepository:
    """Repository for hitl_requests collection with CAS and fenced-write semantics."""

    def __init__(self, mongo: MongoDAL, collection_name: str = "hitl_requests") -> None:
        self._hitl = mongo.collection(collection_name)

    async def create(self, request_data: dict) -> str:
        await self._hitl.insert_one(dict(request_data))
        return str(request_data.get("request_id", ""))

    async def get_by_id(self, request_id: str) -> dict | None:
        return await self._hitl.find_one({"request_id": request_id})

    async def update(self, request_id: str, **updates) -> bool:
        if not updates:
            return False
        return await self._hitl.update_one(
            {"request_id": request_id}, {"$set": updates}
        )

    async def cas_update(
        self, request_id: str, expected_status: str, **updates
    ) -> bool:
        doc = await self._hitl.find_one_and_update(
            {"request_id": request_id, "status": expected_status},
            {"$set": updates},
        )
        return doc is not None

    async def fenced_update(
        self, request_id: str, claim_id: str, updates: dict | None = None, **kw_updates
    ) -> bool:
        merged = dict(updates or {})
        merged.update(kw_updates)
        if not merged:
            return False
        doc = await self._hitl.find_one_and_update(
            {"request_id": request_id, "claim_id": claim_id},
            {"$set": merged},
        )
        return doc is not None

    async def claim(self, request_id: str, **updates) -> dict | None:
        return await self._hitl.find_one_and_update(
            {"request_id": request_id, "status": "pending"},
            {"$set": {"status": "processing", **updates}},
        )

    async def get_pending_for_room(self, room_id: str) -> list[dict]:
        return await self._hitl.find(
            {"room_id": room_id, "status": "pending"},
            sort=[("created_at", 1)],
        )

    async def get_pending_for_message(self, user_message_id: str) -> list[dict]:
        return await self._hitl.find(
            {"user_message_id": user_message_id, "status": "pending"},
            sort=[("created_at", 1)],
        )

    async def get_group_requests(self, group_id: str) -> list[dict]:
        return await self._hitl.find(
            {"group_id": group_id},
            sort=[("group_index", 1)],
        )

    async def count_pending_in_group(self, group_id: str) -> int:
        return await self._hitl.count(
            {"group_id": group_id, "status": "pending"}
        )

    async def count_for_message(self, continuation_message_id: str) -> int:
        return await self._hitl.count(
            {"continuation_message_id": continuation_message_id}
        )

    async def claim_group_routing(self, group_id: str, claim_id: str) -> bool:
        doc = await self._hitl.find_one_and_update(
            {"group_id": group_id, "routing_claim_id": None},
            {"$set": {"routing_claim_id": claim_id}},
        )
        return doc is not None

    async def release_group_routing(self, group_id: str, claim_id: str) -> bool:
        doc = await self._hitl.find_one_and_update(
            {"group_id": group_id, "routing_claim_id": claim_id},
            {"$set": {"routing_claim_id": None}},
        )
        return doc is not None

    async def iter_stale_processing(self, cutoff_minutes: int) -> list[dict]:
        from datetime import timedelta
        cutoff = utcnow() - timedelta(minutes=cutoff_minutes)
        return await self._hitl.find(
            {"status": "processing", "claimed_at": {"$lt": cutoff}},
            sort=[("claimed_at", 1)],
        )

    async def ensure_indexes(self) -> None:
        await self._hitl.create_index(
            [("room_id", 1), ("status", 1)],
            name="room_status_idx",
        )
        await self._hitl.create_index(
            [("user_message_id", 1), ("status", 1)],
            name="user_message_status_idx",
        )
        await self._hitl.create_index(
            [("group_id", 1)],
            name="group_idx",
        )
        await self._hitl.create_index(
            [("continuation_message_id", 1)],
            name="continuation_idx",
        )
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `uv run pytest tests/test_hitl_repository.py -v`

- [ ] **Step 5: Commit**

```bash
git add execution/repository/hitl_mongo.py tests/test_hitl_repository.py
git commit -m "feat(execution): add HITLMongoRepository with CAS and fenced-write semantics"
```

---

### Task 7: Wire HITLMongoRepository into HITL service

**Files:**
- Modify: `execution/hitl/service.py`
- Modify: `execution/hitl/factory.py`
- Modify: `container.py`
- Modify: `main.py`

- [ ] **Step 1: Add factory to container.py**

```python
def create_hitl_repository(*, mongo: MongoDAL):
    from execution.repository.hitl_mongo import HITLMongoRepository
    return HITLMongoRepository(mongo=mongo)
```

- [ ] **Step 2: Update HITL factory to accept repository**

In `execution/hitl/factory.py`, the `create_hitl_service()` function currently accepts `database_service`. Add `hitl_repository` parameter and pass it through. The HITLService constructor should accept both the old `database_service` and the new `hitl_repository`, falling back to `database_service` if repository is None (for incremental migration).

- [ ] **Step 3: Replace db_service HITL calls in service.py**

Replace each `self._db.create_hitl_request(...)` with `self._hitl_repo.create(...)`, `self._db.cas_update_hitl_request(...)` with `self._hitl_repo.cas_update(...)`, etc. The mapping is:

| Old (db_service) | New (hitl_repo) |
|-----------------|-----------------|
| `create_hitl_request(data)` | `create(data)` |
| `get_hitl_request(id)` | `get_by_id(id)` |
| `update_hitl_request(id, **kw)` | `update(id, **kw)` |
| `cas_update_hitl_request(id, expected, **kw)` | `cas_update(id, expected, **kw)` |
| `fenced_update_hitl_request(id, claim_id, updates, **kw)` | `fenced_update(id, claim_id, updates, **kw)` |
| `claim_hitl_request(id, **kw)` | `claim(id, **kw)` |
| `get_pending_hitl_requests(room_id)` | `get_pending_for_room(room_id)` |
| `get_pending_hitl_requests_for_message(msg_id)` | `get_pending_for_message(msg_id)` |
| `get_hitl_group_requests(group_id)` | `get_group_requests(group_id)` |
| `count_pending_in_hitl_group(group_id)` | `count_pending_in_group(group_id)` |
| `claim_hitl_group_routing(group_id, claim_id)` | `claim_group_routing(group_id, claim_id)` |
| `release_hitl_group_routing(group_id, claim_id)` | `release_group_routing(group_id, claim_id)` |
| `count_hitl_requests_for_message(msg_id)` | `count_for_message(msg_id)` |

- [ ] **Step 4: Wire in main.py**

```python
from container import create_hitl_repository
_hitl_repo = create_hitl_repository(mongo=mongo_dal)
```

Pass `_hitl_repo` when creating the HITL service.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/ -x -q`

- [ ] **Step 6: Commit**

```bash
git add execution/hitl/service.py execution/hitl/factory.py container.py main.py
git commit -m "refactor(hitl): wire HITLMongoRepository into HITL service"
```

---

## Phase 4: Cancellation & Claims (Category E)

---

### Task 8: Create UserMessageClaimMongoRepository

**Files:**
- Create: `execution/repository/claim_mongo.py`
- Test: `tests/test_claim_repository.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_claim_repository.py
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_mongo():
    mongo = MagicMock()
    col = AsyncMock()
    mongo.collection = MagicMock(return_value=col)
    return mongo, col


@pytest.mark.asyncio
async def test_claim_for_processing(mock_mongo):
    from execution.repository.claim_mongo import UserMessageClaimMongoRepository

    mongo, col = mock_mongo
    col.find_one_and_update = AsyncMock(return_value={"message_id": "msg-1"})
    repo = UserMessageClaimMongoRepository(mongo=mongo)
    result = await repo.claim_for_processing("msg-1")
    assert result is True


@pytest.mark.asyncio
async def test_claim_fails_if_already_claimed(mock_mongo):
    from execution.repository.claim_mongo import UserMessageClaimMongoRepository

    mongo, col = mock_mongo
    col.find_one_and_update = AsyncMock(return_value=None)
    repo = UserMessageClaimMongoRepository(mongo=mongo)
    result = await repo.claim_for_processing("msg-1")
    assert result is False


@pytest.mark.asyncio
async def test_unclaim(mock_mongo):
    from execution.repository.claim_mongo import UserMessageClaimMongoRepository

    mongo, col = mock_mongo
    col.update_one = AsyncMock(return_value=True)
    repo = UserMessageClaimMongoRepository(mongo=mongo)
    result = await repo.unclaim("msg-1")
    assert result is True
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `uv run pytest tests/test_claim_repository.py -v`

- [ ] **Step 3: Implement UserMessageClaimMongoRepository**

Create `execution/repository/claim_mongo.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta

from common.protocols import MongoDAL
from common.utils.time import utcnow


class UserMessageClaimMongoRepository:
    """Atomic claims on room_user_messages for processing coordination."""

    def __init__(self, mongo: MongoDAL, collection_name: str = "room_user_messages") -> None:
        self._messages = mongo.collection(collection_name)

    async def claim_for_processing(self, message_id: str) -> bool:
        doc = await self._messages.find_one_and_update(
            {"message_id": message_id, "processing_claimed": {"$ne": True}},
            {"$set": {"processing_claimed": True, "processing_claimed_at": utcnow()}},
        )
        return doc is not None

    async def unclaim(self, message_id: str) -> bool:
        return await self._messages.update_one(
            {"message_id": message_id},
            {"$set": {"processing_claimed": False, "processing_claimed_at": None}},
        )

    async def claim_or_reclaim(self, message_id: str, stale_threshold: datetime) -> bool:
        doc = await self._messages.find_one_and_update(
            {
                "message_id": message_id,
                "$or": [
                    {"processing_claimed": {"$ne": True}},
                    {"processing_claimed_at": {"$lt": stale_threshold}},
                ],
            },
            {"$set": {"processing_claimed": True, "processing_claimed_at": utcnow()}},
        )
        return doc is not None

    async def refresh_claim(self, message_id: str) -> bool:
        return await self._messages.update_one(
            {"message_id": message_id, "processing_claimed": True},
            {"$set": {"processing_claimed_at": utcnow()}},
        )

    async def save_continuation(self, message_id: str, continuation_data: dict) -> bool:
        return await self._messages.update_one(
            {"message_id": message_id},
            {"$set": {"pending_continuation": continuation_data}},
        )

    async def get_and_clear_continuation(self, message_id: str) -> dict | None:
        doc = await self._messages.find_one_and_update(
            {"message_id": message_id, "pending_continuation": {"$ne": None}},
            {"$set": {"pending_continuation": None}},
        )
        if doc is None:
            return None
        return doc.get("pending_continuation")

    async def claim_stuck_supervisor_trajectory(self, message_id: str) -> bool:
        doc = await self._messages.find_one_and_update(
            {
                "message_id": message_id,
                "supervisor_trajectory_status": "running",
            },
            {"$set": {"supervisor_trajectory_status": "recovering", "recovery_claimed_at": utcnow()}},
        )
        return doc is not None

    async def get_stuck_supervisor_trajectory_messages(
        self, older_than_minutes: int, limit: int = 100
    ) -> list[dict]:
        cutoff = utcnow() - timedelta(minutes=older_than_minutes)
        return await self._messages.find(
            {
                "supervisor_trajectory_status": "running",
                "message_created_at": {"$lt": cutoff},
            },
            sort=[("message_created_at", 1)],
            limit=limit,
        )
```

- [ ] **Step 4: Run test — expect PASS**

Run: `uv run pytest tests/test_claim_repository.py -v`

- [ ] **Step 5: Commit**

```bash
git add execution/repository/claim_mongo.py tests/test_claim_repository.py
git commit -m "feat(execution): add UserMessageClaimMongoRepository"
```

---

### Task 9: Create CancellationMongoRepository

**Files:**
- Create: `execution/repository/cancellation_mongo.py`
- Test: `tests/test_cancellation_repository.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_cancellation_repository.py
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_mongo():
    mongo = MagicMock()
    mongo.collection = MagicMock(side_effect=lambda name: AsyncMock())
    return mongo


@pytest.mark.asyncio
async def test_is_cancelled(mock_mongo):
    from execution.repository.cancellation_mongo import CancellationMongoRepository

    repo = CancellationMongoRepository(mongo=mock_mongo)
    repo._cancelled.find_one = AsyncMock(return_value={"message_id": "msg-1"})
    result = await repo.is_cancelled("msg-1")
    assert result is True


@pytest.mark.asyncio
async def test_is_not_cancelled(mock_mongo):
    from execution.repository.cancellation_mongo import CancellationMongoRepository

    repo = CancellationMongoRepository(mongo=mock_mongo)
    repo._cancelled.find_one = AsyncMock(return_value=None)
    result = await repo.is_cancelled("msg-1")
    assert result is False
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `uv run pytest tests/test_cancellation_repository.py -v`

- [ ] **Step 3: Implement CancellationMongoRepository**

Create `execution/repository/cancellation_mongo.py`:

```python
from __future__ import annotations

from common.protocols import MongoDAL


class CancellationMongoRepository:
    """Owns the cancelled_messages collection and BFS descendant cancellation."""

    def __init__(
        self,
        mongo: MongoDAL,
        cancelled_collection: str = "cancelled_messages",
        agent_messages_collection: str = "room_agent_messages",
    ) -> None:
        self._cancelled = mongo.collection(cancelled_collection)
        self._agent_messages = mongo.collection(agent_messages_collection)

    async def is_cancelled(self, message_id: str) -> bool:
        doc = await self._cancelled.find_one({"message_id": message_id})
        return doc is not None

    async def cancel_descendants(self, message_id: str) -> int:
        """BFS walk of related_message_id chain, marking descendants cancelled."""
        count = 0
        queue = [message_id]
        seen: set[str] = set()

        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)

            children = await self._agent_messages.find(
                {"related_message_id": current}
            )
            for child in children:
                child_id = child.get("message_id")
                if child_id and child_id not in seen:
                    modified = await self._agent_messages.update_one(
                        {"message_id": child_id, "status": {"$nin": ["cancelled"]}},
                        {"$set": {"status": "cancelled"}},
                    )
                    if modified:
                        count += 1
                    queue.append(child_id)

        return count

    async def cancel_agent_messages_by_ids(self, message_ids: list[str]) -> int:
        if not message_ids:
            return 0
        return await self._agent_messages.update_many(
            {"message_id": {"$in": message_ids}, "status": {"$nin": ["cancelled"]}},
            {"$set": {"status": "cancelled"}},
        )
```

- [ ] **Step 4: Run test — expect PASS**

Run: `uv run pytest tests/test_cancellation_repository.py -v`

- [ ] **Step 5: Commit**

```bash
git add execution/repository/cancellation_mongo.py tests/test_cancellation_repository.py
git commit -m "feat(execution): add CancellationMongoRepository"
```

---

## Phase 5: Room Memory Repository (Category F)

**IMPORTANT:** The `context_memory/repository/mongo.py::MemoryMongoRepository` already implements atomic push+trim using MongoDB aggregation pipelines (`$push` + `$slice` in a single `find_one_and_update`). The `ContextMemoryFacade` wraps this. Do NOT create a second repository that reimplements these operations non-atomically — that would cause data races under concurrent writes.

Instead, this task creates a thin `RoomMemoryMongoRepository` for the simpler CRUD methods that `database_service.py` exposes but which are NOT already on `ContextMemoryFacade`. For `push_and_trim_conversation_turn`, `compact_turns_bulk`, `update_room_summary_atomic`, etc., callers should use the existing `ContextMemoryFacade` (already bound via `compaction_service.bind_facade(context_memory_facade)` and `room_memory_service.bind_facade(context_memory_facade)`).

---

### Task 10: Create RoomMemoryMongoRepository (CRUD-only, no push/trim)

**Files:**
- Create: `room/repository/memory_mongo.py`
- Test: `tests/test_room_memory_repository.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_room_memory_repository.py
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_mongo():
    mongo = MagicMock()
    col = AsyncMock()
    mongo.collection = MagicMock(return_value=col)
    return mongo, col


@pytest.mark.asyncio
async def test_get_by_room_id(mock_mongo):
    from room.repository.memory_mongo import RoomMemoryMongoRepository

    mongo, col = mock_mongo
    col.find_one = AsyncMock(return_value={"room_id": "r1", "conversation_history": []})
    repo = RoomMemoryMongoRepository(mongo=mongo)
    result = await repo.get_by_room_id("r1")
    assert result is not None
    assert result["room_id"] == "r1"


@pytest.mark.asyncio
async def test_ensure_room_memory_creates_if_missing(mock_mongo):
    from room.repository.memory_mongo import RoomMemoryMongoRepository

    mongo, col = mock_mongo
    col.find_one = AsyncMock(return_value=None)
    col.insert_one = AsyncMock(return_value="inserted-id")
    repo = RoomMemoryMongoRepository(mongo=mongo)
    result = await repo.ensure_room_memory("r1", {"room_id": "r1", "conversation_history": []})
    assert result is not None
    col.insert_one.assert_called_once()


@pytest.mark.asyncio
async def test_update_by_room_id(mock_mongo):
    from room.repository.memory_mongo import RoomMemoryMongoRepository

    mongo, col = mock_mongo
    col.update_one = AsyncMock(return_value=True)
    repo = RoomMemoryMongoRepository(mongo=mongo)
    result = await repo.update_by_room_id("r1", {"room_summary": {"text": "updated"}})
    assert result is True
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `uv run pytest tests/test_room_memory_repository.py -v`

- [ ] **Step 3: Implement RoomMemoryMongoRepository**

Create `room/repository/memory_mongo.py`:

```python
from __future__ import annotations

from common.protocols import MongoDAL


class RoomMemoryMongoRepository:
    """CRUD-only repository for room_memories collection.

    For atomic push/trim/compact operations, use ContextMemoryFacade which
    wraps context_memory/repository/mongo.py::MemoryMongoRepository — that
    uses MongoDB aggregation pipelines ($push + $slice in a single
    find_one_and_update) to guarantee atomicity under concurrent writes.
    """

    def __init__(self, mongo: MongoDAL, collection_name: str = "room_memories") -> None:
        self._memories = mongo.collection(collection_name)

    async def get_by_room_id(self, room_id: str) -> dict | None:
        return await self._memories.find_one({"room_id": room_id})

    async def get_by_memory_id(self, memory_id: str) -> dict | None:
        return await self._memories.find_one({"memory_id": memory_id})

    async def create(self, memory: dict) -> str:
        doc = dict(memory)
        await self._memories.insert_one(doc)
        return str(doc.get("memory_id") or doc.get("room_id", ""))

    async def ensure_room_memory(self, room_id: str, defaults: dict) -> dict:
        existing = await self._memories.find_one({"room_id": room_id})
        if existing is not None:
            return existing
        doc = dict(defaults)
        doc["room_id"] = room_id
        await self._memories.insert_one(doc)
        return doc

    async def update_by_room_id(self, room_id: str, updates: dict) -> bool:
        return await self._memories.update_one(
            {"room_id": room_id}, {"$set": updates}
        )

    async def update_by_memory_id(self, memory_id: str, updates: dict) -> bool:
        return await self._memories.update_one(
            {"memory_id": memory_id}, {"$set": updates}
        )

    async def delete_by_memory_id(self, memory_id: str) -> bool:
        return await self._memories.delete_one({"memory_id": memory_id})

    async def delete_by_room_id(self, room_id: str) -> bool:
        return await self._memories.delete_one({"room_id": room_id})

    async def get_conversation_history_length(self, room_id: str) -> int:
        doc = await self._memories.find_one({"room_id": room_id})
        if doc is None:
            return 0
        return len(doc.get("conversation_history", []))
```

**NOTE:** The following methods remain on `ContextMemoryFacade` (already wired):
- `push_and_trim_conversation_turn` — uses atomic aggregation pipeline
- `push_and_trim_conversation_turn_if_absent` — dedup-safe atomic append
- `update_room_summary_atomic` — uses `$push` + `$slice` for facts
- `compact_turns_bulk` — uses atomic pipeline with `$elemMatch` guard
- `update_turn_notes` — uses array_filters for positional update
- `get_room_summary_projection` — already on facade
- `list_room_ids_with_memory` — already on facade

Callers needing these operations should use `ContextMemoryFacade` or its underlying `MemoryMongoRepository` via the existing `MemoryRepository` protocol.

- [ ] **Step 4: Run tests — expect PASS**

Run: `uv run pytest tests/test_room_memory_repository.py -v`

- [ ] **Step 5: Commit**

```bash
git add room/repository/memory_mongo.py tests/test_room_memory_repository.py
git commit -m "feat(room): add RoomMemoryMongoRepository for simple CRUD operations"
```

---

## Phase 6: Wire Services to Repositories (remove db_service dependency)

---

### Task 11: Replace db_service in app_shell/room_coordinator_service.py

**Files:**
- Modify: `app_shell/room_coordinator_service.py`
- Modify: `main.py`

- [ ] **Step 1: Change constructor to accept protocols**

Replace the `database_service` fallback pattern with explicit repository injection:

```python
def __init__(
    self,
    *,
    message_repository: MessageRepository | None = None,
    agent_repository: AgentRepository | None = None,
    room_repository: RoomRepository | None = None,
) -> None:
    self._message_repo = message_repository
    self._agent_repo = agent_repository
    self._room_repo = room_repository
```

Add `bind_repositories(...)` method if lazy binding is needed.

- [ ] **Step 2: Replace db_service method calls**

| Old | New |
|-----|-----|
| `self._database_service.get_room_by_room_id(id)` | `self._room_repo.get_by_id(id)` |
| `self._database_service.get_room_user_message_by_message_id(id)` | `self._message_repo.get_user_message_by_id(id)` |
| `self._database_service.get_room_agent_messages_by_related_message_id(id)` | `self._message_repo.get_agent_messages_by_related_id(id)` |
| `self._database_service.get_agent_name_by_agent_id(id)` | `doc = await self._agent_repo.get_by_id(id); name = (doc or {}).get("agent_name")` |
| `self._database_service.add_room_agent_message(msg)` | `await self._message_repo.save_agent_message(msg.model_dump())` |

- [ ] **Step 3: Wire in main.py**

```python
room_coordinator_service.bind_repositories(
    message_repository=_room_deps.room_message_store,
    agent_repository=_agent_deps.agent_repository,
    room_repository=_room_deps.room_registry,
)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/ -x -q`

- [ ] **Step 5: Commit**

```bash
git add app_shell/room_coordinator_service.py main.py
git commit -m "refactor(room): replace db_service in room_coordinator_service with repositories"
```

---

### Task 12: Replace db_service in app_shell/relay_service.py

**Files:**
- Modify: `app_shell/relay_service.py`
- Modify: `main.py`

- [ ] **Step 1: Replace constructor parameter**

Change `db: Any | None = None, database_service: Any | None = None` to accept typed protocols:

```python
def __init__(
    self,
    *,
    mongo: Any,
    message_repository: MessageRepository,
    agent_repository: AgentRepository,
    room_repository: RoomRepository,
    cancellation_repository: CancellationRepository,  # for is_cancelled()
    ...
):
```

- [ ] **Step 2: Replace db method calls**

| Old (self._db.*) | New |
|-----------------|-----|
| `get_room_agent_message_by_message_id(id)` | `self._message_repo.get_agent_message_by_id(id)` |
| `get_agent_by_agent_id(id)` | `self._agent_repo.get_by_id(id)` |
| `get_room_user_message_by_message_id(id)` | `self._message_repo.get_user_message_by_id(id)` |
| `get_room_by_room_id(id)` | `self._room_repo.get_by_id(id)` |
| `is_message_cancelled(id)` | `self._cancellation_repo.is_cancelled(id)` (lives on CancellationMongoRepository from Task 9) |

**Note:** `is_message_cancelled` lives on `CancellationMongoRepository` (Task 9), NOT on `MessageRepository`. The `MessageRepository` protocol does not define this method.

- [ ] **Step 3: Wire in main.py**

Update `init_relay_service()` call to pass repositories from `_room_deps`, `_agent_deps`, and `_cancellation_repo`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/ -x -q`

- [ ] **Step 5: Commit**

```bash
git add app_shell/relay_service.py main.py
git commit -m "refactor(relay): replace db_service with typed repository protocols"
```

---

### Task 13: Replace db_service in app_shell/room_runtime.py

This is the largest consumer (~3400 lines, ~20+ db_service method calls). Split into sub-steps to reduce risk.

**Files:**
- Modify: `app_shell/room_runtime.py`
- Modify: `main.py`

- [ ] **Step 1: Add bind method for repositories (additive — no removals yet)**

Add a new `bind_data_repositories` method alongside the existing `self.database_service`:

```python
def bind_data_repositories(
    self,
    *,
    message_repository: MessageRepository,
    agent_repository: AgentRepository,
    room_repository: RoomRepository,
    agent_group_repository: AgentGroupRepository,
    room_memory_repository: Any,
):
    self._message_repo = message_repository
    self._agent_repo = agent_repository
    self._room_repo = room_repository
    self._agent_group_repo = agent_group_repository
    self._room_memory_repo = room_memory_repository
```

- [ ] **Step 2: Wire in main.py (before any call-site changes)**

```python
room_runtime.bind_data_repositories(
    message_repository=_room_deps.room_message_store,
    agent_repository=_agent_deps.agent_repository,
    room_repository=_room_deps.room_registry,
    agent_group_repository=_agent_group_store,
    room_memory_repository=_room_memory_repo,
)
```

Run: `uv run pytest tests/ -x -q` — should still pass (no call sites changed yet)

- [ ] **Step 3: Replace room CRUD calls**

Replace:
| Old | New |
|-----|-----|
| `self.database_service.get_room_by_room_id(id)` | `await self._room_repo.get_by_id(id)` |
| `self.database_service.get_rooms_by_room_owner_id(id)` | `await self._room_repo.get_by_owner(id)` |
| `self.database_service.update_room_by_room_id(id, room)` | `await self._room_repo.update(id, room.model_dump())` |
| `self.database_service.get_active_runs_by_room_id(id)` | Use execution repos (already wired separately) |

Run: `uv run pytest tests/ -x -q`

- [ ] **Step 4: Replace agent/group lookup calls**

Replace:
| Old | New |
|-----|-----|
| `self.database_service.get_agent_by_agent_id(id)` | `await self._agent_repo.get_by_id(id)` |
| `self.database_service.get_agent_group_by_id(id)` | `await self._agent_group_repo.get_by_id(id)` |
| `self.database_service.get_all_active_agents(uid)` | `await self._agent_repo.list_visible(user_id=uid, active_only=True)` |
| `self.database_service.get_agents_with_conditions(q)` | `await self._agent_repo.list_visible(query=q)` |

Note: Repositories return dicts. Call sites expecting Agent/AgentGroup models need `Model.model_validate(doc)` at the boundary.

Run: `uv run pytest tests/ -x -q`

- [ ] **Step 5: Replace message persistence calls**

Replace:
| Old | New |
|-----|-----|
| `self.database_service.add_room_agent_message(msg)` | `await self._message_repo.save_agent_message(msg.model_dump())` |
| `self.database_service.add_room_user_message(msg)` | `await self._message_repo.save_user_message(msg.model_dump())` |
| `self.database_service.update_room_user_message_by_message_id(id, msg)` | `await self._message_repo.update_user_message(id, msg.model_dump())` |

Run: `uv run pytest tests/ -x -q`

- [ ] **Step 6: Replace memory calls**

Replace:
| Old | New |
|-----|-----|
| `self.database_service.get_room_memory_by_room_id(id)` | `await self._room_memory_repo.get_by_room_id(id)` |

Run: `uv run pytest tests/ -x -q`

- [ ] **Step 7: Remove singleton import**

Remove `from app_shell.database_service import db_service` and `self.database_service = db_service`. Verify no remaining references.

Run: `uv run pytest tests/ -x -q`

- [ ] **Step 8: Commit**

```bash
git add app_shell/room_runtime.py main.py
git commit -m "refactor(room_runtime): eliminate db_service singleton dependency"
```

---

### Task 14: Replace db_service in app_shell/a2a_runtime.py

The A2A service uses `self._task_db` (bound to `_db_svc`) for task tracking, webhook tokens, agent lookups, and call counting.

**Files:**
- Modify: `app_shell/a2a_runtime.py`
- Modify: `main.py`

- [ ] **Step 1: Add repository bind methods**

```python
def bind_task_repositories(
    self,
    *,
    task_message_repo: TaskMessageRepository,
    message_repo: MessageRepository,
    agent_repo: AgentRepository,
):
    self._task_message_repo = task_message_repo
    self._message_repo = message_repo
    self._agent_repo = agent_repo
```

- [ ] **Step 2: Replace task_db calls (incremental)**

The major calls from the exploration:
| Old (`task_db.*`) | New |
|-------------------|-----|
| `check_task_limits(user_id, room_id, states)` | `count = await self._task_message_repo.count_non_terminal_tasks_for_user(...)`; raise if over limit |
| `generate_webhook_token()` | Move to standalone utility (already `import secrets; secrets.token_urlsafe(32)`) |
| `hash_webhook_token(token)` | Move to standalone utility (`hashlib.sha256(token.encode()).hexdigest()`) |
| `enable_task_tracking_on_message(...)` | `await self._task_message_repo.enable_task_tracking(...)` |
| `update_task_on_message(msg_id, task_data, text)` | `await self._task_message_repo.update_task_on_message(...)` |
| `get_room_agent_message_by_message_id(id)` | `await self._message_repo.get_agent_message_by_id(id)` |
| `update_webhook_token_hash_on_message(id, hash)` | `await self._task_message_repo.update_webhook_token_hash(id, hash)` |
| `get_agent_by_agent_id(id)` | `await self._agent_repo.get_by_id(id)` |
| `mongo.increment_agent_call_count(id)` | `await self._agent_repo.increment_agent_call_count(id, success=True)` |

- [ ] **Step 3: Wire in main.py**

After task_message_repo creation:
```python
a2a_service.bind_task_repositories(
    task_message_repo=_task_message_repo,
    message_repo=_room_deps.room_message_store,
    agent_repo=_agent_deps.agent_repository,
)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/ -x -q`

- [ ] **Step 5: Commit**

```bash
git add app_shell/a2a_runtime.py main.py
git commit -m "refactor(a2a): replace task_db with typed repositories"
```

---

### Task 15: Replace db_service in execution/orchestration/room_message_center.py

**This is the hardest single task.** `room_message_center.py` receives `database_service=_db_svc` in its factory and uses it extensively. Critically, it passes `database_service` to **four sub-services** that each have their own db_service calls:

- `AgentResponseHandler` (`execution/dispatch/response_handler.py`) — uses `get_room_agent_message_by_message_id`, `update_room_agent_message_by_message_id`, `resolve_client_request_id_for_agent_message`
- `AgentMessageProcessor` (`execution/dispatch/agent_message_processor.py`) — uses `add_room_agent_message`, `upsert_room_agent_message`, `get_room_agent_message_by_message_id`
- `QueueExecutor` (`execution/orchestration/queue_executor.py`) — uses `get_room_user_message_by_message_id`, `get_room_by_room_id`, `claim_user_message_for_processing`
- `SupervisorExecutor` (`execution/orchestration/supervisor_executor.py`) — uses `get_room_by_room_id`, `claim_stuck_supervisor_trajectory`

Each sub-service needs migration. Approach: pass a typed "execution data access" facade that bundles the repository references, rather than threading 5 separate repos to each sub-service.

**Files:**
- Modify: `execution/orchestration/room_message_center.py`
- Modify: `execution/orchestration/factory.py`
- Modify: `execution/dispatch/response_handler.py`
- Modify: `execution/dispatch/agent_message_processor.py`
- Modify: `execution/orchestration/queue_executor.py`
- Modify: `execution/orchestration/supervisor_executor.py`
- Modify: `main.py`

- [ ] **Step 1: Create an ExecutionDataAccess protocol**

Define in `execution/ports.py` or a new `execution/data_access.py`:

```python
from typing import Protocol

class ExecutionDataAccess(Protocol):
    """Bundles repository access needed by execution sub-services."""
    message_repository: MessageRepository
    room_repository: RoomRepository
    claim_repository: UserMessageClaimRepository
    cancellation_repository: CancellationRepository
    task_message_repository: TaskMessageRepository
```

Or use a simple dataclass:
```python
@dataclass(frozen=True)
class ExecutionDataAccess:
    message_repository: MessageRepository
    room_repository: RoomRepository
    claim_repository: UserMessageClaimRepository
    cancellation_repository: CancellationRepository
    task_message_repository: TaskMessageRepository
```

- [ ] **Step 2: Add repository parameters to factory**

Update `create_room_message_center()` in `execution/orchestration/factory.py` to accept:
```python
def create_room_message_center(
    *,
    ...existing params...,
    data_access: ExecutionDataAccess,
):
```

- [ ] **Step 3: Replace db_service calls in room_message_center.py**

| Old | New |
|-----|-----|
| `self.database_service.get_room_by_room_id(id)` | `await self._data.room_repository.get_by_id(id)` |
| `self.database_service.get_room_user_message_by_message_id(id)` | `await self._data.message_repository.get_user_message_by_id(id)` |
| `self.database_service.claim_user_message_for_processing(id)` | `await self._data.claim_repository.claim_for_processing(id)` |
| `self.database_service.claim_or_reclaim_user_message(id, threshold)` | `await self._data.claim_repository.claim_or_reclaim(id, threshold)` |
| `self.database_service.unclaim_user_message(id)` | `await self._data.claim_repository.unclaim(id)` |
| `self.database_service.refresh_processing_claim(id)` | `await self._data.claim_repository.refresh_claim(id)` |
| `self.database_service.cancel_descendants(id)` | `await self._data.cancellation_repository.cancel_descendants(id)` |

- [ ] **Step 4: Migrate AgentResponseHandler**

Replace `self._db` in response_handler.py:
| Old | New |
|-----|-----|
| `self._db.get_room_agent_message_by_message_id(id)` | `await self._data.message_repository.get_agent_message_by_id(id)` |
| `self._db.update_room_agent_message_by_message_id(id, msg)` | `await self._data.message_repository.update_agent_message(id, msg.model_dump())` |

- [ ] **Step 5: Migrate AgentMessageProcessor**

Replace `self.db` in agent_message_processor.py:
| Old | New |
|-----|-----|
| `self.db.add_room_agent_message(msg)` | `await self._data.message_repository.save_agent_message(msg.model_dump())` |
| `self.db.upsert_room_agent_message(msg)` | `await self._data.message_repository.save_agent_message(msg.model_dump())` |

- [ ] **Step 6: Migrate QueueExecutor and SupervisorExecutor**

Replace db_service in queue_executor.py and supervisor_executor.py with `self._data.*` calls.

- [ ] **Step 7: Wire in main.py**

```python
from execution.data_access import ExecutionDataAccess

_exec_data_access = ExecutionDataAccess(
    message_repository=_room_deps.room_message_store,
    room_repository=_room_deps.room_registry,
    claim_repository=_claim_repo,
    cancellation_repository=_cancellation_repo,
    task_message_repository=_task_message_repo,
)
```

Pass `data_access=_exec_data_access` to `create_room_message_center()`.

- [ ] **Step 8: Run tests**

Run: `uv run pytest tests/ -x -q`

- [ ] **Step 9: Commit**

```bash
git add execution/orchestration/room_message_center.py execution/orchestration/factory.py execution/dispatch/response_handler.py execution/dispatch/agent_message_processor.py execution/orchestration/queue_executor.py execution/orchestration/supervisor_executor.py main.py
git commit -m "refactor(orchestration): replace db_service in room_message_center and sub-services with repositories"
```

---

### Task 16: Replace mongodb collection dependencies in all background jobs

Three jobs receive raw `mongodb.*_collection` references that break once the `mongodb` singleton is removed:

1. **stale_task_checker** — `rooms_collection=mongodb.rooms_collection` (used for `update_many` to clear stale `processing_message_id`)
2. **orphaned_upload_cleaner** — `file_uploads_collection=mongodb.file_uploads_collection` and `room_user_messages_collection=mongodb.room_user_messages_collection`
3. **compaction_sweep** — `room_memories_collection=mongodb.room_memories_collection` and `get_room_ids_with_non_terminal_runs=mongodb.get_room_ids_with_non_terminal_runs`

**Files:**
- Modify: `jobs/stale_task_checker.py`
- Modify: `jobs/cleanup_orphaned_uploads.py`
- Modify: `jobs/compaction_sweep.py`
- Modify: `main.py`

- [ ] **Step 1: Replace stale_task_checker rooms_collection**

Replace:
```python
rooms_collection=mongodb.rooms_collection,
```
With:
```python
rooms_collection=mongo_dal.collection("rooms"),
```

- [ ] **Step 2: Replace orphaned_upload_cleaner collections**

Replace:
```python
OrphanedUploadCleanerDeps(
    file_uploads_collection=mongodb.file_uploads_collection,
    room_user_messages_collection=mongodb.room_user_messages_collection,
    object_storage=s3_service,
)
```
With:
```python
OrphanedUploadCleanerDeps(
    file_uploads_collection=mongo_dal.collection("file_uploads"),
    room_user_messages_collection=mongo_dal.collection("room_user_messages"),
    object_storage=s3_service,
)
```

- [ ] **Step 3: Replace compaction_sweep dependencies**

Replace:
```python
CompactionSweepDeps(
    room_memories_collection=mongodb.room_memories_collection,
    get_room_ids_with_non_terminal_runs=mongodb.get_room_ids_with_non_terminal_runs,
    compaction_service=compaction_service,
)
```
With:
```python
CompactionSweepDeps(
    room_memories_collection=mongo_dal.collection("room_memories"),
    get_room_ids_with_non_terminal_runs=_execution_repos["run_repository"].get_room_ids_with_non_terminal_runs,
    compaction_service=compaction_service,
)
```

Note: `get_room_ids_with_non_terminal_runs` is already on `RunMongoRepository` (line 63 of `execution/repository/mongo.py`). Pass the method reference directly.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/ -x -q`

- [ ] **Step 5: Commit**

```bash
git add jobs/stale_task_checker.py jobs/cleanup_orphaned_uploads.py jobs/compaction_sweep.py main.py
git commit -m "refactor(jobs): replace mongodb.* collection refs with DAL collection adapters"
```

---

### Task 17: Replace db_service in routes (a2a_task_routes, sse_routes, room_routes)

**Files:**
- Modify: `api_gateway/routes/a2a_task_routes.py`
- Modify: `api_gateway/routes/sse_routes.py`
- Modify: `api_gateway/routes/room_routes.py`
- Modify: `main.py`

- [ ] **Step 1: Create A2ATaskReader adapter in container.py**

The routes already type their dependency as `A2ATaskReader` protocol. Create an adapter:

```python
def create_a2a_task_reader(
    *,
    task_message_repo: TaskMessageRepository,
    message_repo: MessageRepository,
    room_repo: RoomRepository,
) -> Any:
    class _A2ATaskReaderAdapter:
        async def get_pending_task_messages_for_user(self, user_id, states):
            docs = await task_message_repo.get_pending_task_messages_for_user(user_id, states)
            from models.room import RoomAgentMessage
            return [RoomAgentMessage.model_validate(d) for d in docs]

        async def get_room_agent_message_by_message_id(self, message_id):
            doc = await message_repo.get_agent_message_by_id(message_id)
            if doc is None:
                return None
            from models.room import RoomAgentMessage
            return RoomAgentMessage.model_validate(doc)

        async def get_room_by_room_id(self, room_id):
            doc = await room_repo.get_by_id(room_id)
            if doc is None:
                return None
            from models.room import Room
            return Room.model_validate(doc)

        async def get_room_user_message_by_message_id(self, message_id):
            doc = await message_repo.get_user_message_by_id(message_id)
            if doc is None:
                return None
            from models.room import RoomUserMessage
            return RoomUserMessage.model_validate(doc)

        async def get_task_messages_for_room(self, room_id, *, limit=50):
            docs = await task_message_repo.get_task_messages_for_room(room_id, limit)
            from models.room import RoomAgentMessage
            return [RoomAgentMessage.model_validate(d) for d in docs]

    return _A2ATaskReaderAdapter()
```

- [ ] **Step 2: Wire in main.py**

Replace:
```python
a2a_tasks.bind_a2a_task_dependencies(_db_svc)
sse.bind_sse_dependencies(_db_svc, sse_manager)
```
With:
```python
_task_reader = create_a2a_task_reader(
    task_message_repo=_task_message_repo,
    message_repo=_room_deps.room_message_store,
    room_repo=_room_deps.room_registry,
)
a2a_tasks.bind_a2a_task_dependencies(_task_reader)
sse.bind_sse_dependencies(_task_reader, sse_manager)
```

- [ ] **Step 3: Update room_routes.py**

`room_routes.py` only needs `get_room_by_room_id` for ownership verification. It already receives a `database_service` via `bind_room_dependencies`. Change the type to accept the room facade directly (which it already does for room operations).

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/ -x -q`

- [ ] **Step 5: Commit**

```bash
git add api_gateway/routes/a2a_task_routes.py api_gateway/routes/sse_routes.py api_gateway/routes/room_routes.py container.py main.py
git commit -m "refactor(routes): wire routes to repository-backed protocol adapters"
```

---

## Phase 7: Final Cleanup

---

### Task 18: Remove database.* imports from main.py

**Files:**
- Create: `dal/index_registry.py`
- Modify: `main.py`
- Modify: `tests/fixtures/dal_database_convergence_manifest.json`

- [ ] **Step 1: Create `dal/index_registry.py` — centralized index creation**

```python
from __future__ import annotations

from common.protocols import MongoDAL
from common.utils.logger import get_logger

logger = get_logger(__name__)


class IndexRegistry:
    """Declarative index management via MongoDAL."""

    def __init__(self, mongo: MongoDAL) -> None:
        self._mongo = mongo

    async def ensure_all(self) -> None:
        await self._ensure_agent_indexes()
        await self._ensure_capability_issue_indexes()
        await self._ensure_run_lifecycle_indexes()
        await self._ensure_room_quotes_indexes()
        await self._ensure_context_memory_indexes()

    async def ensure_task_tracking_indexes(self) -> None:
        col = self._mongo.collection("room_agent_messages")
        await col.create_index(
            [("has_task_tracking", 1), ("task_data.status.state", 1), ("task_updated_at", 1)],
            name="task_stale_idx",
        )
        await col.create_index(
            [("has_task_tracking", 1), ("room_id", 1)],
            name="task_room_idx",
        )

    async def ensure_hitl_indexes(self) -> None:
        col = self._mongo.collection("hitl_requests")
        await col.create_index([("room_id", 1), ("status", 1)], name="room_status_idx")
        await col.create_index([("user_message_id", 1), ("status", 1)], name="user_message_status_idx")
        await col.create_index([("group_id", 1)], name="group_idx")
        await col.create_index([("continuation_message_id", 1)], name="continuation_idx")

    async def _ensure_agent_indexes(self) -> None:
        col = self._mongo.collection("agents")
        await col.create_index([("agent_id", 1)], unique=True, name="agent_id_unique")
        await col.create_index([("provider_id", 1)], name="provider_id_idx")
        await col.create_index([("normalized_url", 1)], name="normalized_url_idx")

    async def _ensure_capability_issue_indexes(self) -> None:
        col = self._mongo.collection("agent_capability_issues")
        await col.create_index([("agent_id", 1), ("status", 1)], name="agent_status_idx")

    async def _ensure_run_lifecycle_indexes(self) -> None:
        runs = self._mongo.collection("runs")
        await runs.create_index([("run_id", 1)], unique=True, name="run_id_unique")
        await runs.create_index([("room_id", 1), ("state", 1)], name="room_state_idx")
        events = self._mongo.collection("run_events")
        await events.create_index([("run_id", 1), ("seq", 1)], name="run_seq_idx")

    async def _ensure_room_quotes_indexes(self) -> None:
        col = self._mongo.collection("room_quotes")
        await col.create_index([("room_id", 1)], name="room_id_idx")
        await col.create_index([("quote_id", 1)], unique=True, name="quote_id_unique")

    async def _ensure_context_memory_indexes(self) -> None:
        col = self._mongo.collection("room_memories")
        await col.create_index([("room_id", 1)], unique=True, name="room_id_unique")
```

Add factory to `container.py`:
```python
def create_index_registry(*, mongo: MongoDAL):
    from dal.index_registry import IndexRegistry
    return IndexRegistry(mongo=mongo)
```

- [ ] **Step 2: Replace mongodb index calls in main.py**

Replace:
```python
await mongodb.ensure_agent_indexes()
await mongodb.create_capability_issue_indexes()
await mongodb.create_run_lifecycle_indexes()
await mongodb.create_room_quotes_indexes()
```
With:
```python
_index_registry = create_index_registry(mongo=mongo_dal)
await _index_registry.ensure_all()
```

And replace:
```python
await mongodb.create_task_tracking_indexes()
```
With:
```python
await _index_registry.ensure_task_tracking_indexes()
```

And replace:
```python
await db_service.ensure_hitl_indexes()
```
With:
```python
await _index_registry.ensure_hitl_indexes()
```

- [ ] **Step 3: Replace mongodb connection lifecycle**

Replace `await mongodb.connect()` / `await mongodb.close_database_connection()` with DAL lifecycle:
- `await mongo_dal.connect()` (already called)
- `await mongo_dal.close()` (already called in shutdown)

The `mongodb.client is not None` guard becomes a check on `mongo_dal`:
```python
if await mongo_dal.is_connected():
```

- [ ] **Step 4: Replace job collection references**

`StaleTaskCheckerDeps.rooms_collection` currently receives `mongodb.rooms_collection`. Replace with a repository query:
```python
stale_task_checker.set_runtime_deps(
    StaleTaskCheckerDeps(
        ...
        rooms_collection=mongo_dal.collection("rooms"),  # MongoCollectionAdapter
        ...
    )
)
```

Or better: refactor `StaleTaskCheckerDeps` to accept a `RoomRepository` and use `room_repo.get_by_id()` instead of raw collection access.

- [ ] **Step 5: Remove `from database.mongodb import mongodb` and `from database.pinecone_db import pinecone_db`**

- [ ] **Step 6: Update convergence gate manifest**

Remove `main.py` from `database_singleton_import_blockers` and `pinecone_singleton_import_blockers`.

- [ ] **Step 7: Run convergence gate test**

Run: `uv run pytest tests/test_dal_database_convergence_gate.py -v`
Expected: All pass with empty blocker lists

- [ ] **Step 8: Run full test suite**

Run: `uv run pytest tests/ -x -q`

- [ ] **Step 9: Commit**

```bash
git add dal/index_registry.py container.py main.py tests/fixtures/dal_database_convergence_manifest.json
git commit -m "refactor(main): remove database.* singleton imports, add IndexRegistry"
```

---

### Task 19: Gut DatabaseService to compatibility stub

**Files:**
- Modify: `app_shell/database_service.py`

- [ ] **Step 1: Reduce to stub**

Replace the entire file with:

```python
"""DEPRECATED: This module exists only for migration compatibility.

All production data access now goes through module-owned repositories
constructed in container.py. See docs/MODULAR_DECOUPLING_DESIGN.md.

If you're seeing import errors, the caller should be migrated to use
the appropriate repository protocol instead.
"""

from __future__ import annotations

from common.utils.logger import get_logger

logger = get_logger(__name__)


class DatabaseService:
    """Compatibility stub — all methods raise NotImplementedError."""

    def __getattr__(self, name: str):
        raise NotImplementedError(
            f"DatabaseService.{name}() is deprecated. "
            f"Use the appropriate repository from container.py instead."
        )


db_service = DatabaseService()
```

- [ ] **Step 2: Verify no production imports remain**

Run: `grep -rn "from app_shell.database_service import" --include="*.py" | grep -v __pycache__ | grep -v tests/`

Expected: Only `main.py` (if still importing for legacy bind) or nothing.

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/ -x -q`

- [ ] **Step 4: Verify app starts**

Run: `uv run python -c "from main import app; print('import OK')"`

- [ ] **Step 5: Commit**

```bash
git add app_shell/database_service.py
git commit -m "refactor(database_service): gut to compatibility stub — all access via repositories"
```

---

### Task 20: Final verification

- [ ] **Step 1: Run convergence verification**

```bash
grep -rn "from database\." --include="*.py" | grep -v __pycache__ | grep -v tests/ | grep -v scripts/ | grep -v "database/"
```
Expected: Empty or only DAL construction in container.py

- [ ] **Step 2: Run db_service usage check**

```bash
grep -rn "db_service\|database_service" --include="*.py" app_shell/ execution/ | grep -v __pycache__ | grep -v tests/
```
Expected: Only deprecation stubs or type annotations

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/ -q`
Expected: All pass

- [ ] **Step 4: Verify app startup**

Run: `uv run python -c "from main import app; print('import OK')"`
Expected: `import OK`

- [ ] **Step 5: Final commit (if any cleanup needed)**

```bash
git commit -m "chore: final DAL migration verification — database_service fully deprecated"
```

---

## Execution Notes

**Recommended session order:**
- Session 1: Tasks 1-3 (Phase 1: Room Messages & Agent Groups)
- Session 2: Tasks 4-5 (Phase 2: Task Tracking Repository)
- Session 3: Tasks 6-7 (Phase 3: HITL Repository)
- Session 4: Tasks 8-10 (Phase 4+5: Cancellation/Claims + Room Memory)
- Session 5: Tasks 11-13 (Phase 6a: Wire coordinator, relay, room_runtime)
- Session 6: Tasks 14-15 (Phase 6b: Wire a2a_runtime + room_message_center — hardest tasks)
- Session 7: Tasks 16-17 (Phase 6c: Jobs + routes)
- Session 8: Tasks 18-20 (Phase 7: Remove legacy imports & gut DatabaseService)

**Critical invariant:** After every task, `uv run pytest tests/ -x -q` must pass. If a test breaks, fix it before moving to the next task.

**Model validation boundary:** Repositories return `dict`. Call sites that need Pydantic models should validate at the boundary: `Model.model_validate(doc)`. Do NOT put model validation inside repositories — they stay at the dict level. Import models from their actual locations:
- `from models.agent import Agent` (not `models.agent_group`)
- `from models.agent_group import AgentGroup`
- `from models.room import Room, RoomUserMessage, RoomAgentMessage`

**Atomic operations:** The HITLMongoRepository (Task 6) uses `find_one_and_update` for CAS semantics. The MongoCollectionAdapter supports this method — verify by reading `dal/mongo/client.py:41`.

**Terminal-state guard (CRITICAL):** Task 4's `update_task_on_message` and `update_task_state` MUST include the `$nin: terminal_values` filter in the query to prevent concurrent overwrites of final states. This is a correctness invariant — not an optimization.

**Memory operations — use existing facade:** Do NOT reimplement `push_and_trim_conversation_turn`, `compact_turns_bulk`, or `update_room_summary_atomic` in a new repository. These use MongoDB aggregation pipelines for atomicity and already exist in `context_memory/repository/mongo.py::MemoryMongoRepository`, wrapped by `ContextMemoryFacade`. Callers needing these operations use the existing facade binding.

**Collection name consistency:** Always verify collection names match what `database/mongodb.py` uses: `agents`, `rooms`, `room_user_messages`, `room_agent_messages`, `room_memories`, `agent_groups`, `hitl_requests`, `cancelled_messages`, `quoted_snippets`, `room_quotes`.

**A2A runtime (Task 14):** The `a2a_service._task_db` is currently `_db_svc`. After gutting DatabaseService (Task 19), the A2A runtime WILL break unless Task 14 is completed first. Task ordering ensures this.

**room_message_center (Task 15):** The hardest single task. It passes `database_service` to four sub-services: AgentResponseHandler, AgentMessageProcessor, QueueExecutor, SupervisorExecutor. The plan uses an `ExecutionDataAccess` dataclass to bundle repos and pass them through, avoiding 5-parameter threading to each sub-service. All four sub-services must be migrated in the same task to maintain consistency.
