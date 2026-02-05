"""
Stale Task Checker Background Job

This module provides a background job that:
1. Polls agents for tasks that haven't received webhook updates
2. Auto-fails tasks that have been pending too long
3. Handles tasks that were never acknowledged by agents
"""

import asyncio
from uuid import uuid4

from a2a.types import Message, Role, Task, TaskState, TaskStatus, TextPart

from api.webhooks import notify_task_update
from common.utils.logger import get_logger
from common.utils.time import ensure_utc, utcnow
from config.settings import settings
from services.a2a_constants import INTERACTIVE_STATES, is_terminal_state
from services.a2a_service import a2a_service
from services.a2a_task_service import get_a2a_task_service

logger = get_logger(__name__)


class StaleTaskChecker:
    """
    Background job that checks for stale and expired tasks.

    This provides a fallback mechanism when webhooks fail:
    1. Stale tasks: Poll agent for current status
    2. Expired tasks: Auto-fail tasks that have been pending too long
    3. Never-acknowledged tasks: Fail tasks where agent never responded
    """

    def __init__(
        self,
        stale_check_minutes: int = 10,
        task_expiry_hours: int = 4,
        pending_task_warning_hours: int = 1,
        check_interval_minutes: int = 5,
    ):
        """
        Initialize the stale task checker.

        Args:
            stale_check_minutes: Poll tasks not updated in this time
            task_expiry_hours: Auto-fail tasks older than this
            pending_task_warning_hours: Warn (log) after this time
            check_interval_minutes: How often to run the check
        """
        self.stale_check_minutes = stale_check_minutes
        self.task_expiry_hours = task_expiry_hours
        self.pending_task_warning_hours = pending_task_warning_hours
        self.check_interval_minutes = check_interval_minutes
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
        Main check function that handles stale and expired tasks.

        This is called periodically by the background loop.
        """
        try:
            task_service = get_a2a_task_service()
        except RuntimeError:
            logger.debug("A2A Task Service not initialized yet, skipping check")
            return

        # 1. Check stale tasks (not updated recently)
        stale_tasks = await task_service.get_stale_tasks(self.stale_check_minutes)
        logger.info(f"Found {len(stale_tasks)} stale tasks to check")

        for stored_task in stale_tasks:
            await self._process_stale_task(stored_task, task_service)

        # 2. Auto-fail expired tasks (been pending too long)
        expired_tasks = await task_service.get_expired_tasks(self.task_expiry_hours)
        logger.info(f"Found {len(expired_tasks)} expired tasks to auto-fail")

        for stored_task in expired_tasks:
            await self._auto_fail_expired_task(stored_task, task_service)

    async def _process_stale_task(
        self,
        stored_task: dict,
        task_service,
    ) -> None:
        """Process a single stale task."""
        internal_id = stored_task["internal_id"]
        agent_url = stored_task["agent_url"]
        agent_task_id = stored_task["task"].id
        created_at = ensure_utc(stored_task["created_at"])

        # Log warning for long-running tasks
        age_hours = (utcnow() - created_at).total_seconds() / 3600
        if age_hours > self.pending_task_warning_hours:
            logger.warning(
                f"Task {internal_id} has been pending for {age_hours:.1f} hours"
            )

        # Task was never acknowledged by agent
        if agent_task_id == "pending":
            logger.warning(f"Task {internal_id} never acknowledged, marking failed")
            await self._mark_task_failed(
                internal_id=internal_id,
                stored_task=stored_task,
                error="Agent did not acknowledge the task",
                task_service=task_service,
            )
            return

        try:
            # Poll agent for current status
            agent_card = await a2a_service.get_agent_card_from_url(agent_url)
            current_task = await self._get_task_from_agent(agent_card, agent_task_id)

            if current_task is None:
                # Agent doesn't have this task anymore
                logger.warning(
                    f"Task {internal_id} not found on agent, touching timestamp"
                )
                await task_service.touch_task(internal_id)
                return

            # Update our record
            await task_service.update_task(internal_id, current_task)

            # Notify if terminal or interactive state changed
            new_state = current_task.status.state
            if is_terminal_state(new_state) or new_state in INTERACTIVE_STATES:
                await notify_task_update(
                    internal_id=internal_id,
                    task=current_task,
                    room_id=stored_task["room_id"],
                    user_id=stored_task["user_id"],
                )
            else:
                # Still working - timestamp already touched by update_task
                logger.debug(f"Task {internal_id} still in state: {new_state}")

        except Exception as e:
            logger.warning(f"Failed to poll stale task {internal_id}: {e}")
            # Don't fail the task yet - might be transient network issue
            # Touch timestamp to prevent immediate re-check
            await task_service.touch_task(internal_id)

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

    async def _auto_fail_expired_task(
        self,
        stored_task: dict,
        task_service,
    ) -> None:
        """Auto-fail a task that has been pending too long."""
        internal_id = stored_task["internal_id"]
        created_at = ensure_utc(stored_task["created_at"])

        age_hours = (utcnow() - created_at).total_seconds() / 3600

        logger.error(
            f"Auto-failing task {internal_id} after {age_hours:.1f} hours "
            f"(threshold: {self.task_expiry_hours}h)"
        )

        await self._mark_task_failed(
            internal_id=internal_id,
            stored_task=stored_task,
            error=f"Task expired after {self.task_expiry_hours} hours without completion. "
            "The agent may be unresponsive.",
            task_service=task_service,
        )

    async def _mark_task_failed(
        self,
        internal_id: str,
        stored_task: dict,
        error: str,
        task_service,
    ) -> None:
        """Mark a task as failed and notify the user."""
        failed_task = Task(
            id=stored_task["task"].id,
            context_id=stored_task["task"].context_id,
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

        await task_service.update_task(internal_id, failed_task)

        await notify_task_update(
            internal_id=internal_id,
            task=failed_task,
            room_id=stored_task["room_id"],
            user_id=stored_task["user_id"],
        )


# Singleton instance
stale_task_checker = StaleTaskChecker(
    stale_check_minutes=settings.stale_check_minutes,
    task_expiry_hours=settings.task_expiry_hours,
    pending_task_warning_hours=settings.pending_task_warning_hours,
)
