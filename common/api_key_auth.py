"""
API Key Authentication for Discovery API

Validates X-API-Key header for external API access.
Keys are hashed with SHA-256 and validated against MongoDB.
"""

import hashlib
from typing import Any

from fastapi import HTTPException, Request, status
from loguru import logger

from common.protocols import APIKeyAuthenticator
from models.api_key import APIKey

api_key_authenticator: APIKeyAuthenticator | None = None
api_key_store: Any | None = None


class _StoreAPIKeyAuthenticator:
    def __init__(self, store: Any) -> None:
        self._store = store

    async def validate_api_key(
        self, api_key: str, *, track_usage: bool = True
    ) -> APIKey:
        key_hash = hash_api_key(api_key)
        api_key_doc = await self._store.get_api_key_by_hash(key_hash)

        if not api_key_doc:
            logger.warning("API key validation failed: key not found")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": "invalid_key",
                    "message": "Invalid API key",
                },
            )

        if not api_key_doc.is_active:
            logger.warning(
                f"API key validation failed: key {api_key_doc.key_id} is inactive"
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": "key_inactive",
                    "message": "API key is inactive",
                },
            )

        if track_usage:
            try:
                await self._store.update_api_key_usage(key_hash)
            except Exception as e:
                logger.warning(f"Failed to update API key usage: {e}")

        return api_key_doc


def bind_api_key_authenticator(authenticator: APIKeyAuthenticator) -> None:
    global api_key_authenticator, api_key_store

    api_key_authenticator = authenticator
    api_key_store = None


def bind_api_key_store(store: Any) -> None:
    global api_key_authenticator, api_key_store

    api_key_store = store
    api_key_authenticator = _StoreAPIKeyAuthenticator(store)


def _require_api_key_authenticator() -> APIKeyAuthenticator:
    if api_key_store is not None:
        return _StoreAPIKeyAuthenticator(api_key_store)
    if api_key_authenticator is None:
        raise RuntimeError("API key authenticator dependency has not been bound")
    return api_key_authenticator


def hash_api_key(api_key: str) -> str:
    """
    Hash an API key using SHA-256.
    
    Args:
        api_key: The plaintext API key
        
    Returns:
        str: The SHA-256 hash (hex digest)
    """
    return hashlib.sha256(api_key.encode()).hexdigest()


async def validate_api_key(api_key: str, *, track_usage: bool = True) -> APIKey:
    """
    Validate an API key against MongoDB.

    Args:
        api_key: The plaintext API key from the request
        track_usage: Whether to increment usage_count and update last_used_at.
            Set to False for infrastructure/daemon endpoints that should not
            inflate the user-visible usage counter.

    Returns:
        APIKey: The validated API key model

    Raises:
        HTTPException: If the key is invalid, inactive, or not found
    """
    authenticator = _require_api_key_authenticator()
    return await authenticator.validate_api_key(api_key, track_usage=track_usage)


async def get_api_key(request: Request) -> APIKey:
    """
    FastAPI dependency to extract and validate API key from request headers.

    Extracts the X-API-Key header, hashes it, and validates against MongoDB.
    Also increments the key's usage_count. Use this on user-initiated endpoints.

    For infrastructure/daemon endpoints (heartbeat, SSE events, agent sync, hub
    register) use get_api_key_no_track instead to avoid inflating the counter.

    Usage:
        @router.post("/endpoint")
        async def endpoint(api_key: APIKey = Depends(get_api_key)):
            # api_key is the validated APIKey model
            ...

    Args:
        request: The FastAPI Request object

    Returns:
        APIKey: The validated API key model

    Raises:
        HTTPException: If the key is missing, invalid, or inactive
    """
    # Extract API key from header
    api_key = request.headers.get("X-API-Key")

    if not api_key:
        logger.warning("API key validation failed: X-API-Key header missing")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "missing_key",
                "message": "X-API-Key header is required",
            },
        )

    # Validate the key and track usage
    return await validate_api_key(api_key)


async def get_api_key_no_track(request: Request) -> APIKey:
    """
    FastAPI dependency that authenticates the API key without tracking usage.

    Identical to get_api_key but skips the usage_count increment and
    last_used_at update. Use this on high-frequency infrastructure endpoints
    called by the hub daemon (heartbeat, SSE events, agent sync, hub register)
    so that background daemon traffic does not inflate the user-visible
    usage counter.

    Args:
        request: The FastAPI Request object

    Returns:
        APIKey: The validated API key model

    Raises:
        HTTPException: If the key is missing, invalid, or inactive
    """
    api_key = request.headers.get("X-API-Key")

    if not api_key:
        logger.warning("API key validation failed: X-API-Key header missing")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "missing_key",
                "message": "X-API-Key header is required",
            },
        )

    return await validate_api_key(api_key, track_usage=False)
