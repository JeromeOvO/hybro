"""
Unit tests for InMemoryCache (common/utils/in_memory_cache.py).

Tests cover:
- set/get: basic storage and retrieval
- TTL expiration
- delete: key removal
- clear: full reset
- Default value when key missing
"""

import time
import pytest
from unittest.mock import patch

from common.utils.in_memory_cache import InMemoryCache


@pytest.fixture
def cache():
    """Create a fresh InMemoryCache (reset singleton state)."""
    InMemoryCache._instance = None
    InMemoryCache._initialized = False
    c = InMemoryCache()
    yield c
    c.clear()
    InMemoryCache._instance = None
    InMemoryCache._initialized = False


# =============================================================================
# Basic CRUD Tests
# =============================================================================


class TestInMemoryCacheCRUD:
    def test_set_and_get(self, cache):
        cache.set("key1", {"data": 42})
        assert cache.get("key1") == {"data": 42}

    def test_get_returns_default_for_missing_key(self, cache):
        assert cache.get("missing") is None
        assert cache.get("missing", "fallback") == "fallback"

    def test_delete_removes_key(self, cache):
        cache.set("key1", "value")
        result = cache.delete("key1")
        assert result is True
        assert cache.get("key1") is None

    def test_delete_returns_false_for_missing_key(self, cache):
        result = cache.delete("nonexistent")
        assert result is False

    def test_clear_removes_all(self, cache):
        cache.set("a", 1)
        cache.set("b", 2)
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None


# =============================================================================
# TTL Tests
# =============================================================================


class TestInMemoryCacheTTL:
    def test_returns_value_before_expiry(self, cache):
        cache.set("key", "value", ttl=60)
        assert cache.get("key") == "value"

    def test_returns_default_after_expiry(self, cache):
        cache.set("key", "value", ttl=1)
        with patch("common.utils.in_memory_cache.time") as mock_time:
            mock_time.time.return_value = time.time() + 2
            assert cache.get("key") is None

    def test_overwrite_clears_ttl_when_none(self, cache):
        cache.set("key", "v1", ttl=1)
        cache.set("key", "v2")
        with patch("common.utils.in_memory_cache.time") as mock_time:
            mock_time.time.return_value = time.time() + 100
            assert cache.get("key") == "v2"


# =============================================================================
# Singleton Tests
# =============================================================================


class TestSingleton:
    def test_same_instance(self, cache):
        c2 = InMemoryCache()
        assert cache is c2
