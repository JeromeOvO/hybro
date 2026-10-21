from __future__ import annotations

from common.utils.logger import get_logger
from models.memory import RoomMemory

logger = get_logger(__name__)


def _safe_parse_room_memory(doc: dict | None) -> RoomMemory | None:
    if doc is None:
        return None
    try:
        return RoomMemory.model_validate(doc)
    except Exception:
        logger.warning("Invalid room memory document", exc_info=True)
        return None
