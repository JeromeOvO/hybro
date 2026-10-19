"""Unit tests for SSE services.

Tests cover:
- SSEConnection: send_message, get_message (with timeout/heartbeat), close
- DeliveryFacade: add_connection/remove_connection and typed delivery helpers
"""

import json

import pytest

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
