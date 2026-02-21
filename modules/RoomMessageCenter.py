from collections import deque

from common.utils.context_utils import get_context_stats
from common.utils.logger import get_logger
from models.request import OrchestrationRequest, RoomCenterAgentMessageRequest
from models.response import OrchestrationResponse
from models.supervisor import RoomConfig, StepResult, SupervisorPlan
from modules.AgentDispatcher import AgentDispatcher
from modules.QueueExecutor import QueueExecutor, QueueProcessingResult, QueueResult
from modules.ResponseProcessor import ResponseProcessor
from modules.TaskStateManager import TaskStateManager
from services.a2a_constants import SSEProcessingStatus
from services.a2a_service import a2a_service
from services.agent_resolver_service import agent_resolver_service
from services.database_service import db_service
from services.debate_service import debate_service
from services.memory_service import room_memory_service
from services.notification_service import notification_service
from services.rate_limit_service import rate_limit_service
from services.room_coordinator_service import room_coordinator_service
from services.room_services import room_services
from services.room_supervisor_service import room_supervisor_service
from services.sse_services import sse_manager
from services.task_service import task_service

logger = get_logger(__name__)


class RoomMessageCenter:
    """Room user message processing: agent communication,
    streaming/sync responses, queue management, and memory updates."""

    def __init__(self):
        self.room_services = room_services
        self.database_service = db_service
        self.sse_manager = sse_manager
        self.room_coordinator_service = room_coordinator_service
        self.tsm = TaskStateManager(room_services, notification_service)
        self.response_processor = ResponseProcessor(
            tsm=self.tsm,
            sse_manager=self.sse_manager,
            a2a_service=a2a_service,
            task_service=task_service,
            database_service=self.database_service,
        )
        self.agent_dispatcher = AgentDispatcher(
            agent_resolver=agent_resolver_service,
            database_service=self.database_service,
        )
        self.queue_executor = QueueExecutor(
            tsm=self.tsm,
            sse_manager=self.sse_manager,
            response_processor=self.response_processor,
            a2a_service=a2a_service,
            room_services=self.room_services,
            room_memory_service=room_memory_service,
            database_service=self.database_service,
            debate_service=debate_service,
            rate_limit_service=rate_limit_service,
            agent_dispatcher=self.agent_dispatcher,
            supervisor_service=room_supervisor_service,
        )

    # ------------------------------------------------------------------

    async def process_room_user_message(
        self, request: OrchestrationRequest
    ) -> OrchestrationResponse:
        """
        Process a room user message by executing all related agent messages in sequence.

        This method:
        1. Gets room memory context
        2. Queries all agent messages related to the user message
        3. Processes each agent message in order using streaming
        4. Updates room memory after all agents have responded
        5. Sends SSE events to the room for real-time updates

        Args:
            request: Contains room_id and room_user_message_id

        Returns:
            OrchestrationResponse with success status
        """
        logger.debug(
            "RoomMessageCenter: Starting to process room user message %s in room %s",
            request.room_user_message_id,
            request.room_id,
        )

        # Validate request
        validation_response = self._validate_room_message_request(request)
        if validation_response:
            return validation_response

        room_id = request.room_id
        room_user_message_id = request.room_user_message_id

        # Get user_id from the user message for rate limiting.
        # Fall back to the request-level user_id (from auth) if the stored
        # message is missing or has no user_id.
        user_message = await self.database_service.get_room_user_message_by_message_id(
            room_user_message_id
        )
        user_id = (
            (user_message.user_id if user_message else None) or request.user_id
        )

        # Extract quoted context from user message extend_info (set when user quotes text)
        quoted_text: str | None = None
        supervisor_plan = None
        if user_message and isinstance(user_message.extend_info, dict):
            quoted_text = user_message.extend_info.get("quoted_text") or None
            # Extract SupervisorPlan if present (set by _parse_with_supervisor)
            plan_data = user_message.extend_info.get("supervisor_plan")
            if plan_data:
                from models.supervisor import SupervisorPlan
                try:
                    supervisor_plan = SupervisorPlan.model_validate(plan_data)
                except Exception as e:
                    logger.warning(
                        "RoomMessageCenter: Failed to parse supervisor_plan: %s", e
                    )

        # Create a CancellationToken for this message pipeline (A-3).
        # The token is pre-signalled if cancel_message() was called before
        # processing started — no race window.
        # If a token was already created (e.g. by send_message_to_room for
        # the parsing phase), reuse it so the entire pipeline shares one token.
        token = self.sse_manager.get_token(room_user_message_id)
        if token is None:
            token = self.sse_manager.create_token(room_user_message_id)

        # Query agent messages to process
        query_response = (
            await self.room_services.inquiry_agent_messages_by_related_message_id(
                RoomCenterAgentMessageRequest(related_message_id=room_user_message_id)
            )
        )
        if not query_response.success:
            return OrchestrationResponse(
                room_id=room_id,
                success=False,
                error=query_response.error,
                status_code=500,
            )

        # Process all agent messages in sequence
        message_queue = (
            deque(query_response.message_list)
            if query_response.message_list is not None
            else deque()
        )

        logger.debug(
            "RoomMessageCenter: Starting to process %d agent messages for room %s and user message %s",
            len(message_queue),
            room_id,
            room_user_message_id,
        )

        # Check for cancellation before processing agent messages
        if token.is_cancelled:
            logger.info(
                "RoomMessageCenter: Processing cancelled for message %s, stopping all processing",
                room_user_message_id,
            )
            # Capture IDs before cancellation for descendant cleanup
            step1_ids = [msg.message_id for msg in message_queue]
            await self.tsm.cancel_remaining_queue(message_queue)
            # Cancel DB-only descendants (step 2, 3, …) downstream in the
            # related_message_id chain from these step-1 messages.
            for mid in step1_ids:
                await self.database_service.cancel_descendants(mid)
            await self.sse_manager.send_processing_status(
                room_id, SSEProcessingStatus.CANCELED, room_user_message_id
            )
            self.sse_manager.clear_cancellation(room_user_message_id)
            return OrchestrationResponse(
                success=True,
                error="Processing cancelled by user",
                status_code=200,
            )

        queue_processing_result = await self.queue_executor.process_queue(
            message_queue,
            room_id,
            room_user_message_id,
            token=token,
            request_user_id=user_id,
            quoted_text=quoted_text,
            supervisor_plan=supervisor_plan,
        )

        if queue_processing_result.result == QueueResult.FAILED:
            await self.sse_manager.send_processing_status(
                room_id,
                SSEProcessingStatus.FAILED,
                room_user_message_id,
                details="Failed to process agent messages",
            )
            return OrchestrationResponse(
                success=False,
                error="Failed to process agent messages",
                status_code=500,
            )

        if queue_processing_result.result == QueueResult.PAUSED:
            # Queue paused for push notification — do NOT trigger summary or
            # COMPLETED yet. The webhook handler will resume and trigger
            # summary when the agent finishes.
            return OrchestrationResponse(
                room_id=room_id, success=True, error=None, status_code=200
            )

        if queue_processing_result.result == QueueResult.CANCELED:
            # CANCELED status was already sent to the frontend inside the queue
            # processor. Return early — do NOT send COMPLETED or trigger summary.
            return OrchestrationResponse(
                success=True,
                error="Processing cancelled by user",
                status_code=200,
            )

        # QueueResult.COMPLETED — proceed with summary + completion.
        await self._handle_completion(
            room_id=room_id,
            room_user_message_id=room_user_message_id,
            supervisor_plan=queue_processing_result.supervisor_plan,
            step_results=queue_processing_result.step_results,
        )

        # Send completion status
        await self.sse_manager.send_processing_status(
            room_id, SSEProcessingStatus.COMPLETED, room_user_message_id
        )

        # Log room memory stats (debug/monitoring)
        await self._log_room_memory_stats(room_id)

        return OrchestrationResponse(
            room_id=room_id, success=True, error=None, status_code=200
        )

    def _validate_room_message_request(
        self, request: OrchestrationRequest
    ) -> OrchestrationResponse | None:
        """Validate the room message request parameters."""
        if request.room_id is None:
            return OrchestrationResponse(
                success=False,
                error="Room id is required",
                status_code=400,
            )

        if request.room_user_message_id is None:
            return OrchestrationResponse(
                success=False,
                error="Room user message id is required",
                status_code=400,
            )

        return None

    async def _handle_completion(
        self,
        room_id: str,
        room_user_message_id: str,
        supervisor_plan: SupervisorPlan | None,
        step_results: list[StepResult] | None,
    ) -> None:
        """Handle completion: use Supervisor synthesis or fall back to coordinator.

        If a SupervisorPlan exists:
        - For 2+ step results: use Supervisor's synthesize_results() for guided synthesis
        - For 1 step result: skip synthesis entirely (single agent doesn't need summary)

        If no SupervisorPlan: fall back to legacy RoomCoordinatorService.
        """
        # If Supervisor was used, it owns the completion logic
        if supervisor_plan:
            # Only synthesize if we have multiple results
            if step_results and len(step_results) >= 2:
                try:
                    # Get room config for synthesis
                    room = await self.database_service.get_room_by_room_id(room_id)
                    is_debate_mode = False
                    if room and room.extend_info and isinstance(room.extend_info, dict):
                        is_debate_mode = bool(room.extend_info.get("debateMode", False))

                    room_config = RoomConfig(is_debate_mode=is_debate_mode)

                    # Convert list to dict for synthesize_results
                    step_results_dict = {r.step_id: r for r in step_results}

                    synthesis_text = await room_supervisor_service.synthesize_results(
                        plan=supervisor_plan,
                        step_results=step_results_dict,
                        room_config=room_config,
                    )

                    if synthesis_text:
                        await self.room_coordinator_service.emit_synthesis_message(
                            room_id=room_id,
                            room_user_message_id=room_user_message_id,
                            synthesis_text=synthesis_text,
                            coordinator_agent_id="supervisor_synthesis",
                        )
                        logger.info(
                            "RoomMessageCenter: Supervisor synthesis completed for %s",
                            room_user_message_id,
                        )

                except Exception as e:
                    logger.warning(
                        "RoomMessageCenter: Supervisor synthesis failed: %s", e
                    )

            # For 1-step Supervisor plans, no synthesis needed - just return
            # Do NOT fall through to legacy coordinator
            return

        # Fall back to legacy coordinator (only when no supervisor_plan)
        await self.room_coordinator_service.on_room_user_message_completed(
            room_id, room_user_message_id
        )

    # ------------------------------------------------------------------
    # Webhook resume (thin wrapper around QueueExecutor)
    # ------------------------------------------------------------------

    async def resume_queue_from_continuation(
        self,
        message_id: str,
        task_result_text: str | None = None,
    ) -> bool:
        """Resume queue processing after a push notification task completes.

        Delegates the actual queue mechanics to ``QueueExecutor`` and handles
        the post-completion logic (synthesis + COMPLETED SSE status) here.

        Returns ``True`` if the queue was resumed successfully.
        """
        result = await self.queue_executor.resume_from_continuation(
            message_id,
            task_result_text,
        )

        if not result.success:
            return False

        if result.needs_completion and result.room_id and result.user_message_id:
            await self._handle_completion(
                room_id=result.room_id,
                room_user_message_id=result.user_message_id,
                supervisor_plan=result.supervisor_plan,
                step_results=result.step_results,
            )
            await self.sse_manager.send_processing_status(
                result.room_id, SSEProcessingStatus.COMPLETED, result.user_message_id
            )
            await self._log_room_memory_stats(result.room_id)

        return True

    # ------------------------------------------------------------------
    # Monitoring
    # ------------------------------------------------------------------

    async def _log_room_memory_stats(self, room_id: str) -> None:
        """Log room memory stats after processing (debug/monitoring only)."""
        room_memory = await self.database_service.get_room_memory_by_room_id(room_id)
        if room_memory and room_memory.memory_content:
            stats = get_context_stats(room_memory.memory_content)
            logger.info(
                "RoomMessageCenter: Room %s memory - %d turns, summary=%s, chars=%d",
                room_id,
                stats.get("history_turns", 0),
                "yes" if stats.get("has_summary") else "no",
                stats.get("total_chars", 0),
            )


# Module-level singleton
room_message_center = RoomMessageCenter()
