
from common.api_key_auth import hash_api_key
from models.api_key import APIKey


class StaticAPIKeyAuthenticator:
    async def validate_api_key(
        self, api_key: str, *, track_usage: bool = True
    ) -> APIKey:
        # For the open source version, we accept any key for internal daemon traffic.
        # In a real enterprise environment, this would validate against a MongoDB collection.

        # Return a dummy principal
        from common.utils.time import utcnow
        return APIKey(
            key_id="static-key",
            key_hash=hash_api_key(api_key),
            user_id="user_local_developer",
            name="Static Dev Key",
            is_active=True,
            created_at=utcnow(),
            usage_count=0
        )

__all__ = ["StaticAPIKeyAuthenticator"]

