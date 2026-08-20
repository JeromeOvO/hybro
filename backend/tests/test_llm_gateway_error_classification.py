from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from llm_gateway.error_classification import classify_gateway_error


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, status_code=None, retry_after=None, code=None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.response = SimpleNamespace(
            status_code=status_code,
            headers={} if retry_after is None else {"Retry-After": str(retry_after)},
        )


@pytest.mark.parametrize(
    ("error", "kind", "retryable"),
    [
        (ProviderError("bad key", status_code=401), "authentication", False),
        (ProviderError("slow", status_code=429, retry_after=12), "rate_limit", True),
        (ProviderError("down", status_code=503), "provider_5xx", True),
        (
            ProviderError("maximum context length", status_code=400),
            "context_overflow",
            False,
        ),
        (ProviderError("bad request", status_code=400), "invalid_request", False),
        (ProviderError("network connection failed"), "network", True),
        (ProviderError("filtered", code="content_filter"), "content_filter", False),
        (TimeoutError(), "timeout", True),
        (asyncio.CancelledError(), "aborted", False),
    ],
)
def test_gateway_error_classification(error, kind, retryable):
    classified = classify_gateway_error(error)
    assert (classified.error_class, classified.retryable) == (kind, retryable)


def test_retry_after_is_normalized_and_never_contains_error_payload():
    classified = classify_gateway_error(
        ProviderError("secret", status_code=429, retry_after=-3)
    )
    assert classified.retry_after_seconds == 0
    assert "secret" not in repr(classified)
