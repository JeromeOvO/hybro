import asyncio
import json
from enum import Enum
from typing import Any
from uuid import uuid4

from cachetools import TTLCache

from common.utils.logger import get_logger
from common.utils.time import utcnow
from services.a2a_constants import PROCESSING_DONE_STATUSES, SSEProcessingStatus
from services.database_service import db_service


def _enum_value(v: Any) -> Any:
    """Extract the .value from an Enum member (e.g. TaskState), or return as-is."""
    return v.value if isinstance(v, Enum) else v

logger = get_logger(__name__)


class SSEConnection:
    def __init__(self, room_id: str):
        self.room_id = room_id
        self.connection_id = str(uuid4())
        self.queue: asyncio.Queue = asyncio.Queue()
        self.connected_at = utcnow()
        self.is_active = True

    async def send_message(self, message_type: str, data: Any):
        """send message to connection"""
        if not self.is_active:
            return False

        try:
            message = {
                "type": message_type,
                "timestamp": utcnow().isoformat(),
                "room_id": self.room_id,
                "data": data,
            }
            await self.queue.put(json.dumps(message))
            return True
        except Exception as e:
            logger.error(
                f"Failed to send message to connection {self.connection_id}: {e}"
            )
            self.is_active = False
            return False

    async def get_message(self, timeout: float = 30.0) -> str | None:
        """get message from queue"""
        try:
            message = await asyncio.wait_for(self.queue.get(), timeout=timeout)
            return message
        except TimeoutError:
            # send heartbeat
            heartbeat = {
                "type": "heartbeat",
                "timestamp": utcnow().isoformat(),
                "room_id": self.room_id,
            }
            await self.queue.put(json.dumps(heartbeat))
            return json.dumps(heartbeat)

    def close(self):
        """close the connection"""
        self.is_active = False


class SSEManager:
    def __init__(self):
        # room_id -> {connection_id: connection}
        self.room_connections: dict[str, dict[str, SSEConnection]] = {}
        self.lock = asyncio.Lock()

        # Message cancellation tracking — TTL cache auto-evicts entries that
        # are never cleared by a processing checkpoint (e.g. message already
        # finished or was never started).  maxsize is generous; TTL is the
        # primary eviction mechanism.
        self.cancelled_messages: TTLCache[str, bool] = TTLCache(
            maxsize=10_000, ttl=3600
        )
        self._db_collection = None
        self._change_stream_task = None
        self._shutdown_flag = False

    async def add_connection(self, room_id: str) -> SSEConnection:
        """add connection"""
        async with self.lock:
            if room_id not in self.room_connections:
                self.room_connections[room_id] = {}

            connection = SSEConnection(room_id)
            self.room_connections[room_id][connection.connection_id] = connection

            logger.info(
                f"SSE connection {connection.connection_id} added to room {room_id}"
            )
            return connection

    async def remove_connection(self, room_id: str, connection_id: str):
        """remove connection"""
        async with self.lock:
            if (
                room_id in self.room_connections
                and connection_id in self.room_connections[room_id]
            ):
                connection = self.room_connections[room_id][connection_id]
                connection.close()
                del self.room_connections[room_id][connection_id]

                if not self.room_connections[room_id]:
                    del self.room_connections[room_id]

                logger.info(
                    f"SSE connection {connection_id} removed from room {room_id}"
                )

    async def broadcast_to_room(self, room_id: str, message_type: str, data: Any):
        """broadcast message to room"""
        async with self.lock:
            if room_id not in self.room_connections:
                logger.warning(f"SSE broadcast [{message_type}] - NO connections for room {room_id}, event DROPPED!")
                return

            disconnected_connections = []

            for connection_id, connection in self.room_connections[room_id].items():
                success = await connection.send_message(message_type, data)
                if not success:
                    disconnected_connections.append(connection_id)

            # clean up disconnected connections
            for connection_id in disconnected_connections:
                if connection_id in self.room_connections[room_id]:
                    del self.room_connections[room_id][connection_id]

            active_connections = len(self.room_connections[room_id])
            logger.info(
                f"SSE broadcast [{message_type}] to {active_connections} connection(s) in room {room_id}"
            )

    async def send_user_message(
        self, room_id: str, message_id: str, user_id: str, content: str
    ):
        """send user message"""
        data = {
            "message_id": message_id,
            "user_id": user_id,
            "content": content,
            "timestamp": utcnow().isoformat(),
        }
        await self.broadcast_to_room(room_id, "user_message", data)

    async def send_agent_response(
        self,
        room_id: str,
        message_id: str,
        agent_id: str,
        content: str,
        related_message_id: str = None,
    ):
        """send agent response"""
        data = {
            "message_id": message_id,
            "agent_id": agent_id,
            "content": content,
            "related_message_id": related_message_id,
            "timestamp": utcnow().isoformat(),
        }
        await self.broadcast_to_room(room_id, "agent_response", data)

    async def send_agent_token(
        self, room_id: str, message_id: str, agent_id: str, token: str
    ):
        """
        Send incremental token from agent streaming response.

        This is for real-time token-by-token streaming from agents.
        Tokens are sent as they arrive from the agent, enabling
        real-time display in the frontend.

        Args:
            room_id: The room ID
            message_id: The message being generated
            agent_id: The agent sending the token
            token: The incremental text token (word, character, etc.)
        """
        data = {
            "message_id": message_id,
            "agent_id": agent_id,
            "token": token,
            "timestamp": utcnow().isoformat(),
        }
        await self.broadcast_to_room(room_id, "agent_token", data)

    async def send_error(self, room_id: str, error: str, message_id: str = None):
        """
        Send error event to room.

        Args:
            room_id: The room ID
            error: Error message
            message_id: Optional message ID related to the error
        """
        data = {
            "error": error,
            "message_id": message_id,
            "timestamp": utcnow().isoformat(),
        }
        await self.broadcast_to_room(room_id, "error", data)

    async def send_rate_limit_error(
        self,
        room_id: str,
        message_id: str,
        agent_id: str,
        reason: str,
        retry_after_seconds: int | None = None,
        user_requests_used: int = 0,
        user_requests_limit: int | None = None,
        system_requests_used: int = 0,
        system_requests_limit: int | None = None,
    ):
        """
        Send rate limit error event to room with detailed information.

        Args:
            room_id: The room ID
            message_id: The message ID that triggered the rate limit
            agent_id: The agent ID that was rate limited
            reason: Human-readable error message
            retry_after_seconds: Seconds until the user can retry
            user_requests_used: Number of requests made by this user
            user_requests_limit: Maximum requests allowed per user
            system_requests_used: Total requests to this agent
            system_requests_limit: Maximum total requests allowed
        """
        data = {
            "error": reason,
            "error_type": "rate_limit_exceeded",
            "message_id": message_id,
            "agent_id": agent_id,
            "retry_after_seconds": retry_after_seconds,
            "user_requests_used": user_requests_used,
            "user_requests_limit": user_requests_limit,
            "system_requests_used": system_requests_used,
            "system_requests_limit": system_requests_limit,
            "timestamp": utcnow().isoformat(),
        }
        await self.broadcast_to_room(room_id, "error", data)

    async def send_artifact_update(
        self,
        room_id: str,
        message_id: str,
        agent_id: str,
        artifact: Any,
        append: bool = False,
        last_chunk: bool = False,
    ):
        """
        Send artifact update event from A2A agent streaming.

        This is used when agents stream artifacts (files, data, documents)
        incrementally during task execution. Following A2A protocol section 7.2.3.

        Args:
            room_id: The room ID
            message_id: The message being generated
            agent_id: The agent sending the artifact
            artifact: The artifact data (dict from A2A TaskArtifactUpdateEvent)
            append: Whether to append to existing artifact
            last_chunk: Whether this is the final chunk
        """
        data = {
            "message_id": message_id,
            "agent_id": agent_id,
            "artifact": artifact,
            "append": append,
            "last_chunk": last_chunk,
            "timestamp": utcnow().isoformat(),
        }
        await self.broadcast_to_room(room_id, "artifact_update", data)

    async def send_processing_status(
        self, room_id: str, status: str, message_id: str = None, details: str = None
    ):
        """Send processing status and persist to room for page refresh recovery.

        Args:
            room_id: The room ID
            status: An SSEProcessingStatus value or A2A TaskState string
            message_id: The user message ID being processed
            details: Optional details about the status
        """
        # Persist processing state to room for page refresh recovery
        # Set processing_message_id when processing starts, clear it when done
        if status == SSEProcessingStatus.PROCESSING and message_id:
            await db_service.update_room_processing_status(room_id, message_id)
        elif status in PROCESSING_DONE_STATUSES:
            await db_service.update_room_processing_status(room_id, None)

        # Send SSE event to connected clients
        data = {
            "status": status,
            "message_id": message_id,
            "details": details,
            "timestamp": utcnow().isoformat(),
        }
        await self.broadcast_to_room(room_id, "processing_status", data)

    async def send_task_submitted(
        self,
        room_id: str,
        message_id: str,
        task_id: str,
        agent_name: str,
        agent_id: str | None = None,
        status: Any = "working",
        related_message_id: str | None = None,
        created_at: str | None = None,
        step_number: int | None = None,
        total_steps: int | None = None,
        task_content: str | None = None,
    ):
        """
        Send task submitted event for long-running tasks.

        Args:
            room_id: The room ID
            message_id: The message ID (used for task tracking and frontend message identification)
            task_id: The agent's task ID
            agent_name: Name of the agent processing the task
            status: Initial status — TaskState enum or string (serialised automatically)
            created_at: Task creation timestamp (for consistent ordering)
            step_number: Current step number in the workflow (1-indexed)
            total_steps: Total number of steps in the workflow
            task_content: The task description/content being processed
        """
        data = {
            "message_id": message_id,
            "task_id": _enum_value(task_id),
            "agent_name": agent_name,
            "agent_id": agent_id,
            "status": _enum_value(status),
            "related_message_id": related_message_id,
            "created_at": created_at or utcnow().isoformat(),
            "step_number": step_number,
            "total_steps": total_steps,
            "task_content": task_content,
            "timestamp": utcnow().isoformat(),
        }
        await self.broadcast_to_room(room_id, "task_submitted", data)

    async def send_task_update(
        self,
        room_id: str,
        message_id: str,
        status: Any,
        content: str | None = None,
        error: str | None = None,
        requires_input: bool = False,
        requires_auth: bool = False,
        status_message: str | None = None,
        agent_name: str | None = None,
        agent_id: str | None = None,
        related_message_id: str | None = None,
        created_at: str | None = None,
        step_number: int | None = None,
        total_steps: int | None = None,
        task_content: str | None = None,
    ):
        """
        Send task update event when task state changes.

        Args:
            room_id: The room ID
            message_id: The message ID (used for task tracking and frontend message identification)
            status: The new task status
            content: Content if task completed
            error: Error message if task failed
            requires_input: True if input_required state
            requires_auth: True if auth_required state
            status_message: Human-readable status message from agent
            created_at: Task creation timestamp (for consistent ordering)
            step_number: Current step number in the workflow (1-indexed)
            total_steps: Total number of steps in the workflow
            task_content: The task description/content being processed
        """
        data = {
            "message_id": message_id,
            "status": _enum_value(status),
            "content": content,
            "error": error,
            "requires_input": requires_input,
            "requires_auth": requires_auth,
            "status_message": status_message,
            "agent_name": agent_name,
            "agent_id": agent_id,
            "related_message_id": related_message_id,
            "created_at": created_at,
            "step_number": step_number,
            "total_steps": total_steps,
            "task_content": task_content,
            "timestamp": utcnow().isoformat(),
        }
        await self.broadcast_to_room(room_id, "task_update", data)

    def get_room_status(self, room_id: str) -> dict:
        """get room status"""
        if room_id not in self.room_connections:
            return {
                "room_id": room_id,
                "active_connections": 0,
                "status": "no_connections",
            }

        return {
            "room_id": room_id,
            "active_connections": len(self.room_connections[room_id]),
            "status": "active",
        }

    # ============== Message Cancellation Methods ==============

    async def start_change_stream_watcher(self, db_collection):
        """
        Start watching MongoDB change stream for cancellation events.
        Should be called on application startup.

        Args:
            db_collection: MongoDB collection for cancelled_messages
        """
        self._db_collection = db_collection
        self._change_stream_task = asyncio.create_task(self._watch_cancellations())
        logger.info("Change stream watcher started for message cancellations")

    async def _watch_cancellations(self):
        """Background task that watches MongoDB for cancellation changes"""
        while not self._shutdown_flag:
            try:
                pipeline = [{"$match": {"operationType": "insert"}}]

                async with self._db_collection.watch(pipeline) as change_stream:
                    logger.info("Connected to cancellation change stream")

                    async for change in change_stream:
                        if self._shutdown_flag:
                            break
                        try:
                            message_id = change["fullDocument"]["message_id"]
                            self.cancelled_messages[message_id] = True
                            logger.info(
                                f"Received cancellation via change stream: {message_id}"
                            )
                        except KeyError as e:
                            logger.error(f"Invalid change stream document: {e}")

            except asyncio.CancelledError:
                logger.info("Change stream watcher cancelled")
                break
            except Exception as e:
                if not self._shutdown_flag:
                    logger.error(f"Change stream error: {e}. Reconnecting in 5s...")
                    await asyncio.sleep(5)

    async def stop_change_stream_watcher(self):
        """Stop the change stream watcher. Should be called on application shutdown."""
        self._shutdown_flag = True
        if self._change_stream_task:
            self._change_stream_task.cancel()
            try:
                await self._change_stream_task
            except asyncio.CancelledError:
                logger.info("Change stream watcher stopped")

    def cancel_message(self, message_id: str) -> None:
        """
        Mark a message as cancelled (local cache only).
        Actual persistence to MongoDB should be done separately.

        Args:
            message_id: The message ID to cancel
        """
        self.cancelled_messages[message_id] = True
        logger.info(f"Message {message_id} marked as cancelled in local cache")

    def is_cancelled(self, message_id: str) -> bool:
        """
        Check if a message has been cancelled.
        Uses local cache updated by change stream.

        Args:
            message_id: The message ID to check

        Returns:
            True if message is cancelled, False otherwise
        """
        return message_id in self.cancelled_messages

    def clear_cancellation(self, message_id: str) -> None:
        """
        Clear cancellation flag for a message.
        Should be called after workflow completes to clean up memory.

        Args:
            message_id: The message ID to clear
        """
        self.cancelled_messages.pop(message_id, None)
        logger.debug(f"Cleared cancellation flag for message {message_id}")


# global SSE manager instance
sse_manager = SSEManager()
