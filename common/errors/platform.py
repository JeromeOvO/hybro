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


__all__ = [
    "FileStoragePlatformError",
    "GatewayPlatformError",
    "PlatformRouteError",
]
