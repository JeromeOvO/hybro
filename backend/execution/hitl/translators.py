from __future__ import annotations

from typing import Any

from common.dto import (
    HITLRequest as CommonHITLRequest,
)
from common.dto import (
    HITLResponse as CommonHITLResponse,
)


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def model_hitl_request_to_common(request: Any) -> CommonHITLRequest:
    message_id = (
        getattr(request, "display_message_id", None)
        or getattr(request, "continuation_message_id", None)
        or getattr(request, "user_message_id", None)
    )
    return CommonHITLRequest(
        request_id=request.request_id,
        room_id=request.room_id,
        user_message_id=request.user_message_id,
        message_id=message_id,
        source=_enum_value(request.source),
        prompt=request.prompt,
        prompt_type=_enum_value(getattr(request, "prompt_type", "text")),
        choices=getattr(request, "choices", None),
        agent_id=getattr(request, "agent_id", None),
        agent_name=getattr(request, "agent_name", None),
        source_step_id=getattr(request, "source_step_id", None),
        a2a_task_id=getattr(request, "a2a_task_id", None),
        a2a_context_id=getattr(request, "a2a_context_id", None),
        continuation_message_id=getattr(request, "continuation_message_id", None),
        display_message_id=getattr(request, "display_message_id", None),
        client_request_id=getattr(request, "client_request_id", None),
        group_id=getattr(request, "group_id", None),
        group_total=getattr(request, "group_total", None),
        group_index=getattr(request, "group_index", None),
        status=_enum_value(getattr(request, "status", "pending")),
        expires_at=getattr(request, "expires_at", None),
        created_at=getattr(request, "created_at", None),
        user_input=getattr(request, "user_input", None),
        responded_at=getattr(request, "responded_at", None),
        responded_by_user_id=getattr(request, "responded_by_user_id", None),
    )


def hitl_response_dict_to_common(result: dict[str, Any]) -> CommonHITLResponse:
    return CommonHITLResponse(
        request_id=str(result["request_id"]),
        status=str(result.get("status") or "ok"),
        response_text=result.get("response_text"),
        responder_id=result.get("responder_id"),
        resolved_at=result.get("resolved_at"),
        reclaimed=result.get("reclaimed"),
        error=result.get("error"),
    )


def hitl_cancel_none_to_success(result: None) -> bool:
    return True


__all__ = [
    "hitl_cancel_none_to_success",
    "hitl_response_dict_to_common",
    "model_hitl_request_to_common",
]
