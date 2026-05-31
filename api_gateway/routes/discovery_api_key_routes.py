"""
API Key Management Endpoints

Provides authenticated API key management for developer portal users.
"""

import secrets
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.params import Depends as DependsParam
from loguru import logger

from api_gateway.registry import mark_declared_owner as _mark_declared_owner
from common.api_key_auth import hash_api_key
from common.auth import ClerkUser, get_current_user
from common.protocols import APIKeyStore
from models.api_key import APIKey
from models.request import APIKeyCreateRequest
from models.response import (
    APIKeyCreateResponse,
    APIKeyErrorResponse,
    APIKeyItemResponse,
    APIKeyListResponse,
    APIKeyOperationResponse,
)

router = APIRouter()
api_key_store: APIKeyStore | None = None


def bind_api_key_store(store: APIKeyStore) -> None:
    global api_key_store

    api_key_store = store


def get_api_key_store() -> APIKeyStore:
    if api_key_store is None:
        raise RuntimeError("API key store dependency has not been bound")
    return api_key_store


def _resolve_dependency(value: Any, provider) -> Any:
    if isinstance(value, DependsParam):
        return provider()
    return value


def generate_api_key() -> str:
    """Generate a secure API key with hybro_ prefix."""
    random_part = secrets.token_urlsafe(24)[:32]
    return f"hybro_{random_part}"


@router.get(
    "/api-keys",
    response_model=APIKeyListResponse,
    responses={
        401: {"model": APIKeyErrorResponse, "description": "Authentication required"},
        500: {"model": APIKeyErrorResponse, "description": "Internal server error"},
    },
    summary="List API Keys",
)
async def list_api_keys(
    current_user: ClerkUser = Depends(get_current_user),
    store: APIKeyStore = Depends(get_api_key_store),
) -> APIKeyListResponse:
    """List all API keys belonging to the authenticated user."""
    store = _resolve_dependency(store, get_api_key_store)
    try:
        keys = await store.get_api_keys_by_user(current_user.user_id)
        sorted_keys = sorted(keys, key=lambda key: key.created_at, reverse=True)
        return APIKeyListResponse(
            keys=[
                APIKeyItemResponse(
                    key_id=key.key_id,
                    name=key.name,
                    created_at=key.created_at,
                    last_used_at=key.last_used_at,
                    is_active=key.is_active,
                    usage_count=key.usage_count,
                )
                for key in sorted_keys
            ],
            count=len(sorted_keys),
        )
    except Exception as e:
        logger.error(f"Failed to list API keys for user {current_user.user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "internal_error", "message": "Failed to list API keys"},
        ) from e


@router.post(
    "/api-keys",
    response_model=APIKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": APIKeyErrorResponse, "description": "Invalid request"},
        401: {"model": APIKeyErrorResponse, "description": "Authentication required"},
        500: {"model": APIKeyErrorResponse, "description": "Internal server error"},
    },
    summary="Create API Key",
)
async def create_api_key(
    request_body: APIKeyCreateRequest,
    current_user: ClerkUser = Depends(get_current_user),
    store: APIKeyStore = Depends(get_api_key_store),
) -> APIKeyCreateResponse:
    """Create a new API key and return its plaintext value once."""
    key_name = request_body.name.strip()
    if not key_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_name", "message": "API key name cannot be empty"},
        )

    plaintext_key = generate_api_key()
    api_key = APIKey(
        key_id=str(uuid4()),
        key_hash=hash_api_key(plaintext_key),
        user_id=current_user.user_id,
        name=key_name,
    )
    store = _resolve_dependency(store, get_api_key_store)

    try:
        await store.add_api_key(api_key)
        return APIKeyCreateResponse(
            key_id=api_key.key_id,
            name=api_key.name,
            created_at=api_key.created_at,
            api_key=plaintext_key,
        )
    except Exception as e:
        logger.error(f"Failed to create API key for user {current_user.user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "internal_error", "message": "Failed to create API key"},
        ) from e


@router.delete(
    "/api-keys/{key_id}",
    response_model=APIKeyOperationResponse,
    responses={
        401: {"model": APIKeyErrorResponse, "description": "Authentication required"},
        404: {"model": APIKeyErrorResponse, "description": "API key not found"},
        500: {"model": APIKeyErrorResponse, "description": "Internal server error"},
    },
    summary="Deactivate API Key",
)
async def deactivate_api_key(
    key_id: str,
    current_user: ClerkUser = Depends(get_current_user),
    store: APIKeyStore = Depends(get_api_key_store),
) -> APIKeyOperationResponse:
    """Soft-delete an API key owned by the authenticated user."""
    store = _resolve_dependency(store, get_api_key_store)
    api_key = await store.get_api_key_by_id(key_id)
    if not api_key or api_key.user_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": "API key not found"},
        )

    if not api_key.is_active:
        return APIKeyOperationResponse(
            success=True,
            message="API key is already inactive",
        )

    try:
        deactivated = await store.deactivate_api_key(key_id)
        if not deactivated:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "error": "deactivation_failed",
                    "message": "Failed to deactivate API key",
                },
            )
        return APIKeyOperationResponse(
            success=True,
            message="API key deactivated successfully",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to deactivate API key {key_id} for user {current_user.user_id}: {e}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "internal_error", "message": "Failed to deactivate API key"},
        ) from e


_mark_declared_owner(router, __name__)
