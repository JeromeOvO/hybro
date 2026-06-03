"""Tests for agent call counter tracking.

Covers:
- A2AService._record_call  (cloud agent path)
- RelayTransport.dispatch   (hub agent path)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hub_runtime_bridge.task_ownership import InMemoryHubTaskOwnershipStore
from models.processing import ProcessingStatus
from execution.dispatch.response_handler import AgentResponseHandler
from execution.dispatch.transports.relay import RelayTransport
from app_shell.a2a_runtime import A2AService

# ===========================================================================
# Helpers
# ===========================================================================


def _make_relay_transport(
    *,
    relay_service=None,
    db_service=None,
    sse_manager=None,
    ownership_store=None,
    ownership_lease_maintainer=None,
):
    handler = MagicMock(spec=AgentResponseHandler)
    handler.handle = AsyncMock()
    if relay_service is None:
        relay_service = MagicMock()
        relay_service.push_to_hub = AsyncMock(return_value=True)
    if db_service is None:
        db_service = MagicMock()
        db_service.enable_task_tracking_on_message = AsyncMock()
    if sse_manager is None:
        sse_manager = MagicMock()
        sse_manager.send_error = AsyncMock()
    return RelayTransport(
        response_handler=handler,
        relay_service=relay_service,
        db=db_service,
        sse_manager=sse_manager,
        ownership_store=ownership_store,
        ownership_lease_maintainer=ownership_lease_maintainer,
        worker_id="worker-1",
    )


def _make_dispatch_ctx(*, agent_id="agent-001", hub_id="hub-001"):
    """Build a minimal DispatchContext-like object for RelayTransport.dispatch."""
    from execution.dispatch.dispatch_middleware import DispatchContext

    agent = MagicMock()
    agent.agent_id = agent_id
    agent.hub_id = hub_id
    agent.local_agent_id = "local-001"
    agent.agent_card = MagicMock()
    agent.agent_card.url = "http://localhost:9000"

    prepared_message = MagicMock()
    prepared_message.model_dump = MagicMock(return_value={})

    room_agent_message = MagicMock()
    room_agent_message.message_id = "amsg-001"

    ctx = DispatchContext(
        agent=agent,
        room_agent_message=room_agent_message,
        room_id="room-001",
        user_message_id="umsg-001",
        prepared_message=prepared_message,
        transport="relay",
    )
    return ctx


def _make_room_agent_message(message_id="amsg-001"):
    msg = MagicMock()
    msg.message_id = message_id
    return msg


# ===========================================================================
# A2AService._record_call
# ===========================================================================


class TestA2AServiceRecordCall:
    @pytest.mark.asyncio
    async def test_records_success(self):
        svc = A2AService()
        with patch("app_shell.a2a_runtime.mongodb") as mock_db:
            mock_db.increment_agent_call_count = AsyncMock()
            await svc._record_call("agent-001", success=True)
            mock_db.increment_agent_call_count.assert_awaited_once_with(
                "agent-001", success=True,
            )

    @pytest.mark.asyncio
    async def test_records_failure(self):
        svc = A2AService()
        with patch("app_shell.a2a_runtime.mongodb") as mock_db:
            mock_db.increment_agent_call_count = AsyncMock()
            await svc._record_call("agent-001", success=False)
            mock_db.increment_agent_call_count.assert_awaited_once_with(
                "agent-001", success=False,
            )

    @pytest.mark.asyncio
    async def test_skips_none_agent_id(self):
        svc = A2AService()
        with patch("app_shell.a2a_runtime.mongodb") as mock_db:
            mock_db.increment_agent_call_count = AsyncMock()
            await svc._record_call(None, success=True)
            mock_db.increment_agent_call_count.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_empty_agent_id(self):
        svc = A2AService()
        with patch("app_shell.a2a_runtime.mongodb") as mock_db:
            mock_db.increment_agent_call_count = AsyncMock()
            await svc._record_call("", success=True)
            mock_db.increment_agent_call_count.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_swallows_db_exception(self):
        svc = A2AService()
        with patch("app_shell.a2a_runtime.mongodb") as mock_db:
            mock_db.increment_agent_call_count = AsyncMock(
                side_effect=RuntimeError("DB down"),
            )
            await svc._record_call("agent-001", success=True)


# ===========================================================================
# RelayTransport.dispatch — call counter tracking
# ===========================================================================


class TestRelayTransportDispatchCallCounter:
    @pytest.mark.asyncio
    async def test_claims_hub_task_ownership_before_dispatch(self):
        relay_svc = MagicMock()
        relay_svc.push_to_hub = AsyncMock(return_value=True)
        ownership = InMemoryHubTaskOwnershipStore()
        maintainer = MagicMock()
        rt = _make_relay_transport(
            relay_service=relay_svc,
            ownership_store=ownership,
            ownership_lease_maintainer=maintainer,
        )
        ctx = _make_dispatch_ctx()
        msg = _make_room_agent_message()

        await rt.dispatch(ctx, msg)

        owner = await ownership.resolve_owner("amsg-001")
        assert owner is not None
        assert owner["owner_id"] == "worker-1"
        assert owner["aliases"]["local_task_id"] == "relay-pending-amsg-001"
        maintainer.track.assert_called_once()

    @pytest.mark.asyncio
    async def test_records_success_on_delivered(self):
        relay_svc = MagicMock()
        relay_svc.push_to_hub = AsyncMock(return_value=True)
        rt = _make_relay_transport(relay_service=relay_svc)
        ctx = _make_dispatch_ctx()
        msg = _make_room_agent_message()

        with patch("execution.dispatch.transports.relay.mongodb") as mock_db:
            mock_db.increment_agent_call_count = AsyncMock()
            result = await rt.dispatch(ctx, msg)

            assert result.status == ProcessingStatus.RELAY_DISPATCHED
            mock_db.increment_agent_call_count.assert_awaited_once_with(
                "agent-001", success=True,
            )

    @pytest.mark.asyncio
    async def test_records_failure_on_not_delivered(self):
        relay_svc = MagicMock()
        relay_svc.push_to_hub = AsyncMock(return_value=False)
        rt = _make_relay_transport(relay_service=relay_svc)
        ctx = _make_dispatch_ctx()
        ctx.metadata["queued_for_offline"] = True
        msg = _make_room_agent_message()

        with patch("execution.dispatch.transports.relay.mongodb") as mock_db:
            mock_db.increment_agent_call_count = AsyncMock()
            result = await rt.dispatch(ctx, msg)

            assert result.status == ProcessingStatus.RELAY_DISPATCHED
            mock_db.increment_agent_call_count.assert_awaited_once_with(
                "agent-001", success=False,
            )

    @pytest.mark.asyncio
    async def test_dispatch_succeeds_when_counter_raises(self):
        relay_svc = MagicMock()
        relay_svc.push_to_hub = AsyncMock(return_value=True)
        rt = _make_relay_transport(relay_service=relay_svc)
        ctx = _make_dispatch_ctx()
        msg = _make_room_agent_message()

        with patch("execution.dispatch.transports.relay.mongodb") as mock_db:
            mock_db.increment_agent_call_count = AsyncMock(
                side_effect=RuntimeError("DB down"),
            )
            result = await rt.dispatch(ctx, msg)
            assert result.status == ProcessingStatus.RELAY_DISPATCHED
