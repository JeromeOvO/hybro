"""
Flow contract tests for critical user journeys.

These tests call real endpoint functions and real service methods, mocking
only at the infrastructure boundary (database, external HTTP, SSE).
They verify that multi-step flows produce correct end-to-end behavior
through actual application code.

For HTTP-layer integration tests, see test_api_integration.py.

Tests cover:
1. Room lifecycle (create -> send message -> get messages)
2. Agent lifecycle (register -> query -> delete)
3. HITL flow (request -> list pending -> cancel)
4. A2A task flow (poll status -> list pending)
5. Message cancellation
6. Error handling
"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from a2a.types import AgentCard, AgentSkill, AgentCapabilities, Task, TaskStatus, TaskState

from common.auth import ClerkUser
from common.dto import ExecutionAck
from common.dto.agent import AgentInfo
from models.agent import Agent, AgentStatus
from models.room import Room, RoomUserMessage, RoomAgentMessage, MessageContent
from models.hitl import HITLRequest, HITLStatus, HITLPromptType
from models.response import (
    RoomCenterRoomSettingResponse,
    RoomCenterUserMessageResponse,
    RoomCenterRoomMessageResponse,
    AgentCenterResponse,
)
from tests.conftest import PATCH


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def flow_user() -> ClerkUser:
    return ClerkUser(
        user_id="flow_user_001",
        session_id="flow_session_001",
        claims={"sub": "flow_user_001", "email": "flow@test.com"},
    )


# =============================================================================
# Room Lifecycle Flow Tests
# =============================================================================


class TestRoomLifecycleFlow:
    """Flow tests that exercise real endpoint functions for room CRUD."""

    @pytest.mark.asyncio
    async def test_create_room_and_send_message_flow(self, flow_user):
        """Create room -> ownership check -> send message -> query messages.

        Each step calls the REAL endpoint function; only the modules/services
        behind the endpoint are mocked.
        """
        from api.room_center import (
            create_new_room,
            inquiry_room_setting,
            send_message,
            inquiry_room_messages,
        )

        room_id = "flow-room-001"
        message_id = "flow-msg-001"

        mock_room = Room(
            room_id=room_id,
            room_name="Flow Test Room",
            room_owner_id=flow_user.user_id,
            room_owner_name="Flow User",
            room_agent_set={"agent-1": "TestAgent"},
        )

        user_msg = RoomUserMessage(
            room_id=room_id,
            message_id=message_id,
            user_id=flow_user.user_id,
            message_content=MessageContent(message_text="Hello agents!"),
        )

        mock_db = MagicMock()
        mock_db.get_room_by_room_id = AsyncMock(return_value=mock_room)

        mock_rc = MagicMock()
        mock_rc.create_new_room = AsyncMock(
            return_value=RoomCenterRoomSettingResponse(
                success=True, room_id=room_id, room=mock_room,
            )
        )
        mock_rc.inquiry_room_setting = AsyncMock(
            return_value=RoomCenterRoomSettingResponse(success=True, room=mock_room)
        )
        mock_rc.send_message_to_room = AsyncMock(
            return_value=RoomCenterUserMessageResponse(
                success=True, message_id=message_id,
            )
        )
        mock_rc.inquiry_room_messages_by_room_id = AsyncMock(
            return_value=RoomCenterRoomMessageResponse(
                success=True, message_list=[user_msg],
            )
        )

        mock_rmc = MagicMock()
        mock_rmc.process_room_user_message = AsyncMock()
        mock_execution_engine = MagicMock()
        mock_execution_engine.execute = AsyncMock(
            return_value=ExecutionAck(success=True, message_id=message_id)
        )
        mock_execution_engine.start_orchestration = AsyncMock()

        with patch(PATCH["room_center.db_service"], mock_db):
            with patch(PATCH["room_center.room_center"], mock_rc):
                with patch(PATCH["room_center.room_message_center"], mock_rmc):
                    with patch("api.room_center.execution_engine", mock_execution_engine):
                        # Step 1: Create room (real endpoint parses request JSON,
                        # builds RoomCenterRoomSettingRequest, calls room_center)
                        req1 = MagicMock()
                        req1.json = AsyncMock(return_value={
                            "room_name": "Flow Test Room",
                            "room_owner_name": "Flow User",
                            "room_agent_set": {"agent-1": "TestAgent"},
                        })
                        create_resp = await create_new_room(req1, flow_user)
                        assert create_resp.success is True
                        assert create_resp.room_id == room_id

                        # Verify endpoint set room_owner_id from auth user
                        create_call = mock_rc.create_new_room.call_args[0][0]
                        assert create_call.room_owner_id == flow_user.user_id

                        # Step 2: Query room setting (real endpoint verifies ownership)
                        req2 = MagicMock()
                        req2.json = AsyncMock(return_value={"room_id": room_id})
                        setting_resp = await inquiry_room_setting(req2, flow_user)
                        assert setting_resp.success is True
                        assert setting_resp.room.room_id == room_id

                        # Step 3: Send message (real endpoint builds request,
                        # calls execution engine, queues background task)
                        req3 = MagicMock()
                        req3.json = AsyncMock(return_value={
                            "room_id": room_id,
                            "message": user_msg.model_dump(),
                            "target_group": "room_team",
                            "client_request_id": "c7c9a000-0000-4000-8000-000000000003",
                        })
                        bg = MagicMock()
                        send_resp = await send_message(req3, bg, flow_user)
                        assert send_resp.success is True
                        assert send_resp.message_id == message_id
                        bg.add_task.assert_called_once()

                        # Step 4: Query messages
                        req4 = MagicMock()
                        req4.json = AsyncMock(return_value={"room_id": room_id})
                        msgs_resp = await inquiry_room_messages(req4, flow_user)
                        assert msgs_resp.success is True
                        assert len(msgs_resp.message_list) == 1

    @pytest.mark.asyncio
    async def test_room_ownership_enforcement(self, flow_user):
        """Verify that a non-owner is blocked at every ownership-gated step."""
        from api.room_center import (
            inquiry_active_runs,
            inquiry_room_setting,
            send_message,
            inquiry_room_messages,
        )
        from fastapi import HTTPException

        other_user = ClerkUser(
            user_id="other_user_999",
            session_id="s",
            claims={"sub": "other_user_999"},
        )

        mock_room = Room(
            room_id="guarded-room",
            room_name="Not Yours",
            room_owner_id=flow_user.user_id,
            room_owner_name="Flow User",
            room_agent_set={},
        )

        mock_db = MagicMock()
        mock_db.get_room_by_room_id = AsyncMock(return_value=mock_room)

        with patch(PATCH["room_center.db_service"], mock_db):
            for endpoint_fn in [
                inquiry_room_setting,
                inquiry_active_runs,
                inquiry_room_messages,
            ]:
                req = MagicMock()
                req.json = AsyncMock(return_value={"room_id": "guarded-room"})
                with pytest.raises(HTTPException) as exc:
                    await endpoint_fn(req, other_user)
                assert exc.value.status_code == 403

            # send_message has a different signature (request, background_tasks, user)
            req = MagicMock()
            req.json = AsyncMock(return_value={
                "room_id": "guarded-room",
                "message": {"message_text": "x"},
                "client_request_id": "c7c9a000-0000-4000-8000-000000000004",
            })
            with pytest.raises(HTTPException) as exc:
                await send_message(req, MagicMock(), other_user)
            assert exc.value.status_code == 403


# =============================================================================
# Agent Lifecycle Flow Tests
# =============================================================================


class TestAgentLifecycleFlow:
    """Flow tests that exercise real agent endpoint functions."""

    @pytest.mark.asyncio
    async def test_register_query_and_delete_agent_flow(self, flow_user):
        """register_agent -> get_agent -> delete_agent through real endpoints."""
        from api.agent import register_agent, get_agent, delete_agent

        agent_id = "flow-agent-001"

        mock_agent_card = AgentCard(
            name="Flow Agent",
            description="Agent for flow testing",
            url="https://flow-agent.example.com/.well-known/agent.json",
            version="1.0.0",
            skills=[AgentSkill(
                id="s1", name="Skill", description="Test", tags=["test"],
            )],
            capabilities=AgentCapabilities(streaming=True),
            defaultInputModes=["text"],
            defaultOutputModes=["text"],
        )

        mock_agent = Agent(
            agent_id=agent_id,
            provider_id=flow_user.user_id,
            agent_card=mock_agent_card,
            agent_status=AgentStatus.active,
            is_public=True,
        )

        mock_ac = MagicMock()
        mock_ac.register_agent = AsyncMock(
            return_value=AgentCenterResponse(
                success=True, agent_id=agent_id, agent=mock_agent,
            )
        )
        mock_ac.query_agent_by_agent_id = AsyncMock(
            return_value=AgentCenterResponse(success=True, agent=mock_agent)
        )
        mock_ac.remove_agent = AsyncMock(
            return_value=AgentCenterResponse(success=True)
        )
        mock_ac._mask_sensitive_information = MagicMock(side_effect=lambda r, _: r)

        mock_as = MagicMock()
        mock_as.get_agent_by_agent_id = AsyncMock(return_value=mock_agent)

        with patch(PATCH["agent.agent_center"], mock_ac):
            with patch(PATCH["agent.agent_service"], mock_as):
                with patch(
                    "api.agent.agent_liveness_checker",
                    new=AsyncMock(return_value=mock_agent),
                ):
                    # Step 1: Register (real endpoint extracts agent_url,
                    # sets provider_id from auth user, calls agent_center)
                    req1 = MagicMock()
                    req1.json = AsyncMock(return_value={
                        "agent_url": "https://flow-agent.example.com/.well-known/agent.json",
                    })
                    reg_resp = await register_agent(req1, flow_user)
                    assert reg_resp.success is True
                    assert reg_resp.agent_id == agent_id

                    reg_call = mock_ac.register_agent.call_args[0][0]
                    assert reg_call.provider_id == flow_user.user_id

                    # Step 2: Query (real endpoint constructs AgentCenterRequest)
                    query_resp = await get_agent(agent_id, user=flow_user)
                    assert query_resp.success is True
                    assert query_resp.agent.agent_id == agent_id

                    query_call = mock_ac.query_agent_by_agent_id.call_args[0][0]
                    assert query_call.user_id == flow_user.user_id

                    # Step 3: Delete (real endpoint verifies ownership,
                    # then calls agent_center.remove_agent)
                    req3 = MagicMock()
                    req3.json = AsyncMock(return_value={"agent_id": agent_id})
                    del_resp = await delete_agent(req3, flow_user)
                    assert del_resp.success is True

    @pytest.mark.asyncio
    async def test_private_agent_visibility(self, flow_user):
        """Private agent: owner sees it, others get 404."""
        from services.agent_service import AgentService
        from models.request import AgentCenterRequest

        agent_id = "private-flow-001"

        private_agent = Agent(
            agent_id=agent_id,
            provider_id=flow_user.user_id,
            agent_card=AgentCard(
                name="Private Agent",
                description="Private",
                url="https://private.example.com",
                version="1.0.0",
                skills=[],
                capabilities=AgentCapabilities(),
                defaultInputModes=["text"],
                defaultOutputModes=["text"],
            ),
            agent_status=AgentStatus.active,
            is_public=False,
        )

        svc = AgentService()
        facade = MagicMock()
        facade.get_agent = AsyncMock(
            return_value=AgentInfo(
                agent_id=private_agent.agent_id,
                provider_id=private_agent.provider_id,
                name=private_agent.agent_card.name,
                description=private_agent.agent_card.description,
                url=private_agent.agent_card.url,
                status=private_agent.agent_status.value,
                is_public=False,
            )
        )
        svc.bind_facade(facade)

        owner_resp = await svc.query_agent_by_agent_id(
            AgentCenterRequest(agent_id=agent_id, user_id=flow_user.user_id)
        )
        assert owner_resp.success is True

        stranger_resp = await svc.query_agent_by_agent_id(
            AgentCenterRequest(agent_id=agent_id, user_id="stranger")
        )
        assert stranger_resp.success is False
        assert stranger_resp.status_code == 404


# =============================================================================
# HITL Flow Tests
# =============================================================================


class TestHITLFlow:
    """Flow tests for HITL request_input -> get_pending -> cancel."""

    @pytest.mark.asyncio
    async def test_complete_hitl_flow(self):
        """request_input -> get_pending_requests -> cancel_request."""
        from services.hitl_service import HITLService

        room_id = "hitl-flow-room"
        msg_id = "hitl-flow-msg"
        req_id = "hitl-flow-req"

        mock_req = HITLRequest(
            request_id=req_id,
            room_id=room_id,
            user_message_id=msg_id,
            source="supervisor",
            prompt="Clarify please",
            prompt_type=HITLPromptType.TEXT,
            status=HITLStatus.PENDING,
        )

        mock_db = MagicMock()
        mock_db.create_hitl_request = AsyncMock(return_value=True)
        mock_db.count_hitl_requests_for_message = AsyncMock(return_value=0)
        mock_db.get_pending_hitl_requests = AsyncMock(
            return_value=[mock_req.model_dump(mode="json")]
        )
        mock_db.get_hitl_request = AsyncMock(
            return_value=mock_req.model_dump(mode="json")
        )
        mock_db.cas_update_hitl_request = AsyncMock(return_value=True)

        mock_sse = MagicMock()
        mock_sse.broadcast_to_room = AsyncMock()

        svc = HITLService()
        svc._db_service = mock_db
        svc._sse_manager = mock_sse

        # Step 1: request_input
        created = await svc.request_input(
            room_id=room_id,
            user_message_id=msg_id,
            source="supervisor",
            prompt="Clarify please",
        )
        assert created is not None
        assert created.status == HITLStatus.PENDING
        mock_sse.broadcast_to_room.assert_called()

        # Step 2: get_pending_requests
        pending = await svc.get_pending_requests(room_id)
        assert len(pending) == 1
        assert pending[0].request_id == req_id

        # Step 3: cancel_request
        await svc.cancel_request(req_id, room_id=room_id)
        mock_db.cas_update_hitl_request.assert_awaited_once_with(
            req_id,
            expected_status=HITLStatus.PENDING.value,
            status=HITLStatus.CANCELED.value,
        )

    @pytest.mark.asyncio
    async def test_hitl_max_rounds_enforcement(self):
        """Should return None when max rounds exceeded."""
        from services.hitl_service import HITLService, MAX_HITL_ROUNDS

        svc = HITLService()
        mock_db = MagicMock()
        mock_db.count_hitl_requests_for_message = AsyncMock(
            return_value=MAX_HITL_ROUNDS,
        )
        svc._db_service = mock_db

        result = await svc.request_input(
            room_id="r", user_message_id="m", source="supervisor",
            prompt="x", continuation_message_id="c",
        )
        assert result is None


# =============================================================================
# A2A Task Flow Tests
# =============================================================================


class TestA2ATaskFlow:
    """Flow tests that call real A2A task endpoint functions."""

    @pytest.mark.asyncio
    async def test_task_status_polling_flow(self, flow_user):
        """get_task_status through the real endpoint."""
        from api.a2a_tasks import get_task_status

        msg_id = "task-flow-msg"
        mock_task = Task(
            id="task-flow-001",
            contextId="ctx-flow-001",
            status=TaskStatus(state=TaskState.working),
        )
        mock_msg = RoomAgentMessage(
            room_id="room-t",
            message_id=msg_id,
            agent_id="agent-t",
            user_id=flow_user.user_id,
            message_content=MessageContent(
                message_text="Working...", message_task=mock_task,
            ),
            has_task_tracking=True,
            task_created_at=datetime.now(),
        )

        mock_db = MagicMock()
        mock_db.get_room_agent_message_by_message_id = AsyncMock(
            return_value=mock_msg,
        )

        with patch(PATCH["a2a_tasks.db_service"], mock_db):
            result = await get_task_status(msg_id, flow_user)

        assert result["message_id"] == msg_id
        assert result["status"] == "working"

    @pytest.mark.asyncio
    async def test_list_user_pending_tasks(self, flow_user):
        """list_user_pending_tasks through the real endpoint."""
        from api.a2a_tasks import list_user_pending_tasks

        msgs = []
        for i in range(3):
            t = Task(
                id=f"t-{i}", contextId=f"c-{i}",
                status=TaskStatus(state=TaskState.working),
            )
            msgs.append(RoomAgentMessage(
                room_id=f"r-{i}",
                message_id=f"m-{i}",
                agent_id=f"a-{i}",
                user_id=flow_user.user_id,
                message_content=MessageContent(
                    message_text="...", message_task=t,
                ),
                has_task_tracking=True,
                task_created_at=datetime.now(),
            ))

        mock_db = MagicMock()
        mock_db.get_pending_task_messages_for_user = AsyncMock(
            return_value=msgs,
        )

        with patch(PATCH["a2a_tasks.db_service"], mock_db):
            result = await list_user_pending_tasks(flow_user)

        assert len(result["tasks"]) == 3


# =============================================================================
# Message Cancellation Flow Tests
# =============================================================================


class TestMessageCancellationFlow:
    """cancel_message through the real endpoint function."""

    @pytest.mark.asyncio
    async def test_cancel_message_flow(self, flow_user):
        from api.sse import cancel_message

        room_id = "cancel-flow-room"
        msg_id = "cancel-flow-msg"

        mock_room = Room(
            room_id=room_id,
            room_name="Cancel Room",
            room_owner_id=flow_user.user_id,
            room_owner_name="Flow User",
            room_agent_set={},
        )
        mock_msg = RoomUserMessage(
            room_id=room_id,
            message_id=msg_id,
            user_id=flow_user.user_id,
            message_content=MessageContent(message_text="Cancel me"),
        )

        mock_db = MagicMock()
        mock_db.get_room_user_message_by_message_id = AsyncMock(return_value=mock_msg)
        mock_db.get_room_by_room_id = AsyncMock(return_value=mock_room)
        mock_db.get_room_agent_messages_by_related_message_id = AsyncMock(return_value=[])
        mock_db.update_task_state_on_message = AsyncMock(return_value=True)

        mock_mongodb = MagicMock()
        mock_mongodb.cancel_message = AsyncMock(return_value=True)

        mock_sse = MagicMock()
        mock_sse.cancel_message_and_broadcast = AsyncMock()
        mock_sse.send_processing_status = AsyncMock()

        mock_hitl = MagicMock()
        mock_hitl.cancel_requests_for_message = AsyncMock()
        mock_execution_engine = MagicMock()
        mock_execution_engine.cancel = AsyncMock(return_value=True)

        with patch(PATCH["sse.db_service"], mock_db):
            with patch("api.sse.execution_engine", mock_execution_engine):
                result = await cancel_message(msg_id, flow_user)

        assert result["success"] is True
        assert result["message_id"] == msg_id
        mock_execution_engine.cancel.assert_awaited_once_with(
            room_id=room_id,
            message_id=msg_id,
            requested_by_user_id=flow_user.user_id,
        )


# =============================================================================
# Error Handling Flow Tests
# =============================================================================


class TestErrorHandlingFlow:
    """Verify graceful error handling through real service code."""

    @pytest.mark.asyncio
    async def test_graceful_db_error_handling(self):
        from services.agent_service import AgentService
        from models.request import AgentCenterRequest

        svc = AgentService()
        facade = MagicMock()
        facade.list_visible_agents = AsyncMock(
            side_effect=Exception("Database connection failed"),
        )
        svc.bind_facade(facade)

        result = await svc.get_all_active_agents(AgentCenterRequest())
        assert result.success is False
        assert result.status_code == 500
        assert "failed" in result.error.lower()

    @pytest.mark.asyncio
    async def test_ownership_blocks_unauthorized_user(self):
        from fastapi import HTTPException
        from api.room_center import verify_room_ownership

        other_user = ClerkUser(
            user_id="unauthorized", session_id="s", claims={},
        )
        mock_room = Room(
            room_id="r", room_name="R",
            room_owner_id="real_owner",
            room_owner_name="Owner",
            room_agent_set={},
        )
        mock_db = MagicMock()
        mock_db.get_room_by_room_id = AsyncMock(return_value=mock_room)

        with patch(PATCH["room_center.db_service"], mock_db):
            with pytest.raises(HTTPException) as exc:
                await verify_room_ownership("r", other_user)
            assert exc.value.status_code == 403
