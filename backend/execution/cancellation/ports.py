from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol


class CancellationMarkerRepositoryPort(Protocol):
    async def request(
        self,
        message_id: str,
        requested_by_user_id: str,
    ) -> bool: ...

    async def list_pending(
        self,
        *,
        limit: int = 100,
        after_message_id: str | None = None,
    ) -> list[dict[str, Any]]: ...

    async def mark_reconciled(self, message_id: str) -> bool: ...


class CancellationMessageReaderPort(Protocol):
    async def __call__(self, message_id: str) -> Any | None: ...


class CancellationReconciliationPort(Protocol):
    async def reconcile_pending(
        self,
        *,
        settle_cutoff: datetime,
        batch_size: int = 100,
    ) -> int: ...


__all__ = [
    "CancellationMarkerRepositoryPort",
    "CancellationMessageReaderPort",
    "CancellationReconciliationPort",
]
