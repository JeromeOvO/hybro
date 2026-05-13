# Phase 4 Room Module Implementation Plan

> **For agentic workers:** REQUIRED: Use `superpowers:subagent-driven-development` if subagents are available, or `superpowers:executing-plans` to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the Room business module so `room/` owns room CRUD, room membership resolution, raw room message persistence, message history reads, and room ownership checks through Common protocols while preserving existing room endpoint behavior.

**Architecture:** Add a `room/` package with `RoomFacade` implementing `RoomRegistry`, `RoomManagement`, `RoomMessageStore`, `RoomHistoryReader`, and `RoomOwnershipReader`. The facade depends only on Common DTOs/protocols, `RoomRepository`, `MessageRepository`, Agent read protocols, and a membership seed source protocol; legacy `services/room_services.py`, `modules/RoomCenter.py`, and the room-facing parts of `modules/RoomMessageCenter.py` become C3 migration wrappers that delegate through `bind_facade()` until Phase 9 cleanup.

**Tech Stack:** Python 3.11+, FastAPI, MongoDAL, Pydantic DTOs in `common.dto`, Agent protocol dependencies from Phase 3, pytest, pytest-asyncio, AST import-boundary tests.

---

## Scope

Include:
- Create the `room/` package on `phase-4-room-module`.
- Implement `RoomFacade` as the concrete implementation for all five Room protocols in `common/protocols/room_protocols.py`.
- Implement `RoomMongoRepository` against `MongoDAL`, extending `RoomRepository` only where Phase 4 requires missing room query/write operations.
- Implement `MessageMongoRepository` against `MongoDAL`, extending `MessageRepository` only where Phase 4 requires message history, delete, and status operations.
- Move room CRUD, canonical membership seed resolution, legacy room agent set normalization, visibility validation, room ownership checks, raw user-message persistence, raw agent-message persistence, and room message history reads into the Room module.
- Preserve `dispatch_root_message_id` on user-message create responses by asserting `dispatch_root_message_id == message_id` for `/roomCenter/createAndParseUserMessage` and `/roomCenter/sendMessage`.
- Add the C3 migration adapter pattern in `services/room_services.py`, `modules/RoomCenter.py`, and room-persistence seams in `modules/RoomMessageCenter.py`: delegated methods raise `RuntimeError` before bind.
- Wire a `RoomDeps` sub-container in `container.py` alongside existing `AgentDeps` and bind old singletons during `main.py` lifespan startup.
- Add unit, golden, endpoint compatibility, migration adapter, and import-boundary tests.

Exclude:
- Full API route extraction from `api/room_center.py`; Phase 4 can keep routes as legacy adapters as long as endpoint responses stay identical.
- Moving message delivery, background processing, room locks, queue execution, direct transport, relay dispatch, SSE delivery, task tracking, HITL, or supervisor orchestration out of `modules/RoomMessageCenter.py`. That work belongs to Phase 6/7.
- Moving context assembly, room memory compaction, room facts, or `room_memories` ownership into `room/`; that belongs to Phase 5.
- Moving file upload, S3 cleanup, or artifact cleanup into `room/`; that belongs to Platform work.
- Removing `services/`, `modules/`, or legacy singleton imports globally; removal is Phase 9.
- Rewriting `api/room_center.py` authentication and ownership checks beyond wiring compatibility helpers.
- Changing Agent module behavior. Room membership may depend on Agent protocols, but `room/**` must not import `agent/**`, `services/**`, or `models/**`.

## Current Repo Check

The original prompt described Phase 4 as starting from branch `phase-3-agent-module`, but Phase 3 review fixes have since landed on `main`. Before implementation, create `phase-4-room-module` from the right base:
- Preferred path after the review fixes: `git switch main`, verify it contains the Phase 3 review-fix commits, then `git switch -c phase-4-room-module`.
- If using `phase-3-agent-module`, first fast-forward or rebase it onto the current `main`; do not create Phase 4 from the stale local `phase-3-agent-module` branch if it lacks the review fixes.
- The current checkout used to update this plan is based on later `main` commits including `Simplify hub liveness reader contract`, where `HubLivenessReader.is_hub_online()` is async and authoritative. Do not assume the implementation checkout is identical until Task 0 verifies it.

IMPORTANT: The prompt names `services/room_service.py`, but the current repo file is `services/room_services.py`. Phase 4 should modify `services/room_services.py` unless Task 0 finds a renamed file on the implementation branch.

IMPORTANT: `RoomMessageCenter` is not an Execution extraction in Phase 4. It gets a C3 binding seam for Room facade dependencies, but its SSE integration, locks, queueing, supervisor flow, direct transport, relay dispatch, and task tracking stay in the legacy layer until Phase 6/7.

IMPORTANT: `dispatch_root_message_id` currently belongs to `RoomCenterUserMessageResponse`, not `RoomCenterRoomSettingResponse`. Phase 4 gate tests must assert it on message-create responses (`createAndParseUserMessage` and `sendMessage`) without adding unrelated fields to room-setting responses.

Branch used for implementation: create `phase-4-room-module` from current `main` after the Phase 3 review fixes, or from `phase-3-agent-module` only after that branch has been updated to the same Phase 3 review-fix state.

## File Inventory

Create:
- `room/__init__.py`: exports `RoomFacade`, `RoomMongoRepository`, and `MessageMongoRepository`.
- `room/facade.py`: concrete implementation of `RoomRegistry`, `RoomManagement`, `RoomMessageStore`, `RoomHistoryReader`, and `RoomOwnershipReader`.
- `room/membership.py`: pure membership seed resolution helpers for `manual`, `saved_group`, and `all_current_agents`.
- `room/translators.py`: pure dict/DTO conversion helpers. This file must not import `models.*` or A2A SDK types.
- `room/message_graph.py`: pure helpers for message ordering, combined history normalization, thread selection, and status-field updates.
- `room/repository/__init__.py`: exports `RoomMongoRepository` and `MessageMongoRepository`.
- `room/repository/mongo.py`: `RoomRepository` and `MessageRepository` implementations using `MongoDAL`.
- `services/room_membership_source.py`: legacy adapter implementing the seed-source protocol for saved groups and current visible agents until Agent group extraction is complete.
- `tests/test_room_protocols.py`: runtime protocol conformance, exports, package list, and import-boundary tests.
- `tests/test_room_repository.py`: repository tests against fake `MongoCollection` instances.
- `tests/test_room_facade.py`: facade unit tests with fake room repository, message repository, agent registry, and membership source.
- `tests/test_room_membership.py`: focused tests for manual, saved group, all current agents, visibility validation, provenance, and legacy normalization.
- `tests/test_room_golden.py`: golden behavior tests for create, delete, update, membership, user-message persistence, agent-message persistence, history, and ownership.

Delete if porting from another branch:
- `room/ports.py`: remove any temporary bridge that delegates reads to `services.room_services` or `services.database_service`. The Phase 4 facade must own room logic directly.
- Any `room/**` scaffold that imports `services`, `modules`, `api`, `database`, `models`, `main`, `container`, `agent`, `a2a_adapter`, or `llm_gateway`.

Modify:
- `common/dto/room.py`: add a saved-group snapshot DTO if the implementation branch lacks one.
- `common/protocols/room_protocols.py`: add the membership seed source protocol if needed by the facade constructor.
- `common/protocols/repository_protocols.py`: extend `RoomRepository` and `MessageRepository` only for missing Phase 4 queries/writes.
- `common/protocols/__init__.py`: export any new protocol names.
- `pyproject.toml`: add `room` and `room.repository` to `[tool.setuptools].packages`.
- `container.py`: add `RoomDeps` and `create_room_deps()` alongside existing `AgentDeps`.
- `services/room_services.py`: convert Room CRUD, membership, and raw message methods to C3 facade delegation with legacy response conversion.
- `services/room_membership_source.py`: implement the adapter for saved groups and current-agent reads using legacy services while keeping `room/**` decoupled.
- `modules/RoomCenter.py`: keep as a legacy adapter that delegates to `services.room_services`.
- `modules/RoomMessageCenter.py`: add C3 binding for `RoomMessageStore`/`RoomHistoryReader` while keeping orchestration and SSE logic legacy.
- `api/room_center.py`: only adjust imports/wiring if needed; endpoint shapes should remain identical.
- `main.py`: build `RoomDeps` during lifespan startup after `AgentDeps`, bind room legacy adapters before serving traffic.
- Existing room tests: update to bind fake facades where they construct migrated legacy services directly.

Reference-only:
- `docs/MODULAR_DECOUPLING_DESIGN.md`: Phase 4 description, Room protocols, repository protocol pattern, C3 adapter pattern, dependency rules.
- `docs/superpowers/plans/2026-05-10-phase-3-agent-module.md`: exact plan structure and migration style.
- `services/room_services.py`: legacy room lifecycle, membership, message persistence, response compatibility, and `dispatch_root_message_id` behavior.
- `services/database_service.py`: room, room user message, room agent message, active run, and agent group persistence call sites.
- `modules/RoomCenter.py`: current legacy wrapper shape.
- `modules/RoomMessageCenter.py`: complex execution/SSE behavior to preserve outside `room/`.
- `api/room_center.py`: endpoint request parsing, auth, and response shapes.
- `models/room.py`: legacy model field names and provenance enums.
- `models/request.py`: `RoomCenter*Request` compatibility fields.
- `models/response.py`: `RoomCenter*Response` compatibility fields.
- `common/protocols/room_protocols.py`: target facade protocols.
- `common/protocols/repository_protocols.py`: repository contract.
- `common/protocols/hub_protocols.py`: current async `HubLivenessReader` contract and `validate_hub_liveness_reader()` review-fix behavior.
- `common/protocols/dal_protocols.py`: `MongoDAL` and `MongoCollection`.
- `common/dto/room.py`: target DTOs.
- `agent/facade.py`, `agent/repository/mongo.py`, `agent/translators.py`, `container.py`, `services/agent_service.py`, `services/relay_service.py`, `main.py`: Phase 3 implementation patterns, including review-fix changes for `AgentRepository.list_visible(query=...)`, async hub liveness validation, and the single `RelayHubLivenessReader` bind path.

## Dependency Diagram

```text
api/room_center.py
  -> modules.RoomCenter.RoomCenter                 legacy adapter, migration only
    -> services.room_services.RoomServices         legacy response converter
      -> room.facade.RoomFacade                    protocol implementation
        -> common.dto.*
        -> common.protocols.RoomRepository
        -> common.protocols.MessageRepository
        -> common.protocols.AgentRegistry
        -> common.protocols.RoomMembershipSeedSource
        -> room.repository.RoomMongoRepository
        -> room.repository.MessageMongoRepository
          -> common.protocols.MongoDAL

modules.RoomMessageCenter
  -> RoomMessageStore / RoomHistoryReader          persistence only
  -> legacy SSE, queue, supervisor, transport       stays until Phase 6/7

Context & Memory (Phase 5)
  -> RoomHistoryReader                             read-only history projection

Execution / HubRuntimeBridge
  -> RoomRegistry, RoomMessageStore, RoomOwnershipReader protocols only
```

Forbidden from `room/**`:
- `agent`
- `services`
- `modules`
- `api`
- `database`
- `models`
- `main`
- `container`
- `a2a_adapter`
- `llm_gateway`
- `infrastructure`
- legacy `config`

Allowed in `room/**`:
- stdlib
- `common.*`
- relative imports inside `room`

Additional import-boundary detail:
- `room/repository/mongo.py` may reference `common.protocols.MongoDAL` in its constructor signature.
- No `room/**` file may import from `dal` at all, including concrete implementations such as `dal.mongo.client.MongoDALImpl`.
- No `room/**` file may import from `agent/**`; cross-module agent reads must go through Common protocols.
- The application shell in `container.py` owns concrete implementation construction.
- Legacy adapters in `services/**`, `modules/**`, and `api/**` may import `room` during migration, but `room` must never import them.

## Interface Definitions

### RoomFacade Constructor

Use explicit dependency injection. Do not construct singletons inside `room/`.

```python
from collections.abc import Callable
from datetime import datetime
from typing import Any

from common.observability import tracer as default_tracer
from common.protocols import (
    AgentRegistry,
    MessageRepository,
    RoomHistoryReader,
    RoomManagement,
    RoomMembershipSeedSource,
    RoomMessageStore,
    RoomOwnershipReader,
    RoomRegistry,
    RoomRepository,
)

class RoomFacade:
    def __init__(
        self,
        *,
        repository: RoomRepository,
        message_repository: MessageRepository,
        agent_registry: AgentRegistry,
        membership_source: RoomMembershipSeedSource,
        id_factory: Callable[[], str],
        now: Callable[[], datetime],
        tracer: Any | None = None,
    ) -> None: ...
```

Do not include old delegation parameters such as `room_services`, `room_center`, `database_service`, `room_message_center`, `sse_manager`, `agent_service`, or `task_service`. The facade owns Room CRUD, membership resolution, raw persistence, and history logic directly.

Protocol methods implemented exactly:
- `get_room(room_id: str) -> RoomInfo | None`
- `get_room_agents(room_id: str) -> list[str]`
- `get_room_owner(room_id: str) -> str | None`
- `create_room(request: CreateRoomRequest) -> RoomInfo`
- `delete_room(room_id: str, owner_id: str) -> bool`
- `update_room(room_id: str, updates: dict) -> RoomInfo | None`
- `update_membership(room_id: str, request: MembershipUpdateRequest) -> RoomInfo`
- `save_user_message(room_id: str, message: UserMessageInput) -> SavedUserMessage`
- `save_agent_message(room_id: str, message: AgentMessageInput) -> str`
- `update_agent_message_status(message_id: str, status: str, **kwargs) -> bool`
- `get_message(message_id: str) -> RoomMessageInfo | None`
- `get_messages_for_room(room_id: str, limit: int = 100, before: datetime | None = None) -> list[RoomMessageInfo]`
- `get_messages_by_ids(message_ids: list[str]) -> list[RoomMessageInfo]`
- `get_message_thread(parent_message_id: str) -> list[RoomMessageInfo]`
- `verify_room_agent_membership(room_id: str, agent_id: str) -> bool`
- `verify_room_hub_ownership(room_id: str, hub_id: str) -> bool`

Non-protocol compatibility helpers allowed on `RoomFacade`:
- `list_rooms_for_owner(owner_id: str) -> list[RoomInfo]`, used by `inquiryRoomsByRoomOwnerId`.
- `replace_membership(room_id: str, seed: MembershipSeed, requesting_user_id: str | None = None) -> RoomInfo`, used by legacy `updateRoomAgentSet` where the old request replaces the whole set.
- `get_room_default_status(room_id: str, viewer_user_id: str | None = None) -> tuple[list[RoomAgentRefDTO], str]`, or equivalent Common DTO output, used only by legacy adapter conversion.
- `delete_room_owned_messages(room_id: str) -> dict[str, int]`, used by legacy delete cleanup if `delete_room()` keeps room and message deletion separate for response compatibility.

### RoomMembershipSeedSource Protocol

Add only if the implementation branch does not already have an equivalent Common protocol. This protocol isolates saved-group and current-agent reads from `room/**`.

```python
# common/protocols/room_protocols.py

@runtime_checkable
class RoomMembershipSeedSource(Protocol):
    async def get_saved_group(self, group_id: str) -> SavedAgentGroupSnapshot | None: ...
    async def list_current_agents(self, user_id: str | None) -> list[AgentInfo]: ...
```

DTO addition if needed:

```python
# common/dto/room.py

class SavedAgentGroupSnapshot(FrozenDTO):
    group_id: str
    name: str
    owner_id: str | None = None
    type: str | None = None
    agent_ids: list[str] = Field(default_factory=list)
```

The implementation of `RoomMembershipSeedSource` belongs outside `room/`, for example `services/room_membership_source.py`, and can temporarily read legacy Agent APIs. Prefer the current Phase 3 Agent facade/service path for all-current-agent listing because it now preserves legacy conditional filters through `list_visible_agents(..., query=..., limit=...)`; do not reintroduce direct raw agent collection scans. The Room facade sees only Common DTOs.

### RoomRepository Additions

Start from the existing protocol:

```python
class RoomRepository(Protocol):
    async def get_by_id(self, room_id: str) -> dict | None: ...
    async def get_by_owner(self, owner_id: str) -> list[dict]: ...
    async def create(self, room: dict) -> str: ...
    async def update(self, room_id: str, updates: dict) -> bool: ...
    async def delete(self, room_id: str) -> bool: ...
```

Verify completeness in Task 2. If missing, add only these domain-specific methods:

```python
async def update_fields(self, room_id: str, updates: dict) -> dict | None: ...

async def set_membership(
    self,
    room_id: str,
    *,
    agent_set: dict[str, str],
    membership_origin: str,
    membership_origin_status: str,
    source_group_id: str | None = None,
    source_group_name: str | None = None,
) -> dict | None: ...
```

Keep repository inputs and outputs as dicts. The repository must not return `models.room.Room`.

### MessageRepository Additions

Start from the existing protocol:

```python
class MessageRepository(Protocol):
    async def save_user_message(self, message: dict) -> str: ...
    async def save_agent_message(self, message: dict) -> str: ...
    async def get_by_id(self, message_id: str) -> dict | None: ...
    async def get_by_ids(self, message_ids: list[str]) -> list[dict]: ...
    async def get_for_room(
        self, room_id: str, limit: int, before: datetime | None = None
    ) -> list[dict]: ...
    async def get_thread(self, parent_message_id: str) -> list[dict]: ...
    async def update_status(self, message_id: str, status: str, **fields) -> bool: ...
```

Verify completeness in Task 2. If missing, add only these domain-specific methods:

```python
async def delete_for_room(self, room_id: str) -> dict[str, int]: ...

async def get_user_messages_for_room(
    self, room_id: str, limit: int = 100, before: datetime | None = None
) -> list[dict]: ...

async def get_agent_messages_for_room(
    self, room_id: str, limit: int = 100, before: datetime | None = None
) -> list[dict]: ...
```

`MessageMongoRepository` may internally use both `room_user_messages` and `room_agent_messages` Mongo collections. That does not violate module ownership because both are Room-owned raw message stores in Phase 4.

### RoomDeps Sub-Container

Extend `container.py` rather than creating a parallel container:

```python
from dataclasses import dataclass

from common.protocols import (
    RoomHistoryReader,
    RoomManagement,
    RoomMessageStore,
    RoomOwnershipReader,
    RoomRegistry,
)

@dataclass(frozen=True)
class RoomDeps:
    room_registry: RoomRegistry
    room_management: RoomManagement
    room_message_store: RoomMessageStore
    room_history_reader: RoomHistoryReader
    room_ownership_reader: RoomOwnershipReader
```

Because one `RoomFacade` implements all five protocols, the initial assembly can bind all fields to the same instance.

## Implementation Order

Parallelization note: Tasks 2 and 3 are independent after Task 1 lands. Task 6 adapter tests can be drafted in parallel with Task 5 message-store work if workers keep disjoint write sets. Do not parallelize `services/room_services.py` and `modules/RoomMessageCenter.py` edits in separate workers unless ownership of exact methods is split up front.

### Task 0: Branch, Baseline, and Room Inventory Reconciliation

**Files:**
- Maybe create: `room/**`
- Maybe modify: `container.py`
- No behavior changes yet

- [ ] **Step 1: Verify branch starts from Phase 3**

```bash
git status --short --branch
git log --oneline --decorate -5
```

Expected: branch is `phase-4-room-module` created from current `main`, or from a `phase-3-agent-module` branch that has been fast-forwarded/rebased onto current `main`; worktree is clean except planned changes.

- [ ] **Step 2: Verify Phase 3 artifacts exist**

```bash
test -f agent/facade.py
test -f agent/repository/mongo.py
test -f container.py
test -f services/agent_service.py
```

Expected: all commands exit 0. If any fail, do not start Phase 4; first reconcile Phase 3 into the branch.

- [ ] **Step 3: Verify Phase 3 review-fix contracts are present**

```bash
git log --oneline --decorate -8
rg -n "validate_hub_liveness_reader|def is_hub_online\\(" common/protocols/hub_protocols.py agent/facade.py services/relay_service.py
rg -n "query: dict \\| None|list_visible_agents\\(" common/protocols/repository_protocols.py agent/repository/mongo.py services/agent_service.py
if rg -n "HubLivenessProbe|RelayHubLivenessProbe|is_hub_online_async|getattr\\(.*is_hub_online_async" common agent services; then exit 1; fi
```

Expected: recent history or local files include the Phase 3 review fixes: async `HubLivenessReader.is_hub_online`, `validate_hub_liveness_reader()` rejecting sync implementations, `RelayHubLivenessReader.is_hub_online()` delegating to `RelayService.is_hub_alive()`, no `HubLivenessProbe`/`RelayHubLivenessProbe`, no `is_hub_online_async` duck-typing, and Agent visible-list query passthrough. If missing, update the branch from `main` before starting Phase 4.

- [ ] **Step 4: Confirm room service file name**

```bash
test -f services/room_services.py
test ! -f services/room_service.py
```

Expected: `services/room_services.py` exists. If the implementation branch has renamed it to `services/room_service.py`, update this plan's file references before implementation.

- [ ] **Step 5: Check whether a `room/` scaffold already exists**

```bash
git ls-tree -r --name-only HEAD -- room
```

Expected: either no `room/` files exist, or any existing scaffold is inspected before use.

- [ ] **Step 6: If scaffold exists, inspect before porting**

```bash
git show HEAD:room/facade.py | sed -n '1,220p'
git show HEAD:room/repository/mongo.py | sed -n '1,220p'
```

Expected: confirm whether the scaffold delegates to legacy services. Do not keep any `room/**` import from `agent`, `services`, `modules`, `api`, `database`, `models`, `main`, `container`, `a2a_adapter`, or `llm_gateway`.

- [ ] **Step 7: Check referenced test-file availability**

```bash
for path in \
  tests/test_api_room_center.py \
  tests/test_service_room.py \
  tests/test_module_room_message_center.py \
  tests/test_flow_contracts.py \
  tests/test_distributed_room_lock.py \
  tests/test_room_coordinator_service.py
do
  test -f "$path" && printf "exists %s\n" "$path" || printf "missing %s\n" "$path"
done
```

Expected: record which files already exist. For missing files, create focused replacements in the task that first references them rather than silently dropping coverage.

- [ ] **Step 8: Run baseline tests for completed phases and existing room behavior**

```bash
uv run python -m pytest tests/test_common_foundation.py tests/test_agent_protocols.py tests/test_agent_repository.py tests/test_agent_facade.py tests/test_service_agent.py tests/test_heartbeat_fixes.py tests/test_adapter_protocols.py tests/test_api_room_center.py tests/test_service_room.py tests/test_flow_contracts.py -q
```

Expected: PASS before Phase 4 changes, or document existing failures before editing Room code.

### Task 1: Add Failing Room Protocol, Packaging, and Boundary Tests

**Files:**
- Create: `tests/test_room_protocols.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add runtime protocol conformance test**

Assert:
- `RoomFacade(...)` is a `RoomRegistry`.
- `RoomFacade(...)` is a `RoomManagement`.
- `RoomFacade(...)` is a `RoomMessageStore`.
- `RoomFacade(...)` is a `RoomHistoryReader`.
- `RoomFacade(...)` is a `RoomOwnershipReader`.
- `RoomMongoRepository(mongo=fake_mongo)` is a `RoomRepository`.
- `MessageMongoRepository(mongo=fake_mongo)` is a `MessageRepository`.

- [ ] **Step 2: Add top-level export test**

Assert:
- `from room import RoomFacade` works.
- `from room import RoomMongoRepository` works.
- `from room import MessageMongoRepository` works.
- `from room.repository import RoomMongoRepository, MessageMongoRepository` works.
- `room.__all__ == ["RoomFacade", "RoomMongoRepository", "MessageMongoRepository"]` or the final explicit export set.

- [ ] **Step 3: Add package-list test**

Assert `pyproject.toml` includes:
- `room`
- `room.repository`

- [ ] **Step 4: Add import-boundary AST test**

Use the helper style from `tests/test_agent_protocols.py`. Allowed roots:
- `__future__`
- stdlib roots from `sys.stdlib_module_names`
- `common`
- `room`

Forbidden roots:
- `a2a_adapter`
- `agent`
- `api`
- `config`
- `container`
- `database`
- `infrastructure`
- `llm_gateway`
- `main`
- `models`
- `modules`
- `services`

- [ ] **Step 5: Add RoomDeps container test**

Assert:
- `create_room_deps(...)` returns one facade instance bound to all five protocol fields.
- `RoomDeps.room_registry is RoomDeps.room_management`.
- `RoomDeps.room_registry is RoomDeps.room_message_store`.
- `RoomDeps.room_registry is RoomDeps.room_history_reader`.
- `RoomDeps.room_registry is RoomDeps.room_ownership_reader`.

- [ ] **Step 6: Run and verify failure**

```bash
uv run python -m pytest tests/test_room_protocols.py -q
```

Expected before implementation: FAIL because `room` package, repositories, or `RoomDeps` are missing.

### Task 2: Extend and Implement Room and Message Repositories

**Files:**
- Modify: `common/protocols/repository_protocols.py`
- Create/modify: `room/repository/mongo.py`
- Create/modify: `room/repository/__init__.py`
- Create: `tests/test_room_repository.py`

- [ ] **Step 1: Write room repository contract tests**

Cover:
- `RoomMongoRepository(mongo=fake_mongo)` calls `mongo.collection("rooms")`.
- `get_by_id()` queries `{"room_id": room_id}`.
- `get_by_owner()` queries `{"room_owner_id": owner_id}`.
- `create()` inserts the supplied dict and returns the stored `room_id`.
- `update()` applies `$set` by `room_id` and returns `bool`.
- `update_fields()` returns the updated room dict or `None`.
- `set_membership()` writes `room_agent_set`, `membership_origin`, `membership_origin_status`, `source_group_id`, and `source_group_name`.
- `delete()` deletes by `room_id`.
- Repository outputs stay raw dicts, not `models.room.Room`.

- [ ] **Step 2: Write message repository contract tests**

Cover:
- `MessageMongoRepository(mongo=fake_mongo)` calls `mongo.collection("room_user_messages")` and `mongo.collection("room_agent_messages")`.
- `save_user_message()` inserts into `room_user_messages` and returns `message_id`.
- `save_agent_message()` inserts into `room_agent_messages` and returns `message_id`.
- `get_by_id()` searches user messages first, then agent messages.
- `get_by_ids()` returns both user and agent messages and preserves repository output as dicts.
- `get_for_room()` combines user and agent messages sorted by `message_created_at`, then `step_number`, then `message_id`.
- `get_for_room(before=...)` filters both collections before sorting.
- `get_thread(parent_message_id)` walks `related_message_id` / `parent_message_id` descendants without returning unrelated room messages.
- `update_status()` updates agent task status fields and any extra fields via `$set`.
- `delete_for_room()` deletes from both room message collections and returns counts, for example `{"user_messages": 2, "agent_messages": 3}`.

- [ ] **Step 3: Extend repository protocols only as needed**

Use the additions listed in "RoomRepository Additions" and "MessageRepository Additions". Keep protocols domain-scoped; do not expose generic `find(query)`, raw collection access, or cross-module collection cleanup.

- [ ] **Step 4: Implement `RoomMongoRepository`**

Implementation notes:
- Constructor accepts `mongo: MongoDAL` and optional `collection_name: str = "rooms"`.
- Store `self._rooms = mongo.collection(collection_name)`.
- Use `MongoCollection.find_one`, `find`, `insert_one`, `update_one`, `find_one_and_update`, and `delete_one`.
- Do not import `database.mongodb`, `pymongo`, or `models.room`.
- For `create()`, if incoming dict has no `room_id`, let the facade generate one before repository call rather than importing `uuid` here unless tests explicitly require repository fallback.

- [ ] **Step 5: Implement `MessageMongoRepository`**

Implementation notes:
- Constructor accepts `mongo: MongoDAL`, `user_collection_name: str = "room_user_messages"`, and `agent_collection_name: str = "room_agent_messages"`.
- Store `self._user_messages` and `self._agent_messages`.
- Use only `MongoCollection` protocol methods.
- Normalize `message_type` to `"user"` or `"agent"` when combining history.
- Do not convert dicts to Pydantic models in the repository.

- [ ] **Step 6: Run repository tests**

```bash
uv run python -m pytest tests/test_room_repository.py tests/test_room_protocols.py -k "repository or package" -q
```

Expected: repository tests PASS; facade conformance may still fail until Task 4.

### Task 3: Add Room Translators, Message Graph Helpers, and Membership Resolution

**Files:**
- Create: `room/translators.py`
- Create: `room/message_graph.py`
- Create: `room/membership.py`
- Modify: `common/dto/room.py`
- Modify: `common/protocols/room_protocols.py`
- Modify: `common/protocols/__init__.py`
- Create: `tests/test_room_membership.py`
- Modify: `tests/test_room_facade.py` utility sections if useful

- [ ] **Step 1: Add translator tests**

Cover:
- Mongo room dict to `RoomInfo`.
- Legacy field names map correctly:
  - `room_owner_id` -> `RoomInfo.owner_id`
  - `room_owner_name` -> `RoomInfo.owner_name`
  - `room_created_at` -> `RoomInfo.created_at`
  - `room_agent_set.keys()` -> `RoomInfo.agent_ids`
- Missing provenance defaults to `membership_origin="manual"` and `membership_origin_status="manual"`.
- `RoomInfo` to legacy-compatible room dict for create/update.
- User message dict to `RoomMessageInfo`.
- Agent message dict to `RoomMessageInfo`.
- `SavedUserMessage.dispatch_root_message_id` is set to the saved user `message_id`.

- [ ] **Step 2: Implement `room/translators.py`**

Rules:
- Read room membership from `doc["room_agent_set"]` and keep keys as canonical agent IDs.
- Preserve `processing_message_id`.
- Preserve `source_group_id` and `source_group_name`.
- Message DTO `content` should contain the serialized `message_content` dict.
- `RoomMessageInfo.parent_message_id` maps from `parent_message_id` first, then `related_message_id`.
- Do not instantiate `models.room.Room`, `RoomUserMessage`, or `RoomAgentMessage`.

- [ ] **Step 3: Add message graph tests**

Cover:
- Combined messages sort by `message_created_at`, then `step_number`, then `message_id`.
- Missing timestamps sort deterministically after timestamped rows.
- Thread selection returns direct and chained descendants.
- Thread selection detects cycles and stops.
- Status update payload maps `status="completed"` to `message_content.message_task.status.state` without dropping other update fields.

- [ ] **Step 4: Implement `room/message_graph.py`**

Keep helpers pure. Do not import repositories or legacy models.

- [ ] **Step 5: Add membership seed source protocol and DTO tests**

If missing, add tests asserting:
- `SavedAgentGroupSnapshot` has `group_id`, `name`, `owner_id`, `type`, and `agent_ids`.
- `RoomMembershipSeedSource` is exported from `common.protocols`.
- `RoomMembershipSeedSource` is runtime-checkable.

- [ ] **Step 6: Add membership resolution tests**

Cover:
- Legacy inverted room agent set `{agent_name: agent_id}` is normalized to `{agent_id: agent_name}`.
- Manual seed with explicit IDs resolves names through `AgentRegistry.get_agents_by_ids()`.
- Manual seed fails with a clear error when any explicit ID is unknown.
- Manual seed fails when any explicit private agent is not visible to the requesting user.
- Saved group seed requires `group_id`.
- Saved group seed returns 404-style failure when group is missing.
- Saved group seed allows `type == "builtin"` for any user.
- Saved group seed rejects a non-builtin group when `group.owner_id != requesting_user_id`.
- Saved group seed includes only active agents from the saved group.
- All current agents seed calls `membership_source.list_current_agents(requesting_user_id)` and includes active visible agents.
- Resolved provenance is:
  - manual: `membership_origin="manual"`, `membership_origin_status="manual"`
  - saved group: `membership_origin="saved_group"`, `membership_origin_status="seeded_never_edited"`
  - all current agents: `membership_origin="all_current_agents"`, `membership_origin_status="seeded_never_edited"`

- [ ] **Step 7: Implement `room/membership.py`**

Target helper shape:

```python
@dataclass(frozen=True)
class ResolvedMembership:
    agent_set: dict[str, str]
    membership_origin: str
    membership_origin_status: str
    source_group_id: str | None = None
    source_group_name: str | None = None

async def resolve_membership_seed(
    *,
    seed: MembershipSeed,
    owner_id: str,
    agent_registry: AgentRegistry,
    membership_source: RoomMembershipSeedSource,
) -> ResolvedMembership: ...
```

The facade should call this helper. The helper must use only Common DTOs/protocols and local pure functions.

- [ ] **Step 8: Run utility and membership tests**

```bash
uv run python -m pytest tests/test_room_membership.py tests/test_room_facade.py -k "translator or message_graph or membership" -q
```

Expected: PASS.

### Task 4: Implement RoomFacade Registry, Lifecycle, Ownership, and Membership

**Files:**
- Create/modify: `room/facade.py`
- Modify: `tests/test_room_facade.py`
- Modify: `tests/test_room_golden.py`

- [ ] **Step 1: Write failing registry tests**

Cover:
- `get_room()` returns `RoomInfo` for existing repository doc.
- `get_room()` returns `None` for missing doc.
- `get_room_agents()` returns normalized agent IDs.
- `get_room_owner()` returns `room_owner_id`.
- `get_room_owner()` returns `None` for missing room.
- `verify_room_agent_membership(room_id, agent_id)` returns true only when the agent is in `room_agent_set`.
- `verify_room_hub_ownership(room_id, hub_id)` fetches room agents through `AgentRegistry.get_agents_by_ids()` and returns true only when one room agent has the requested `hub_id`.

- [ ] **Step 2: Implement registry and ownership methods**

Use repository reads only. Do not call `services.database_service`.

- [ ] **Step 3: Write failing create-room tests for all seed modes**

Cover:
- Missing `owner_id`, `owner_name`, or `room_name` raises `ValueError` or a Common domain error if one exists.
- Manual seed creates a room with explicit agent IDs and names.
- Saved group seed creates a room with group provenance fields.
- All current agents seed creates a room with all active visible agents.
- Empty manual seed creates an empty room with manual provenance.
- Created room dict includes:
  - generated `room_id`
  - `room_name`
  - `room_owner_id`
  - `room_owner_name`
  - canonical `room_agent_set`
  - `room_created_at`
  - `membership_origin`
  - `membership_origin_status`
  - `source_group_id`
  - `source_group_name`
  - `extend_info` only through non-protocol compatibility adapter, not `CreateRoomRequest`

- [ ] **Step 4: Implement `create_room()`**

Algorithm:
1. Validate required fields.
2. Resolve `request.membership_seed` through `room/membership.py`.
3. Generate `room_id`.
4. Build repository dict through `room/translators.py`.
5. `repository.create(room_doc)`.
6. Read back the room if needed for storage defaults.
7. Return `RoomInfo`.

- [ ] **Step 5: Write failing update/delete tests**

Cover:
- `update_room(room_id, {"room_name": "New"})` persists allowed fields and returns `RoomInfo`.
- `update_room(room_id, {"extend_info": {...}})` persists only through compatibility helper if keeping `extend_info` outside protocol input.
- Unknown update keys raise `ValueError`.
- Missing room update returns `None`.
- `delete_room(room_id, owner_id)` returns false for missing room.
- `delete_room(room_id, owner_id)` returns false for non-owner.
- Owner delete removes room and Room-owned messages through repositories.

- [ ] **Step 6: Implement update and delete methods**

Allowed `update_room()` keys:
- `room_name`
- `extend_info`
- `processing_message_id`

Deletion rules:
- Check room exists and `room_owner_id == owner_id` in facade.
- Delete Room-owned raw messages through `MessageRepository.delete_for_room()`.
- Delete room through `RoomRepository.delete()`.
- Do not delete `room_memories`, file upload metadata, S3 prefixes, or conversation content from inside `room/`; legacy adapter handles transitional non-room cleanup until later phases.

- [ ] **Step 7: Write failing membership update tests**

Cover:
- `update_membership(add_agent_ids=["a2"])` adds accessible active agent names.
- `update_membership(remove_agent_ids=["a1"])` removes only listed IDs.
- Adding unknown agents fails.
- Adding inaccessible private agents fails.
- Editing a saved-group or all-current room sets `membership_origin_status="seeded_edited"`.
- Editing a manual room keeps `membership_origin_status="manual"`.
- `replace_membership()` compatibility helper reuses full seed resolution and replaces the set.

- [ ] **Step 8: Implement membership update methods**

Use `AgentRegistry.get_agents_by_ids()` for explicit additions. Preserve current room provenance unless the edit changes seeded membership status.

- [ ] **Step 9: Run facade lifecycle tests**

```bash
uv run python -m pytest tests/test_room_facade.py tests/test_room_golden.py -k "registry or create or update or delete or membership or ownership" -q
```

Expected: PASS.

### Task 5: Implement Room Message Store and History Reader

**Files:**
- Modify: `room/facade.py`
- Modify: `room/translators.py`
- Modify: `room/message_graph.py`
- Modify: `tests/test_room_facade.py`
- Modify: `tests/test_room_golden.py`

- [ ] **Step 1: Write failing user-message persistence tests**

Cover:
- `save_user_message(room_id, UserMessageInput(...))` fails when room is missing.
- Saved user message dict includes:
  - generated `message_id`
  - `room_id`
  - `message_type == "user"`
  - `user_id`
  - `message_content.message_text`
  - `client_request_id`
  - `message_created_at`
- Return value is `SavedUserMessage`.
- `SavedUserMessage.dispatch_root_message_id == SavedUserMessage.message_id`.
- `scope_resolution_error` is preserved when supplied in metadata.

- [ ] **Step 2: Implement `save_user_message()`**

Algorithm:
1. Verify room exists through `repository.get_by_id(room_id)`.
2. Generate `message_id`.
3. Build raw user message dict through translator.
4. `message_repository.save_user_message(doc)`.
5. Return `SavedUserMessage`.

- [ ] **Step 3: Write failing agent-message persistence tests**

Cover:
- `save_agent_message(room_id, AgentMessageInput(...))` fails when room is missing.
- Saved agent message dict includes:
  - generated `message_id`
  - `room_id`
  - `message_type == "agent"`
  - `agent_id`
  - `related_message_id` or `parent_message_id`
  - `message_content`
  - `message_created_at`
  - metadata fields needed by legacy task tracking when supplied.
- Return value is the saved `message_id`.

- [ ] **Step 4: Implement `save_agent_message()`**

Use `MessageRepository.save_agent_message()` only. Do not call A2A or SSE code from the facade.

- [ ] **Step 5: Write failing status and read tests**

Cover:
- `update_agent_message_status()` delegates to message repository.
- `get_message()` returns user or agent `RoomMessageInfo`.
- `get_messages_for_room()` returns combined sorted history.
- `get_messages_by_ids()` returns only found messages and preserves caller ID order.
- `get_message_thread()` returns descendants of the parent message.
- History output contains enough raw content for Phase 5 Context & Memory projection.

- [ ] **Step 6: Implement status and history methods**

Use `MessageRepository` and `room/message_graph.py`. Keep history read-only and side-effect free.

- [ ] **Step 7: Add golden message-create tests**

Golden cases:
- Legacy `/roomCenter/createAndParseUserMessage` response includes `dispatch_root_message_id == message_id`.
- Legacy `/roomCenter/sendMessage` response includes `dispatch_root_message_id == message_id`.
- Failed pre-persist scope validation does not create a user message and has no `dispatch_root_message_id`.
- Failed all-agents post-persist selection returns the real `message_id` and preserves current failure behavior.

- [ ] **Step 8: Run message store and history tests**

```bash
uv run python -m pytest tests/test_room_facade.py tests/test_room_golden.py tests/test_api_room_center.py -k "message or history or dispatch_root_message_id" -q
```

Expected: PASS.

### Task 6: Add C3 Migration Adapter for `services/room_services.py`

**Files:**
- Modify: `services/room_services.py`
- Create/modify: `services/room_membership_source.py`
- Modify: `tests/test_service_room.py`
- Modify: `tests/test_room_golden.py`

- [ ] **Step 1: Write fail-fast binding tests for `RoomServices`**

Cover:
- New `RoomServices()` has `_bound is False`.
- Calling delegated room lifecycle methods before `bind_facade()` raises `RuntimeError("RoomServices.bind_facade() not called - startup incomplete")`.
- After bind, legacy methods call the facade.

- [ ] **Step 2: Add `RoomServices.bind_facade()`**

Target shape:

```python
class RoomServices:
    def __init__(self) -> None:
        self._facade = None
        self._bound = False
        # Legacy execution/SSE dependencies remain until Phase 6/7.

    def bind_facade(self, facade) -> None:
        self._facade = facade
        self._bound = True

    def _require_facade(self):
        if not self._bound or self._facade is None:
            raise RuntimeError(
                "RoomServices.bind_facade() not called - startup incomplete"
            )
        return self._facade
```

- [ ] **Step 3: Implement legacy request/response conversion helpers**

This is the only place allowed to convert new Room DTOs to `models.room.Room`, `RoomUserMessage`, `RoomAgentMessage`, and `RoomMessage` for old callers. `room/` must not import `models`.

Conversion rules:
- `RoomInfo.owner_id` maps to `Room.room_owner_id`.
- `RoomInfo.owner_name` maps to `Room.room_owner_name`.
- `RoomInfo.agent_ids` plus resolved names maps to `Room.room_agent_set`.
- Preserve `membership_origin`, `membership_origin_status`, `source_group_id`, `source_group_name`, `processing_message_id`, and `extend_info`.
- Convert `SavedUserMessage.message` back to `RoomUserMessage` for legacy response models.

- [ ] **Step 4: Delegate room CRUD and membership methods**

Legacy method mapping:
- `create_new_room(request)` -> `facade.create_room(CreateRoomRequest(...))` -> `RoomCenterRoomSettingResponse`.
- `inquiry_room_setting(request)` -> `facade.get_room(request.room_id)` plus legacy active-run lookup -> `RoomCenterRoomSettingResponse`.
- `inquiry_rooms_by_room_owner_id(request)` -> `facade.list_rooms_for_owner(owner_id)` -> `RoomCenterRoomSettingResponse`.
- `update_room_agent_set(request)` -> `facade.replace_membership(...)` -> `RoomCenterRoomSettingResponse`.
- `update_room_name(request)` -> `facade.update_room(room_id, {"room_name": request.room_name})`.
- `update_room_extend_info(request)` -> `facade.update_room(room_id, {"extend_info": request.extend_info})`.
- `delete_room_by_room_id(request)` -> verify owner if available, `facade.delete_room(room_id, owner_id)`, then transitional non-room cleanup in the adapter.

- [ ] **Step 5: Preserve transitional delete cleanup outside `room/`**

Keep these side effects in `services/room_services.py` until owning modules extract them:
- S3 prefix cleanup through `services.s3_service`.
- `room_memories` cleanup.
- `file_uploads` cleanup when S3 cleanup succeeded.
- `conversation_content` cleanup.

The Room facade should only delete Room-owned room and message data.

- [ ] **Step 6: Implement `services/room_membership_source.py`**

Target behavior:
- `get_saved_group(group_id)` reads legacy agent group storage and returns `SavedAgentGroupSnapshot`.
- `list_current_agents(user_id)` returns active agents visible to the user using the bound Agent facade when available. Prefer `AgentFacade.list_visible_agents(user_id=user_id, active_only=True, query=None, limit=0)` or `services.agent_service.get_agents_with_conditions(AgentCenterRequest(user_id=user_id, query={"agent_status": "active"}, limit=0))` during migration; this preserves the Phase 3 review-fix query passthrough instead of reintroducing raw DB reads.
- Do not construct fake hub liveness readers with sync `def is_hub_online()` in tests for this adapter or startup wiring. Current Phase 3 validates that `HubLivenessReader.is_hub_online` is async and rejects sync implementations.
- This file may import legacy services and models because it is outside `room/`.

- [ ] **Step 7: Run service adapter tests**

```bash
uv run python -m pytest tests/test_service_room.py tests/test_room_golden.py -k "adapter or create or update_room_agent_set or delete_room or membership" -q
```

Expected: PASS after updating tests for bind behavior and response compatibility.

### Task 7: Add C3 Migration Adapter for `modules/RoomCenter.py`

**Files:**
- Modify: `modules/RoomCenter.py`
- Modify: `tests/test_api_room_center.py`
- Modify: `tests/test_flow_contracts.py`

- [ ] **Step 1: Write fail-fast binding tests for `RoomCenter`**

Cover:
- `RoomCenter()` without a bound service/facade raises `RuntimeError("RoomCenter.bind_facade() not called - startup incomplete")` for delegated room methods if the global service is not already bound.
- After bind, `create_new_room()`, `inquiry_room_setting()`, `delete_room_by_room_id()`, `inquiry_rooms_by_room_owner_id()`, `update_room_agent_set()`, `update_room_name()`, and `update_room_extend_info()` delegate through `RoomServices`.

- [ ] **Step 2: Add `RoomCenter.bind_facade()` or `bind_room_services()`**

Preferred shape:

```python
class RoomCenter:
    def __init__(self, room_services=None) -> None:
        self.room_services = room_services

    def bind_facade(self, facade) -> None:
        from services.room_services import room_services
        room_services.bind_facade(facade)
        self.room_services = room_services

    def _require_room_services(self):
        if self.room_services is None:
            raise RuntimeError(
                "RoomCenter.bind_facade() not called - startup incomplete"
            )
        return self.room_services
```

If retaining import-time `room_services` is necessary for compatibility, still add a fail-fast assertion that the underlying service is bound before delegated business methods run.

- [ ] **Step 3: Keep API endpoint response shapes unchanged**

Do not change:
- `/roomCenter/createNewRoom`
- `/roomCenter/inquiryRoomSetting`
- `/roomCenter/inquiryActiveRuns`
- `/roomCenter/inquiryRoomsByRoomOwnerId`
- `/roomCenter/updateRoomAgentSet`
- `/roomCenter/updateRoomName`
- `/roomCenter/updateRoomExtendInfo`
- `/roomCenter/createAndParseUserMessage`
- `/roomCenter/inquiryRoomMessagesByRoomId`
- `/roomCenter/sendMessage`

- [ ] **Step 4: Run RoomCenter and API tests**

```bash
uv run python -m pytest tests/test_api_room_center.py tests/test_flow_contracts.py -k "RoomLifecycleFlow or RoomCenter or create_new_room or update_room_agent_set or inquiry_room_setting" -q
```

Expected: PASS with endpoint response bodies unchanged.

### Task 8: Bind RoomFacade into RoomMessageCenter Without Moving Execution/SSE

**Files:**
- Modify: `modules/RoomMessageCenter.py`
- Modify: `services/room_services.py`
- Modify: `tests/test_module_room_message_center.py`
- Modify: `tests/test_distributed_room_lock.py` only if constructor or helper setup changes

- [ ] **Step 1: Write fail-fast binding tests for room persistence seams**

Cover:
- `RoomMessageCenter.__new__(RoomMessageCenter)` plus unbound room facade raises `RuntimeError("RoomMessageCenter.bind_facade() not called - startup incomplete")` when a method needs Room persistence.
- Pure lock helpers such as `_acquire_room_lock()` and `_release_room_lock()` can still be unit-tested without facade binding.
- After bind, message persistence calls use `RoomMessageStore` where Phase 4 routes them through the facade.

- [ ] **Step 2: Add `RoomMessageCenter.bind_facade()`**

Target shape:

```python
class RoomMessageCenter:
    def bind_facade(self, facade) -> None:
        self._room_facade = facade
        self._room_bound = True

    def _require_room_facade(self):
        if not getattr(self, "_room_bound", False) or self._room_facade is None:
            raise RuntimeError(
                "RoomMessageCenter.bind_facade() not called - startup incomplete"
            )
        return self._room_facade
```

- [ ] **Step 3: Move only raw Room persistence calls to the bound facade**

Eligible Phase 4 call sites:
- User message persistence before dispatch.
- Agent message persistence for generated tasks where no A2A/SSE delivery happens inside the persistence operation.
- Message status updates that map directly to `RoomMessageStore.update_agent_message_status()`.
- History reads that map directly to `RoomHistoryReader`.

Do not move in Phase 4:
- `process_room_user_message()`
- Redis/local room lock behavior.
- `QueueExecutor`
- `SupervisorExecutor`
- `AgentDispatcher`
- `AgentMessageProcessor`
- `DirectTransport`
- SSE send/broadcast calls.
- HITL checks.
- Task tracking and webhook token persistence that belongs to Execution.

- [ ] **Step 4: Preserve legacy execution behavior**

If a call site mixes raw room persistence with Execution-owned side effects, keep the whole method in legacy code and delegate only the smallest safe persistence operation. Prefer a compatibility helper in `services/room_services.py` over importing repositories into `RoomMessageCenter`.

- [ ] **Step 5: Run RoomMessageCenter tests**

```bash
uv run python -m pytest tests/test_module_room_message_center.py tests/test_distributed_room_lock.py -q
```

Expected: PASS. Lock/SSE/execution behavior remains unchanged except for explicit fail-fast binding tests around Room persistence.

### Task 9: Wire RoomDeps in Container and Startup

**Files:**
- Modify: `container.py`
- Modify: `main.py`
- Modify: tests that patch startup or room dependencies

- [ ] **Step 1: Add container assembly tests**

Create tests that instantiate the container with fakes and assert:
- `RoomDeps.room_registry` is a `RoomRegistry`.
- `RoomDeps.room_management` is a `RoomManagement`.
- `RoomDeps.room_message_store` is a `RoomMessageStore`.
- `RoomDeps.room_history_reader` is a `RoomHistoryReader`.
- `RoomDeps.room_ownership_reader` is a `RoomOwnershipReader`.
- All five fields are the same `RoomFacade` instance.

- [ ] **Step 2: Implement `container.py` RoomDeps assembly**

Target:

```python
def create_room_deps(
    *,
    mongo: MongoDAL,
    agent_registry: AgentRegistry,
    membership_source: RoomMembershipSeedSource,
) -> RoomDeps:
    repository = RoomMongoRepository(mongo=mongo)
    message_repository = MessageMongoRepository(mongo=mongo)
    facade = RoomFacade(
        repository=repository,
        message_repository=message_repository,
        agent_registry=agent_registry,
        membership_source=membership_source,
        id_factory=lambda: uuid4().hex,
        now=utcnow,
    )
    return RoomDeps(
        room_registry=facade,
        room_management=facade,
        room_message_store=facade,
        room_history_reader=facade,
        room_ownership_reader=facade,
    )
```

If a broader app container already exists on the implementation branch, extend that file in place instead of creating a parallel container.

- [ ] **Step 3: Instantiate RoomDeps during lifespan startup**

In `main.py`, after Mongo and `AgentDeps` are ready:
- Build `MongoDALImpl(database=mongodb.db)`.
- Reuse `_agent_deps.agent_registry` for explicit room-agent reads.
- Build `RoomMembershipSeedSource` legacy adapter.
- Build `RoomDeps`.
- Bind `services.room_services.room_services.bind_facade(room_facade)`.
- Bind the `api.room_center.room_center` instance or its underlying `RoomServices` dependency.
- Bind `modules.RoomMessageCenter.room_message_center.bind_facade(room_facade)`.
- Preserve the Phase 3 review-fix relay liveness wiring: `RelayHubLivenessReader.is_hub_online()` is the single authoritative async path and delegates to `RelayService.is_hub_alive()`. Do not add a cached reader path, `HubLivenessProbe`, `RelayHubLivenessProbe`, `is_hub_online_async`, or direct relay `_mongo` access.
- Keep Redis/SSE/relay startup order unchanged except where RoomMessageCenter binding must happen before traffic/background work.

- [ ] **Step 4: Add startup fail-fast tests**

Cover:
- If Mongo is unavailable, startup logs a warning and does not partially bind Room services.
- If `RoomDeps` is built, all legacy adapters are bound before `await agent_health_service.start()` and relay startup.
- Room binding does not require Redis or relay to be initialized.
- Existing Phase 3 hub liveness tests still pass after Room startup wiring changes.

- [ ] **Step 5: Run startup-related tests**

```bash
uv run python -m pytest tests/test_room_protocols.py tests/test_multi_worker_safety.py tests/test_heartbeat_fixes.py tests/test_api_room_center.py -q
```

Expected: PASS.

### Task 10: Golden Tests and Endpoint Compatibility

**Files:**
- Create/modify: `tests/test_room_golden.py`
- Modify: `tests/test_api_room_center.py`
- Modify: `tests/test_flow_contracts.py`
- Modify: `tests/test_service_room.py`

- [ ] **Step 1: Add golden create tests**

Fixture:
- Fake agent registry returns active agents `a1`, `a2`, and an inactive `a3`.
- Fake membership source returns a saved group and visible current agents.
- Fake repositories initially empty.

Assert:
- Manual seed endpoint response status, success flag, room id, room object, room agent set, and provenance match current behavior.
- Saved group seed response includes `source_group_id` and `source_group_name`.
- All current agents seed includes only active visible agents.
- Legacy `room_agent_set` input is still accepted by the adapter and normalized.

- [ ] **Step 2: Add golden update membership tests**

Assert:
- Replacing room agent set through `/roomCenter/updateRoomAgentSet` preserves current response shape.
- Inaccessible private agents return existing 403-style response.
- Unknown agents return existing 400-style response.
- Saved group edits set `membership_origin_status="seeded_edited"`.

- [ ] **Step 3: Add golden delete test**

Assert:
- Owner can delete.
- Non-owner cannot delete through facade and remains blocked by API auth.
- Room-owned user and agent messages are deleted.
- Transitional non-room cleanup remains in legacy adapter and is not imported by `room/**`.

- [ ] **Step 4: Add golden user-message persistence tests**

Assert:
- `/roomCenter/createAndParseUserMessage` includes `dispatch_root_message_id == message_id`.
- `/roomCenter/sendMessage` includes `dispatch_root_message_id == message_id`.
- `client_request_id` remains required by API endpoints.
- Attachment resolution failures return the same error shape.

- [ ] **Step 5: Add golden history tests**

Assert:
- `/roomCenter/inquiryRoomMessagesByRoomId` returns combined user and agent messages in stable legacy order.
- Agent task artifact text extraction remains in the legacy adapter and still works.
- `RoomHistoryReader.get_messages_for_room()` returns raw `RoomMessageInfo` suitable for Phase 5 projection.

- [ ] **Step 6: Add golden ownership tests**

Assert:
- `verify_room_agent_membership()` returns true for room agents and false for non-members.
- `verify_room_hub_ownership()` returns true only when one room agent is hub-backed by the requested hub.
- Existing `api.room_center.verify_room_ownership()` keeps HTTP 400/403/404 behavior.

- [ ] **Step 7: Run endpoint compatibility tests**

```bash
uv run python -m pytest tests/test_room_golden.py tests/test_api_room_center.py tests/test_flow_contracts.py tests/test_service_room.py -q
```

Expected: PASS with endpoint response bodies unchanged except for intentional fail-fast startup behavior in unit tests.

### Task 11: Final Import Boundary and Full Gate

**Files:**
- Modify: `tests/test_room_protocols.py`
- Maybe modify: `docs/MODULAR_DECOUPLING_DESIGN.md` only if documenting actual Phase 4 deviations

- [ ] **Step 1: Run Room module tests**

```bash
uv run python -m pytest tests/test_room_protocols.py tests/test_room_repository.py tests/test_room_membership.py tests/test_room_facade.py tests/test_room_golden.py -q
```

Expected: PASS.

- [ ] **Step 2: Run legacy room compatibility tests**

```bash
uv run python -m pytest tests/test_service_room.py tests/test_api_room_center.py tests/test_module_room_message_center.py tests/test_flow_contracts.py -q
```

Expected: PASS.

- [ ] **Step 3: Run room-adjacent execution and lock tests**

```bash
uv run python -m pytest tests/test_distributed_room_lock.py tests/test_room_coordinator_service.py tests/test_get_room_ids_non_terminal_runs.py -q
```

Expected: PASS.

- [ ] **Step 4: Run completed phase tests**

```bash
uv run python -m pytest tests/test_common_foundation.py tests/test_agent_protocols.py tests/test_agent_repository.py tests/test_agent_facade.py tests/test_agent_golden.py tests/test_service_agent.py tests/test_heartbeat_fixes.py tests/test_adapter_protocols.py tests/test_dal_protocols.py -q
```

Expected: PASS.

- [ ] **Step 5: Run import-boundary tests**

```bash
uv run python -m pytest tests/test_room_protocols.py -k import_boundary -q
```

Expected: PASS and no forbidden imports from `room/**`.

- [ ] **Step 6: Run broad regression suite if time allows**

```bash
uv run python -m pytest -q
```

Expected: PASS. If too slow, record the targeted commands above and any skipped broad-suite reason.

- [ ] **Step 7: Commit Phase 4**

```bash
git status --short
git add room common/protocols common/dto pyproject.toml container.py main.py services modules tests
git commit -m "feat: extract room module facade"
```

Expected: one focused Phase 4 implementation commit, or several commits matching task boundaries if using subagents.

- [ ] **Step 8: Re-run final Room gate after commit**

```bash
uv run python -m pytest tests/test_room_protocols.py tests/test_room_repository.py tests/test_room_membership.py tests/test_room_facade.py tests/test_room_golden.py tests/test_api_room_center.py tests/test_flow_contracts.py -q
```

Expected: PASS.

## Migration Adapter Wiring

The C3 pattern is mandatory for `services/room_services.py`, `modules/RoomCenter.py`, and the Room persistence seams in `modules/RoomMessageCenter.py`:
- No import-time construction of new Room business dependencies.
- No fallback to legacy Room CRUD, membership, or raw persistence logic before bind.
- Before bind, raise `RuntimeError`.
- After bind, migrated public methods delegate to the new facade.
- Legacy execution/SSE code can remain in `RoomMessageCenter`, but it must not bypass the bound Room facade for methods migrated in Phase 4.

Recommended binding order during startup:
1. Connect Mongo and initialize DAL.
2. Build `AgentDeps` exactly as Phase 3 does.
3. Build the legacy `RoomMembershipSeedSource` adapter, using Agent protocol dependencies where possible.
4. Build `RoomDeps`.
5. Bind `services.room_services.room_services`.
6. Bind the `RoomCenter` instance used by `api.room_center.py`, or ensure it delegates to the already-bound `room_services`.
7. Bind `modules.RoomMessageCenter.room_message_center` to the Room facade before background processing can run.
8. Initialize Redis/SSE/event broker exactly as current startup does.
9. Initialize relay and background jobs exactly as current startup does.
10. Serve traffic only after Agent and Room adapters are both bound.

Avoid circular imports:
- `container.py` can import concrete implementations.
- `main.py` can import `container.py`.
- `room/**` must never import `container.py` or `main.py`.
- `room/**` must never import `agent/**`; use Common Agent protocols only.
- Legacy `services/**` and `modules/**` may import `room` during migration because they are wrappers, but `room` must not import them.

## Test Plan

Unit tests:
- `tests/test_room_repository.py`: Mongo repository query/update/history behavior against fakes.
- `tests/test_room_membership.py`: manual, saved group, all current agents, visibility validation, and provenance.
- `tests/test_room_facade.py`: facade behavior with fake repositories, fake Agent registry, and fake membership source.
- `tests/test_room_protocols.py`: runtime protocol conformance, exports, packaging, container assembly, and import boundaries.

Golden integration tests:
- `tests/test_room_golden.py`: create for all three seed modes, delete, update membership, user-message persistence, agent-message persistence, history, ownership, and `dispatch_root_message_id`.
- Existing API tests: `tests/test_api_room_center.py`, `tests/test_flow_contracts.py`.
- Existing room-adjacent tests: `tests/test_room_coordinator_service.py`, `tests/test_get_room_ids_non_terminal_runs.py`.

Migration adapter tests:
- `tests/test_service_room.py`: fail-fast before bind and exact response compatibility after bind.
- `tests/test_module_room_message_center.py`: Room facade binding seam exists while execution/SSE helpers remain compatible.
- `tests/test_api_room_center.py`: endpoint request parsing and auth behavior remain stable.

Import boundary tests:
- `room/**` imports only stdlib, `common`, and `room`.
- `room/**` does not import `agent`, `services`, `modules`, `api`, `database`, `models`, `main`, `container`, `a2a_adapter`, or `llm_gateway`.
- Existing Agent, adapter, and DAL boundary tests continue to pass.

Verification commands:

```bash
uv run python -m pytest tests/test_room_protocols.py tests/test_room_repository.py tests/test_room_membership.py tests/test_room_facade.py tests/test_room_golden.py -q
uv run python -m pytest tests/test_service_room.py tests/test_api_room_center.py tests/test_module_room_message_center.py tests/test_flow_contracts.py -q
uv run python -m pytest tests/test_distributed_room_lock.py tests/test_room_coordinator_service.py tests/test_get_room_ids_non_terminal_runs.py -q
uv run python -m pytest tests/test_common_foundation.py tests/test_agent_protocols.py tests/test_agent_repository.py tests/test_agent_facade.py tests/test_agent_golden.py tests/test_service_agent.py tests/test_heartbeat_fixes.py tests/test_adapter_protocols.py tests/test_dal_protocols.py -q
```

## Gate Criteria Checklist

- [ ] `room/` package exists and is listed in `pyproject.toml`.
- [ ] `RoomFacade` satisfies `RoomRegistry`, `RoomManagement`, `RoomMessageStore`, `RoomHistoryReader`, and `RoomOwnershipReader` at runtime.
- [ ] `RoomMongoRepository` satisfies `RoomRepository` at runtime.
- [ ] `MessageMongoRepository` satisfies `MessageRepository` at runtime.
- [ ] `RoomDeps` exists in `container.py` alongside `AgentDeps`.
- [ ] `create_room_deps()` binds one `RoomFacade` to all five Room protocol fields.
- [ ] `room/**` import-boundary test passes.
- [ ] No `room/**` imports from `agent`, `services`, `modules`, `api`, `database`, `models`, `main`, `container`, `a2a_adapter`, or `llm_gateway`.
- [ ] Room membership seed resolution lives inside the Room module.
- [ ] Manual membership seed supports explicit agent IDs and rejects unknown/inaccessible agents.
- [ ] Saved group seed resolves group ownership and provenance correctly.
- [ ] All current agents seed includes active visible agents only.
- [ ] Room create, update, delete, and membership golden tests pass.
- [ ] `RoomHistoryReader` returns raw message history for Phase 5 Context & Memory consumers.
- [ ] `RoomMessageStore` saves user messages and agent messages without calling SSE or Execution code.
- [ ] `/roomCenter/createAndParseUserMessage` response includes `dispatch_root_message_id == message_id`.
- [ ] `/roomCenter/sendMessage` response includes `dispatch_root_message_id == message_id`.
- [ ] `services/room_services.py` uses `bind_facade()` and raises `RuntimeError` before bind for migrated methods.
- [ ] `modules/RoomCenter.py` delegates through the bound room service/facade.
- [ ] `modules/RoomMessageCenter.py` has a Room facade binding seam but keeps SSE/delivery/execution behavior legacy.
- [ ] Existing room API response compatibility tests pass.
- [ ] Phase 0, Phase 1, Phase 2, and current-main Phase 3 review-fix tests still pass, including `tests/test_common_foundation.py`, `tests/test_service_agent.py`, and `tests/test_heartbeat_fixes.py`.

## Risk Assessment

### Risk: Current branch is missing Phase 3 review fixes

Impact: Phase 4 may build on a branch missing Agent protocols, `AgentDeps`, C3 adapter patterns, async hub liveness validation, Agent visible-list query passthrough, or final relay liveness simplification.

Mitigation:
- Start with Task 0 branch, artifact, and review-fix checks.
- Create `phase-4-room-module` from current `main` after the Phase 3 review fixes.
- If using `phase-3-agent-module`, first fast-forward or rebase it onto current `main`.
- Do not start Room extraction if Phase 3 files or review-fix contracts are missing.
- Keep `HubLivenessReader.is_hub_online()` async and authoritative. Do not add `HubLivenessProbe`, `RelayHubLivenessProbe`, cached liveness paths, or `is_hub_online_async` duck-typing.

Verification:
- `git status --short --branch`
- `test -f agent/facade.py`
- `rg -n "validate_hub_liveness_reader|query: dict \\| None" common/protocols agent services`
- `if rg -n "HubLivenessProbe|RelayHubLivenessProbe|is_hub_online_async|getattr\\(.*is_hub_online_async" common agent services; then exit 1; fi`
- `uv run python -m pytest tests/test_common_foundation.py tests/test_agent_protocols.py tests/test_service_agent.py tests/test_heartbeat_fixes.py -q`

### Risk: Room module imports legacy models or services

Impact: The Room extraction would violate hard boundaries and make Phase 5/6 coupling worse.

Mitigation:
- Add import-boundary tests before implementation.
- Keep all legacy model conversion in `services/room_services.py`.
- Keep saved-group/current-agent adapters outside `room/`.

Verification:
- `uv run python -m pytest tests/test_room_protocols.py -k import_boundary -q`

### Risk: Membership seed behavior regresses

Impact: Room creation or membership updates may include wrong agents, leak private agents, or lose provenance fields.

Mitigation:
- Port seed behavior into `room/membership.py` with tests for manual, saved group, all current agents, private agent access, and provenance.
- Apply visibility validation before persistence.
- Keep legacy `room_agent_set` normalization tests.

Verification:
- `uv run python -m pytest tests/test_room_membership.py tests/test_room_golden.py -k membership -q`

### Risk: `RoomMessageCenter` extraction scope creeps into Execution/SSE

Impact: Phase 4 could destabilize message delivery, locks, supervisor runs, relay dispatch, or SSE.

Mitigation:
- Bind only Room persistence/history seams in `RoomMessageCenter`.
- Keep queueing, locks, transports, supervisor, HITL, task tracking, and SSE code legacy until Phase 6/7.
- Run lock and RoomMessageCenter tests after adapter changes.

Verification:
- `uv run python -m pytest tests/test_module_room_message_center.py tests/test_distributed_room_lock.py -q`

### Risk: `dispatch_root_message_id` disappears

Impact: Frontend turn correlation breaks, and the Phase 4 gate fails.

Mitigation:
- Add golden tests for `/roomCenter/createAndParseUserMessage` and `/roomCenter/sendMessage`.
- Set `SavedUserMessage.dispatch_root_message_id` in `room/translators.py` or `RoomFacade.save_user_message()`.
- Keep legacy adapter response conversion explicit.

Verification:
- `uv run python -m pytest tests/test_room_golden.py tests/test_api_room_center.py -k dispatch_root_message_id -q`

### Risk: Repository protocol becomes too generic

Impact: Facade may expose raw Mongo query coupling or start depending on collection internals.

Mitigation:
- Extend `RoomRepository` and `MessageRepository` with domain-specific methods only.
- Keep raw `MongoCollection` access inside `room/repository/mongo.py`.
- Do not add generic `find`, `aggregate`, or collection properties to repository protocols.

Verification:
- Repository tests inspect queries.
- Import-boundary tests pass.

### Risk: Delete cleanup crosses module boundaries

Impact: Room facade could take ownership of memory/file/platform data prematurely.

Mitigation:
- Room facade deletes rooms and Room-owned raw message data only.
- Keep S3, file metadata, room memory, and conversation content cleanup in the legacy adapter until their modules own those workflows.
- Document any temporary cleanup in adapter tests.

Verification:
- `room/**` import-boundary test.
- Golden delete tests assert room-owned deletes happen through repositories and non-room cleanup stays outside `room/`.

### Risk: History output order drifts

Impact: Room transcripts, Context & Memory projection, and frontend rendering may reorder messages.

Mitigation:
- Port current sort key: `message_created_at`, `step_number`, `message_id`.
- Test combined user/agent messages with equal timestamps.
- Keep rich artifact text extraction in legacy adapter, while `RoomHistoryReader` exposes raw records.

Verification:
- `uv run python -m pytest tests/test_room_repository.py tests/test_room_golden.py -k history -q`

### Risk: C3 fail-fast breaks tests that construct services directly

Impact: Existing unit tests may fail because they instantiate legacy services without startup wiring.

Mitigation:
- Update tests to bind fake facades explicitly.
- Keep pure helper methods testable without binding where they do not require Room persistence.
- Use clear error text for unbound service methods.

Verification:
- `uv run python -m pytest tests/test_service_room.py tests/test_module_room_message_center.py -q`

### Risk: Saved group ownership remains trapped in Agent legacy code

Impact: Room module cannot own membership resolution without importing legacy Agent group models.

Mitigation:
- Add `RoomMembershipSeedSource` as a Common protocol.
- Implement the adapter outside `room/`.
- Return `SavedAgentGroupSnapshot` DTOs only.

Verification:
- `tests/test_room_membership.py`
- `tests/test_room_protocols.py`

## Final Handoff Notes

Implement Phase 4 in small commits:
1. Branch/scaffold and failing tests.
2. Room and message repositories.
3. Translators, message graph helpers, and membership seed resolution.
4. RoomFacade lifecycle and membership.
5. RoomFacade message store and history.
6. Legacy migration adapters.
7. Container/startup wiring.
8. Golden tests and boundary gates.

Do not start by editing `api/room_center.py`. The safest path is to make the new facade match legacy behavior behind `services.room_services`, then run existing endpoint tests unchanged. Keep `RoomMessageCenter` execution and SSE behavior in place until Phase 6/7.
