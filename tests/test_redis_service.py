"""Unit tests for RedisService.

Tests the shared Redis client for key-value and stream operations.
Uses mocks to avoid requiring a real Redis instance.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from infrastructure.redis_service import RedisService, create_redis_service


# =============================================================================
# Test Helpers
# =============================================================================


def _make_service() -> RedisService:
    """Create RedisService with mocked client for testing."""
    svc = RedisService(url="redis://localhost:6379/0")
    svc._client = MagicMock()
    return svc


# =============================================================================
# Factory Tests
# =============================================================================


class TestRedisServiceFactory:
    """Tests for the create_redis_service factory function."""

    def test_returns_none_when_redis_url_empty(self):
        """Factory returns None when redis_url is empty."""
        with patch("infrastructure.redis_service.settings") as mock_settings:
            mock_settings.redis_url = ""
            result = create_redis_service()
            assert result is None

    def test_returns_service_when_redis_url_set(self):
        """Factory returns RedisService instance when redis_url is configured."""
        with patch("infrastructure.redis_service.settings") as mock_settings:
            mock_settings.redis_url = "redis://localhost:6379/0"
            result = create_redis_service()
            assert result is not None
            assert isinstance(result, RedisService)


# =============================================================================
# Set NX Tests
# =============================================================================


class TestRedisServiceSetNx:
    """Tests for set_nx (SET if not exists) operation."""

    @pytest.mark.asyncio
    async def test_set_nx_returns_true_on_first_call(self):
        """set_nx returns True when key is successfully set."""
        svc = _make_service()
        svc._client.set = AsyncMock(return_value=True)

        result = await svc.set_nx("test_key", "test_value", ex=60)

        assert result is True
        svc._client.set.assert_called_once_with(
            "test_key", "test_value", nx=True, ex=60
        )

    @pytest.mark.asyncio
    async def test_set_nx_returns_false_on_duplicate(self):
        """set_nx returns False when key already exists."""
        svc = _make_service()
        svc._client.set = AsyncMock(return_value=None)

        result = await svc.set_nx("existing_key", "value")

        assert result is False

    @pytest.mark.asyncio
    async def test_set_nx_returns_false_when_not_connected(self):
        """set_nx returns False when client is not connected."""
        svc = RedisService(url="redis://localhost:6379/0")
        # _client is None (not connected)

        result = await svc.set_nx("test_key", "test_value")

        assert result is False

    @pytest.mark.asyncio
    async def test_set_nx_returns_false_on_error(self):
        """set_nx returns False when Redis operation fails."""
        svc = _make_service()
        svc._client.set = AsyncMock(side_effect=Exception("Connection error"))

        result = await svc.set_nx("test_key", "test_value")

        assert result is False


# =============================================================================
# Exists Tests
# =============================================================================


class TestRedisServiceExists:
    """Tests for exists operation."""

    @pytest.mark.asyncio
    async def test_exists_returns_true_when_key_set(self):
        """exists returns True when key exists in Redis."""
        svc = _make_service()
        svc._client.exists = AsyncMock(return_value=1)

        result = await svc.exists("test_key")

        assert result is True
        svc._client.exists.assert_called_once_with("test_key")

    @pytest.mark.asyncio
    async def test_exists_returns_false_when_missing(self):
        """exists returns False when key doesn't exist."""
        svc = _make_service()
        svc._client.exists = AsyncMock(return_value=0)

        result = await svc.exists("missing_key")

        assert result is False

    @pytest.mark.asyncio
    async def test_exists_returns_false_when_not_connected(self):
        """exists returns False when client is not connected."""
        svc = RedisService(url="redis://localhost:6379/0")
        # _client is None (not connected)

        result = await svc.exists("test_key")

        assert result is False

    @pytest.mark.asyncio
    async def test_exists_returns_false_on_error(self):
        """exists returns False when Redis operation fails."""
        svc = _make_service()
        svc._client.exists = AsyncMock(side_effect=Exception("Connection error"))

        result = await svc.exists("test_key")

        assert result is False


# =============================================================================
# Get Tests
# =============================================================================


class TestRedisServiceGet:
    """Tests for get operation."""

    @pytest.mark.asyncio
    async def test_get_returns_value(self):
        """get returns value when key exists."""
        svc = _make_service()
        svc._client.get = AsyncMock(return_value="test_value")

        result = await svc.get("test_key")

        assert result == "test_value"
        svc._client.get.assert_called_once_with("test_key")

    @pytest.mark.asyncio
    async def test_get_returns_none_when_missing(self):
        """get returns None when key doesn't exist."""
        svc = _make_service()
        svc._client.get = AsyncMock(return_value=None)

        result = await svc.get("missing_key")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_returns_none_when_not_connected(self):
        """get returns None when client is not connected."""
        svc = RedisService(url="redis://localhost:6379/0")
        # _client is None (not connected)

        result = await svc.get("test_key")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_returns_none_on_error(self):
        """get returns None when Redis operation fails."""
        svc = _make_service()
        svc._client.get = AsyncMock(side_effect=Exception("Connection error"))

        result = await svc.get("test_key")

        assert result is None


# =============================================================================
# Delete Tests
# =============================================================================


class TestRedisServiceDelete:
    """Tests for delete operation."""

    @pytest.mark.asyncio
    async def test_delete_returns_true_when_key_deleted(self):
        """delete returns True when key is successfully deleted."""
        svc = _make_service()
        svc._client.delete = AsyncMock(return_value=1)

        result = await svc.delete("test_key")

        assert result is True
        svc._client.delete.assert_called_once_with("test_key")

    @pytest.mark.asyncio
    async def test_delete_returns_false_when_key_missing(self):
        """delete returns False when key doesn't exist."""
        svc = _make_service()
        svc._client.delete = AsyncMock(return_value=0)

        result = await svc.delete("missing_key")

        assert result is False

    @pytest.mark.asyncio
    async def test_delete_returns_false_when_not_connected(self):
        """delete returns False when client is not connected."""
        svc = RedisService(url="redis://localhost:6379/0")
        # _client is None (not connected)

        result = await svc.delete("test_key")

        assert result is False

    @pytest.mark.asyncio
    async def test_delete_returns_false_on_error(self):
        """delete returns False when Redis operation fails."""
        svc = _make_service()
        svc._client.delete = AsyncMock(side_effect=Exception("Connection error"))

        result = await svc.delete("test_key")

        assert result is False


# =============================================================================
# Set With TTL Tests
# =============================================================================


class TestRedisServiceSetWithTtl:
    """Tests for set_with_ttl operation."""

    @pytest.mark.asyncio
    async def test_set_with_ttl_success(self):
        """set_with_ttl returns True on successful set."""
        svc = _make_service()
        svc._client.set = AsyncMock()

        result = await svc.set_with_ttl("test_key", "test_value", ex=120)

        assert result is True
        svc._client.set.assert_called_once_with("test_key", "test_value", ex=120)

    @pytest.mark.asyncio
    async def test_set_with_ttl_without_expiry(self):
        """set_with_ttl works without expiry time."""
        svc = _make_service()
        svc._client.set = AsyncMock()

        result = await svc.set_with_ttl("test_key", "test_value")

        assert result is True
        svc._client.set.assert_called_once_with("test_key", "test_value", ex=None)

    @pytest.mark.asyncio
    async def test_set_with_ttl_returns_false_when_not_connected(self):
        """set_with_ttl returns False when client is not connected."""
        svc = RedisService(url="redis://localhost:6379/0")
        # _client is None (not connected)

        result = await svc.set_with_ttl("test_key", "test_value")

        assert result is False

    @pytest.mark.asyncio
    async def test_set_with_ttl_returns_false_on_error(self):
        """set_with_ttl returns False when Redis operation fails."""
        svc = _make_service()
        svc._client.set = AsyncMock(side_effect=Exception("Connection error"))

        result = await svc.set_with_ttl("test_key", "test_value")

        assert result is False


# =============================================================================
# Lua Script Tests
# =============================================================================


class TestRedisServiceEvalScript:
    """Tests for eval_script (Lua script execution)."""

    @pytest.mark.asyncio
    async def test_eval_script_success(self):
        """eval_script executes Lua script and returns result."""
        svc = _make_service()
        script = "return redis.call('GET', KEYS[1])"
        svc._client.eval = AsyncMock(return_value="result_value")

        result = await svc.eval_script(script, 1, "test_key")

        assert result == "result_value"
        svc._client.eval.assert_called_once_with(script, 1, "test_key")

    @pytest.mark.asyncio
    async def test_eval_script_returns_none_when_not_connected(self):
        """eval_script returns None when client is not connected."""
        svc = RedisService(url="redis://localhost:6379/0")
        # _client is None (not connected)

        result = await svc.eval_script("return 1", 0)

        assert result is None

    @pytest.mark.asyncio
    async def test_eval_script_returns_none_on_error(self):
        """eval_script returns None when script execution fails."""
        svc = _make_service()
        svc._client.eval = AsyncMock(side_effect=Exception("Script error"))

        result = await svc.eval_script("return 1", 0)

        assert result is None


# =============================================================================
# Streams Tests
# =============================================================================


class TestRedisServiceStreams:
    """Tests for Redis Streams operations."""

    @pytest.mark.asyncio
    async def test_xadd_calls_client(self):
        """xadd adds entry to stream with correct parameters."""
        svc = _make_service()
        svc._client.xadd = AsyncMock(return_value="1234567890-0")

        fields = {"field1": "value1", "field2": "value2"}
        result = await svc.xadd("test_stream", fields, maxlen=1000)

        assert result == "1234567890-0"
        svc._client.xadd.assert_called_once_with(
            "test_stream", fields, maxlen=1000, approximate=True
        )

    @pytest.mark.asyncio
    async def test_xadd_without_maxlen(self):
        """xadd works without maxlen parameter."""
        svc = _make_service()
        svc._client.xadd = AsyncMock(return_value="1234567890-0")

        fields = {"field": "value"}
        result = await svc.xadd("test_stream", fields)

        assert result == "1234567890-0"
        svc._client.xadd.assert_called_once_with("test_stream", fields)

    @pytest.mark.asyncio
    async def test_xadd_returns_none_when_not_connected(self):
        """xadd returns None when client is not connected."""
        svc = RedisService(url="redis://localhost:6379/0")
        # _client is None (not connected)

        result = await svc.xadd("test_stream", {"field": "value"})

        assert result is None

    @pytest.mark.asyncio
    async def test_xadd_returns_none_on_error(self):
        """xadd returns None when operation fails."""
        svc = _make_service()
        svc._client.xadd = AsyncMock(side_effect=Exception("Stream error"))

        result = await svc.xadd("test_stream", {"field": "value"})

        assert result is None

    @pytest.mark.asyncio
    async def test_xread_returns_data(self):
        """xread reads entries from streams and returns parsed data."""
        svc = _make_service()
        mock_data = [
            (
                "stream1",
                [
                    ("1234567890-0", {"field1": "value1"}),
                    ("1234567890-1", {"field2": "value2"}),
                ],
            )
        ]
        svc._client.xread = AsyncMock(return_value=mock_data)

        result = await svc.xread({"stream1": "0"}, count=10, block=5000)

        assert result == mock_data
        svc._client.xread.assert_called_once_with(
            {"stream1": "0"}, count=10, block=5000
        )

    @pytest.mark.asyncio
    async def test_xread_returns_empty_list_when_no_data(self):
        """xread returns empty list when no entries available."""
        svc = _make_service()
        svc._client.xread = AsyncMock(return_value=None)

        result = await svc.xread({"stream1": "$"})

        assert result == []

    @pytest.mark.asyncio
    async def test_xread_returns_none_when_not_connected(self):
        """xread returns None when client is not connected."""
        svc = RedisService(url="redis://localhost:6379/0")
        # _client is None (not connected)

        result = await svc.xread({"stream1": "0"})

        assert result is None

    @pytest.mark.asyncio
    async def test_xread_returns_none_on_error(self):
        """xread returns None when operation fails."""
        svc = _make_service()
        svc._client.xread = AsyncMock(side_effect=Exception("Stream error"))

        result = await svc.xread({"stream1": "0"})

        assert result is None

    @pytest.mark.asyncio
    async def test_xlen_returns_count(self):
        """xlen returns number of entries in stream."""
        svc = _make_service()
        svc._client.xlen = AsyncMock(return_value=42)

        result = await svc.xlen("test_stream")

        assert result == 42
        svc._client.xlen.assert_called_once_with("test_stream")

    @pytest.mark.asyncio
    async def test_xlen_returns_zero_when_not_connected(self):
        """xlen returns 0 when client is not connected."""
        svc = RedisService(url="redis://localhost:6379/0")
        # _client is None (not connected)

        result = await svc.xlen("test_stream")

        assert result == 0

    @pytest.mark.asyncio
    async def test_xlen_returns_zero_on_error(self):
        """xlen returns 0 when operation fails."""
        svc = _make_service()
        svc._client.xlen = AsyncMock(side_effect=Exception("Stream error"))

        result = await svc.xlen("test_stream")

        assert result == 0


# =============================================================================
# Lifecycle Tests
# =============================================================================


class TestRedisServiceLifecycle:
    """Tests for start/stop lifecycle methods."""

    @pytest.mark.asyncio
    async def test_start_connects_and_pings(self):
        """start creates client and pings Redis."""
        svc = RedisService(url="redis://localhost:6379/0")

        with patch("infrastructure.redis_service.aioredis") as mock_redis:
            mock_client = MagicMock()
            mock_client.ping = AsyncMock()
            mock_redis.from_url.return_value = mock_client

            await svc.start()

            mock_redis.from_url.assert_called_once_with(
                "redis://localhost:6379/0",
                decode_responses=True,
                socket_connect_timeout=5,
                max_connections=50,
            )
            mock_client.ping.assert_called_once()
            assert svc.is_connected is True

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self):
        """start can be called multiple times without effect."""
        svc = RedisService(url="redis://localhost:6379/0")

        with patch("infrastructure.redis_service.aioredis") as mock_redis:
            mock_client = MagicMock()
            mock_client.ping = AsyncMock()
            mock_redis.from_url.return_value = mock_client

            await svc.start()
            await svc.start()  # second call should be no-op

            # from_url should only be called once
            assert mock_redis.from_url.call_count == 1

    @pytest.mark.asyncio
    async def test_start_sets_client_to_none_on_error(self):
        """start sets _client to None when connection fails."""
        svc = RedisService(url="redis://localhost:6379/0")

        with patch("infrastructure.redis_service.aioredis") as mock_redis:
            mock_redis.from_url.side_effect = Exception("Connection failed")

            await svc.start()

            assert svc._client is None
            assert svc.is_connected is False

    @pytest.mark.asyncio
    async def test_stop_closes_client(self):
        """stop closes the Redis client."""
        svc = _make_service()
        mock_aclose = AsyncMock()
        svc._client.aclose = mock_aclose

        await svc.stop()

        mock_aclose.assert_called_once()
        assert svc._client is None

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self):
        """stop can be called multiple times safely."""
        svc = RedisService(url="redis://localhost:6379/0")

        await svc.stop()  # first call when _client is None
        await svc.stop()  # second call should be safe

        # Should not raise any errors
        assert svc._client is None

    @pytest.mark.asyncio
    async def test_stop_handles_close_errors(self):
        """stop handles errors during client close gracefully."""
        svc = _make_service()
        svc._client.aclose = AsyncMock(side_effect=Exception("Close error"))

        await svc.stop()  # should not raise

        assert svc._client is None

    def test_is_connected_returns_false_when_client_none(self):
        """is_connected returns False when _client is None."""
        svc = RedisService(url="redis://localhost:6379/0")
        assert svc.is_connected is False

    def test_is_connected_returns_true_when_client_set(self):
        """is_connected returns True when _client is set."""
        svc = _make_service()
        assert svc.is_connected is True
