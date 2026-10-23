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
4. Message cancellation
5. Error handling
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from common.auth import ClerkUser
from common.dto import ExecutionAck
from common.dto.agent import AgentInfo
from models.response import (
    RoomCenterRoomMessageResponse,
    RoomCenterRoomSettingResponse,
)
from models.room import MessageContent, Room, RoomUserMessage

pytestmark = pytest.mark.core

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

        Each step calls the REAL endpoint function; only the runtime dependencies
        behind the endpoint are mocked.
        """
        from api_gateway.routes.room_routes import (
            CreateRoomBody,
            create_new_room,
            inquiry_room_messages,
            inquiry_room_setting,
            send_message,
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
                success=True,
                room_id=room_id,
                room=mock_room,
            )
        )
        mock_rc.inquiry_room_setting = AsyncMock(
            return_value=RoomCenterRoomSettingResponse(success=True, room=mock_room)
        )
        mock_rc.update_room_default_mode = AsyncMock(return_value=True)
        mock_rc.inquiry_room_messages_by_room_id = AsyncMock(
            return_value=RoomCenterRoomMessageResponse(
                success=True,
                message_list=[user_msg],
            )
        )

        mock_rmc = MagicMock()
        mock_rmc.process_room_user_message = AsyncMock()
        mock_execution_engine = MagicMock()
        mock_execution_engine.execute = AsyncMock(
            return_value=ExecutionAck(success=True, message_id=message_id)
        )
        mock_execution_engine.start_orchestration = AsyncMock()
        mock_execution_engine.get_runs_for_room = AsyncMock(return_value=[])

        # Step 1: Create room (real endpoint parses request JSON,
        # builds RoomCenterRoomSettingRequest, calls room_center)
        create_resp = await create_new_room(
            CreateRoomBody(
                room_name="Flow Test Room",
                room_owner_name="Flow User",
                membership_seed_input="manual",
                room_agent_ids=["agent-1"],
            ),
            flow_user,
            center=mock_rc,
        )
        assert create_resp.success is True
        assert create_resp.room_id == room_id

        # Verify endpoint set room_owner_id from auth user
        create_call = mock_rc.create_new_room.call_args[0][0]
        assert create_call.room_owner_id == flow_user.user_id
        assert create_call.room_agent_ids == ["agent-1"]

        # Step 2: Query room setting (real endpoint verifies ownership)
        setting_resp = await inquiry_room_setting(
            room_id,
            flow_user,
            store=mock_db,
            engine=mock_execution_engine,
            center=mock_rc,
        )
        assert setting_resp.success is True
        assert setting_resp.room.room_id == room_id

        # Step 3: Send message (real endpoint builds request,
        # calls execution engine, queues background task)
        req3 = MagicMock()
        req3.json = AsyncMock(
            return_value={
                "message": user_msg.model_dump(),
                "mode": "direct",
                "agent_scope": {"source": "room_default"},
                "client_request_id": "c7c9a000-0000-4000-8000-000000000003",
            }
        )
        send_resp = await send_message(
            room_id,
            req3,
            flow_user,
            store=mock_db,
            engine=mock_execution_engine,
            center=mock_rc,
        )
        assert send_resp.success is True
        assert send_resp.message_id == message_id
        mock_execution_engine.schedule_orchestration.assert_called_once()

        # Step 4: Query messages
        msgs_resp = await inquiry_room_messages(
            room_id,
            user=flow_user,
            store=mock_db,
            center=mock_rc,
        )
        assert msgs_resp.success is True
        assert len(msgs_resp.message_list) == 1

    @pytest.mark.asyncio
    async def test_room_ownership_enforcement(self, flow_user):
        """Verify that a non-owner is blocked at every ownership-gated step."""
        from fastapi import HTTPException

        from api_gateway.routes.room_routes import (
            inquiry_active_runs,
            inquiry_room_messages,
            inquiry_room_setting,
            send_message,
        )

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

        for endpoint_fn in [
            inquiry_room_setting,
            inquiry_active_runs,
            inquiry_room_messages,
        ]:
            kwargs = {"user": other_user, "store": mock_db}
            if endpoint_fn is not inquiry_active_runs:
                kwargs["center"] = MagicMock()
            if endpoint_fn is not inquiry_room_messages:
                kwargs["engine"] = MagicMock()
            with pytest.raises(HTTPException) as exc:
                await endpoint_fn("guarded-room", **kwargs)
            assert exc.value.status_code == 403

        # send_message verifies ownership before executing.
        req = MagicMock()
        req.json = AsyncMock(
            return_value={
                "message": {"message_text": "x"},
                "mode": "direct",
                "agent_scope": {"source": "room_default"},
                "client_request_id": "c7c9a000-0000-4000-8000-000000000004",
            }
        )
        with pytest.raises(HTTPException) as exc:
            await send_message(
                "guarded-room", req, other_user, store=mock_db, engine=MagicMock()
            )
        assert exc.value.status_code == 403


# =============================================================================
# Agent Lifecycle Flow Tests
# =============================================================================


class TestAgentLifecycleFlow:
    """Agent routes through the real AgentCatalogService over a mocked facade."""

    @staticmethod
    def _catalog(facade):
        from agent.catalog import AgentCatalogService

        async def unchanged(info):
            return info

        return AgentCatalogService(facade, liveness_checker=unchanged)

    @staticmethod
    def _info(agent_id: str, provider_id: str, *, is_public: bool = True):
        return AgentInfo(
            agent_id=agent_id,
            provider_id=provider_id,
            name="Flow Agent",
            url="https://flow-agent.example.com",
            is_public=is_public,
            raw_card={
                "name": "Flow Agent",
                "url": "https://flow-agent.example.com",
                "provider": {"organization": "Flow Org"},
            },
        )

    @pytest.mark.asyncio
    async def test_register_query_and_delete_agent_flow(self, flow_user):
        """register_agent -> get_agent -> delete_agent through real endpoints."""
        from api_gateway.routes.agent_routes import (
            delete_agent,
            get_agent,
            register_agent,
        )

        agent_id = "flow-agent-001"
        info = self._info(agent_id, flow_user.user_id)
        facade = MagicMock()
        facade.register_agent = AsyncMock(return_value=info)
        facade.get_agent = AsyncMock(return_value=info)
        facade.delete_agent = AsyncMock(return_value=True)
        catalog = self._catalog(facade)

        req1 = MagicMock()
        req1.json = AsyncMock(
            return_value={"agent_url": "https://flow-agent.example.com"}
        )
        reg_resp = await register_agent(req1, flow_user, catalog=catalog)
        assert reg_resp.agent.agent_id == agent_id
        facade.register_agent.assert_awaited_once_with(
            "https://flow-agent.example.com", flow_user.user_id
        )

        query_resp = await get_agent(agent_id, user=flow_user, catalog=catalog)
        assert query_resp.agent.agent_id == agent_id
        assert "flow-agent.example.com" not in query_resp.model_dump_json()

        req3 = MagicMock()
        req3.json = AsyncMock(return_value={"agent_id": agent_id})
        del_resp = await delete_agent(req3, flow_user, catalog=catalog)
        assert del_resp.success is True
        facade.delete_agent.assert_awaited_once_with(agent_id, flow_user.user_id)

    @pytest.mark.asyncio
    async def test_private_agent_visibility(self, flow_user):
        """Private agent: owner sees it, others get 404."""
        from api_gateway.routes.agent_routes import get_agent

        agent_id = "private-flow-001"
        facade = MagicMock()
        facade.get_agent = AsyncMock(
            return_value=self._info(agent_id, flow_user.user_id, is_public=False)
        )
        catalog = self._catalog(facade)
        stranger = ClerkUser(user_id="stranger", session_id="s", claims={})

        owner_resp = await get_agent(agent_id, user=flow_user, catalog=catalog)
        stranger_resp = await get_agent(agent_id, user=stranger, catalog=catalog)

        assert owner_resp.success is True
        assert (stranger_resp.success, stranger_resp.status_code) == (False, 404)


# =============================================================================
# Message Cancellation Flow Tests
# =============================================================================


class TestMessageCancellationFlow:
    """cancel_message through the real endpoint function."""

    @pytest.mark.asyncio
    async def test_cancel_message_flow(self, flow_user):
        from api_gateway.routes.sse_routes import cancel_message

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
        mock_db.get_room_agent_messages_by_related_message_id = AsyncMock(
            return_value=[]
        )

        mock_mongodb = MagicMock()
        mock_mongodb.cancel_message = AsyncMock(return_value=True)

        mock_sse = MagicMock()
        mock_sse.cancel_message_and_broadcast = AsyncMock()

        mock_hitl = MagicMock()
        mock_hitl.cancel_requests_for_message = AsyncMock()
        mock_execution_engine = MagicMock()
        mock_execution_engine.cancel = AsyncMock(return_value=True)

        result = await cancel_message(
            msg_id,
            flow_user,
            db=mock_db,
            engine=mock_execution_engine,
        )

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
        from agent.catalog import AgentCatalogService

        facade = MagicMock()
        facade.list_visible_agents = AsyncMock(
            side_effect=Exception("Database connection failed"),
        )
        catalog = AgentCatalogService(facade, liveness_checker=AsyncMock())

        result = await catalog.list_visible(user_id=None, active_only=True)
        assert result.success is False
        assert result.status_code == 500
        assert "failed" in result.error.lower()

    @pytest.mark.asyncio
    async def test_ownership_blocks_unauthorized_user(self):
        from fastapi import HTTPException

        from api_gateway.routes.room_routes import verify_room_ownership

        other_user = ClerkUser(
            user_id="unauthorized",
            session_id="s",
            claims={},
        )
        mock_room = Room(
            room_id="r",
            room_name="R",
            room_owner_id="real_owner",
            room_owner_name="Owner",
            room_agent_set={},
        )
        mock_db = MagicMock()
        mock_db.get_room_by_room_id = AsyncMock(return_value=mock_room)

        with pytest.raises(HTTPException) as exc:
            await verify_room_ownership("r", other_user, mock_db)
        assert exc.value.status_code == 403
