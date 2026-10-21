from __future__ import annotations

from common.utils.logger import get_logger
from models.agent import Agent
from models.agent_group import AgentGroup
from models.memory import RoomMemory

logger = get_logger(__name__)


def _safe_parse_agent_group(doc: dict | None) -> AgentGroup | None:
    if doc is None:
        return None
    try:
        return AgentGroup.model_validate(doc)
    except Exception:
        logger.warning("Invalid agent group document", exc_info=True)
        return None


def _safe_parse_agent(doc: dict | None) -> Agent | None:
    if doc is None:
        return None
    try:
        return Agent.model_validate(doc)
    except Exception:
        logger.warning("Invalid agent document", exc_info=True)
        return None


def _safe_parse_room_memory(doc: dict | None) -> RoomMemory | None:
    if doc is None:
        return None
    try:
        return RoomMemory.model_validate(doc)
    except Exception:
        logger.warning("Invalid room memory document", exc_info=True)
        return None
