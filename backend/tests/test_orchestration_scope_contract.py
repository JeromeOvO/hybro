from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api_gateway.routes.room_routes import send_message
from common.dto import ExecutionAck, ExecutionRequest
from execution.facade import ExecutionFacade
from models.agent import AgentStatus
from models.agent_group import AgentGroup
from models.request import RoomCenterUserMessageRequest
from models.response import RoomCenterUserMessageResponse
from models.room import MessageContent, Room, RoomUserMessage
from room.compat.runtime import RoomMessagePreflightContext, RoomServices


def test_execution_request_accepts_candidate_scope_fields():
    request = ExecutionRequest(
        room_id="room-1",
        sender_id="user-1",
        mode="supervisor",
        selected_agent_ids=["agent-1", "agent-2"],
        candidate_scope_mode="explicit_selection",
        candidate_scope_group_id="group-1",
        mentioned_agent_ids=["agent-2"],
    )

    assert request.selected_agent_ids == ["agent-1", "agent-2"]
    assert request.candidate_scope_mode == "explicit_selection"
    assert request.candidate_scope_group_id == "group-1"
    assert request.mentioned_agent_ids == ["agent-2"]


@pytest.mark.asyncio
async def test_send_message_allows_supervisor_selected_scope_with_mentions(
    mock_user,
    sample_supervisor_room,
    sample_user_message,
    patch_room_center_deps,
):
    mock_request = MagicMock()
    mock_request.json = AsyncMock(
        return_value={
            "room_id": sample_supervisor_room.room_id,
            "message": {
                **sample_user_message.model_dump(mode="json"),
                "room_id": sample_supervisor_room.room_id,
            },
            "client_request_id": "scope-contract-client-1",
            "mode": "supervisor",
            "message_target_mode": "room_default",
            "selected_agent_ids": [" agent-1 ", "agent-2"],
            "mentioned_agent_ids": [" agent-2 "],
            "candidate_scope_mode": "explicit_selection",
            "candidate_scope_group_id": " group-1 ",
        }
    )
    patch_room_center_deps[
        "db_service"
    ].get_room_by_room_id.return_value = sample_supervisor_room
    patch_room_center_deps["execution_engine"].execute.return_value = ExecutionAck(
        success=True,
        message_id="message-1",
    )

    response = await send_message(
        mock_request,
        MagicMock(),
        mock_user,
        store=patch_room_center_deps["db_service"],
        engine=patch_room_center_deps["execution_engine"],
    )

    assert response.success is True
    execution_request = patch_room_center_deps[
        "execution_engine"
    ].execute.await_args.args[0]
    assert execution_request.mode == "supervisor"
    assert execution_request.message_target_mode == "room_default"
    assert execution_request.selected_agent_ids == ["agent-1", "agent-2"]
    assert execution_request.mentioned_agent_ids == ["agent-2"]
    assert execution_request.candidate_scope_mode == "explicit_selection"
    assert execution_request.candidate_scope_group_id == "group-1"


@pytest.mark.asyncio
async def test_send_message_still_rejects_direct_mentions_with_message_target_mode(
    mock_user,
    sample_room,
    sample_user_message,
    patch_room_center_deps,
):
    mock_request = MagicMock()
    mock_request.json = AsyncMock(
        return_value={
            "room_id": sample_room.room_id,
            "message": sample_user_message.model_dump(mode="json"),
            "client_request_id": "scope-contract-client-2",
            "mode": "direct",
            "message_target_mode": "room_default",
            "mentioned_agent_ids": ["agent-1"],
        }
    )
    patch_room_center_deps["db_service"].get_room_by_room_id.return_value = sample_room

    response = await send_message(
        mock_request,
        MagicMock(),
        mock_user,
        store=patch_room_center_deps["db_service"],
        engine=patch_room_center_deps["execution_engine"],
    )

    assert response.status_code == 400
    assert "Cannot specify both mentioned_agent_ids and message_target_mode" in (
        response.error or ""
    )
    patch_room_center_deps["execution_engine"].execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_message_rejects_spoofed_supervisor_mode_for_non_supervisor_room(
    mock_user,
    sample_room,
    sample_user_message,
    patch_room_center_deps,
):
    mock_request = MagicMock()
    mock_request.json = AsyncMock(
        return_value={
            "room_id": sample_room.room_id,
            "message": sample_user_message.model_dump(mode="json"),
            "client_request_id": "scope-contract-client-3",
            "mode": "supervisor",
            "message_target_mode": "room_default",
            "selected_agent_ids": ["agent-1"],
            "mentioned_agent_ids": ["agent-1"],
        }
    )
    patch_room_center_deps["db_service"].get_room_by_room_id.return_value = sample_room

    response = await send_message(
        mock_request,
        MagicMock(),
        mock_user,
        store=patch_room_center_deps["db_service"],
        engine=patch_room_center_deps["execution_engine"],
    )

    assert response.status_code == 400
    assert "Cannot specify both mentioned_agent_ids and message_target_mode" in (
        response.error or ""
    )
    patch_room_center_deps["execution_engine"].execute.assert_not_awaited()


def _execution_facade_for_scope_test(room_center):
    return ExecutionFacade(
        room_center=room_center,
        room_message_center=SimpleNamespace(process_room_user_message=AsyncMock()),
        hitl_manager=SimpleNamespace(
            get_pending_requests=AsyncMock(return_value=[]),
            request_input=AsyncMock(),
            handle_response=AsyncMock(),
            cancel_request=AsyncMock(return_value=None),
        ),
        run_lifecycle=SimpleNamespace(record_processing_status=AsyncMock()),
        run_reader=SimpleNamespace(
            get_run=AsyncMock(return_value=None),
            get_runs_for_room=AsyncMock(return_value=[]),
        ),
        cancellation_state=SimpleNamespace(
            cancel_message_and_broadcast=AsyncMock(),
            clear_cancellation=MagicMock(),
        ),
        cancellation_store=SimpleNamespace(
            cancel_message=AsyncMock(return_value=True),
            mark_cancellation_reconciled=AsyncMock(return_value=True),
        ),
        hitl_message_cancellation=SimpleNamespace(
            cancel_requests_for_message=AsyncMock(),
        ),
        agent_task_cleanup=SimpleNamespace(
            cleanup_cancelled_message_tasks=AsyncMock(),
        ),
        agent_response_handler=SimpleNamespace(handle=AsyncMock()),
        event_publisher=SimpleNamespace(emit=AsyncMock()),
        run_event_enabled=lambda: False,
        client_request_id_resolver=SimpleNamespace(
            resolve_client_request_id=AsyncMock(
                side_effect=lambda _, provided: provided
            ),
        ),
    )


@pytest.mark.asyncio
async def test_execution_facade_passes_scope_fields_to_room_request():
    room_center = SimpleNamespace(
        get_idempotent_user_message=AsyncMock(return_value=None),
        persist_message_to_room=AsyncMock(
            return_value=(
                RoomCenterUserMessageResponse(
                    room_id="room-1",
                    message_id="message-1",
                    success=True,
                ),
                None,
            )
        ),
        run_message_preflight_to_room=AsyncMock(),
    )
    facade = _execution_facade_for_scope_test(room_center)

    await facade.execute(
        ExecutionRequest(
            room_id="room-1",
            sender_id="user-1",
            mode="supervisor",
            client_request_id="client-1",
            selected_agent_ids=["agent-1", "agent-2"],
            candidate_scope_mode="explicit_selection",
            candidate_scope_group_id="group-1",
            mentioned_agent_ids=["agent-2"],
        )
    )

    room_request = room_center.persist_message_to_room.await_args.args[0]
    assert room_request.extend_info == {
        "mode": "supervisor",
        "selected_agent_ids": ["agent-1", "agent-2"],
        "candidate_scope_mode": "explicit_selection",
        "candidate_scope_group_id": "group-1",
    }
    assert room_center.persist_message_to_room.await_args.args[1:] == (
        None,
        ["agent-2"],
    )


def _agent(agent_id: str, name: str, *, owner_id: str = "user-1"):
    return SimpleNamespace(
        agent_id=agent_id,
        provider_id=owner_id,
        agent_status=AgentStatus.active,
        is_public=True,
        agent_card=SimpleNamespace(name=name),
    )


@pytest.mark.asyncio
async def test_explicit_selection_omits_spoofed_candidate_scope_group_id():
    svc = object.__new__(RoomServices)
    svc._store = MagicMock()
    svc._store.update_room_user_message_by_message_id = AsyncMock(return_value=True)
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
    )

    result = await svc._prepare_orchestration_envelope(
        request=RoomCenterUserMessageRequest(
            room_id="room-1",
            user_id="user-1",
            message=user_message,
            client_request_id="client-1",
            extend_info={
                "mode": "supervisor",
                "selected_agent_ids": ["agent-1"],
                "candidate_scope_mode": "explicit_selection",
                "candidate_scope_group_id": "spoofed-group",
            },
        ),
        user_message=user_message,
        selected_agent_set={"agent-1": "Agent One"},
        explicit_mentions=None,
        client_request_id="client-1",
    )

    assert result.success is True
    assert user_message.extend_info["candidate_scope_mode"] == "explicit_selection"
    assert "candidate_scope_group_id" not in user_message.extend_info


@pytest.mark.asyncio
async def test_saved_group_keeps_sanitized_candidate_scope_group_id():
    svc = object.__new__(RoomServices)
    svc._store = MagicMock()
    svc._store.update_room_user_message_by_message_id = AsyncMock(return_value=True)
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
    )

    result = await svc._prepare_orchestration_envelope(
        request=RoomCenterUserMessageRequest(
            room_id="room-1",
            user_id="user-1",
            message=user_message,
            client_request_id="client-1",
            extend_info={
                "mode": "supervisor",
                "selected_agent_ids": ["agent-1"],
                "candidate_scope_mode": "saved_group",
                "candidate_scope_group_id": " group-1 ",
            },
        ),
        user_message=user_message,
        selected_agent_set={"agent-1": "Agent One"},
        explicit_mentions=None,
        client_request_id="client-1",
    )

    assert result.success is True
    assert user_message.extend_info["candidate_scope_source"] == "saved_group"
    assert user_message.extend_info["candidate_scope_mode"] == "saved_group"
    assert user_message.extend_info["candidate_agent_ids"] == ["agent-1"]
    assert user_message.extend_info["candidate_scope_group_id"] == "group-1"


@pytest.mark.asyncio
async def test_orchestration_envelope_preserves_safe_quote_metadata_only():
    svc = object.__new__(RoomServices)
    svc._store = MagicMock()
    svc._store.update_room_user_message_by_message_id = AsyncMock(return_value=True)
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "quoted_text": "Quoted evidence",
            "quoted_sender_name": "Agent One",
            "quote_id": "quote-1",
            "supervisor_trajectory": {"private": True},
            "conversation_context": "private context",
        },
    )

    result = await svc._prepare_orchestration_envelope(
        request=RoomCenterUserMessageRequest(
            room_id="room-1",
            user_id="user-1",
            message=user_message,
            client_request_id="client-1",
            extend_info={
                "mode": "supervisor",
                "selected_agent_ids": ["agent-1"],
                "candidate_scope_mode": "explicit_selection",
            },
        ),
        user_message=user_message,
        selected_agent_set={"agent-1": "Agent One"},
        explicit_mentions=None,
        client_request_id="client-1",
    )

    assert result.success is True
    assert user_message.extend_info["quoted_text"] == "Quoted evidence"
    assert user_message.extend_info["quoted_sender_name"] == "Agent One"
    assert user_message.extend_info["quote_id"] == "quote-1"
    assert "supervisor_trajectory" not in user_message.extend_info
    assert "conversation_context" not in user_message.extend_info


@pytest.mark.asyncio
async def test_saved_group_rejects_selected_agents_outside_group():
    svc = object.__new__(RoomServices)
    svc._store = MagicMock()
    svc._store.get_room_by_room_id = AsyncMock(
        return_value=Room(
            room_id="room-1",
            room_name="Room",
            room_owner_id="user-1",
            room_owner_name="User",
            room_agent_set={"agent-1": "Agent One", "agent-3": "Agent Three"},
            extend_info={"use_supervisor": True, "debateMode": False},
        )
    )
    agents = {
        "agent-1": _agent("agent-1", "Agent One"),
        "agent-3": _agent("agent-3", "Agent Three"),
    }
    svc._store.get_agent_by_agent_id = AsyncMock(
        side_effect=lambda agent_id: agents.get(agent_id)
    )
    svc._store.get_agent_group_by_id = AsyncMock(
        return_value=AgentGroup(
            group_id="group-1",
            name="Saved Group",
            type="user",
            owner_id="user-1",
            agents=["agent-1", "agent-2"],
        )
    )
    svc.delivery = SimpleNamespace(create_token=MagicMock(return_value=object()))
    svc._validate_send_message_request = MagicMock(return_value=None)
    svc._resolve_and_apply_attachments = AsyncMock(return_value=None)
    svc._materialize_room_quote = AsyncMock(return_value=None)
    svc._persist_user_message = AsyncMock(return_value=True)

    response, context = await svc.persist_message_to_room(
        RoomCenterUserMessageRequest(
            room_id="room-1",
            user_id="user-1",
            message=RoomUserMessage(
                room_id="room-1",
                message_id="message-1",
                user_id="user-1",
                message_content=MessageContent(message_text="Coordinate this"),
            ),
            client_request_id="client-1",
            extend_info={
                "mode": "supervisor",
                "selected_agent_ids": ["agent-1", "agent-3"],
                "candidate_scope_mode": "saved_group",
                "candidate_scope_group_id": "group-1",
            },
        ),
        target_group="room_team",
    )

    assert response.success is False
    assert response.status_code == 400
    assert "not members of the selected saved group" in (response.error or "")
    assert context is None
    svc._persist_user_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_saved_group_requires_selected_agent_snapshot():
    svc = object.__new__(RoomServices)
    svc._store = MagicMock()
    svc._store.get_room_by_room_id = AsyncMock(
        return_value=Room(
            room_id="room-1",
            room_name="Room",
            room_owner_id="user-1",
            room_owner_name="User",
            room_agent_set={"agent-1": "Agent One"},
            extend_info={"use_supervisor": True},
        )
    )
    svc._store.get_agent_group_by_id = AsyncMock(
        return_value=AgentGroup(
            group_id="group-1",
            name="Saved Group",
            type="user",
            owner_id="user-1",
            agents=["agent-1"],
        )
    )
    svc.delivery = SimpleNamespace(create_token=MagicMock(return_value=object()))
    svc._validate_send_message_request = MagicMock(return_value=None)
    svc._resolve_and_apply_attachments = AsyncMock(return_value=None)
    svc._materialize_room_quote = AsyncMock(return_value=None)
    svc._persist_user_message = AsyncMock(return_value=True)

    response, context = await svc.persist_message_to_room(
        RoomCenterUserMessageRequest(
            room_id="room-1",
            user_id="user-1",
            message=RoomUserMessage(
                room_id="room-1",
                message_id="message-1",
                user_id="user-1",
                message_content=MessageContent(message_text="Coordinate this"),
            ),
            extend_info={
                "mode": "supervisor",
                "candidate_scope_mode": "saved_group",
                "candidate_scope_group_id": "group-1",
            },
        )
    )

    assert response.success is False
    assert response.status_code == 400
    assert "selected_agent_ids" in (response.error or "")
    assert context is None
    svc._persist_user_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_candidate_scope_mode_is_rejected_before_persistence():
    svc = object.__new__(RoomServices)
    svc._store = MagicMock()
    svc._store.get_room_by_room_id = AsyncMock(
        return_value=Room(
            room_id="room-1",
            room_name="Room",
            room_owner_id="user-1",
            room_owner_name="User",
            room_agent_set={"agent-1": "Agent One"},
            extend_info={"use_supervisor": True},
        )
    )
    svc.delivery = SimpleNamespace(create_token=MagicMock(return_value=object()))
    svc._validate_send_message_request = MagicMock(return_value=None)
    svc._resolve_and_apply_attachments = AsyncMock(return_value=None)
    svc._materialize_room_quote = AsyncMock(return_value=None)
    svc._persist_user_message = AsyncMock(return_value=True)

    response, context = await svc.persist_message_to_room(
        RoomCenterUserMessageRequest(
            room_id="room-1",
            user_id="user-1",
            message=RoomUserMessage(
                room_id="room-1",
                message_id="message-1",
                user_id="user-1",
                message_content=MessageContent(message_text="Coordinate this"),
            ),
            extend_info={
                "mode": "supervisor",
                "selected_agent_ids": ["agent-1"],
                "candidate_scope_mode": "saved_groups",
            },
        )
    )

    assert response.success is False
    assert response.status_code == 400
    assert response.scope_resolution_error is not None
    assert response.scope_resolution_error.code == "invalid_target"
    assert "Unsupported candidate_scope_mode 'saved_groups'" in (response.error or "")
    assert context is None
    svc._persist_user_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_orchestration_mentions_must_be_subset_of_selected_candidates():
    svc = object.__new__(RoomServices)
    svc._store = MagicMock()
    svc._store.get_room_by_room_id = AsyncMock(
        return_value=Room(
            room_id="room-1",
            room_name="Room",
            room_owner_id="user-1",
            room_owner_name="User",
            room_agent_set={"agent-1": "Agent One", "agent-2": "Agent Two"},
            extend_info={"use_supervisor": True},
        )
    )
    agents = {
        "agent-1": _agent("agent-1", "Agent One"),
        "agent-2": _agent("agent-2", "Agent Two"),
    }
    svc._store.get_agent_by_agent_id = AsyncMock(
        side_effect=lambda agent_id: agents.get(agent_id)
    )
    svc.delivery = SimpleNamespace(create_token=MagicMock(return_value=object()))
    svc._validate_send_message_request = MagicMock(return_value=None)
    svc._resolve_and_apply_attachments = AsyncMock(return_value=None)
    svc._materialize_room_quote = AsyncMock(return_value=None)
    svc._persist_user_message = AsyncMock(return_value=True)

    response, context = await svc.persist_message_to_room(
        RoomCenterUserMessageRequest(
            room_id="room-1",
            user_id="user-1",
            message=RoomUserMessage(
                room_id="room-1",
                message_id="message-1",
                user_id="user-1",
                message_content=MessageContent(message_text="Ask agent two"),
            ),
            extend_info={
                "mode": "supervisor",
                "selected_agent_ids": ["agent-1"],
                "candidate_scope_mode": "explicit_selection",
            },
        ),
        mentioned_agent_ids=["agent-2"],
    )

    assert response.success is False
    assert response.status_code == 400
    assert "candidate scope" in (response.error or "")
    assert context is None
    svc._persist_user_message.assert_not_awaited()


def _orchestration_preflight_context(
    *,
    pending_clarification: bool = False,
    empty_scope: bool = False,
):
    room_extend_info = {"use_supervisor": True, "debateMode": False}
    if pending_clarification:
        room_extend_info["pending_clarification_message_id"] = "clarify-1"
    room = Room(
        room_id="room-1",
        room_name="Room",
        room_owner_id="user-1",
        room_owner_name="User",
        room_agent_set={"agent-1": "Agent One"},
        extend_info=room_extend_info,
    )
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
    )
    request = RoomCenterUserMessageRequest(
        room_id="room-1",
        user_id="user-1",
        message=user_message,
        extend_info={
            "mode": "supervisor",
            "selected_agent_ids": ["agent-1"],
            "candidate_scope_mode": "explicit_selection",
        },
    )
    agent = _agent("agent-1", "Agent One")
    selected_scope = (
        ({}, True, []) if empty_scope else ({"agent-1": "Agent One"}, False, [agent])
    )
    return RoomMessagePreflightContext(
        request=request,
        target_group="room_team",
        mentioned_agent_ids=None,
        user_message=user_message,
        client_request_id=None,
        room=room,
        is_debate_mode=False,
        use_supervisor=True,
        message_text="Coordinate this",
        pre_resolved_mentions=None,
        pre_resolved_scope=None,
        pre_resolved_selected_scope=selected_scope,
        token=SimpleNamespace(is_cancelled=False),
    )
