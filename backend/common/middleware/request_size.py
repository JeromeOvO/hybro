from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from starlette._utils import get_route_path
from starlette.exceptions import HTTPException


class RequestBodyLimitMiddleware:
    """Bound selected request bodies before framework-level parsing."""

    def __init__(
        self,
        app,
        *,
        path: str,
        max_bytes: int,
        method: str | None = None,
    ) -> None:
        self.app = app
        self.path = path.rstrip("/")
        self.max_bytes = max_bytes
        self.method = method

    async def __call__(self, scope, receive, send) -> None:
        if (
            scope["type"] != "http"
            or (self.method is not None and scope.get("method") != self.method)
            or get_route_path(scope).rstrip("/") != self.path
        ):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                if int(content_length) > self.max_bytes:
                    await self._reject(send)
                    return
            except ValueError:
                await self._reject(send, status=400)
                return

        received = 0

        async def bounded_receive() -> dict[str, Any]:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _RequestBodyTooLarge
            return message

        try:
            await self.app(scope, bounded_receive, send)
        except _RequestBodyTooLarge:
            await self._reject(send)

    @staticmethod
    async def _reject(
        send: Callable[[dict[str, Any]], Awaitable[None]], *, status: int = 413
    ) -> None:
        body = b'{"detail":"Payload too large"}'
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class _RequestBodyTooLarge(HTTPException):
    # FastAPI preserves HTTPException during body parsing; ordinary exceptions
    # are converted to 400 before they can reach this middleware.
    def __init__(self) -> None:
        super().__init__(status_code=413, detail="Payload too large")
