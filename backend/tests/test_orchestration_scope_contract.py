from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.room_center import send_message
from common.dto import ExecutionAck, ExecutionRequest
from execution.facade import ExecutionFacade
from models.agent import AgentStatus
from models.request import RoomCenterUserMessageRequest
from models.response import RoomCenterUserMessageResponse
from models.room import MessageContent, Room, RoomUserMessage
from room.compat.runtime import RoomServices


def test_execution_request_accepts_candidate_scope_fields():
    request = ExecutionRequest(
        room_id="room-1",
        sender_id="user-1",
        mode="supervisor",
        selected_agent_ids=["agent-1", "agent-2"],
        candidate_scope_mode="explicit_selection",
        candidate_scope_group_id="group-1",
        orchestration_schema_version=2,
        mentioned_agent_ids=["agent-2"],
    )

    assert request.selected_agent_ids == ["agent-1", "agent-2"]
    assert request.candidate_scope_mode == "explicit_selection"
    assert request.candidate_scope_group_id == "group-1"
    assert request.orchestration_schema_version == 2
    assert request.mentioned_agent_ids == ["agent-2"]


@pytest.mark.asyncio
async def test_send_message_allows_v2_supervisor_selected_scope_with_mentions(
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
            "orchestration_schema_version": 2,
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
    assert execution_request.orchestration_schema_version == 2
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
            "orchestration_schema_version": 2,
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
        run_reader=SimpleNamespace(get_runs_for_room=AsyncMock(return_value=[])),
        cancellation_state=SimpleNamespace(
            cancel_message_and_broadcast=AsyncMock(),
            clear_cancellation=MagicMock(),
        ),
        cancellation_store=SimpleNamespace(cancel_message=AsyncMock(return_value=True)),
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
            resolve_client_request_id=AsyncMock(side_effect=lambda _, provided: provided),
        ),
    )


@pytest.mark.asyncio
async def test_execution_facade_passes_scope_fields_to_room_request():
    room_center = SimpleNamespace(
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
            orchestration_schema_version=2,
            mentioned_agent_ids=["agent-2"],
        )
    )

    room_request = room_center.persist_message_to_room.await_args.args[0]
    assert room_request.extend_info == {
        "mode": "supervisor",
        "selected_agent_ids": ["agent-1", "agent-2"],
        "candidate_scope_mode": "explicit_selection",
        "candidate_scope_group_id": "group-1",
        "orchestration_schema_version": 2,
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
async def test_v2_supervisor_preflight_persists_lightweight_orchestration_envelope():
    svc = object.__new__(RoomServices)
    svc._store = MagicMock()
    svc._store.get_room_by_room_id = AsyncMock(
        return_value=Room(
            room_id="room-1",
            room_name="Room",
            room_owner_id="user-1",
            room_owner_name="User",
            room_agent_set={"agent-1": "Agent One", "agent-2": "Agent Two"},
            extend_info={"use_supervisor": True, "debateMode": False},
        )
    )
    agents = {
        "agent-1": _agent("agent-1", "Agent One"),
        "agent-2": _agent("agent-2", "Agent Two"),
    }
    svc._store.get_agent_by_agent_id = AsyncMock(side_effect=lambda aid: agents.get(aid))
    svc._store.get_room_memory_by_room_id = AsyncMock(
        side_effect=AssertionError("v2 scope envelope should not assemble room memory")
    )
    svc._store.update_room_user_message_by_message_id = AsyncMock(return_value=True)
    svc.delivery = SimpleNamespace(create_token=MagicMock(return_value=object()))
    svc._validate_send_message_request = MagicMock(return_value=None)
    svc._resolve_and_apply_attachments = AsyncMock(return_value=None)
    svc._materialize_room_quote = AsyncMock(return_value=None)
    svc._persist_user_message = AsyncMock(return_value=True)

    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
    )
    response, context = await svc.persist_message_to_room(
        RoomCenterUserMessageRequest(
            room_id="room-1",
            user_id="user-1",
            message=user_message,
            client_request_id="client-1",
            extend_info={
                "mode": "supervisor",
                "selected_agent_ids": ["agent-1", "agent-2"],
                "candidate_scope_mode": "explicit_selection",
                "candidate_scope_group_id": "group-1",
                "orchestration_schema_version": 2,
            },
        ),
        target_group="room_team",
        mentioned_agent_ids=["agent-2"],
    )
    assert response.success is True
    assert context is not None

    preflight_response = await svc.run_message_preflight_to_room(context)

    assert preflight_response.success is True
    assert user_message.extend_info == {
        "orchestration": True,
        "orchestration_schema_version": 2,
        "orchestration_run_id": "message-1",
        "candidate_scope_mode": "explicit_selection",
        "candidate_scope_group_id": "group-1",
        "candidate_agent_ids": ["agent-1", "agent-2"],
        "candidate_scope_snapshot_version": 1,
        "mentioned_agent_ids": ["agent-2"],
        "client_request_id": "client-1",
    }
    forbidden_keys = {
        "supervisor",
        "agent_registry",
        "room_config",
        "conversation_context",
        "explicit_mentions",
        "supervisor_trajectory",
    }
    assert forbidden_keys.isdisjoint(user_message.extend_info)
    svc._store.update_room_user_message_by_message_id.assert_awaited_once_with(
        "message-1",
        user_message,
    )
    svc._store.get_room_memory_by_room_id.assert_not_awaited()
