from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
from a2a.client.errors import A2AClientHTTPError

logger = logging.getLogger(__name__)

_DOCKER_HOST = "host.docker.internal"
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})
_NETWORK_ERROR_MARKERS = (
    "network communication",
    "connection",
    "connect",
    "all connection attempts failed",
)

async def with_docker_host_fallback[T](
    card: Any,
    operation: Callable[[Any], Awaitable[T]],
) -> T:
    try:
        return await operation(card)
    except Exception as exc:
        fallback_card = _fallback_card(card, exc)
        if fallback_card is None:
            raise
        return await operation(fallback_card)


async def stream_with_docker_host_fallback[T](
    card: Any,
    operation: Callable[[Any], AsyncGenerator[T, None]],
) -> AsyncGenerator[T, None]:
    try:
        async for item in operation(card):
            yield item
    except Exception as exc:
        fallback_card = _fallback_card(card, exc)
        if fallback_card is None:
            raise
        async for item in operation(fallback_card):
            yield item


def _fallback_card(card: Any, exc: Exception) -> Any | None:
    original_url = str(getattr(card, "url", "") or "")
    fallback_url = docker_host_fallback_url(original_url)
    if fallback_url is None or not _is_network_connection_error(exc):
        return None

    logger.warning(
        "fall_back from docker to local service: retrying A2A request with %s "
        "instead of %s",
        fallback_url,
        original_url,
    )
    return _copy_card_with_url(card, fallback_url)


def docker_host_fallback_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    hostname = (parsed.hostname or "").lower()
    if hostname not in _LOCAL_HOSTS:
        return None

    port = parsed.port
    netloc = _DOCKER_HOST if port is None else f"{_DOCKER_HOST}:{port}"
    return urlunparse(
        (
            parsed.scheme,
            netloc,
            parsed.path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )


def _is_network_connection_error(exc: Exception) -> bool:
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.NetworkError)):
        return True
    if isinstance(exc, OSError):
        return True
    if getattr(exc, "status_code", None) == 503:
        message = str(exc).lower()
        return any(marker in message for marker in _NETWORK_ERROR_MARKERS)
    if isinstance(exc, A2AClientHTTPError):
        message = str(exc).lower()
        return any(marker in message for marker in _NETWORK_ERROR_MARKERS)
    return False


def _copy_card_with_url(card: Any, url: str) -> Any:
    if hasattr(card, "model_dump"):
        data = card.model_dump(mode="json", by_alias=True)
        data["url"] = url
        return type(card)(**data)
    data = dict(getattr(card, "__dict__", {}))
    data["url"] = url
    return type(card)(**data)


__all__ = [
    "docker_host_fallback_url",
    "stream_with_docker_host_fallback",
    "with_docker_host_fallback",
]
