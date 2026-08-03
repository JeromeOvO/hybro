# api/sse.py
import json

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import StreamingResponse

from api_gateway.dependencies import (
    get_execution_engine,
    get_sse_store,
    get_sse_transport,
)
from api_gateway.registry import mark_declared_owner as _mark_declared_owner
from common.auth import ClerkUser, get_current_user, get_current_user_with_query_token
from common.dto import CancellationAck
from common.protocols import ExecutionEngine, SSERouteTransport, SSEStateReader
from common.utils.logger import get_logger
from common.utils.time import utcnow

logger = get_logger(__name__)
router = APIRouter()

_PUBLIC_TERMINAL_ORCHESTRATION_STATUS = {
    "completed": "completed",
    "failed": "failed",
    "canceled": "canceled",
    "budget_exhausted": "failed",
}


@router.get("/sse/room/{room_id}/stream")
async def stream_room_messages(
    room_id: str = Path(..., description="room ID"),
    user: ClerkUser = Depends(get_current_user_with_query_token),
    transport: SSERouteTransport = Depends(get_sse_transport),
    db: SSEStateReader = Depends(get_sse_store),
):
    """
    create SSE message stream for specified room

    Args:
        room_id: room ID
        user: Authenticated user (from header or query param token)

    Returns:
        StreamingResponse: SSE stream response
    """
    room = await db.get_room_by_room_id(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    if room.room_owner_id != user.user_id:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to stream this room",
        )

    async def event_generator():
        connection = None
        try:
            # create SSE connection
            connection = await transport.add_connection(room_id)
            logger.info(f"SSE stream started for room {room_id}")

            # send connected message
            connected_message = {
                "type": "connected",
                "room_id": room_id,
                "timestamp": utcnow().isoformat(),
                "data": {"connection_id": connection.connection_id},
            }
            yield f"data: {json.dumps(connected_message)}\n\n"

            while connection.is_active:
                try:
                    # get next message
                    message = await connection.get_message(timeout=30.0)
                    if message:
                        yield f"data: {message}\n\n"

                except Exception as exc:
                    logger.error(
                        "sse_stream_failed",
                        extra={
                            "room_id": room_id,
                            "error_type": type(exc).__name__,
                        },
                    )
                    break

        except Exception as exc:
            logger.error(
                "sse_connection_failed",
                extra={"room_id": room_id, "error_type": type(exc).__name__},
            )

        finally:
            # clean up connection
            if connection:
                await transport.remove_connection(room_id, connection.connection_id)
                logger.info(f"SSE stream closed for room {room_id}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "text/event-stream",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/sse/room/{room_id}/status")
async def get_room_sse_status(
    room_id: str = Path(..., description="room ID"),
    user: ClerkUser = Depends(get_current_user_with_query_token),
    transport: SSERouteTransport = Depends(get_sse_transport),
    db: SSEStateReader = Depends(get_sse_store),
):
    """
    get SSE connection status for specified room

    Args:
        room_id: room ID
        user: Authenticated user (from header or query param token)

    Returns:
        dict: room connection status information
    """
    room = await db.get_room_by_room_id(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    if room.room_owner_id != user.user_id:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to inspect this room",
        )
    return transport.get_room_status(room_id)


@router.post("/sse/message/{message_id}/cancel")
async def cancel_message(
    message_id: str = Path(..., description="Message ID to cancel"),
    user: ClerkUser = Depends(get_current_user),
    db: SSEStateReader = Depends(get_sse_store),
    engine: ExecutionEngine = Depends(get_execution_engine),
):
    """
    Cancel an ongoing message processing workflow.

    This marks the message as cancelled, which will be detected at the next
    checkpoint in the workflow, causing it to stop gracefully.

    Args:
        message_id: The ID of the message to cancel
        user: Authenticated user

    Returns:
        dict: Success status and message
    """
    try:
        # Verify the message exists and the user owns the room it belongs to
        message = await db.get_room_user_message_by_message_id(message_id)
        if not message:
            raise HTTPException(status_code=404, detail="Message not found")

        room = await db.get_room_by_room_id(message.room_id)
        if not room:
            raise HTTPException(status_code=404, detail="Room not found")

        if room.room_owner_id != user.user_id:
            raise HTTPException(
                status_code=403,
                detail="You do not have permission to cancel this message",
            )

        extend_info = getattr(message, "extend_info", None)
        persisted_status = (
            extend_info.get("orchestration_status")
            if isinstance(extend_info, dict)
            else None
        )
        terminal_status = _PUBLIC_TERMINAL_ORCHESTRATION_STATUS.get(persisted_status)
        if terminal_status is not None and terminal_status != "canceled":
            return {
                "success": True,
                "message_id": message_id,
                "message": "Message processing had already finished",
                "status": terminal_status,
                "outcome": "already_terminal",
            }

        success = await engine.cancel(
            room_id=message.room_id,
            message_id=message_id,
            requested_by_user_id=user.user_id,
        )
        if not success:
            raise HTTPException(
                status_code=500, detail="Failed to persist cancellation to database"
            )
        if isinstance(success, CancellationAck) and not success.reconciled:
            return {
                "success": True,
                "message_id": message_id,
                "message": "Message cancellation accepted and pending reconciliation",
                "status": success.status,
                "outcome": "pending_reconciliation",
            }
        if isinstance(success, CancellationAck) and not success.cancellation_applied:
            return {
                "success": True,
                "message_id": message_id,
                "message": "Message processing had already finished",
                "status": _PUBLIC_TERMINAL_ORCHESTRATION_STATUS.get(
                    success.status,
                    success.status,
                ),
                "outcome": "already_terminal",
            }

        logger.info(f"Message {message_id} cancelled by user {user.user_id}")

        return {
            "success": True,
            "message_id": message_id,
            "message": "Message cancellation requested",
            "status": "canceled",
            "outcome": "canceled",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "message_cancellation_failed",
            extra={"message_id": message_id, "error_type": type(e).__name__},
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to cancel message: {str(e)}"
        ) from e


_mark_declared_owner(router, __name__)
