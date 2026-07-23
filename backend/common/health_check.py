from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class RuntimeHealthCheck:
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
        redis_runtime = getattr(request.app.state, "redis_runtime", None)
        redis_service = getattr(redis_runtime, "command_client", None)
        relay_streams = getattr(redis_runtime, "relay_streams", None)
        result = self._compute_health_status(
            delivery_pubsub_connected=bool(
                delivery_facade and delivery_facade.delivery_pubsub_connected
            ),
            delivery_kv_connected=bool(
                delivery_facade and delivery_facade.delivery_kv_connected
            ),
            redis_runtime_connected=bool(redis_service and redis_service.is_connected),
            relay_streams_available=bool(relay_streams and relay_streams.is_connected),
            redis_url=self._redis_url,
            change_stream_connected=bool(
                delivery_facade and delivery_facade.change_stream_connected
            ),
            agent_search_index_ready=bool(
                getattr(request.app.state, "agent_search_index_ready", False)
            ),
            memory_search_index_ready=bool(
                getattr(request.app.state, "memory_search_index_ready", False)
            ),
            search_indexes_ready=bool(
                getattr(request.app.state, "search_indexes_ready", False)
            ),
        )
        return JSONResponse(content=result["body"], status_code=result["status_code"])


__all__ = ["RuntimeHealthCheck"]
