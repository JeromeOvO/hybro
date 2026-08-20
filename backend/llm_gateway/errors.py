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
