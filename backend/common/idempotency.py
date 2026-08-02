"""Shared request-id normalization constants for persistence idempotency."""

from __future__ import annotations

MAX_CLIENT_REQUEST_ID_LENGTH = 128


def normalize_client_request_id(value: str) -> str:
    """Return the canonical client request key used at API and storage boundaries."""

    return value.strip()


__all__ = ["MAX_CLIENT_REQUEST_ID_LENGTH", "normalize_client_request_id"]
