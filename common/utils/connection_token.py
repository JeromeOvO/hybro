"""JWT-based connection tokens for relay /publish authentication.

Tokens are issued when a hub connects via SSE and must be presented
on every POST to /publish.  They are short-lived (1 h by default)
and bound to a specific hub_id.
"""

from __future__ import annotations

from datetime import timedelta

import jwt

from common.utils.time import utcnow


def create_connection_token(
    hub_id: str,
    secret: str,
    ttl_hours: int = 1,
) -> str:
    if not secret:
        raise ValueError("relay_connection_token_secret must not be empty")
    payload = {
        "hub_id": hub_id,
        "iat": utcnow(),
        "exp": utcnow() + timedelta(hours=ttl_hours),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def verify_connection_token(
    token: str,
    hub_id: str,
    secret: str,
) -> bool:
    """Return True if *token* is valid and was issued for *hub_id*."""
    if not secret:
        return False
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        return payload.get("hub_id") == hub_id
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return False
