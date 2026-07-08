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


class HITLRequestProjectionError(HITLError):
    """Projection/update compensation failed while creating a HITL request."""

    def __init__(
        self,
        message: str,
        *,
        request_id: str | None = None,
        code: str = "HITL_REQUEST_PROJECTION_ERROR",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details)
        self.request_id = request_id


class HITLRoutingFailedError(HITLError):
    pass


__all__ = [
    "ContinuationLostError",
    "HITLConflictError",
    "HITLContinuationLostError",
    "HITLError",
    "HITLNotFoundError",
    "HITLRoomMismatchError",
    "HITLRequestProjectionError",
    "HITLRoutingFailedError",
]
