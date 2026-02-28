"""
Stale Task Checker Background Job

This module provides a background job that:
1. Polls agents for tasks that haven't received webhook updates
2. Auto-fails tasks that have been pending too long
3. Handles tasks that were never acknowledged by agents
4. Recovers orphaned agent messages that were never processed
5. Cleans up stuck room processing status
"""

import asyncio
from datetime import timedelta
from uuid import uuid4

from a2a.types import Message, Role, Task, TaskState, TaskStatus, TextPart

from api.webhooks import notify_task_update
from common.utils.logger import get_logger
from common.utils.time import ensure_utc, utcnow
from config.settings import settings
from database.mongodb import get_db
from models.request import OrchestrationRequest
from models.room import RoomAgentMessage
from modules.RoomMessageCenter import room_message_center
from services.a2a_constants import (
    INTERACTIVE_STATES,
    NON_TERMINAL_STATES,
    TERMINAL_STATES,
    is_terminal_state,
)
from services.a2a_service import a2a_service
from services.database_service import db_service

logger = get_logger(__name__)


class StaleTaskChecker:
    """
    Background job that checks for stale and expired tasks.

    This provides a fallback mechanism when webhooks fail:
    1. Stale tasks: Poll agent for current status
    2. Expired tasks: Auto-fail tasks that have been pending too long
    3. Never-acknowledged tasks: Fail tasks where agent never responded
    4. Orphaned messages: Recover agent messages that were never processed
    5. Stuck processing status: Clear processing_message_id on rooms where
       all tasks are done but the status was never cleared
    """

    def __init__(
        self,
        stale_check_minutes: int = 10,
        task_expiry_hours: int = 4,
        pending_task_warning_hours: int = 1,
        check_interval_minutes: int = 5,
        orphan_threshold_minutes: int = 2,
        processing_status_expiry_minutes: int = 30,
    ):
        """
        Initialize the stale task checker.

        Args:
            stale_check_minutes: Poll tasks not updated in this time
            task_expiry_hours: Auto-fail tasks older than this
            pending_task_warning_hours: Warn (log) after this time
            check_interval_minutes: How often to run the check
            orphan_threshold_minutes: Recover orphaned messages older than this
            processing_status_expiry_minutes: Clear stuck processing status older than this
        """
        self.stale_check_minutes = stale_check_minutes
        self.task_expiry_hours = task_expiry_hours
        self.pending_task_warning_hours = pending_task_warning_hours
        self.check_interval_minutes = check_interval_minutes
        self.orphan_threshold_minutes = orphan_threshold_minutes
        self.processing_status_expiry_minutes = processing_status_expiry_minutes
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the background checker."""
        if self._running:
            logger.warning("Stale task checker already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            f"Stale task checker started (interval: {self.check_interval_minutes}min)"
        )

    async def stop(self) -> None:
        """Stop the background checker."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Stale task checker stopped")

    async def _run_loop(self) -> None:
        """Main loop that runs the check periodically."""
        while self._running:
            try:
                await self.check_stale_tasks()
            except Exception as e:
                logger.error(f"Error in stale task checker: {e}", exc_info=True)

            # Wait for next check
            await asyncio.sleep(self.check_interval_minutes * 60)

    async def check_stale_tasks(self) -> None:
        """
        Main check function that handles stale, expired, and orphaned tasks.

        This is called periodically by the background loop.
        """
        # Get non-terminal state values for queries
        non_terminal_state_values = [s.value for s in NON_TERMINAL_STATES]

        # 1. Check stale tasks (not updated recently)
        stale_messages = await db_service.get_stale_task_messages(
            self.stale_check_minutes, non_terminal_state_values
        )
        logger.info(f"Found {len(stale_messages)} stale tasks to check")

        for msg in stale_messages:
            await self._process_stale_task(msg)

        # 2. Auto-fail expired tasks (been pending too long)
        expired_messages = await db_service.get_expired_task_messages(
            self.task_expiry_hours, non_terminal_state_values
        )
        logger.info(f"Found {len(expired_messages)} expired tasks to auto-fail")

        for msg in expired_messages:
            await self._auto_fail_expired_task(msg)

        # 3. Recover orphaned agent messages (never processed)
        await self._recover_orphaned_messages()

        # 4. Clean up stuck room processing status
        await self._cleanup_stuck_processing_status()

        # 5. Auto-fail non-tracked tasks stuck in submitted/working state too long.
        #    These are typically queued pipeline steps that were never picked up
        #    (e.g., server restarted before the pipeline got to them and orphan
        #    recovery didn't re-trigger them).  We use the same expiry threshold
        #    as tracked tasks to be consistent.
        #    We intentionally exclude interactive states (input_required,
        #    auth_required) since non-tracked tasks should never reach those.
        non_tracked_state_values = [TaskState.submitted.value, TaskState.working.value]
        non_tracked_stale = await db_service.get_non_tracked_stale_task_messages(
            self.task_expiry_hours, non_tracked_state_values
        )
        if non_tracked_stale:
            logger.info(
                f"Found {len(non_tracked_stale)} non-tracked stale tasks to auto-fail"
            )
        for msg in non_tracked_stale:
            try:
                await self._auto_fail_non_tracked_task(msg)
            except Exception as e:
                logger.error(
                    f"Failed to auto-fail non-tracked task {msg.message_id}: {e}",
                    exc_info=True,
                )

        # 6. Recover V2 supervisor trajectories stuck in "running" status.
        #    This handles mid-loop crashes where the server restarted while
        #    SupervisorExecutor.run() was in-flight.
        await self._recover_stuck_supervisor_trajectories()

    async def _process_stale_task(
        self,
        msg: RoomAgentMessage,
    ) -> None:
        """Process a single stale task."""
        message_id = msg.message_id
        if not msg.has_task_tracking:
            return

        agent_url = msg.agent_url
        task = msg.message_content.message_task if msg.message_content else None
        if not task:
            return

        agent_task_id = task.id
        created_at = (
            ensure_utc(msg.task_created_at) if msg.task_created_at else utcnow()
        )

        # Log warning for long-running tasks
        age_hours = (utcnow() - created_at).total_seconds() / 3600
        if age_hours > self.pending_task_warning_hours:
            logger.warning(
                f"Task for message {message_id} has been pending for {age_hours:.1f} hours"
            )

        # Task was never acknowledged by agent
        if agent_task_id.startswith("pending"):
            logger.warning(
                f"Task for message {message_id} never acknowledged, marking failed"
            )
            await self._mark_task_failed(
                message_id=message_id,
                msg=msg,
                error="Agent did not acknowledge the task",
            )
            return

        if not agent_url:
            logger.warning(
                f"Task for message {message_id} has no agent_url, touching timestamp"
            )
            await db_service.touch_task_message(message_id)
            return

        try:
            # Poll agent for current status
            agent_card = await a2a_service.get_agent_card_from_url(agent_url)
            current_task = await self._get_task_from_agent(agent_card, agent_task_id)

            if current_task is None:
                # Agent doesn't have this task anymore
                logger.warning(
                    f"Task for message {message_id} not found on agent, touching timestamp"
                )
                await db_service.touch_task_message(message_id)
                return

            # Update our record
            await db_service.update_task_on_message(
                message_id, current_task.model_dump(mode="json")
            )

            # Notify if terminal or interactive state changed
            new_state = current_task.status.state
            if is_terminal_state(new_state) or new_state in INTERACTIVE_STATES:
                await notify_task_update(
                    message_id=message_id,
                    task=current_task,
                    room_id=msg.room_id,
                    user_id=msg.user_id or "",
                )
            else:
                # Still working - timestamp already touched by update_task_on_message
                logger.debug(
                    f"Task for message {message_id} still in state: {new_state}"
                )

        except Exception as e:
            logger.warning(f"Failed to poll stale task for message {message_id}: {e}")
            # Don't fail the task yet - might be transient network issue
            # Touch timestamp to prevent immediate re-check
            await db_service.touch_task_message(message_id)

    async def _get_task_from_agent(self, agent_card, task_id: str) -> Task | None:
        """Get task status from agent."""
        from a2a.types import GetTaskRequest, JSONRPCErrorResponse, TaskQueryParams

        try:
            a2a_client = await a2a_service.create_a2a_client(agent_card)
            response = await a2a_client.get_task(
                GetTaskRequest(id=task_id, params=TaskQueryParams(id=task_id))
            )
            if not response or isinstance(response.root, JSONRPCErrorResponse):
                return None
            return response.root.result
        except Exception as e:
            logger.error(f"Failed to get task from agent: {e}")
            return None

    HITL_EXPIRY_HOURS = 24

    async def _auto_fail_expired_task(
        self,
        msg: RoomAgentMessage,
    ) -> None:
        """Auto-fail a task that has been pending too long."""
        message_id = msg.message_id
        if not msg.has_task_tracking:
            return

        task_state = (
            msg.message_content.message_task.status.state
            if msg.message_content
            and msg.message_content.message_task
            and msg.message_content.message_task.status
            else None
        )

        is_interactive = task_state and task_state in INTERACTIVE_STATES
        created_at = (
            ensure_utc(msg.task_created_at) if msg.task_created_at else utcnow()
        )
        age_hours = (utcnow() - created_at).total_seconds() / 3600

        if is_interactive:
            if age_hours < self.HITL_EXPIRY_HOURS:
                return
            logger.warning(
                "Auto-failing HITL task for message %s after %.1f hours "
                "(HITL threshold: %dh)",
                message_id, age_hours, self.HITL_EXPIRY_HOURS,
            )

        threshold = self.HITL_EXPIRY_HOURS if is_interactive else self.task_expiry_hours
        logger.error(
            f"Auto-failing task for message {message_id} after {age_hours:.1f} hours "
            f"(threshold: {threshold}h)"
        )

        await self._mark_task_failed(
            message_id=message_id,
            msg=msg,
            error=f"Task expired after {threshold} hours without completion. "
            "The agent may be unresponsive.",
        )

    async def _auto_fail_non_tracked_task(
        self,
        msg: RoomAgentMessage,
    ) -> None:
        """Auto-fail a non-tracked task that has been stuck in non-terminal state too long.

        These are typically queued pipeline steps that were created but never
        picked up for processing (e.g., server restarted before the pipeline
        reached them, and orphan recovery did not re-trigger them).
        """
        message_id = msg.message_id
        created_at = msg.message_created_at
        age_hours = (
            (utcnow() - ensure_utc(created_at)).total_seconds() / 3600
            if created_at
            else 0
        )

        current_state = (
            msg.message_content.message_task.status.state
            if msg.message_content and msg.message_content.message_task
            else "unknown"
        )

        logger.warning(
            f"Auto-failing non-tracked task for message {message_id} "
            f"(state: {current_state}, age: {age_hours:.1f}h, "
            f"threshold: {self.task_expiry_hours}h)"
        )

        await self._mark_task_failed(
            message_id=message_id,
            msg=msg,
            error="Task was never processed — it may have been orphaned "
            "due to a server restart or processing failure.",
        )

    async def _mark_task_failed(
        self,
        message_id: str,
        msg: RoomAgentMessage,
        error: str,
    ) -> None:
        """Mark a task as failed and notify the user."""
        task = msg.message_content.message_task if msg.message_content else None
        task_id = task.id if task else "unknown"
        context_id = task.context_id if task else ""

        failed_task = Task(
            id=task_id,
            context_id=context_id,
            status=TaskStatus(
                state=TaskState.failed,
                message=Message(
                    message_id=str(uuid4()),
                    role=Role.agent,
                    parts=[TextPart(text=error)],
                ),
                timestamp=utcnow().isoformat(),
            ),
        )

        await db_service.update_task_on_message(
            message_id, failed_task.model_dump(mode="json")
        )

        # Clear any orphaned continuation (on both agent and user messages)
        await db_service.get_and_clear_continuation_on_message(message_id)
        if msg.related_message_id:
            await db_service.get_and_clear_continuation_on_user_message(msg.related_message_id)

        # Cancel any pending HITL request for this message
        try:
            from services.hitl_service import hitl_service
            # HITL requests are keyed by user_message_id, not agent message_id.
            # related_message_id on the agent message points to the user message.
            user_msg_id = msg.related_message_id or message_id
            await hitl_service.cancel_requests_for_message(user_msg_id)
        except Exception as e:
            logger.warning(
                "stale_task_checker: Failed to cancel HITL requests for %s: %s",
                message_id, e,
            )

        await notify_task_update(
            message_id=message_id,
            task=failed_task,
            room_id=msg.room_id,
            user_id=msg.user_id or "",
        )

    async def _cleanup_stuck_processing_status(self) -> None:
        """
        Clean up rooms with stuck processing_message_id.

        This handles cases where processing_message_id was never cleared due to:
        1. Server restart while processing a message
        2. Unhandled exception during processing
        3. All agent tasks completed but status was never cleared

        For each room with processing_message_id set, checks if the referenced
        user message is old enough and all related agent tasks are in terminal
        state (or no agent messages exist). If so, clears the processing status.
        """
        mongo_db = await get_db()
        rooms_collection = mongo_db.rooms
        agent_messages_collection = mongo_db.room_agent_messages
        user_messages_collection = mongo_db.room_user_messages

        # Find rooms with processing_message_id set
        rooms_with_processing = await rooms_collection.find(
            {"processing_message_id": {"$ne": None}}
        ).to_list(length=None)

        if not rooms_with_processing:
            return

        terminal_state_values = [s.value for s in TERMINAL_STATES]
        threshold = utcnow() - timedelta(minutes=self.processing_status_expiry_minutes)

        rooms_to_clear = []

        for room in rooms_with_processing:
            room_id = room["room_id"]
            processing_message_id = room["processing_message_id"]

            # Get the user message being processed
            user_message = await user_messages_collection.find_one(
                {"message_id": processing_message_id}
            )

            if not user_message:
                # User message doesn't exist - definitely stuck
                rooms_to_clear.append(room_id)
                logger.warning(
                    f"Clearing stuck processing_message_id on room {room_id}: "
                    f"user message {processing_message_id} not found"
                )
                continue

            message_created_at = user_message.get("message_created_at")
            if isinstance(message_created_at, str):
                from dateutil.parser import parse

                message_created_at = parse(message_created_at)

            # Skip if message is recent (still legitimately processing)
            if message_created_at and ensure_utc(message_created_at) > threshold:
                continue

            # Get agent messages for this user message
            agent_messages = await agent_messages_collection.find(
                {"related_message_id": processing_message_id}
            ).to_list(length=None)

            if not agent_messages:
                # No agent messages but message is old - stuck
                rooms_to_clear.append(room_id)
                logger.warning(
                    f"Clearing stuck processing_message_id on room {room_id}: "
                    f"no agent messages for {processing_message_id}"
                )
                continue

            # Check if all tasks are in terminal state (or have no task at all)
            has_non_terminal_task = False
            for msg in agent_messages:
                task = msg.get("message_content", {}).get("message_task")
                if task:
                    state = task.get("status", {}).get("state")
                    if state and state not in terminal_state_values:
                        has_non_terminal_task = True
                        break

            if not has_non_terminal_task:
                rooms_to_clear.append(room_id)
                logger.warning(
                    f"Clearing stuck processing_message_id on room {room_id}: "
                    f"all tasks terminal for {processing_message_id}"
                )

        if rooms_to_clear:
            logger.info(
                f"Clearing stuck processing_message_id on {len(rooms_to_clear)} rooms"
            )
            await rooms_collection.update_many(
                {"room_id": {"$in": rooms_to_clear}},
                {"$set": {"processing_message_id": None}},
            )

    async def _recover_orphaned_messages(self) -> None:
        """
        Recover orphaned agent messages that were never processed.

        This handles the case where:
        1. User sends a message
        2. SendMessage creates user message + agent messages
        3. User refreshes before processRoomUserMessage is called
        4. Agent messages exist but were never executed

        Recovery groups orphaned messages by their related_message_id (user message)
        and triggers processing for each unique user message.
        """
        orphaned_messages = await db_service.get_orphaned_agent_messages(
            self.orphan_threshold_minutes
        )

        if not orphaned_messages:
            return

        logger.info(
            f"Found {len(orphaned_messages)} orphaned agent messages to recover"
        )

        # Group by related_message_id (user message) to avoid duplicate processing
        user_messages_to_process: dict[str, str] = {}  # user_message_id -> room_id

        for msg in orphaned_messages:
            user_message_id = msg.related_message_id
            if user_message_id and user_message_id not in user_messages_to_process:
                user_messages_to_process[user_message_id] = msg.room_id
                logger.info(
                    f"Orphaned message {msg.message_id} belongs to user message {user_message_id} "
                    f"in room {msg.room_id}"
                )

        for user_message_id, room_id in user_messages_to_process.items():
            try:
                logger.info(
                    f"Recovering orphaned messages for user message {user_message_id} in room {room_id}"
                )

                request = OrchestrationRequest(
                    room_id=room_id,
                    room_user_message_id=user_message_id,
                    room_related_message_id="",
                )

                # Process in background to not block the checker
                asyncio.create_task(
                    self._process_orphaned_user_message(room_message_center, request)
                )

            except Exception as e:
                logger.error(
                    f"Failed to trigger recovery for user message {user_message_id}: {e}"
                )

    async def _process_orphaned_user_message(
        self,
        room_message_center,
        request: OrchestrationRequest,
    ) -> None:
        """Process an orphaned user message in the background."""
        try:
            result = await room_message_center.process_room_user_message(request)
            if result.success:
                logger.info(
                    f"Successfully recovered orphaned messages for user message {request.room_user_message_id}"
                )
            else:
                logger.warning(
                    f"Recovery for user message {request.room_user_message_id} completed with error: {result.error}"
                )
        except Exception as e:
            logger.error(
                f"Exception during recovery of user message {request.room_user_message_id}: {e}"
            )

    async def _recover_stuck_supervisor_trajectories(self) -> None:
        """Recover V2 supervisor trajectories stuck in "running" status.

        When the server crashes mid-loop, ``_checkpoint_trajectory`` has
        already persisted the trajectory with ``status="running"`` to the
        user message's ``extend_info.supervisor_trajectory``.  On restart,
        we scan for these and re-trigger ``process_room_user_message`` so
        ``_process_supervisor_v2`` picks up the checkpointed trajectory.

        Only messages older than ``orphan_threshold_minutes`` are recovered
        to avoid racing with actively running trajectories.
        """
        stuck_messages = await db_service.get_stuck_supervisor_trajectory_messages(
            self.orphan_threshold_minutes
        )

        if not stuck_messages:
            return

        logger.info(
            "supervisor_recovery: found %d stuck V2 trajectories to recover",
            len(stuck_messages),
        )

        for doc in stuck_messages:
            message_id = doc.get("message_id")
            room_id = doc.get("room_id")
            if not message_id or not room_id:
                continue

            # Atomically claim this trajectory so no other worker (or
            # subsequent check cycle) can recover it concurrently.
            claimed = await db_service.claim_stuck_supervisor_trajectory(message_id)
            if not claimed:
                logger.info(
                    "supervisor_recovery: message %s already claimed by another worker",
                    message_id,
                )
                continue

            # Respect persistent cancellation: if the user canceled during
            # the crash window, the in-memory token was lost but the
            # cancelled_messages DB record survives.
            if await db_service.is_message_cancelled(message_id):
                logger.info(
                    "supervisor_recovery: skipping message %s — cancelled by user",
                    message_id,
                )
                continue

            try:
                logger.info(
                    "supervisor_recovery: re-triggering message %s in room %s",
                    message_id,
                    room_id,
                )
                request = OrchestrationRequest(
                    room_id=room_id,
                    room_user_message_id=message_id,
                    room_related_message_id="",
                )
                asyncio.create_task(
                    self._process_recovered_supervisor_message(request, message_id)
                )
            except Exception as e:
                logger.error(
                    "supervisor_recovery: failed to trigger recovery for %s: %s",
                    message_id,
                    e,
                )

    async def _process_recovered_supervisor_message(
        self,
        request: OrchestrationRequest,
        message_id: str,
    ) -> None:
        """Process a recovered supervisor V2 message in the background."""
        try:
            result = await room_message_center.process_room_user_message(request)
            if result.success:
                logger.info(
                    "supervisor_recovery: successfully recovered message %s",
                    message_id,
                )
            else:
                logger.warning(
                    "supervisor_recovery: recovery for %s completed with error: %s",
                    message_id,
                    result.error,
                )
        except Exception as e:
            logger.error(
                "supervisor_recovery: exception recovering message %s: %s",
                message_id,
                e,
            )


# Singleton instance
stale_task_checker = StaleTaskChecker(
    stale_check_minutes=settings.stale_check_minutes,
    task_expiry_hours=settings.task_expiry_hours,
    pending_task_warning_hours=settings.pending_task_warning_hours,
    orphan_threshold_minutes=settings.orphan_threshold_minutes,
    processing_status_expiry_minutes=settings.processing_status_expiry_minutes,
)
