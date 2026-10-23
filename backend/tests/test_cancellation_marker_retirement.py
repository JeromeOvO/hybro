"""C2 step 2b: the cancellation-marker path is gone and its health roles moved."""

from __future__ import annotations

import importlib
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

RETIRED_CANCELLATION_SETTINGS = {
    "cancellation_ttl_seconds",
    "cancellation_cache_maxsize",
    "redis_cancel_channel",
    "redis_cancel_key_prefix",
    "cs_backoff_base",
    "cs_backoff_max",
    "cs_backoff_factor",
    "cs_jitter_fraction",
    "orphan_threshold_minutes",
}


def _guard_kwargs(**overrides):
    kwargs = {
        "is_gunicorn": True,
        "delivery_pubsub_connected": True,
        "eventing_connected": True,
        "delivery_kv_connected": True,
        "redis_service_connected": True,
        "mongo_supports_transactions": True,
    }
    kwargs.update(overrides)
    return kwargs


@pytest.mark.parametrize(
    "module",
    # A leftover __pycache__ directory makes the bare package name importable as
    # an empty namespace package, so check modules that held code.
    ["execution.cancellation.runtime", "common.utils.cancellation"],
)
def test_cancellation_marker_modules_are_gone(module):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


def test_multi_worker_guard_requires_mongo_transactions():
    from container import check_multi_worker_safety

    with pytest.raises(RuntimeError, match="replica set or mongos"):
        check_multi_worker_safety(**_guard_kwargs(mongo_supports_transactions=False))


def test_multi_worker_guard_passes_with_redis_and_transactions():
    from container import check_multi_worker_safety

    check_multi_worker_safety(**_guard_kwargs())


def test_multi_worker_guard_is_inactive_outside_gunicorn():
    from container import check_multi_worker_safety

    check_multi_worker_safety(
        **_guard_kwargs(is_gunicorn=False, mongo_supports_transactions=False)
    )


def test_multi_worker_guard_no_longer_takes_a_change_stream_flag():
    from container import check_multi_worker_safety

    assert (
        "change_stream_connected"
        not in inspect.signature(check_multi_worker_safety).parameters
    )


def test_health_status_has_no_change_stream_key():
    from main import compute_health_status

    assert (
        "change_stream_connected"
        not in inspect.signature(compute_health_status).parameters
    )
    result = compute_health_status(
        delivery_pubsub_connected=True,
        delivery_kv_connected=True,
        redis_runtime_connected=True,
        redis_url="redis://localhost:6379/0",
        agent_search_index_ready=True,
        memory_search_index_ready=True,
        search_indexes_ready=True,
    )

    assert result["status_code"] == 200
    assert "change_stream_connected" not in result["body"]


@pytest.mark.asyncio
async def test_health_check_reads_delivery_pubsub_without_cancellation_runtime():
    from common.health_check import RuntimeHealthCheck

    compute_health_status = MagicMock(
        return_value={"body": {"status": "ok"}, "status_code": 200}
    )
    delivery = SimpleNamespace(
        refresh_health=AsyncMock(),
        delivery_pubsub_connected=True,
        delivery_kv_connected=True,
    )
    service = RuntimeHealthCheck(
        redis_url="redis://localhost:6379/0",
        compute_health_status=compute_health_status,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(delivery_facade=delivery))
    )

    await service.check(request)

    kwargs = compute_health_status.call_args.kwargs
    assert kwargs["delivery_pubsub_connected"] is True
    assert "change_stream_connected" not in kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("hello", "expected"),
    [
        ({"setName": "rs0"}, True),
        ({"msg": "isdbgrid"}, True),
        ({"isWritablePrimary": True}, False),
    ],
)
async def test_mongo_dal_reports_transaction_support(hello, expected):
    from dal.mongo import MongoDALImpl

    client = MagicMock()
    client.admin.command = AsyncMock(return_value=hello)
    dal = MongoDALImpl(client=client)

    assert await dal.supports_transactions() is expected
    client.admin.command.assert_awaited_once_with("hello")


@pytest.mark.asyncio
async def test_mongo_dal_transaction_probe_fails_closed():
    from dal.mongo import MongoDALImpl

    client = MagicMock()
    client.admin.command = AsyncMock(side_effect=RuntimeError("down"))

    assert await MongoDALImpl(client=client).supports_transactions() is False
    assert await MongoDALImpl().supports_transactions() is False


def test_cancellation_settings_are_retired():
    from common.config.loader import REMOVED_FIELDS, Settings

    assert RETIRED_CANCELLATION_SETTINGS <= REMOVED_FIELDS
    assert not RETIRED_CANCELLATION_SETTINGS & set(Settings.model_fields)


def test_conversation_runtime_needs_no_cancellation_control():
    from conversation.preflight import RoomMessagePreflightContext
    from conversation.runtime import ConversationRuntime

    runtime = ConversationRuntime()

    assert not hasattr(runtime, "bind_cancellation_control")
    assert "cancellation_control" not in runtime.missing_required_bindings()
    assert "token" not in RoomMessagePreflightContext.__dataclass_fields__


def test_execution_facade_takes_no_cancellation_marker_dependencies():
    from execution.facade import ExecutionFacade

    parameters = inspect.signature(ExecutionFacade).parameters
    for name in (
        "cancellation_state",
        "cancellation_repository",
        "cancellation_message_reader",
        "agent_task_cleanup",
        "orchestration_run_store",
    ):
        assert name not in parameters
    assert not hasattr(ExecutionFacade, "finalize_pending_cancellation")
    assert not hasattr(ExecutionFacade, "cancellation_service")


@pytest.mark.asyncio
async def test_execution_facade_cancel_requires_the_orchestrator_router():
    from execution.facade import ExecutionFacade

    facade = object.__new__(ExecutionFacade)
    facade._orchestrator_router = None

    with pytest.raises(RuntimeError, match="router"):
        await facade.cancel("room-1", "message-1", requested_by_user_id="user-1")


@pytest.mark.core
@pytest.mark.asyncio
async def test_execution_facade_cancel_routes_to_the_orchestrator():
    from execution.facade import ExecutionFacade

    facade = object.__new__(ExecutionFacade)
    router = MagicMock()
    router.route_cancellation_by_user_message = AsyncMock(return_value=True)
    facade._orchestrator_router = router
    facade._hitl_message_cancellation = MagicMock(
        cancel_requests_for_message=AsyncMock()
    )

    assert await facade.cancel("room-1", "message-1", requested_by_user_id="u") is True
    router.route_cancellation_by_user_message.assert_awaited_once()


def test_hitl_message_cancellation_adapter_lives_with_execution_adapters():
    from execution.adapters.hitl import HITLMessageCancellationAdapter

    assert HITLMessageCancellationAdapter.__module__ == "execution.adapters.hitl"


@pytest.mark.asyncio
async def test_hitl_message_cancellation_adapter_forwards_to_hitl_manager():
    from execution.adapters.hitl import HITLMessageCancellationAdapter

    manager = MagicMock(cancel_requests_for_message=AsyncMock())

    await HITLMessageCancellationAdapter(manager).cancel_requests_for_message("m-1")

    manager.cancel_requests_for_message.assert_awaited_once_with("m-1")


def test_stale_checker_no_longer_reconciles_cancellation_markers():
    from jobs.stale_task_checker import StaleTaskChecker

    checker = StaleTaskChecker()

    assert not hasattr(checker, "set_cancellation_reconciliation_deps")
    assert not hasattr(checker, "_is_message_cancelled_for_recovery")


def test_room_state_deletion_no_longer_lists_cancelled_messages():
    import container

    assert "cancelled_messages" not in inspect.getsource(container)
