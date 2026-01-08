# api/sse.py
import json

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import StreamingResponse

from common.auth import ClerkUser, get_current_user, get_current_user_with_query_token
from common.utils.logger import get_logger
from common.utils.time import utcnow
from database.mongodb import mongodb
from services.sse_services import sse_manager

logger = get_logger(__name__)
router = APIRouter()


@router.get("/sse/room/{room_id}/stream")
async def stream_room_messages(
    room_id: str = Path(..., description="room ID"),
    user: ClerkUser = Depends(get_current_user_with_query_token),
):
    """
    create SSE message stream for specified room

    Args:
        room_id: room ID
        user: Authenticated user (from header or query param token)

    Returns:
        StreamingResponse: SSE stream response
    """

    async def event_generator():
        connection = None
        try:
            # create SSE connection
            connection = await sse_manager.add_connection(room_id)
            logger.info(f"SSE stream started for room {room_id}")

            # send connected message
            connected_message = {
                "type": "connected",
                "room_id": room_id,
                "connection_id": connection.connection_id,
                "timestamp": utcnow().isoformat(),
            }
            yield f"data: {json.dumps(connected_message)}\n\n"

            while connection.is_active:
                try:
                    # get next message
                    message = await connection.get_message(timeout=30.0)
                    if message:
                        yield f"data: {message}\n\n"

                except Exception as e:
                    logger.error(f"Error in SSE stream for room {room_id}: {e}")
                    break

        except Exception as e:
            logger.error(f"SSE connection error for room {room_id}: {e}")

        finally:
            # clean up connection
            if connection:
                await sse_manager.remove_connection(room_id, connection.connection_id)
                logger.info(f"SSE stream closed for room {room_id}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "text/event-stream",
        },
    )


@router.get("/sse/room/{room_id}/status")
async def get_room_sse_status(
    room_id: str = Path(..., description="room ID"),
    user: ClerkUser = Depends(get_current_user_with_query_token),
):
    """
    get SSE connection status for specified room

    Args:
        room_id: room ID
        user: Authenticated user (from header or query param token)

    Returns:
        dict: room connection status information
    """
    return sse_manager.get_room_status(room_id)


@router.post("/sse/message/{message_id}/cancel")
async def cancel_message(
    message_id: str = Path(..., description="Message ID to cancel"),
    user: ClerkUser = Depends(get_current_user),
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
        # Add to local cache immediately (for same instance)
        sse_manager.cancel_message(message_id)

        # Persist to MongoDB (will trigger change stream for other instances)
        success = await mongodb.cancel_message(message_id, user.user_id)

        if not success:
            sse_manager.clear_cancellation(message_id)
            raise HTTPException(
                status_code=500, detail="Failed to persist cancellation to database"
            )

        logger.info(f"Message {message_id} cancelled by user {user.user_id}")

        return {
            "success": True,
            "message_id": message_id,
            "message": "Message cancellation requested",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling message {message_id}: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to cancel message: {str(e)}"
        ) from e
