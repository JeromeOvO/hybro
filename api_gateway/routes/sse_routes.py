# api/sse.py
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.params import Depends as DependsParam
from fastapi.responses import StreamingResponse

from api_gateway.registry import mark_declared_owner as _mark_declared_owner
from app_shell.bound import SSEManagerRouteOwner
from common.auth import ClerkUser, get_current_user, get_current_user_with_query_token
from common.protocols import A2ATaskReader, ExecutionEngine
from common.utils.logger import get_logger
from common.utils.time import utcnow

logger = get_logger(__name__)
router = APIRouter()
execution_engine: ExecutionEngine | None = None
sse_store: A2ATaskReader | None = None
sse_manager: SSEManagerRouteOwner | None = None


def bind_sse_dependencies(
    store: A2ATaskReader,
    manager: SSEManagerRouteOwner,
) -> None:
    global sse_store, sse_manager

    sse_store = store
    sse_manager = manager


def bind_execution_deps(deps) -> None:
    global execution_engine
    execution_engine = deps.execution_engine


def _require_execution_engine() -> ExecutionEngine:
    if execution_engine is None:
        raise RuntimeError("ExecutionDeps have not been bound")
    return execution_engine


def get_execution_engine() -> ExecutionEngine:
    return _require_execution_engine()


def get_sse_store() -> A2ATaskReader:
    if sse_store is None:
        raise RuntimeError("SSE database dependency has not been bound")
    return sse_store


def get_sse_manager() -> SSEManagerRouteOwner:
    if sse_manager is None:
        raise RuntimeError("SSE manager dependency has not been bound")
    return sse_manager


def _resolve_dependency(value: Any, provider) -> Any:
    if isinstance(value, DependsParam):
        return provider()
    return value


@router.get("/sse/room/{room_id}/stream")
async def stream_room_messages(
    room_id: str = Path(..., description="room ID"),
    user: ClerkUser = Depends(get_current_user_with_query_token),
    manager: SSEManagerRouteOwner = Depends(get_sse_manager),
    db: A2ATaskReader = Depends(get_sse_store),
):
    """
    create SSE message stream for specified room

    Args:
        room_id: room ID
        user: Authenticated user (from header or query param token)

    Returns:
        StreamingResponse: SSE stream response
    """
    manager = _resolve_dependency(manager, get_sse_manager)
    if not isinstance(db, DependsParam):
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
            connection = await manager.add_connection(room_id)
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

                except Exception as e:
                    logger.error(f"Error in SSE stream for room {room_id}: {e}")
                    break

        except Exception as e:
            logger.error(f"SSE connection error for room {room_id}: {e}")

        finally:
            # clean up connection
            if connection:
                await manager.remove_connection(room_id, connection.connection_id)
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
    manager: SSEManagerRouteOwner = Depends(get_sse_manager),
    db: A2ATaskReader = Depends(get_sse_store),
):
    """
    get SSE connection status for specified room

    Args:
        room_id: room ID
        user: Authenticated user (from header or query param token)

    Returns:
        dict: room connection status information
    """
    manager = _resolve_dependency(manager, get_sse_manager)
    if not isinstance(db, DependsParam):
        room = await db.get_room_by_room_id(room_id)
        if not room:
            raise HTTPException(status_code=404, detail="Room not found")
        if room.room_owner_id != user.user_id:
            raise HTTPException(
                status_code=403,
                detail="You do not have permission to inspect this room",
            )
    return manager.get_room_status(room_id)


@router.post("/sse/message/{message_id}/cancel")
async def cancel_message(
    message_id: str = Path(..., description="Message ID to cancel"),
    user: ClerkUser = Depends(get_current_user),
    db: A2ATaskReader = Depends(get_sse_store),
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
        db = _resolve_dependency(db, get_sse_store)
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

        engine = _resolve_dependency(engine, get_execution_engine)
        success = await engine.cancel(
            room_id=message.room_id,
            message_id=message_id,
            requested_by_user_id=user.user_id,
        )
        if not success:
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


_mark_declared_owner(router, __name__)
