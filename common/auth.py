"""
Clerk Authentication for FastAPI
Validates JWT tokens from Clerk and provides user authentication.

Two verification paths:
1. Clerk SDK session handshake (browser / frontend requests with cookies).
2. Direct Bearer JWT decode (Postman / API clients that send a raw token).
"""

import jwt

from clerk_backend_api import authenticate_request
from clerk_backend_api.security.types import AuthenticateRequestOptions
from fastapi import HTTPException, Request, status
from loguru import logger

from config.settings import settings

# Authorized parties (azp) for Clerk JWT verification
# These are the origins that are allowed to use the Clerk tokens
AUTHORIZED_PARTIES = [
    "https://hybro.ai",
    "https://developer.hybro.ai",
    # Include localhost for development
    "http://localhost:3000",
    "http://dev.localhost:3000",
]

_jwks_client: jwt.PyJWKClient | None = None


def _get_jwks_client() -> jwt.PyJWKClient:
    """
    Build a JWKS client pointed at the correct Clerk instance.

    Clerk secret keys have the form:  sk_live_<base58_payload>
    The Clerk Backend API exposes JWKS at:
        https://<clerk_secret_key>/.well-known/jwks.json  (via Clerk's own SDK)
    but the simplest universal endpoint is the one the SDK already uses:
        https://api.clerk.dev/v1/jwks   with Authorization: Bearer <secret_key>

    PyJWKClient doesn't support auth headers, so we use Clerk's public JWKS
    URL which is derived from the publishable key / CLERK_JWKS_URL env var if
    set, otherwise we fall back to fetching keys via the Clerk REST API once
    and caching the result in a local JWKSet.
    """
    global _jwks_client
    if _jwks_client is None:
        # Clerk exposes a public JWKS endpoint per instance.
        # Format: https://<instance>.clerk.accounts.dev/.well-known/jwks.json
        # We can derive this from the secret key: sk_live_<base58>
        # The simplest reliable approach: use Clerk's API with the secret key.
        jwks_url = "https://api.clerk.dev/v1/jwks"
        _jwks_client = jwt.PyJWKClient(
            jwks_url,
            cache_keys=True,
            headers={"Authorization": f"Bearer {settings.clerk_secret_key}"},
        )
    return _jwks_client


def _verify_bearer_jwt(token: str) -> dict:
    """
    Verify a raw Clerk JWT using JWKS (RS256).
    Returns the decoded payload or raises HTTPException on failure.
    """
    try:
        client = _get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_exp": True},
        )
        # Validate azp (authorized party) claim if present
        azp = payload.get("azp")
        if azp and azp not in AUTHORIZED_PARTIES:
            logger.warning(f"JWT azp claim '{azp}' not in AUTHORIZED_PARTIES")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: unauthorized party",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return payload
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e
    except (jwt.InvalidTokenError, Exception) as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e


class ClerkUser:
    """Represents an authenticated Clerk user"""

    def __init__(self, user_id: str, session_id: str, claims: dict):
        self.user_id = user_id
        self.session_id = session_id
        self.claims = claims
        self.email = claims.get("email")
        self.username = claims.get("username")


async def verify_clerk_token_from_request(request: Request) -> ClerkUser:
    """
    Verify a Clerk JWT token from a FastAPI Request.

    Strategy:
    1. Try the Clerk SDK session handshake (works for browser/frontend clients).
    2. If the SDK says not signed in but a Bearer token is present, fall back to
       direct JWKS-based JWT verification (works for Postman / API clients).

    Args:
        request: The FastAPI Request object

    Returns:
        ClerkUser object with user information

    Raises:
        HTTPException: If token is invalid or verification fails
    """
    try:
        options = AuthenticateRequestOptions(
            secret_key=settings.clerk_secret_key,
            authorized_parties=AUTHORIZED_PARTIES,
        )
        request_state = authenticate_request(request, options)

        if request_state.is_signed_in and request_state.payload:
            payload = request_state.payload
            user_id = payload.get("sub")
            session_id = payload.get("sid")
            if not user_id:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token: missing user ID",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return ClerkUser(user_id=user_id, session_id=session_id, claims=payload)

        # SDK handshake did not resolve — try raw Bearer token (Postman / API clients)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            raw_token = auth_header[len("Bearer "):]
            payload = _verify_bearer_jwt(raw_token)
            user_id = payload.get("sub")
            session_id = payload.get("sid")
            if not user_id:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token: missing user ID",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return ClerkUser(user_id=user_id, session_id=session_id, claims=payload)

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token verification failed: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication verification failed",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e


async def get_current_user(
    request: Request,
) -> ClerkUser:
    """
    FastAPI dependency to get the current authenticated user.
    Uses Clerk SDK for token verification.

    Usage:
        @app.get("/protected")
        async def protected_route(user: ClerkUser = Depends(get_current_user)):
            return {"user_id": user.user_id}
    """
    # The Clerk SDK will verify the token from the request automatically
    return await verify_clerk_token_from_request(request)


async def get_optional_user(
    request: Request,
) -> ClerkUser | None:
    """
    FastAPI dependency for optional authentication.
    Returns None if no token is provided, otherwise validates the token using Clerk SDK.

    Usage:
        @app.get("/optional-auth")
        async def optional_route(user: Optional[ClerkUser] = Depends(get_optional_user)):
            if user:
                return {"message": f"Hello {user.user_id}"}
            return {"message": "Hello guest"}
    """
    # Try to verify token from request, return None if no token provided
    try:
        return await verify_clerk_token_from_request(request)
    except HTTPException:
        return None


async def get_current_user_with_query_token(
    request: Request,
) -> ClerkUser:
    """
    FastAPI dependency for authentication that supports both:
    - Query parameter token (for SSE/EventSource which can't send custom headers) - checked first
    - Authorization header (fallback) - verified by Clerk SDK

    Usage:
        @app.get("/sse/stream")
        async def sse_stream(user: ClerkUser = Depends(get_current_user_with_query_token)):
            return StreamingResponse(...)
    """
    # Check for query parameter token first (primary for SSE)
    token = request.query_params.get("token")
    if token:
        # For query parameter tokens, we need to create a modified request
        # with the token in the Authorization header for the SDK to verify
        
        # Build headers list from original request, replacing/adding authorization
        # ASGI headers must be lowercase bytes tuples
        original_headers = list(request.scope.get("headers", []))
        
        # Remove any existing authorization header (case-insensitive)
        filtered_headers = [
            (k, v) for k, v in original_headers 
            if k.lower() != b"authorization"
        ]
        
        # Add the new authorization header
        filtered_headers.append((b"authorization", f"Bearer {token}".encode()))

        # Create a modified request scope
        modified_scope = dict(request.scope)
        modified_scope["headers"] = filtered_headers

        # Create new request with modified headers
        modified_request = Request(scope=modified_scope)

        return await verify_clerk_token_from_request(modified_request)

    # Fall back to Authorization header (standard auth)
    return await verify_clerk_token_from_request(request)
