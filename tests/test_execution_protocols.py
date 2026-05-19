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
        "execution/dispatch/agent_dispatcher.py": {
            "services.agent_resolver_service",
            "services.database_service",
        },
        "execution/dispatch/agent_message_processor.py": {
            "services.agent_health_service",
            "services.database_service",
            "services.room_services",
            "services.sse_services",
        },
        "execution/dispatch/dispatch_middleware.py": {"a2a.types"},
        "execution/dispatch/middleware/cloud_health.py": {
            "services.agent_health_service",
        },
        "execution/dispatch/response_handler.py": {
            "a2a.types",
            "services.database_service",
            "services.notification_service",
            "services.sse_services",
            "services.task_notification_service",
        },
        "execution/dispatch/transports/direct.py": {
            "a2a.types",
            "services.a2a_service",
            "services.agent_capability_issue_service",
            "services.s3_service",
        },
        "execution/dispatch/transports/relay.py": {
            "services.database_service",
            "services.sse_services",
        },
        "execution/dispatch/transports/webhook.py": {
            "a2a.types",
            "fastapi",
            "services.database_service",
            "services.task_notification_service",
        },
        "execution/orchestration/queue_executor.py": {
            "a2a.types",
            "services.a2a_service",
            "services.database_service",
            "services.debate_service",
            "services.memory_service",
            "services.rate_limit_service",
            "services.room_services",
            "services.sse_services",
        },
        "execution/orchestration/room_message_center.py": {
            "a2a.types",
            "services.a2a_service",
            "services.agent_resolver_service",
            "services.compaction_service",
            "services.context_assembly_service",
            "services.database_service",
            "services.debate_service",
            "services.memory_search_service",
            "services.memory_service",
            "services.notification_service",
            "services.openai_service",
            "services.rate_limit_service",
            "services.room_coordinator_service",
            "services.room_services",
            "services.room_supervisor_service",
            "services.sse_services",
            "services.task_service",
        },
        "execution/orchestration/supervisor_executor.py": {
            "services.database_service",
            "services.memory_service",
            "services.rate_limit_service",
            "services.room_coordinator_service",
            "services.room_services",
            "services.room_supervisor_service",
            "services.sse_services",
        },
        "execution/state/task_state_manager.py": {
            "a2a.types",
            "services.notification_service",
            "services.room_services",
        },
    }
    actual: dict[str, set[str]] = {}
    for path in sorted((ROOT / "execution").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(), filename=rel)
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in legacy_prefixes:
                    modules.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in legacy_prefixes:
                        modules.add(alias.name)
        if modules:
            actual[rel] = modules

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
    hitl = create_hitl_service(database_service=db)
    assert hitl._db_service is db
    runtime = create_room_message_center(database_service=db)
    assert runtime.database_service is db
    assert isinstance(room_message_center, BoundRoomMessageCenterProxy)


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
            "openai_service",
            "hitl_coordinator",
            "task_notifications",
        ]
    }
    runtime = create_room_message_center(**deps)

    assert runtime.database_service is deps["database_service"]
    assert runtime.sse_manager is deps["sse_manager"]
    assert runtime.room_services is deps["room_services"]
    assert runtime.openai_service is deps["openai_service"]
    assert runtime.tsm.room_services is deps["room_services"]
    assert runtime.tsm.notification_service is deps["notification_service"]
    assert runtime.agent_dispatcher.database_service is deps["database_service"]
    assert runtime.agent_dispatcher.agent_resolver is deps["agent_resolver_service"]
    assert runtime.agent_response_handler._db is deps["database_service"]
    assert runtime.agent_response_handler._sse is deps["sse_manager"]
    assert runtime.direct_transport.database_service is deps["database_service"]
    assert runtime.direct_transport.sse_manager is deps["sse_manager"]
    assert runtime.direct_transport.a2a_service is deps["a2a_service"]
    assert runtime.direct_transport.task_service is deps["task_service"]
    assert runtime.agent_message_processor.database_service is deps["database_service"]
    assert runtime.agent_message_processor.sse_manager is deps["sse_manager"]
    assert runtime.queue_executor.database_service is deps["database_service"]
    assert runtime.queue_executor.sse_manager is deps["sse_manager"]
    assert runtime.queue_executor.room_services is deps["room_services"]
    assert runtime.queue_executor.hitl_coordinator is deps["hitl_coordinator"]
    assert runtime.supervisor_executor.database_service is deps["database_service"]
    assert runtime.supervisor_executor.sse_manager is deps["sse_manager"]
    assert runtime.supervisor_executor.room_services is deps["room_services"]
    assert runtime.supervisor_executor.hitl_coordinator is deps["hitl_coordinator"]
    assert runtime.agent_response_handler.hitl_coordinator is deps["hitl_coordinator"]
    assert runtime.task_notifications is deps["task_notifications"]


def test_room_message_center_factory_owns_default_dependency_wiring():
    import inspect

    from execution.orchestration.factory import create_room_message_center
    from execution.orchestration.room_message_center import RoomMessageCenter

    assert "globals()" not in inspect.getsource(RoomMessageCenter.__init__)

    runtime = create_room_message_center()

    assert runtime.database_service is not None
    assert runtime.sse_manager is not None
    assert runtime.room_services is not None


def test_room_message_center_constructor_requires_explicit_dependencies():
    from execution.orchestration.room_message_center import RoomMessageCenter

    with pytest.raises(TypeError):
        RoomMessageCenter()


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
        self.find_one = AsyncMock()
        self.cursor = _FakeCursor(self.docs, error=find_error)

    def find(self, query):
        self.find_calls.append(query)
        return self.cursor


@pytest.mark.asyncio
async def test_run_query_adapter_filters_non_terminal_runs_and_preserves_trigger_message_id():
    from execution.run_queries import RunQueryAdapter
    from models.run import NON_TERMINAL_RUN_STATE_VALUES

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

    assert collection.find_calls == [
        {
            "room_id": "room-1",
            "state": {"$in": list(NON_TERMINAL_RUN_STATE_VALUES)},
        }
    ]
    assert collection.cursor.sort_calls == [("updated_at", -1)]
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
