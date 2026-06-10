# DAL Database Module Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete DAL/database convergence so production modules use `dal/*` plus module-scoped repositories/facades, while `database.mongodb` remains only for operational migrations and removed compatibility paths.

**Architecture:** Follow `docs/MODULAR_DECOUPLING_DESIGN.md`: modules own domain repositories, shared infrastructure enters through `common.protocols.dal_protocols`, and `container.py` is the composition root for concrete DAL adapters. Migrate by shrinking exact blockers: first add gates that record the current database coupling, then move one module surface at a time behind repository protocols, and finally remove `app_shell.database_service` as a concrete production dependency.

**Tech Stack:** Python 3.11+, FastAPI route dependency binding, Motor via `dal.mongo.MongoDALImpl`, Pinecone via `dal.pinecone.VectorDALImpl`, S3 via `dal.s3.ObjectStorageDALImpl`, pytest/pytest-asyncio, Ruff.

---

## Scope

This plan covers production DAL/database convergence only.

In scope:

- Replace production imports of `database.mongodb` and `database.pinecone_db` with `dal` adapters and module repositories.
- Stop passing `app_shell.database_service.DatabaseService` as a cross-module service locator.
- Move collection helpers into the owning module repositories:
  - Agent-owned data: agent registry, capability issues, domain aliases, agent call counters.
  - Room-owned data: rooms, user messages, agent messages, quotes, room cleanup linkage.
  - Execution-owned data: runs, run events, task tracking fields, HITL persistence, stale task recovery queries.
  - Context-memory-owned data: room memory, conversation content, memory search storage.
  - Platform-owned data: API keys, rate limit request collections, file metadata.
  - Hub-owned data: hubs, hub response journal, task ownership.
- Keep route paths, response models, and frontend behavior unchanged.
- Keep `database/migration/*` operational scripts working.

Out of scope:

- Full A2A SDK confinement. This plan only removes database access from A2A runtime surfaces.
- Replacing MongoDB, Pinecone, S3, Redis, or the A2A SDK.
- Changing frontend contracts.

## Current Blockers

The current `tests/fixtures/phase9_cleanup_manifest.json` records these DAL-related blockers:

- `app_shell/a2a_runtime.py`
- `app_shell/agent_capability_issue_service.py`
- `app_shell/agent_health_service.py`
- `app_shell/database_service.py`
- `app_shell/domain_alias_service.py`
- `app_shell/memory_search_service.py`
- `app_shell/room_runtime.py`

Additional production coupling exists in `main.py`, `execution/run_command_handler.py`, relay startup, and route binders that receive `DatabaseService`.

## Target Boundary

After this plan:

- `database.mongodb` may be imported only by `database/`, `database/migration/`, and tests that explicitly unit-test legacy migration helpers.
- `database.pinecone_db` may be imported only by `database/` and the new `dal.pinecone` adapter.
- `app_shell/database_service.py` may contain protocol aliases and a fail-fast compatibility object only if route imports still require the module path; it must not import `database.mongodb`, `database.pinecone_db`, `a2a.types`, or own Mongo/Pinecone logic.
- `main.py` constructs `MongoDALImpl`, `VectorDALImpl`, `ObjectStorageDALImpl`, and Redis DAL clients through `container.py` helpers before constructing module facades.
- Business modules depend on `common.protocols` and module repositories, not concrete app-shell or database singletons.

## File Structure

Create:

- `tests/fixtures/dal_database_convergence_manifest.json`
  - Exact shrinking inventory of temporary DAL/database blockers.
- `tests/test_dal_database_convergence_gate.py`
  - AST gates for forbidden `database.mongodb`, `database.pinecone_db`, hidden Mongo fallbacks, and `app_shell.database_service.DatabaseService` production coupling.
- `agent/repository/capability_issue_mongo.py`
  - Mongo repository for `agent_capability_issues`.
- `agent/capability_issue.py`
  - Agent-owned service for issue recording/exclusion cache.
- `agent/domain_alias.py`
  - Agent-owned public URL alias generator using `AgentRepository.public_url_exists`.
- `room/repository/quote_mongo.py`
  - Mongo repository for `room_quotes`.
- `execution/repository/__init__.py`
- `execution/repository/mongo.py`
  - Mongo repositories for runs, run events, HITL requests, task tracking, and cancellation-facing task queries.
- `platform_module/attachments.py`
  - Platform-owned file metadata lookup protocol/adapter for Room attachment resolution and room cleanup.
- `platform_module/api_keys.py`
  - Platform-owned API key store/authentication repository over `MongoDAL`.
- `context_memory/search_adapter.py`
  - App-shell compatible memory search adapter backed by `ContextMemoryFacade`.
- `jobs/repository_ports.py`
  - Narrow protocol types for job dependencies that currently consume broad `DatabaseService`.

Modify:

- `common/protocols/repository_protocols.py`
  - Add missing focused repository protocols used by this migration.
- `common/protocols/agent_protocols.py`
  - Export agent capability and domain alias protocols if cross-module injection is required.
- `common/protocols/execution_protocols.py`
  - Add task reader/task tracking/HITL persistence protocols used by routes/jobs/transports.
- `common/protocols/platform_protocols.py`
  - Add API key store protocol if not already complete for route usage.
- `dal/mongo/client.py`
  - Add only generic collection operations required by repositories.
- `container.py`
  - Construct all repository/facade dependencies from DAL adapters.
- `main.py`
  - Stop importing `database.mongodb` in production startup; wire DAL-backed modules.
- `app_shell/database_service.py`
  - Convert to fail-fast compatibility module or remove production binding.
- `app_shell/a2a_runtime.py`
- `app_shell/agent_capability_issue_service.py`
- `app_shell/agent_health_service.py`
- `app_shell/domain_alias_service.py`
- `app_shell/memory_search_service.py`
- `app_shell/room_runtime.py`
- `execution/run_command_handler.py`
- `execution/run_lifecycle.py`
- `execution/run_queries.py`
- `execution/cancellation.py`
- `jobs/stale_task_checker.py`
- `jobs/compaction_sweep.py`
- `jobs/cleanup_orphaned_uploads.py`
- `api_gateway/routes/a2a_task_routes.py`
- `api_gateway/routes/agent_group_routes.py`
- `api_gateway/routes/discovery_api_key_routes.py`
- `api_gateway/routes/sse_routes.py`
- `api_gateway/routes/room_routes.py`
- `hub_runtime_bridge/repository/mongo.py`
- `tests/fixtures/phase9_cleanup_manifest.json`
- `System-Architecture.md`
- `docs/MODULAR_DECOUPLING_DESIGN.md`

---

## Task 1: Add DAL/database convergence gates

**Files:**

- Create: `tests/fixtures/dal_database_convergence_manifest.json`
- Create: `tests/test_dal_database_convergence_gate.py`
- Modify: `tests/test_phase9_cleanup_gate.py`

- [ ] **Step 1: Add the initial shrinking manifest**

Create `tests/fixtures/dal_database_convergence_manifest.json`:

```json
{
  "database_singleton_import_blockers": [
    "app_shell/a2a_runtime.py",
    "app_shell/agent_capability_issue_service.py",
    "app_shell/agent_health_service.py",
    "app_shell/database_service.py",
    "app_shell/domain_alias_service.py",
    "app_shell/memory_search_service.py",
    "app_shell/relay_service.py",
    "app_shell/room_runtime.py",
    "main.py"
  ],
  "hidden_mongo_fallback_blockers": [
    "execution/dispatch/transports/relay.py",
    "execution/run_command_handler.py",
    "jobs/stale_task_checker.py"
  ],
  "database_service_type_blockers": [
    "api_gateway/routes/a2a_task_routes.py",
    "api_gateway/routes/agent_group_routes.py",
    "api_gateway/routes/room_routes.py",
    "api_gateway/routes/sse_routes.py",
    "app_shell/a2a_runtime.py",
    "app_shell/agent_resolver_service.py",
    "app_shell/compaction_service.py",
    "app_shell/debate_service.py",
    "app_shell/memory_service.py",
    "app_shell/quote_service.py",
    "app_shell/relay_service.py",
    "app_shell/room_coordinator_service.py",
    "app_shell/room_membership_source.py",
    "app_shell/room_runtime.py",
    "execution/dispatch/agent_dispatcher.py",
    "execution/dispatch/agent_message_processor.py",
    "execution/dispatch/task_notifications.py",
    "execution/dispatch/transports/relay.py",
    "execution/dispatch/transports/webhook.py",
    "execution/orchestration/queue_executor.py",
    "execution/orchestration/room_supervisor_service.py",
    "execution/orchestration/supervisor_executor.py",
    "main.py"
  ],
  "database_service_duck_type_blockers": [
    "api_gateway/dependencies.py",
    "api_gateway/routes/a2a_task_routes.py",
    "api_gateway/routes/agent_group_routes.py",
    "api_gateway/routes/room_routes.py",
    "api_gateway/routes/sse_routes.py",
    "app_shell/agent_matcher.py",
    "app_shell/agent_resolver_service.py",
    "app_shell/compaction_service.py",
    "app_shell/database_service.py",
    "app_shell/debate_service.py",
    "app_shell/memory_service.py",
    "app_shell/relay_service.py",
    "app_shell/room_coordinator_service.py",
    "app_shell/room_membership_source.py",
    "app_shell/room_runtime.py",
    "common/utils/turn_id.py",
    "execution/client_request_id.py",
    "execution/cancellation.py",
    "execution/dispatch/agent_dispatcher.py",
    "execution/dispatch/agent_message_processor.py",
    "execution/dispatch/response_handler.py",
    "execution/dispatch/transports/direct.py",
    "execution/hitl/adapters.py",
    "execution/hitl/factory.py",
    "execution/hitl/service.py",
    "execution/orchestration/factory.py",
    "execution/orchestration/queue_executor.py",
    "execution/orchestration/room_message_center.py",
    "execution/orchestration/room_supervisor_service.py",
    "execution/orchestration/supervisor_executor.py",
    "hub_runtime_bridge/adapters/legacy_failure.py",
    "jobs/stale_task_checker.py",
    "main.py"
  ],
  "pinecone_singleton_import_blockers": [
    "app_shell/database_service.py",
    "app_shell/memory_search_service.py",
    "main.py"
  ]
}
```

- [ ] **Step 2: Add AST tests that force blockers to shrink exactly**

Create `tests/test_dal_database_convergence_gate.py`:

```python
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests/fixtures/dal_database_convergence_manifest.json"
PRODUCTION_ROOTS = (
    "api",
    "api_gateway",
    "agent",
    "room",
    "context_memory",
    "delivery",
    "execution",
    "hub_runtime_bridge",
    "a2a_adapter",
    "platform_module",
    "llm_gateway",
    "app_shell",
    "jobs",
    "common",
    "container.py",
    "main.py",
)
ALWAYS_ALLOWED_DATABASE_ROOTS = ("database",)


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text())


def _py_files() -> list[Path]:
    files: list[Path] = []
    for root in PRODUCTION_ROOTS:
        path = ROOT / root
        if not path.exists():
            continue
        files.extend([path] if path.is_file() else sorted(path.rglob("*.py")))
    return sorted(set(files))


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _imports_prefix(path: Path, prefix: str) -> bool:
    return any(
        module == prefix or module.startswith(prefix + ".")
        for module in _imports(path)
    )


def _violating_files(prefix: str) -> list[str]:
    found: list[str] = []
    for path in _py_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(ALWAYS_ALLOWED_DATABASE_ROOTS):
            continue
        if _imports_prefix(path, prefix):
            found.append(rel)
    return sorted(found)


def _has_hidden_mongo_fallback(path: Path) -> bool:
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "mongodb"
            for target in node.targets
        ):
            return True
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "mongodb"
        ):
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"getattr", "hasattr"}
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "mongodb"
        ):
            return True
    return False


def _has_database_service_duck_usage(path: Path) -> bool:
    source = path.read_text()
    if (
        "database_service" not in source
        and "db_service" not in source
        and "DatabaseService" not in source
    ):
        return False
    tree = ast.parse(source, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
                if arg.arg in {"database_service", "db_service"}:
                    return True
        if isinstance(node, ast.Assign):
            target_names = {
                target.attr if isinstance(target, ast.Attribute) else target.id
                for target in node.targets
                if isinstance(target, (ast.Attribute, ast.Name))
            }
            if target_names.intersection({"_db", "_database_service", "_db_service"}):
                if isinstance(node.value, ast.Name) and node.value.id in {"database_service", "db_service"}:
                    return True
    forbidden_snippets = (
        "self.database_service",
        "self._database_service",
        "self._db_service",
        "self._db = db_service",
        "database_service=",
        "db_service=",
        "DatabaseHITLPersistenceAdapter",
        "_DatabaseServiceLike",
        "\"database_service\":",
        "\"db_service\":",
        "'database_service':",
        "'db_service':",
        "db_service:",
        "db_service =",
        "global db_service",
        "(\"db_service\"",
        "('db_service'",
    )
    return any(snippet in source for snippet in forbidden_snippets)


def test_database_mongodb_import_blockers_are_exact():
    expected = sorted(_manifest()["database_singleton_import_blockers"])
    assert _violating_files("database.mongodb") == expected


def test_hidden_mongo_fallback_blockers_are_exact():
    expected = sorted(_manifest()["hidden_mongo_fallback_blockers"])
    found = [
        path.relative_to(ROOT).as_posix()
        for path in _py_files()
        if _has_hidden_mongo_fallback(path)
    ]
    assert sorted(found) == expected


def test_database_pinecone_import_blockers_are_exact():
    expected = sorted(_manifest()["pinecone_singleton_import_blockers"])
    assert _violating_files("database.pinecone_db") == expected


def test_database_service_type_blockers_are_exact():
    expected = sorted(_manifest()["database_service_type_blockers"])
    found: list[str] = []
    for path in _py_files():
        rel = path.relative_to(ROOT).as_posix()
        if _imports_prefix(path, "app_shell.database_service"):
            found.append(rel)
    assert sorted(found) == expected


def test_database_service_duck_type_blockers_are_exact():
    expected = sorted(_manifest()["database_service_duck_type_blockers"])
    found = [
        path.relative_to(ROOT).as_posix()
        for path in _py_files()
        if _has_database_service_duck_usage(path)
    ]
    assert sorted(found) == expected
```

- [ ] **Step 3: Run the new gates**

Run:

```bash
uv run pytest tests/test_dal_database_convergence_gate.py -q
```

Expected result: tests pass with the initial exact inventory.

- [ ] **Step 4: Add a phase9 assertion that the DAL convergence manifest cannot grow silently**

In `tests/test_phase9_cleanup_gate.py`, add:

```python
def test_dal_database_convergence_manifest_exists_and_has_no_unknown_sections():
    manifest = json.loads(Path("tests/fixtures/dal_database_convergence_manifest.json").read_text())
    assert set(manifest) == {
        "database_singleton_import_blockers",
        "hidden_mongo_fallback_blockers",
        "database_service_type_blockers",
        "database_service_duck_type_blockers",
        "pinecone_singleton_import_blockers",
    }
```

- [ ] **Step 5: Run phase9 cleanup gates**

Run:

```bash
uv run pytest tests/test_dal_database_convergence_gate.py tests/test_phase9_cleanup_gate.py -q
```

Expected result: both test files pass.

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/dal_database_convergence_manifest.json tests/test_dal_database_convergence_gate.py tests/test_phase9_cleanup_gate.py
git commit -m "test: add dal database convergence gates"
```

---

## Task 2: Align DAL primitives for repository-owned Mongo access

**Files:**

- Modify: `common/protocols/dal_protocols.py`
- Modify: `dal/mongo/client.py`
- Modify: `tests/test_dal_protocols.py`
- Modify: `tests/test_dal_unit.py`

- [ ] **Step 1: Add protocol tests for required Mongo collection operations**

In `tests/test_dal_protocols.py`, add:

```python
from common.protocols import MongoCollection


def test_mongo_collection_protocol_covers_repository_operations():
    required = {
        "find_one",
        "find",
        "find_one_and_update",
        "insert_one",
        "insert_many",
        "update_one",
        "update_many",
        "delete_one",
        "delete_many",
        "count",
        "aggregate",
        "create_index",
        "create_indexes",
        "bulk_write",
        "distinct",
        "find_one_by_stable_or_native_id",
        "watch",
    }
    assert required.issubset(set(MongoCollection.__dict__))
```

- [ ] **Step 2: Run the protocol test and confirm it fails**

Run:

```bash
uv run pytest tests/test_dal_protocols.py::test_mongo_collection_protocol_covers_repository_operations -q
```

Expected result: FAIL because `create_indexes`, `bulk_write`, and `distinct` are not in the protocol yet.

- [ ] **Step 3: Extend `MongoCollection` protocol**

In `common/protocols/dal_protocols.py`, add these methods to `MongoCollection`:

```python
    async def create_indexes(self, indexes: list, **kwargs) -> list[str]: ...
    async def bulk_write(self, operations: list, **kwargs) -> Any: ...
    async def distinct(self, key: str, query: dict | None = None) -> list: ...
```

Also add `Any` to the imports:

```python
from typing import Any, Protocol, runtime_checkable
```

- [ ] **Step 4: Add adapter unit tests**

In `tests/test_dal_unit.py`, add fake Motor coverage:

```python
@pytest.mark.asyncio
async def test_mongo_collection_adapter_delegates_bulk_write():
    collection = AsyncMock()
    collection.bulk_write = AsyncMock(return_value="bulk-result")
    adapter = MongoCollectionAdapter(collection)

    result = await adapter.bulk_write(["op"], ordered=False)

    assert result == "bulk-result"
    collection.bulk_write.assert_awaited_once_with(["op"], ordered=False)


@pytest.mark.asyncio
async def test_mongo_collection_adapter_delegates_distinct():
    collection = AsyncMock()
    collection.distinct = AsyncMock(return_value=["room-1"])
    adapter = MongoCollectionAdapter(collection)

    result = await adapter.distinct("room_id", {"state": "running"})

    assert result == ["room-1"]
    collection.distinct.assert_awaited_once_with("room_id", {"state": "running"})
```

- [ ] **Step 5: Implement adapter methods**

In `dal/mongo/client.py`, add:

```python
    async def create_indexes(self, indexes: list, **kwargs) -> list[str]:
        return await self._collection.create_indexes(indexes, **kwargs)

    async def bulk_write(self, operations: list, **kwargs) -> Any:
        return await self._collection.bulk_write(operations, **kwargs)

    async def distinct(self, key: str, query: dict | None = None) -> list:
        return await self._collection.distinct(key, query or {})
```

- [ ] **Step 6: Run DAL tests**

Run:

```bash
uv run pytest tests/test_dal_protocols.py tests/test_dal_unit.py -q
```

Expected result: DAL protocol and adapter tests pass.

- [ ] **Step 7: Commit**

```bash
git add common/protocols/dal_protocols.py dal/mongo/client.py tests/test_dal_protocols.py tests/test_dal_unit.py
git commit -m "refactor(dal): cover repository mongo operations"
```

---

## Task 3: Move agent DB blockers into Agent-owned repositories

**Files:**

- Create: `agent/repository/capability_issue_mongo.py`
- Create: `agent/capability_issue.py`
- Create: `agent/domain_alias.py`
- Modify: `app_shell/agent_capability_issue_service.py`
- Modify: `app_shell/domain_alias_service.py`
- Modify: `app_shell/agent_health_service.py`
- Modify: `app_shell/agent_resolver_service.py`
- Modify: `app_shell/agent_matcher.py`
- Modify: `container.py`
- Modify: `main.py`
- Modify: `tests/test_agent_facade.py`
- Modify: `tests/test_service_agent_resolver.py`
- Modify: `tests/fixtures/dal_database_convergence_manifest.json`

- [ ] **Step 1: Add repository tests for capability issues**

Create focused tests in `tests/test_agent_capability_issue_repository.py`:

```python
import re

import pytest

from agent.repository.capability_issue_mongo import AgentCapabilityIssueMongoRepository


class FakeCollection:
    def __init__(self):
        self.inserted = []
        self.pipeline = None

    async def insert_one(self, doc):
        self.inserted.append(doc)
        return doc["issue_id"]

    async def aggregate(self, pipeline):
        self.pipeline = pipeline
        return [{"_id": "agent-1"}, {"_id": "agent-2"}]


class FakeMongo:
    def __init__(self):
        self.collection_obj = FakeCollection()

    def collection(self, name):
        assert name == "agent_capability_issues"
        return self.collection_obj


@pytest.mark.asyncio
async def test_capability_issue_repository_records_issue():
    mongo = FakeMongo()
    repo = AgentCapabilityIssueMongoRepository(mongo)

    await repo.insert({"issue_id": "issue-1", "agent_id": "agent-1"})

    assert mongo.collection_obj.inserted == [{"issue_id": "issue-1", "agent_id": "agent-1"}]


@pytest.mark.asyncio
async def test_capability_issue_repository_lists_excluded_agent_ids():
    mongo = FakeMongo()
    repo = AgentCapabilityIssueMongoRepository(mongo)

    result = await repo.list_excluded_agent_ids(threshold=2)

    assert result == {"agent-1", "agent-2"}
    assert mongo.collection_obj.pipeline == [
        {"$match": {"status": "open"}},
        {"$group": {"_id": "$agent_id", "count": {"$sum": 1}}},
        {"$match": {"count": {"$gte": 2}}},
    ]
```

- [ ] **Step 2: Implement `AgentCapabilityIssueMongoRepository`**

Create `agent/repository/capability_issue_mongo.py`:

```python
from __future__ import annotations

from common.protocols import MongoDAL


class AgentCapabilityIssueMongoRepository:
    def __init__(self, mongo: MongoDAL, collection_name: str = "agent_capability_issues") -> None:
        self._issues = mongo.collection(collection_name)

    async def insert(self, issue: dict) -> str:
        await self._issues.insert_one(dict(issue))
        return str(issue["issue_id"])

    async def list_excluded_agent_ids(self, *, threshold: int) -> set[str]:
        docs = await self._issues.aggregate(
            [
                {"$match": {"status": "open"}},
                {"$group": {"_id": "$agent_id", "count": {"$sum": 1}}},
                {"$match": {"count": {"$gte": threshold}}},
            ]
        )
        return {str(doc["_id"]) for doc in docs if doc.get("_id")}

    async def get_by_id(self, issue_id: str) -> dict | None:
        return await self._issues.find_one({"issue_id": issue_id})

    async def list_for_agent(self, agent_id: str, *, status: str | None, limit: int, offset: int) -> list[dict]:
        query: dict = {"agent_id": agent_id}
        if status is not None:
            query["status"] = status
        return await self._issues.find(query, sort=[("created_at", -1)], skip=offset, limit=limit)

    async def resolve(self, issue_id: str, provider_id: str, resolved_at) -> dict | None:
        return await self._issues.find_one_and_update(
            {"issue_id": issue_id, "status": "open"},
            {
                "$set": {
                    "status": "resolved",
                    "resolved_at": resolved_at,
                    "resolved_by": provider_id,
                }
            },
            return_document=True,
        )

    async def resolve_all_for_agent(self, agent_id: str, provider_id: str, resolved_at) -> int:
        return await self._issues.update_many(
            {"agent_id": agent_id, "status": "open"},
            {
                "$set": {
                    "status": "resolved",
                    "resolved_at": resolved_at,
                    "resolved_by": provider_id,
                }
            },
        )
```

- [ ] **Step 3: Move capability issue service logic into `agent/capability_issue.py`**

Create `agent/capability_issue.py` with the cache and model conversion currently in `app_shell/agent_capability_issue_service.py`. The constructor must accept:

```python
class AgentCapabilityIssueService:
    def __init__(
        self,
        *,
        repository,
        threshold: int,
        id_factory,
        now,
        cache_ttl_seconds: float = 60.0,
    ) -> None:
        ...
```

The service must not import `database.mongodb`.

It must implement the complete route-facing capability issue surface currently
used by `api_gateway/routes/agent_routes.py`:

```python
class AgentCapabilityIssueService:
    async def record_issue(
        self,
        agent_id: str,
        error_message: str,
        query_text: str,
        room_id: str | None = None,
        message_id: str | None = None,
    ) -> AgentCapabilityIssue: ...
    async def get_excluded_agent_ids(self) -> frozenset[str]: ...
    async def get_issue_by_id(self, issue_id: str) -> AgentCapabilityIssue | None: ...
    async def get_issues_for_agent(
        self,
        agent_id: str,
        status: IssueStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AgentCapabilityIssue]: ...
    async def resolve_issue(
        self,
        issue_id: str,
        provider_id: str,
    ) -> AgentCapabilityIssue | None: ...
    async def resolve_all_for_agent(
        self,
        agent_id: str,
        provider_id: str,
    ) -> int: ...
```

- [ ] **Step 4: Convert `app_shell/agent_capability_issue_service.py` into a binding adapter**

Keep the public singleton name, but make it fail fast before binding:

```python
class AgentCapabilityIssueServiceAdapter:
    def __init__(self) -> None:
        self._delegate = None

    def bind(self, delegate) -> None:
        self._delegate = delegate

    def _require_delegate(self):
        if self._delegate is None:
            raise RuntimeError("AgentCapabilityIssueService has not been bound")
        return self._delegate

    async def record_issue(self, *args, **kwargs):
        return await self._require_delegate().record_issue(*args, **kwargs)

    async def get_excluded_agent_ids(self):
        return await self._require_delegate().get_excluded_agent_ids()

    async def get_issue_by_id(self, *args, **kwargs):
        return await self._require_delegate().get_issue_by_id(*args, **kwargs)

    async def get_issues_for_agent(self, *args, **kwargs):
        return await self._require_delegate().get_issues_for_agent(*args, **kwargs)

    async def resolve_issue(self, *args, **kwargs):
        return await self._require_delegate().resolve_issue(*args, **kwargs)

    async def resolve_all_for_agent(self, *args, **kwargs):
        return await self._require_delegate().resolve_all_for_agent(*args, **kwargs)
```

- [ ] **Step 5: Move domain alias DB check into AgentRepository**

Create `agent/domain_alias.py`:

```python
class DomainAliasService:
    def __init__(self, *, agent_repository, base_domain: str = "hybro.ai", protocol: str = "https") -> None:
        self._agent_repository = agent_repository
        self.BASE_DOMAIN = base_domain
        self.PROTOCOL = protocol
```

Replace `_is_subdomain_available()` with:

```python
    async def _is_subdomain_available(self, subdomain: str) -> bool:
        if subdomain in self.BLOCKED_SUBDOMAINS:
            return False
        return not await self._agent_repository.public_url_exists(subdomain, self.BASE_DOMAIN)
```

- [ ] **Step 6: Make `app_shell/domain_alias_service.py` a compatibility import**

Replace its direct DB use with:

```python
from agent.domain_alias import DomainAliasService

domain_alias_service = None


def bind_domain_alias_service(service: DomainAliasService) -> None:
    global domain_alias_service
    domain_alias_service = service


def get_domain_alias_service() -> DomainAliasService:
    if domain_alias_service is None:
        raise RuntimeError("DomainAliasService has not been bound")
    return domain_alias_service
```

- [ ] **Step 7: Refactor `app_shell/agent_health_service.py` off `database.mongodb`**

Inject a narrow dependency object with:

```python
class AgentHealthRepositoryPort(Protocol):
    async def get_by_id(self, agent_id: str) -> dict | None: ...
    async def list_visible(self, *, active_only: bool = False, limit: int = 0, **kwargs) -> list[dict]: ...
    async def update(self, agent_id: str, updates: dict) -> dict | None: ...
```

The service should call this port instead of `mongodb.agents_collection` and `mongodb.get_agent_by_agent_id`.

- [ ] **Step 8: Bind agent-owned services in `container.py` and `main.py`**

In `container.py`, add `AgentCapabilityIssueMongoRepository` construction inside `create_agent_deps` or return an extended app startup bundle. In `main.py`, bind:

```python
capability_issue_service.bind(agent_capability_issue_service)
bind_domain_alias_service(domain_alias_service_impl)
```

- [ ] **Step 9: Move ViewSet and avatar writes behind Agent-owned ports**

In `main.py`, remove startup imports of `database.mongodb.get_db` and
`database.repository.Repository` for ViewSet binding. Add an Agent-owned
ViewSet repository provider built from `AgentRepository`/`MongoDAL` and bind
that provider instead of `AppShellViewSetRepositoryProvider(db_provider=get_db,
create_repository=Repository)`.

Move `AppShellAgentAvatarManager` out of `main.py` into an Agent-owned adapter
or service that receives:

```python
class AgentAvatarWriter(Protocol):
    async def update_agent_avatar_url(self, agent_id: str, icon_url: str) -> bool: ...


class AgentVectorWriter(Protocol):
    async def upsert_agent_description(self, agent_id: str, description: str) -> None: ...
    async def delete_agent(self, agent_id: str) -> None: ...
```

Implement the writer in the Agent repository over `MongoDAL`; do not write via
`mongodb.agents_collection` or pass `mongodb` into the avatar manager. Move the
ViewSet vector side effect out of `api_gateway/viewsets/agent.py` by routing
create/update/delete mutations through an Agent-owned mutation service that uses
`AgentVectorWriter` backed by `VectorDAL`. Do not bind `database.pinecone_db` or
`app_shell.bound.VectorIndex` into the ViewSet from `main.py`.

Add a source gate:

```python
def test_agent_viewset_does_not_touch_vector_sdk_or_legacy_index():
    source = Path("api_gateway/viewsets/agent.py").read_text()
    assert "VectorIndex" not in source
    assert "pinecone" not in source.lower()
    assert "update_pinecone_index" not in source
```

- [ ] **Step 10: Move agent resolver and matcher off broad `db_service`**

In `app_shell/agent_resolver_service.py`, replace the singleton
`db_service` dependency with explicit Agent ports:

```python
class AgentResolutionRepository(Protocol):
    async def query_similar_agents(self, *args, **kwargs) -> list: ...
    async def get_agents_with_conditions_visible(self, *args, **kwargs) -> list: ...
```

Bind the implementation from `AgentFacade` or Agent repositories in
`container.py`; keep resolver method names and responses unchanged.

In `app_shell/agent_matcher.py`, remove the
`database_service`/`_legacy_database_service` constructor fallback. The matcher
must receive an Agent facade or selection port explicitly; tests should assert
that passing only the old database service is no longer supported.

- [ ] **Step 11: Remove these entries from `dal_database_convergence_manifest.json`**

Remove:

```json
"app_shell/agent_capability_issue_service.py",
"app_shell/agent_health_service.py",
"app_shell/domain_alias_service.py",
"app_shell/agent_resolver_service.py",
"app_shell/agent_matcher.py"
```

Remove each path from every section where it appears:
`database_singleton_import_blockers`, `database_service_type_blockers`, and
`database_service_duck_type_blockers`.

- [ ] **Step 12: Run focused tests**

Run:

```bash
uv run pytest tests/test_agent_capability_issue_repository.py tests/test_service_agent_resolver.py tests/test_agent_facade.py tests/test_dal_database_convergence_gate.py -q
```

Expected result: all tests pass, and the DAL convergence manifest has fewer blockers.

- [ ] **Step 13: Commit**

```bash
git add agent app_shell/agent_capability_issue_service.py app_shell/agent_health_service.py app_shell/domain_alias_service.py app_shell/agent_resolver_service.py app_shell/agent_matcher.py container.py main.py tests/test_agent_capability_issue_repository.py tests/test_service_agent_resolver.py tests/test_agent_facade.py tests/fixtures/dal_database_convergence_manifest.json
git commit -m "refactor(agent): move database access behind agent repositories"
```

---

## Task 4: Move room direct database access into Room repositories and Platform attachment lookup

**Files:**

- Create: `room/repository/quote_mongo.py`
- Create: `platform_module/attachments.py`
- Modify: `common/protocols/platform_protocols.py`
- Modify: `common/protocols/__init__.py`
- Modify: `room/repository/mongo.py`
- Modify: `room/facade.py`
- Modify: `app_shell/room_runtime.py`
- Modify: `app_shell/room_coordinator_service.py`
- Modify: `app_shell/room_membership_source.py`
- Modify: `app_shell/quote_service.py`
- Modify: `platform_module/deps.py`
- Modify: `platform_module/adapters/mongo.py`
- Modify: `container.py`
- Modify: `main.py`
- Modify: `tests/test_room_repository.py`
- Modify: `tests/test_platform_files.py`
- Modify: `tests/test_service_room.py`
- Modify: `tests/fixtures/dal_database_convergence_manifest.json`

- [ ] **Step 1: Add quote repository tests**

In `tests/test_room_repository.py`, add:

```python
@pytest.mark.asyncio
async def test_quote_repository_deletes_room_quotes():
    mongo = FakeMongo()
    repo = RoomQuoteMongoRepository(mongo)

    count = await repo.delete_for_room("room-1")

    assert count == 1
    assert mongo.collection("room_quotes").delete_many_calls == [{"room_id": "room-1"}]
```

- [ ] **Step 2: Implement `RoomQuoteMongoRepository`**

Create `room/repository/quote_mongo.py`:

```python
from __future__ import annotations

from common.protocols import MongoDAL


class RoomQuoteMongoRepository:
    def __init__(self, mongo: MongoDAL, collection_name: str = "room_quotes") -> None:
        self._quotes = mongo.collection(collection_name)

    async def insert(self, snippet: dict) -> str:
        await self._quotes.insert_one(dict(snippet))
        return str(snippet["quote_id"])

    async def get_by_id(self, quote_id: str) -> dict | None:
        return await self._quotes.find_one({"quote_id": quote_id})

    async def delete_by_id(self, quote_id: str) -> bool:
        return await self._quotes.delete_one({"quote_id": quote_id})

    async def delete_for_room(self, room_id: str) -> int:
        return await self._quotes.delete_many({"room_id": room_id})
```

- [ ] **Step 3: Extend `MessageMongoRepository` with trace lookup helpers**

Add methods to `room/repository/mongo.py`:

```python
    async def get_user_message_by_id(self, message_id: str) -> dict | None:
        return await self._user_messages.find_one({"message_id": message_id})

    async def get_agent_message_by_id(self, message_id: str) -> dict | None:
        return await self._agent_messages.find_one({"message_id": message_id})

    async def update_user_message(self, message_id: str, updates: dict) -> bool:
        return await self._user_messages.update_one({"message_id": message_id}, {"$set": dict(updates)})

    async def update_agent_message(self, message_id: str, updates: dict) -> bool:
        return await self._agent_messages.update_one({"message_id": message_id}, {"$set": dict(updates)})
```

- [ ] **Step 4: Add common attachment ports and Platform-owned adapters**

Add consumer-facing ports to `common/protocols/platform_protocols.py` and export
them from `common.protocols.__init__`:

```python
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AttachmentMetadataReader(Protocol):
    async def get_for_room_file(self, room_id: str, file_id: str) -> dict | None: ...


@runtime_checkable
class AttachmentCleanupPort(Protocol):
    async def delete_for_room(self, room_id: str) -> int: ...
```

Create `platform_module/attachments.py` with Platform-owned implementations:

```python
from __future__ import annotations

from common.protocols import AttachmentCleanupPort, AttachmentMetadataReader

class PlatformAttachmentMetadataReader:
    def __init__(self, file_metadata_repository) -> None:
        self._files = file_metadata_repository

    async def get_for_room_file(self, room_id: str, file_id: str) -> dict | None:
        doc = await self._files.get(file_id)
        if doc is None or doc.get("room_id") != room_id:
            return None
        return doc


class PlatformAttachmentCleanupPort:
    def __init__(self, file_metadata_repository) -> None:
        self._files = file_metadata_repository

    async def delete_for_room(self, room_id: str) -> int:
        return await self._files.delete_for_room(room_id)
```

Extend the existing Platform-owned `FileMetadataRepository` protocol in
`platform_module/deps.py`:

```python
class FileMetadataRepository(Protocol):
    async def create(self, data: dict) -> str: ...
    async def get(self, file_id: str) -> dict | None: ...
    async def delete(self, file_id: str) -> bool: ...
    async def list_for_room(self, room_id: str) -> list[dict]: ...
    async def delete_for_room(self, room_id: str) -> int: ...
```

Add `delete_for_room()` to `platform_module/adapters/mongo.py::MongoFileMetadataRepository`:

```python
    async def delete_for_room(self, room_id: str) -> int:
        return await self._collection.delete_many({"room_id": room_id})
```

- [ ] **Step 5: Bind quote cleanup and attachment reader into `RoomFacade`**

Add optional constructor dependencies to `room/facade.py`:

```python
quote_repository=None,
attachment_metadata_reader=None,
```

Expose a Room-owned cleanup method that does not write Platform data:

```python
    async def cleanup_room_owned_data(self, room_id: str) -> dict[str, int]:
        result = await self._message_repository.delete_for_room(room_id)
        if self._quote_repository is not None:
            result["quotes"] = await self._quote_repository.delete_for_room(room_id)
        return result
```

Create an application/composition-level cleanup helper in `container.py` or a
small app-shell orchestrator that coordinates:

```python
room_cleanup = await room_facade.cleanup_room_owned_data(room_id)
file_count = await platform_attachment_cleanup.delete_for_room(room_id)
```

Room may read Platform attachment metadata through `AttachmentMetadataReader`,
but Room must not own Platform file-metadata deletion.

- [ ] **Step 6: Replace direct `database.mongodb` imports in `app_shell/room_runtime.py`**

Replace direct calls to:

- `mongodb.file_uploads_collection.delete_many`
- `mongodb.delete_room_quotes_by_room_id`
- `mongodb.file_uploads_collection.find_one`
- `mongodb.get_room_user_message_by_message_id`
- `mongodb.get_room_agent_message_by_message_id`

with bound RoomFacade or repository-facing methods.

For attachment lookup, replace the current query:

```python
await mongodb.file_uploads_collection.find_one({"file_id": file_id, "room_id": room_id})
```

with a bound file metadata port:

```python
await attachment_metadata_reader.get_for_room_file(room_id, file_id)
```

Do not make Room import `platform_module`; inject the file metadata port from
`container.py` so Room only depends on a protocol-shaped capability.

- [ ] **Step 7: Split `RoomRuntime` broad database service into explicit ports**

Before removing `app_shell/room_runtime.py` from
`database_service_duck_type_blockers`, introduce a constructor dependency object
or dataclass such as:

```python
@dataclass(frozen=True)
class RoomRuntimeDeps:
    room_facade: RoomFacade
    agent_lookup: AgentLookupPort
    agent_group_reader: AgentGroupReaderPort
    execution_message_store: ExecutionMessagePort
    run_reader: RunReaderPort
    memory_reader: ContextMemoryReaderPort
    turn_context_loader: TurnContextLoader
    attachment_metadata_reader: AttachmentMetadataReader
```

Before editing runtime code, add a `ROOM_RUNTIME_METHOD_OWNER_MAP` test that
extracts or enumerates every current `self.database_service.*` call in
`app_shell/room_runtime.py` and maps it to one of the dependencies above. The
test must fail when a method is missing from the map. Include current calls for
active runs, agent/group reads, active agent lists, quote deletion, memory
reads, user/agent message writes, and turn context loading.

Replace `self.database_service = db_service` with explicit fields from this
dependency object. Every former `self.database_service.*` call must be assigned
to one owner:

- Room-owned room and user-message commands go through `room_facade` or Room
  repositories.
- Agent and group reads go through Agent ports.
- run, task, cancellation, and agent-message persistence go through Execution
  ports.
- room memory and turn context access goes through Context Memory ports.
- file metadata goes through the Platform-owned `AttachmentMetadataReader`.

Add a source gate:

```python
def test_room_runtime_does_not_store_database_service():
    source = Path("app_shell/room_runtime.py").read_text()
    assert "from app_shell.database_service" not in source
    assert "self.database_service" not in source
    assert "db_service" not in source
```

- [ ] **Step 8: Bind repositories in `container.py`**

Change `container.py::create_room_deps` to accept an optional injected
attachment reader instead of importing Platform from Room:

```python
def create_room_deps(
    *,
    mongo: MongoDAL,
    agent_registry: AgentRegistry,
    membership_source: RoomMembershipSeedSource,
    quote_repository: RoomQuoteMongoRepository | None = None,
    attachment_metadata_reader: AttachmentMetadataReader | None = None,
) -> RoomDeps:
    repository = RoomMongoRepository(mongo=mongo)
    message_repository = MessageMongoRepository(mongo=mongo)
    facade = RoomFacade(
        repository=repository,
        message_repository=message_repository,
        agent_registry=agent_registry,
        membership_source=membership_source,
        quote_repository=quote_repository,
        attachment_metadata_reader=attachment_metadata_reader,
        id_factory=lambda: uuid4().hex,
        now=utcnow,
    )
    ...
```

In `main.py`, after Platform deps are constructed, create:

```python
quote_repository = RoomQuoteMongoRepository(mongo=mongo)
attachment_metadata_reader = PlatformAttachmentMetadataReader(
    platform_deps.file_metadata_repository
)
```

Pass both `quote_repository` and `attachment_metadata_reader` into
`create_room_deps(...)`; `create_room_deps()` is responsible for passing them
through to the internal `RoomFacade` it constructs.

- [ ] **Step 9: Move quote source validation behind Room/Execution ports**

In `app_shell/quote_service.py`, remove the `TYPE_CHECKING` import of
`DatabaseService` and replace function parameters named `db` with an explicit
protocol:

```python
class QuoteSourceReader(Protocol):
    async def get_room_user_message_by_message_id(self, message_id: str): ...
    async def get_room_agent_message_by_message_id(self, message_id: str): ...


class QuoteWriter(Protocol):
    async def insert_quoted_snippet(self, snippet: QuotedSnippet) -> str: ...
```

`validate_quote_source()` receives `QuoteSourceReader`; `create_quoted_snippet()`
receives both `QuoteSourceReader` and `QuoteWriter`, or a composed
`QuoteRepository`. The concrete writer is `RoomQuoteMongoRepository` or
`RoomFacade`, not `DatabaseService`.

- [ ] **Step 10: Move room companion services off broad `db_service`**

In `app_shell/room_coordinator_service.py`, replace `self.database_service`
with explicit Room, Agent, and Execution ports for:

- room lookup and room updates
- user-message lookup/update
- agent-message lookup/write
- agent and group lookups used for room orchestration

In `app_shell/room_membership_source.py`, replace the singleton import with a
Room membership seed port or Agent group reader injected from `container.py`.
Do not import `app_shell.database_service` in either file.

- [ ] **Step 11: Remove room entries from the DB convergence manifest**

Update `tests/fixtures/dal_database_convergence_manifest.json` by removing:

```json
"app_shell/room_runtime.py",
"app_shell/room_coordinator_service.py",
"app_shell/room_membership_source.py",
"app_shell/quote_service.py"
```

Remove each path from every section where it appears:
`database_singleton_import_blockers`, `database_service_type_blockers`, and
`database_service_duck_type_blockers`.

- [ ] **Step 12: Run room tests and gates**

Run:

```bash
uv run pytest tests/test_room_repository.py tests/test_room_facade.py tests/test_service_room.py tests/test_dal_database_convergence_gate.py -q
```

Expected result: room tests and convergence gates pass.

- [ ] **Step 13: Commit**

```bash
git add room app_shell/room_runtime.py app_shell/room_coordinator_service.py app_shell/room_membership_source.py app_shell/quote_service.py container.py main.py tests/test_room_repository.py tests/test_room_facade.py tests/test_service_room.py tests/fixtures/dal_database_convergence_manifest.json
git commit -m "refactor(room): move room database helpers into repositories"
```

---

## Task 5: Move execution run, task, and HITL persistence into Execution repositories

**Files:**

- Create: `execution/repository/__init__.py`
- Create: `execution/repository/mongo.py`
- Modify: `common/protocols/repository_protocols.py`
- Modify: `common/protocols/execution_protocols.py`
- Modify: `execution/run_command_handler.py`
- Modify: `execution/run_lifecycle.py`
- Modify: `execution/run_queries.py`
- Modify: `execution/hitl/service.py`
- Modify: `execution/state/task_state_manager.py`
- Modify: `execution/dispatch/task_notifications.py`
- Modify: `jobs/stale_task_checker.py`
- Modify: `container.py`
- Modify: `main.py`
- Create: `tests/test_execution_repository.py`
- Modify: `tests/test_run_lifecycle_service.py`
- Modify: `tests/test_heal_head_from_events.py`
- Modify: `tests/test_stale_task_checker_run_lifecycle.py`
- Modify: `tests/fixtures/dal_database_convergence_manifest.json`

- [ ] **Step 0: Add the execution persistence method mapping before writing repositories**

Add this table to the top of `tests/test_execution_repository.py` as an
executable test constant and mirror it in the repository class names. The
implementation tasks below must not start until every method has a target owner:

```python
EXECUTION_PERSISTENCE_METHOD_MAP = {
    # Run lifecycle and run watchdog.
    "get_active_runs_by_room_id": "RunMongoRepository.get_active_for_room",
    "get_room_ids_with_non_terminal_runs": "RunMongoRepository.get_room_ids_with_non_terminal_runs",
    "find_stale_non_terminal_runs": "RunMongoRepository.find_stale_non_terminal_runs",
    "claim_user_message_for_processing": "UserMessageClaimMongoRepository.claim_for_processing",
    "unclaim_user_message": "UserMessageClaimMongoRepository.unclaim",
    "claim_or_reclaim_user_message": "UserMessageClaimMongoRepository.claim_or_reclaim",
    "refresh_processing_claim": "UserMessageClaimMongoRepository.refresh_claim",
    # A2A task persistence on room_agent_messages.
    "enable_task_tracking_on_message": "TaskMessageMongoRepository.enable_task_tracking_on_message",
    "update_task_on_message": "TaskMessageMongoRepository.update_task_on_message",
    "update_task_state_on_message": "TaskMessageMongoRepository.update_task_state_on_message",
    "update_webhook_token_hash_on_message": "TaskMessageMongoRepository.update_webhook_token_hash_on_message",
    "accumulate_artifact_on_message": "TaskMessageMongoRepository.accumulate_artifact_on_message",
    "touch_task_message": "TaskMessageMongoRepository.touch",
    "get_stale_task_messages": "TaskMessageMongoRepository.get_stale_tracked",
    "get_expired_task_messages": "TaskMessageMongoRepository.get_expired_tracked",
    "get_non_tracked_stale_task_messages": "TaskMessageMongoRepository.get_stale_untracked",
    "get_orphaned_agent_messages": "TaskMessageMongoRepository.get_orphaned",
    "get_stuck_supervisor_trajectory_messages": "UserMessageSupervisorRecoveryMongoRepository.get_stuck_supervisor_trajectory_messages",
    "claim_stuck_supervisor_trajectory": "UserMessageSupervisorRecoveryMongoRepository.claim_stuck_supervisor_trajectory",
    "get_pending_task_messages_for_user": "TaskMessageMongoRepository.get_pending_for_user",
    "get_task_messages_for_room": "TaskMessageMongoRepository.get_for_room",
    "count_non_terminal_tasks_for_user": "TaskMessageMongoRepository.count_non_terminal_for_user",
    "count_non_terminal_tasks_for_room": "TaskMessageMongoRepository.count_non_terminal_for_room",
    # HITL persistence and continuation.
    "create_hitl_request": "HITLMongoRepository.create_hitl_request",
    "get_hitl_request": "HITLMongoRepository.get_hitl_request",
    "claim_hitl_request": "HITLMongoRepository.claim_hitl_request",
    "update_hitl_request": "HITLMongoRepository.update_hitl_request",
    "cas_update_hitl_request": "HITLMongoRepository.cas_update_hitl_request",
    "fenced_update_hitl_request": "HITLMongoRepository.fenced_update_hitl_request",
    "get_pending_hitl_requests": "HITLMongoRepository.get_pending_for_room",
    "get_pending_hitl_requests_for_message": "HITLMongoRepository.get_pending_for_message",
    "get_hitl_group_requests": "HITLMongoRepository.get_group_requests",
    "count_pending_in_hitl_group": "HITLMongoRepository.count_pending_in_group",
    "claim_hitl_group_routing": "HITLMongoRepository.claim_group_routing",
    "release_hitl_group_routing": "HITLMongoRepository.release_group_routing",
    "count_hitl_requests_for_message": "HITLMongoRepository.count_for_message",
    "iter_stale_processing_hitl_requests": "HITLMongoRepository.iter_stale_processing",
    "update_agent_message_task_state": "TaskMessageMongoRepository.update_agent_message_task_state",
    "persist_hitl_user_answer": "TaskMessageMongoRepository.persist_hitl_user_answer",
    "persist_hitl_group_metadata": "TaskMessageMongoRepository.persist_hitl_group_metadata",
    "save_continuation_on_message": "TaskMessageMongoRepository.save_continuation",
    "get_and_clear_continuation_on_message": "TaskMessageMongoRepository.get_and_clear_continuation",
    "save_continuation_on_user_message": "UserMessageContinuationMongoRepository.save_continuation",
    "get_and_clear_continuation_on_user_message": "UserMessageContinuationMongoRepository.get_and_clear_continuation",
    "get_pending_continuation_on_message": "TaskMessageMongoRepository.get_pending_continuation",
    # Shared lookup helpers used by execution call sites.
    "get_room_agent_message_by_message_id": "TaskMessageMongoRepository.get_by_message_id",
    "get_room_user_message_by_message_id": "UserMessageClaimMongoRepository.get_by_message_id",
    "resolve_client_request_id_for_message_id": "ExecutionClientRequestMongoRepository.resolve_for_message_id",
    "reset_last_notified_state": "TaskMessageMongoRepository.reset_last_notified_state",
}
```

Add a test that fails when the map omits any method from
`execution.ports.HITLPersistencePort`:

```python
def test_execution_persistence_map_covers_hitl_port():
    port_methods = {
        name
        for name, value in HITLPersistencePort.__dict__.items()
        if callable(value) and not name.startswith("_")
    }
    assert port_methods.issubset(EXECUTION_PERSISTENCE_METHOD_MAP)


def test_execution_persistence_map_targets_exist():
    import execution.repository.mongo as repositories

    for target in EXECUTION_PERSISTENCE_METHOD_MAP.values():
        class_name, method_name = target.split(".", 1)
        assert hasattr(repositories, class_name)
        assert hasattr(getattr(repositories, class_name), method_name)


def test_execution_database_service_uses_are_mapped():
    # Keep this gate scoped to methods owned by Execution repositories. Mixed
    # owner files such as response_handler.py and stale_task_checker.py are
    # split into explicit owner maps in Task 7 so Room/Agent methods are not
    # forced into Execution persistence.
    source_paths = [
        Path("execution/dispatch/transports/direct.py"),
        Path("execution/hitl/service.py"),
    ]
    used = set()
    for path in source_paths:
        used.update(re.findall(r"(?:database_service|_db_service|self\\._db)\\.([A-Za-z_][A-Za-z0-9_]*)", path.read_text()))
    used.discard("mongo")  # Covered by the HITL source gate below, not a repository method.
    assert used.issubset(EXECUTION_PERSISTENCE_METHOD_MAP)
```

Add a service-level source gate for the HITL migration:

```python
def test_hitl_service_uses_persistence_port_not_database_internals():
    source = Path("execution/hitl/service.py").read_text()
    assert ".mongo.db.hitl_requests" not in source
    assert "self.database_service" not in source
    assert "self._db_service" not in source
    assert "getattr(self.database_service" not in source
    assert "getattr(self._db_service" not in source


def test_hitl_persistence_adapter_does_not_forward_with_getattr():
    source = Path("execution/hitl/adapters.py").read_text()
    assert "def __getattr__" not in source
    assert "getattr(self._database_service" not in source
```

When converting `HITLService`, rename the persistence dependency away from the
legacy service-locator name. Use `self._persistence` or `self.persistence`, not
`self.database_service` or `self._db_service`.

When converting `recover_stale_processing()`, replace the direct cursor:

```python
cursor = self.database_service.mongo.db.hitl_requests.find(...)
```

with:

```python
async for doc in self._persistence.iter_stale_processing_hitl_requests(cutoff):
    ...
```

- [ ] **Step 1: Add execution repository tests**

Create `tests/test_execution_repository.py` with:

```python
import pytest

from execution.repository.mongo import RunMongoRepository, RunEventMongoRepository


# Define local FakeMongo/FakeCollection helpers in this test file, following the
# pattern in tests/test_room_repository.py. Do not assume a global fake_mongo
# fixture exists.


@pytest.mark.asyncio
async def test_run_repository_gets_non_terminal_room_ids():
    mongo = FakeMongo()
    repo = RunMongoRepository(mongo)

    await repo.get_room_ids_with_non_terminal_runs()

    assert mongo.collection("runs").distinct_args == (
        "room_id",
        {"state": {"$in": ["queued", "running", "awaiting_input"]}},
    )


@pytest.mark.asyncio
async def test_run_event_repository_appends_event():
    mongo = FakeMongo()
    repo = RunEventMongoRepository(mongo)

    event_id = await repo.append("run-1", {"event_id": "evt-1", "run_id": "run-1"})

    assert event_id == "evt-1"
    assert mongo.collection("run_events").inserted[0]["run_id"] == "run-1"
```

- [ ] **Step 2: Implement run repositories**

Create `execution/repository/mongo.py` with:

```python
from __future__ import annotations

from models.run import NON_TERMINAL_RUN_STATE_VALUES


class RunMongoRepository:
    def __init__(self, mongo, collection_name: str = "runs") -> None:
        self._runs = mongo.collection(collection_name)

    async def create(self, run: dict) -> str:
        await self._runs.insert_one(dict(run))
        return str(run["run_id"])

    async def get_by_id(self, run_id: str) -> dict | None:
        return await self._runs.find_one({"run_id": run_id})

    async def get_active_for_room(self, room_id: str) -> list[dict]:
        return await self._runs.find(
            {
                "room_id": room_id,
                "state": {"$in": list(NON_TERMINAL_RUN_STATE_VALUES)},
            },
            sort=[("updated_at", -1)],
        )

    async def get_for_room(self, room_id: str) -> list[dict]:
        return await self._runs.find(
            {"room_id": room_id},
            sort=[("updated_at", -1)],
        )

    async def update_state(self, run_id: str, state: str, **fields) -> bool:
        return await self._runs.update_one({"run_id": run_id}, {"$set": {"state": state, **fields}})

    async def update(self, run_id: str, update: dict) -> bool:
        return await self._runs.update_one({"run_id": run_id}, update)

    async def get_diverged(self, limit: int) -> list[dict]:
        return await self._runs.find(
            {"state": {"$in": list(NON_TERMINAL_RUN_STATE_VALUES)}},
            limit=limit,
        )

    async def get_room_ids_with_non_terminal_runs(self) -> list[str]:
        return await self._runs.distinct(
            "room_id",
            {"state": {"$in": list(NON_TERMINAL_RUN_STATE_VALUES)}},
        )


class RunEventMongoRepository:
    def __init__(self, mongo, collection_name: str = "run_events") -> None:
        self._events = mongo.collection(collection_name)

    async def append(self, run_id: str, event: dict) -> str:
        payload = dict(event)
        payload["run_id"] = run_id
        await self._events.insert_one(payload)
        return str(payload["event_id"])

    async def get_for_run(self, run_id: str) -> list[dict]:
        return await self._events.find({"run_id": run_id}, sort=[("seq", 1)])

    async def get_latest(self, run_id: str) -> dict | None:
        rows = await self._events.find({"run_id": run_id}, sort=[("seq", -1)], limit=1)
        return rows[0] if rows else None
```

- [ ] **Step 3: Convert `RunCommandHandler` to repository injection**

In `execution/run_command_handler.py`:

- Remove the module-level `mongodb = None`.
- Replace `runs_collection` and `run_events_collection` constructor parameters with `run_repository` and `run_event_repository`.
- Replace direct collection calls with repository methods.
- Keep method names and return payloads unchanged.

The constructor must be:

```python
class RunCommandHandler:
    def __init__(self, *, run_repository, run_event_repository) -> None:
        self._runs = run_repository
        self._run_events = run_event_repository
```

- [ ] **Step 4: Convert `RunLifecycleAdapter` and `RunQueryAdapter`**

`RunLifecycleAdapter` should accept `run_repository` instead of `runs_collection`.

`RunQueryAdapter` should accept `run_repository` and call `get_by_id()` / `get_for_room()`.

- [ ] **Step 5: Add task tracking and HITL repositories**

In `execution/repository/mongo.py`, add the repository classes below and then
continue expanding them until every target method in
`EXECUTION_PERSISTENCE_METHOD_MAP` is implemented. The shown methods are the
minimum constructor and smoke-test surface; do not wire these repositories into
runtime until the map coverage test passes.

```python
class TaskMessageMongoRepository:
    def __init__(self, mongo, collection_name: str = "room_agent_messages") -> None:
        self._messages = mongo.collection(collection_name)

    async def get_by_message_id(self, message_id: str) -> dict | None:
        return await self._messages.find_one({"message_id": message_id})

    async def update_task_on_message(
        self,
        message_id: str,
        task_data: dict,
        message_text: str | None = None,
    ) -> bool:
        from common.a2a_constants import TERMINAL_STATES

        terminal_values = [state.value for state in TERMINAL_STATES]
        set_fields = {
            "message_content.message_task": dict(task_data),
            "task_updated_at": utcnow(),
        }
        if message_text is not None:
            set_fields["message_content.message_text"] = message_text
        return await self._messages.update_one(
            {
                "message_id": message_id,
                "message_content.message_task.status.state": {
                    "$nin": terminal_values,
                },
            },
            {"$set": set_fields},
        )

    async def update_task_state_on_message(self, message_id: str, state: str, **fields) -> bool:
        from common.a2a_constants import TERMINAL_STATES

        terminal_values = [terminal.value for terminal in TERMINAL_STATES]
        payload = {
            "message_content.message_task.status.state": state,
            "task_updated_at": utcnow(),
            **_task_state_fields(fields),
        }
        return await self._messages.update_one(
            {
                "message_id": message_id,
                "message_content.message_task.status.state": {
                    "$nin": terminal_values,
                },
            },
            {"$set": payload},
        )

    async def get_pending_for_user(self, user_id: str, states: list[str]) -> list[dict]:
        return await self._messages.find({"user_id": user_id, "task_state": {"$in": states}})


class HITLMongoRepository:
    def __init__(self, mongo, collection_name: str = "hitl_requests") -> None:
        self._hitl = mongo.collection(collection_name)

    async def create_hitl_request(self, request: dict) -> str:
        await self._hitl.insert_one(dict(request))
        return str(request["request_id"])

    async def get_hitl_request(self, request_id: str) -> dict | None:
        return await self._hitl.find_one({"request_id": request_id})

    async def get_pending_for_room(self, room_id: str) -> list[dict]:
        return await self._hitl.find({"room_id": room_id, "status": "pending"})

    async def update_hitl_request(self, request_id: str, response: dict) -> bool:
        return await self._hitl.update_one({"request_id": request_id}, {"$set": dict(response)})


class ExecutionCancellationMongoRepository:
    def __init__(
        self,
        mongo,
        collection_name: str = "cancelled_messages",
        agent_messages_collection_name: str = "room_agent_messages",
    ) -> None:
        self._cancelled = mongo.collection(collection_name)
        self._agent_messages = mongo.collection(agent_messages_collection_name)

    async def is_message_cancelled(self, message_id: str) -> bool:
        return await self._cancelled.find_one({"message_id": message_id}) is not None
```

Then move the existing `cancel_descendants` query/update logic from
`DatabaseService` into `ExecutionCancellationMongoRepository.cancel_descendants`
and add repository tests for descendant selection and idempotent cancellation.

Before completing this step, add concrete fake-collection tests for:

- `HITLMongoRepository.claim_hitl_request`: query must include `request_id`, the expected previous status, and no existing claim; update must set `status`, `claim_id`, `user_input`, `responded_at`, and `responded_by_user_id`.
- `HITLMongoRepository.fenced_update_hitl_request`: query must include both `request_id` and `claim_id`.
- `TaskMessageMongoRepository.get_stale_tracked`: query must select non-terminal tracked tasks older than the cutoff.
- `UserMessageSupervisorRecoveryMongoRepository.claim_stuck_supervisor_trajectory`: query must use `room_user_messages`, atomically fence by the current stuck trajectory state, and write recovery claim fields.
- `TaskMessageMongoRepository.update_task_on_message`: update must write
  `message_content.message_task`, optionally write
  `message_content.message_text`, update `task_updated_at`, and include the
  terminal-state guard on `message_content.message_task.status.state`.
- `RunMongoRepository.get_active_for_room`: query must filter by room id and
  `NON_TERMINAL_RUN_STATE_VALUES`.

This step depends on Task 2 adding `MongoCollection.distinct()` and
`MongoCollectionAdapter.distinct()`. Confirm the DAL protocol and adapter tests
pass before implementing `RunMongoRepository.get_room_ids_with_non_terminal_runs()`.

- [ ] **Step 6: Bind execution repositories in `container.py`**

Create repositories in a helper:

```python
def create_execution_repositories(*, mongo: MongoDAL):
    return {
        "run_repository": RunMongoRepository(mongo),
        "run_event_repository": RunEventMongoRepository(mongo),
        "task_message_repository": TaskMessageMongoRepository(mongo),
        "hitl_repository": HITLMongoRepository(mongo),
        "cancellation_store": ExecutionCancellationMongoRepository(mongo),
        "supervisor_recovery_repository": UserMessageSupervisorRecoveryMongoRepository(mongo),
    }
```

- [ ] **Step 7: Replace `mongodb.runs_collection` and `_db_svc` execution startup wiring in `main.py`**

Construct:

```python
execution_repositories = create_execution_repositories(mongo=mongo_dal)
run_command_handler = RunCommandHandler(
    run_repository=execution_repositories["run_repository"],
    run_event_repository=execution_repositories["run_event_repository"],
)
run_lifecycle = RunLifecycleAdapter(
    command_handler=run_command_handler,
    run_repository=execution_repositories["run_repository"],
)
```

- [ ] **Step 8: Remove execution entries from the DB convergence manifest**

Update `tests/fixtures/dal_database_convergence_manifest.json` by removing:

```json
"execution/run_command_handler.py",
"execution/hitl/adapters.py",
"execution/hitl/service.py"
```

Remove `execution/run_command_handler.py` from
`hidden_mongo_fallback_blockers`, and remove the other paths from
`database_service_duck_type_blockers` once the gates prove those broad
dependencies are gone. Keep `jobs/stale_task_checker.py` in
`database_service_duck_type_blockers` and `hidden_mongo_fallback_blockers` until
Task 7 introduces the non-execution Agent, cancellation, and Room cleanup ports
it also needs.

- [ ] **Step 9: Run execution tests**

Run:

```bash
uv run pytest tests/test_execution_repository.py tests/test_run_lifecycle_service.py tests/test_heal_head_from_events.py tests/test_stale_task_checker_run_lifecycle.py tests/test_dal_database_convergence_gate.py -q
```

Expected result: execution persistence tests and DAL convergence gates pass.

- [ ] **Step 10: Commit**

```bash
git add execution common/protocols container.py main.py tests/test_execution_repository.py tests/test_run_lifecycle_service.py tests/test_heal_head_from_events.py tests/test_stale_task_checker_run_lifecycle.py tests/fixtures/dal_database_convergence_manifest.json
git commit -m "refactor(execution): move run and task persistence to repositories"
```

---

## Task 6: Move A2A runtime database access behind execution and agent ports

**Files:**

- Modify: `app_shell/a2a_runtime.py`
- Modify: `execution/ports.py`
- Modify: `execution/repository/mongo.py`
- Modify: `agent/facade.py`
- Modify: `main.py`
- Modify: `tests/test_a2a_service_webhook_fallback.py`
- Modify: `tests/test_api_webhooks.py`
- Modify: `tests/test_transport_parity.py`
- Modify: `tests/fixtures/dal_database_convergence_manifest.json`

- [ ] **Step 1: Add narrow A2A persistence protocol**

In `execution/ports.py`, add:

```python
class A2ATaskPersistencePort(Protocol):
    async def check_task_limits(
        self,
        user_id: str,
        room_id: str,
        *,
        non_terminal_states: list[str] | None = None,
    ) -> None: ...
    async def enable_task_tracking_on_message(
        self,
        message_id: str,
        *,
        webhook_token_hash: str,
        agent_url: str,
        task_created_at,
        task_updated_at,
        task_data: dict,
    ) -> bool: ...
    async def update_task_on_message(
        self,
        message_id: str,
        task_data: dict,
        message_text: str | None = None,
    ) -> bool: ...
    async def update_webhook_token_hash_on_message(self, message_id: str, token_hash: str) -> bool: ...
    async def verify_webhook_token_for_task(self, message_id: str, token: str) -> tuple[bool, str | None]: ...
    async def get_room_agent_message_by_message_id(self, message_id: str): ...


class A2AAgentLookupPort(Protocol):
    async def get_agent_by_agent_id(self, agent_id: str): ...


class WebhookAuthPort(Protocol):
    async def verify_webhook_token_for_task(self, message_id: str, token: str) -> tuple[bool, str | None]: ...


class WebhookMessageReader(Protocol):
    async def get_room_agent_message_by_message_id(self, message_id: str): ...


class WebhookCancellationReader(Protocol):
    async def is_message_cancelled(self, message_id: str) -> bool: ...
```

Add a token helper under Execution ownership, for example
`execution/webhook_tokens.py`, instead of keeping token generation on a database
service or app-shell helper:

```python
class WebhookTokenService:
    def __init__(self, signing_key: bytes) -> None:
        self._signing_key = signing_key

    def generate_webhook_token(self) -> str:
        return secrets.token_urlsafe(32)

    def hash_webhook_token(self, token: str) -> str:
        return hmac.new(
            self._signing_key,
            token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def verify_webhook_token(self, token: str, stored_hash: str) -> bool:
        expected = self.hash_webhook_token(token)
        return hmac.compare_digest(expected, stored_hash)
```

Replace existing `db_service.generate_webhook_token()` and
`db_service.hash_webhook_token(...)` calls with this token service, and persist
only the resulting hash through `A2ATaskPersistencePort`.

Also update `execution/dispatch/transports/webhook.py` so `WebhookTransport`
receives webhook ingress ports instead of a broad `DatabaseService`.
The existing call:

```python
is_valid, error_reason = await self._db.verify_webhook_token_for_task(message_id, token)
```

must become:

```python
is_valid, error_reason = await self._webhook_auth.verify_webhook_token_for_task(
    message_id,
    token,
)
```

Message lookup and cancellation checks must also move off `_db`:

```python
msg = await self._webhook_messages.get_room_agent_message_by_message_id(message_id)
is_cancelled = await self._webhook_cancellation.is_message_cancelled(message_id)
if not is_cancelled and msg and getattr(msg, "related_message_id", None):
    is_cancelled = await self._webhook_cancellation.is_message_cancelled(
        msg.related_message_id
    )
if is_cancelled:
    ...
```

Add a `tests/test_api_webhooks.py` regression test that a webhook is discarded
when either the task message or its related user message is cancelled.

Add a source gate:

```python
def test_webhook_transport_does_not_import_database_service():
    source = Path("execution/dispatch/transports/webhook.py").read_text()
    assert "app_shell.database_service" not in source
    assert "DatabaseService" not in source
    assert "self._db" not in source
    assert "self._db.verify_webhook_token_for_task" not in source
```

- [ ] **Step 2: Refactor `A2AService` constructor and binding**

In `app_shell/a2a_runtime.py`, remove:

```python
from database.mongodb import mongodb
```

Add:

```python
    def bind_persistence(
        self,
        persistence: A2ATaskPersistencePort,
        agent_call_counter,
        agent_lookup: A2AAgentLookupPort,
    ) -> None:
        self._persistence = persistence
        self._agent_call_counter = agent_call_counter
        self._agent_lookup = agent_lookup

    def _require_persistence(self) -> A2ATaskPersistencePort:
        if self._persistence is None:
            raise RuntimeError("A2A task persistence has not been bound")
        return self._persistence
```

Replace `db_service.*` task calls and `mongodb.increment_agent_call_count(...)`
with the bound ports. In `reply_to_task()`, replace
`db_service.get_agent_by_agent_id(...)` with the bound `A2AAgentLookupPort`.
For task limits, change the current positional call to keyword form:

```python
await self._persistence.check_task_limits(
    user_id,
    room_id,
    non_terminal_states=non_terminal_state_values,
)
```

- [ ] **Step 3: Create the concrete A2A persistence adapter**

In `execution/repository/mongo.py` or `execution/a2a_persistence.py`, create an
adapter that composes existing Execution repositories:

```python
class ExecutionA2ATaskPersistenceAdapter:
    def __init__(
        self,
        *,
        task_messages: TaskMessageMongoRepository,
        run_repository: RunMongoRepository,
        webhook_token_service: WebhookTokenService,
        max_tasks_per_user: int,
        max_tasks_per_room: int,
    ) -> None:
        self._task_messages = task_messages
        self._runs = run_repository
        self._webhook_tokens = webhook_token_service
        self._max_tasks_per_user = max_tasks_per_user
        self._max_tasks_per_room = max_tasks_per_room
```

It must implement every method in `A2ATaskPersistencePort`, including
`check_task_limits()` by preserving the current limit logic from
`DatabaseService.check_task_limits()` over non-terminal task/run counts. Add
tests that verify user and room limits, webhook token verification, and nested
task update semantics. Add a regression test for
`enable_task_tracking_on_message()` that asserts the update document sets
`has_task_tracking`, `webhook_token_hash`, `agent_url`, `task_created_at`,
`task_updated_at`, and `message_content.message_task`, and returns success based
on matched update semantics matching the legacy Mongo method. Do not implement
this method with `MongoCollection.update_one()` alone, because the current DAL
adapter returns success from modified/upserted counts and would report a
matched-but-unchanged idempotent retry as failure. Implement it with
`MongoCollection.find_one_and_update(..., return_document=ReturnDocument.AFTER)`
and return `doc is not None`, or first add a DAL primitive that exposes
`matched_count` explicitly.

Expose a container helper:

```python
def create_a2a_task_persistence(*, execution_repositories, settings) -> A2ATaskPersistencePort:
    webhook_token_service = WebhookTokenService(
        signing_key=settings.webhook_signing_key.encode("utf-8")
    )
    return ExecutionA2ATaskPersistenceAdapter(
        task_messages=execution_repositories["task_message_repository"],
        run_repository=execution_repositories["run_repository"],
        webhook_token_service=webhook_token_service,
        max_tasks_per_user=settings.max_tasks_per_user,
        max_tasks_per_room=settings.max_tasks_per_room,
    )
```

- [ ] **Step 4: Bind A2A runtime in `main.py`**

After agent and execution deps are created:

```python
execution_task_persistence = create_a2a_task_persistence(
    execution_repositories=execution_repositories,
    settings=settings,
)
a2a_service.bind_persistence(
    persistence=execution_task_persistence,
    agent_call_counter=_agent_deps.agent_call_counter,
    agent_lookup=_agent_deps.agent_registry,
)
```

- [ ] **Step 5: Wire `WebhookTransport` with explicit ports**

Change `execution/dispatch/transports/webhook.py::WebhookTransport` from:

```python
def __init__(self, ..., db: DatabaseService, ...):
    self._db = db
```

to:

```python
def __init__(
    self,
    *,
    webhook_auth: WebhookAuthPort,
    webhook_messages: WebhookMessageReader,
    webhook_cancellation: WebhookCancellationReader,
    ...
) -> None:
    self._webhook_auth = webhook_auth
    self._webhook_messages = webhook_messages
    self._webhook_cancellation = webhook_cancellation
```

In `main.py`, replace:

```python
WebhookTransport(db=_db_svc, ...)
```

with:

```python
WebhookTransport(
    webhook_auth=execution_task_persistence,
    webhook_messages=execution_repositories["task_message_repository"],
    webhook_cancellation=execution_repositories["cancellation_store"],
    ...
)
```

- [ ] **Step 6: Remove A2A and webhook blockers**

Update `tests/fixtures/dal_database_convergence_manifest.json` by removing:

```json
"app_shell/a2a_runtime.py",
"execution/dispatch/transports/webhook.py"
```

Remove `app_shell/a2a_runtime.py` from `database_singleton_import_blockers` and
`database_service_type_blockers`. Remove
`execution/dispatch/transports/webhook.py` from
`database_service_type_blockers` after the source gate proves the transport no
longer imports or stores `DatabaseService`.

- [ ] **Step 7: Run A2A focused tests**

Run:

```bash
uv run pytest tests/test_a2a_service_webhook_fallback.py tests/test_api_webhooks.py tests/test_transport_parity.py tests/test_dal_database_convergence_gate.py -q
```

Expected result: A2A tests and convergence gates pass.

- [ ] **Step 8: Commit**

```bash
git add app_shell/a2a_runtime.py execution/ports.py execution/repository/mongo.py agent/facade.py main.py tests/test_a2a_service_webhook_fallback.py tests/test_api_webhooks.py tests/test_transport_parity.py tests/fixtures/dal_database_convergence_manifest.json
git commit -m "refactor(a2a): inject task persistence ports"
```

---

## Task 7: Replace `app_shell.database_service` service locator with bound module facades

**Files:**

- Modify: `app_shell/database_service.py`
- Modify: `api_gateway/dependencies.py`
- Modify: `api_gateway/routes/a2a_task_routes.py`
- Modify: `api_gateway/routes/agent_group_routes.py`
- Modify: `api_gateway/routes/room_routes.py`
- Modify: `api_gateway/routes/sse_routes.py`
- Modify: `app_shell/debate_service.py`
- Modify: `common/utils/turn_id.py`
- Modify: `execution/orchestration/queue_executor.py`
- Modify: `execution/orchestration/factory.py`
- Modify: `execution/orchestration/room_message_center.py`
- Modify: `execution/orchestration/supervisor_executor.py`
- Modify: `execution/orchestration/room_supervisor_service.py`
- Modify: `execution/hitl/factory.py`
- Modify: `hub_runtime_bridge/adapters/legacy_failure.py`
- Modify: `execution/client_request_id.py`
- Modify: `execution/cancellation.py`
- Modify: `execution/dispatch/agent_dispatcher.py`
- Modify: `execution/dispatch/agent_message_processor.py`
- Modify: `execution/dispatch/response_handler.py`
- Modify: `execution/dispatch/task_notifications.py`
- Modify: `execution/dispatch/transports/direct.py`
- Modify: `execution/dispatch/transports/relay.py`
- Modify: `execution/dispatch/transports/webhook.py`
- Modify: `jobs/stale_task_checker.py`
- Modify: `tests/test_service_database.py`
- Modify: `tests/fixtures/dal_database_convergence_manifest.json`

- [ ] **Step 1: Add fail-fast compatibility tests**

In `tests/test_service_database.py`, add:

```python
import pytest

from app_shell.database_service import db_service


@pytest.mark.asyncio
async def test_database_service_compatibility_fails_fast_when_unbound(monkeypatch):
    monkeypatch.setattr(db_service, "_room_store", None, raising=False)

    with pytest.raises(RuntimeError, match="room_store"):
        await db_service.get_room_by_room_id("room-1")
```

- [ ] **Step 2: Replace concrete `DatabaseService` with a compatibility adapter**

In `app_shell/database_service.py`, remove imports of:

- `database.mongodb`
- `database.pinecone_db`
- `a2a.types`

Keep only route protocol definitions that still need stable import paths. Implement:

```python
class DatabaseService:
    def __init__(self) -> None:
        self._agent_store = None
        self._room_store = None
        self._execution_store = None
        self._memory_store = None
        self._platform_store = None

    def bind_dependencies(
        self,
        *,
        agent_store=None,
        room_store=None,
        execution_store=None,
        memory_store=None,
        platform_store=None,
    ) -> None:
        self._agent_store = agent_store
        self._room_store = room_store
        self._execution_store = execution_store
        self._memory_store = memory_store
        self._platform_store = platform_store

    def _require(self, dependency, name: str):
        if dependency is None:
            raise RuntimeError(f"DatabaseService compatibility dependency is not bound: {name}")
        return dependency

    async def get_room_by_room_id(self, room_id: str):
        return await self._require(self._room_store, "room_store").get_room_by_room_id(room_id)

    async def get_room_agent_message_by_message_id(self, message_id: str):
        return await self._require(self._execution_store, "execution_store").get_room_agent_message_by_message_id(message_id)
```

- [ ] **Step 3: Create the delegate in `main.py` from module facades/repositories**

Create explicit compatibility adapters in `container.py`; do not use
`__getattr__`, `getattr(delegate, name)`, or a single object that forwards
arbitrary calls. Until all duck-typed consumers are migrated, the temporary
compatibility surface must be derived from both
`database_service_type_blockers` and `database_service_duck_type_blockers`,
grouped by owner:

- Agent adapter: `get_agent_by_agent_id`, `get_agent_name_by_agent_id`,
  `get_agents_with_conditions`, `get_agent_group_by_id`,
  `get_agent_groups_by_owner`, `add_agent_group`, `update_agent_group`,
  `delete_agent_group`.
- Room adapter: `get_room_by_room_id`, `get_room_user_message_by_message_id`,
  `get_room_user_messages_by_room_id`, `add_room_user_message`,
  `update_room_user_message_by_message_id`, room ownership reads used by routes.
- Execution adapter: every method listed in
  `EXECUTION_PERSISTENCE_METHOD_MAP`, including task tracking, continuation,
  HITL, stale recovery, and client request id resolution.
- Context-memory adapter: `add_room_memory`, `get_room_memory_by_room_id`,
  `get_room_memory_by_memory_id`, `update_room_memory_by_room_id`,
  `update_room_memory_by_memory_id`, `delete_room_memory_by_memory_id`,
  `push_and_trim_conversation_turn`, `update_turn_notes`,
  `compact_turns_bulk`, `get_room_summary_projection`.
- Platform adapter: API key methods only until Task 8 migrates those routes.

Do not add new Mongo/Pinecone logic to these compatibility adapters.

Add a gate in `tests/test_service_database.py`:

```python
def test_database_service_compatibility_does_not_use_getattr_forwarding():
    source = Path("app_shell/database_service.py").read_text()
    assert "def __getattr__" not in source
    assert "getattr(self._require" not in source
```

- [ ] **Step 4: Migrate type imports out of route modules**

Change route modules from:

```python
from app_shell.database_service import A2ATaskReader
```

to protocol imports from `common.protocols.execution_protocols` or route-local protocols.

Change:

```python
from app_shell.database_service import AgentGroupStore
```

to an agent/platform protocol owned outside app-shell.

Also rename route globals and dependency binder parameters away from broad
`db_service` names. For example:

```python
a2a_task_store: A2ATaskReader | None = None
agent_group_store: AgentGroupStore | None = None
room_task_reader: A2ATaskReader | None = None
sse_task_reader: A2ATaskReader | None = None
```

Update `api_gateway/dependencies.py` so dependency patching no longer lists
`("db_service",)` or any broad service-locator attribute names. Route modules
should bind owner-specific ports with explicit names and getters.

- [ ] **Step 5: Replace RoomMessageCenter service-locator injection**

In `execution/orchestration/room_message_center.py`, replace the constructor
parameter and attribute named `database_service` with explicit ports grouped by
owner:

```python
@dataclass(frozen=True)
class RoomMessageCenterDeps:
    room_store: RoomMessageStore
    agent_store: AgentLookupPort
    execution_store: ExecutionMessageStore
    cancellation_store: CancellationStore
    context_memory: ContextMemoryReader
```

Remove any dynamic forwarding from `BoundRoomMessageCenterProxy`; expose
explicit proxy methods used by routes/tests, or bind the runtime directly at the
composition root.

Add a source gate:

```python
def test_room_message_center_does_not_accept_database_service():
    source = Path("execution/orchestration/room_message_center.py").read_text()
    assert "database_service" not in source
    assert "db_service" not in source
    assert "def __getattr__" not in source
```

- [ ] **Step 6: Replace factory-level database-service remapping**

In `execution/orchestration/factory.py`, replace:

```python
"database_service": _defaults.db_service,
```

with the explicit `RoomMessageCenterDeps` object built from module ports.

In `execution/hitl/factory.py`, remove the `dependency_attrs` aliases for
`database_service` and `db_service`; callers must pass `persistence`,
`continuation`, `delivery`, and notification ports by their real names. Add a
test that constructing the service with `database_service=` raises `TypeError`
or is rejected before runtime binding.

- [ ] **Step 7: Move debate service off `db_service`**

In `app_shell/debate_service.py`, replace the singleton `db_service` dependency
with:

```python
class DebateMessageStore(Protocol):
    async def get_room_agent_message_by_message_id(self, message_id: str): ...
    async def update_room_agent_message_with_new_message_content_by_message_id(
        self, message_id: str, content: MessageContent
    ) -> bool: ...


class DebateAgentLookup(Protocol):
    async def get_agent_name_by_agent_id(self, agent_id: str) -> str | None: ...
```

Bind these from Execution and Agent ports in `container.py`; do not import
`app_shell.database_service`.

- [ ] **Step 8: Replace direct execution adapters that store broad DB services**

In `execution/client_request_id.py`, replace `SSEClientRequestIdResolver(db_service)`
with:

```python
class ClientRequestIdStore(Protocol):
    async def resolve_client_request_id_for_message_id(self, message_id: str) -> str | None: ...
```

In `execution/cancellation.py`, replace
`AgentTaskCleanupAdapter(db_service=...)` with explicit ports:

```python
class RelatedAgentMessageReader(Protocol):
    async def get_room_agent_messages_by_related_message_id(self, message_id: str) -> list: ...


class TaskCancellationWriter(Protocol):
    async def update_task_state_on_message(self, message_id: str, state: str, **fields) -> bool: ...
```

In `execution/dispatch/transports/direct.py`, replace the
`database_service` constructor argument and `self.database_service` attribute
with named ports for message lookup, task updates, artifact accumulation, and
room memory reads. Add a source gate:

```python
def test_direct_transport_does_not_store_database_service():
    source = Path("execution/dispatch/transports/direct.py").read_text()
    assert "database_service" not in source
    assert "self.database_service" not in source
```

- [ ] **Step 9: Replace shared utility and Hub legacy adapters that accept DB service shapes**

In `common/utils/turn_id.py`, rename `resolve_turn_id(msg, db_service)` to
receive a protocol:

```python
class TurnMessageReader(Protocol):
    async def get_room_user_message_by_message_id(self, message_id: str): ...
    async def get_room_agent_message_by_message_id(self, message_id: str): ...
```

The function parameter should be named `message_reader`, not `db_service`.

In `hub_runtime_bridge/adapters/legacy_failure.py`, replace
`database_service`/`self._db` with explicit ports:

```python
class HubFailureMessageStore(Protocol):
    async def get_room_agent_message_by_message_id(self, message_id: str): ...
    async def update_room_agent_message_by_message_id(self, message_id: str, message) -> bool: ...
```

Add source gates for both files rejecting `database_service`, `db_service`, and
`self._db`.

- [ ] **Step 10: Replace `AgentResponseHandler` broad DB dependency**

In `execution/dispatch/response_handler.py`, replace `_DatabaseServiceLike` and
`self._db` with explicit protocols:

```python
class ResponseMessageWriter(Protocol):
    async def update_room_agent_message_by_message_id(self, message_id: str, updates) -> bool: ...
    async def upsert_room_agent_message(self, message) -> bool: ...
    async def get_room_agent_message_by_message_id(self, message_id: str): ...


class ResponseTaskWriter(Protocol):
    async def update_task_on_message(
        self,
        message_id: str,
        task_data: dict,
        message_text: str | None = None,
    ) -> bool: ...
    async def update_task_state_on_message(self, message_id: str, state: str, **fields) -> bool: ...
    async def accumulate_artifact_on_message(self, message_id: str, artifact: dict) -> bool: ...


class ResponseContinuationStore(Protocol):
    async def save_continuation_on_message(self, message_id: str, continuation: dict) -> bool: ...
    async def get_pending_continuation_on_message(self, message_id: str): ...


class ResponseClientRequestResolver(Protocol):
    async def resolve_client_request_id_for_message_id(self, message_id: str) -> str | None: ...
    async def resolve_client_request_id_for_agent_message(self, message) -> str | None: ...


class ResponseRoomReader(Protocol):
    async def get_room_by_room_id(self, room_id: str): ...


class ResponseHITLReader(Protocol):
    async def get_pending_hitl_requests_for_message(self, message_id: str) -> list[dict]: ...
```

Remove `getattr(self._db, ...)` fallback behavior. Add a source gate:

```python
def test_response_handler_does_not_use_database_service_like():
    source = Path("execution/dispatch/response_handler.py").read_text()
    assert "_DatabaseServiceLike" not in source
    assert "self._db" not in source
    assert "getattr(self._db" not in source
```

- [ ] **Step 11: Replace remaining execution constructors that accept `DatabaseService`**

For the execution files still present in `database_service_type_blockers` and
`database_service_duck_type_blockers`, replace the broad constructor dependency
with grouped execution ports:

Before editing constructors, add a method-use map to
`tests/test_service_database.py` (or a new `tests/test_execution_port_maps.py`)
that lists every current `self.database_service.*` call in these files and its
target owner. The test must fail if any current call is unmapped, similar to
`EXECUTION_PERSISTENCE_METHOD_MAP`.

- `execution/orchestration/queue_executor.py`: inject
  `QueueCancellationPort`, `QueueAgentLookupPort`, `QueueContinuationStore`,
  `QueueMessageStore`, `QueueRoomReader`, and `QueueTurnContextLoader` for the
  current `cancel_descendants`, agent lookup, continuation, message lookup, room
  lookup, and `load_turn_context(...)` calls.
- `execution/orchestration/supervisor_executor.py`: inject
  `SupervisorClientRequestResolver`, `SupervisorMessageStore`,
  `SupervisorContinuationStore`, and `SupervisorUserMessageStore` for client
  request id resolution, agent-message writes/deletes, user-message reads and
  updates, and continuation saves.
- `execution/orchestration/room_supervisor_service.py`: inject only the ports
  used by room supervision; remove lazy fallback imports of
  `app_shell.database_service.db_service`.
- `execution/dispatch/agent_dispatcher.py`: inject `DispatchMessageWriter`,
  `DispatchAgentLookup`, and `DispatchAgentGroupReader` for message updates,
  agent lookup, and group lookup.
- `execution/dispatch/agent_message_processor.py`: inject
  `ProcessorResponseHandlerPort` and `ProcessorMemoryReader`; do not pass
  `db=self.database_service` into response handling.
- Before cleaning `AgentMessageProcessor`, migrate
  `execution/dispatch/transports/relay.py` to the explicit
  `RelayTaskTracker`/`RelayAgentCallCounter` ports described in Task 11 Step 4,
  or move that step into this task. `AgentMessageProcessor.bind_relay_service()`
  must construct `RelayTransport` without `db=self.database_service`.
- `execution/dispatch/task_notifications.py`: replace direct imports of
  `DatabaseService`/`db_service` with `TaskNotificationStore`, containing only
  the message/task reads and writes used to emit notifications.

Add source gates for each file asserting no `app_shell.database_service`,
`DatabaseService`, `self.database_service`, `self._database_service`, or
`db_service` remains.

- [ ] **Step 12: Move stale task checker off broad database service**

In `jobs/stale_task_checker.py`, replace the broad `db_service` constructor
argument with an explicit dependency bundle that keeps ownership separated:

```python
@dataclass(frozen=True)
class StaleTaskCheckerDeps:
    runs: StaleTaskRunStore
    tasks: StaleTaskMessageStore
    continuations: StaleTaskContinuationStore
    agents: StaleTaskAgentLookup
    cancellation: StaleTaskCancellationStore
    rooms: StaleTaskRoomProcessingCleanup


class StaleTaskRunStore(Protocol):
    async def find_stale_non_terminal_runs(self, *args, **kwargs) -> list: ...
    async def get_room_ids_with_non_terminal_runs(self) -> list[str]: ...


class StaleTaskMessageStore(Protocol):
    async def get_stale_task_messages(self, *args, **kwargs) -> list: ...
    async def get_expired_task_messages(self, *args, **kwargs) -> list: ...
    async def get_non_tracked_stale_task_messages(self, *args, **kwargs) -> list: ...
    async def get_orphaned_agent_messages(self, *args, **kwargs) -> list: ...
    async def get_stuck_supervisor_trajectory_messages(self, *args, **kwargs) -> list: ...
    async def claim_stuck_supervisor_trajectory(self, *args, **kwargs) -> bool: ...
    async def update_task_on_message(
        self,
        message_id: str,
        task_data: dict,
        message_text: str | None = None,
    ) -> bool: ...
    async def update_task_state_on_message(self, message_id: str, state: str, **fields) -> bool: ...
    async def touch_task_message(self, message_id: str, **fields) -> bool: ...


class StaleTaskContinuationStore(Protocol):
    async def get_and_clear_continuation_on_message(self, message_id: str): ...
    async def get_and_clear_continuation_on_user_message(self, message_id: str): ...
class StaleTaskAgentLookup(Protocol):
    async def get_agent_by_agent_id(self, agent_id: str): ...
class StaleTaskCancellationStore(Protocol):
    async def is_message_cancelled(self, message_id: str) -> bool: ...
    async def cancel_descendants(self, message_id: str) -> int: ...


class StaleTaskRoomProcessingCleanup(Protocol):
    async def clear_stale_processing_message_ids(self, *, exclude_room_ids: list[str]) -> int: ...
```

The Execution repositories own task queries and updates; Agent owns agent
lookup; cancellation ownership follows the Execution cancellation port; Room
owns cleanup of `rooms.processing_message_id`. Remove
`jobs/stale_task_checker.py` from both `database_service_duck_type_blockers` and
`hidden_mongo_fallback_blockers` only after this split removes the global
`mongodb` fallback and broad `db_service` dependency.

Implement `StaleTaskRoomProcessingCleanup` in the Room repository/facade in this
task, not in Task 12. It must return an integer modified count and hide Motor
`modified_count` semantics from the job. Add a test for the query:
`processing_message_id != None` and `room_id not in busy room ids`.

- [ ] **Step 13: Remove remaining database-service compatibility blockers**

Update `tests/fixtures/dal_database_convergence_manifest.json` so
`database_service_type_blockers` contains only files still importing
`app_shell.database_service`.

Also update `database_service_duck_type_blockers` after replacing constructor
arguments and stored attributes such as `database_service=`,
`self.database_service`, `self._database_service`, `self._db_service`, and
`db_service=` in the execution and job files touched by this task. The gate must
prove the section shrank; do not edit the manifest ahead of the code.

- [ ] **Step 14: Run compatibility and route tests**

Run:

```bash
uv run pytest tests/test_service_database.py tests/test_api_a2a_tasks.py tests/test_api_agent_group.py tests/test_api_room_center.py tests/test_api_sse.py tests/test_dal_database_convergence_gate.py -q
```

Expected result: routes keep behavior while the database service blocker list
shrinks, and `execution/orchestration/factory.py`,
`execution/hitl/factory.py`, `execution/orchestration/room_message_center.py`,
`app_shell/debate_service.py`, `execution/client_request_id.py`,
`execution/cancellation.py`, `execution/dispatch/response_handler.py`,
`execution/dispatch/transports/direct.py`, `api_gateway/dependencies.py`,
the migrated route modules, and `jobs/stale_task_checker.py` no longer appear in
`database_service_duck_type_blockers`.

- [ ] **Step 15: Commit**

```bash
git add app_shell/database_service.py app_shell/debate_service.py api_gateway execution jobs main.py container.py tests/test_service_database.py tests/fixtures/dal_database_convergence_manifest.json
git commit -m "refactor(app-shell): thin database service compatibility"
```

---

## Task 8: Move platform API keys and file metadata fully behind Platform

**Files:**

- Create: `platform_module/api_keys.py`
- Modify: `common/protocols/platform_protocols.py`
- Modify: `api_gateway/routes/discovery_api_key_routes.py`
- Modify: `common/api_key_auth.py`
- Modify: `app_shell/api_key_auth.py`
- Modify: `container.py`
- Modify: `main.py`
- Modify: `tests/test_api_discovery_api_keys.py`
- Modify: `tests/test_common_api_key_auth.py`

- [ ] **Step 1: Add API key store repository**

First update `common/protocols/platform_protocols.py` deliberately with two
separate protocols:

```python
@runtime_checkable
class APIKeyStore(Protocol):
    async def get_api_keys_by_user(self, user_id: str) -> list[APIKeyRecord]: ...
    async def add_api_key(self, api_key: APIKeyRecord) -> str: ...
    async def get_api_key_by_id(self, key_id: str) -> APIKeyRecord | None: ...
    async def deactivate_api_key(self, key_id: str) -> bool: ...


@runtime_checkable
class APIKeyValidationStore(Protocol):
    async def get_api_key_by_hash(self, key_hash: str) -> APIKeyRecord | None: ...
    async def update_api_key_usage(self, key_hash: str) -> bool: ...
```

Export `APIKeyValidationStore` from `common.protocols.__init__` and update
`app_shell/api_key_auth.py` to import it from `common.protocols` instead of
declaring a local protocol.

Create `platform_module/api_keys.py`:

```python
from __future__ import annotations

from common.protocols import MongoDAL
from common.utils.time import utcnow
from models.api_key import APIKey


class MongoAPIKeyStore:
    def __init__(self, mongo: MongoDAL, collection_name: str = "api_keys") -> None:
        self._keys = mongo.collection(collection_name)

    async def get_api_key_by_hash(self, key_hash: str) -> APIKey | None:
        doc = await self._keys.find_one({"key_hash": key_hash})
        return APIKey.model_validate(doc) if doc else None

    async def get_api_key_by_id(self, key_id: str) -> APIKey | None:
        doc = await self._keys.find_one({"key_id": key_id})
        return APIKey.model_validate(doc) if doc else None

    async def get_api_keys_by_user(self, user_id: str) -> list[APIKey]:
        docs = await self._keys.find({"user_id": user_id})
        return [APIKey.model_validate(doc) for doc in docs]

    async def add_api_key(self, api_key: APIKey) -> str:
        await self._keys.insert_one(api_key.model_dump(mode="json"))
        return api_key.key_id

    async def deactivate_api_key(self, key_id: str) -> bool:
        return await self._keys.update_one({"key_id": key_id}, {"$set": {"is_active": False}})

    async def update_api_key_usage(self, key_hash: str) -> bool:
        return await self._keys.update_one(
            {"key_hash": key_hash},
            {"$inc": {"usage_count": 1}, "$set": {"last_used_at": utcnow()}},
        )
```

Add a regression test asserting `update_api_key_usage()` both increments
`usage_count` and sets `last_used_at`, matching the legacy
`database.mongodb.MongoDB` behavior.

- [ ] **Step 2: Bind API key store through Platform/container**

In `container.py`, expose:

```python
def create_api_key_store(*, mongo: MongoDAL) -> MongoAPIKeyStore:
    return MongoAPIKeyStore(mongo)
```

In `main.py`, replace:

```python
discovery_api_keys.bind_api_key_store(mongodb)
bind_api_key_authenticator(MongoAPIKeyAuthenticator(mongodb))
```

with:

```python
api_key_store = create_api_key_store(mongo=mongo_dal)
discovery_api_keys.bind_api_key_store(api_key_store)
bind_api_key_authenticator(MongoAPIKeyAuthenticator(api_key_store))
```

- [ ] **Step 3: Run API key tests**

Run:

```bash
uv run pytest tests/test_api_discovery_api_keys.py tests/test_common_api_key_auth.py tests/test_dal_database_convergence_gate.py -q
```

Expected result: API key tests pass and `main.py` has fewer direct `mongodb` usages.

- [ ] **Step 4: Commit**

```bash
git add platform_module/api_keys.py common/protocols/platform_protocols.py api_gateway/routes/discovery_api_key_routes.py common/api_key_auth.py app_shell/api_key_auth.py container.py main.py tests/test_api_discovery_api_keys.py tests/test_common_api_key_auth.py
git commit -m "refactor(platform): move api keys behind platform store"
```

---

## Task 9: Move context memory search and memory service off database singletons

**Files:**

- Create: `context_memory/search_adapter.py`
- Modify: `app_shell/compaction_service.py`
- Modify: `app_shell/memory_search_service.py`
- Modify: `app_shell/memory_service.py`
- Modify: `context_memory/facade.py`
- Modify: `context_memory/repository/mongo.py`
- Modify: `container.py`
- Modify: `main.py`
- Modify: `tests/test_context_memory_adapters.py`
- Modify: `tests/test_context_memory_bugfixes.py`
- Modify: `tests/test_dal_database_convergence_gate.py`
- Modify: `tests/fixtures/dal_database_convergence_manifest.json`

- [ ] **Step 1: Add boundary tests for context memory wrappers**

In `tests/test_context_memory_adapters.py`, add source gates:

```python
def test_memory_search_service_has_no_database_singleton_imports():
    source = Path("app_shell/memory_search_service.py").read_text()
    assert "database.mongodb" not in source
    assert "database.pinecone_db" not in source
    assert "from database" not in source


def test_memory_search_service_has_no_direct_pinecone_sdk_imports():
    source = Path("app_shell/memory_search_service.py").read_text()
    assert "import pinecone" not in source
    assert "from pinecone" not in source


def test_room_memory_service_does_not_use_database_service_locator():
    source = Path("app_shell/memory_service.py").read_text()
    assert "from app_shell.database_service" not in source
    assert "self.database_service" not in source
    assert "self.db_service" not in source


def test_compaction_service_does_not_use_database_service_locator():
    source = Path("app_shell/compaction_service.py").read_text()
    assert "from app_shell.database_service" not in source
    assert "self.db_service" not in source
```

- [ ] **Step 2: Add explicit context memory ports for side effects**

Create protocol-shaped dependencies for the remaining non-memory side effects:

```python
class UserInteractionTracker(Protocol):
    async def increment_user_interactions(self, user_id: str) -> bool: ...
    async def record_agent_call(
        self,
        agent_id: str,
        *,
        success: bool,
        response_time_ms: float = 0.0,
    ) -> bool: ...


class TurnNotesRepository(Protocol):
    async def update_turn_notes(self, room_id: str, turn_id: str, turn_notes: dict) -> bool: ...
```

Bind `TurnNotesRepository` to `MemoryMongoRepository.update_turn_notes`.
Bind user/agent counters through Agent-owned repositories or a narrow adapter in
`container.py`; do not reach back through `DatabaseService`. Add a regression
test preserving the legacy counter fields: every call increments `total_calls`
and `total_response_time_ms`, and successful calls increment
`successful_calls`.

- [ ] **Step 3: Convert `app_shell/memory_search_service.py` to a facade adapter**

Keep the public singleton `memory_search_service`, but remove direct
`database.mongodb`, `database.pinecone_db`, and direct `pinecone` SDK imports.
The adapter should be constructed with or bound to `ContextMemoryFacade` and
delegate:

```python
class MemorySearchService:
    def __init__(self) -> None:
        self._facade = None

    def bind_facade(self, facade: ContextMemoryFacade) -> None:
        self._facade = facade

    def _require_facade(self) -> ContextMemoryFacade:
        if self._facade is None:
            raise RuntimeError("MemorySearchService facade has not been bound")
        return self._facade

    async def search_memory(self, room_id: str, query: str, limit: int = 10):
        return await self._require_facade().search_memory(room_id, query, limit)
```

If existing callers require index-management methods, implement explicit
delegate methods on `ContextMemoryFacade` and `VectorDAL`; do not retain
Pinecone SDK access in app-shell.

- [ ] **Step 4: Convert `CompactionService` to ContextMemoryFacade ports**

In `app_shell/compaction_service.py`, remove `from app_shell.database_service
import db_service` and the `self.db_service` attribute. The service already has
`bind_facade()` and `bind_content_storage()`; make all room-memory reads,
compaction writes, content expansion, and indexing calls go through:

- `ContextMemoryFacade`
- `ContentStorageRepository`
- `MemorySearchService` facade adapter or `VectorDAL`

Do not import `database.mongodb`, `database.pinecone_db`, or
`app_shell.database_service` from this wrapper.

- [ ] **Step 5: Convert `RoomMemoryService` side effects to explicit dependencies**

In `app_shell/memory_service.py`, remove the import of `db_service` and replace
`self.database_service` with bound dependencies:

```python
def bind_runtime_dependencies(
    self,
    *,
    user_interaction_tracker: UserInteractionTracker,
    turn_notes_repository: TurnNotesRepository,
) -> None:
    self._user_interaction_tracker = user_interaction_tracker
    self._turn_notes_repository = turn_notes_repository
```

Existing calls to `facade.legacy_*` stay on `ContextMemoryFacade`; calls to
`increment_user_interactions`, `record_agent_call`, and `update_turn_notes` use
the explicit ports.

- [ ] **Step 6: Wire context memory dependencies from `container.py` and `main.py`**

In `container.py`, make the context-memory dependency bundle return:

- `ContextMemoryFacade`
- `MemoryMongoRepository`
- `ContentStorageMongoRepository`
- `VectorDAL`
- user interaction tracker

In `main.py`, bind:

```python
memory_search_service.bind_facade(context_memory_facade)
room_memory_service.bind_runtime_dependencies(
    user_interaction_tracker=context_memory_deps.user_interaction_tracker,
    turn_notes_repository=context_memory_deps.memory_repository,
)
compaction_service.bind_facade(context_memory_facade)
compaction_service.bind_content_storage(context_memory_deps.content_repository)
```

- [ ] **Step 7: Remove context memory blockers**

Update `tests/fixtures/dal_database_convergence_manifest.json` by removing:

```json
"app_shell/memory_search_service.py",
"app_shell/memory_service.py",
"app_shell/compaction_service.py"
```

from `database_singleton_import_blockers`,
`database_service_type_blockers`,
`pinecone_singleton_import_blockers`, and
`database_service_duck_type_blockers` as applicable.

- [ ] **Step 8: Run context memory tests**

Run:

```bash
uv run pytest tests/test_context_memory_adapters.py tests/test_context_memory_bugfixes.py tests/test_llm_app_shell_boundaries.py tests/test_dal_database_convergence_gate.py -q
```

Expected result: context memory behavior remains stable and the convergence
manifest shrinks.

- [ ] **Step 9: Commit**

```bash
git add context_memory app_shell/compaction_service.py app_shell/memory_search_service.py app_shell/memory_service.py container.py main.py tests/test_context_memory_adapters.py tests/test_context_memory_bugfixes.py tests/test_dal_database_convergence_gate.py tests/fixtures/dal_database_convergence_manifest.json
git commit -m "refactor(context-memory): remove database singleton access"
```

---

## Task 10: Convert Hub repository and stores to require MongoDAL only

**Files:**

- Modify: `hub_runtime_bridge/repository/mongo.py`
- Modify: `hub_runtime_bridge/hub_response_journal.py`
- Modify: `hub_runtime_bridge/task_ownership.py`
- Modify: `app_shell/relay_service.py`
- Modify: `main.py`
- Modify: `tests/test_hub_runtime_bridge_facade.py`
- Modify: `tests/test_hub_runtime_bridge_task_ownership.py`
- Modify: `tests/test_relay_service_hub_facade_adapter.py`

- [ ] **Step 1: Add a repository boundary test**

In `tests/test_hub_runtime_bridge_facade.py`, add:

```python
def test_hub_repository_does_not_branch_on_legacy_mongodb_service():
    sources = [
        Path("hub_runtime_bridge/repository/mongo.py").read_text(),
        Path("hub_runtime_bridge/hub_response_journal.py").read_text(),
        Path("hub_runtime_bridge/task_ownership.py").read_text(),
    ]
    forbidden = [
        "hasattr(self._mongo, \"get_hub\")",
        "hasattr(self._mongo, \"hubs_collection\")",
        "mongo.db.hub_response_journal",
        "mongo.db.hub_task_ownership",
        "database.mongodb",
    ]
    assert not [
        snippet
        for source in sources
        for snippet in forbidden
        if snippet in source
    ]
```

- [ ] **Step 2: Simplify `HubMongoRepository`**

In `hub_runtime_bridge/repository/mongo.py`, replace `_collection()` and legacy branches with:

```python
class HubMongoRepository:
    def __init__(self, mongo: MongoDAL, *, clock: Callable[[], datetime] = utcnow) -> None:
        self._hubs = mongo.collection("hubs")
        self._clock = clock
```

All methods should call `self._hubs` directly.

- [ ] **Step 3: Convert Hub journal and task ownership stores to MongoDAL**

In `hub_runtime_bridge/hub_response_journal.py` and
`hub_runtime_bridge/task_ownership.py`, require a `MongoDAL` constructor argument
and initialize collections through `mongo.collection(...)`; do not branch on
`mongo.db`.

For `MongoHubTaskOwnershipStore.claim_or_refresh()`, preserve atomic lease
semantics when the store is backed by `MongoCollectionAdapter`. Do not rely on
`getattr(result, "matched_count", 1)` after `update_one()`, because the DAL
adapter returns a bool and a failed lease update can otherwise default to
success. Either add a DAL primitive that exposes matched/modified counts, or
rewrite the claim update with
`find_one_and_update(..., return_document=ReturnDocument.AFTER)` and treat
`doc is None` as "ownership lease is held by another worker".

Add a regression test that uses the DAL adapter's bool-returning update
semantics, not only a Motor-like fake result object with `matched_count`, and
asserts failed lease takeover raises instead of returning a claimed ownership.

- [ ] **Step 4: Change Hub repository construction to receive `mongo_dal`**

In `main.py`, keep relay route compatibility unchanged for this task, but make
the Hub repository/facade construction use `mongo_dal` instead of
`database.mongodb.MongoDB`. Do not pass `database_compatibility_delegate` into
new Hub repository constructors.

The relay `database_service` constructor argument is removed in Task 11; this
task only removes legacy Mongo branching from `hub_runtime_bridge`.

- [ ] **Step 5: Run hub repository tests**

Run:

```bash
uv run pytest tests/test_hub_runtime_bridge_facade.py tests/test_hub_runtime_bridge_task_ownership.py tests/test_phase8_hub_runtime_bridge_gate.py -q
```

Expected result: tests pass.

- [ ] **Step 6: Commit**

```bash
git add hub_runtime_bridge/repository/mongo.py hub_runtime_bridge/hub_response_journal.py hub_runtime_bridge/task_ownership.py main.py tests/test_hub_runtime_bridge_facade.py tests/test_hub_runtime_bridge_task_ownership.py
git commit -m "refactor(hub): require dal mongo repository"
```

---

## Task 11: Replace relay `database_service` adapters with explicit ports

**Files:**

- Modify: `app_shell/relay_service.py`
- Modify: `execution/dispatch/transports/relay.py`
- Modify: `hub_runtime_bridge/deps.py`
- Modify: `hub_runtime_bridge/facade.py`
- Modify: `container.py`
- Modify: `main.py`
- Modify: `tests/test_relay_service_hub_facade_adapter.py`
- Modify: `tests/test_phase8_hub_runtime_bridge_gate.py`
- Modify: `tests/fixtures/dal_database_convergence_manifest.json`

- [ ] **Step 1: Add a relay boundary test that fails on broad database service injection**

In `tests/test_relay_service_hub_facade_adapter.py`, add:

```python
def test_relay_service_does_not_import_or_accept_database_service():
    source = Path("app_shell/relay_service.py").read_text()
    assert "app_shell.database_service" not in source
    assert "DatabaseService" not in source
    assert "database_service:" not in source
    assert "_RelayPublishAuthorizationReader(database_service)" not in source
    assert "_RelayCancellationReader(database_service)" not in source
```

- [ ] **Step 2: Replace relay database adapters with explicit protocols**

In `app_shell/relay_service.py`, replace `_RelayPublishAuthorizationReader` and
`_RelayCancellationReader` constructor dependencies with explicit ports:

```python
class RelayRoomOwnershipReader(Protocol):
    async def get_room_by_room_id(self, room_id: str): ...
    async def get_room_agent_message_by_message_id(self, message_id: str): ...


class RelayCancellationReader(Protocol):
    async def is_message_cancelled(self, message_id: str) -> bool: ...


class RelayAgentLookup(Protocol):
    async def get_agent_by_agent_id(self, agent_id: str): ...
```

The relay service constructor must receive these named dependencies directly:

```python
def __init__(
    self,
    *,
    mongo: MongoDAL,
    room_ownership_reader: RelayRoomOwnershipReader,
    cancellation_reader: RelayCancellationReader,
    agent_lookup: RelayAgentLookup,
    ...
) -> None:
```

- [ ] **Step 3: Wire relay ports from module facades/repositories**

In `container.py`, add a small factory that returns relay dependencies from
Room, Execution, and Agent ports. In `main.py`, replace:

```python
init_relay_service(mongo=mongodb, database_service=_db_svc, ...)
```

with:

```python
relay_ports = create_relay_ports(
    mongo=mongo_dal,
    room_deps=_room_deps,
    execution_repositories=execution_repositories,
    agent_deps=_agent_deps,
)
init_relay_service(**relay_ports, ...)
```

- [ ] **Step 4: Remove relay transport Mongo fallback and broad DB injection**

In `execution/dispatch/transports/relay.py`, remove the module-level
`mongodb = None`, the `db: DatabaseService` constructor argument, and the
`self._db` field. Inject explicit ports instead:

```python
class RelayTaskTracker(Protocol):
    async def enable_task_tracking_on_message(
        self,
        message_id: str,
        *,
        webhook_token_hash: str | None,
        agent_url: str | None,
        task_created_at,
        task_updated_at,
        task_data: dict,
    ) -> bool: ...


class RelayAgentCallCounter(Protocol):
    async def increment_agent_call_count(self, agent_id: str, *, success: bool) -> bool: ...
```

`RelayTransport.dispatch()` must use these ports directly. It must not fall back
to `mongodb` or a relay-service attribute for call counting.

Add a source gate:

```python
def test_relay_transport_does_not_use_mongodb_or_database_service():
    source = Path("execution/dispatch/transports/relay.py").read_text()
    assert "mongodb = None" not in source
    assert "app_shell.database_service" not in source
    assert "DatabaseService" not in source
    assert "self._db" not in source
    assert "**fields" not in source
```

- [ ] **Step 5: Remove relay database blockers**

Update `tests/fixtures/dal_database_convergence_manifest.json` by removing:

```json
"app_shell/relay_service.py",
"execution/dispatch/transports/relay.py"
```

from `database_singleton_import_blockers`, `hidden_mongo_fallback_blockers`,
`database_service_type_blockers`, and `database_service_duck_type_blockers` once
the gates prove each file has no broad database dependency.

- [ ] **Step 6: Run relay and hub tests**

Run:

```bash
uv run pytest tests/test_hub_runtime_bridge_facade.py tests/test_relay_service_hub_facade_adapter.py tests/test_phase8_hub_runtime_bridge_gate.py -q
```

Expected result: tests pass.

- [ ] **Step 7: Commit**

```bash
git add app_shell/relay_service.py execution/dispatch/transports/relay.py hub_runtime_bridge/deps.py hub_runtime_bridge/facade.py container.py main.py tests/test_relay_service_hub_facade_adapter.py tests/test_phase8_hub_runtime_bridge_gate.py tests/fixtures/dal_database_convergence_manifest.json
git commit -m "refactor(relay): replace database service adapters"
```

---

## Task 12: Make `main.py` startup use DAL as the production infrastructure owner

**Files:**

- Modify: `dal/index_registry.py`
- Modify: `common/protocols/dal_protocols.py`
- Modify: `container.py`
- Modify: `main.py`
- Modify: `tests/test_startup_index_registry.py`
- Modify: `tests/test_dal_database_convergence_gate.py`
- Modify: `tests/fixtures/dal_database_convergence_manifest.json`

- [ ] **Step 1: Change `create_mongo_dal` to own connection settings**

In `container.py`, change:

```python
def create_mongo_dal(*, database: Any) -> MongoDAL:
    from dal.mongo import MongoDALImpl

    return MongoDALImpl(database=database)
```

to:

```python
def create_mongo_dal() -> MongoDAL:
    from dal.mongo import MongoDALImpl

    return MongoDALImpl()
```

- [ ] **Step 2: Replace startup DB connection**

In `main.py`, replace:

```python
await mongodb.connect()
mongo_dal = create_mongo_dal(database=mongodb.db)
```

with:

```python
mongo_dal = create_mongo_dal()
await mongo_dal.connect()
```

Use `mongo_dal.collection("...")` only for module repositories and index
registration. Do not pass DAL collection adapters into jobs or helpers that
still expect Motor cursor/result semantics.

- [ ] **Step 3: Extend the existing DAL index registry**

Use the existing `dal/index_registry.py::IndexRegistryImpl` and
`common.protocols.IndexRegistry`; do not replace them with a second callable
registry API. Add small helper methods only if the existing collection/spec
registration shape is insufficient:

```python
registry = IndexRegistryImpl(mongo=mongo_dal)
registry.register("agent", "agents", [("agent_id", 1)], unique=True)
registry.register("room", "rooms", [("room_id", 1)], unique=True)
await registry.ensure_all()
```

Keep existing `tests/test_dal_unit.py` coverage for registration order and
aggregate error reporting. Add `tests/test_startup_index_registry.py` only for
startup-specific registration mapping.

- [ ] **Step 4: Register existing startup indexes by owner**

Move startup index creation from `database.mongodb` methods into module
repository/index helpers and register them in `container.py`:

- ContextMemory: replacement for `mongodb.create_context_memory_indexes()`.
- Platform rate limits / request logs: replacement for indexes on
  `mongodb.agent_requests_collection`.
- Agent: replacement for `mongodb.ensure_agent_indexes()` and
  `mongodb.create_capability_issue_indexes()`.
- Execution: replacement for `mongodb.create_run_lifecycle_indexes()`,
  `mongodb.create_task_tracking_indexes()`, and `db_service.ensure_hitl_indexes()`.
- Room: replacement for `mongodb.create_room_quotes_indexes()`.
- Cleanup/orphan uploads: create a job-specific repository/port that returns
  lists/counts with DAL adapter semantics. Do not pass `mongo_dal.collection()`
  directly to `OrphanedUploadCleaner`, because current code uses Motor cursor
  iteration and Motor result objects.
- Stale task cleanup: wire the Room-owned processing cleanup port created in
  Task 7; do not reintroduce raw rooms collections or Motor result semantics.

In `main.py`, the only startup call should be:

```python
await index_registry.ensure_all()
```

If an index is module-specific, register it in the module container helper before `ensure_all()`.

- [ ] **Step 5: Replace shutdown close**

In `main.py`, replace:

```python
await mongodb.close_database_connection()
```

with:

```python
await mongo_dal.close()
```

Store `mongo_dal` in `app.state.mongo_dal` so shutdown can close the same object.

- [ ] **Step 6: Remove `main.py` from singleton manifests**

Update `tests/fixtures/dal_database_convergence_manifest.json`:

- Remove `main.py` from `database_singleton_import_blockers`.
- Remove `main.py` from `pinecone_singleton_import_blockers`.

- [ ] **Step 7: Run startup boundary tests**

Run:

```bash
uv run pytest tests/test_startup_index_registry.py tests/test_dal_database_convergence_gate.py tests/test_phase9_cleanup_gate.py tests/test_multi_worker_safety.py -q
```

Expected result: startup boundary tests pass.

- [ ] **Step 8: Commit**

```bash
git add dal/index_registry.py common/protocols/dal_protocols.py container.py main.py tests/test_startup_index_registry.py tests/test_dal_database_convergence_gate.py tests/fixtures/dal_database_convergence_manifest.json
git commit -m "refactor(startup): make dal the database owner"
```

---

## Task 13: Empty convergence manifests and remove app-shell database compatibility from production paths

**Files:**

- Modify: `tests/fixtures/dal_database_convergence_manifest.json`
- Modify: `tests/fixtures/phase9_cleanup_manifest.json`
- Modify: `app_shell/database_service.py`
- Modify: `pyproject.toml`
- Modify: `tests/test_dal_database_convergence_gate.py`
- Modify: `tests/test_phase9_cleanup_gate.py`
- Modify: `tests/test_service_database.py`
- Modify: `tests/test_a2a_service_webhook_fallback.py`
- Modify: `tests/test_api_thin_adapters.py`

- [ ] **Step 1: Empty the DAL convergence manifest**

Change `tests/fixtures/dal_database_convergence_manifest.json` to:

```json
{
  "database_singleton_import_blockers": [],
  "hidden_mongo_fallback_blockers": [],
  "database_service_type_blockers": [],
  "database_service_duck_type_blockers": [],
  "pinecone_singleton_import_blockers": []
}
```

- [ ] **Step 2: Remove DAL blockers from phase9 cleanup manifest**

Remove all entries for deleted paths from
`tests/fixtures/phase9_cleanup_manifest.json`, regardless of contract. In
particular, remove every `app_shell/database_service.py` entry, including
`sdk_confinement`, because the file no longer exists. For other paths, remove
entries if their only reason was pending DAL injection:

- `app_shell/a2a_runtime.py`
- `app_shell/agent_capability_issue_service.py`
- `app_shell/agent_health_service.py`
- `app_shell/database_service.py`
- `app_shell/domain_alias_service.py`
- `app_shell/memory_search_service.py`
- `app_shell/room_runtime.py`

- [ ] **Step 3: Make `app_shell/database_service.py` non-production**

Remove `app_shell/database_service.py` once no runtime imports remain. Do not keep
a module-level `__getattr__` sentinel because that still allows dynamic imports
to look successful and conflicts with the convergence gate.

If an external test fixture still needs to assert the old import path is gone,
move the sentinel exception into the test itself:

```python
class DatabaseServiceRemoved(RuntimeError):
    pass
```

- [ ] **Step 4: Migrate tests that still import or patch broad DB compatibility**

Before deleting `app_shell/database_service.py`, run a test-source audit:

```bash
rg -n "app_shell\\.database_service|DatabaseService|\\bdb_service\\b|\\bdatabase_service\\b" tests
```

Update every matching test that depends on the production compatibility module
or broad service-locator naming. Known starting points include:

- `tests/test_service_database.py`: replace compatibility behavior tests with
  assertions that module-owned ports fail fast before binding.
- `tests/test_a2a_service_webhook_fallback.py`: patch the A2A persistence and
  webhook ports instead of `app_shell.database_service.db_service`.
- `tests/test_api_thin_adapters.py`: bind route-level owner-specific ports
  instead of importing `AgentGroupStore`, `A2ATaskReader`, or `db_service` from
  `app_shell.database_service`.
- task notification, dispatch middleware, supervisor, scope validation, API
  room center, and other suites discovered by the source audit.

Add a test-only legacy import assertion only if it proves the production import
path is gone; do not keep production compatibility code for tests.

- [ ] **Step 5: Remove obsolete package registrations if a file/package is deleted**

If `app_shell/database_service.py` is deleted and no remaining app-shell modules need packaging changes, leave `pyproject.toml` package list unchanged. If an entire compatibility package is removed, update `tool.setuptools.packages`.

- [ ] **Step 6: Run final boundary tests**

Run:

```bash
uv run pytest tests/test_dal_database_convergence_gate.py tests/test_phase9_cleanup_gate.py tests/test_api_gateway_module_boundaries.py tests/test_no_legacy_import_scanner.py tests/test_service_database.py tests/test_a2a_service_webhook_fallback.py tests/test_api_thin_adapters.py -q
```

Expected result: all boundary tests pass with empty DAL blocker lists.

- [ ] **Step 7: Commit**

```bash
git add app_shell/database_service.py pyproject.toml tests/fixtures/dal_database_convergence_manifest.json tests/fixtures/phase9_cleanup_manifest.json tests/test_dal_database_convergence_gate.py tests/test_phase9_cleanup_gate.py tests/test_service_database.py tests/test_a2a_service_webhook_fallback.py tests/test_api_thin_adapters.py
git commit -m "refactor(dal): remove database compatibility blockers"
```

---

## Task 14: Move S3 service usage behind ObjectStorageDAL/facade

**Files:**

- Modify: `common/protocols/dal_protocols.py`
- Modify: `dal/s3/client.py`
- Create: `platform_module/object_storage.py`
- Modify: `api/files.py`
- Modify: `app_shell/room_runtime.py`
- Modify: `main.py`
- Modify: `container.py`
- Modify: `tests/test_dal_unit.py`
- Modify: `tests/test_file_upload.py`
- Modify: `tests/test_message_retrieval.py`
- Modify: `tests/test_service_room.py`
- Modify: `tests/test_phase9_cleanup_gate.py`

- [ ] **Step 1: Extend the ObjectStorage boundary to cover current callers**

The current production callers need the legacy `S3Service` surface:

- `upload_file(file_data, room_id, user_id, filename, content_type)`
- `generate_presigned_url(s3_key, filename=None, expires_in=...)`
- `batch_presigned_urls(s3_keys, filenames=None, expires_in=...)`
- `delete_prefix(prefix)`
- `get_public_url(s3_key)`

Do not keep those operations in `app_shell.s3_service`. Either extend
`ObjectStorageDAL` with generic operations that preserve these semantics, or add
`platform_module/object_storage.py` as the platform-owned facade over
`ObjectStorageDAL`. Keep bucket/key naming and content-disposition filename
behavior unchanged.

- [ ] **Step 2: Move file upload and room attachment flows to the facade**

In `api/files.py`, `app_shell/room_runtime.py`, and `main.py`, inject the new
object-storage facade or `ObjectStorageDAL` wrapper from `container.py`. Remove
runtime imports of `app_shell.s3_service.s3_service` and direct calls to the old
singleton.

Room cleanup must call the new `delete_prefix` facade operation for
`uploads/{room_id}/` and `artifacts/{room_id}/`. Message retrieval must keep the
batch presigned URL behavior, including filename-aware content disposition.

- [ ] **Step 3: Add production object-storage import gates**

In `tests/test_phase9_cleanup_gate.py` or
`tests/test_dal_database_convergence_gate.py`, add a source gate that rejects
production imports of the app-shell S3 singleton and direct provider SDK usage
outside the DAL:

```python
def test_production_object_storage_access_goes_through_dal():
    production_roots = [
        Path("api"),
        Path("api_gateway"),
        Path("agent"),
        Path("room"),
        Path("context_memory"),
        Path("delivery"),
        Path("execution"),
        Path("hub_runtime_bridge"),
        Path("a2a_adapter"),
        Path("platform_module"),
        Path("llm_gateway"),
        Path("app_shell"),
        Path("jobs"),
        Path("common"),
        Path("container.py"),
        Path("main.py"),
    ]
    offenders = []
    for root in production_roots:
        files = [root] if root.is_file() else root.rglob("*.py")
        for path in files:
            rel = path.as_posix()
            if rel.startswith("dal/s3/"):
                continue
            source = path.read_text()
            if "app_shell.s3_service" in source or "import aioboto3" in source or "from aioboto3" in source:
                offenders.append(rel)
    assert offenders == []
```

- [ ] **Step 4: Update tests and remove legacy S3 singleton patches**

Update file upload, room runtime, message retrieval, and multimodal tests to
patch the new facade or injected object-storage protocol instead of
`app_shell.s3_service.s3_service`. Keep test coverage for upload errors,
filename-aware batch presigned URLs, and room prefix cleanup.

- [ ] **Step 5: Run object-storage tests and gates**

Run:

```bash
uv run pytest tests/test_dal_unit.py tests/test_file_upload.py tests/test_message_retrieval.py tests/test_service_room.py tests/test_phase9_cleanup_gate.py -q
```

Expected result: object-storage behavior and boundary gates pass.

- [ ] **Step 6: Commit**

```bash
git add common/protocols/dal_protocols.py dal/s3/client.py platform_module/object_storage.py api/files.py app_shell/room_runtime.py main.py container.py tests/test_dal_unit.py tests/test_file_upload.py tests/test_message_retrieval.py tests/test_service_room.py tests/test_phase9_cleanup_gate.py
git commit -m "refactor(storage): route s3 usage through object storage dal"
```

---

## Task 15: Documentation and full verification

**Files:**

- Modify: `System-Architecture.md`
- Modify: `docs/MODULAR_DECOUPLING_DESIGN.md`

- [ ] **Step 1: Update `System-Architecture.md`**

Update the `dal and database` section to state:

```markdown
`dal` owns production database, vector, object-storage, and Redis adapter access.
Business modules use module-scoped repositories built from `MongoDAL`,
`VectorDAL`, and `ObjectStorageDAL`. `database/mongodb.py` remains only for
operational migration scripts and legacy data migration helpers; it is not a
production module dependency.
```

- [ ] **Step 2: Update `docs/MODULAR_DECOUPLING_DESIGN.md`**

In Phase 1, replace target-only language with implemented status:

```markdown
**Implemented DAL convergence note (2026-06-05):** Production startup constructs
DAL adapters directly from the composition root. Agent, Room, Execution,
ContextMemory, Platform, HubRuntimeBridge, and Jobs use module-owned repositories
or protocols rather than `database.mongodb` or `app_shell.database_service`.
```

- [ ] **Step 3: Run focused verification**

Run:

```bash
uv run pytest \
  tests/test_dal_database_convergence_gate.py \
  tests/test_dal_protocols.py \
  tests/test_dal_unit.py \
  tests/test_agent_repository.py \
  tests/test_room_repository.py \
  tests/test_context_memory_repository.py \
  tests/test_execution_repository.py \
  tests/test_platform_module_protocols.py \
  tests/test_hub_runtime_bridge_facade.py \
  tests/test_phase9_cleanup_gate.py -q
```

Expected result: all selected tests pass.

- [ ] **Step 4: Run broader module tests**

Run:

```bash
uv run pytest tests/test_api_gateway.py tests/test_flow_contracts.py tests/test_multi_worker_safety.py tests/test_pipeline_integration.py -q
```

Expected result: API and integration contracts pass.

- [ ] **Step 5: Run lint**

Run:

```bash
uv run ruff check .
```

Expected result: Ruff exits with code 0.

- [ ] **Step 6: Run full test suite**

Run:

```bash
uv run pytest
```

Expected result: full suite exits with code 0.

- [ ] **Step 7: Commit**

```bash
git add System-Architecture.md docs/MODULAR_DECOUPLING_DESIGN.md
git commit -m "docs: record dal database convergence"
```

---

## Completion Criteria

The migration is complete when all of these are true:

- `uv run pytest tests/test_dal_database_convergence_gate.py -q` passes with empty blocker lists.
- `rg -n "from database.mongodb|import database.mongodb|from database.pinecone_db|import database.pinecone_db" api api_gateway agent room context_memory delivery execution hub_runtime_bridge a2a_adapter platform_module llm_gateway app_shell jobs common container.py main.py` returns no production matches.
- `rg -n "import pinecone|from pinecone" api api_gateway agent room context_memory delivery execution hub_runtime_bridge a2a_adapter platform_module llm_gateway app_shell jobs common container.py main.py --glob '!dal/pinecone/**'` returns no production matches.
- `rg -n "app_shell\\.s3_service|import aioboto3|from aioboto3" api api_gateway agent room context_memory delivery execution hub_runtime_bridge a2a_adapter platform_module llm_gateway app_shell jobs common container.py main.py --glob '!dal/s3/**'` returns no production matches.
- `rg -n "from app_shell.database_service|import app_shell.database_service|DatabaseService" api_gateway execution hub_runtime_bridge a2a_adapter jobs app_shell llm_gateway main.py container.py` returns no production dependency matches.
- `rg -n "\bdatabase_service\b|\bdb_service\b" api api_gateway agent room context_memory delivery execution hub_runtime_bridge a2a_adapter platform_module llm_gateway app_shell jobs common container.py main.py` returns no production matches.
- `rg -n "VectorIndex|update_pinecone_index" api_gateway/viewsets main.py` returns no production matches.
- `main.py` starts infrastructure through `container.create_mongo_dal()`, `create_vector_dal()`, and `create_object_storage_dal()`.
- All domain writes are owned by module repositories or facades.
- `database/migration/*` remains runnable for operational migrations.
- `uv run pytest` and `uv run ruff check .` pass.

## Execution Notes

- Do not remove `database/migration/*` imports of `database.mongodb`.
- Do not batch all modules into one commit. The blocker manifest must shrink after each module-level task.
- Preserve API response shapes and route paths.
- Keep app-shell adapters only as compatibility surfaces; they must fail fast before binding and must not own database clients.
- Prefer module repository tests over route-only tests for query behavior.
