"""
Mock Authentication for FastAPI (Open Source Edition)
Replaces Clerk validation with a local-only dummy user context.
"""
import logging

from fastapi import Request

logger = logging.getLogger(__name__)

class ClerkUser:
    """Represents an authenticated local user"""
    def __init__(self, user_id: str, session_id: str, claims: dict):
        self.user_id = user_id
        self.session_id = session_id
        self.claims = claims
        self.role = claims.get("role", "user")
        
    def is_admin(self) -> bool:
        """Check if user has admin role"""
        return self.role == "admin"


async def verify_clerk_token_from_request(request: Request) -> ClerkUser:
    """
    Mock token verification. Always returns the local developer user.
    """
    payload = {
        "sub": "user_local_developer",
        "role": "admin",
        "email": "developer@hybro.local"
    }
    
    return ClerkUser(
        user_id="user_local_developer", 
        session_id="sess_local_dev", 
        claims=payload
    )


async def get_current_user(
    request: Request,
) -> ClerkUser:
    """
    Dependency for protected routes. Returns the local mock user.
    """
    return await verify_clerk_token_from_request(request)


async def get_optional_user(
    request: Request,
) -> ClerkUser | None:
    """
    Dependency for routes where auth is optional.
    Returns the mock user regardless.
    """
    return await verify_clerk_token_from_request(request)


async def get_current_user_with_query_token(
    request: Request,
) -> ClerkUser:
    """
    Dependency for endpoints like SSE that may pass tokens in query params.
    """
    return await verify_clerk_token_from_request(request)

def resolve_provider_name(provider_id: str) -> str:
    """Mock implementation returning a static user."""
    return "Developer Local"
