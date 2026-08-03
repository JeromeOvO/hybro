"""Stable request fingerprints for ``sendMessage`` persistence idempotency."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any

from common.dto import ExecutionRequest
from common.idempotency import (
    MAX_CLIENT_REQUEST_ID_LENGTH,
    normalize_client_request_id,
)

IDEMPOTENCY_FINGERPRINT_VERSION = 1

_QUOTE_FIELDS = (
    "text",
    "source_message_id",
    "source_kind",
    "sender_display_name",
    "source_agent_id",
)
_LEGACY_QUOTE_FIELDS = (
    "quoted_text",
    "quoted_sender_name",
)


def build_execution_request_fingerprint(request: ExecutionRequest) -> str:
    """Hash only fields that can change persisted-message or execution semantics."""

    canonical_json = json.dumps(
        execution_request_fingerprint_payload(request),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def execution_request_fingerprint_payload(
    request: ExecutionRequest,
) -> dict[str, Any]:
    """Build the version-1 canonical semantic payload for ``ExecutionRequest``.

    Server-generated message/quote IDs, timestamps, resolved attachment metadata,
    file URLs, and the authenticated sender's display name are intentionally absent.
    """

    message = _as_mapping(request.message)
    content = _as_mapping(message.get("message_content"))
    semantic_content = {
        "message_text": _plain_json(content.get("message_text")),
    }

    quote = _semantic_quote(message.get("quote"))
    extend_info = _as_mapping(message.get("extend_info"))
    legacy_quote = (
        {
            field: value
            for field in _LEGACY_QUOTE_FIELDS
            if isinstance((value := extend_info.get(field)), str)
        }
        if quote is None
        else {}
    )
    candidate_scope_mode = _effective_candidate_scope_mode(request)

    return {
        "room_id": request.room_id,
        "sender_id": request.sender_id,
        "message_content": semantic_content,
        "related_message_id": _optional_plain(message.get("related_message_id")),
        "parent_message_id": _optional_plain(message.get("parent_message_id")),
        "execution_parent_message_id": request.parent_message_id,
        "attachment_file_ids": _effective_attachment_file_ids(request, message),
        "quote": quote,
        "legacy_quote": legacy_quote or None,
        "mode": request.mode,
        "message_target_mode": request.message_target_mode,
        "target_group": request.target_group,
        "target_group_id": request.target_group_id,
        "mentioned_agent_ids": _normalized_id_set(
            request.mentioned_agent_ids,
            empty_as_none=True,
        ),
        "selected_agent_ids": _normalized_id_set(
            request.selected_agent_ids,
            empty_as_none=False,
        ),
        "candidate_scope_mode": candidate_scope_mode,
        "candidate_scope_group_id": (
            _normalized_optional_id(request.candidate_scope_group_id)
            if candidate_scope_mode == "saved_group"
            else None
        ),
    }


def _effective_candidate_scope_mode(request: ExecutionRequest) -> str:
    explicit = request.candidate_scope_mode
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    if request.selected_agent_ids is not None:
        return "explicit_selection"
    if request.target_group == "all_agents":
        return "all_agents"
    if request.target_group == "room_team":
        return "room_default"
    return "saved_group"


def _semantic_quote(value: Any) -> dict[str, Any] | None:
    quote = _as_mapping(value)
    if not quote:
        return None
    result = {field: _plain_json(quote.get(field)) for field in _QUOTE_FIELDS}
    if "text" in result and isinstance(result["text"], str):
        result["text"] = result["text"].strip()
    if "source_kind" not in result or not result["source_kind"]:
        result["source_kind"] = "unknown"
    return result


def _effective_attachment_file_ids(
    request: ExecutionRequest,
    message: Mapping[str, Any],
) -> list[str]:
    file_ids: list[str] = []
    seen: set[str] = set()

    def append(value: Any) -> None:
        if not isinstance(value, str) or value in seen:
            return
        seen.add(value)
        file_ids.append(value)

    for attachment in request.attachments or []:
        append(_as_mapping(attachment).get("file_id"))
    for file_id in request.inline_file_ids or []:
        append(file_id)

    content = _as_mapping(message.get("message_content"))
    raw_attachments = content.get("attachments")
    if isinstance(raw_attachments, Sequence) and not isinstance(
        raw_attachments, str | bytes
    ):
        for attachment in raw_attachments:
            append(_as_mapping(attachment).get("file_id"))
    return file_ids


def _normalized_id_set(
    value: Sequence[str] | None,
    *,
    empty_as_none: bool,
) -> list[str] | None:
    if value is None:
        return None
    normalized = sorted(
        {
            stripped
            for item in value
            if isinstance(item, str) and (stripped := item.strip())
        }
    )
    return None if empty_as_none and not normalized else normalized


def _normalized_optional_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _optional_plain(value: Any) -> Any:
    return None if value is None else _plain_json(value)


def _plain_json(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [_plain_json(item) for item in value]
    if isinstance(value, set | frozenset):
        return sorted(_plain_json(item) for item in value)
    if hasattr(value, "model_dump"):
        return _plain_json(value.model_dump(mode="json"))
    return value


__all__ = [
    "IDEMPOTENCY_FINGERPRINT_VERSION",
    "MAX_CLIENT_REQUEST_ID_LENGTH",
    "build_execution_request_fingerprint",
    "execution_request_fingerprint_payload",
    "normalize_client_request_id",
]
