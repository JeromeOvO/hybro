"""Provider exception classification for one-attempt gateway calls."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .turn_types import GatewayErrorClass


@dataclass(frozen=True, slots=True)
class ClassifiedGatewayError:
    error_class: GatewayErrorClass
    retryable: bool
    retry_after_seconds: float | None = None


def classify_gateway_error(exc: BaseException) -> ClassifiedGatewayError:
    if isinstance(exc, asyncio.CancelledError):
        return ClassifiedGatewayError("aborted", False)
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return ClassifiedGatewayError("timeout", True)

    status = _status_code(exc)
    code = str(getattr(exc, "code", "") or "").lower()
    message = str(exc).lower()
    retry_after = _retry_after(exc)
    if status in {401, 403}:
        return ClassifiedGatewayError("authentication", False)
    if status == 429:
        return ClassifiedGatewayError("rate_limit", True, retry_after)
    if status is not None and 500 <= status <= 599:
        return ClassifiedGatewayError("provider_5xx", True, retry_after)
    if status in {400, 404, 409, 422}:
        overflow = "context" in message and any(
            token in message for token in ("length", "window", "maximum", "too long")
        )
        return ClassifiedGatewayError(
            "context_overflow" if overflow else "invalid_request", False
        )
    if "content_filter" in code or "content filter" in message:
        return ClassifiedGatewayError("content_filter", False)
    if any(token in message for token in ("connection", "network", "dns")):
        return ClassifiedGatewayError("network", True)
    return ClassifiedGatewayError("unknown", False)


def _status_code(exc: BaseException) -> int | None:
    value: Any = getattr(exc, "status_code", None)
    if value is None:
        response = getattr(exc, "response", None)
        value = getattr(response, "status_code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _retry_after(exc: BaseException) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    value = headers.get("retry-after") or headers.get("Retry-After")
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, parsed)


__all__ = ["ClassifiedGatewayError", "classify_gateway_error"]
