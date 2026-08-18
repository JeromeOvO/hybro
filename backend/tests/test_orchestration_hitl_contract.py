from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from common.dto import HITLRequestEvent
from delivery.translator import to_sse_frame
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
    persistence.persist_hitl_request_id_on_message = AsyncMock(return_value=True)
    persistence.persist_hitl_user_answer = AsyncMock(return_value=True)
    persistence.persist_hitl_group_metadata = AsyncMock(return_value=True)
    persistence.get_hitl_request = AsyncMock(return_value=None)
    persistence.get_hitl_group_requests = AsyncMock(return_value=[])
    persistence.cas_update_hitl_request = AsyncMock(return_value=True)
    persistence.update_hitl_request = AsyncMock(return_value=True)
    persistence.get_and_clear_continuation_on_message = AsyncMock(return_value=None)
    persistence.get_and_clear_continuation_on_user_message = AsyncMock(
        return_value=None
    )
    return persistence


def test_hitl_request_model_preserves_optional_orchestration_run_link_fields():
    request = HITLRequest(
        schema_version=3,
        interaction_id="interaction-1",
        question_index=0,
        question_count=1,
        room_id="room-1",
        user_message_id="user-msg-1",
        application_route="supervisor_run",
        public_source="supervisor",
        evidence_origin="supervisor",
        prompt="Clarify?",
        orchestration_run_id="run-msg-1",
    )

    assert request.orchestration_run_id == "run-msg-1"
    payload = request.model_dump(mode="json")
    assert payload["orchestration_run_id"] == "run-msg-1"


def test_hitl_delivery_event_has_no_orchestration_run_link():
    event = HITLRequestEvent(
        room_id="room-1",
        request_id="hitl-1",
        message_id="display-msg-1",
        source="supervisor",
        prompt="Clarify the scope",
        prompt_type="text",
        related_message_id="user-msg-1",
    )

    assert "orchestration_run_id" not in HITLRequestEvent.model_fields
    assert "orchestration_run_id" not in event.model_dump()
    frame = to_sse_frame(event, timestamp=NOW)

    assert frame["data"]["message_id"] == "display-msg-1"
    assert frame["data"]["related_message_id"] == "user-msg-1"
    assert "orchestration_run_id" not in frame["data"]
    assert "lifecycle_message_id" not in frame["data"]
