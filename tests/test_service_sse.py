"""
Unit tests for SSE services (sse_services.py).

Tests cover:
- SSEConnection: send_message, get_message (with timeout/heartbeat), close
- SSEManager: cancel_message/is_cancelled/clear_cancellation lifecycle
- SSEManager: CancellationToken creation and pre-signalling
- SSEManager: add_connection/remove_connection/broadcast_to_room
"""

import asyncio
import json
import pytest
from unittest.mock import MagicMock

from services.sse_services import SSEConnection, SSEManager


# =============================================================================
# SSEConnection Tests
# =============================================================================


class TestSSEConnection:
    """Tests for SSEConnection message queue."""

    @pytest.mark.asyncio
    async def test_send_message_queues_json(self):
        conn = SSEConnection(room_id="room-1")
        result = await conn.send_message("test_event", {"key": "value"})

        assert result is True
        raw = await conn.queue.get()
        parsed = json.loads(raw)
        assert parsed["type"] == "test_event"
        assert parsed["room_id"] == "room-1"
        assert parsed["data"] == {"key": "value"}

    @pytest.mark.asyncio
    async def test_send_message_fails_when_closed(self):
        conn = SSEConnection(room_id="room-1")
        conn.close()
        result = await conn.send_message("test", {})
        assert result is False

    @pytest.mark.asyncio
    async def test_get_message_returns_queued_data(self):
        conn = SSEConnection(room_id="room-1")
        await conn.send_message("ping", {})
        msg = await conn.get_message(timeout=1.0)
        assert msg is not None
        assert json.loads(msg)["type"] == "ping"

    @pytest.mark.asyncio
    async def test_get_message_returns_heartbeat_on_timeout(self):
        conn = SSEConnection(room_id="room-1")
        msg = await conn.get_message(timeout=0.01)
        parsed = json.loads(msg)
        assert parsed["type"] == "heartbeat"
        assert parsed["room_id"] == "room-1"

    def test_close_marks_inactive(self):
        conn = SSEConnection(room_id="room-1")
        assert conn.is_active is True
        conn.close()
        assert conn.is_active is False


# =============================================================================
# SSEManager Cancellation Tests
# =============================================================================


class TestSSEManagerCancellation:
    """Tests for message cancellation lifecycle."""

    def test_cancel_then_is_cancelled(self):
        mgr = SSEManager()
        assert mgr.is_cancelled("msg-1") is False
        mgr.cancel_message("msg-1")
        assert mgr.is_cancelled("msg-1") is True

    def test_clear_cancellation(self):
        mgr = SSEManager()
        mgr.cancel_message("msg-1")
        mgr.clear_cancellation("msg-1")
        assert mgr.is_cancelled("msg-1") is False

    def test_clear_also_removes_token(self):
        mgr = SSEManager()
        token = mgr.create_token("msg-1")
        mgr.clear_cancellation("msg-1")
        assert mgr.get_token("msg-1") is None


# =============================================================================
# CancellationToken Tests
# =============================================================================


class TestCancellationToken:
    """Tests for CancellationToken creation and pre-signalling."""

    def test_create_token_returns_token(self):
        mgr = SSEManager()
        token = mgr.create_token("msg-1")
        assert token is not None
        assert token.message_id == "msg-1"
        assert mgr.get_token("msg-1") is token

    def test_create_token_pre_signals_if_already_cancelled(self):
        mgr = SSEManager()
        mgr.cancel_message("msg-1")
        token = mgr.create_token("msg-1")
        assert token.is_cancelled is True

    def test_cancel_signals_existing_token(self):
        mgr = SSEManager()
        token = mgr.create_token("msg-1")
        assert token.is_cancelled is False
        mgr.cancel_message("msg-1")
        assert token.is_cancelled is True

    def test_remove_token(self):
        mgr = SSEManager()
        mgr.create_token("msg-1")
        mgr.remove_token("msg-1")
        assert mgr.get_token("msg-1") is None


# =============================================================================
# SSEManager Connection Tests
# =============================================================================


class TestSSEManagerConnections:
    """Tests for connection management."""

    @pytest.mark.asyncio
    async def test_add_and_remove_connection(self):
        mgr = SSEManager()
        conn = await mgr.add_connection("room-1")
        assert "room-1" in mgr.room_connections
        assert conn.connection_id in mgr.room_connections["room-1"]

        await mgr.remove_connection("room-1", conn.connection_id)
        assert "room-1" not in mgr.room_connections

    @pytest.mark.asyncio
    async def test_broadcast_delivers_to_all_connections(self):
        mgr = SSEManager()
        c1 = await mgr.add_connection("room-1")
        c2 = await mgr.add_connection("room-1")

        await mgr.broadcast_to_room("room-1", "update", {"x": 1})

        for conn in [c1, c2]:
            msg = await conn.queue.get()
            parsed = json.loads(msg)
            assert parsed["type"] == "update"
            assert parsed["data"] == {"x": 1}

    @pytest.mark.asyncio
    async def test_broadcast_to_empty_room_is_noop(self):
        mgr = SSEManager()
        await mgr.broadcast_to_room("nonexistent", "event", {})
