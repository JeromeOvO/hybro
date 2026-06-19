from __future__ import annotations

from typing import Any
import logging
from common.config.settings import settings

logger = logging.getLogger(__name__)


class HubInternalResponseRouter:
    def __init__(
        self,
        *,
        sink: Any,
        journal: Any | None = None,
        ownership_store: Any | None = None,
        worker_id: str = "local-worker",
    ) -> None:
        self._sink = sink
        self._journal = journal
        self._ownership_store = ownership_store
        self._worker_id = worker_id

    async def dispatch_hub_internal_response(self, event: Any) -> None:
        if not await self._owns_response(event):
            return
        claim = None
        journal_id = getattr(event, "journal_id", None)
        if self._journal and journal_id:
            event_claim_token = getattr(event, "claim_token", None)
            if event_claim_token:
                claim = {"claim_token": event_claim_token}
            else:
                claim = await self._journal.claim_for_processing(
                    journal_id, self._worker_id
                )
            if claim is None:
                return
        try:
            await self._sink.handle_hub_agent_response(event)
        except Exception:
            releaser = getattr(self._journal, "release_claim", None)
            if releaser is not None and journal_id and claim:
                await releaser(
                    journal_id,
                    claim_token=claim.get("claim_token") if claim else None,
                )
            raise
        if self._journal and journal_id:
            await self._journal.mark_processed(
                journal_id, claim_token=claim.get("claim_token") if claim else None
            )

    async def _owns_response(self, event: Any) -> bool:
        if self._ownership_store is None:
            return True
        for alias in _response_aliases(event):
            owner = await self._ownership_store.resolve_owner(alias)
            if owner is None:
                continue
            if owner.get("owner_id") != self._worker_id:
                if settings.app_env == "development":
                    logger.warning(
                        "Bypassing ownership check in development: owner_id %s != worker_id %s",
                        owner.get("owner_id"), self._worker_id
                    )
                    return True
                return False
            return True
        return True


def _response_aliases(event: Any) -> list[str]:
    payload = getattr(event, "payload", {}) or {}
    aliases = [
        getattr(event, "task_id", None),
        payload.get("task_id") if isinstance(payload, dict) else None,
        payload.get("message_id") if isinstance(payload, dict) else None,
    ]
    return list(dict.fromkeys(alias for alias in aliases if isinstance(alias, str) and alias))


__all__ = ["HubInternalResponseRouter"]
