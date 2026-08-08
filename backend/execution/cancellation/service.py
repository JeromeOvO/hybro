from __future__ import annotations

from datetime import datetime

from common.dto import CancellationAck
from common.utils.logger import get_logger
from common.utils.time import ensure_utc
from execution.cancellation.finalizer import (
    CancellationFinalizationPort,
    CancellationFinalizationResult,
)
from execution.cancellation.ports import (
    CancellationMarkerRepositoryPort,
    CancellationMessageReaderPort,
)

logger = get_logger(__name__)


class CancellationService:
    """Own durable cancellation requests, finalization, and pending recovery."""

    def __init__(
        self,
        *,
        repository: CancellationMarkerRepositoryPort,
        finalizer: CancellationFinalizationPort,
        message_reader: CancellationMessageReaderPort,
    ) -> None:
        self._repository = repository
        self._finalizer = finalizer
        self._message_reader = message_reader

    async def request_cancellation(
        self,
        *,
        room_id: str,
        message_id: str,
        requested_by_user_id: str,
    ) -> bool | CancellationAck:
        persisted = await self._repository.request(
            message_id,
            requested_by_user_id,
        )
        if not persisted:
            return False
        try:
            result = await self.finalize(
                room_id=room_id,
                message_id=message_id,
            )
            if result.cancellation_applied:
                return True
            return CancellationAck(
                status=result.status.value,
                cancellation_applied=False,
                reconciled=result.reconciled,
            )
        except Exception:
            logger.warning(
                "cancellation_finalization_pending",
                extra={"room_id": room_id, "message_id": message_id},
                exc_info=True,
            )
            return CancellationAck(
                status="cancellation_pending",
                cancellation_applied=False,
                reconciled=False,
            )

    async def cancel(
        self,
        *,
        room_id: str,
        message_id: str,
        requested_by_user_id: str,
    ) -> bool | CancellationAck:
        return await self.request_cancellation(
            room_id=room_id,
            message_id=message_id,
            requested_by_user_id=requested_by_user_id,
        )

    async def finalize(
        self,
        *,
        room_id: str,
        message_id: str,
        settle_no_run: bool = False,
    ) -> CancellationFinalizationResult:
        return await self._finalizer.finalize(
            room_id=room_id,
            message_id=message_id,
            settle_no_run=settle_no_run,
        )

    @staticmethod
    def _is_settled(marker: dict, settle_cutoff: datetime) -> bool:
        cancelled_at = marker.get("cancelled_at")
        if isinstance(cancelled_at, str):
            cancelled_at = datetime.fromisoformat(cancelled_at)
        return isinstance(cancelled_at, datetime) and ensure_utc(
            cancelled_at
        ) <= ensure_utc(settle_cutoff)

    async def _reconcile_marker(
        self,
        marker: dict,
        *,
        settle_cutoff: datetime,
    ) -> bool:
        message_id = marker.get("message_id")
        if not isinstance(message_id, str) or not message_id:
            return False
        try:
            message = await self._message_reader(message_id)
            room_id = getattr(message, "room_id", None)
            if not isinstance(room_id, str) or not room_id:
                if not await self._repository.mark_reconciled(message_id):
                    raise RuntimeError("cancellation marker reconciliation failed")
                return True

            result = await self.finalize(
                room_id=room_id,
                message_id=message_id,
                settle_no_run=self._is_settled(marker, settle_cutoff),
            )
            return result.reconciled
        except Exception:
            # One bad marker must remain pending without starving later markers.
            logger.warning(
                "cancellation_marker_reconciliation_failed",
                extra={"message_id": message_id},
                exc_info=True,
            )
            return False

    async def reconcile_pending(
        self,
        *,
        settle_cutoff: datetime,
        batch_size: int = 100,
    ) -> int:
        """Reconcile all pending markers visible in a stable message-id scan."""
        if batch_size < 1:
            raise ValueError("batch_size must be positive")

        reconciled = 0
        after_message_id: str | None = None
        while True:
            # Repository scan failures intentionally propagate so the job cycle is
            # observable as failed rather than silently appearing successful.
            markers = await self._repository.list_pending(
                limit=batch_size,
                after_message_id=after_message_id,
            )
            if not markers:
                return reconciled

            for marker in markers:
                reconciled += await self._reconcile_marker(
                    marker,
                    settle_cutoff=settle_cutoff,
                )

            if len(markers) < batch_size:
                return reconciled
            last_message_id = markers[-1].get("message_id")
            if not isinstance(last_message_id, str) or not last_message_id:
                return reconciled
            after_message_id = last_message_id


__all__ = ["CancellationService"]
