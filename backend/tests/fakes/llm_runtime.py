"""Explicit canonical config/credential fixtures; never load developer state."""

import base64
import json
import time

from pydantic import SecretStr

from common.config.runtime_config import (
    ApiKeyCredential,
    OAuthCredential,
    ResolvedCredential,
    RuntimeConfig,
    RuntimeModels,
    RuntimeProvider,
)
from common.config.runtime_store import RuntimeState


def oauth_credential(
    account="fixture-account", *, expires_at=None, refresh="fixture-refresh"
) -> OAuthCredential:
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {"https://api.openai.com/auth": {"chatgpt_account_id": account}}
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    return OAuthCredential(
        access_token=SecretStr("fixture." + payload + ".signature"),
        refresh_token=SecretStr(refresh),
        account_id=account,
        expires_at=float(expires_at if expires_at is not None else time.time() + 3600),
    )


def oauth_config() -> RuntimeConfig:
    return RuntimeConfig(
        provider=RuntimeProvider(id="openai", auth="oauth"),
        models=RuntimeModels(text="gpt-5.4"),
    )


def runtime_state(provider="openai", model="gpt-4o-mini") -> RuntimeState:
    return RuntimeState(
        RuntimeConfig(
            provider=RuntimeProvider(id=provider, auth="api_key"),
            models=RuntimeModels(text=model),
        ),
        ResolvedCredential(
            "stored",
            ApiKeyCredential(provider=provider, api_key=SecretStr("test-only-key")),
        ),
    )
