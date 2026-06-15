"""Compatibility checks for the retired legacy SSE broker surface.

The broker behavior itself now lives under ``delivery/event_bus`` and
``delivery/event_publisher``. This file keeps the legacy factory/no-Redis
behavior and app-shell health aliases covered for callers that still import the
old test helpers.
"""

class MockRedisService:
    """In-memory mock used by Phase 7a golden transport tests."""

    def __init__(self):
        self._store: dict[str, str] = {}
        self._set_nx_calls: list[tuple] = []
        self._exists_calls: list[str] = []
        self._is_connected = True

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    async def set_nx(self, key: str, value: str, ex: int | None = None) -> bool:
        self._set_nx_calls.append((key, value, ex))
        if key in self._store:
            return False
        self._store[key] = value
        return True

    async def exists(self, key: str) -> bool:
        self._exists_calls.append(key)
        return key in self._store

    async def get(self, key: str) -> str | None:
        return self._store.get(key)


class TestHealthStatus:
    def test_health_status_ok_when_redis_not_expected_and_watcher_connected(self):
        from main import compute_health_status

        result = compute_health_status(
            delivery_pubsub_connected=False,
            delivery_kv_connected=False,
            legacy_redis_service_connected=False,
            relay_streams_available=False,
            redis_url="",
            change_stream_connected=True,
        )

        assert result["body"]["status"] == "ok"
        assert result["status_code"] == 200
        assert result["body"]["redis_expected"] is False
        assert result["body"]["broker_expected"] is False
        assert result["body"]["broker_connected"] is False

    def test_health_status_degraded_when_redis_expected_but_delivery_down(self):
        from main import compute_health_status

        result = compute_health_status(
            delivery_pubsub_connected=False,
            delivery_kv_connected=True,
            legacy_redis_service_connected=True,
            relay_streams_available=True,
            redis_url="redis://localhost:6379/0",
            change_stream_connected=True,
        )

        assert result["body"]["status"] == "degraded"
        assert result["status_code"] == 503
        assert result["body"]["redis_expected"] is True
        assert result["body"]["delivery_pubsub_connected"] is False
        assert result["body"]["delivery_kv_connected"] is True

    def test_health_status_ok_when_all_expected_services_connected(self):
        from main import compute_health_status

        result = compute_health_status(
            delivery_pubsub_connected=True,
            delivery_kv_connected=True,
            legacy_redis_service_connected=True,
            relay_streams_available=True,
            redis_url="redis://localhost:6379/0",
            change_stream_connected=True,
        )

        assert result["body"]["status"] == "ok"
        assert result["status_code"] == 200
        assert result["body"]["broker_connected"] is True
        assert result["body"]["redis_service_connected"] is True

    def test_health_status_degraded_when_change_stream_disconnected(self):
        from main import compute_health_status

        result = compute_health_status(
            delivery_pubsub_connected=True,
            delivery_kv_connected=True,
            legacy_redis_service_connected=True,
            relay_streams_available=True,
            redis_url="redis://localhost:6379/0",
            change_stream_connected=False,
        )

        assert result["body"]["status"] == "degraded"
        assert result["status_code"] == 503
        assert result["body"]["change_stream_connected"] is False
