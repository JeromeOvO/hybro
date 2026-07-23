from __future__ import annotations

import asyncio
import contextlib
from typing import Any


class OwnershipLeaseMaintainer:
    def __init__(
        self,
        *,
        task_runner,
        ownership_store,
        worker_id: str,
        interval_seconds: float = 30.0,
    ) -> None:
        self._task_runner = task_runner
        self._ownership_store = ownership_store
        self._worker_id = worker_id
        self._interval_seconds = interval_seconds
        self._running = False
        self._task = None
        self._tracked: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        try:
            self._task = self._task_runner(
                self._run(),
                name="hub-ownership-lease-maintainer",
            )
        except TypeError:
            self._task = self._task_runner(self._run())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def track(self, aliases: dict, lease_token: str | None = None) -> None:
        clean_aliases = {
            key: value
            for key, value in aliases.items()
            if isinstance(value, str) and value
        }
        if not clean_aliases:
            return
        primary_alias = next(iter(clean_aliases.values()))
        self._tracked[primary_alias] = {
            "aliases": clean_aliases,
            "lease_token": lease_token,
        }

    async def release(self, alias: str) -> None:
        self._tracked.pop(alias, None)
        for primary, record in list(self._tracked.items()):
            if alias in record["aliases"].values():
                self._tracked.pop(primary, None)
        await self._ownership_store.release(alias, owner_id=self._worker_id)

    async def renew_once(self) -> None:
        for primary, record in list(self._tracked.items()):
            aliases = dict(record["aliases"])
            try:
                refreshed = await self._ownership_store.claim_or_refresh(
                    aliases,
                    self._worker_id,
                    record.get("lease_token"),
                )
            except ValueError:
                self._tracked.pop(primary, None)
                continue
            lease_token = refreshed.get("lease_token", record.get("lease_token"))
            self.track(refreshed.get("aliases") or aliases, lease_token)

    async def _run(self) -> None:
        while self._running:
            await asyncio.sleep(self._interval_seconds)
            await self.renew_once()


__all__ = ["OwnershipLeaseMaintainer"]
