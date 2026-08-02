"""Room-owned errors and helpers for user-message request idempotency."""

from __future__ import annotations

from typing import Any


class IdempotencyConflictError(Exception):
    """The same room/request key was reused for a different semantic payload."""

    def __init__(self, room_id: str, client_request_id: str) -> None:
        super().__init__("The idempotency key was already used for a different request")
        self.room_id = room_id
        self.client_request_id = client_request_id


class UserMessagePersistenceError(Exception):
    """A user-message insert failed for a reason other than a valid replay."""


class UnexpectedUserMessageDuplicateError(UserMessagePersistenceError):
    """A unique index other than the matching request key rejected the insert."""


def stored_fingerprint_matches(
    document: dict[str, Any],
    *,
    fingerprint: str,
    fingerprint_version: int,
) -> bool:
    return (
        document.get("idempotency_fingerprint") == fingerprint
        and document.get("idempotency_fingerprint_version") == fingerprint_version
    )


__all__ = [
    "IdempotencyConflictError",
    "UnexpectedUserMessageDuplicateError",
    "UserMessagePersistenceError",
    "stored_fingerprint_matches",
]
