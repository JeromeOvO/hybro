from typing import Any

from fastapi import HTTPException, status
from loguru import logger

from common.api_key_auth import hash_api_key
from models.api_key import APIKey


class MongoAPIKeyAuthenticator:
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
            except Exception as exc:
                logger.warning(f"Failed to update API key usage: {exc}")

        return api_key_doc


__all__ = ["MongoAPIKeyAuthenticator"]
