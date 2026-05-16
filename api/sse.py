# api/sse.py
import json

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import StreamingResponse

from common.auth import ClerkUser, get_current_user, get_current_user_with_query_token
from common.utils.logger import get_logger
from common.utils.time import utcnow
from database.mongodb import mongodb
from services.database_service import db_service
from services.run_lifecycle_service import record_and_maybe_broadcast_run_event
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
            "X-Accel-Buffering": "no",
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
        # Verify the message exists and the user owns the room it belongs to
        message = await db_service.get_room_user_message_by_message_id(message_id)
        if not message:
            raise HTTPException(status_code=404, detail="Message not found")

        room = await db_service.get_room_by_room_id(message.room_id)
        if not room:
            raise HTTPException(status_code=404, detail="Room not found")

        if room.room_owner_id != user.user_id:
            raise HTTPException(
                status_code=403,
                detail="You do not have permission to cancel this message",
            )

        # Cancel locally + broadcast to other instances via broker
        await sse_manager.cancel_message_and_broadcast(message_id)

        # Cancel any pending HITL requests associated with this message
        from services.hitl_service import hitl_service
        await hitl_service.cancel_requests_for_message(message_id)

        # Persist to MongoDB (will trigger change stream for other instances)
        success = await mongodb.cancel_message(message_id, user.user_id)

        if not success:
            sse_manager.clear_cancellation(message_id)
            raise HTTPException(
                status_code=500, detail="Failed to persist cancellation to database"
            )

        logger.info(f"Message {message_id} cancelled by user {user.user_id}")

        # Clear room processing status for the user message (must use user message ID)
        await record_and_maybe_broadcast_run_event(
            message.room_id,
            "canceled",
            message_id,
            sse=sse_manager,
        )
        await sse_manager.send_processing_status(
            message.room_id, "canceled", message_id
        )

        # Phase 7a: root cancellation lifecycle/frontend clear is complete above.
        # Paused-agent DB task-state updates, task notifications, and remote
        # cancels are separate best-effort cleanup; failures here must not
        # block the root cancellation result.
        try:
            from a2a.types import TaskState
            from services.a2a_service import a2a_service
            from services.task_notification_service import notify_task_update

            agent_msgs = await db_service.get_room_agent_messages_by_related_message_id(
                message_id
            )
            for agent_msg in agent_msgs:
                if not agent_msg.has_task_tracking:
                    continue
                # Update task state in DB to canceled (terminal)
                await db_service.update_task_state_on_message(
                    agent_msg.message_id,
                    TaskState.canceled.value,
                    message_text="Task was canceled",
                )
                # Notify frontend via SSE (no need for send_processing_status
                # since we already cleared it above with the user message ID)
                await notify_task_update(
                    message_id=agent_msg.message_id,
                    state=TaskState.canceled,
                    room_id=agent_msg.room_id,
                    user_id=agent_msg.user_id or "",
                )
                # Best-effort: tell remote agent to stop processing
                if agent_msg.agent_url:
                    task = (
                        agent_msg.message_content.message_task
                        if agent_msg.message_content
                        else None
                    )
                    if task and task.id:
                        try:
                            agent_card = await a2a_service.get_agent_card_from_url(
                                agent_msg.agent_url
                            )
                            await a2a_service.cancel_remote_task(agent_card, task.id)
                            logger.info(
                                "Sent remote cancel for task %s (agent %s)",
                                task.id,
                                agent_msg.agent_url,
                            )
                        except Exception as cancel_err:
                            logger.debug(
                                "Remote cancel failed for task %s: %s (best-effort)",
                                task.id,
                                cancel_err,
                            )
        except Exception as e:
            logger.debug("Failed to cancel agent tasks: %s (best-effort)", e)

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
