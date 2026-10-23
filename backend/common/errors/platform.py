class PlatformRouteError(Exception):
    def __init__(self, status_code: int, detail) -> None:
        self.status_code = status_code
        self.detail = detail
        message = detail.get("message") if isinstance(detail, dict) else str(detail)
        super().__init__(message)


class GatewayPlatformError(PlatformRouteError):
    pass


class FileStoragePlatformError(PlatformRouteError):
    pass


class RetryableFileStoragePlatformError(FileStoragePlatformError):
    """A transient file backend failure safe to retry with identical bytes."""


class UnsupportedAgentProtocolError(ValueError):
    """An agent card advertises an A2A protocol version Hybro does not speak."""


__all__ = [
    "FileStoragePlatformError",
    "GatewayPlatformError",
    "RetryableFileStoragePlatformError",
    "PlatformRouteError",
    "UnsupportedAgentProtocolError",
]
