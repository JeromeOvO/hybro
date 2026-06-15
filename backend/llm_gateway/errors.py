class LLMGatewayError(RuntimeError):
    """Base exception for gateway-owned runtime failures."""


class LLMStreamingUnsupportedError(LLMGatewayError):
    """Raised when a resolved provider cannot stream text chunks."""


class LLMModelRoutingError(LLMGatewayError):
    """Raised when a model cannot be resolved to a provider safely."""


class LLMServiceNotBoundError(LLMGatewayError):
    """Raised when a legacy compatibility adapter is used before binding."""
