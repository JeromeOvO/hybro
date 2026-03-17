"""
Unit tests for Discovery API Key Management endpoints.

Tests cover:
- Listing API keys (sorted, empty, db error)
- Creating API keys (success, validation, hash storage)
- Deactivating API keys (success, already inactive, not found, wrong owner, db failure)
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from api.discovery_api_keys import create_api_key, deactivate_api_key, list_api_keys
from common.auth import ClerkUser
from models.api_key import APIKey
from models.request import APIKeyCreateRequest
from models.response import (
    APIKeyCreateResponse,
    APIKeyListResponse,
    APIKeyOperationResponse,
)
from tests.conftest import FROZEN_TIME

PATCH_MONGODB = "api.discovery_api_keys.mongodb"
PATCH_GENERATE = "api.discovery_api_keys.generate_api_key"
PATCH_HASH = "api.discovery_api_keys.hash_api_key"

FAKE_PLAINTEXT_KEY = "hybro_deterministic_test_key_value1"
FAKE_HASH = "sha256_hashed_deterministic_value"


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def user() -> ClerkUser:
    return ClerkUser(
        user_id="user_test_123",
        session_id="session_test_456",
        claims={"sub": "user_test_123", "email": "test@example.com"},
    )


@pytest.fixture
def other_user() -> ClerkUser:
    return ClerkUser(
        user_id="user_other_789",
        session_id="session_other_012",
        claims={"sub": "user_other_789"},
    )


@pytest.fixture
def active_key(user) -> APIKey:
    return APIKey(
        key_id="key-001",
        key_hash="hashed_value_1",
        user_id=user.user_id,
        name="Production Key",
        created_at=FROZEN_TIME,
    )


@pytest.fixture
def inactive_key(user) -> APIKey:
    return APIKey(
        key_id="key-002",
        key_hash="hashed_value_2",
        user_id=user.user_id,
        name="Old Key",
        created_at=FROZEN_TIME - timedelta(days=30),
        is_active=False,
    )


@pytest.fixture
def mock_mongodb():
    mock = MagicMock()
    mock.get_api_keys_by_user = AsyncMock(return_value=[])
    mock.add_api_key = AsyncMock(return_value=True)
    mock.get_api_key_by_id = AsyncMock(return_value=None)
    mock.deactivate_api_key = AsyncMock(return_value=True)
    return mock


# =============================================================================
# List API Keys
# =============================================================================


class TestListApiKeys:
    """Tests for the GET /api-keys endpoint."""

    @pytest.mark.asyncio
    async def test_list_api_keys_returns_sorted_by_created_at(
        self, user, active_key, inactive_key, mock_mongodb
    ):
        older_key = APIKey(
            key_id="key-older",
            key_hash="hashed_older",
            user_id=user.user_id,
            name="Older Key",
            created_at=FROZEN_TIME - timedelta(days=7),
        )
        mock_mongodb.get_api_keys_by_user = AsyncMock(
            return_value=[older_key, active_key]
        )

        with patch(PATCH_MONGODB, mock_mongodb):
            result = await list_api_keys(current_user=user)

        assert isinstance(result, APIKeyListResponse)
        assert result.count == 2
        assert result.keys[0].key_id == active_key.key_id
        assert result.keys[1].key_id == older_key.key_id
        assert result.keys[0].created_at > result.keys[1].created_at
        mock_mongodb.get_api_keys_by_user.assert_called_once_with(user.user_id)

    @pytest.mark.asyncio
    async def test_list_api_keys_empty(self, user, mock_mongodb):
        mock_mongodb.get_api_keys_by_user = AsyncMock(return_value=[])

        with patch(PATCH_MONGODB, mock_mongodb):
            result = await list_api_keys(current_user=user)

        assert result.count == 0
        assert result.keys == []

    @pytest.mark.asyncio
    async def test_list_api_keys_db_error(self, user, mock_mongodb):
        mock_mongodb.get_api_keys_by_user = AsyncMock(
            side_effect=Exception("connection lost")
        )

        with patch(PATCH_MONGODB, mock_mongodb):
            with pytest.raises(HTTPException) as exc:
                await list_api_keys(current_user=user)

        assert exc.value.status_code == 500
        assert exc.value.detail["error"] == "internal_error"


# =============================================================================
# Create API Key
# =============================================================================


class TestCreateApiKey:
    """Tests for the POST /api-keys endpoint."""

    @pytest.mark.asyncio
    async def test_create_api_key_success(self, user, mock_mongodb):
        request_body = APIKeyCreateRequest(name="My New Key")

        with (
            patch(PATCH_MONGODB, mock_mongodb),
            patch(PATCH_GENERATE, return_value=FAKE_PLAINTEXT_KEY),
            patch(PATCH_HASH, return_value=FAKE_HASH),
        ):
            result = await create_api_key(request_body=request_body, current_user=user)

        assert isinstance(result, APIKeyCreateResponse)
        assert result.api_key == FAKE_PLAINTEXT_KEY
        assert result.api_key.startswith("hybro_")
        assert result.name == "My New Key"
        assert result.key_id is not None
        assert isinstance(result.created_at, datetime)
        mock_mongodb.add_api_key.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_api_key_empty_name_rejected(self, user, mock_mongodb):
        request_body = APIKeyCreateRequest(name="   ")

        with patch(PATCH_MONGODB, mock_mongodb):
            with pytest.raises(HTTPException) as exc:
                await create_api_key(request_body=request_body, current_user=user)

        assert exc.value.status_code == 400
        assert exc.value.detail["error"] == "invalid_name"
        mock_mongodb.add_api_key.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_api_key_stores_hash_not_plaintext(self, user, mock_mongodb):
        request_body = APIKeyCreateRequest(name="Hash Test Key")

        with (
            patch(PATCH_MONGODB, mock_mongodb),
            patch(PATCH_GENERATE, return_value=FAKE_PLAINTEXT_KEY),
            patch(PATCH_HASH, return_value=FAKE_HASH),
        ):
            await create_api_key(request_body=request_body, current_user=user)

        stored_key: APIKey = mock_mongodb.add_api_key.call_args[0][0]
        assert stored_key.key_hash == FAKE_HASH
        assert stored_key.key_hash != FAKE_PLAINTEXT_KEY


# =============================================================================
# Deactivate API Key
# =============================================================================


class TestDeactivateApiKey:
    """Tests for the DELETE /api-keys/{key_id} endpoint."""

    @pytest.mark.asyncio
    async def test_deactivate_key_success(self, user, active_key, mock_mongodb):
        mock_mongodb.get_api_key_by_id = AsyncMock(return_value=active_key)
        mock_mongodb.deactivate_api_key = AsyncMock(return_value=True)

        with patch(PATCH_MONGODB, mock_mongodb):
            result = await deactivate_api_key(
                key_id=active_key.key_id, current_user=user
            )

        assert isinstance(result, APIKeyOperationResponse)
        assert result.success is True
        assert "deactivated" in result.message.lower()
        mock_mongodb.deactivate_api_key.assert_called_once_with(active_key.key_id)

    @pytest.mark.asyncio
    async def test_deactivate_key_already_inactive(
        self, user, inactive_key, mock_mongodb
    ):
        mock_mongodb.get_api_key_by_id = AsyncMock(return_value=inactive_key)

        with patch(PATCH_MONGODB, mock_mongodb):
            result = await deactivate_api_key(
                key_id=inactive_key.key_id, current_user=user
            )

        assert result.success is True
        assert "already inactive" in result.message.lower()
        mock_mongodb.deactivate_api_key.assert_not_called()

    @pytest.mark.asyncio
    async def test_deactivate_key_not_found_returns_404(self, user, mock_mongodb):
        mock_mongodb.get_api_key_by_id = AsyncMock(return_value=None)

        with patch(PATCH_MONGODB, mock_mongodb):
            with pytest.raises(HTTPException) as exc:
                await deactivate_api_key(key_id="nonexistent-key", current_user=user)

        assert exc.value.status_code == 404
        assert exc.value.detail["error"] == "not_found"

    @pytest.mark.asyncio
    async def test_deactivate_key_wrong_owner_returns_404(
        self, other_user, active_key, mock_mongodb
    ):
        mock_mongodb.get_api_key_by_id = AsyncMock(return_value=active_key)

        with patch(PATCH_MONGODB, mock_mongodb):
            with pytest.raises(HTTPException) as exc:
                await deactivate_api_key(
                    key_id=active_key.key_id, current_user=other_user
                )

        assert exc.value.status_code == 404
        assert exc.value.detail["error"] == "not_found"

    @pytest.mark.asyncio
    async def test_deactivate_key_db_failure(self, user, active_key, mock_mongodb):
        mock_mongodb.get_api_key_by_id = AsyncMock(return_value=active_key)
        mock_mongodb.deactivate_api_key = AsyncMock(return_value=False)

        with patch(PATCH_MONGODB, mock_mongodb):
            with pytest.raises(HTTPException) as exc:
                await deactivate_api_key(
                    key_id=active_key.key_id, current_user=user
                )

        assert exc.value.status_code == 500
        assert exc.value.detail["error"] == "deactivation_failed"
