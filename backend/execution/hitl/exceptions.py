from __future__ import annotations

from typing import Any

from common.errors import AppError


class HITLError(AppError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "HITL_ERROR",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details)


class HITLNotFoundError(HITLError):
    pass


class HITLConflictError(HITLError):
    pass


class HITLRoomMismatchError(HITLError):
    pass


class HITLContinuationLostError(HITLError):
    pass


ContinuationLostError = HITLContinuationLostError


class HITLRoutingFailedError(HITLError):
    pass


__all__ = [
    "ContinuationLostError",
    "HITLConflictError",
    "HITLContinuationLostError",
    "HITLError",
    "HITLNotFoundError",
    "HITLRoomMismatchError",
    "HITLRoutingFailedError",
]
