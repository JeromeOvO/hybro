import ast
import inspect
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from common.dto import AgentEvent, ExecutionAck, ExecutionRequest, HITLRequest, RunInfo

ROOT = Path(__file__).resolve().parents[1]


def test_execution_request_matches_send_message_payload_shape():
    req = ExecutionRequest(
        room_id="room-1",
        sender_id="user-1",
        sender_name="User",
        message={"message_content": {"message_text": "hello"}},
        attachments=[{"file_id": "file-1"}],
        inline_file_ids=["file-inline"],
        client_request_id="cr-1",
        target_group="room_team",
        target_group_id=None,
        mentioned_agent_ids=["agent-1"],
        mode="supervisor",
    )
    assert req.message["message_content"]["message_text"] == "hello"
    assert req.client_request_id == "cr-1"


def test_run_info_preserves_active_run_ref_shape():
    info = RunInfo(
        run_id="run-1",
        room_id="room-1",
        state="processing",
        trigger_message_id="user-msg-1",
        agent_id="agent-1",
        seq=3,
    )
    assert info.trigger_message_id == "user-msg-1"


def test_hitl_request_preserves_pending_api_shape():
    req = HITLRequest(
        request_id="hitl-1",
        room_id="room-1",
        user_message_id="user-msg-1",
        message_id="display-msg-1",
        source="supervisor",
        prompt="Choose",
        prompt_type="choice",
        choices=["A", "B"],
        agent_id="agent-1",
        agent_name="Researcher",
        display_message_id="display-msg-1",
        group_id="group-1",
        group_total=2,
        group_index=1,
        status="pending",
    )
    assert req.message_id == "display-msg-1"
    assert req.choices == ["A", "B"]


def test_hitl_request_populates_message_id_from_display_or_continuation_or_user_message():
    display = HITLRequest(
        request_id="hitl-1",
        room_id="room-1",
        user_message_id="user-msg-1",
        display_message_id="display-msg-1",
        source="agent",
        prompt="Choose",
    )
    continuation = HITLRequest(
        request_id="hitl-2",
        room_id="room-1",
        user_message_id="user-msg-1",
        continuation_message_id="cont-msg-1",
        source="agent",
        prompt="Choose",
    )
    fallback = HITLRequest(
        request_id="hitl-3",
        room_id="room-1",
        user_message_id="user-msg-1",
        source="agent",
        prompt="Choose",
    )

    assert display.message_id == "display-msg-1"
    assert continuation.message_id == "cont-msg-1"
    assert fallback.message_id == "user-msg-1"


def test_execution_request_preserves_missing_message_as_none():
    req = ExecutionRequest(
        room_id="room-1",
        sender_id="user-1",
        message=None,
    )
    assert req.message is None


def test_execution_ack_preserves_missing_message_error_shape():
    ack = ExecutionAck(
        message_id=None,
        message=None,
        success=False,
        error="Message is required",
        status_code=400,
    )
    assert ack.message_id is None
    assert ack.message is None


def test_common_agent_event_preserves_legacy_compatibility_shape():
    event = AgentEvent(
        room_id="r1",
        agent_id="a1",
        message_id="m1",
        event_type="final",
        payload={"text": "hello"},
        hub_id="hub-1",
    )
    assert event.event_type == "final"
    assert event.payload == {"text": "hello"}
    assert event.hub_id == "hub-1"


def test_execution_protocols_exported():
    from common.protocols import ExecutionEngine, HITLManager, HubAgentResponseSink

    assert ExecutionEngine.__name__ == "ExecutionEngine"
    assert HITLManager.__name__ == "HITLManager"
    assert HubAgentResponseSink.__name__ == "HubAgentResponseSink"
    assert getattr(ExecutionEngine, "_is_runtime_protocol", False)
    assert getattr(HITLManager, "_is_runtime_protocol", False)
    assert getattr(HubAgentResponseSink, "_is_runtime_protocol", False)


def test_execution_engine_cancel_requires_requested_by_user_id():
    from common.protocols import ExecutionEngine

    sig = inspect.signature(ExecutionEngine.cancel)
    assert "requested_by_user_id" in sig.parameters
    assert sig.parameters["requested_by_user_id"].kind == inspect.Parameter.KEYWORD_ONLY


def test_execution_engine_separates_execute_from_start_orchestration():
    from common.protocols import ExecutionEngine

    execute_sig = inspect.signature(ExecutionEngine.execute)
    start_sig = inspect.signature(ExecutionEngine.start_orchestration)
    assert list(execute_sig.parameters) == ["self", "request"]
    assert list(start_sig.parameters) == ["self", "request", "ack"]


def test_hitl_manager_sensitive_methods_require_room_id():
    from common.protocols import HITLManager

    resolve_sig = inspect.signature(HITLManager.resolve_hitl)
    cancel_sig = inspect.signature(HITLManager.cancel_hitl)
    assert "room_id" in resolve_sig.parameters
    assert "room_id" in cancel_sig.parameters


def test_hitl_manager_create_preserves_public_metadata_fields():
    from common.protocols import HITLManager

    sig = inspect.signature(HITLManager.create_hitl_request)
    for name in [
        "source_step_id",
        "agent_name",
        "display_message_id",
        "prompt_type",
        "choices",
        "group_id",
        "group_total",
        "group_index",
    ]:
        assert name in sig.parameters


def test_execution_facade_satisfies_task6_public_protocols():
    from common.protocols import ExecutionEngine, HITLManager, HubAgentResponseSink
    from execution.facade import ExecutionFacade

    facade = ExecutionFacade.__new__(ExecutionFacade)

    assert isinstance(facade, ExecutionEngine)
    assert isinstance(facade, HITLManager)
    assert isinstance(facade, HubAgentResponseSink)


def test_execution_boundary_temporary_legacy_import_inventory_does_not_expand():
    legacy_prefixes = {
        "a2a",
        "api",
        "container",
        "database",
        "delivery",
        "fastapi",
        "main",
        "modules",
        "services",
    }
    expected = {
        "execution/dispatch/transports/webhook.py": {
            "fastapi",
        },
    }
    actual: dict[str, set[str]] = {}
    for path in sorted((ROOT / "execution").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(), filename=rel)
        type_checking_lines: set[int] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Name)
                and node.test.id == "TYPE_CHECKING"
            ):
                for child in ast.walk(node):
                    if hasattr(child, "lineno"):
                        type_checking_lines.add(child.lineno)
        imported_names: set[str] = set()
        for node in ast.walk(tree):
            if hasattr(node, "lineno") and node.lineno in type_checking_lines:
                continue
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in legacy_prefixes:
                    imported_names.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in legacy_prefixes:
                        imported_names.add(alias.name)
        if imported_names:
            actual[rel] = imported_names

    assert actual == expected


def test_execution_scaffold_adapters_are_available():
    from execution.dispatch.task_notifications import TaskNotificationAdapter
    from execution.hitl.factory import create_hitl_service
    from execution.orchestration.factory import (
        BoundRoomMessageCenterProxy,
        create_room_message_center,
        room_message_center,
    )
    from execution.state.locking import RoomLockManager

    assert TaskNotificationAdapter.__name__ == "TaskNotificationAdapter"
    assert BoundRoomMessageCenterProxy.__name__ == "BoundRoomMessageCenterProxy"
    assert RoomLockManager.__name__ == "RoomLockManager"
    db = object()
    hitl = create_hitl_service(store=db)
    assert hitl._store is db
    runtime = create_room_message_center(database_service=db, debate_rounds=7)
    assert runtime._store is db
    assert runtime.debate_rounds == 7
    assert isinstance(room_message_center, BoundRoomMessageCenterProxy)


def test_hitl_factory_does_not_accept_legacy_database_aliases():
    from execution.hitl.factory import create_hitl_service

    db = object()
    with pytest.raises(TypeError, match="database_service"):
        create_hitl_service(database_service=db)
    with pytest.raises(TypeError, match="db_service"):
        create_hitl_service(db_service=db)


def test_room_message_center_factory_propagates_overrides_to_children():
    from execution.orchestration.factory import create_room_message_center

    deps = {
        name: object()
        for name in [
            "database_service",
            "sse_manager",
            "room_services",
            "notification_service",
            "a2a_service",
            "task_service",
            "agent_resolver_service",
            "room_memory_service",
            "debate_service",
            "rate_limit_service",
            "room_supervisor_service",
            "room_coordinator_service",
            "summary_service",
            "hitl_coordinator",
            "task_notifications",
        ]
    }
    runtime = create_room_message_center(**deps, debate_rounds=5)

    assert runtime._store is deps["database_service"]
    assert runtime.sse_manager is deps["sse_manager"]
    assert runtime.room_runtime is deps["room_services"]
    assert runtime.summary_service is deps["summary_service"]
    assert runtime.tsm.room_runtime is deps["room_services"]
    assert runtime.tsm.notification_service is deps["notification_service"]
    assert runtime.agent_dispatcher._message_writer is deps["database_service"]
    assert runtime.agent_dispatcher._agent_lookup is deps["database_service"]
    assert runtime.agent_dispatcher._agent_group_reader is deps["database_service"]
    assert runtime.debate_rounds == 5
    assert runtime.supervisor_executor.debate_rounds == 5
    assert runtime.agent_dispatcher.agent_resolver is deps["agent_resolver_service"]
    assert runtime.agent_response_handler._message_writer is deps["database_service"]
    assert runtime.agent_response_handler._task_writer is deps["database_service"]
    assert runtime.agent_response_handler._sse is deps["sse_manager"]
    assert runtime.direct_transport._message_reader is deps["database_service"]
    assert runtime.direct_transport._artifact_store is deps["database_service"]
    assert runtime.direct_transport._task_updater is deps["database_service"]
    assert runtime.direct_transport.sse_manager is deps["sse_manager"]
    assert runtime.direct_transport.a2a_service is deps["a2a_service"]
    assert runtime.direct_transport.task_service is deps["task_service"]
    assert (
        runtime.agent_message_processor._room_memory_reader is deps["database_service"]
    )
    assert runtime.agent_message_processor._task_tracker is deps["database_service"]
    assert runtime.agent_message_processor.sse_manager is deps["sse_manager"]
    assert runtime.queue_executor._store is deps["database_service"]
    assert runtime.queue_executor.sse_manager is deps["sse_manager"]
    assert runtime.queue_executor.room_runtime is deps["room_services"]
    assert runtime.queue_executor.hitl_coordinator is deps["hitl_coordinator"]
    assert runtime.supervisor_executor._store is deps["database_service"]
    assert runtime.supervisor_executor.sse_manager is deps["sse_manager"]
    assert runtime.supervisor_executor.room_runtime is deps["room_services"]
    assert runtime.supervisor_executor.hitl_coordinator is deps["hitl_coordinator"]
    assert runtime.agent_response_handler.hitl_coordinator is deps["hitl_coordinator"]
    assert runtime.task_notifications is deps["task_notifications"]


def test_room_message_center_factory_owns_default_dependency_wiring():
    import inspect
    from unittest.mock import MagicMock

    from execution.orchestration.factory import create_room_message_center
    from execution.orchestration.room_message_center import RoomMessageCenter

    assert "globals()" not in inspect.getsource(RoomMessageCenter.__init__)

    deps = {
        "room_services": MagicMock(),
        "database_service": MagicMock(),
        "sse_manager": MagicMock(),
        "room_coordinator_service": MagicMock(),
        "summary_service": MagicMock(),
        "notification_service": MagicMock(),
        "agent_resolver_service": MagicMock(),
        "a2a_service": MagicMock(),
        "task_service": MagicMock(),
        "room_memory_service": MagicMock(),
        "debate_service": MagicMock(),
        "rate_limit_service": MagicMock(),
        "room_supervisor_service": MagicMock(),
    }
    runtime = create_room_message_center(**deps, debate_rounds=6)

    assert runtime._store is deps["database_service"]
    assert runtime.sse_manager is deps["sse_manager"]
    assert runtime.room_runtime is deps["room_services"]
    assert runtime.debate_rounds == 6


def test_room_message_center_constructor_requires_explicit_dependencies():
    from execution.orchestration.room_message_center import RoomMessageCenter

    with pytest.raises(TypeError):
        RoomMessageCenter()


def test_room_message_center_uses_common_room_lock_protocol():
    from pathlib import Path
    from typing import get_type_hints

    from common.protocols import RoomDistributedLock
    from execution.orchestration.room_message_center import RoomMessageCenter

    source = Path("execution/orchestration/room_message_center.py").read_text()
    hints = get_type_hints(RoomMessageCenter.set_room_distributed_lock)

    assert "AppShellRedisService" not in source
    assert "._client" not in source
    assert hints["room_lock"] == RoomDistributedLock | None
    assert (
        get_type_hints(RoomMessageCenter.set_redis_service)["redis_service"]
        == RoomDistributedLock | None
    )


def test_app_shell_room_lock_uses_public_redis_protocol_surface():
    from pathlib import Path
    from typing import get_type_hints

    from app_shell.room_lock import RedisLockStore, RedisRoomDistributedLock

    source = Path("app_shell/room_lock.py").read_text()
    init_hints = get_type_hints(RedisRoomDistributedLock.__init__)

    assert "Any" not in source
    assert "._client" not in source
    assert "_client" not in source
    assert ".set_nx(" in source
    assert init_hints["redis_service"] == RedisLockStore | None
    acquire_hints = get_type_hints(RedisRoomDistributedLock.acquire)
    assert acquire_hints["ttl"] is int


class _FakeCursor:
    def __init__(self, docs=None, error: Exception | None = None):
        self.docs = docs or []
        self.error = error
        self.sort_calls = []
        self.limit_calls = []

    def sort(self, *args):
        self.sort_calls.append(args)
        return self

    def limit(self, *args):
        self.limit_calls.append(args)
        return self

    async def to_list(self, *, length):
        if self.error:
            raise self.error
        return self.docs


class _FakeRunsCollection:
    def __init__(self, *, docs=None, find_error: Exception | None = None):
        self.docs = docs or []
        self.find_error = find_error
        self.find_calls = []
        self.get_active_calls = []
        self.find_one = AsyncMock()
        self.cursor = _FakeCursor(self.docs, error=find_error)

    def find(self, query):
        self.find_calls.append(query)
        return self.cursor

    async def get_active_for_room(self, room_id: str) -> list[dict]:
        self.get_active_calls.append(room_id)
        return list(self.docs)


@pytest.mark.asyncio
async def test_run_query_adapter_filters_non_terminal_runs_and_preserves_trigger_message_id():
    from execution.run_queries import RunQueryAdapter

    collection = _FakeRunsCollection(
        docs=[
            {
                "run_id": "run-1",
                "room_id": "room-1",
                "state": "processing",
                "trigger_message_id": "user-msg-1",
                "agent_id": "agent-1",
                "seq": 4,
            }
        ]
    )
    adapter = RunQueryAdapter(collection)

    runs = await adapter.get_runs_for_room("room-1")

    assert collection.get_active_calls == ["room-1"]
    assert runs[0].trigger_message_id == "user-msg-1"
    assert runs[0].seq == 4


@pytest.mark.asyncio
async def test_run_query_adapter_returns_empty_list_on_collection_errors():
    from execution.run_queries import RunQueryAdapter

    collection = _FakeRunsCollection(find_error=RuntimeError("db down"))
    adapter = RunQueryAdapter(collection)

    assert await adapter.get_runs_for_room("room-1") == []


@pytest.mark.asyncio
async def test_run_query_adapter_get_run_returns_none_on_lookup_errors():
    from execution.run_queries import RunQueryAdapter

    collection = _FakeRunsCollection()
    collection.find_one.side_effect = RuntimeError("db down")
    adapter = RunQueryAdapter(collection)

    assert await adapter.get_run("run-1") is None


@pytest.mark.asyncio
async def test_run_query_adapter_get_run_preserves_doc_fields():
    from execution.run_queries import RunQueryAdapter

    collection = _FakeRunsCollection()
    collection.find_one.return_value = {
        "run_id": "run-1",
        "room_id": "room-1",
        "state": "awaiting_input",
        "trigger_message_id": "user-msg-1",
        "error_message": "waiting",
    }
    adapter = RunQueryAdapter(collection)

    run = await adapter.get_run("run-1")

    assert run is not None
    assert run.trigger_message_id == "user-msg-1"
    assert run.error == "waiting"
