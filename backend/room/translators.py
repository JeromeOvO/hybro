from __future__ import annotations

from datetime import datetime
from typing import Any

from common.dto import (
    AgentMessageInput,
    RoomInfo,
    RoomMessageInfo,
    SavedUserMessage,
    UserMessageInput,
)
from room.membership import normalize_room_agent_set


def room_info_from_doc(doc: dict[str, Any]) -> RoomInfo:
    agent_set = normalize_room_agent_set(doc.get("room_agent_set"))
    return RoomInfo(
        room_id=str(doc["room_id"]),
        room_name=str(doc.get("room_name") or ""),
        owner_id=str(doc.get("room_owner_id") or doc.get("owner_id") or ""),
        owner_name=doc.get("room_owner_name") or doc.get("owner_name"),
        agent_ids=list(agent_set.keys()),
        agent_set=agent_set,
        membership_origin=_string_value(doc.get("membership_origin")) or "manual",
        membership_origin_status=(
            _string_value(doc.get("membership_origin_status")) or "manual"
        ),
        source_group_id=doc.get("source_group_id") or doc.get("applied_from_group"),
        source_group_name=doc.get("source_group_name"),
        created_at=doc.get("room_created_at") or doc.get("created_at"),
        processing_message_id=doc.get("processing_message_id"),
        extend_info=doc.get("extend_info"),
    )


def create_room_doc(
    *,
    room_id: str,
    owner_id: str,
    owner_name: str,
    room_name: str,
    agent_set: dict[str, str],
    created_at: datetime,
    membership_origin: str,
    membership_origin_status: str,
    source_group_id: str | None = None,
    source_group_name: str | None = None,
    processing_message_id: str | None = None,
    extend_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    doc = {
        "room_id": room_id,
        "room_name": room_name,
        "room_owner_id": owner_id,
        "room_owner_name": owner_name,
        "room_agent_set": dict(agent_set),
        "room_created_at": created_at,
        "membership_origin": membership_origin,
        "membership_origin_status": membership_origin_status,
        "source_group_id": source_group_id,
        "source_group_name": source_group_name,
        "processing_message_id": processing_message_id,
    }
    if extend_info is not None:
        doc["extend_info"] = dict(extend_info)
    return doc


def message_info_from_doc(doc: dict[str, Any]) -> RoomMessageInfo:
    content = doc.get("message_content") or doc.get("content") or {}
    metadata_keys = (
        "client_request_id",
        "extend_info",
        "scope_resolution_error",
        "step_number",
        "total_steps",
        "task_updated_at",
        "task_content",
        "has_task_tracking",
        "turn_id",
        "run_id",
        "agent_name",
        "was_successful",
        "success",
    )
    metadata = {key: doc[key] for key in metadata_keys if key in doc}
    return RoomMessageInfo(
        room_id=str(doc["room_id"]),
        message_id=str(doc["message_id"]),
        message_type=str(doc.get("message_type") or _infer_message_type(doc)),
        content=dict(content),
        created_at=doc.get("message_created_at") or doc.get("created_at"),
        sender_id=doc.get("user_id"),
        sender_name=doc.get("user_name"),
        agent_id=doc.get("agent_id"),
        parent_message_id=doc.get("parent_message_id") or doc.get("related_message_id"),
        metadata=metadata,
    )


def user_message_doc_from_input(
    *,
    room_id: str,
    message_id: str,
    message: UserMessageInput,
    created_at: datetime,
) -> dict[str, Any]:
    metadata = _plain(message.metadata or {})
    doc: dict[str, Any] = {
        "room_id": room_id,
        "message_id": message_id,
        "message_type": "user",
        "user_id": message.sender_id,
        "user_name": message.sender_name,
        "message_content": {"message_text": message.message_text},
        "message_created_at": created_at,
        "client_request_id": message.client_request_id,
    }
    if metadata:
        doc["extend_info"] = metadata
    if "scope_resolution_error" in metadata:
        doc["scope_resolution_error"] = metadata["scope_resolution_error"]
    if "attachments" in metadata:
        doc["message_content"]["attachments"] = metadata["attachments"]
    if "content_summary" in metadata:
        doc["message_content"]["content_summary"] = metadata["content_summary"]
    return doc


def agent_message_doc_from_input(
    *,
    room_id: str,
    message_id: str,
    message: AgentMessageInput,
    created_at: datetime,
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "room_id": room_id,
        "message_id": message_id,
        "message_type": "agent",
        "agent_id": message.agent_id,
        "message_content": dict(message.content or {}),
        "message_created_at": created_at,
    }
    if message.parent_message_id is not None:
        doc["related_message_id"] = message.parent_message_id
        doc["parent_message_id"] = message.parent_message_id
    for key, value in _plain(message.metadata or {}).items():
        doc[key] = value
    return doc


def saved_user_message_from_doc(doc: dict[str, Any]) -> SavedUserMessage:
    message_id = str(doc["message_id"])
    return SavedUserMessage(
        room_id=str(doc["room_id"]),
        message_id=message_id,
        dispatch_root_message_id=message_id,
        user_id=str(doc.get("user_id") or ""),
        user_name=str(doc.get("user_name") or ""),
        message=dict(doc),
        scope_resolution_error=doc.get("scope_resolution_error"),
    )


def _string_value(value: Any) -> str | None:
    enum_value = getattr(value, "value", value)
    return str(enum_value) if enum_value is not None else None


def _infer_message_type(doc: dict[str, Any]) -> str:
    return "agent" if doc.get("agent_id") else "user"


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    return value
