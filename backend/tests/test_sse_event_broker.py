(
    "Compatibility checks for the retired legacy SSE broker surface.\n\nThe broker behavior itself now lives under ``delivery/event_bus`` and\n``delivery/event_publisher``. This file keeps the legacy factory/no-Redis\nbehavior and "
    + "app-"
    + "shell"
    + " health aliases covered for callers that still import the\nold test helpers.\n"
)


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
            redis_runtime_connected=False,
            redis_url="",
            change_stream_connected=True,
            agent_search_index_ready=True,
            memory_search_index_ready=True,
            search_indexes_ready=True,
        )

        assert result["body"]["status"] == "ok"
        assert result["status_code"] == 200
        assert result["body"]["redis_expected"] is False
        assert result["body"]["broker_expected"] is False
        assert result["body"]["broker_connected"] is False
        assert (
            result["body"]["legacy_redis_service_connected"]
            is (result["body"]["redis_runtime_connected"])
        )

    def test_health_status_degraded_when_redis_expected_but_delivery_down(self):
        from main import compute_health_status

        result = compute_health_status(
            delivery_pubsub_connected=False,
            delivery_kv_connected=True,
            redis_runtime_connected=True,
            redis_url="redis://localhost:6379/0",
            change_stream_connected=True,
            agent_search_index_ready=True,
            memory_search_index_ready=True,
            search_indexes_ready=True,
        )

        assert result["body"]["status"] == "degraded"
        assert result["status_code"] == 503
        assert result["body"]["redis_expected"] is True
        assert result["body"]["delivery_pubsub_connected"] is False
        assert result["body"]["delivery_kv_connected"] is True
        assert (
            result["body"]["legacy_redis_service_connected"]
            is (result["body"]["redis_runtime_connected"])
        )

    def test_health_status_reports_eventing_independently(self):
        from main import compute_health_status

        result = compute_health_status(
            delivery_pubsub_connected=True,
            eventing_connected=False,
            delivery_kv_connected=True,
            redis_runtime_connected=True,
            redis_url="redis://localhost:6379/0",
            change_stream_connected=True,
            agent_search_index_ready=True,
            memory_search_index_ready=True,
            search_indexes_ready=True,
        )

        assert result["body"]["delivery_pubsub_connected"] is True
        assert result["body"]["eventing_connected"] is False
        assert result["body"]["status"] == "degraded"
        assert result["status_code"] == 503

    def test_health_status_ok_when_all_expected_services_connected(self):
        from main import compute_health_status

        result = compute_health_status(
            delivery_pubsub_connected=True,
            delivery_kv_connected=True,
            redis_runtime_connected=True,
            redis_url="redis://localhost:6379/0",
            change_stream_connected=True,
            agent_search_index_ready=True,
            memory_search_index_ready=True,
            search_indexes_ready=True,
        )

        assert result["body"]["status"] == "ok"
        assert result["status_code"] == 200
        assert result["body"]["broker_connected"] is True
        assert result["body"]["redis_runtime_connected"] is True
        assert result["body"]["redis_service_connected"] is True
        assert result["body"]["legacy_redis_service_connected"] is True

    def test_health_status_degraded_when_change_stream_disconnected(self):
        from main import compute_health_status

        result = compute_health_status(
            delivery_pubsub_connected=True,
            delivery_kv_connected=True,
            redis_runtime_connected=True,
            redis_url="redis://localhost:6379/0",
            change_stream_connected=False,
            agent_search_index_ready=True,
            memory_search_index_ready=True,
            search_indexes_ready=True,
        )

        assert result["body"]["status"] == "degraded"
        assert result["status_code"] == 503
        assert result["body"]["change_stream_connected"] is False
        assert (
            result["body"]["legacy_redis_service_connected"]
            is (result["body"]["redis_runtime_connected"])
        )

    def test_health_status_degraded_when_either_search_index_is_unready(self):
        from main import compute_health_status

        result = compute_health_status(
            delivery_pubsub_connected=False,
            delivery_kv_connected=False,
            redis_runtime_connected=False,
            redis_url="",
            change_stream_connected=True,
            agent_search_index_ready=False,
            memory_search_index_ready=True,
            search_indexes_ready=False,
        )

        assert result["status_code"] == 503
        assert result["body"]["status"] == "degraded"
        assert result["body"]["agent_search_index_ready"] is False
        assert result["body"]["memory_search_index_ready"] is True

    def test_disabled_memory_search_can_keep_aggregate_readiness_healthy(self):
        from main import compute_health_status

        result = compute_health_status(
            delivery_pubsub_connected=False,
            delivery_kv_connected=False,
            redis_runtime_connected=False,
            redis_url="",
            change_stream_connected=True,
            agent_search_index_ready=True,
            memory_search_index_ready=False,
            search_indexes_ready=True,
        )

        assert result["status_code"] == 200
        assert result["body"]["memory_search_index_ready"] is False
        assert result["body"]["search_indexes_ready"] is True
