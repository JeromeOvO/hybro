# api/sse.py
import json
from datetime import datetime

from fastapi import APIRouter, Path
from fastapi.responses import StreamingResponse

from common.utils.logger import get_logger
from services.sse_services import sse_manager

logger = get_logger(__name__)
router = APIRouter()


@router.get("/sse/room/{room_id}/stream")
async def stream_room_messages(room_id: str = Path(..., description="room ID")):
    """
    create SSE message stream for specified room

    Args:
        room_id: room ID

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
                "timestamp": datetime.now().isoformat(),
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
async def get_room_sse_status(room_id: str = Path(..., description="room ID")):
    """
    get SSE connection status for specified room

    Args:
        room_id: room ID

    Returns:
        dict: room connection status information
    """
    return sse_manager.get_room_status(room_id)
