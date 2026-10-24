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
        include_subpaths: bool = False,
    ) -> None:
        self.app = app
        self.path = path.rstrip("/")
        self.max_concurrent = max_concurrent
        self.method = method
        # Subpaths share one admission pool, for routes with path parameters.
        self.include_subpaths = include_subpaths
        self._active = 0

    def _matches(self, route_path: str) -> bool:
        route_path = route_path.rstrip("/")
        return route_path == self.path or (
            self.include_subpaths and route_path.startswith(f"{self.path}/")
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != self.method
            or not self._matches(get_route_path(scope))
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
