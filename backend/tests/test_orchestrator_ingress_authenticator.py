from __future__ import annotations

import pytest

from a2a_adapter.orchestrator_ingress_authenticator import (
    WebhookObservationIngressAuthenticator,
)


class FakeVerifier:
    def __init__(self, valid=True, reason=None):
        self.valid = valid
        self.reason = reason
        self.calls = []

    async def __call__(self, message_id, token):
        self.calls.append((message_id, token))
        return self.valid, self.reason


async def test_webhook_authenticator_accepts_notification_token_header():
    verifier = FakeVerifier()
    authenticator = WebhookObservationIngressAuthenticator(
        verify_token_for_task=verifier
    )
    identity = await authenticator.authenticate(
        source_kind="webhook",
        headers={
            "x-a2a-message-id": "message-1",
            "X-A2A-Notification-Token": "secret-token",
        },
        body=b"{}",
    )
    assert identity == "webhook:message-1"
    assert verifier.calls == [("message-1", "secret-token")]


async def test_webhook_authenticator_accepts_authorization_bearer():
    verifier = FakeVerifier()
    authenticator = WebhookObservationIngressAuthenticator(
        verify_token_for_task=verifier
    )
    identity = await authenticator.authenticate(
        source_kind="webhook",
        headers={
            "x-a2a-message-id": "message-2",
            "Authorization": "Bearer bearer-token",
        },
        body=b"{}",
    )
    assert identity == "webhook:message-2"
    assert verifier.calls == [("message-2", "bearer-token")]


async def test_webhook_authenticator_rejects_missing_identity():
    verifier = FakeVerifier()
    authenticator = WebhookObservationIngressAuthenticator(
        verify_token_for_task=verifier
    )
    with pytest.raises(PermissionError, match="missing message identity"):
        await authenticator.authenticate(
            source_kind="webhook",
            headers={"X-A2A-Notification-Token": "secret"},
            body=b"{}",
        )


async def test_webhook_authenticator_rejects_invalid_token():
    verifier = FakeVerifier(valid=False, reason="invalid_token")
    authenticator = WebhookObservationIngressAuthenticator(
        verify_token_for_task=verifier
    )
    with pytest.raises(PermissionError, match="invalid_token"):
        await authenticator.authenticate(
            source_kind="webhook",
            headers={
                "x-a2a-message-id": "message-1",
                "X-A2A-Notification-Token": "wrong",
            },
            body=b"{}",
        )


async def test_webhook_authenticator_rejects_other_source_kinds():
    verifier = FakeVerifier()
    authenticator = WebhookObservationIngressAuthenticator(
        verify_token_for_task=verifier
    )
    with pytest.raises(PermissionError):
        await authenticator.authenticate(
            source_kind="relay",
            headers={"x-a2a-message-id": "message-1"},
            body=b"{}",
        )
