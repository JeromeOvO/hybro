from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.dto import HITLRequestEvent, ProcessingStatusEvent
from delivery.translator import to_sse_frame
from execution.events import emit_processing_status
from execution.hitl.service import HITLService
from models.hitl import HITLPromptType, HITLRequest, HITLStatus

NOW = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)


def _hitl_doc(**overrides):
    request = HITLRequest(
        request_id=overrides.pop("request_id", "hitl-1"),
        room_id=overrides.pop("room_id", "room-1"),
        user_message_id=overrides.pop("user_message_id", "user-msg-1"),
        source=overrides.pop("source", "supervisor"),
        prompt=overrides.pop("prompt", "Clarify?"),
        prompt_type=overrides.pop("prompt_type", HITLPromptType.TEXT),
        continuation_message_id=overrides.pop("continuation_message_id", "cont-msg-1"),
        display_message_id=overrides.pop("display_message_id", "display-msg-1"),
        group_id=overrides.pop("group_id", None),
        group_total=overrides.pop("group_total", None),
        group_index=overrides.pop("group_index", None),
        status=overrides.pop("status", HITLStatus.PENDING),
        **overrides,
    )
    return request.model_dump(mode="json")


def _persistence_mock():
    persistence = MagicMock()
    persistence.count_hitl_requests_for_message = AsyncMock(return_value=0)
    persistence.create_hitl_request = AsyncMock(return_value=True)
    persistence.get_room_user_message_by_message_id = AsyncMock(return_value=None)
    persistence.resolve_client_request_id_for_message_id = AsyncMock(return_value=None)
    persistence.update_agent_message_task_state = AsyncMock(return_value=True)
    persistence.persist_hitl_user_answer = AsyncMock(return_value=True)
    persistence.persist_hitl_group_metadata = AsyncMock(return_value=True)
    persistence.get_hitl_request = AsyncMock(return_value=None)
    persistence.get_hitl_group_requests = AsyncMock(return_value=[])
    persistence.cas_update_hitl_request = AsyncMock(return_value=True)
    persistence.get_and_clear_continuation_on_message = AsyncMock(return_value=None)
    persistence.get_and_clear_continuation_on_user_message = AsyncMock(
        return_value=None
    )
    return persistence


def test_hitl_request_model_preserves_optional_v2_run_link_fields():
    request = HITLRequest(
        room_id="room-1",
        user_message_id="user-msg-1",
        source="supervisor",
        prompt="Clarify?",
        orchestration_run_id="run-msg-1",
        orchestration_schema_version=2,
    )

    assert request.orchestration_run_id == "run-msg-1"
    assert request.orchestration_schema_version == 2
    payload = request.model_dump(mode="json")
    assert payload["orchestration_run_id"] == "run-msg-1"
    assert payload["orchestration_schema_version"] == 2


@pytest.mark.asyncio
async def test_v2_hitl_creation_persists_run_links_and_keeps_public_sse_ids():
    service = HITLService()
    persistence = _persistence_mock()
    delivery = MagicMock()
    delivery.emit = AsyncMock()
    captured_docs = []

    async def create_hitl_request(doc):
        captured_docs.append(doc)
        return True

    persistence.create_hitl_request.side_effect = create_hitl_request
    service._persistence = persistence
    service._delivery = delivery

    result = await service.request_input(
        room_id="room-1",
        user_message_id="user-msg-1",
        source="supervisor",
        prompt="Clarify the scope",
        continuation_message_id="cont-msg-1",
        display_message_id="display-msg-1",
        orchestration_run_id="run-msg-1",
        orchestration_schema_version=2,
    )

    assert result is not None
    assert captured_docs[0]["orchestration_run_id"] == "run-msg-1"
    assert captured_docs[0]["orchestration_schema_version"] == 2

    event = delivery.emit.await_args.args[0]
    assert isinstance(event, HITLRequestEvent)
    assert event.message_id == "display-msg-1"
    assert event.related_message_id == "user-msg-1"
    assert event.orchestration_run_id == "run-msg-1"
    assert event.orchestration_schema_version == 2


def test_hitl_sse_frame_includes_v2_run_links_when_present():
    event = HITLRequestEvent(
        room_id="room-1",
        request_id="hitl-1",
        message_id="display-msg-1",
        source="supervisor",
        prompt="Clarify the scope",
        prompt_type="text",
        related_message_id="user-msg-1",
        orchestration_run_id="run-msg-1",
        orchestration_schema_version=2,
    )

    frame = to_sse_frame(event, timestamp=NOW)

    assert frame["data"]["message_id"] == "display-msg-1"
    assert frame["data"]["related_message_id"] == "user-msg-1"
    assert frame["data"]["orchestration_run_id"] == "run-msg-1"
    assert frame["data"]["orchestration_schema_version"] == 2
    assert "lifecycle_message_id" not in frame["data"]


@pytest.mark.asyncio
async def test_group_cancel_cancels_pending_siblings_once_with_user_correlation():
    service = HITLService()
    persistence = _persistence_mock()
    delivery = MagicMock()
    delivery.emit = AsyncMock()
    current = _hitl_doc(
        request_id="hitl-1",
        group_id="group-1",
        group_total=2,
        group_index=0,
    )
    sibling = _hitl_doc(
        request_id="hitl-2",
        group_id="group-1",
        group_total=2,
        group_index=1,
        display_message_id="display-msg-2",
    )
    persistence.get_hitl_request.return_value = current
    persistence.get_hitl_group_requests.return_value = [current, sibling]
    service._persistence = persistence
    service._delivery = delivery

    await service.cancel_request("hitl-1", room_id="room-1")

    canceled_ids = [
        call.args[0] for call in persistence.cas_update_hitl_request.await_args_list
    ]
    assert canceled_ids == ["hitl-1", "hitl-2"]

    emitted = [call.args[0] for call in delivery.emit.await_args_list]
    assert [event.request_id for event in emitted] == ["hitl-1", "hitl-2"]
    assert [event.related_message_id for event in emitted] == [
        "user-msg-1",
        "user-msg-1",
    ]


@pytest.mark.asyncio
async def test_group_expiry_expires_pending_siblings_once_with_user_correlation():
    service = HITLService()
    persistence = _persistence_mock()
    delivery = MagicMock()
    delivery.emit = AsyncMock()
    current = _hitl_doc(
        request_id="hitl-1",
        group_id="group-1",
        group_total=2,
        group_index=0,
    )
    sibling = _hitl_doc(
        request_id="hitl-2",
        group_id="group-1",
        group_total=2,
        group_index=1,
        display_message_id="display-msg-2",
    )
    persistence.get_hitl_request.return_value = current
    persistence.get_hitl_group_requests.return_value = [current, sibling]
    service._persistence = persistence
    service._delivery = delivery

    await service.expire_request("hitl-1", room_id="room-1")

    expired_statuses = [
        call.kwargs["status"]
        for call in persistence.cas_update_hitl_request.await_args_list
    ]
    assert expired_statuses == [HITLStatus.EXPIRED.value, HITLStatus.EXPIRED.value]

    emitted = [call.args[0] for call in delivery.emit.await_args_list]
    assert [event.request_id for event in emitted] == ["hitl-1", "hitl-2"]
    assert [event.status for event in emitted] == [
        HITLStatus.EXPIRED.value,
        HITLStatus.EXPIRED.value,
    ]
    assert [event.related_message_id for event in emitted] == [
        "user-msg-1",
        "user-msg-1",
    ]


@pytest.mark.asyncio
async def test_processing_status_public_payload_uses_related_message_correlation():
    emitted = []
    publisher = SimpleNamespace(emit=AsyncMock(side_effect=emitted.append))
    run_lifecycle = SimpleNamespace(record_processing_status=AsyncMock(return_value=None))
    resolver = SimpleNamespace(resolve_client_request_id=AsyncMock(return_value="cr-1"))

    await emit_processing_status(
        room_id="room-1",
        status="processing",
        message_id="display-msg-1",
        lifecycle_message_id="user-msg-1",
        run_lifecycle=run_lifecycle,
        event_publisher=publisher,
        run_event_enabled=lambda: False,
        client_request_id_resolver=resolver,
        client_request_id="cr-1",
    )

    event = emitted[0]
    assert isinstance(event, ProcessingStatusEvent)
    assert event.message_id == "display-msg-1"
    assert event.related_message_id == "user-msg-1"
    assert not hasattr(event, "lifecycle_message_id")

    frame = to_sse_frame(event, timestamp=NOW)
    assert frame["data"]["related_message_id"] == "user-msg-1"
    assert "lifecycle_message_id" not in frame["data"]
