from __future__ import annotations

from typing import Any

from common.utils.a2a_helpers import sanitize_task_dict
from common.utils.logger import get_logger
from models.agent import Agent
from models.agent_group import AgentGroup
from models.memory import ChatContext, RoomMemory
from models.room import Room, RoomAgentMessage, RoomUserMessage

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


def _safe_parse_room(doc: dict | None) -> Room | None:
    if doc is None:
        return None
    try:
        return Room.model_validate(doc)
    except Exception:
        logger.warning("Invalid room document", exc_info=True)
        return None


def _safe_parse_room_memory(doc: dict | None) -> RoomMemory | None:
    if doc is None:
        return None
    try:
        return RoomMemory.model_validate(doc)
    except Exception:
        logger.warning("Invalid room memory document", exc_info=True)
        return None


def _safe_parse_agent_message(doc: dict | None) -> RoomAgentMessage | None:
    if doc is None:
        return None
    try:
        mc = doc.get("message_content")
        if mc and isinstance(mc, dict):
            task = mc.get("message_task")
            if task and isinstance(task, dict):
                sanitize_task_dict(task)
        return RoomAgentMessage.model_validate(doc)
    except Exception:
        logger.warning("Invalid room agent message document", exc_info=True)
        return None


def _strip_unset_task_tracking_fields(update_data: dict[str, Any]) -> dict[str, Any]:
    task_tracking_fields = {
        "webhook_token_hash",
        "pending_continuation",
        "last_notified_state",
        "agent_url",
        "task_created_at",
        "task_updated_at",
        "task_content",
    }
    for field in task_tracking_fields:
        if update_data.get(field) is None:
            update_data.pop(field, None)
    if update_data.get("has_task_tracking") is False:
        update_data.pop("has_task_tracking", None)
    return update_data


def _task_tracking_matches(
    doc: dict | None,
    *,
    webhook_token_hash: str,
    agent_url: str,
    task_data: dict,
) -> bool:
    if not doc:
        return False
    message_content = doc.get("message_content") or {}
    return (
        doc.get("has_task_tracking") is True
        and doc.get("webhook_token_hash") == webhook_token_hash
        and doc.get("agent_url") == agent_url
        and message_content.get("message_task") == task_data
    )


def _extract_text_from_artifact_parts(parts: list[dict]) -> str:
    chunks: list[str] = []
    for part in parts:
        root = part.get("root", part)
        if isinstance(root, dict) and isinstance(root.get("text"), str):
            chunks.append(root["text"])
    return "".join(chunks)


def _mongo_update_succeeded(result: Any) -> bool:
    if isinstance(result, bool):
        return result
    modified_count = getattr(result, "modified_count", None)
    upserted_id = getattr(result, "upserted_id", None)
    if modified_count is not None:
        return modified_count > 0 or upserted_id is not None
    return bool(result)


def _modified_count(result: Any) -> int:
    if isinstance(result, bool):
        return int(result)
    if isinstance(result, int):
        return result
    modified_count = getattr(result, "modified_count", None)
    if modified_count is not None:
        return int(modified_count)
    return int(bool(result))


def _safe_parse_user_message(doc: dict | None) -> RoomUserMessage | None:
    if doc is None:
        return None
    try:
        return RoomUserMessage.model_validate(doc)
    except Exception:
        logger.warning("Invalid room user message document", exc_info=True)
        return None


def _strip_file_urls(doc: dict) -> None:
    target = doc.get("$set", doc)
    content = target.get("message_content")
    if not content:
        return
    for attachment in content.get("attachments") or []:
        attachment.pop("file_url", None)


def _safe_parse_chat_context(doc: dict | None) -> ChatContext | None:
    if doc is None:
        return None
    try:
        return ChatContext.model_validate(doc)
    except Exception:
        logger.warning("Invalid chat context document", exc_info=True)
        return None
