"""Canonical runtime configuration and credential-source validation.

No environment, application settings, or Provider clients are loaded on import.
Callers supply the already-resolved deployment environment explicitly.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)

ProviderId = Literal["openai", "deepseek", "anthropic"]
AuthMethod = Literal["api_key", "oauth"]
ModelId = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]

_PROVIDER_ENV_KEYS: dict[ProviderId, str] = {
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


class RuntimeConfigurationError(ValueError):
    """Safe configuration failure: messages must never include credential input."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, hide_input_in_errors=True
    )


class RuntimeProvider(_StrictModel):
    id: ProviderId
    auth: AuthMethod

    @model_validator(mode="after")
    def validate_auth(self) -> Self:
        if self.auth == "oauth" and self.id != "openai":
            raise ValueError("Only OpenAI supports OAuth configuration")
        return self


class RuntimeModels(_StrictModel):
    text: ModelId
    image: ModelId | None = None


class RuntimeConfig(_StrictModel):
    version: Literal[1] = 1
    provider: RuntimeProvider
    models: RuntimeModels

    @field_validator("version", mode="before")
    @classmethod
    def validate_version(cls, value: object) -> object:
        # Literal[1] otherwise accepts True or 1.0 because Python equates them.
        if type(value) is not int:
            raise ValueError("Schema version must be an integer")
        return value

    @property
    def revision(self) -> str:
        canonical = json.dumps(
            self.model_dump(exclude_none=True),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        digest = hashlib.sha256(canonical).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


class ApiKeyCredential(_StrictModel):
    provider: ProviderId
    auth: Literal["api_key"] = "api_key"
    api_key: SecretStr

    @field_validator("api_key")
    @classmethod
    def validate_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("API key must not be blank")
        return value


class OAuthCredential(_StrictModel):
    """Subscription credentials and unsigned account routing metadata."""

    provider: Literal["openai"] = "openai"
    auth: Literal["oauth"] = "oauth"
    access_token: SecretStr
    refresh_token: SecretStr
    expires_at: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    account_id: Annotated[
        str,
        StringConstraints(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_-]+$"),
    ]

    @model_validator(mode="after")
    def validate_account(self) -> Self:
        from llm_gateway.openai_oauth import account_id

        if account_id(self.access_token.get_secret_value()) != self.account_id:
            raise ValueError("OAuth account metadata mismatch")
        return self

    @field_validator("access_token", "refresh_token")
    @classmethod
    def validate_token(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("OAuth token must not be blank")
        return value


StoredCredential = Annotated[
    ApiKeyCredential | OAuthCredential, Field(discriminator="auth")
]
CREDENTIAL_ADAPTER: TypeAdapter[StoredCredential] = TypeAdapter(StoredCredential)


@dataclass(frozen=True, slots=True)
class ResolvedCredential:
    source: Literal["environment", "stored"]
    credential: ApiKeyCredential | OAuthCredential


def resolve_credential(
    config: RuntimeConfig,
    stored: StoredCredential | None,
    environment: Mapping[str, str],
) -> ResolvedCredential:
    """Resolve one source, never substitute a key for an OAuth session."""
    if stored is not None and (stored.provider, stored.auth) != (
        config.provider.id,
        config.provider.auth,
    ):
        raise RuntimeConfigurationError(
            "Configuration and stored credential identity differ; rerun setup."
        )
    if config.provider.auth == "oauth":
        if not isinstance(stored, OAuthCredential):
            raise RuntimeConfigurationError("OAuth credential missing; rerun setup.")
        return ResolvedCredential("stored", stored)
    key = environment.get(_PROVIDER_ENV_KEYS[config.provider.id], "")
    if key.strip() and stored is not None:
        raise RuntimeConfigurationError(
            "Both environment and stored credentials are configured; remove one."
        )
    if key.strip():
        credential = ApiKeyCredential(
            provider=config.provider.id, api_key=SecretStr(key)
        )
        return ResolvedCredential("environment", credential)
    if isinstance(stored, ApiKeyCredential):
        return ResolvedCredential("stored", stored)
    raise RuntimeConfigurationError("Provider API key missing; run setup.")
