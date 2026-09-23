"""Fail-fast, per-worker admission for one HTTP endpoint."""

from starlette._utils import get_route_path
from starlette.types import ASGIApp, Receive, Scope, Send


class RequestConcurrencyLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        path: str,
        max_concurrent: int,
        method: str = "POST",
    ) -> None:
        self.app = app
        self.path = path.rstrip("/")
        self.max_concurrent = max_concurrent
        self.method = method
        self._active = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != self.method
            or get_route_path(scope).rstrip("/") != self.path
        ):
            await self.app(scope, receive, send)
            return

        # No await between checking and taking a slot: admission is atomic on
        # the worker's event loop, with no waiting requests or shared app state.
        if self._active >= self.max_concurrent:
            body = b'{"detail":"Too many concurrent requests"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        self._active += 1
        try:
            await self.app(scope, receive, send)
        finally:
            self._active -= 1
