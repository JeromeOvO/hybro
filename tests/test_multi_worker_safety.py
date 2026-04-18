"""Tests for multi-worker gunicorn safety guard and pool wiring.

Covers:
  A. check_multi_worker_safety() pure function
  B. Pool-size settings wired into MongoDB/Redis constructors
  C. Lifespan ordering (guard fires before background services)
  D. Startup failure cleanup (does not poison SSE singleton state)
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from main import check_multi_worker_safety


# =========================================================================
# A. Guard function tests (pure, no mocking)
# =========================================================================


def test_gunicorn_no_redis_raises():
    """All Redis services down under gunicorn → RuntimeError listing 3 problems."""
    with pytest.raises(RuntimeError, match="Event broker.*RedisService.*Relay"):
        check_multi_worker_safety(
            is_gunicorn=True,
            broker_connected=False,
            redis_service_connected=False,
            relay_streams_connected=False,
        )


def test_gunicorn_partial_redis_raises():
    """Relay streams down under gunicorn → RuntimeError for that one service."""
    with pytest.raises(RuntimeError, match="Relay streams"):
        check_multi_worker_safety(
            is_gunicorn=True,
            broker_connected=True,
            redis_service_connected=True,
            relay_streams_connected=False,
        )


def test_gunicorn_all_redis_ok():
    """All Redis services up under gunicorn → no error."""
    check_multi_worker_safety(
        is_gunicorn=True,
        broker_connected=True,
        redis_service_connected=True,
        relay_streams_connected=True,
    )


def test_not_gunicorn_no_redis_ok():
    """Not running under gunicorn → guard skipped regardless of Redis state."""
    check_multi_worker_safety(
        is_gunicorn=False,
        broker_connected=False,
        redis_service_connected=False,
        relay_streams_connected=False,
    )


# =========================================================================
# B. Pool wiring tests (patch module-local settings)
# =========================================================================


def _mock_settings(**overrides):
    """Create a mock settings object with given overrides."""
    defaults = {
        "mongodb_max_pool_size": 50,
        "mongodb_min_pool_size": 10,
        "redis_max_connections": 50,
        "redis_cancel_channel": "cancel:global",
        "redis_reconnect_delay": 1.0,
        "redis_reconnect_max_delay": 30.0,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_mongodb_pool_uses_settings(monkeypatch):
    """MongoDB connect() passes pool sizes from settings to AsyncIOMotorClient."""
    monkeypatch.setattr(
        "database.mongodb.settings",
        _mock_settings(mongodb_max_pool_size=25, mongodb_min_pool_size=5),
    )

    with patch("database.mongodb.AsyncIOMotorClient") as mock_client:
        mock_instance = MagicMock()
        mock_instance.admin.command = AsyncMock()
        mock_client.return_value = mock_instance

        from database.mongodb import MongoDB
        db = MongoDB()
        await db.connect()

        _, kwargs = mock_client.call_args
        assert kwargs["maxPoolSize"] == 25
        assert kwargs["minPoolSize"] == 5


@pytest.mark.asyncio
async def test_redis_service_pool_uses_settings(monkeypatch):
    """RedisService.start() passes max_connections from settings."""
    monkeypatch.setattr(
        "infrastructure.redis_service.settings",
        _mock_settings(redis_max_connections=30),
    )

    with patch("infrastructure.redis_service.aioredis") as mock_aioredis:
        mock_client = AsyncMock()
        mock_aioredis.from_url.return_value = mock_client

        from infrastructure.redis_service import RedisService
        svc = RedisService("redis://fake")
        await svc.start()

        _, kwargs = mock_aioredis.from_url.call_args
        assert kwargs["max_connections"] == 30


@pytest.mark.asyncio
async def test_redis_broker_pool_uses_settings(monkeypatch):
    """RedisBroker.start() passes max_connections from settings."""
    monkeypatch.setattr(
        "infrastructure.brokers.redis_broker.settings",
        _mock_settings(redis_max_connections=30, redis_cancel_channel="cancel:global"),
    )

    with patch("infrastructure.brokers.redis_broker.aioredis") as mock_aioredis:
        mock_client = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_client.pubsub.return_value = mock_pubsub
        mock_aioredis.from_url.return_value = mock_client

        from infrastructure.brokers.redis_broker import RedisBroker
        broker = RedisBroker("redis://fake")
        await broker.start()

        _, kwargs = mock_aioredis.from_url.call_args
        assert kwargs["max_connections"] == 30


# =========================================================================
# C + D. Lifespan ordering + cleanup tests
# =========================================================================


def _patch_infrastructure_noop(monkeypatch):
    """Patch all infrastructure to no-op for lifespan testing."""
    # MongoDB
    monkeypatch.setattr("main.mongodb.connect", AsyncMock())
    monkeypatch.setattr("main.mongodb.create_context_memory_indexes", AsyncMock())
    monkeypatch.setattr("main.mongodb.ensure_agent_indexes", AsyncMock())
    monkeypatch.setattr("main.mongodb.create_capability_issue_indexes", AsyncMock())
    monkeypatch.setattr("main.mongodb.ensure_turn_events_indexes", AsyncMock())
    monkeypatch.setattr("main.mongodb.close_database_connection", AsyncMock())
    monkeypatch.setattr("main.pinecone_db.connect", Mock())

    # SSE manager
    monkeypatch.setattr("main.sse_manager.start_event_broker", AsyncMock())
    monkeypatch.setattr("main.sse_manager.stop_event_broker", AsyncMock())
    monkeypatch.setattr("main.sse_manager.start_redis_service", AsyncMock())
    monkeypatch.setattr("main.sse_manager.stop_redis_service", AsyncMock())
    # broker_connected is a @property derived from _broker; with create_event_broker
    # returning None and start_event_broker mocked, it will be False naturally.

    # Background services
    monkeypatch.setattr("main.agent_health_service.set_leader_election", Mock())
    monkeypatch.setattr("main.agent_health_service.start", AsyncMock())
    monkeypatch.setattr("main.agent_health_service.stop", AsyncMock())
    monkeypatch.setattr("main.stale_task_checker.set_leader_election", Mock())
    monkeypatch.setattr("main.stale_task_checker.start", AsyncMock())
    monkeypatch.setattr("main.stale_task_checker.stop", AsyncMock())
    monkeypatch.setattr("main.compaction_sweep.set_leader_election", Mock())
    monkeypatch.setattr("main.compaction_sweep.start", AsyncMock())
    monkeypatch.setattr("main.compaction_sweep.stop", AsyncMock())
    monkeypatch.setattr("main.orphaned_upload_cleaner.set_leader_election", Mock())
    monkeypatch.setattr("main.orphaned_upload_cleaner.start", AsyncMock())
    monkeypatch.setattr("main.orphaned_upload_cleaner.stop", AsyncMock())

    # Settings: disable webhook to skip stale_task_checker conditional branch
    monkeypatch.setattr("main.settings.webhook_signing_key", "")

    # Redis factory returns None (no Redis)
    monkeypatch.setattr(
        "infrastructure.redis_service.create_redis_service", lambda: None
    )
    monkeypatch.setattr(
        "infrastructure.brokers.create_event_broker", lambda: None
    )


@pytest.mark.asyncio
async def test_guard_fires_before_background_services(monkeypatch):
    """Background services must NOT start before guard passes."""
    call_order = []

    async def track_health_start():
        call_order.append("agent_health_service.start")

    async def track_stale_start():
        call_order.append("stale_task_checker.start")

    _patch_infrastructure_noop(monkeypatch)
    monkeypatch.setattr("main.agent_health_service.start", track_health_start)
    monkeypatch.setattr("main.stale_task_checker.start", track_stale_start)

    # Force guard failure: gunicorn detected, no Redis
    monkeypatch.setenv("SERVER_SOFTWARE", "gunicorn/25.3.0")

    from main import lifespan, app

    ctx = lifespan(app)
    with pytest.raises(RuntimeError, match="gunicorn requires"):
        await ctx.__aenter__()

    assert "agent_health_service.start" not in call_order
    assert "stale_task_checker.start" not in call_order


@pytest.mark.asyncio
async def test_startup_failure_cleans_up_without_poisoning_sse(monkeypatch):
    """Startup failure must clean up resources without poisoning SSE state."""
    closed = []
    draining_calls = []

    async def track_mongo_close():
        closed.append("mongodb")

    def track_draining(flag):
        draining_calls.append(flag)

    _patch_infrastructure_noop(monkeypatch)
    monkeypatch.setattr("main.mongodb.close_database_connection", track_mongo_close)
    monkeypatch.setattr("main.sse_manager.set_draining", track_draining)

    monkeypatch.setenv("SERVER_SOFTWARE", "gunicorn/25.3.0")

    from main import lifespan, app
    from services.sse_services import sse_manager

    ctx = lifespan(app)
    with pytest.raises(RuntimeError):
        await ctx.__aenter__()

    # Infrastructure was cleaned up
    assert "mongodb" in closed
    # set_draining must NOT have been called during startup failure
    assert draining_calls == []
    # _shutdown_flag must NOT have been set
    assert sse_manager._shutdown_flag is False


# =========================================================================
# E. RelayStreamService.is_connected property
# =========================================================================


def test_relay_stream_service_is_connected():
    """is_connected delegates to underlying RedisService."""
    from infrastructure.relay_streams import RelayStreamService

    mock_redis = Mock(is_connected=True)
    svc = RelayStreamService(mock_redis)
    assert svc.is_connected is True

    mock_redis.is_connected = False
    assert svc.is_connected is False
