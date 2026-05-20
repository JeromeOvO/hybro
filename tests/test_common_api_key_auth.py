"""
Unit tests for API Key Auth (common/api_key_auth.py).

Tests cover:
- hash_api_key: deterministic SHA-256 hashing
- validate_api_key: valid key, missing key, inactive key, usage update failure,
  track_usage=False skips increment
- get_api_key: header extraction and delegation
- get_api_key_no_track: header extraction and delegation without usage tracking
"""

import ast
import hashlib
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app_shell.api_key_auth import MongoAPIKeyAuthenticator
from common.api_key_auth import (
    bind_api_key_authenticator,
    hash_api_key,
    validate_api_key,
    get_api_key,
    get_api_key_no_track,
)


@pytest.fixture(autouse=True)
def reset_api_key_authenticator(monkeypatch):
    monkeypatch.setattr("common.api_key_auth.api_key_authenticator", None)


def test_common_auth_delegates_without_persistence_store_logic():
    tree = ast.parse(open("common/api_key_auth.py").read())
    forbidden_names = {
        "api_key_store",
        "bind_api_key_store",
        "get_api_key_by_hash",
        "update_api_key_usage",
    }
    found = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id in forbidden_names
    }
    found.update(
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in forbidden_names
    )

    assert found == set()


# =============================================================================
# hash_api_key Tests
# =============================================================================


class TestHashApiKey:
    def test_returns_sha256_hex(self):
        result = hash_api_key("test-key-123")
        expected = hashlib.sha256("test-key-123".encode()).hexdigest()
        assert result == expected
        assert len(result) == 64

    def test_different_keys_produce_different_hashes(self):
        assert hash_api_key("key-a") != hash_api_key("key-b")

    def test_deterministic(self):
        assert hash_api_key("same") == hash_api_key("same")


# =============================================================================
# validate_api_key Tests
# =============================================================================


class TestValidateApiKey:
    @pytest.mark.asyncio
    async def test_delegates_to_bound_authenticator_protocol(self):
        mock_key = MagicMock()
        authenticator = MagicMock()
        authenticator.validate_api_key = AsyncMock(return_value=mock_key)

        bind_api_key_authenticator(authenticator)

        result = await validate_api_key("raw-key", track_usage=False)

        assert result is mock_key
        authenticator.validate_api_key.assert_awaited_once_with(
            "raw-key", track_usage=False
        )

    @pytest.mark.asyncio
    async def test_mongo_authenticator_validates_and_tracks_usage(self):
        mock_key = MagicMock()
        mock_key.is_active = True
        store = MagicMock()
        store.get_api_key_by_hash = AsyncMock(return_value=mock_key)
        store.update_api_key_usage = AsyncMock()

        bind_api_key_authenticator(MongoAPIKeyAuthenticator(store))

        result = await validate_api_key("raw-key")

        assert result is mock_key
        store.update_api_key_usage.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_valid_key(self):
        mock_key = MagicMock()
        mock_key.is_active = True
        mock_key.key_id = "k-001"
        store = MagicMock()
        store.get_api_key_by_hash = AsyncMock(return_value=mock_key)
        store.update_api_key_usage = AsyncMock()

        bind_api_key_authenticator(MongoAPIKeyAuthenticator(store))
        result = await validate_api_key("raw-key")

        assert result is mock_key

    @pytest.mark.asyncio
    async def test_raises_401_for_unknown_key(self):
        store = MagicMock()
        store.get_api_key_by_hash = AsyncMock(return_value=None)
        bind_api_key_authenticator(MongoAPIKeyAuthenticator(store))

        with pytest.raises(HTTPException) as exc:
            await validate_api_key("bad-key")

        assert exc.value.status_code == 401
        assert exc.value.detail["error"] == "invalid_key"

    @pytest.mark.asyncio
    async def test_raises_401_for_inactive_key(self):
        mock_key = MagicMock()
        mock_key.is_active = False
        mock_key.key_id = "k-disabled"
        store = MagicMock()
        store.get_api_key_by_hash = AsyncMock(return_value=mock_key)
        bind_api_key_authenticator(MongoAPIKeyAuthenticator(store))

        with pytest.raises(HTTPException) as exc:
            await validate_api_key("inactive-key")

        assert exc.value.status_code == 401
        assert exc.value.detail["error"] == "key_inactive"

    @pytest.mark.asyncio
    async def test_tolerates_usage_update_failure(self):
        mock_key = MagicMock()
        mock_key.is_active = True
        store = MagicMock()
        store.get_api_key_by_hash = AsyncMock(return_value=mock_key)
        store.update_api_key_usage = AsyncMock(
            side_effect=RuntimeError("stats DB down")
        )

        bind_api_key_authenticator(MongoAPIKeyAuthenticator(store))

        result = await validate_api_key("valid-key")
        assert result is mock_key

    @pytest.mark.asyncio
    async def test_skips_usage_update_when_track_usage_false(self):
        mock_key = MagicMock()
        mock_key.is_active = True
        store = MagicMock()
        store.get_api_key_by_hash = AsyncMock(return_value=mock_key)
        store.update_api_key_usage = AsyncMock()

        bind_api_key_authenticator(MongoAPIKeyAuthenticator(store))
        result = await validate_api_key("raw-key", track_usage=False)

        assert result is mock_key
        store.update_api_key_usage.assert_not_called()


# =============================================================================
# get_api_key Tests
# =============================================================================


class TestGetApiKey:
    @pytest.mark.asyncio
    async def test_raises_401_when_header_missing(self):
        request = MagicMock()
        request.headers = {}

        with pytest.raises(HTTPException) as exc:
            await get_api_key(request)

        assert exc.value.status_code == 401
        assert exc.value.detail["error"] == "missing_key"

    @pytest.mark.asyncio
    async def test_delegates_to_validate(self):
        mock_key = MagicMock()
        mock_key.is_active = True

        request = MagicMock()
        request.headers = {"X-API-Key": "my-key"}

        with patch("common.api_key_auth.validate_api_key", new_callable=AsyncMock) as mock_validate:
            mock_validate.return_value = mock_key
            result = await get_api_key(request)

        assert result is mock_key
        mock_validate.assert_called_once_with("my-key")


# =============================================================================
# get_api_key_no_track Tests
# =============================================================================


class TestGetApiKeyNoTrack:
    @pytest.mark.asyncio
    async def test_raises_401_when_header_missing(self):
        request = MagicMock()
        request.headers = {}

        with pytest.raises(HTTPException) as exc:
            await get_api_key_no_track(request)

        assert exc.value.status_code == 401
        assert exc.value.detail["error"] == "missing_key"

    @pytest.mark.asyncio
    async def test_delegates_to_validate_with_track_usage_false(self):
        mock_key = MagicMock()
        mock_key.is_active = True

        request = MagicMock()
        request.headers = {"X-API-Key": "my-key"}

        with patch("common.api_key_auth.validate_api_key", new_callable=AsyncMock) as mock_validate:
            mock_validate.return_value = mock_key
            result = await get_api_key_no_track(request)

        assert result is mock_key
        mock_validate.assert_called_once_with("my-key", track_usage=False)

    @pytest.mark.asyncio
    async def test_does_not_call_update_api_key_usage(self):
        mock_key = MagicMock()
        mock_key.is_active = True

        request = MagicMock()
        request.headers = {"X-API-Key": "infra-key"}
        store = MagicMock()
        store.get_api_key_by_hash = AsyncMock(return_value=mock_key)
        store.update_api_key_usage = AsyncMock()

        bind_api_key_authenticator(MongoAPIKeyAuthenticator(store))

        await get_api_key_no_track(request)

        store.update_api_key_usage.assert_not_called()
