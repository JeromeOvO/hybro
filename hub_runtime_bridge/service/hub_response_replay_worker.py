from __future__ import annotations

import asyncio
import contextlib

from hub_runtime_bridge.service.hub_publish import internal_event_from_journal_claim


class HubResponseReplayWorker:
    def __init__(
        self,
        *,
        journal,
        dispatcher,
        worker_id: str,
        batch_size: int = 100,
        interval_seconds: int = 5,
        task_runner=None,
        ownership_store=None,
    ) -> None:
        self._journal = journal
        self._dispatcher = dispatcher
        self._worker_id = worker_id
        self._batch_size = batch_size
        self._interval_seconds = interval_seconds
        self._task_runner = task_runner
        self._ownership_store = ownership_store
        self._task = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await self.replay_once()
        if self._task_runner is not None:
            self._task = self._task_runner(self._run())

    async def stop(self) -> None:
        self._running = False
        task = self._task
        self._task = None
        cancel = getattr(task, "cancel", None)
        if callable(cancel):
            cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def replay_once(self) -> int:
        count = 0
        for record in await self._journal.find_replayable(self._batch_size):
            if not await self._owns_record(record):
                continue
            claim = await self._journal.claim_for_processing(
                record["journal_id"], self._worker_id
            )
            if claim:
                await self._dispatcher.dispatch_hub_internal_response(
                    internal_event_from_journal_claim(claim)
                )
                count += 1
        return count

    async def _run(self) -> None:
        while self._running:
            await asyncio.sleep(self._interval_seconds)
            with contextlib.suppress(Exception):
                await self.replay_once()

    async def _owns_record(self, record: dict) -> bool:
        if self._ownership_store is None:
            return True
        for alias in _record_aliases(record):
            owner = await self._ownership_store.resolve_owner(alias)
            if owner is None:
                continue
            return owner.get("owner_id") == self._worker_id
        return True


def _record_aliases(record: dict) -> list[str]:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    aliases = [
        record.get("task_id"),
        record.get("agent_message_id"),
        payload.get("task_id"),
        payload.get("message_id"),
    ]
    return list(dict.fromkeys(alias for alias in aliases if isinstance(alias, str)))


__all__ = ["HubResponseReplayWorker"]
