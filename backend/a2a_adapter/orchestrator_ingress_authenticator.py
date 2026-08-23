"""Observation ingress authenticators for the orchestrator runtime.

These adapters bridge the existing A2A webhook token scheme to the orchestrator's
provider-neutral ``ObservationIngressAuthenticator`` signature
(``authenticate(source_kind, headers, body) -> source_identity``). They live in
``a2a_adapter`` so the orchestrator package never sees the token/header details,
and they keep the existing ``hash_webhook_token`` / ``verify_webhook_token``
scheme as the single source of truth (no second HMAC key or scheme).
"""

from __future__ import annotations

from typing import Protocol


class WebhookTokenVerifier(Protocol):
    async def __call__(self, message_id: str, token: str) -> tuple[bool, str]: ...


class WebhookObservationIngressAuthenticator:
    """Authenticate A2A push notifications against the stored message token hash.

    The existing FastAPI route (``api_gateway/routes/webhook_routes.py``) reads
    the token from ``X-A2A-Notification-Token`` / ``Authorization: Bearer`` and
    looks up the stored HMAC digest by the ``{message_id}`` path parameter. The
    orchestrator ingress signature has no path parameter, so the routing seam
    (step 7) must surface the path parameter as the ``x-a2a-message-id`` header
    before calling ``authenticate``; the verifier then performs the exact same
    ``hmac.compare_digest`` lookup through the injected store port.
    """

    def __init__(
        self,
        *,
        verify_token_for_task: WebhookTokenVerifier,
        message_id_header: str = "x-a2a-message-id",
    ) -> None:
        self._verify_token_for_task = verify_token_for_task
        self._message_id_header = message_id_header.lower()

    async def authenticate(
        self,
        *,
        source_kind: str,
        headers: dict[str, str],
        body: bytes,
    ) -> str:
        del body
        if source_kind != "webhook":
            raise PermissionError(
                "webhook authenticator does not accept this ingress source"
            )
        normalized = {key.lower(): value for key, value in headers.items()}
        message_id = normalized.get(self._message_id_header) or ""
        if not message_id:
            raise PermissionError("webhook ingress is missing message identity")
        token = _extract_bearer_token(normalized)
        if not token:
            raise PermissionError("webhook ingress is missing authorization token")
        valid, reason = await self._verify_token_for_task(message_id, token)
        if not valid:
            raise PermissionError(
                f"webhook token verification failed: {reason or 'invalid token'}"
            )
        return f"webhook:{message_id}"


class RejectExternalIngressAuthenticator:
    """Safe default that rejects every external ingress until a source is enabled."""

    async def authenticate(self, **_: object) -> str:
        raise PermissionError("external observation ingress is not configured")


def _extract_bearer_token(headers: dict[str, str]) -> str:
    token = headers.get("x-a2a-notification-token") or ""
    if token:
        return token
    authorization = headers.get("authorization") or ""
    if authorization.startswith("Bearer "):
        return authorization[len("Bearer ") :]
    return ""


__all__ = [
    "RejectExternalIngressAuthenticator",
    "WebhookObservationIngressAuthenticator",
    "WebhookTokenVerifier",
]
