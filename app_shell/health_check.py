from typing import Any, Callable, Protocol, runtime_checkable

from fastapi import Request
from fastapi.responses import JSONResponse


@runtime_checkable
class HealthCheck(Protocol):
    async def check(self, request: Request) -> JSONResponse: ...

class AppShellHealthCheck:
    def __init__(
        self,
        *,
        redis_url: str | None,
        compute_health_status: Callable[..., dict[str, Any]],
    ) -> None:
        self._redis_url = redis_url
        self._compute_health_status = compute_health_status

    async def check(self, request: Request) -> JSONResponse:
        delivery_facade = getattr(request.app.state, "delivery_facade", None)
        if delivery_facade is not None:
            await delivery_facade.refresh_health()
        legacy_redis_service = getattr(request.app.state, "legacy_redis_service", None)
        relay_service = getattr(request.app.state, "relay_service", None)
        relay_streams = getattr(relay_service, "_streams", None)
        result = self._compute_health_status(
            delivery_pubsub_connected=bool(
                delivery_facade and delivery_facade.delivery_pubsub_connected
            ),
            delivery_kv_connected=bool(
                delivery_facade and delivery_facade.delivery_kv_connected
            ),
            legacy_redis_service_connected=bool(
                legacy_redis_service and legacy_redis_service.is_connected
            ),
            relay_streams_available=bool(
                relay_streams and relay_streams.is_connected
            ),
            redis_url=self._redis_url,
            change_stream_connected=bool(
                delivery_facade and delivery_facade.change_stream_connected
            ),
        )
        return JSONResponse(content=result["body"], status_code=result["status_code"])


__all__ = ["AppShellHealthCheck", "HealthCheck"]
