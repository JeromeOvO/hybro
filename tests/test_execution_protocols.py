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
    hitl_persistence = object()
    hitl = create_hitl_service(persistence=hitl_persistence)
    assert hitl._persistence is hitl_persistence

    deps = _make_room_message_center_port_deps()
    runtime = create_room_message_center(**deps, debate_rounds=7)
    assert runtime.message_reader is deps["message_reader"]
    assert runtime.message_writer is deps["message_writer"]
    assert runtime.task_state_store is deps["task_state_store"]
    assert runtime.continuation_store is deps["continuation_store"]
    assert runtime.debate_rounds == 7
    assert isinstance(room_message_center, BoundRoomMessageCenterProxy)


def test_hitl_factory_rejects_legacy_runtime_aliases():
    from execution.hitl.factory import create_hitl_service

    legacy = object()
    for name in ["store", "database_service", "db_service", "a2a" + "_" + "service"]:
        with pytest.raises(TypeError, match=name):
            create_hitl_service(**{name: legacy})


def _make_room_message_center_port_deps():
    from unittest.mock import MagicMock

    return {
        "room_runtime": MagicMock(),
        "message_reader": MagicMock(),
        "message_writer": MagicMock(),
        "task_state_store": MagicMock(),
        "continuation_store": MagicMock(),
        "agent_lookup": MagicMock(),
        "agent_group_reader": MagicMock(),
        "room_reader": MagicMock(),
        "room_writer": MagicMock(),
        "memory_reader": MagicMock(),
        "memory_writer": MagicMock(),
        "hitl_reader": MagicMock(),
        "delivery": MagicMock(),
        "event_publisher": MagicMock(),
        "coordinator": MagicMock(),
        "summary_service": MagicMock(),
        "task_notifier": MagicMock(),
        "agent_resolver_service": MagicMock(),
        "a2a_transport": MagicMock(),
        "remote_task_reader": MagicMock(),
        "room_memory": MagicMock(),
        "debate_prompt_injector": MagicMock(),
        "rate_limit_service": MagicMock(),
        "room_supervisor_service": MagicMock(),
        "context_memory_runtime": MagicMock(),
        "context_compaction": MagicMock(),
        "hitl_coordinator": MagicMock(),
        "task_notifications": MagicMock(),
        "object_storage": MagicMock(),
    }


def test_room_message_center_factory_propagates_overrides_to_children():
    from execution.orchestration.factory import create_room_message_center

    deps = _make_room_message_center_port_deps()
    runtime = create_room_message_center(
        **deps,
        debate_rounds=5,
        orphan_threshold_minutes=9,
    )

    assert runtime.room_runtime is deps["room_runtime"]
    assert runtime.message_reader is deps["message_reader"]
    assert runtime.message_writer is deps["message_writer"]
    assert runtime.task_state_store is deps["task_state_store"]
    assert runtime.continuation_store is deps["continuation_store"]
    assert runtime.room_reader is deps["room_reader"]
    assert runtime.room_writer is deps["room_writer"]
    assert runtime.memory_reader is deps["memory_reader"]
    assert runtime.memory_writer is deps["memory_writer"]
    assert runtime.hitl_reader is deps["hitl_reader"]
    assert runtime.delivery is deps["delivery"]
    assert runtime.event_publisher is deps["event_publisher"]
    assert runtime.coordinator is deps["coordinator"]
    assert runtime.summary_service is deps["summary_service"]
    assert runtime.room_memory is deps["room_memory"]
    assert runtime.tsm.room_runtime is deps["room_runtime"]
    assert runtime.tsm.task_notifier is deps["task_notifier"]
    assert runtime.agent_dispatcher._message_writer is deps["message_writer"]
    assert runtime.agent_dispatcher._agent_lookup is deps["agent_lookup"]
    assert runtime.agent_dispatcher._agent_group_reader is deps["agent_group_reader"]
    assert runtime.debate_rounds == 5
    assert runtime.orphan_threshold_minutes == 9
    assert runtime.supervisor_executor.debate_rounds == 5
    assert runtime.agent_dispatcher.agent_resolver is deps["agent_resolver_service"]
    assert runtime.agent_response_handler._message_writer is deps["message_writer"]
    assert runtime.agent_response_handler._task_writer is deps["message_writer"]
    assert (
        runtime.agent_response_handler._continuation_store is deps["continuation_store"]
    )
    client_request_resolver = runtime.agent_response_handler._client_request_resolver
    assert (
        client_request_resolver.resolve_client_request_id_for_message_id
        is deps["task_state_store"].resolve_client_request_id_for_message_id
    )
    assert (
        client_request_resolver.resolve_client_request_id_for_agent_message
        is deps["task_state_store"].resolve_client_request_id_for_agent_message
    )
    assert (
        client_request_resolver.get_room_agent_message_by_message_id
        is deps["message_reader"].get_room_agent_message_by_message_id
    )
    assert runtime.agent_response_handler._room_reader is deps["room_reader"]
    assert runtime.agent_response_handler._hitl_reader is deps["hitl_reader"]
    assert runtime.agent_response_handler._delivery is deps["delivery"]
    assert runtime.direct_transport._message_reader is deps["message_reader"]
    assert runtime.direct_transport._artifact_store is deps["message_writer"]
    assert runtime.direct_transport._task_updater is deps["task_state_store"]
    assert runtime.direct_transport.delivery is deps["delivery"]
    assert runtime.direct_transport.a2a_transport is deps["a2a_transport"]
    assert runtime.direct_transport.remote_task_reader is deps["remote_task_reader"]
    assert runtime.agent_message_processor._room_memory_reader is deps["memory_reader"]
    assert runtime.agent_message_processor._task_tracker is deps["task_state_store"]
    assert runtime.agent_message_processor.delivery is deps["delivery"]
    assert runtime.queue_executor.task_state_store is deps["task_state_store"]
    assert runtime.queue_executor.message_reader is deps["message_reader"]
    assert runtime.queue_executor.message_writer is deps["message_writer"]
    assert runtime.queue_executor.delivery is deps["delivery"]
    assert runtime.queue_executor.room_runtime is deps["room_runtime"]
    assert runtime.queue_executor.event_publisher is deps["event_publisher"]
    assert (
        runtime.queue_executor.debate_prompt_injector is deps["debate_prompt_injector"]
    )
    assert runtime.queue_executor.hitl_coordinator is deps["hitl_coordinator"]
    assert runtime.supervisor_executor.task_state_store is deps["task_state_store"]
    assert runtime.supervisor_executor.message_reader is deps["message_reader"]
    assert runtime.supervisor_executor.message_writer is deps["message_writer"]
    assert runtime.supervisor_executor.delivery is deps["delivery"]
    assert runtime.supervisor_executor.room_runtime is deps["room_runtime"]
    assert runtime.supervisor_executor.event_publisher is deps["event_publisher"]
    assert runtime.supervisor_executor.hitl_coordinator is deps["hitl_coordinator"]
    assert runtime.agent_response_handler.hitl_coordinator is deps["hitl_coordinator"]
    assert runtime.hitl_coordinator is deps["hitl_coordinator"]
    assert runtime.task_notifications is deps["task_notifications"]
    assert runtime.context_memory_runtime is deps["context_memory_runtime"]
    assert runtime.context_compaction is deps["context_compaction"]
    assert runtime.direct_transport.object_storage is deps["object_storage"]


def test_room_message_center_factory_requires_event_publisher():
    from execution.orchestration.factory import create_room_message_center

    deps = _make_room_message_center_port_deps()
    deps.pop("event_publisher")

    with pytest.raises(RuntimeError, match="event_publisher"):
        create_room_message_center(**deps, debate_rounds=5)


def test_room_message_center_factory_owns_default_dependency_wiring():
    import inspect

    from execution.orchestration.factory import create_room_message_center
    from execution.orchestration.room_message_center import RoomMessageCenter

    factory_source = inspect.getsource(create_room_message_center)
    assert "globals()" not in factory_source
    assert "database_service" not in factory_source
    assert "db_service" not in factory_source
    assert "sse_manager" not in factory_source
    assert "room_services" not in factory_source
    assert "a2a_service" not in factory_source
    assert "room_memory_service" not in factory_source
    assert "room_coordinator_service" not in factory_source
    assert "task_service" not in factory_source
    assert "globals()" not in inspect.getsource(RoomMessageCenter.__init__)

    deps = _make_room_message_center_port_deps()
    runtime = create_room_message_center(**deps, debate_rounds=6)

    assert runtime.message_reader is deps["message_reader"]
    assert runtime.delivery is deps["delivery"]
    assert runtime.room_runtime is deps["room_runtime"]
    assert runtime.debate_rounds == 6
    assert runtime.context_memory_runtime is deps["context_memory_runtime"]
    assert runtime.context_compaction is deps["context_compaction"]


def test_container_wires_execution_with_focused_port_names():
    source = (ROOT / "container.py").read_text()
    tree = ast.parse(source)

    def call_name(node: ast.Call) -> str | None:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        return None

    def calls_named(name: str) -> list[ast.Call]:
        return [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and call_name(node) == name
        ]

    def keyword_names(call: ast.Call) -> set[str]:
        return {kw.arg for kw in call.keywords if kw.arg is not None}

    def keyword_value(call: ast.Call, name: str) -> ast.expr:
        for kw in call.keywords:
            if kw.arg == name:
                return kw.value
        raise AssertionError(f"keyword {name!r} not found")

    room_center_calls = calls_named("create_room_message_center")
    assert len(room_center_calls) == 1
    room_center_call = room_center_calls[0]
    room_center_keywords = keyword_names(room_center_call)
    focused_keywords = {
        "room_runtime",
        "message_reader",
        "message_writer",
        "task_state_store",
        "continuation_store",
        "agent_lookup",
        "agent_group_reader",
        "room_reader",
        "room_writer",
        "memory_reader",
        "memory_writer",
        "hitl_reader",
        "delivery",
        "event_publisher",
        "coordinator",
        "a2a_transport",
        "remote_task_reader",
        "room_memory",
        "context_compaction",
    }
    legacy_keywords = {
        "store",
        "sse_manager",
        "room_services",
        "room_coordinator_service",
        "a2a_service",
        "task_service",
        "room_memory_service",
        "compaction_service",
    }
    assert focused_keywords <= room_center_keywords
    assert room_center_keywords.isdisjoint(legacy_keywords)
    expected_room_center_adapter_names = {
        "room_runtime": "execution_room_runtime",
        "delivery": "execution_delivery",
        "a2a_transport": "execution_a2a_transport",
        "remote_task_reader": "execution_remote_task_reader",
        "room_memory": "execution_room_memory",
        "event_publisher": "_delivery_deps.event_publisher",
        "context_compaction": "context_memory_facade",
    }
    for keyword, expected_name in expected_room_center_adapter_names.items():
        value = keyword_value(room_center_call, keyword)
        if "." in expected_name:
            assert isinstance(value, ast.Attribute)
            assert isinstance(value.value, ast.Name)
            assert f"{value.value.id}.{value.attr}" == expected_name
        else:
            assert isinstance(value, ast.Name)
            assert value.id == expected_name
    coordinator_value = keyword_value(room_center_call, "coordinator")
    assert isinstance(coordinator_value, ast.Name)
    assert coordinator_value.id == "execution_coordinator"

    assert "get_quoted_snippet_by_id" in source
    assert "_room_deps.room_quote_repository.get_by_id" in source
    assert "QuotedSnippet.model_validate" in source
    assert "execution_inquiry_agent_messages_by_related_message_id" in source
    assert (
        "RoomCenterAgentMessageRequest(related_message_id=related_message_id)" in source
    )
    assert (
        ('from ' + 'app_' + 'shell' + '.') + "compaction_service import compaction_service" not in source
    )
    assert "compaction_service.bind_content_storage" not in source
    assert "compaction_service.bind_room_memory_reader" not in source
    assert "compaction_service.bind_facade" not in source

    rmc_source = (
        ROOT / "execution" / "orchestration" / "room_message_center.py"
    ).read_text()
    assert (
        "RoomCenterAgentMessageRequest(related_message_id=room_user_message_id)"
        not in rmc_source
    )
    assert (
        "inquiry_agent_messages_by_related_message_id(\n"
        "                room_user_message_id\n" in rmc_source
    )

    hitl_call = calls_named("create_hitl_service")[0]
    hitl_keywords = keyword_names(hitl_call)
    assert {
        "persistence",
        "delivery",
        "agent_reply",
        "continuation",
        "task_notifications",
    } <= hitl_keywords
    assert hitl_keywords.isdisjoint({"store", "a2a_service"})

    task_notification_call = calls_named("bind_task_notification_runtime")[0]
    task_notification_keywords = keyword_names(task_notification_call)
    assert "delivery" in task_notification_keywords
    assert "sse_manager" not in task_notification_keywords
    task_notification_delivery = keyword_value(task_notification_call, "delivery")
    assert isinstance(task_notification_delivery, ast.Name)
    assert task_notification_delivery.id == "execution_delivery"

    cleanup_call = calls_named("AgentTaskCleanupAdapter")[0]
    cleanup_keywords = keyword_names(cleanup_call)
    assert "message_task_store" in cleanup_keywords
    assert "store" not in cleanup_keywords

    webhook_handler_call = calls_named("AgentResponseHandler")[0]
    webhook_keywords = keyword_names(webhook_handler_call)
    assert "delivery" in webhook_keywords
    assert "sse_manager" not in webhook_keywords
    delivery_value = keyword_value(webhook_handler_call, "delivery")
    assert isinstance(delivery_value, ast.Name)
    assert delivery_value.id == "execution_delivery"


def test_room_message_center_constructor_requires_explicit_dependencies():
    from execution.orchestration.room_message_center import RoomMessageCenter

    with pytest.raises(TypeError):
        RoomMessageCenter()

    params = inspect.signature(RoomMessageCenter.__init__).parameters
    legacy_names = {
        "store",
        "sse_manager",
        "room_services",
        "a2a_service",
        "task_service",
        "room_memory_service",
        "room_coordinator_service",
        "compaction_service",
    }
    assert legacy_names.isdisjoint(params)


def test_room_message_center_constructor_requires_event_publisher():
    from execution.orchestration.room_message_center import RoomMessageCenter

    deps = _make_room_message_center_port_deps()
    deps["event_publisher"] = None

    with pytest.raises(RuntimeError, match="event_publisher"):
        RoomMessageCenter(**deps, debate_rounds=5)


def test_room_message_center_uses_common_room_lock_protocol():
    from pathlib import Path
    from typing import get_type_hints

    from common.protocols import RoomDistributedLock
    from execution.orchestration.room_message_center import RoomMessageCenter

    source = Path("execution/orchestration/room_message_center.py").read_text()
    hints = get_type_hints(RoomMessageCenter.set_room_distributed_lock)

    assert ('App' + 'Shell' + 'RedisService') not in source
    assert "._client" not in source
    assert hints["room_lock"] == RoomDistributedLock | None
    assert (
        get_type_hints(RoomMessageCenter.set_redis_service)["redis_service"]
        == RoomDistributedLock | None
    )


def test_dal_room_lock_uses_dal_redis_owner_module():
    from typing import get_type_hints

    from common.protocols import RoomDistributedLock
    from dal.redis.lock import RoomRedisDistributedLock
    from execution.orchestration.room_message_center import RoomMessageCenter

    hints = get_type_hints(RoomMessageCenter.set_room_distributed_lock)
    assert hints["room_lock"] == RoomDistributedLock | None
    acquire_hints = get_type_hints(RoomRedisDistributedLock.acquire)
    assert acquire_hints["ttl"] is int
    assert RoomRedisDistributedLock.__module__ == "dal.redis.lock"


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
