from __future__ import annotations

from typing import Any

from common.dto import RunInfo
from common.utils.logger import get_logger
from models.run import NON_TERMINAL_RUN_STATE_VALUES

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
    def __init__(self, runs_collection) -> None:
        self._runs_collection = runs_collection

    async def get_run(self, run_id: str) -> RunInfo | None:
        try:
            doc = await self._runs_collection.find_one({"run_id": run_id})
        except Exception:
            logger.warning("run lookup failed for run_id=%s", run_id, exc_info=True)
            return None
        return run_doc_to_run_info(doc) if doc else None

    async def get_runs_for_room(self, room_id: str) -> list[RunInfo]:
        try:
            cursor = self._runs_collection.find(
                {
                    "room_id": room_id,
                    "state": {"$in": list(NON_TERMINAL_RUN_STATE_VALUES)},
                }
            ).sort("updated_at", -1)
            docs = await cursor.to_list(length=None)
            return [run_doc_to_run_info(doc) for doc in docs]
        except Exception:
            logger.warning("active-run lookup failed for room_id=%s", room_id, exc_info=True)
            return []
