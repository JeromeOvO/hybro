"""
Clerk Authentication for FastAPI
Validates JWT tokens from Clerk and provides user authentication.
"""

import json
from functools import lru_cache

import httpx
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger

from config.settings import settings


security = HTTPBearer()
optional_security = HTTPBearer(auto_error=False)


class ClerkUser:
    """Represents an authenticated Clerk user"""

    def __init__(self, user_id: str, session_id: str, claims: dict):
        self.user_id = user_id
        self.session_id = session_id
        self.claims = claims
        self.email = claims.get("email")
        self.username = claims.get("username")


async def fetch_jwks() -> dict:
    """Fetch JWKS (JSON Web Key Set) from Clerk"""
    jwks_url = settings.clerk_jwks_url

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(jwks_url, timeout=10.0)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Failed to fetch JWKS from Clerk: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to verify authentication",
        ) from e


@lru_cache(maxsize=10)
def get_signing_key(jwks_data: str, token_kid: str):
    """
    Get the signing key from JWKS data for a specific key ID.
    Cached to avoid repeated parsing.
    """
    import json

    jwks = json.loads(jwks_data)
    for key in jwks.get("keys", []):
        if key.get("kid") == token_kid:
            return jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key))

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unable to find appropriate key",
    )


async def verify_clerk_token(token: str) -> ClerkUser:
    """
    Verify a Clerk JWT token and return user information.

    Args:
        token: The JWT token from the Authorization header

    Returns:
        ClerkUser object with user information

    Raises:
        HTTPException: If token is invalid or verification fails
    """
    try:
        # Get the token header to find the key ID
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")

        if not kid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing key ID",
            )

        # Fetch JWKS
        jwks_data = await fetch_jwks()

        # Get the signing key
        signing_key = get_signing_key(
            json.dumps(jwks_data), kid
        )  # Convert to string for caching

        # Verify and decode the token
        payload = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            options={"verify_exp": True, "verify_aud": False},  # Clerk doesn't use aud
        )

        # Extract user information
        user_id = payload.get("sub")
        session_id = payload.get("sid")

        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing user ID",
            )

        return ClerkUser(user_id=user_id, session_id=session_id, claims=payload)

    except jwt.ExpiredSignatureError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        ) from e
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid token: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        ) from e
    except Exception as e:
        logger.error(f"Token verification failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication verification failed",
        ) from e


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> ClerkUser:
    """
    FastAPI dependency to get the current authenticated user.

    Usage:
        @app.get("/protected")
        async def protected_route(user: ClerkUser = Depends(get_current_user)):
            return {"user_id": user.user_id}
    """
    token = credentials.credentials
    return await verify_clerk_token(token)


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_security),
) -> ClerkUser | None:
    """
    FastAPI dependency for optional authentication.
    Returns None if no token is provided, otherwise validates the token.

    Usage:
        @app.get("/optional-auth")
        async def optional_route(user: Optional[ClerkUser] = Depends(get_optional_user)):
            if user:
                return {"message": f"Hello {user.user_id}"}
            return {"message": "Hello guest"}
    """
    if not credentials:
        return None

    token = credentials.credentials
    return await verify_clerk_token(token)
