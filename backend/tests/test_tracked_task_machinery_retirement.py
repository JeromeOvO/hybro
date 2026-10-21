"""C2 step 6: tracked-task machinery and pending_continuation are retired."""

from __future__ import annotations

import importlib
import inspect
from dataclasses import fields
from unittest.mock import AsyncMock, MagicMock

import pytest

RETIRED_TASK_SETTINGS = {
    "stale_check_minutes",
    "task_expiry_hours",
    "pending_task_warning_hours",
}


@pytest.mark.parametrize(
    "module",
    [
        "execution.task_tracking",
        "execution.dispatch.task_notifications",
        "delivery.task_notifier",
        # Only the retired tracked-task code imported these.
        "a2a_adapter.task_status",
        "execution.dispatch.agent_event",
        "execution.dispatch.a2a_interaction",
    ],
)
def test_tracked_task_modules_are_gone(module):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


def test_a2a_service_has_no_tracked_task_surface():
    from a2a_adapter import runtime_service
    from a2a_adapter.runtime_service import A2AService

    assert not hasattr(runtime_service, "A2ATaskTrackingPort")
    for name in (
        "bind_task_tracking",
        "create_task_for_tracking",
        "send_message_to_tracked_agent",
        "reply_to_task",
        "_webhook_base_url",
    ):
        assert not hasattr(A2AService, name), name


def test_execution_ports_drop_tracked_task_and_continuation_contracts():
    import execution.ports as ports

    assert not hasattr(ports, "A2ATransportPort")
    assert not hasattr(ports, "A2ATaskTrackingStorePort")
    for name in (
        "get_pending_continuation_on_message",
        "save_continuation_on_user_message",
        "get_and_clear_continuation_on_message",
        "get_and_clear_continuation_on_user_message",
    ):
        assert not hasattr(ports.HITLPersistencePort, name), name


def test_task_lifecycle_store_keeps_only_live_methods():
    from dal.runtime_store.parts.task_lifecycle_store import (
        TaskLifecycleRuntimeStorePart,
    )

    public = {
        name
        for name, member in inspect.getmembers(TaskLifecycleRuntimeStorePart)
        if callable(member) and not name.startswith("__")
    }

    assert public == {
        "resolve_client_request_id_for_agent_message",
        "resolve_client_request_id_for_message_id",
        "get_room_ids_with_non_terminal_runs",
        # Webhook token helpers retire with the webhook ingress in C2 step 7.
        "_get_webhook_signing_key",
        "hash_webhook_token",
        "verify_webhook_token",
        "generate_webhook_token",
        "verify_webhook_token_on_message",
        "verify_webhook_token_for_task",
    }


def test_stale_checker_keeps_only_hitl_and_processing_cleanup():
    from jobs.stale_task_checker import StaleTaskChecker, StaleTaskCheckerDeps

    for name in (
        "_process_stale_task",
        "_auto_fail_expired_task",
        "_auto_fail_non_tracked_task",
        "_mark_task_failed",
        "_message_or_parent_cancelled",
        "_is_message_cancelled_for_recovery",
        "_get_task_from_agent",
        "configure_timing",
    ):
        assert not hasattr(StaleTaskChecker, name), name
    assert {field.name for field in fields(StaleTaskCheckerDeps)} == {
        "store",
        "rooms_collection",
    }
    for name in RETIRED_TASK_SETTINGS:
        assert name not in inspect.signature(StaleTaskChecker).parameters


@pytest.mark.asyncio
async def test_stale_checker_iteration_runs_hitl_reconcile_and_cleanup():
    from jobs.stale_task_checker import (
        StaleHITLDeps,
        StaleTaskChecker,
        StaleTaskCheckerDeps,
    )

    store = MagicMock(get_room_ids_with_non_terminal_runs=AsyncMock(return_value=[]))
    rooms = MagicMock(update_many=AsyncMock(return_value=MagicMock(modified_count=0)))
    hitl = StaleHITLDeps(
        recover_stale_processing=AsyncMock(),
        reconcile_lifecycle=AsyncMock(),
    )
    checker = StaleTaskChecker()
    checker.set_runtime_deps(StaleTaskCheckerDeps(store=store, rooms_collection=rooms))
    checker.set_hitl_deps(hitl)

    await checker.check_stale_tasks()

    hitl.reconcile_lifecycle.assert_awaited_once()
    hitl.recover_stale_processing.assert_awaited_once()
    rooms.update_many.assert_awaited_once()


def test_conversation_facade_has_no_stale_card_auto_fail():
    from conversation.facade import ConversationFacade

    assert not hasattr(ConversationFacade, "_auto_fail_stale_agent_messages")


def test_pending_continuation_field_is_gone():
    from common.dto.runtime_store import RuntimeRoomAgentMessage
    from models.room import RoomAgentMessage, RoomUserMessage

    for model in (RoomAgentMessage, RoomUserMessage, RuntimeRoomAgentMessage):
        assert "pending_continuation" not in model.model_fields, model


def test_hitl_service_no_longer_clears_continuations():
    from execution.hitl.service import HITLService

    assert not hasattr(HITLService, "_clear_hitl_continuation_once")


def test_task_timing_settings_are_retired():
    from common.config.loader import REMOVED_FIELDS, Settings

    assert RETIRED_TASK_SETTINGS <= REMOVED_FIELDS
    assert not RETIRED_TASK_SETTINGS & set(Settings.model_fields)


def test_hitl_reconciler_has_no_remote_command_inspection():
    from execution.hitl.reconciler import HITLLifecycleReconciler

    parameters = inspect.signature(HITLLifecycleReconciler).parameters

    assert "inspect_remote_command" not in parameters


def test_container_no_longer_wires_tracked_task_notifications():
    import container

    source = inspect.getsource(container)

    for name in (
        "bind_notification_store",
        "bind_task_notification_runtime",
        "TaskUpdateNotifier",
        "task_notification_store",
        "bind_task_processing_status_emitter",
        "inspect_uncertain_hitl_command",
        "enable_task_tracking_on_message",
        "pending_continuation",
    ):
        assert name not in source, name


def test_tracked_task_only_constants_and_queries_are_gone():
    import common.a2a_constants as constants
    from common.protocols.repository_protocols import MessageRepository
    from common.protocols.runtime_store_protocols import RuntimeMessageStore

    assert not hasattr(constants, "UNSUPPORTED_INTERACTION_CODE")
    assert not hasattr(constants, "UNSUPPORTED_INTERACTION_MESSAGE")
    for name in (
        "get_agent_task_messages_for_user",
        "get_task_messages_for_room",
        "get_pending_task_messages_for_user",
    ):
        assert not hasattr(MessageRepository, name), name
    assert not hasattr(RuntimeMessageStore, "update_last_notified_state")
