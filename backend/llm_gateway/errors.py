from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_gateway._diagnostics import _Diagnostic
    from llm_gateway.error_classification import ClassifiedGatewayError


class LLMGatewayError(RuntimeError):
    """Base exception for gateway-owned runtime failures."""


class LLMStreamingUnsupportedError(LLMGatewayError):
    """Raised when a resolved provider cannot stream text chunks."""


class LLMModelRoutingError(LLMGatewayError):
    """Raised when a model cannot be resolved to a provider safely."""


class LLMProviderConfigurationError(LLMGatewayError):
    """Raised when a selected provider has no valid credential/configuration."""


class UnsupportedConfiguredProvider(LLMProviderConfigurationError):
    """Raised for a retired provider found in deployment settings."""

    code = "unsupported_configured_provider"


class LLMServiceNotBoundError(LLMGatewayError):
    """Raised when a legacy compatibility adapter is used before binding."""


class LLMProviderFailure(LLMGatewayError):
    """Sanitized failure retaining only retry classification and correlation."""

    def __init__(
        self,
        classification: ClassifiedGatewayError,
        client_request_id: str | None,
        *,
        diagnostic: _Diagnostic | None = None,
    ) -> None:
        super().__init__("Provider text operation failed")
        self._diagnostic = diagnostic
        self.classification = classification
        self.client_request_id = client_request_id
        self.code = classification.error_class
