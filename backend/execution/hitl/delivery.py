from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class HITLDeliveryDisposition(StrEnum):
    """What the command journal may safely do after a delivery failure."""

    RETRYABLE = "retryable"
    DELIVERY_UNCERTAIN = "delivery_uncertain"
    PERMANENT = "permanent"


class HITLDeliveryPhase(StrEnum):
    PRE_SEND = "pre_send"
    IN_FLIGHT = "in_flight"
    REMOTE_RESPONSE = "remote_response"
    POST_SEND_PERSISTENCE = "post_send_persistence"


@dataclass(frozen=True)
class HITLDeliveryResult:
    payload: dict[str, Any]
    phase: HITLDeliveryPhase = HITLDeliveryPhase.REMOTE_RESPONSE

    def to_dict(self) -> dict[str, Any]:
        return {**self.payload, "delivery_phase": self.phase.value}


class HITLDeliveryError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        disposition: HITLDeliveryDisposition,
        phase: HITLDeliveryPhase,
        error_code: str,
    ) -> None:
        super().__init__(message)
        self.disposition = disposition
        self.phase = phase
        self.error_code = error_code


__all__ = [
    "HITLDeliveryDisposition",
    "HITLDeliveryError",
    "HITLDeliveryPhase",
    "HITLDeliveryResult",
]
