"""Tests for cross-instance SSE event broker integration.

Covers: broker factory, SSEManager broker integration, self-dedup,
cross-instance delivery, cancellation broadcast, dynamic subscribe/unsubscribe,
graceful publish failure, and degraded state observability.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from infrastructure.brokers import create_event_broker
from infrastructure.event_broker import MessageHandler


# ---------------------------------------------------------------------------
# MockBroker — in-memory EventBroker for unit tests
# ---------------------------------------------------------------------------

class MockBroker:
    """In-memory EventBroker for testing."""

    def __init__(self):
        self._connected = True
        self._handlers: dict[str, MessageHandler] = {}
        self._subscribed: set[str] = set()
        self.published: list[tuple[str, dict]] = []  # (channel, payload) log

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        self._connected = False

    async def publish(self, channel: str, payload: dict) -> None:
        self.published.append((channel, payload))

    async def subscribe(self, channel: str) -> None:
        self._subscribed.add(channel)

    async def unsubscribe(self, channel: str) -> None:
        self._subscribed.discard(channel)

    def set_handler(self, kind: str, handler: MessageHandler) -> None:
        self._handlers[kind] = handler

    async def simulate_incoming(self, payload: dict) -> None:
        """Simulate receiving a message from another instance."""
        kind = payload.get("kind")
        handler = self._handlers.get(kind)
        if handler:
            await handler(payload)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sse_manager():
    """Fresh SSEManager instance (not the global singleton)."""
    from services.sse_services import SSEManager
    return SSEManager()


@pytest.fixture
def broker():
    return MockBroker()


@pytest.fixture
async def sse_with_broker(sse_manager, broker):
    """SSEManager with a MockBroker attached."""
    await sse_manager.start_event_broker(broker)
    yield sse_manager, broker
    await sse_manager.stop_event_broker()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBrokerFactory:
    """Test create_event_broker() factory."""

    def test_broker_disabled_when_no_redis_url(self):
        """create_event_broker() returns None when redis_url is empty."""
        with patch("config.settings.settings") as mock_settings:
            mock_settings.redis_url = ""
            result = create_event_broker()
            assert result is None


class TestBroadcastPublishesToBroker:
    """Test that broadcast_to_room publishes to the broker."""

    async def test_broadcast_publishes_to_broker(self, sse_with_broker):
        """With broker attached, broadcast_to_room publishes an sse_event envelope."""
        mgr, broker = sse_with_broker
        await mgr.broadcast_to_room("room1", "agent_response", {"content": "hello"})

        assert len(broker.published) == 1
        channel, payload = broker.published[0]
        assert channel == "sse:room:room1"
        assert payload["kind"] == "sse_event"
        assert payload["origin"] == mgr._instance_id
        assert payload["room_id"] == "room1"
        assert payload["type"] == "agent_response"
        assert payload["data"] == {"content": "hello"}


class TestBroadcastDeliversLocally:
    """Test that broadcast_to_room still delivers to local connections."""

    async def test_broadcast_delivers_locally(self, sse_with_broker):
        """Local SSE connections receive the event even with broker attached."""
        mgr, broker = sse_with_broker
        conn = await mgr.add_connection("room1")

        await mgr.broadcast_to_room("room1", "agent_response", {"content": "hello"})

        # Connection queue should have the message
        msg = await asyncio.wait_for(conn.queue.get(), timeout=1.0)
        assert "agent_response" in msg
        assert "hello" in msg


class TestSelfDedup:
    """Test that messages from self are not re-delivered."""

    async def test_self_dedup(self, sse_with_broker):
        """simulate_incoming with own origin does NOT deliver to local connections."""
        mgr, broker = sse_with_broker
        conn = await mgr.add_connection("room1")

        # Simulate a message from THIS instance arriving via broker
        await broker.simulate_incoming({
            "kind": "sse_event",
            "origin": mgr._instance_id,  # same instance
            "room_id": "room1",
            "type": "agent_response",
            "data": {"content": "self-echo"},
        })

        # Queue should be empty — self-dedup should have skipped it
        assert conn.queue.empty()


class TestCrossInstanceDelivery:
    """Test that messages from other instances are delivered locally."""

    async def test_cross_instance_delivery(self, sse_with_broker):
        """simulate_incoming with different origin delivers to local connections."""
        mgr, broker = sse_with_broker
        conn = await mgr.add_connection("room1")

        await broker.simulate_incoming({
            "kind": "sse_event",
            "origin": "other-instance-id",
            "room_id": "room1",
            "type": "agent_response",
            "data": {"content": "from-other"},
        })

        msg = await asyncio.wait_for(conn.queue.get(), timeout=1.0)
        assert "from-other" in msg


class TestCancellationBroadcast:
    """Test that cancel_message_and_broadcast publishes to broker."""

    async def test_cancellation_broadcast(self, sse_with_broker):
        """cancel_message_and_broadcast publishes to cancel:global channel."""
        mgr, broker = sse_with_broker
        await mgr.cancel_message_and_broadcast("msg1")

        assert len(broker.published) == 1
        channel, payload = broker.published[0]
        assert channel == "cancel:global"
        assert payload["kind"] == "cancellation"
        assert payload["message_id"] == "msg1"
        assert payload["origin"] == mgr._instance_id

        # Also verify local cancellation was applied
        assert mgr.is_cancelled("msg1")


class TestCancellationFromBroker:
    """Test that cancellation from broker marks message as cancelled."""

    async def test_cancellation_from_broker(self, sse_with_broker):
        """Incoming cancellation from another instance marks message as cancelled."""
        mgr, broker = sse_with_broker

        # Create a cancellation token to verify it gets signalled
        token = mgr.create_token("msg1")
        assert not token.is_cancelled

        await broker.simulate_incoming({
            "kind": "cancellation",
            "origin": "other-instance-id",
            "message_id": "msg1",
        })

        assert mgr.is_cancelled("msg1")
        assert token.is_cancelled


class TestDynamicSubscribeOnFirstConnection:
    """Test broker subscription when first connection joins a room."""

    async def test_dynamic_subscribe_on_first_connection(self, sse_with_broker):
        """add_connection subscribes to the room's broker channel on first connection."""
        mgr, broker = sse_with_broker

        conn1 = await mgr.add_connection("room1")
        assert "sse:room:room1" in broker._subscribed

        # Second connection should NOT trigger another subscribe (idempotent)
        initial_subscribed = broker._subscribed.copy()
        conn2 = await mgr.add_connection("room1")
        assert broker._subscribed == initial_subscribed


class TestDynamicUnsubscribeOnLastDisconnect:
    """Test broker unsubscription when last connection leaves a room."""

    async def test_dynamic_unsubscribe_on_last_disconnect(self, sse_with_broker):
        """Removing all connections unsubscribes from the room's broker channel."""
        mgr, broker = sse_with_broker

        conn1 = await mgr.add_connection("room1")
        conn2 = await mgr.add_connection("room1")
        assert "sse:room:room1" in broker._subscribed

        # Remove first connection — channel should still be subscribed
        await mgr.remove_connection("room1", conn1.connection_id)
        assert "sse:room:room1" in broker._subscribed

        # Remove last connection — channel should be unsubscribed
        await mgr.remove_connection("room1", conn2.connection_id)
        assert "sse:room:room1" not in broker._subscribed


class TestPublishFailureGraceful:
    """Test that publish failures don't break local delivery."""

    async def test_publish_failure_graceful(self, sse_manager):
        """If broker.publish raises, broadcast_to_room still delivers locally."""
        broker = MockBroker()

        async def failing_publish(channel, payload):
            raise ConnectionError("Redis down")

        broker.publish = failing_publish
        await sse_manager.start_event_broker(broker)

        conn = await sse_manager.add_connection("room1")
        # This should NOT raise despite broker.publish failing
        await sse_manager.broadcast_to_room("room1", "agent_response", {"content": "ok"})

        msg = await asyncio.wait_for(conn.queue.get(), timeout=1.0)
        assert "ok" in msg

        await sse_manager.stop_event_broker()


class TestNoDuplicateDbWrites:
    """Test that _on_sse_event does NOT trigger DB writes."""

    async def test_no_duplicate_db_writes(self, sse_with_broker):
        """_on_sse_event (subscriber path) only calls _deliver_to_local_connections, no DB writes."""
        mgr, broker = sse_with_broker
        conn = await mgr.add_connection("room1")

        with patch.object(mgr, "_deliver_to_local_connections", new_callable=AsyncMock) as mock_deliver:
            await broker.simulate_incoming({
                "kind": "sse_event",
                "origin": "other-instance-id",
                "room_id": "room1",
                "type": "processing_status",
                "data": {"status": "processing", "message_id": "m1"},
            })

            # _deliver_to_local_connections should be called
            mock_deliver.assert_called_once_with(
                "room1",
                "processing_status",
                {"status": "processing", "message_id": "m1"},
            )


class TestBrokerDegradedState:
    """Test that broker disconnection is observable."""

    async def test_broker_connected_false_when_disconnected(self, sse_manager):
        """broker_connected should be False when broker is attached but disconnected."""
        broker = MockBroker()
        broker._connected = False
        await sse_manager.start_event_broker(broker)

        assert sse_manager.broker_connected is False

        await sse_manager.stop_event_broker()

    def test_health_status_ok_when_broker_not_expected(self):
        """Pure function: health returns 'ok'/200 when REDIS_URL is empty."""
        from main import compute_health_status
        result = compute_health_status(
            broker_connected=False, redis_url="", change_stream_connected=True,
        )
        assert result["body"]["status"] == "ok"
        assert result["status_code"] == 200
        assert result["body"]["broker_expected"] is False

    def test_health_status_degraded_when_broker_expected_but_down(self):
        """Pure function: health returns 'degraded'/503 when REDIS_URL set but broker down."""
        from main import compute_health_status
        result = compute_health_status(
            broker_connected=False, redis_url="redis://localhost:6379/0",
            change_stream_connected=True,
        )
        assert result["body"]["status"] == "degraded"
        assert result["status_code"] == 503
        assert result["body"]["broker_expected"] is True

    def test_health_status_ok_when_broker_connected(self):
        """Pure function: health returns 'ok'/200 when broker is up and running."""
        from main import compute_health_status
        result = compute_health_status(
            broker_connected=True, redis_url="redis://localhost:6379/0",
            change_stream_connected=True,
        )
        assert result["body"]["status"] == "ok"
        assert result["status_code"] == 200
        assert result["body"]["broker_expected"] is True
        assert result["body"]["broker_connected"] is True
