from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


class RequestBodyLimitMiddleware:
    """Bound selected request bodies before framework-level parsing."""

    def __init__(
        self,
        app,
        *,
        path: str,
        max_bytes: int,
    ) -> None:
        self.app = app
        self.path = path
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope.get("path") != self.path:
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
        rejected = False

        async def bounded_receive() -> dict[str, Any]:
            nonlocal received, rejected
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    rejected = True
                    raise _RequestBodyTooLarge
            return message

        try:
            await self.app(scope, bounded_receive, send)
        except _RequestBodyTooLarge:
            if not rejected:
                raise
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


class _RequestBodyTooLarge(Exception):
    pass
