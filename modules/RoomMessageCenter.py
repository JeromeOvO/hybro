from collections import deque

from common.utils.context_utils import get_context_stats
from common.utils.logger import get_logger
from models.request import OrchestrationRequest, RoomCenterAgentMessageRequest
from models.response import OrchestrationResponse
from models.supervisor import AgentProfile, RoomConfig, StepResult, SupervisorPlan
from models.supervisor_v2 import (
    RunStatus,
    StepStatus,
    SupervisorRunResult,
    SupervisorTrajectory,
)
from modules.AgentDispatcher import AgentDispatcher
from modules.AgentMessageProcessor import AgentMessageProcessor
from modules.QueueExecutor import QueueExecutor, QueueResult
from modules.ResponseProcessor import ResponseProcessor
from modules.SupervisorExecutor import SupervisorExecutor
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
        self.agent_message_processor = AgentMessageProcessor(
            tsm=self.tsm,
            sse_manager=self.sse_manager,
            response_processor=self.response_processor,
            a2a_service=a2a_service,
            room_services=self.room_services,
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
            agent_message_processor=self.agent_message_processor,
        )
        self.supervisor_executor = SupervisorExecutor(
            supervisor_service=room_supervisor_service,
            room_services=self.room_services,
            tsm=self.tsm,
            sse_manager=self.sse_manager,
            database_service=self.database_service,
            room_memory_service=room_memory_service,
            rate_limit_service=rate_limit_service,
            agent_dispatcher=self.agent_dispatcher,
            agent_message_processor=self.agent_message_processor,
            room_coordinator_service=self.room_coordinator_service,
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

        # --- V2 Supervisor branch ---
        # If the user message was prepared via _prepare_for_supervisor_v2, the
        # extend_info contains supervisor_v2=True plus the data needed for the
        # adaptive loop.  Skip the legacy queue path entirely.
        is_supervisor_v2 = (
            user_message
            and isinstance(user_message.extend_info, dict)
            and user_message.extend_info.get("supervisor_v2", False)
        )
        if is_supervisor_v2:
            return await self._process_supervisor_v2(
                user_message=user_message,
                room_id=room_id,
                room_user_message_id=room_user_message_id,
                user_id=user_id,
                quoted_text=quoted_text,
                token=token,
            )

        # --- V1 / legacy path (unchanged) ---

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
                    return  # Success: synthesis emitted or empty (no summary needed)

                except Exception as e:
                    logger.warning(
                        "RoomMessageCenter: Supervisor synthesis failed, "
                        "falling back to coordinator: %s",
                        e,
                    )
                    # Fall through to coordinator below

                # Supervisor synthesis failed — fall back to legacy coordinator
                await self.room_coordinator_service.on_room_user_message_completed(
                    room_id, room_user_message_id
                )
                return

            return

        # No supervisor_plan: use legacy coordinator
        await self.room_coordinator_service.on_room_user_message_completed(
            room_id, room_user_message_id
        )

    # ------------------------------------------------------------------
    # V2 Supervisor adaptive loop
    # ------------------------------------------------------------------

    async def _process_supervisor_v2(
        self,
        user_message,
        room_id: str,
        room_user_message_id: str,
        user_id: str | None,
        quoted_text: str | None,
        token,
    ) -> OrchestrationResponse:
        """Execute the V2 supervisor adaptive loop for a user message.

        Deserializes ``agent_registry``, ``room_config``, and
        ``conversation_context`` from the user message's ``extend_info``
        (set by ``_prepare_for_supervisor_v2`` in ``RoomServices``), then
        delegates to ``SupervisorExecutor.run()``.

        Handles all 5 ``RunStatus`` variants.
        """
        from services.room_supervisor_service import SupervisorPlanningError

        extend = user_message.extend_info
        try:
            agent_registry = [
                AgentProfile(**p) for p in extend["agent_registry"]
            ]
            room_config = RoomConfig(**extend["room_config"])
        except (KeyError, TypeError) as e:
            logger.error(
                "RoomMessageCenter: V2 extend_info missing required keys: %s",
                e,
            )
            await self.sse_manager.send_processing_status(
                room_id, SSEProcessingStatus.FAILED, room_user_message_id,
                details="V2 supervisor data corrupted or incomplete",
            )
            return OrchestrationResponse(
                room_id=room_id,
                success=False,
                error=f"V2 supervisor data corrupted: {e}",
                status_code=500,
            )
        conversation_context = extend.get("conversation_context")

        try:
            result = await self.supervisor_executor.run(
                room_id=room_id,
                user_message_id=room_user_message_id,
                message_text=user_message.message_content.message_text or "",
                agent_registry=agent_registry,
                room_config=room_config,
                conversation_context=conversation_context,
                token=token,
                request_user_id=user_id,
                quoted_text=quoted_text,
            )
        except SupervisorPlanningError:
            logger.warning(
                "RoomMessageCenter: V2 supervisor first decide_next failed, "
                "falling back to V1 queue path for message %s",
                room_user_message_id,
            )
            return await self._fallback_to_v1_queue(
                room_id=room_id,
                room_user_message_id=room_user_message_id,
                user_id=user_id,
                quoted_text=quoted_text,
                token=token,
            )

        # Persist trajectory + handle SSE/synthesis
        await self._handle_v2_run_result(
            result=result,
            room_id=room_id,
            user_message_id=room_user_message_id,
        )

        await self._log_room_memory_stats(room_id)

        is_failure = result.status in (RunStatus.FAILED,)
        return OrchestrationResponse(
            room_id=room_id,
            success=not is_failure,
            error="Supervisor V2 execution failed" if is_failure else None,
            status_code=500 if is_failure else 200,
        )

    async def _fallback_to_v1_queue(
        self,
        room_id: str,
        room_user_message_id: str,
        user_id: str | None,
        quoted_text: str | None,
        token,
    ) -> OrchestrationResponse:
        """Fall back to the V1 QueueExecutor path when the first V2 decide_next fails."""
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

        message_queue = (
            deque(query_response.message_list)
            if query_response.message_list is not None
            else deque()
        )

        if not message_queue:
            await self.sse_manager.send_processing_status(
                room_id, SSEProcessingStatus.FAILED, room_user_message_id,
                details="V2 fallback: no pre-generated agent messages found",
            )
            return OrchestrationResponse(
                room_id=room_id,
                success=False,
                error="V2 supervisor failed and no V1 agent messages available",
                status_code=500,
            )

        queue_processing_result = await self.queue_executor.process_queue(
            message_queue,
            room_id,
            room_user_message_id,
            token=token,
            request_user_id=user_id,
            quoted_text=quoted_text,
        )

        if queue_processing_result.result == QueueResult.COMPLETED:
            await self._handle_completion(
                room_id=room_id,
                room_user_message_id=room_user_message_id,
                supervisor_plan=queue_processing_result.supervisor_plan,
                step_results=queue_processing_result.step_results,
            )
            await self.sse_manager.send_processing_status(
                room_id, SSEProcessingStatus.COMPLETED, room_user_message_id
            )
        elif queue_processing_result.result == QueueResult.FAILED:
            await self.sse_manager.send_processing_status(
                room_id, SSEProcessingStatus.FAILED, room_user_message_id,
                details="V2 fallback queue failed",
            )

        return OrchestrationResponse(
            room_id=room_id, success=True, error=None, status_code=200
        )

    # ------------------------------------------------------------------
    # V2 Supervisor resume (push notification webhook)
    # ------------------------------------------------------------------

    async def _resume_supervisor_v2(
        self,
        continuation: dict,
        paused_message_id: str,
        task_result_text: str | None,
    ) -> bool:
        """Resume a V2 supervisor loop after a push notification webhook.

        Steps:
        1. Deserialize the trajectory from continuation data.
        2. Find the ``TrajectoryEntry`` containing the paused step and append
           the push notification result to it.
        3. Refresh the agent registry from the database (agents may have
           changed while the execution was paused).
        4. Add the completed agent's response to room memory.
        5. Call ``SupervisorExecutor.run(..., resumed_trajectory=...)`` to
           continue the adaptive loop.
        6. Handle the ``RunStatus`` result (synthesis, SSE, trajectory persistence).
        """
        from models.supervisor_v2 import (
            SupervisorTrajectory,
        )

        room_id = continuation.get("room_id")
        user_message_id = continuation.get("user_message_id")
        message_text = continuation.get("message_text", "")
        request_user_id = continuation.get("request_user_id")
        conversation_context = continuation.get("conversation_context")

        if not room_id or not user_message_id:
            logger.error(
                "RoomMessageCenter: V2 resume missing room_id or user_message_id "
                "in continuation. message_id=%s",
                paused_message_id,
            )
            return False

        # 1. Deserialize trajectory
        try:
            trajectory = SupervisorTrajectory(
                **continuation["trajectory"]
            )
        except (KeyError, TypeError) as e:
            logger.error(
                "RoomMessageCenter: V2 resume failed to deserialize trajectory: %s",
                e,
            )
            await self.sse_manager.send_processing_status(
                room_id,
                SSEProcessingStatus.FAILED,
                user_message_id,
                details="V2 resume: corrupted trajectory data",
            )
            return False

        logger.info(
            "supervisor_v2_resume_started",
            extra={
                "room_id": room_id,
                "trajectory_id": trajectory.trajectory_id,
                "paused_message_id": paused_message_id,
                "user_message_id": user_message_id,
            },
        )

        # 2. Identify the paused agent (before appending result, since the
        #    append fills in the missing entry)
        paused_agent_id: str | None = None
        paused_agent_name: str | None = None
        if task_result_text:
            paused_agent_id, paused_agent_name = self._find_paused_agent(
                trajectory, paused_message_id
            )

        # 3. Append the push notification result to the matching entry
        self._append_paused_result_to_trajectory(
            trajectory,
            paused_message_id=paused_message_id,
            task_result_text=task_result_text,
        )

        # 4. Add completed agent response to room memory
        if task_result_text and paused_agent_id:
            await room_memory_service.add_agent_response_to_memory(
                room_id=room_id,
                agent_id=paused_agent_id,
                agent_name=paused_agent_name or "Agent",
                response_text=task_result_text,
            )

        # 5. Refresh agent registry from database (not serialized)
        room = await self.database_service.get_room_by_room_id(room_id)
        if not room:
            logger.error(
                "RoomMessageCenter: V2 resume room not found: %s", room_id
            )
            await self.sse_manager.send_processing_status(
                room_id,
                SSEProcessingStatus.FAILED,
                user_message_id,
                details="V2 resume: room not found",
            )
            return False

        agent_registry: list[AgentProfile] = []
        for aid, aname in (room.room_agent_set or {}).items():
            agent = await self.database_service.get_agent_by_agent_id(aid)
            if agent:
                agent_registry.append(AgentProfile.from_agent(agent))
            else:
                agent_registry.append(
                    AgentProfile(
                        agent_id=aid,
                        agent_name=aname,
                        description="",
                        is_healthy=False,
                    )
                )

        if not agent_registry:
            # Fall back to serialized registry if DB refresh yields nothing
            try:
                agent_registry = [
                    AgentProfile(**p)
                    for p in continuation.get("agent_registry", [])
                ]
            except (TypeError, KeyError) as e:
                logger.warning(
                    "RoomMessageCenter: V2 resume fallback registry failed: %s", e
                )

        # Use serialized room_config from continuation as the base (preserves
        # all fields), then selectively refresh fields that may have changed
        # while execution was paused.
        try:
            room_config = RoomConfig(**continuation.get("room_config", {}))
        except (TypeError, KeyError):
            room_config = RoomConfig()
        room_config.is_debate_mode = bool(
            room.extend_info.get("debateMode", False)
            if isinstance(room.extend_info, dict)
            else False
        )
        room_config.room_agent_set = room.room_agent_set or {}

        # 6. Create/reuse cancellation token
        token = self.sse_manager.get_token(user_message_id)
        if token is None:
            token = self.sse_manager.create_token(user_message_id)

        # 7. Resume the supervisor loop
        try:
            result = await self.supervisor_executor.run(
                room_id=room_id,
                user_message_id=user_message_id,
                message_text=message_text,
                agent_registry=agent_registry,
                room_config=room_config,
                conversation_context=conversation_context,
                token=token,
                request_user_id=request_user_id,
                resumed_trajectory=trajectory,
            )
        except Exception:
            logger.exception(
                "RoomMessageCenter: V2 resume supervisor_executor.run() failed"
            )
            await self.sse_manager.send_processing_status(
                room_id,
                SSEProcessingStatus.FAILED,
                user_message_id,
                details="V2 resume: executor failed",
            )
            return False

        # 8. Handle the result
        await self._handle_v2_run_result(
            result=result,
            room_id=room_id,
            user_message_id=user_message_id,
            room=room,
        )

        await self._log_room_memory_stats(room_id)

        logger.info(
            "supervisor_v2_resume_completed",
            extra={
                "room_id": room_id,
                "trajectory_id": trajectory.trajectory_id,
                "status": result.status,
            },
        )
        return result.status != RunStatus.FAILED

    @staticmethod
    def _append_paused_result_to_trajectory(
        trajectory: SupervisorTrajectory,
        paused_message_id: str,
        task_result_text: str | None,
    ) -> None:
        """Replace the PAUSED ``V2StepResult`` with a completed one carrying
        the push notification response.

        PAUSED results are now preserved in the serialized trajectory (with
        ``status=PAUSED`` and ``agent_message_id`` set).  We find the exact
        result by matching ``agent_message_id == paused_message_id`` and
        replace it in-place, which is correct even when multiple agents in
        the same multi-target DELEGATE are paused.
        """
        from common.utils.time import utcnow
        from models.supervisor_v2 import StepStatus, V2StepResult

        for entry in trajectory.entries:
            for idx, result in enumerate(entry.results):
                if (
                    result.status == StepStatus.PAUSED
                    and result.agent_message_id == paused_message_id
                ):
                    entry.results[idx] = V2StepResult(
                        step_number=entry.step_number,
                        agent_id=result.agent_id,
                        agent_name=result.agent_name,
                        task=result.task,
                        response_text=task_result_text or "",
                        success=bool(task_result_text),
                        status=(
                            StepStatus.SUCCESS
                            if task_result_text
                            else StepStatus.FAILED
                        ),
                        error_message=(
                            None
                            if task_result_text
                            else "No result from push notification"
                        ),
                        agent_message_id=paused_message_id,
                        completed_at=utcnow(),
                    )
                    # Mark entry completed if no more PAUSED results remain
                    still_paused = any(
                        r.status == StepStatus.PAUSED for r in entry.results
                    )
                    if not still_paused and entry.completed_at is None:
                        entry.completed_at = utcnow()

                    logger.info(
                        "supervisor_v2_resume_paused_result_replaced",
                        extra={
                            "trajectory_id": trajectory.trajectory_id,
                            "step_number": entry.step_number,
                            "agent_id": result.agent_id,
                            "paused_message_id": paused_message_id,
                            "success": bool(task_result_text),
                        },
                    )
                    return

        logger.warning(
            "supervisor_v2_resume_no_matching_paused_result: could not find a "
            "PAUSED V2StepResult with agent_message_id=%s. "
            "The push notification result will be visible to the supervisor "
            "only via room memory.",
            paused_message_id,
        )

    @staticmethod
    def _find_paused_agent(
        trajectory: SupervisorTrajectory,
        paused_message_id: str,
    ) -> tuple[str | None, str | None]:
        """Return (agent_id, agent_name) for the agent that was paused.

        Matches by ``agent_message_id`` on PAUSED results (which are now
        preserved in the serialized trajectory).  Returns ``(None, None)``
        if not found.
        """
        for entry in trajectory.entries:
            for result in entry.results:
                if (
                    result.status == StepStatus.PAUSED
                    and result.agent_message_id == paused_message_id
                ):
                    return result.agent_id, result.agent_name
        return None, None

    async def _handle_v2_run_result(
        self,
        result: SupervisorRunResult,
        room_id: str,
        user_message_id: str,
        room=None,
    ) -> None:
        """Persist trajectory and emit SSE/synthesis for a V2 run result.

        Shared by ``_process_supervisor_v2`` and ``_resume_supervisor_v2``.
        """
        user_message = (
            await self.database_service.get_room_user_message_by_message_id(
                user_message_id
            )
        )
        if user_message and result.status in (
            RunStatus.COMPLETED,
            RunStatus.CLARIFYING,
            RunStatus.FAILED,
            RunStatus.CANCELED,
        ):
            if not isinstance(user_message.extend_info, dict):
                user_message.extend_info = {}
            user_message.extend_info["supervisor_trajectory"] = (
                result.trajectory.model_dump(mode="json")
            )
            await self.database_service.update_room_user_message_by_message_id(
                user_message_id, user_message
            )

        match result.status:
            case RunStatus.COMPLETED:
                if result.synthesis_text:
                    try:
                        await self.room_coordinator_service.emit_synthesis_message(
                            room_id=room_id,
                            room_user_message_id=user_message_id,
                            synthesis_text=result.synthesis_text,
                            coordinator_agent_id="supervisor_synthesis",
                        )
                    except Exception as e:
                        logger.error(
                            "RoomMessageCenter: V2 synthesis emission failed: %s",
                            e,
                            exc_info=True,
                        )
                await self.sse_manager.send_processing_status(
                    room_id, SSEProcessingStatus.COMPLETED, user_message_id
                )

            case RunStatus.PAUSED:
                pass

            case RunStatus.CLARIFYING:
                if room is None:
                    room = await self.database_service.get_room_by_room_id(room_id)
                if room:
                    if room.extend_info is None:
                        room.extend_info = {}
                    room.extend_info["pending_clarification_message_id"] = (
                        user_message_id
                    )
                    await self.database_service.update_room_by_room_id(
                        room_id, room
                    )
                if result.clarification_question:
                    try:
                        await self.room_coordinator_service.emit_synthesis_message(
                            room_id=room_id,
                            room_user_message_id=user_message_id,
                            synthesis_text=result.clarification_question,
                            coordinator_agent_id="supervisor_clarify",
                        )
                    except Exception as e:
                        logger.error(
                            "RoomMessageCenter: V2 clarification emission failed: %s",
                            e,
                            exc_info=True,
                        )
                await self.sse_manager.send_processing_status(
                    room_id, SSEProcessingStatus.COMPLETED, user_message_id
                )

            case RunStatus.CANCELED:
                await self.sse_manager.send_processing_status(
                    room_id, SSEProcessingStatus.CANCELED, user_message_id
                )
                self.sse_manager.clear_cancellation(user_message_id)

            case RunStatus.FAILED:
                await self.sse_manager.send_processing_status(
                    room_id,
                    SSEProcessingStatus.FAILED,
                    user_message_id,
                    details="V2 supervisor execution failed",
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

        Delegates the actual queue mechanics to ``QueueExecutor`` (V1) or
        ``SupervisorExecutor`` (V2) depending on the continuation data.

        For V2 supervisor rooms, the continuation data contains
        ``supervisor_v2: True``. The V2 resume path reconstructs the
        trajectory, refreshes the agent registry, and resumes the adaptive
        loop via ``_resume_supervisor_v2``.

        Returns ``True`` if the queue was resumed successfully.
        """
        # Peek at the continuation data to detect V2 before QueueExecutor
        # consumes it (get_and_clear is destructive). The V2 flag is checked
        # first; if present, we handle it here instead of delegating to the
        # V1 QueueExecutor path.
        continuation = (
            await self.database_service.get_and_clear_continuation_on_message(
                message_id
            )
        )
        if not continuation:
            logger.debug(
                "RoomMessageCenter: No continuation found for message %s",
                message_id,
            )
            return False

        if continuation.get("supervisor_v2"):
            try:
                return await self._resume_supervisor_v2(
                    continuation, message_id, task_result_text
                )
            except Exception:
                logger.exception(
                    "RoomMessageCenter: V2 resume failed — re-saving continuation "
                    "for message %s so it can be retried",
                    message_id,
                )
                await self.database_service.save_continuation_on_message(
                    message_id, continuation
                )
                return False

        # V1 path: re-save the continuation so QueueExecutor can read it
        # (we already consumed it with get_and_clear above).
        await self.database_service.save_continuation_on_message(
            message_id, continuation
        )

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
