# HubRuntimeBridge Decouple Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move HubRuntimeBridge business behavior out of `app_shell.relay_service` so relay remains a compatibility adapter and hub operations are owned by `hub_runtime_bridge.HubFacade` plus internal hub services.

**Architecture:** Preserve the existing relay/hub API contract and startup flow while replacing direct access to `HubFacade.deps`, `HubMongoRepository`, and `HubInternalResponseRouter` from app-shell code with explicit Hub facade methods. The first cleanup keeps legacy request/response adapters in `app_shell.relay_service`, but Hub liveness, stream binding, agent writer binding, task ownership access, and internal response routing move behind `HubFacade`.

**Tech Stack:** Python 3.11+, FastAPI route adapters, Python Protocol-style dependency boundaries, existing pytest suite, existing `hub_runtime_bridge` and `app_shell` modules.

---

## Scope and Non-Goals

This is a backend-only refactor.

No frontend changes are required because API paths, payloads, response models, and hub daemon behavior remain unchanged.

Do not add import-boundary enforcement in this plan. The user explicitly wants direct code cleanup instead of boundary gates.

Do not remove `app_shell.relay_service` yet. It remains as the route-facing compatibility adapter until API routes can call `HubFacade` directly in a later cleanup.

Do not split `app_shell.database_service` in this plan. The temporary `_RelayPublishAuthorizationReader` and `_RelayCancellationReader` adapters may still depend on it until Room/Execution cleanup.

Do not clean `execution.orchestration.room_message_center` in this plan. Keep the existing legacy response sink bridge, but hide Hub journal/ownership/router internals behind `HubFacade`.

---

## Current Problems This Plan Fixes

`app_shell/relay_service.py` currently violates the intended module boundary by doing these HubRuntimeBridge-internal things:

- Imports `HubMongoRepository` directly from `hub_runtime_bridge.repository.mongo`.
- Imports `HubInternalResponseRouter` directly from `hub_runtime_bridge.internal_response_router`.
- Reads `self._facade.deps.task_ownership_store`, `self._facade.deps.hub_response_journal`, and `self._facade.deps.worker_id`.
- Mutates `self._facade.deps.streams` and `self._facade.deps.agent_registry_writer` after facade construction.
- Performs hub liveness sweep/recovery directly in app-shell code.

The target after this plan:

- `relay_service` does not access `self._facade.deps`.
- `relay_service` does not import `HubMongoRepository`.
- `relay_service` does not import `HubInternalResponseRouter`.
- `HubFacade` owns stream rebinding, agent writer rebinding, internal response router construction, task ownership access, and liveness sweep.
- `relay_service` remains responsible only for legacy API compatibility, APIKey-to-owner conversion, and temporary adapters to other app-shell services.

---

## File Structure

Modify these files:

- `hub_runtime_bridge/facade.py`
  - Add explicit public methods/properties for the adapter-facing HubRuntimeBridge API.
  - Rebuild internal helper services when mutable runtime dependencies are bound.
  - Own internal response router construction.
  - Own liveness sweep and recovery.

- `app_shell/relay_service.py`
  - Replace all direct `self._facade.deps` access with `HubFacade` public methods/properties.
  - Remove direct `HubMongoRepository` and `HubInternalResponseRouter` imports.
  - Keep temporary legacy adapters and route compatibility methods.

- `tests/test_hub_runtime_bridge_facade.py`
  - Add focused tests for facade runtime binding and internal response router ownership.
  - Add focused tests for liveness sweep behavior using fake repository/streams.

- `tests/test_relay_service_hub_facade_adapter.py`
  - Add a focused test that `RelayService` delegates stream and writer binding without touching facade internals.
  - Add a focused test that heartbeat sweep delegates to facade.

- `System-Architecture.md`
  - Update HubRuntimeBridge/app-shell notes to say relay service is now a compatibility adapter over `HubFacade` public methods, not the owner of Hub internals.

Do not modify frontend files.

---

## Task 1: Add HubFacade runtime binding methods

**Files:**

- Modify: `hub_runtime_bridge/facade.py`
- Test: `tests/test_hub_runtime_bridge_facade.py`

### Desired behavior

`HubFacade` must provide public methods to bind streams and agent registry writer after construction. These methods must rebuild helper services that captured the old dependency at construction time.

This matters because simply replacing `self._facade.deps.streams = streams` with `self._facade.bind_streams(streams)` is not enough. `_liveness`, `_sync`, and `_connection` are currently constructed with dependency values from `__init__`, so the facade must refresh all dependent helpers after a bind. `HubConnectionService` captures the liveness reader, so stream rebinding must refresh `_connection` as well as `_liveness`.

### Steps

- [ ] **Step 1: Add failing tests for runtime binding**

Append tests like this to `tests/test_hub_runtime_bridge_facade.py`:

```python
import pytest

from hub_runtime_bridge import HubFacade
from hub_runtime_bridge.config import HubRuntimeBridgeConfig
from hub_runtime_bridge.deps import HubRuntimeBridgeDeps


class _Streams:
    def __init__(self) -> None:
        self.heartbeats: list[str] = []
        self.alive: set[str] = set()

    async def record_heartbeat(self, hub_id: str) -> None:
        self.heartbeats.append(hub_id)
        self.alive.add(hub_id)

    async def is_hub_alive(self, hub_id: str) -> bool:
        return hub_id in self.alive


class _Writer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, list, bool]] = []

    async def sync_hub_agents(
        self,
        hub_id: str,
        owner_id: str,
        descriptors: list,
        *,
        prune_missing: bool = True,
    ) -> list:
        self.calls.append((hub_id, owner_id, descriptors, prune_missing))
        return []


@pytest.mark.asyncio
async def test_hub_facade_bind_streams_updates_liveness_service() -> None:
    facade = HubFacade()
    streams = _Streams()

    facade.bind_streams(streams)
    await streams.record_heartbeat("hub-1")

    assert await facade.is_hub_online("hub-1") is True


@pytest.mark.asyncio
async def test_hub_facade_bind_agent_registry_writer_updates_sync_service() -> None:
    facade = HubFacade()
    writer = _Writer()

    facade.bind_agent_registry_writer(writer)

    assert await facade.sync_agents("hub-1", [], "owner-1") == []
    assert writer.calls == [("hub-1", "owner-1", [], True)]


class _RepositoryForConnectionLiveness:
    async def get_by_id(self, hub_id: str) -> dict | None:
        return {"hub_id": hub_id, "user_id": "owner-1", "is_online": False}

    async def get_by_owner(self, owner_id: str) -> list[dict]:
        return [{"hub_id": "hub-1", "user_id": owner_id, "is_online": False}]


@pytest.mark.asyncio
async def test_hub_facade_bind_streams_refreshes_connection_liveness() -> None:
    facade = HubFacade(
        deps=HubRuntimeBridgeDeps(
            config=HubRuntimeBridgeConfig(),
            hub_repository=_RepositoryForConnectionLiveness(),
        )
    )
    streams = _Streams()

    facade.bind_streams(streams)
    await streams.record_heartbeat("hub-1")

    hubs = await facade.list_hubs("owner-1")
    assert hubs[0].is_online is True
```

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run:

```bash
uv run pytest tests/test_hub_runtime_bridge_facade.py -k "bind_streams or bind_agent_registry_writer" -q
```

Expected before implementation:

```text
FAILED ... AttributeError: 'HubFacade' object has no attribute 'bind_streams'
FAILED ... AttributeError: 'HubFacade' object has no attribute 'bind_agent_registry_writer'
```

The third test should also fail or return stale `is_online=False` until `bind_streams()` refreshes `_connection` after rebuilding `_liveness`.

- [ ] **Step 3: Implement helper refresh methods and bind methods**

In `hub_runtime_bridge/facade.py`, refactor the helper service construction into private methods and add the public bind methods.

Add imports if needed:

```python
from common.protocols import AgentRegistryWriter
```

Add these methods inside `HubFacade`:

```python
    def _build_liveness_service(self) -> HubLivenessService:
        return HubLivenessService(
            repository=self.deps.hub_repository,
            streams=self.deps.streams,
            local_is_connected=lambda hub_id: hub_id in self._queues,
        )

    def _build_sync_service(self) -> HubAgentSyncService | None:
        if self.deps.agent_registry_writer is None:
            return None
        return HubAgentSyncService(
            writer=self.deps.agent_registry_writer,
            streams=self.deps.streams,
        )

    def _build_connection_service(self) -> HubConnectionService | None:
        if self.deps.hub_repository is None:
            return None
        return HubConnectionService(
            repository=self.deps.hub_repository,
            liveness_reader=self._liveness,
            status_reader=self.deps.hub_agent_status_reader,
        )

    def bind_streams(self, streams: Any) -> None:
        self.deps.streams = streams
        self._liveness = self._build_liveness_service()
        self._connection = self._build_connection_service()
        self._sync = self._build_sync_service()

    def bind_agent_registry_writer(self, writer: AgentRegistryWriter) -> None:
        self.deps.agent_registry_writer = writer
        self._sync = self._build_sync_service()
```

Change the existing constructor setup from inline construction to:

```python
        self._liveness = self._build_liveness_service()
        self._connection = self._build_connection_service()
        self._relay = HubRelayService(
            push_event=self._push_event_dict,
            offline_failure_port=deps.offline_failure_port,
            call_counter=deps.agent_call_counter,
        )
        self._sync = self._build_sync_service()
```

- [ ] **Step 4: Run the focused tests and confirm they pass**

Run:

```bash
uv run pytest tests/test_hub_runtime_bridge_facade.py -k "bind_streams or bind_agent_registry_writer" -q
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Commit this task**

```bash
git add hub_runtime_bridge/facade.py tests/test_hub_runtime_bridge_facade.py
git commit -m "refactor(hub): expose runtime binding methods"
```

---

## Task 2: Add HubFacade public ownership and worker accessors

**Files:**

- Modify: `hub_runtime_bridge/facade.py`
- Test: `tests/test_hub_runtime_bridge_facade.py`

### Desired behavior

`RelayService` currently reads `self._facade.deps.task_ownership_store`, `self._facade.deps.worker_id`, and `self._facade.ownership_lease_maintainer`. This task only adds explicit facade properties. The RelayService replacement is intentionally deferred to Task 5 so facade API and adapter usage remain separate, reviewable changes.

### Steps

- [ ] **Step 1: Add failing tests for accessors**

Append to `tests/test_hub_runtime_bridge_facade.py`:

```python
from hub_runtime_bridge.config import HubRuntimeBridgeConfig
from hub_runtime_bridge.deps import HubRuntimeBridgeDeps


class _OwnershipStore:
    async def ensure_indexes(self) -> None:
        return None

    async def claim_or_refresh(self, *args, **kwargs):
        return {"aliases": kwargs.get("aliases", []), "lease_token": kwargs.get("lease_token")}

    async def resolve_owner(self, *args, **kwargs):
        return None

    async def release(self, *args, **kwargs):
        return None


def test_hub_facade_exposes_worker_and_ownership_dependencies() -> None:
    store = _OwnershipStore()
    deps = HubRuntimeBridgeDeps(
        config=HubRuntimeBridgeConfig(),
        task_ownership_store=store,
        worker_id="worker-1",
    )
    facade = HubFacade(deps=deps)

    assert facade.worker_id == "worker-1"
    assert facade.task_ownership_store is store
    assert facade.ownership_maintainer is facade.ownership_lease_maintainer
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run:

```bash
uv run pytest tests/test_hub_runtime_bridge_facade.py -k "exposes_worker_and_ownership" -q
```

Expected before implementation:

```text
FAILED ... AttributeError: 'HubFacade' object has no attribute 'worker_id'
```

- [ ] **Step 3: Add the facade properties**

Inside `HubFacade` in `hub_runtime_bridge/facade.py`, add:

```python
    @property
    def worker_id(self) -> str:
        return self.deps.worker_id

    @property
    def task_ownership_store(self) -> Any | None:
        return self.deps.task_ownership_store

    @property
    def ownership_maintainer(self) -> OwnershipLeaseMaintainer | None:
        return self.ownership_lease_maintainer
```

- [ ] **Step 4: Run the focused test and confirm it passes**

Run:

```bash
uv run pytest tests/test_hub_runtime_bridge_facade.py -k "exposes_worker_and_ownership" -q
```

Expected:

```text
1 passed
```

- [ ] **Step 5: Commit this task**

```bash
git add hub_runtime_bridge/facade.py tests/test_hub_runtime_bridge_facade.py
git commit -m "refactor(hub): expose ownership facade accessors"
```

---

## Task 3: Move internal response router construction behind HubFacade

**Files:**

- Modify: `hub_runtime_bridge/facade.py`
- Modify: `app_shell/relay_service.py`
- Test: `tests/test_hub_runtime_bridge_facade.py`

### Desired behavior

`relay_service` may temporarily keep `_LegacyPublishSink`, because it still bridges to legacy response handling. But `relay_service` must not construct `HubInternalResponseRouter` or read facade internals to do it.

`HubFacade` should provide:

```python
def bind_internal_response_sink(self, sink: Any) -> Any: ...
```

The facade method creates `HubInternalResponseRouter`, binds it into `HubPublishService`, stores it as dispatcher, and returns the router for observability/backward compatibility. The sink must implement the actual router sink method, `handle_hub_agent_response(...)`; a construction-only test is not sufficient because the publish path depends on `HubPublishService._dispatcher`. Binding must also be safe before or after `HubFacade.start()`. Keep `bind_internal_response_sink()` synchronous for RelayService compatibility. Add explicit `_started` state, because `_replay_worker is None` can mean either not started or started-before-dispatcher-bound. If the facade has already started, synchronously replace the facade's `_replay_worker` reference with a worker built from the latest dispatcher, then use the facade task runner to perform the async old-worker stop and new-worker start. Track the pending restart task and a monotonic restart generation so `stop()` can cancel/await any pending restart and a stale scheduled restart cannot start a worker after shutdown. Tests should assert the synchronously visible worker/dispatcher replacement and separately cover stop-during-pending-restart behavior.

### Steps

- [ ] **Step 1: Add failing test for router construction**

Append to `tests/test_hub_runtime_bridge_facade.py`:

```python
from hub_runtime_bridge.hub_response_journal import InMemoryHubResponseJournal
from hub_runtime_bridge.task_ownership import InMemoryHubTaskOwnershipStore


class _Sink:
    def __init__(self) -> None:
        self.events = []

    async def handle_hub_agent_response(self, event):
        self.events.append(event)


def test_hub_facade_bind_internal_response_sink_owns_router_creation() -> None:
    deps = HubRuntimeBridgeDeps(
        config=HubRuntimeBridgeConfig(),
        hub_response_journal=InMemoryHubResponseJournal(),
        task_ownership_store=InMemoryHubTaskOwnershipStore(),
        worker_id="worker-1",
    )
    facade = HubFacade(deps=deps)

    sink = _Sink()
    router = facade.bind_internal_response_sink(sink)

    assert router is not None
    assert facade.internal_response_dispatcher is router


@pytest.mark.asyncio
async def test_hub_facade_bind_internal_response_sink_receives_publish_event() -> None:
    deps = HubRuntimeBridgeDeps(
        config=HubRuntimeBridgeConfig(),
        hub_response_journal=InMemoryHubResponseJournal(),
        task_ownership_store=InMemoryHubTaskOwnershipStore(),
        worker_id="worker-1",
    )
    facade = HubFacade(deps=deps)
    sink = _Sink()
    facade.bind_internal_response_sink(sink)

    await facade.publish_from_hub(
        "hub-1",
        {
            "owner_id": "owner-1",
            "room_id": "room-1",
            "events": [
                {
                    "type": "agent_response",
                    "agent_message_id": "agent-message-1",
                    "data": {
                        "task_id": "task-1",
                        "content": "done",
                        "response_seq": 1,
                    },
                }
            ],
        },
    )

    assert len(sink.events) == 1


@pytest.mark.asyncio
async def test_hub_facade_bind_internal_response_sink_replaces_started_replay_worker_dispatcher() -> None:
    deps = HubRuntimeBridgeDeps(
        config=HubRuntimeBridgeConfig(),
        hub_response_journal=InMemoryHubResponseJournal(),
        task_ownership_store=InMemoryHubTaskOwnershipStore(),
        worker_id="worker-1",
    )
    facade = HubFacade(deps=deps)
    first_router = facade.bind_internal_response_sink(_Sink())
    await facade.start()
    first_worker = facade._replay_worker
    assert first_worker is not None
    assert first_worker._dispatcher is first_router

    second_router = facade.bind_internal_response_sink(_Sink())

    assert facade.internal_response_dispatcher is second_router
    assert facade._replay_worker is not None
    assert facade._replay_worker is not first_worker
    assert facade._replay_worker._dispatcher is second_router
    await facade.stop()


@pytest.mark.asyncio
async def test_hub_facade_stop_cancels_pending_replay_worker_restart() -> None:
    scheduled = []

    def capture_task(coro):
        task = asyncio.create_task(coro)
        scheduled.append(task)
        return task

    deps = HubRuntimeBridgeDeps(
        config=HubRuntimeBridgeConfig(),
        hub_response_journal=InMemoryHubResponseJournal(),
        task_ownership_store=InMemoryHubTaskOwnershipStore(),
        worker_id="worker-1",
        task_runner=capture_task,
    )
    facade = HubFacade(deps=deps)
    facade.bind_internal_response_sink(_Sink())
    await facade.start()

    facade.bind_internal_response_sink(_Sink())
    assert scheduled

    await facade.stop()

    assert facade._started is False
    assert facade._replay_worker_restart_task is None
    assert all(task.done() or task.cancelled() for task in scheduled)
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run:

```bash
uv run pytest tests/test_hub_runtime_bridge_facade.py -k "bind_internal_response_sink" -q
```

Expected before implementation:

```text
FAILED ... AttributeError: 'HubFacade' object has no attribute 'bind_internal_response_sink'
```

- [ ] **Step 3: Implement facade-owned router construction**

In `hub_runtime_bridge/facade.py`, add import:

```python
from hub_runtime_bridge.internal_response_router import HubInternalResponseRouter
```

Add inside `HubFacade`:

```python
    @property
    def internal_response_dispatcher(self) -> Any | None:
        return self._dispatcher

    def bind_internal_response_sink(self, sink: Any) -> HubInternalResponseRouter:
        router = HubInternalResponseRouter(
            sink=sink,
            journal=self.deps.hub_response_journal,
            ownership_store=self.deps.task_ownership_store,
            worker_id=self.deps.worker_id,
        )
        self.bind_internal_response_dispatcher(router)
        return router
```

Keep the existing `bind_internal_response_dispatcher()` method for cases where a fully constructed dispatcher is injected by tests or future container code. Update that method so it is the single path for dispatcher changes: set `self._dispatcher`, bind it into `self._publish`, and if the facade has already started, ensure `HubResponseReplayWorker` is using the new dispatcher. This covers both cases: an existing replay worker must be stopped and recreated, while a start-time missing replay worker must be created after late binding. Keep this public bind method synchronous: build and assign the replacement `_replay_worker` synchronously, then schedule the async stop/start work with the facade task runner. Add `_started: bool`, `_replay_worker_restart_task: Any | None`, and `_replay_worker_generation: int`. Increment the generation on every dispatcher rebind and in `stop()`. The scheduled async restart must check both the captured generation and `_started` before starting the new worker. `stop()` must mark `_started = False`, invalidate the generation, cancel/await any pending restart task when possible, then stop the currently assigned replay worker. To avoid duplicating worker construction, extract the replay-worker creation in `start()` into a helper such as `_build_replay_worker()` plus an async helper such as `_restart_replay_worker(old_worker, new_worker, generation)`.

- [ ] **Step 4: Refactor RelayService to call the facade method**

In `app_shell/relay_service.py`, remove this import:

```python
from hub_runtime_bridge.internal_response_router import HubInternalResponseRouter
```

Change the `_internal_response_dispatcher` annotation from:

```python
        self._internal_response_dispatcher: HubInternalResponseRouter | None = None
```

to:

```python
        self._internal_response_dispatcher: Any | None = None
```

Replace `_bind_internal_response_router()` with:

```python
    def _bind_internal_response_router(self) -> Any:
        router = self._facade.bind_internal_response_sink(
            _LegacyPublishSink(
                self,
                response_converter=self._response_converter,
            )
        )
        self._internal_response_dispatcher = router
        return router
```

Replace the `internal_response_dispatcher` property annotation with:

```python
    @property
    def internal_response_dispatcher(self) -> Any | None:
        return self._internal_response_dispatcher
```

- [ ] **Step 5: Run focused Hub tests**

Run:

```bash
uv run pytest tests/test_hub_runtime_bridge_facade.py -k "bind_internal_response_sink" -q
```

Expected:

```text
3 passed
```

- [ ] **Step 6: Run focused relay tests if present**

Run:

```bash
uv run pytest tests/test_api_relay.py tests -k "relay_service or relay_routes or hub_runtime_bridge" -q
```

Expected:

```text
all selected tests pass
```

If no tests match `relay_service`, pytest may report no selected tests. In that case, add the adapter tests in Task 5 before relying on relay coverage.

- [ ] **Step 7: Commit this task**

```bash
git add hub_runtime_bridge/facade.py app_shell/relay_service.py tests/test_hub_runtime_bridge_facade.py
git commit -m "refactor(hub): own internal response router wiring"
```

---

## Task 4: Move stream-backed liveness sweep into HubFacade without changing legacy disconnect behavior

**Files:**

- Modify: `hub_runtime_bridge/facade.py`
- Modify: `app_shell/relay_service.py`
- Test: `tests/test_hub_runtime_bridge_facade.py`
- Test: `tests/test_heartbeat_fixes.py`

### Desired behavior

`relay_service` must not construct `HubMongoRepository` or run repository-specific stream liveness/recovery queries. `HubFacade` owns the repository-backed liveness sweep because it owns Hub repository, stream liveness, and agent registry writer integration.

The facade method should be:

```python
async def sweep_stream_liveness(self) -> list[HubStreamLivenessEvent]: ...
```

Return value:

- A list of lightweight facade-owned stale stream events, each with `hub_id` and `connection_id`.
- `RelayService` uses this return value only to preserve current heartbeat-expiry logging and set its legacy `_hub_disconnect_events` until hub connection ownership fully moves into `HubFacade` in a later cleanup.

Preserve current behavior:

- If streams or repository are not configured, the facade sweep is a no-op and returns `[]`.
- For online hubs whose Redis heartbeat expired, use the existing repository methods: guarded `update_hub_status_if_current(...)` only when a connection id is available, otherwise preserve the current unconditional `update_hub_status(...)` fallback used by `RelayService.mark_hub_agents_offline(...)`.
- Mark hub agents offline through `AgentRegistryWriter.mark_hub_agents_offline(...)` when an agent writer is bound. If a hub is newly marked offline but no writer is bound, preserve the existing `RelayService.mark_hub_agents_offline(...)` failure semantics by raising `RuntimeError` after the hub status update.
- For offline hubs whose Redis heartbeat recovered, call `repository.update_hub_status(hub_id, is_online=True)`.
- Preserve the local `RelayService._hub_disconnect_events` signaling until queue/connection ownership is migrated to the facade. This signaling is based on heartbeat expiry, not DB guarded-update success: if Redis says the local stream is stale, return that stale event so `RelayService` can set the local disconnect event even when `update_hub_status_if_current(...)` returns `False`.
- Preserve the existing heartbeat-expiry log content in the stream branch. The log must remain at warning level and include the hub id, `redis_alive=False`, `connection_id`, and `local_disconnect_event` so existing heartbeat diagnostics and tests remain valid. Because only `RelayService` can inspect `_hub_disconnect_events`, keep this log in `RelayService` after `sweep_stream_liveness()` returns stale events.
- Keep the non-stream local queue stale handling in `RelayService._do_heartbeat_check()` unchanged for now.

### Steps

- [ ] **Step 1: Add failing facade liveness sweep test using current repository/writer contracts**

Append to `tests/test_hub_runtime_bridge_facade.py`:

```python
class _HubRepositoryForSweep:
    def __init__(self) -> None:
        self.guarded_offline: list[tuple[str, str | None]] = []
        self.updated_offline: list[str] = []
        self.updated_online: list[str] = []

    async def list_online_hubs_for_liveness(self) -> list[dict]:
        return [{"hub_id": "hub-offline", "connection_id": "conn-1"}]

    async def list_offline_hubs_for_recovery(self, limit: int) -> list[dict]:
        assert limit == 100
        return [{"hub_id": "hub-recovered"}]

    async def update_hub_status_if_current(
        self,
        hub_id: str,
        *,
        connection_id: str | None,
        is_online: bool,
    ) -> bool:
        if is_online is False:
            self.guarded_offline.append((hub_id, connection_id))
        return True

    async def update_hub_status(self, hub_id: str, **kwargs) -> None:
        if kwargs.get("is_online") is False:
            self.updated_offline.append(hub_id)
        if kwargs.get("is_online") is True:
            self.updated_online.append(hub_id)


class _StreamsForSweep:
    async def is_hub_alive(self, hub_id: str) -> bool:
        return hub_id == "hub-recovered"


class _WriterForSweep:
    def __init__(self) -> None:
        self.offline_hubs: list[str] = []

    async def mark_hub_agents_offline(self, hub_id: str) -> None:
        self.offline_hubs.append(hub_id)

    async def sync_hub_agents(self, *args, **kwargs) -> list:
        return []


@pytest.mark.asyncio
async def test_hub_facade_sweep_stream_liveness_uses_repository_streams_and_writer() -> None:
    repository = _HubRepositoryForSweep()
    writer = _WriterForSweep()
    deps = HubRuntimeBridgeDeps(
        config=HubRuntimeBridgeConfig(),
        hub_repository=repository,
        streams=_StreamsForSweep(),
        agent_registry_writer=writer,
    )
    facade = HubFacade(deps=deps)

    stale_hubs = await facade.sweep_stream_liveness()

    assert [event.hub_id for event in stale_hubs] == ["hub-offline"]
    assert [event.connection_id for event in stale_hubs] == ["conn-1"]
    assert repository.guarded_offline == [("hub-offline", "conn-1")]
    assert writer.offline_hubs == ["hub-offline"]
    assert repository.updated_online == ["hub-recovered"]


class _HubRepositoryForSweepWithoutConnectionId(_HubRepositoryForSweep):
    async def list_online_hubs_for_liveness(self) -> list[dict]:
        return [{"hub_id": "hub-offline", "connection_id": None}]

    async def update_hub_status_if_current(self, *args, **kwargs) -> bool:
        raise AssertionError("guarded update should not run without connection_id")


@pytest.mark.asyncio
async def test_hub_facade_sweep_stream_liveness_without_connection_id_uses_unconditional_update() -> None:
    repository = _HubRepositoryForSweepWithoutConnectionId()
    writer = _WriterForSweep()
    deps = HubRuntimeBridgeDeps(
        config=HubRuntimeBridgeConfig(),
        hub_repository=repository,
        streams=_StreamsForSweep(),
        agent_registry_writer=writer,
    )
    facade = HubFacade(deps=deps)

    stale_hubs = await facade.sweep_stream_liveness()

    assert [event.hub_id for event in stale_hubs] == ["hub-offline"]
    assert [event.connection_id for event in stale_hubs] == [None]
    assert repository.updated_offline == ["hub-offline"]
    assert writer.offline_hubs == ["hub-offline"]


class _HubRepositoryForSweepGuardMismatch(_HubRepositoryForSweep):
    async def update_hub_status_if_current(
        self,
        hub_id: str,
        *,
        connection_id: str | None,
        is_online: bool,
    ) -> bool:
        self.guarded_offline.append((hub_id, connection_id))
        return False


@pytest.mark.asyncio
async def test_hub_facade_sweep_stream_liveness_returns_stale_hub_when_guard_fails() -> None:
    repository = _HubRepositoryForSweepGuardMismatch()
    writer = _WriterForSweep()
    deps = HubRuntimeBridgeDeps(
        config=HubRuntimeBridgeConfig(),
        hub_repository=repository,
        streams=_StreamsForSweep(),
        agent_registry_writer=writer,
    )
    facade = HubFacade(deps=deps)

    stale_hubs = await facade.sweep_stream_liveness()

    assert [event.hub_id for event in stale_hubs] == ["hub-offline"]
    assert [event.connection_id for event in stale_hubs] == ["conn-1"]
    assert repository.guarded_offline == [("hub-offline", "conn-1")]
    assert writer.offline_hubs == []
```

- [ ] **Step 2: Run focused test and confirm it fails**

Run:

```bash
uv run pytest tests/test_hub_runtime_bridge_facade.py -k "sweep_stream_liveness" -q
```

Expected before implementation:

```text
FAILED ... AttributeError: 'HubFacade' object has no attribute 'sweep_stream_liveness'
```

- [ ] **Step 3: Implement `HubFacade.sweep_stream_liveness()` using existing repository/writer APIs**

In `hub_runtime_bridge/facade.py`, add the import:

```python
from dataclasses import dataclass
```

Add a small facade-owned result type at module scope:

```python
@dataclass(frozen=True)
class HubStreamLivenessEvent:
    hub_id: str
    connection_id: str | None
```

Add the method inside `HubFacade`:

```python
    async def sweep_stream_liveness(self) -> list[HubStreamLivenessEvent]:
        repository = self.deps.hub_repository
        streams = self.deps.streams
        if repository is None or streams is None:
            return []

        stale_hubs: list[HubStreamLivenessEvent] = []
        for doc in await repository.list_online_hubs_for_liveness():
            hub_id = doc["hub_id"]
            if await streams.is_hub_alive(hub_id):
                continue

            connection_id = doc.get("connection_id")
            if connection_id and hasattr(repository, "update_hub_status_if_current"):
                updated = await repository.update_hub_status_if_current(
                    hub_id,
                    connection_id=connection_id,
                    is_online=False,
                )
            else:
                await repository.update_hub_status(hub_id, is_online=False)
                updated = True

            stale_hubs.append(
                HubStreamLivenessEvent(
                    hub_id=hub_id,
                    connection_id=connection_id,
                )
            )

            if updated:
                if self.deps.agent_registry_writer is None:
                    raise RuntimeError("AgentRegistryWriter not bound")
                await self.deps.agent_registry_writer.mark_hub_agents_offline(hub_id)
                self._liveness_cache[hub_id] = False

        for doc in await repository.list_offline_hubs_for_recovery(100):
            hub_id = doc["hub_id"]
            if await streams.is_hub_alive(hub_id):
                await repository.update_hub_status(hub_id, is_online=True)
                self._liveness_cache[hub_id] = True

        return stale_hubs
```

Use the exact repository method names that exist in `hub_runtime_bridge/repository/mongo.py`. If the repository has a narrower helper already used by `RelayService.mark_hub_agents_offline()`, call that helper instead of inventing a new repository API.

- [ ] **Step 4: Refactor RelayService heartbeat sweep while preserving disconnect events**

In `app_shell/relay_service.py`, remove this import:

```python
from hub_runtime_bridge.repository.mongo import HubMongoRepository
```

Replace the stream branch inside `_do_heartbeat_check()`:

```python
        if self._streams:
            repository = HubMongoRepository(self._mongo)
            for doc in await repository.list_online_hubs_for_liveness():
                ...
            for doc in await repository.list_offline_hubs_for_recovery(100):
                ...
            return
```

with:

```python
        if self._streams:
            stale_events = await self._facade.sweep_stream_liveness()
            for stale in stale_events:
                disconnect = self._hub_disconnect_events.get(stale.hub_id)
                logger.warning(
                    "Hub %s heartbeat expired: redis_alive=False connection_id=%s local_disconnect_event=%s",
                    stale.hub_id,
                    stale.connection_id,
                    disconnect is not None,
                )
                if disconnect is not None:
                    disconnect.set()
            return
```

Keep the local in-memory queue stale handling after that branch unchanged for non-stream mode.

- [ ] **Step 5: Run focused facade and existing heartbeat tests**

Run:

```bash
uv run pytest tests/test_hub_runtime_bridge_facade.py -k "sweep_stream_liveness" -q
uv run pytest tests/test_heartbeat_fixes.py -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 6: Commit this task**

```bash
git add hub_runtime_bridge/facade.py app_shell/relay_service.py tests/test_hub_runtime_bridge_facade.py
git commit -m "refactor(hub): move stream liveness sweep behind facade"
```

---

## Task 5: Replace RelayService direct facade internals access

**Files:**

- Modify: `app_shell/relay_service.py`
- Test: `tests/test_relay_service_hub_facade_adapter.py`

### Desired behavior

`app_shell.relay_service` should call `HubFacade` public methods/properties instead of reading or mutating `self._facade.deps`.

### Steps

- [ ] **Step 1: Add adapter tests for stream/writer binding and sweep delegation**

Create `tests/test_relay_service_hub_facade_adapter.py` with:

```python
from __future__ import annotations

import asyncio

import pytest

from app_shell.relay_service import RelayService


class _DatabaseService:
    pass


class _SSEManager:
    pass


class _StaleHub:
    def __init__(self, hub_id: str, connection_id: str | None = None) -> None:
        self.hub_id = hub_id
        self.connection_id = connection_id


class _FacadeSpy:
    def __init__(self) -> None:
        self.bound_streams = None
        self.bound_writer = None
        self.swept = False
        self.stale_hubs: list[_StaleHub] = []
        self.bound_response_sink = None
        self.worker_id = "worker-1"
        self.task_ownership_store = object()
        self.ownership_maintainer = object()

    def bind_streams(self, streams):
        self.bound_streams = streams

    def bind_agent_registry_writer(self, writer):
        self.bound_writer = writer

    async def sweep_stream_liveness(self) -> list[_StaleHub]:
        self.swept = True
        return list(self.stale_hubs)

    def bind_internal_response_sink(self, sink):
        self.bound_response_sink = sink
        return object()


@pytest.fixture
def service(monkeypatch):
    relay = RelayService(
        mongo=None,
        database_service=_DatabaseService(),
        sse_manager=_SSEManager(),
    )
    # Pass mongo=None so RelayService constructs an in-memory HubFacade before
    # the test replaces it with the spy. A minimal fake Mongo is not enough
    # because mongo-backed HubFacade construction expects collection access.
    spy = _FacadeSpy()
    relay._facade = spy
    return relay, spy


def test_relay_service_binds_streams_through_hub_facade(service) -> None:
    relay, spy = service
    streams = object()

    relay.set_stream_service(streams)

    assert relay._streams is streams
    assert spy.bound_streams is streams


def test_relay_service_binds_agent_writer_through_hub_facade(service) -> None:
    relay, spy = service
    writer = object()

    relay.bind_agent_registry_writer(writer)

    assert relay._agent_registry_writer is writer
    assert spy.bound_writer is writer


def test_relay_service_reads_ownership_accessors_through_hub_facade(service) -> None:
    relay, spy = service

    assert relay.task_ownership_store is spy.task_ownership_store
    assert relay.ownership_lease_maintainer is spy.ownership_maintainer
    assert relay.worker_id == spy.worker_id


@pytest.mark.asyncio
async def test_relay_service_delegates_stream_liveness_sweep(service) -> None:
    relay, spy = service
    relay._streams = object()

    await relay._do_heartbeat_check(stale_threshold=30)

    assert spy.swept is True


@pytest.mark.asyncio
async def test_relay_service_sets_disconnect_event_for_stale_hub(service) -> None:
    relay, spy = service
    relay._streams = object()
    spy.stale_hubs = [_StaleHub("hub-1", "conn-1")]
    disconnect = asyncio.Event()
    relay._hub_disconnect_events["hub-1"] = disconnect

    await relay._do_heartbeat_check(stale_threshold=30)

    assert disconnect.is_set()


def test_relay_service_binds_internal_response_sink_through_hub_facade(service) -> None:
    relay, spy = service

    relay.bind_response_handler(object())

    assert spy.bound_response_sink is not None
    assert relay._internal_response_dispatcher is not None
```

- [ ] **Step 2: Run adapter tests and confirm they fail**

Run:

```bash
uv run pytest tests/test_relay_service_hub_facade_adapter.py -q
```

Expected before implementation:

```text
FAILED ... AttributeError: '_FacadeSpy' object has no attribute 'deps'
```

or a similar failure showing `RelayService` still expects facade internals.

- [ ] **Step 3: Replace direct `deps` access in `relay_service`**

In `app_shell/relay_service.py`, apply these replacements:

```python
    @property
    def task_ownership_store(self) -> Any | None:
        return self._facade.task_ownership_store

    @property
    def ownership_lease_maintainer(self) -> Any | None:
        return self._facade.ownership_maintainer

    @property
    def worker_id(self) -> str:
        return self._facade.worker_id
```

Replace:

```python
        self._facade.deps.streams = streams
```

with:

```python
        self._facade.bind_streams(streams)
```

Replace:

```python
        self._facade.deps.agent_registry_writer = writer
```

with:

```python
        self._facade.bind_agent_registry_writer(writer)
```

Replace any remaining `self._facade.deps.*` usage by using the public facade methods/properties added in Tasks 1-4.

- [ ] **Step 4: Run adapter tests and confirm they pass**

Run:

```bash
uv run pytest tests/test_relay_service_hub_facade_adapter.py -q
```

Expected:

```text
6 passed
```

- [ ] **Step 5: Confirm no direct facade internals remain in relay service**

Run:

```bash
rg -n "self\._facade\.deps|self\._facade\.ownership_lease_maintainer|HubMongoRepository|HubInternalResponseRouter" app_shell/relay_service.py
```

Expected:

```text
(no output)
```

- [ ] **Step 6: Commit this task**

```bash
git add app_shell/relay_service.py tests/test_relay_service_hub_facade_adapter.py
git commit -m "refactor(relay): delegate hub internals through facade"
```

---

## Task 6: Document and preserve RelayService operation fallbacks

**Files:**

- Modify: `app_shell/relay_service.py`
- Test: `tests/test_api_relay.py`
- Test: `tests/test_heartbeat_fixes.py`

### Desired behavior

Do not directly delegate `cancel_hub_task()`, `reply_to_hub_task()`, `send_to_hub()`, or `get_hub_status()` to `HubFacade` in this cleanup unless connection/queue/status ownership has first moved into the facade.

Current behavior that must be preserved:

- In non-stream mode, `RelayService.cancel_relay_task()` and `RelayService.reply_to_relay_task()` fall back to RelayService-owned in-memory `_hub_queues`.
- `HubFacade` has its own `_queues`; direct delegation would not reach existing RelayService-connected clients.
- `RelayService.get_hub_status()` currently returns `models.hub.HubStatus` with fields and counts assembled from legacy Mongo helpers. `HubFacade.get_hub_status()` currently returns facade/domain hub info and is not behavior-equivalent unless a status reader adapter is wired.
- `send_to_hub()` follows the same queue-ownership constraint and should be explicitly deferred with cancel/reply. It must preserve all existing `HubDispatchCommand` fields supported by `RelayToHubEvent`, including `task_id`.

### Steps

- [ ] **Step 1: Add explicit regression tests for preserved fallback behavior**

Extend `tests/test_api_relay.py` instead of creating mocks from scratch, because the file already has `_make_relay_service()` with the required Mongo/database/SSE fakes.

Add `HubDispatchCommand` to the existing import from `common.dto` if it is not already imported:

```python
from common.dto import HubDispatchCommand
```

Add this test near the existing non-stream cancel/reply fallback tests:

```python
@pytest.mark.asyncio
async def test_send_to_hub_uses_in_memory_live_queue_without_streams():
    import asyncio

    svc = _make_relay_service()
    q = asyncio.Queue()
    svc._hub_queues["hub-001"] = q

    result = await svc.send_to_hub(
        HubDispatchCommand(
            hub_id="hub-001",
            agent_id="agent-1",
            local_agent_id="local-1",
            room_id="room-1",
            user_message_id="user-msg-1",
            agent_message_id="agent-msg-1",
            payload={"text": "hello"},
            task_id="task-1",
        )
    )

    event = await q.get()
    assert result.accepted is True
    assert event["type"] == "user_message"
    assert event["task_id"] == "task-1"
    assert event["agent_message_id"] == "agent-msg-1"
    assert event["message"] == {"text": "hello"}
```

Strengthen the existing `TestRelayServiceStatus.test_status_returns_hubs` assertions so it proves the legacy response shape is preserved:

```python
        assert result[0].is_online is False
        assert result[0].last_connected_at is None
        assert result[0].agent_count == 4
        assert result[0].active_agent_count == 3
        assert result[0].inactive_agent_count == 1
```

Keep the existing tests `test_cancel_relay_task_uses_in_memory_live_queue_without_streams` and `test_reply_to_relay_task_uses_in_memory_live_queue_without_streams`; they already prove cancel/reply queue fallback and must continue to pass.

- [ ] **Step 2: Preserve `send_to_hub()` task id in the legacy queue event**

The regression test above asserts `event["task_id"] == "task-1"`. If the current implementation omits the field, update `RelayService.send_to_hub()` so the constructed `RelayToHubEvent` includes:

```python
task_id=command.task_id
```

This is still legacy queue behavior, not facade delegation.

- [ ] **Step 3: Add comments marking intentionally deferred operation delegation**

In `app_shell/relay_service.py`, add short comments near `send_to_hub()`, `cancel_relay_task()`, `reply_to_relay_task()`, and `get_hub_status()` explaining that these methods intentionally preserve legacy queue/status behavior until hub connection ownership is migrated into `HubFacade`.

Use wording equivalent to:

```python
# Compatibility note: this method cannot delegate directly to HubFacade yet.
# RelayService still owns legacy in-memory hub queues for non-stream mode;
# HubFacade owns a separate queue set. Move queue ownership first, then thin this.
```

For `get_hub_status()`, use wording equivalent to:

```python
# Compatibility note: this method preserves the legacy HubStatus response shape
# and count fields. Delegate to HubFacade only after a status-reader adapter is
# bound that produces behavior-equivalent models.HubStatus responses.
```

- [ ] **Step 4: Do not replace command methods with direct facade delegation**

Leave the existing fallback behavior intact. If any earlier edit changed these methods to direct delegation, revert that part and preserve the fallback path.

- [ ] **Step 5: Add heartbeat regression for guard-false local disconnect signaling**

In `tests/test_heartbeat_fixes.py`, add a stream heartbeat regression where the repository guarded update returns `False`, `AgentRegistryWriter.mark_hub_agents_offline(...)` is not called, but `RelayService._hub_disconnect_events[hub_id]` is still set. This preserves the current local disconnect behavior while avoiding incorrect agent offline writes after ownership changed.

- [ ] **Step 6: Run explicit and existing behavior tests that prove fallbacks remain intact**

Run:

```bash
uv run pytest tests/test_api_relay.py -k "send_to_hub_uses_in_memory_live_queue_without_streams or cancel_relay_task_uses_in_memory_live_queue_without_streams or reply_to_relay_task_uses_in_memory_live_queue_without_streams or status_returns_hubs" -q
uv run pytest tests/test_heartbeat_fixes.py -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 7: Commit this task**

```bash
git add app_shell/relay_service.py tests/test_api_relay.py tests/test_heartbeat_fixes.py
git commit -m "docs(relay): mark deferred hub operation delegation"
```

---

## Task 7: Update architecture documentation

**Files:**

- Modify: `System-Architecture.md`

### Desired documentation change

Document the new state precisely:

- `hub_runtime_bridge.HubFacade` owns HubRuntimeBridge runtime internals.
- `app_shell.relay_service` remains as a compatibility adapter for route-facing legacy imports.
- Relay adapter no longer reaches into Hub facade internals or constructs Hub repository/router objects.
- Remaining temporary bridges to Room/Execution/Database are explicitly out of scope for this cleanup and will be removed during Room/Execution/database-service cleanup.

### Steps

- [ ] **Step 1: Update HubRuntimeBridge section**

In `System-Architecture.md`, update the HubRuntimeBridge section with wording equivalent to:

```markdown
`hub_runtime_bridge.HubFacade` owns hub connection management, relay dispatch,
agent sync, liveness, offline queue behavior, task ownership, and internal hub
response routing. `app_shell.relay_service` is a compatibility adapter for
legacy route imports and APIKey/request adaptation; it delegates Hub behavior
through the facade public methods and does not construct Hub repositories or
inspect `HubRuntimeBridgeDeps`.
```

- [ ] **Step 2: Update app-shell compatibility notes**

In the `app_shell` section, add wording equivalent to:

```markdown
`app_shell.relay_service` remains during migration as the route-facing relay
adapter. It may still host temporary adapters to legacy database/response-handler
surfaces, but Hub-owned liveness, stream binding, agent sync, ownership, and
internal response router setup are handled by `HubFacade`.
```

- [ ] **Step 3: Commit this task**

```bash
git add System-Architecture.md
git commit -m "docs: clarify hub relay facade boundary"
```

---

## Task 8: Final targeted backend verification

**Files:**

- No source file changes expected.

### Steps

- [ ] **Step 1: Run HubRuntimeBridge focused tests**

Run:

```bash
uv run pytest tests/test_hub_runtime_bridge_facade.py tests/test_hub_runtime_bridge_protocols.py -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 2: Run relay-focused tests**

Run:

```bash
uv run pytest tests/test_relay_service_hub_facade_adapter.py tests/test_api_relay.py tests -k "relay or hub_runtime_bridge" -q
uv run pytest tests/test_heartbeat_fixes.py -q
```

Expected:

```text
all selected tests pass
```

If this command selects duplicate tests, that is acceptable. If it selects no additional relay tests beyond `test_relay_service_hub_facade_adapter.py`, record that relay coverage is limited to the new adapter tests plus hub facade tests.

- [ ] **Step 3: Confirm frontend contract did not change**

Do not edit frontend code. Confirm by reviewing changed files only:

```text
Expected changed source areas:
- hub_runtime_bridge/facade.py
- app_shell/relay_service.py
- tests/test_hub_runtime_bridge_facade.py
- tests/test_relay_service_hub_facade_adapter.py
- tests/test_api_relay.py
- tests/test_heartbeat_fixes.py
- System-Architecture.md
```

There should be no route path, request model, response model, or frontend file changes.

- [ ] **Step 4: Commit verification-only changes if any**

If no files changed during verification, do not commit.

If a small fix was needed during verification, commit it with:

```bash
git add hub_runtime_bridge/facade.py app_shell/relay_service.py tests/test_hub_runtime_bridge_facade.py tests/test_relay_service_hub_facade_adapter.py tests/test_api_relay.py tests/test_heartbeat_fixes.py System-Architecture.md
git commit -m "refactor(hub): complete relay facade cleanup"
```

---

## Completion Criteria

This cleanup is complete when all of these are true:

- `app_shell/relay_service.py` contains no `self._facade.deps` references.
- `app_shell/relay_service.py` does not import `HubMongoRepository`.
- `app_shell/relay_service.py` does not import `HubInternalResponseRouter`.
- `HubFacade` exposes explicit runtime binding methods for streams and agent registry writer.
- `HubFacade` owns internal response router construction from a supplied sink.
- `HubFacade` owns stream-backed liveness sweep and recovery.
- Relay API behavior is unchanged.
- No frontend files are modified.
- Architecture docs describe the current HubRuntimeBridge boundary.

---

## Follow-Up Work After This Plan

Do these in separate plans, not in this one:

- Remove `execution.orchestration.room_message_center` external exposure.
- Move `_RelayPublishAuthorizationReader` to a Room-owned protocol implementation.
- Move `_RelayCancellationReader` to Room/Execution-owned protocol implementation.
- Replace `_LegacyPublishSink` with direct `EventPublisher.emit_internal()` / `ExecutionFacade` consumption once Execution cleanup is complete.
- Remove `app_shell.database_service` broad service locator after Room, Agent, Memory, Execution, and Hub repositories fully own their queries.
- Let API relay routes depend directly on `HubManagement` / `HubStatusReader` from container wiring once `RelayService` compatibility is no longer needed.
