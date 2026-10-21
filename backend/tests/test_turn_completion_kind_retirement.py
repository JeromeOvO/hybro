"""C2 step 8b: turn_completion_kind has no writer and no readers."""

from __future__ import annotations

import inspect

from conversation.timeline_projection import _public_user_message_extend_info


def test_active_runs_response_and_request_drop_the_completion_lookup():
    from models.request import RoomCenterRoomSettingRequest
    from models.response import RoomCenterActiveRunsResponse

    assert "turn_completion_kind" not in RoomCenterActiveRunsResponse.model_fields
    assert "trigger_message_id" not in RoomCenterRoomSettingRequest.model_fields


def test_active_runs_route_reads_only_execution():
    from api_gateway.routes.room_routes import inquiry_active_runs

    assert "center" not in inspect.signature(inquiry_active_runs).parameters


def test_conversation_no_longer_looks_up_completion_kind():
    from chat.protocols import RoomCenterCompatibility
    from chat.route_adapter import ChatRouteAdapter
    from conversation.facade import ConversationFacade
    from conversation.runtime import ConversationRuntime

    assert not hasattr(ConversationFacade, "get_turn_completion_kind")
    for owner in (ConversationRuntime, ChatRouteAdapter, RoomCenterCompatibility):
        assert not hasattr(owner, "inquiry_active_runs"), owner


def test_message_store_has_no_completion_kind_writer():
    from conversation.repository import ConversationMessageStore
    from dal.runtime_store import RuntimeRepositoryStore

    for owner in (ConversationMessageStore, RuntimeRepositoryStore):
        assert not hasattr(owner, "set_turn_completion_kind"), owner


def test_public_user_extend_info_never_exposes_completion_kind():
    projected = _public_user_message_extend_info(
        {"turn_completion_kind": "synthesis", "quote_id": "q-1"}
    )

    assert projected == {"quote_id": "q-1"}
