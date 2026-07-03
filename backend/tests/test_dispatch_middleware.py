"""
Unit tests for DispatchMiddleware architecture and HubTransportMiddleware.

Tests cover:
- DispatchChain execution order (pre-dispatch forward, post-dispatch reverse)
- ctx.denied short-circuit
- HubTransportMiddleware transport selection (cloud vs hub, online vs offline)
- Integration with AgentMessageProcessor relay dispatch path
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from a2a.types import AgentCapabilities, AgentCard, Message

from execution.dispatch.dispatch_middleware import DispatchChain, DispatchContext
from execution.dispatch.middleware.hub_transport import HubTransportMiddleware
from models.agent import Agent, AgentStatus
from models.processing import ProcessingResult, ProcessingStatus
from models.room import MessageContent, RoomAgentMessage

# ===========================================================================
# Helpers
# ===========================================================================


def _make_agent(**overrides) -> Agent:
    defaults = dict(
        agent_id="agent-001",
        provider_id="user-001",
        agent_card=AgentCard(
            name="Test",
            url="http://test.example.com",
            version="1.0",
            skills=[],
            description="Test",
            capabilities=AgentCapabilities(streaming=False),
            defaultInputModes=["text"],
            defaultOutputModes=["text"],
        ),
        agent_status=AgentStatus.active,
        source="cloud",
    )
    defaults.update(overrides)
    return Agent(**defaults)


def _make_ctx(**overrides) -> DispatchContext:
    agent = overrides.pop("agent", _make_agent())
    msg = overrides.pop(
        "room_agent_message",
        RoomAgentMessage(
            room_id="room-001",
            message_id="amsg-001",
            agent_id=agent.agent_id,
            message_content=MessageContent(message_text=""),
        ),
    )
    defaults = dict(
        agent=agent,
        room_agent_message=msg,
        room_id="room-001",
        user_message_id="umsg-001",
        prepared_message=MagicMock(spec=Message),
    )
    defaults.update(overrides)
    return DispatchContext(**defaults)


class _TrackingMiddleware:
    """Records call order for testing."""

    def __init__(self, name: str, call_log: list):
        self.name = name
        self._log = call_log

    async def pre_dispatch(self, ctx: DispatchContext) -> DispatchContext:
        self._log.append(f"pre:{self.name}")
        return ctx

    async def post_dispatch(
        self, ctx: DispatchContext, result: ProcessingResult
    ) -> ProcessingResult:
        self._log.append(f"post:{self.name}")
        return result


class _DenyMiddleware:
    async def pre_dispatch(self, ctx: DispatchContext) -> DispatchContext:
        ctx.denied = True
        ctx.deny_reason = "blocked"
        return ctx

    async def post_dispatch(
        self, ctx: DispatchContext, result: ProcessingResult
    ) -> ProcessingResult:
        return result


# ===========================================================================
# DispatchChain Tests
# ===========================================================================


class TestDispatchChain:
    @pytest.mark.asyncio
    async def test_pre_dispatch_runs_forward(self):
        log: list[str] = []
        chain = DispatchChain([
            _TrackingMiddleware("A", log),
            _TrackingMiddleware("B", log),
            _TrackingMiddleware("C", log),
        ])
        ctx = _make_ctx()
        await chain.run_pre_dispatch(ctx)
        assert log == ["pre:A", "pre:B", "pre:C"]

    @pytest.mark.asyncio
    async def test_post_dispatch_runs_reverse(self):
        log: list[str] = []
        chain = DispatchChain([
            _TrackingMiddleware("A", log),
            _TrackingMiddleware("B", log),
            _TrackingMiddleware("C", log),
        ])
        ctx = _make_ctx()
        result = ProcessingResult(ProcessingStatus.SUCCESS)
        await chain.run_post_dispatch(ctx, result)
        assert log == ["post:C", "post:B", "post:A"]

    @pytest.mark.asyncio
    async def test_denied_short_circuits(self):
        log: list[str] = []
        chain = DispatchChain([
            _TrackingMiddleware("A", log),
            _DenyMiddleware(),
            _TrackingMiddleware("C", log),
        ])
        ctx = _make_ctx()
        ctx = await chain.run_pre_dispatch(ctx)

        assert ctx.denied
        assert ctx.deny_reason == "blocked"
        assert log == ["pre:A"]

    @pytest.mark.asyncio
    async def test_empty_chain(self):
        chain = DispatchChain()
        ctx = _make_ctx()
        ctx = await chain.run_pre_dispatch(ctx)
        assert not ctx.denied
        assert ctx.transport == "direct"


# ===========================================================================
# HubTransportMiddleware Tests
# ===========================================================================


class TestHubTransportMiddleware:
    @pytest.mark.asyncio
    async def test_cloud_agent_stays_direct(self):
        relay = MagicMock()
        mw = HubTransportMiddleware(relay)
        ctx = _make_ctx(agent=_make_agent(source="cloud"))

        ctx = await mw.pre_dispatch(ctx)
        assert ctx.transport == "direct"
        assert "queued_for_offline" not in ctx.metadata

    @pytest.mark.asyncio
    async def test_hub_agent_online_sets_relay(self):
        relay = MagicMock()
        relay.is_hub_alive = AsyncMock(return_value=True)
        mw = HubTransportMiddleware(relay)
        ctx = _make_ctx(
            agent=_make_agent(source="hub", hub_id="hub-001")
        )

        ctx = await mw.pre_dispatch(ctx)
        assert ctx.transport == "relay"
        assert not ctx.denied

    @pytest.mark.asyncio
    async def test_hub_dispatch_port_cached_liveness_sets_relay(self):
        class CachedRelay:
            def __init__(self) -> None:
                self.calls = []

            def is_hub_online(self, hub_id):
                self.calls.append(hub_id)
                return True

        relay = CachedRelay()
        mw = HubTransportMiddleware(relay)
        ctx = _make_ctx(agent=_make_agent(source="hub", hub_id="hub-001"))

        ctx = await mw.pre_dispatch(ctx)

        assert ctx.transport == "relay"
        assert not ctx.denied
        assert relay.calls == ["hub-001"]

    @pytest.mark.asyncio
    async def test_hub_agent_offline_sets_relay_and_queued(self):
        relay = MagicMock()
        relay.is_hub_alive = AsyncMock(return_value=False)
        relay.mark_hub_agents_offline = AsyncMock()
        mw = HubTransportMiddleware(relay)
        ctx = _make_ctx(
            agent=_make_agent(source="hub", hub_id="hub-001")
        )

        ctx = await mw.pre_dispatch(ctx)
        assert ctx.transport == "relay"
        assert ctx.denied is True
        assert ctx.deny_reason is not None
        relay.mark_hub_agents_offline.assert_awaited_once_with("hub-001")

    @pytest.mark.asyncio
    async def test_post_dispatch_is_passthrough(self):
        relay = MagicMock()
        mw = HubTransportMiddleware(relay)
        ctx = _make_ctx()
        result = ProcessingResult(ProcessingStatus.SUCCESS)
        out = await mw.post_dispatch(ctx, result)
        assert out is result


# ===========================================================================
# AgentMessageProcessor — Relay Dispatch Integration
# ===========================================================================


class TestAMPRelayDispatch:
    def test_bind_relay_service_builds_execution_owned_relay_transport(self):
        from execution.dispatch.agent_message_processor import AgentMessageProcessor

        response_handler = MagicMock()
        direct_transport = MagicMock()
        direct_transport.response_handler = response_handler
        relay_svc = MagicMock()
        relay_svc.relay_transport = None
        relay_svc.agent_call_counter = MagicMock()
        relay_svc.ownership_lease_maintainer = MagicMock()

        amp = AgentMessageProcessor(
            delivery=MagicMock(),
            room_runtime=MagicMock(),
            room_memory_reader=MagicMock(),
            task_tracker=MagicMock(),
            transports={"direct": direct_transport},
        )

        amp.bind_relay_service(relay_svc)

        relay_transport = amp.transports["relay"]
        assert relay_transport.response_handler is response_handler
        assert relay_transport.relay_service is relay_svc
        assert relay_transport._call_counter is relay_svc.agent_call_counter
        assert relay_transport._ownership_lease_maintainer is relay_svc.ownership_lease_maintainer

    @pytest.mark.asyncio
    async def test_relay_transport_returns_relay_dispatched(self):
        from execution.dispatch.agent_message_processor import AgentMessageProcessor

        relay_svc = MagicMock()
        relay_svc.push_to_hub = AsyncMock(return_value=True)
        relay_svc.is_hub_alive = AsyncMock(return_value=True)

        chain = DispatchChain([HubTransportMiddleware(relay_svc)])

        room_runtime = MagicMock()
        process_resp = MagicMock()
        process_resp.success = True
        process_resp.a2a_message = MagicMock(spec=Message)
        process_resp.a2a_message.model_dump = MagicMock(return_value={})
        room_runtime.process_agent_message = AsyncMock(return_value=process_resp)

        db_service = MagicMock()
        db_service.get_room_memory_by_room_id = AsyncMock(return_value=None)

        relay_transport_mock = MagicMock()
        relay_transport_mock.dispatch = AsyncMock(
            return_value=ProcessingResult(ProcessingStatus.RELAY_DISPATCHED, response_text="", message_id="amsg-001")
        )

        amp = AgentMessageProcessor(
            delivery=MagicMock(),
            room_runtime=room_runtime,
            room_memory_reader=db_service,
            task_tracker=MagicMock(),
            transports={"direct": MagicMock(), "relay": relay_transport_mock},
            relay_service=relay_svc,
            dispatch_chain=chain,
        )

        agent = _make_agent(source="hub", hub_id="hub-001")
        msg = RoomAgentMessage(
            room_id="room-001",
            message_id="amsg-001",
            agent_id=agent.agent_id,
            message_content=MessageContent(message_text=""),
        )

        result = await amp.process_single_message(
            msg, "room-001", agent, "umsg-001"
        )

        assert result.status == ProcessingStatus.RELAY_DISPATCHED
        relay_transport_mock.dispatch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cloud_agent_uses_direct_path(self):
        from execution.dispatch.agent_message_processor import AgentMessageProcessor

        chain = DispatchChain()

        room_runtime = MagicMock()
        process_resp = MagicMock()
        process_resp.success = True
        process_resp.a2a_message = MagicMock(spec=Message)
        room_runtime.process_agent_message = AsyncMock(return_value=process_resp)

        db_service = MagicMock()
        db_service.get_room_memory_by_room_id = AsyncMock(return_value=None)

        dt = MagicMock()
        dt.dispatch = AsyncMock(
            return_value=ProcessingResult(ProcessingStatus.SUCCESS, response_text="response text")
        )

        amp = AgentMessageProcessor(
            delivery=MagicMock(),
            room_runtime=room_runtime,
            room_memory_reader=db_service,
            task_tracker=MagicMock(),
            transports={"direct": dt},
            dispatch_chain=chain,
        )

        agent = _make_agent(source="cloud")
        msg = RoomAgentMessage(
            room_id="room-001",
            message_id="amsg-002",
            agent_id=agent.agent_id,
            message_content=MessageContent(message_text=""),
        )

        result = await amp.process_single_message(
            msg, "room-001", agent, "umsg-001"
        )

        assert result.status == ProcessingStatus.SUCCESS
        dt.dispatch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_room_runtime_preflight_failure_preserves_reason_and_skips_transport(self):
        from execution.dispatch.agent_message_processor import AgentMessageProcessor

        room_runtime = MagicMock()
        process_resp = MagicMock()
        process_resp.success = False
        process_resp.error = "Attached file report.pdf exceeds the inline A2A limit."
        process_resp.a2a_message = None
        process_resp.message = None
        room_runtime.process_agent_message = AsyncMock(return_value=process_resp)

        db_service = MagicMock()
        db_service.get_room_memory_by_room_id = AsyncMock(return_value=None)

        dt = MagicMock()
        dt.dispatch = AsyncMock()

        amp = AgentMessageProcessor(
            delivery=MagicMock(),
            room_runtime=room_runtime,
            room_memory_reader=db_service,
            task_tracker=MagicMock(),
            transports={"direct": dt},
            dispatch_chain=DispatchChain(),
        )

        agent = _make_agent(source="cloud")
        msg = RoomAgentMessage(
            room_id="room-001",
            message_id="amsg-preflight",
            agent_id=agent.agent_id,
            message_content=MessageContent(message_text=""),
            extend_info={
                "attachment_preflight_failure": {
                    "code": "file_too_large",
                    "message": "Attached file report.pdf exceeds the inline A2A limit.",
                }
            },
        )

        result = await amp.process_single_message(
            msg,
            "room-001",
            agent,
            "umsg-001",
        )

        assert result.status == ProcessingStatus.FAILED
        assert (
            result.response_text
            == "Attached file report.pdf exceeds the inline A2A limit."
        )
        assert result.status_message == "file_too_large"
        dt.dispatch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_room_runtime_agent_card_preflight_failure_preserves_code(self):
        from execution.dispatch.agent_message_processor import AgentMessageProcessor

        room_runtime = MagicMock()
        process_resp = MagicMock()
        process_resp.success = False
        process_resp.error = "Agent card unavailable while preparing attached files."
        process_resp.a2a_message = None
        process_resp.message = None
        room_runtime.process_agent_message = AsyncMock(return_value=process_resp)

        db_service = MagicMock()
        db_service.get_room_memory_by_room_id = AsyncMock(return_value=None)

        dt = MagicMock()
        dt.dispatch = AsyncMock()

        amp = AgentMessageProcessor(
            delivery=MagicMock(),
            room_runtime=room_runtime,
            room_memory_reader=db_service,
            task_tracker=MagicMock(),
            transports={"direct": dt},
            dispatch_chain=DispatchChain(),
        )

        agent = _make_agent(source="cloud")
        msg = RoomAgentMessage(
            room_id="room-001",
            message_id="amsg-no-card",
            agent_id=agent.agent_id,
            message_content=MessageContent(message_text=""),
            extend_info={
                "attachment_preflight_failure": {
                    "code": "agent_card_unavailable",
                    "message": "Agent card unavailable while preparing attached files.",
                }
            },
        )

        result = await amp.process_single_message(
            msg,
            "room-001",
            agent,
            "umsg-001",
        )

        assert result.status == ProcessingStatus.FAILED
        assert (
            result.response_text
            == "Agent card unavailable while preparing attached files."
        )
        assert result.status_message == "agent_card_unavailable"
        dt.dispatch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_room_runtime_missing_prepared_message_preserves_preflight_reason(self):
        from execution.dispatch.agent_message_processor import AgentMessageProcessor

        room_runtime = MagicMock()
        process_resp = MagicMock()
        process_resp.success = True
        process_resp.error = "Attached file report.pdf could not be prepared."
        process_resp.a2a_message = None
        process_resp.message = None
        room_runtime.process_agent_message = AsyncMock(return_value=process_resp)

        db_service = MagicMock()
        db_service.get_room_memory_by_room_id = AsyncMock(return_value=None)

        dt = MagicMock()
        dt.dispatch = AsyncMock()

        amp = AgentMessageProcessor(
            delivery=MagicMock(),
            room_runtime=room_runtime,
            room_memory_reader=db_service,
            task_tracker=MagicMock(),
            transports={"direct": dt},
            dispatch_chain=DispatchChain(),
        )

        agent = _make_agent(source="cloud")
        msg = RoomAgentMessage(
            room_id="room-001",
            message_id="amsg-missing-prepared",
            agent_id=agent.agent_id,
            message_content=MessageContent(message_text=""),
            extend_info={
                "attachment_preflight_failure": {
                    "code": "file_prepare_failed",
                    "message": "Attached file report.pdf could not be prepared.",
                }
            },
        )

        result = await amp.process_single_message(
            msg,
            "room-001",
            agent,
            "umsg-001",
        )

        assert result.status == ProcessingStatus.FAILED
        assert result.response_text == "Attached file report.pdf could not be prepared."
        assert result.status_message == "file_prepare_failed"
        dt.dispatch.assert_not_awaited()
