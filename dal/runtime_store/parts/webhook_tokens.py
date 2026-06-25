from __future__ import annotations

import hashlib
import hmac
import secrets

from common.config.settings import settings


def get_webhook_signing_key() -> bytes:
    if not settings.webhook_signing_key:
        raise RuntimeError("WEBHOOK_SIGNING_KEY not configured")
    return settings.webhook_signing_key.encode()


def hash_webhook_token(token: str) -> str:
    return hmac.new(
        get_webhook_signing_key(),
        token.encode(),
        hashlib.sha256,
    ).hexdigest()


def verify_webhook_token(token: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_webhook_token(token), stored_hash)


def generate_webhook_token() -> str:
    return secrets.token_urlsafe(32)
