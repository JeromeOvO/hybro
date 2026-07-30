"""Unit tests for SSE services.

Tests cover:
- SSEConnection: send_message, get_message (with timeout/heartbeat), close
- DeliveryFacade: cancel_message/is_cancelled/clear_cancellation lifecycle
- DeliveryFacade: CancellationToken creation and pre-signalling
- DeliveryFacade: add_connection/remove_connection and typed delivery helpers
"""

import json
from unittest.mock import AsyncMock

import pytest

from common.config import settings
from common.utils.time import utcnow
from delivery.sse.connection import SSEConnection
from tests.fakes.delivery import make_delivery_facade

# =============================================================================
# SSEConnection Tests
# =============================================================================


class TestSSEConnection:
    """Tests for SSEConnection message queue."""

    def _connection(self) -> SSEConnection:
        return SSEConnection(
            room_id="room-1",
            connection_id="conn-1",
            heartbeat_interval=0.01,
            now=utcnow,
        )

    @pytest.mark.asyncio
    async def test_send_message_queues_json(self):
        conn = self._connection()
        result = await conn.send_message("test_event", {"key": "value"})

        assert result is True
        parsed = await conn.queue.get()
        assert parsed["type"] == "test_event"
        assert parsed["room_id"] == "room-1"
        assert parsed["data"] == {"key": "value"}

    @pytest.mark.asyncio
    async def test_send_message_fails_when_closed(self):
        conn = self._connection()
        conn.close()
        result = await conn.send_message("test", {})
        assert result is False

    @pytest.mark.asyncio
    async def test_get_message_returns_queued_data(self):
        conn = self._connection()
        await conn.send_message("ping", {})
        msg = await conn.get_message(timeout=1.0)
        assert msg is not None
        assert json.loads(msg)["type"] == "ping"

    @pytest.mark.asyncio
    async def test_get_message_returns_heartbeat_on_timeout(self):
        conn = self._connection()
        msg = await conn.get_message(timeout=0.01)
        parsed = json.loads(msg)
        assert parsed["type"] == "heartbeat"
        assert parsed["room_id"] == "room-1"

    def test_close_marks_inactive(self):
        conn = self._connection()
        assert conn.is_active is True
        conn.close()
        assert conn.is_active is False

        # =============================================================================
        # DeliveryFacade Cancellation Tests
        # =============================================================================


class TestDeliveryFacadeCancellation:
    """Tests for message cancellation lifecycle."""

    def test_cancel_then_is_cancelled(self):
        mgr = make_delivery_facade()
        assert mgr.is_cancelled("msg-1") is False
        mgr.cancel_message("msg-1")
        assert mgr.is_cancelled("msg-1") is True

    def test_clear_cancellation(self):
        mgr = make_delivery_facade()
        mgr.cancel_message("msg-1")
        mgr.clear_cancellation("msg-1")
        assert mgr.is_cancelled("msg-1") is False

    def test_clear_also_removes_token(self):
        mgr = make_delivery_facade()
        mgr.create_token("msg-1")
        mgr.clear_cancellation("msg-1")
        assert mgr.get_token("msg-1") is None

        # =============================================================================
        # CancellationToken Tests
        # =============================================================================


class TestCancellationToken:
    """Tests for CancellationToken creation and pre-signalling."""

    def test_create_token_returns_token(self):
        mgr = make_delivery_facade()
        token = mgr.create_token("msg-1")
        assert token is not None
        assert token.message_id == "msg-1"
        assert mgr.get_token("msg-1") is token

    def test_create_token_pre_signals_if_already_cancelled(self):
        mgr = make_delivery_facade()
        mgr.cancel_message("msg-1")
        token = mgr.create_token("msg-1")
        assert token.is_cancelled is True

    def test_cancel_signals_existing_token(self):
        mgr = make_delivery_facade()
        token = mgr.create_token("msg-1")
        assert token.is_cancelled is False
        mgr.cancel_message("msg-1")
        assert token.is_cancelled is True

    def test_remove_token(self):
        mgr = make_delivery_facade()
        mgr.create_token("msg-1")
        mgr.remove_token("msg-1")
        assert mgr.get_token("msg-1") is None

        # =============================================================================
        # DeliveryFacade Connection Tests
        # =============================================================================


class TestDeliveryFacadeConnections:
    """Tests for connection management."""

    @pytest.mark.asyncio
    async def test_add_and_remove_connection(self):
        mgr = make_delivery_facade()
        conn = await mgr.add_connection("room-1")
        assert "room-1" in mgr.room_connections
        assert conn.connection_id in mgr.room_connections["room-1"]

        await mgr.remove_connection("room-1", conn.connection_id)
        assert "room-1" not in mgr.room_connections

    @pytest.mark.asyncio
    async def test_typed_helper_delivers_to_all_connections(self):
        mgr = make_delivery_facade()
        c1 = await mgr.add_connection("room-1")
        c2 = await mgr.add_connection("room-1")

        await mgr.send_processing_status("room-1", "processing", "msg-1")

        for conn in [c1, c2]:
            parsed = await conn.queue.get()
            assert parsed["type"] == "processing_status"
            assert parsed["data"]["message_id"] == "msg-1"

    @pytest.mark.asyncio
    async def test_typed_helper_to_empty_room_is_noop(self):
        mgr = make_delivery_facade()
        await mgr.send_processing_status("nonexistent", "processing", "msg-1")

        # =============================================================================
        # send_processing_status client_request_id Tests
        # =============================================================================


class TestSendProcessingStatusClientRequestId:
    """Tests that client_request_id is included/omitted correctly in SSE payload."""

    @pytest.mark.asyncio
    async def test_send_processing_status_includes_client_request_id(self):
        mgr = make_delivery_facade()
        conn = await mgr.add_connection("room-1")

        await mgr.send_processing_status(
            "room-1", "processing", "msg-1", client_request_id="cr-abc"
        )

        parsed = await conn.queue.get()
        assert parsed["type"] == "processing_status"
        assert parsed["data"]["client_request_id"] == "cr-abc"
        assert parsed["data"]["message_id"] == "msg-1"

    @pytest.mark.asyncio
    async def test_send_processing_status_omits_client_request_id_when_none(self):
        mgr = make_delivery_facade()
        conn = await mgr.add_connection("room-1")

        await mgr.send_processing_status("room-1", "processing", "msg-1")

        parsed = await conn.queue.get()
        assert parsed["type"] == "processing_status"
        assert "client_request_id" not in parsed["data"]

    @pytest.mark.asyncio
    async def test_send_processing_status_does_not_record_or_emit_run_event(
        self, monkeypatch
    ):
        import execution.run_command_handler as handler_mod

        mgr = make_delivery_facade()
        conn = await mgr.add_connection("room-1")
        record = AsyncMock(
            return_value={
                "event_id": "evt-1",
                "run_id": "msg-1",
                "seq": 1,
                "type": "RUN_STARTED",
                "payload": {},
            }
        )
        monkeypatch.setattr(settings, "feature_run_event_sse", True)
        monkeypatch.setattr(
            handler_mod.run_command_handler,
            "record_processing_status",
            record,
        )

        await mgr.send_processing_status("room-1", "processing", "msg-1")

        record.assert_not_awaited()
        parsed = await conn.queue.get()
        assert parsed["type"] == "processing_status"
        assert conn.queue.empty()
