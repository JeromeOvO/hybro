from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import aclosing
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
from a2a.client.errors import A2AClientHTTPError

from common.url_utils import LOCAL_HOST_ALIASES

logger = logging.getLogger(__name__)

_DOCKER_HOST = "host.docker.internal"
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


async def with_docker_host_url_fallback[T](
    url: str,
    operation: Callable[[str], Awaitable[T]],
) -> T:
    try:
        return await operation(url)
    except Exception as exc:
        fallback_url = docker_host_fallback_url_for_error(url, exc)
        if fallback_url is None:
            raise
        return await operation(fallback_url)


async def stream_with_docker_host_url_fallback[T](
    url: str,
    operation: Callable[[str], AsyncGenerator[T, None]],
) -> AsyncGenerator[T, None]:
    yielded_any = False
    try:
        async with aclosing(operation(url)) as stream:
            async for item in stream:
                yielded_any = True
                yield item
    except Exception as exc:
        if yielded_any:
            raise
        fallback_url = docker_host_fallback_url_for_error(url, exc)
        if fallback_url is None:
            raise
        async with aclosing(operation(fallback_url)) as fallback_stream:
            async for item in fallback_stream:
                yield item


async def stream_with_docker_host_fallback[T](
    card: Any,
    operation: Callable[[Any], AsyncGenerator[T, None]],
) -> AsyncGenerator[T, None]:
    try:
        async with aclosing(operation(card)) as stream:
            async for item in stream:
                yield item
    except Exception as exc:
        fallback_card = _fallback_card(card, exc)
        if fallback_card is None:
            raise
        async with aclosing(operation(fallback_card)) as fallback_stream:
            async for item in fallback_stream:
                yield item


def _fallback_card(card: Any, exc: Exception) -> Any | None:
    original_url = str(getattr(card, "url", "") or "")
    fallback_url = docker_host_fallback_url_for_error(original_url, exc)
    if fallback_url is None:
        return None

    return _copy_card_with_url(card, fallback_url)


def docker_host_fallback_url_for_error(url: str, exc: Exception) -> str | None:
    fallback_url = docker_host_fallback_url(url)
    if fallback_url is None or not _is_network_connection_error(exc):
        return None
    logger.debug(
        "a2a_docker_host_fallback_selected",
        extra={"fallback_url": fallback_url, "original_url": url},
    )
    return fallback_url


def docker_host_fallback_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    hostname = (parsed.hostname or "").lower()
    if hostname not in LOCAL_HOST_ALIASES:
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
    "docker_host_fallback_url_for_error",
    "stream_with_docker_host_fallback",
    "stream_with_docker_host_url_fallback",
    "with_docker_host_fallback",
    "with_docker_host_url_fallback",
]
