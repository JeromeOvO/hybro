from __future__ import annotations

from typing import Any

from common.dto import RunInfo
from common.protocols import RunRepository
from common.utils.logger import get_logger

logger = get_logger(__name__)


def run_doc_to_run_info(doc: dict[str, Any]) -> RunInfo:
    return RunInfo(
        run_id=str(doc.get("run_id") or ""),
        room_id=str(doc.get("room_id") or ""),
        state=str(doc.get("state") or ""),
        trigger_message_id=doc.get("trigger_message_id"),
        agent_id=doc.get("agent_id"),
        parent_run_id=doc.get("parent_run_id"),
        seq=int(doc.get("seq") or 0),
        error_code=doc.get("error_code"),
        created_at=doc.get("created_at"),
        updated_at=doc.get("updated_at"),
        error=doc.get("error") or doc.get("error_message"),
    )


class RunQueryAdapter:
    def __init__(self, run_repository: RunRepository) -> None:
        self._run_repository = run_repository

    async def get_run(self, run_id: str) -> RunInfo | None:
        try:
            doc = await self._run_repository.find_one({"run_id": run_id})
        except Exception:
            logger.warning("run lookup failed for run_id=%s", run_id, exc_info=True)
            return None
        return run_doc_to_run_info(doc) if doc else None

    async def get_run_strict(self, run_id: str) -> RunInfo | None:
        doc = await self._run_repository.find_one({"run_id": run_id})
        return run_doc_to_run_info(doc) if doc else None

    async def get_runs_for_room(self, room_id: str) -> list[RunInfo]:
        try:
            docs = await self._run_repository.get_active_for_room(room_id)
            return [run_doc_to_run_info(doc) for doc in docs]
        except Exception:
            logger.warning(
                "active-run lookup failed for room_id=%s", room_id, exc_info=True
            )
            return []

    async def get_latest_runs_for_rooms(
        self, room_ids: list[str]
    ) -> dict[str, RunInfo]:
        try:
            docs = await self._run_repository.get_latest_for_rooms(room_ids)
        except Exception:
            logger.warning("bulk latest-run lookup failed", exc_info=True)
            return {}
        return {
            info.room_id: info
            for doc in docs
            if (info := run_doc_to_run_info(doc)).room_id
        }
