from __future__ import annotations

from common.dto import ExecutionAck
from models.response import RoomCenterUserMessageResponse


def room_response_to_execution_ack(
    response: RoomCenterUserMessageResponse,
) -> ExecutionAck:
    return ExecutionAck(
        room_id=response.room_id,
        message_id=response.message_id,
        dispatch_root_message_id=response.dispatch_root_message_id,
        user_id=response.user_id,
        user_name=response.user_name,
        message=response.message.model_dump(mode="json") if response.message else None,
        message_list=(
            [message.model_dump(mode="json") for message in response.message_list]
            if response.message_list is not None
            else None
        ),
        scope_resolution_error=(
            response.scope_resolution_error.model_dump(mode="json")
            if response.scope_resolution_error
            else None
        ),
        success=response.success,
        error=response.error,
        status_code=response.status_code,
    )


__all__ = ["room_response_to_execution_ack"]
