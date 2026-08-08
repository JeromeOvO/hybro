from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class RunLifecycleWriteStatus(StrEnum):
    ACCEPTED = "accepted"
    CONFLICT = "conflict"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class RunLifecycleWriteOutcome:
    status: RunLifecycleWriteStatus
    payload: dict[str, Any] | None = None
    error_class: str | None = None
    error_message_size_bytes: int | None = None
    error_fingerprint: str | None = None

    @classmethod
    def accepted(cls, payload: dict[str, Any]) -> RunLifecycleWriteOutcome:
        return cls(status=RunLifecycleWriteStatus.ACCEPTED, payload=payload)

    @classmethod
    def conflict(cls) -> RunLifecycleWriteOutcome:
        return cls(status=RunLifecycleWriteStatus.CONFLICT)

    @classmethod
    def error(cls, exc: BaseException) -> RunLifecycleWriteOutcome:
        raw = str(exc).encode("utf-8", errors="replace")
        fingerprint = hashlib.sha256(
            exc.__class__.__name__.encode("utf-8") + b":" + raw
        ).hexdigest()
        return cls(
            status=RunLifecycleWriteStatus.ERROR,
            error_class=exc.__class__.__name__[:128],
            error_message_size_bytes=len(raw),
            error_fingerprint=fingerprint,
        )


class RunLifecycleWriteError(RuntimeError):
    def __init__(self, outcome: RunLifecycleWriteOutcome) -> None:
        self.outcome = outcome
        super().__init__(
            "run lifecycle persistence error "
            f"class={outcome.error_class or 'unknown'} "
            f"fingerprint={outcome.error_fingerprint or 'unknown'}"
        )


__all__ = [
    "RunLifecycleWriteError",
    "RunLifecycleWriteOutcome",
    "RunLifecycleWriteStatus",
]
