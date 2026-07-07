"""SupervisorExecutor — adaptive step-at-a-time orchestration.

The sole orchestration executor for supervisor-enabled rooms (``use_supervisor``).
``QueueExecutor`` continues to serve non-supervisor rooms and fast-path cases
(direct chat, @mention routing).

Responsibilities:
- Drive the decide → dispatch → record cycle
- Create ``RoomAgentMessage`` records one at a time (no pre-generation)
- Handle push notification pauses (serialize trajectory for resume)
- Enforce cancellation, rate limits, and step budget
- Dispatch concurrent targets via ``asyncio.gather``

See docs/System-Architecture.md for design details.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from common.a2a_constants import SSEProcessingStatus
from common.config import settings as _settings
from common.message_commit_events import publish_message_committed
from common.utils.cancellation import CancellationError, CancellationToken
from common.utils.logger import get_logger
from common.utils.time import utcnow
from execution.orchestration.action_validator import (
    PlannerActionValidationError,
    PlannerActionValidator,
)
from execution.orchestration.candidate_scope import (
    candidate_scope_from_legacy_envelope,
    normalize_candidate_scope,
)
from execution.orchestration.context_builder import build_orchestration_planner_context
from execution.orchestration.debate_dispatcher import SequentialDebateDispatcher
from execution.orchestration.dispatch_payload import (
    DispatchPayloadValidationError,
    ResolvedDispatchPayload,
    resolve_dispatch_payload_refs,
)
from execution.orchestration.planner import (
    OrchestrationPlanner,
    RoomSupervisorPlannerAdapter,
)
from execution.orchestration.resources import OrchestrationResourceProvider
from execution.orchestration.run_reducer import mark_running, mark_terminal
from execution.orchestration.run_store import (
    InMemoryOrchestrationRunStore,
    OrchestrationRunStore,
    OrchestrationStoreConflict,
)
from execution.state.task_status_mapping import system_task_state_from_runtime_status
from models.hitl import HITLPromptType, InterruptKind
from models.orchestration import (
    TERMINAL_ORCHESTRATION_STATUSES,
    AgentOutputRecord,
    DispatchIntent,
    OrchestrationEventType,
    OrchestrationRunEvent,
    OrchestrationRunState,
    OrchestrationStatus,
    PlannerAction,
    PlannerActionType,
)
from models.processing import ProcessingStatus
from models.room import CoordinatorAgentId, UserAttachment
from models.supervisor import (
    ActionType,
    AgentProfile,
    ClarifyQuestion,
    DelegateTarget,
    RoomConfig,
    RunStatus,
    StepResult,
    StepStatus,
    SupervisorAction,
    SupervisorRunResult,
    SupervisorTrajectory,
    TrajectoryEntry,
    TrajectoryStatus,
)

if TYPE_CHECKING:
    from common.protocols import EventPublisher
    from execution.dispatch.agent_dispatcher import AgentDispatcher
    from execution.dispatch.agent_message_processor import AgentMessageProcessor
    from execution.orchestration.room_supervisor_service import RoomSupervisorService
    from execution.ports import (
        ExecutionDeliveryPort,
        HITLCoordinator,
        RateLimitPort,
        RoomContinuationStore,
        RoomMessageReader,
        RoomMessageWriter,
        RoomRuntimePort,
        RoomTaskStateStore,
    )
    from execution.state.task_state_manager import TaskStateManager

logger = get_logger(__name__)


class _SupervisorSettings:
    debate_rounds = 2


settings = _SupervisorSettings()


class SupervisorExecutor:
    """Executes the Supervisor's adaptive loop for a single user message."""

    MAX_STEPS: int = _settings.supervisor_max_steps

    def __init__(
        self,
        *,
        supervisor_service: RoomSupervisorService,
        room_runtime: RoomRuntimePort,
        tsm: TaskStateManager,
        delivery: ExecutionDeliveryPort,
        message_reader: RoomMessageReader,
        message_writer: RoomMessageWriter,
        task_state_store: RoomTaskStateStore,
        continuation_store: RoomContinuationStore,
        event_publisher: EventPublisher,
        rate_limit_service: RateLimitPort | None = None,
        agent_dispatcher: AgentDispatcher,
        agent_message_processor: AgentMessageProcessor,
        slot_lifecycle=None,
        hitl_coordinator: HITLCoordinator | None = None,
        debate_rounds: int = 2,
        orchestration_run_store: OrchestrationRunStore | None = None,
        orchestration_planner: OrchestrationPlanner | None = None,
        orchestration_resource_provider: OrchestrationResourceProvider | None = None,
    ) -> None:
        if event_publisher is None:
            raise RuntimeError("SupervisorExecutor event_publisher dependency is required")
        self.supervisor_service = supervisor_service
        self.room_runtime = room_runtime
        self.tsm = tsm
        self.delivery = delivery
        self.message_reader = message_reader
        self.message_writer = message_writer
        self.task_state_store = task_state_store
        self.continuation_store = continuation_store
        self.event_publisher = event_publisher
        self.rate_limit_service = rate_limit_service
        self.agent_dispatcher = agent_dispatcher
        self.agent_message_processor = agent_message_processor
        self._slot_lifecycle = slot_lifecycle
        self.hitl_coordinator = hitl_coordinator
        self.debate_rounds = debate_rounds
        self.orchestration_run_store = (
            orchestration_run_store or InMemoryOrchestrationRunStore()
        )
        self.orchestration_planner = orchestration_planner or RoomSupervisorPlannerAdapter(
            supervisor_service=supervisor_service
        )
        self.orchestration_resource_provider = (
            orchestration_resource_provider or OrchestrationResourceProvider()
        )
        self._processing_status_emitter = None

    def bind_execution_event_deps(self, processing_status_emitter) -> None:
        self._processing_status_emitter = processing_status_emitter

    @staticmethod
    def _run_status_from_orchestration_status(
        status: OrchestrationStatus,
    ) -> RunStatus:
        if status == OrchestrationStatus.COMPLETED:
            return RunStatus.COMPLETED
        if status == OrchestrationStatus.CANCELED:
            return RunStatus.CANCELED
        if status == OrchestrationStatus.AWAITING_USER:
            return RunStatus.AWAITING_INPUT
        if status == OrchestrationStatus.WAITING_AGENT:
            return RunStatus.PAUSED
        return RunStatus.FAILED

    @staticmethod
    def _state_run_result(
        *,
        status: RunStatus,
        state: OrchestrationRunState,
        synthesis_text: str | None = None,
        clarification_question: str | None = None,
    ) -> SupervisorRunResult:
        return SupervisorRunResult(
            status=status,
            trajectory=None,
            run_id=state.run_id,
            run_state=state,
            synthesis_text=synthesis_text,
            clarification_question=clarification_question,
            terminal_reason=state.terminal_reason,
        )

    @staticmethod
    def _compat_trajectory_from_state(
        state: OrchestrationRunState,
    ) -> SupervisorTrajectory:
        trajectory = SupervisorTrajectory()
        trajectory.system_agent_message_id = (
            state.system_agent_message_id or state.summary_message_id
        )
        if state.participant_snapshot is not None:
            trajectory.debate_agent_ids = list(
                state.participant_snapshot.ordered_agent_ids
            )
        if state.status == OrchestrationStatus.COMPLETED:
            trajectory.status = TrajectoryStatus.COMPLETED
        elif state.status == OrchestrationStatus.CANCELED:
            trajectory.status = TrajectoryStatus.CANCELED
        elif state.status == OrchestrationStatus.AWAITING_USER:
            trajectory.status = TrajectoryStatus.AWAITING_INPUT
        elif state.status in (
            OrchestrationStatus.FAILED,
            OrchestrationStatus.BUDGET_EXHAUSTED,
        ):
            trajectory.status = TrajectoryStatus.FAILED

        agent_names = SupervisorExecutor._state_agent_names(state)
        outputs_by_message_id = {
            output.agent_message_id: output for output in state.agent_outputs
        }
        projected_message_ids: set[str] = set()
        intents_by_step: dict[str, list[DispatchIntent]] = {}
        for intent in state.dispatch_intents:
            intents_by_step.setdefault(intent.step_id, []).append(intent)

        for step_id, intents in intents_by_step.items():
            results: list[StepResult] = []
            for intent in intents:
                output = outputs_by_message_id.get(intent.planned_agent_message_id)
                if output is None:
                    continue
                result = SupervisorExecutor._step_result_from_state_output(
                    output=output,
                    intent=intent,
                    step_number=SupervisorExecutor._step_number_from_step_id(step_id),
                    agent_names=agent_names,
                )
                if result is None:
                    continue
                results.append(result)
                projected_message_ids.add(output.agent_message_id)
            if not results:
                continue
            trajectory.entries.append(
                TrajectoryEntry(
                    step_number=SupervisorExecutor._step_number_from_step_id(step_id),
                    action=SupervisorAction(
                        action=ActionType.DELEGATE,
                        reasoning="Projected from orchestration run state",
                        targets=[
                            DelegateTarget(
                                agent_id=intent.agent_id,
                                agent_name=agent_names.get(intent.agent_id)
                                or intent.agent_id,
                                task=intent.task,
                            )
                            for intent in intents
                        ],
                    ),
                    results=results,
                    started_at=utcnow(),
                    completed_at=utcnow(),
                )
            )

        next_step_number = (
            max((entry.step_number for entry in trajectory.entries), default=0) + 1
        )
        for output in state.agent_outputs:
            if output.agent_message_id in projected_message_ids:
                continue
            result = SupervisorExecutor._step_result_from_state_output(
                output=output,
                intent=None,
                step_number=next_step_number,
                agent_names=agent_names,
            )
            if result is None:
                continue
            trajectory.entries.append(
                TrajectoryEntry(
                    step_number=next_step_number,
                    action=SupervisorAction(
                        action=ActionType.DELEGATE,
                        reasoning="Projected orphan agent output from orchestration run state",
                        targets=[
                            DelegateTarget(
                                agent_id=result.agent_id,
                                agent_name=result.agent_name,
                                task=result.task,
                            )
                        ],
                    ),
                    results=[result],
                    started_at=utcnow(),
                    completed_at=utcnow(),
                )
            )
            next_step_number += 1
        return trajectory

    @staticmethod
    def _state_agent_names(state: OrchestrationRunState) -> dict[str, str]:
        names: dict[str, str] = {}
        if state.candidate_scope is not None:
            for candidate in state.candidate_scope.agents:
                names[candidate.agent_id] = candidate.name or candidate.agent_id
        for output in state.agent_outputs:
            names.setdefault(output.agent_id, output.agent_id)
        for intent in state.dispatch_intents:
            names.setdefault(intent.agent_id, intent.agent_id)
        return names

    @staticmethod
    def _step_number_from_step_id(step_id: str) -> int:
        for part in step_id.split(":"):
            if not part.startswith("step-"):
                continue
            try:
                return int(part.removeprefix("step-"))
            except ValueError:
                return 1
        return 1

    @staticmethod
    def _step_result_from_state_output(
        *,
        output: AgentOutputRecord,
        intent: DispatchIntent | None,
        step_number: int,
        agent_names: dict[str, str],
    ) -> StepResult | None:
        try:
            status = StepStatus(output.status)
        except ValueError:
            return None
        agent_id = output.agent_id or (intent.agent_id if intent else "")
        if not agent_id:
            return None
        task = intent.task if intent is not None else "Agent response"
        return StepResult(
            step_number=step_number,
            agent_id=agent_id,
            agent_name=agent_names.get(agent_id) or agent_id,
            task=task,
            response_text=output.text or "",
            success=status == StepStatus.SUCCESS,
            status=status,
            error_message=output.error,
            agent_message_id=output.agent_message_id,
            completed_at=utcnow(),
        )

    async def _log_state_and_return(
        self,
        room_id: str,
        state: OrchestrationRunState,
        result: SupervisorRunResult,
    ) -> SupervisorRunResult:
        logger.info(
            "supervisor_run_completed",
            extra={
                "room_id": room_id,
                "run_id": state.run_id,
                "status": result.status,
                "steps_used": state.steps_used,
            },
        )

        system_message_id = state.system_agent_message_id or state.summary_message_id
        if system_message_id and result.status != RunStatus.PAUSED:
            try:
                task_status = (
                    "completed"
                    if result.status == RunStatus.COMPLETED
                    else result.status.value
                )
                await self.delivery.send_task_update(
                    room_id=room_id,
                    message_id=system_message_id,
                    status=task_status,
                )

                db_msg = await self.message_reader.get_room_agent_message_by_message_id(
                    system_message_id
                )
                if (
                    db_msg
                    and db_msg.message_content
                    and db_msg.message_content.message_task
                ):
                    db_msg.message_content.message_task.status.state = (
                        system_task_state_from_runtime_status(task_status)
                    )
                    await self.message_writer.update_room_agent_message_with_new_message_content_by_message_id(
                        db_msg.message_id,
                        db_msg.message_content,
                    )
            except Exception:
                logger.warning(
                    "Failed to update terminal state for system:hybro", exc_info=True
                )
        return result

    @property
    def run_store(self) -> OrchestrationRunStore:
        if not hasattr(self, "orchestration_run_store"):
            self.orchestration_run_store = InMemoryOrchestrationRunStore()
        return self.orchestration_run_store

    @run_store.setter
    def run_store(self, value: OrchestrationRunStore) -> None:
        self.orchestration_run_store = value

    async def _publish_agent_message_committed(
        self,
        *,
        room_id: str,
        message_id: str | None,
        agent_id: str | None,
        agent_name: str,
        was_successful: bool,
    ) -> None:
        if not message_id:
            return
        await publish_message_committed(
            self.event_publisher,
            room_id=room_id,
            message_id=message_id,
            message_type="agent",
            agent_id=agent_id,
            agent_name=agent_name,
            was_successful=was_successful,
        )

    async def _emit_processing_status(
        self,
        *,
        room_id: str,
        status,
        message_id: str | None,
        lifecycle_message_id: str | None = None,
        record_lifecycle: bool = True,
        client_request_id: str | None = None,
        details=None,
        agents: list[dict] | None = None,
    ) -> None:
        if self._processing_status_emitter is None:
            raise RuntimeError(
                "SupervisorExecutor execution event dependencies not bound"
            )
        status_value = status.value if hasattr(status, "value") else str(status)
        await self._processing_status_emitter(
            room_id=room_id,
            status=status,
            message_id=message_id,
            lifecycle_message_id=lifecycle_message_id or message_id,
            record_lifecycle=record_lifecycle,
            client_request_id=client_request_id,
            details=(
                details
                if isinstance(details, dict)
                else {"message": details}
                if isinstance(details, str)
                else None
            ),
            error_message=(
                details
                if isinstance(details, str)
                and status_value in {"failed", "canceled", "rejected", "error"}
                else None
            ),
            agents=agents,
        )

    async def _stream_supervisor_synthesis(
        self,
        *,
        room_id: str,
        user_message_id: str,
        trajectory: SupervisorTrajectory,
        synthesis_instruction: str,
        client_request_id: str | None,
    ) -> str:
        """Run supervisor LLM synthesis with live artifact_update streaming."""
        from common.utils.summary_streaming import stream_summary_to_sse

        summary_message_id = trajectory.system_agent_message_id
        if not summary_message_id:
            summary_message_id = f"sys-{user_message_id}"

        return await stream_summary_to_sse(
            self.delivery,
            room_id=room_id,
            message_id=summary_message_id,
            agent_id=CoordinatorAgentId.SYSTEM_HYBRO,
            token_stream=self.supervisor_service.synthesize_stream(
                trajectory=trajectory,
                synthesis_instruction=synthesis_instruction,
            ),
            client_request_id=client_request_id,
        )

    # ------------------------------------------------------------------
    # State-driven orchestration loop
    # ------------------------------------------------------------------

    async def _execute_orchestration_loop(
        self,
        *,
        state: OrchestrationRunState,
        room_id: str,
        user_message_id: str,
        message_text: str,
        agent_registry: list[AgentProfile],
        room_config: RoomConfig,
        conversation_context: str | None = None,
        token: CancellationToken | None = None,
        request_user_id: str | None = None,
        quoted_text: str | None = None,
        user_message=None,
    ) -> SupervisorRunResult:
        """Execute the supervisor loop using persisted orchestration run state."""

        terminal_result = await self._v2_terminal_result_if_done(room_id, state)
        if terminal_result is not None:
            return terminal_result
        if (
            state.status == OrchestrationStatus.AWAITING_USER
            and state.pending_hitl_request_ids
        ):
            return await self._log_state_and_return(
                room_id,
                state,
                self._state_run_result(
                    status=RunStatus.AWAITING_INPUT,
                    state=state,
                ),
            )

        state = await self._ensure_v2_running_state(state)
        state = await self._ensure_v2_system_task(
            room_id=room_id,
            user_message_id=user_message_id,
            request_user_id=request_user_id,
            state=state,
        )
        state, recovered_status = await self._recover_v2_inflight_dispatch(
            state=state,
            agent_registry=agent_registry,
            room_config=room_config,
            room_id=room_id,
            user_message_id=user_message_id,
            message_text=message_text,
            conversation_context=conversation_context,
            token=token,
            request_user_id=request_user_id,
            quoted_text=quoted_text,
            user_message=user_message,
        )
        if recovered_status is not None:
            return await self._log_state_and_return(
                room_id,
                state,
                self._state_run_result(
                    status=recovered_status,
                    state=state,
                ),
            )

        while state.steps_used <= state.step_budget:
            if token and token.is_cancelled:
                state = await self._mark_v2_terminal(
                    state,
                    OrchestrationStatus.CANCELED,
                    reason="request canceled",
                )
                return await self._log_state_and_return(
                    room_id,
                    state,
                    self._state_run_result(
                        status=RunStatus.CANCELED,
                        state=state,
                    ),
                )

            if not (token and token.is_cancelled):
                try:
                    await self._emit_processing_status(
                        room_id=room_id,
                        status=SSEProcessingStatus.PROCESSING,
                        message_id=user_message_id,
                        lifecycle_message_id=user_message_id,
                        details="Planning next action...",
                    )
                except Exception:
                    logger.debug(
                        "SSE stage notification failed (planning)", exc_info=True
                    )

            original_attachments = self._user_attachments_from_message(user_message)
            available_resources = (
                await self.orchestration_resource_provider.list_resources(
                    run_id=state.run_id,
                    room_id=room_id,
                    user_message_id=user_message_id,
                    attachments=original_attachments,
                    candidate_agents=self._v2_candidate_scope(state, agent_registry),
                )
            )
            logger.info(
                "orchestration_resource_catalog_built room_id=%s run_id=%s "
                "user_message_id=%s resource_count=%d",
                room_id,
                state.run_id,
                user_message_id,
                len(available_resources),
            )
            context = build_orchestration_planner_context(
                run_state=state,
                candidate_scope=self._v2_candidate_scope(state, agent_registry),
                message_text=message_text,
                quote=quoted_text,
                room_background=conversation_context,
                available_resources=available_resources,
            )
            await self._append_v2_event(
                state,
                OrchestrationEventType.PLANNER_CONTEXT_BUILT,
                payload={
                    "candidate_agent_ids": context.candidate_agent_ids,
                    "steps_used": state.steps_used,
                },
            )

            planner = getattr(self, "orchestration_planner", None)
            if planner is None:
                planner = RoomSupervisorPlannerAdapter(
                    supervisor_service=self.supervisor_service
                )
                self.orchestration_planner = planner
            plan_coro = planner.plan(context)
            try:
                planner_action = (
                    await token.race(plan_coro) if token else await plan_coro
                )
            except CancellationError:
                state = await self._mark_v2_terminal(
                    state,
                    OrchestrationStatus.CANCELED,
                    reason="request canceled",
                )
                return await self._log_state_and_return(
                    room_id,
                    state,
                    self._state_run_result(
                        status=RunStatus.CANCELED,
                        state=state,
                    ),
                )
            except (PlannerActionValidationError, ValueError) as exc:
                state = await self._mark_v2_terminal(
                    state,
                    OrchestrationStatus.FAILED,
                    reason=str(exc),
                )
                return await self._log_state_and_return(
                    room_id,
                    state,
                    self._state_run_result(
                        status=RunStatus.FAILED,
                        state=state,
                    ),
                )

            try:
                planner_action = PlannerActionValidator.validate(
                    planner_action,
                    run_state=state,
                )
            except PlannerActionValidationError as exc:
                state = await self._mark_v2_terminal(
                    state,
                    OrchestrationStatus.FAILED,
                    reason=str(exc),
                )
                return await self._log_state_and_return(
                    room_id,
                    state,
                    self._state_run_result(
                        status=RunStatus.FAILED,
                        state=state,
                    ),
                )
            state = await self._record_v2_planner_action(state, planner_action)

            match planner_action.action:
                case PlannerActionType.DELEGATE:
                    state, paused_status = await self._run_delegate_action(
                        state=state,
                        planner_action=planner_action,
                        agent_registry=agent_registry,
                        room_config=room_config,
                        room_id=room_id,
                        user_message_id=user_message_id,
                        message_text=message_text,
                        conversation_context=conversation_context,
                        token=token,
                        request_user_id=request_user_id,
                        quoted_text=quoted_text,
                        user_message=user_message,
                    )
                    if paused_status is not None:
                        return await self._log_state_and_return(
                            room_id,
                            state,
                            self._state_run_result(
                                status=paused_status,
                                state=state,
                            ),
                        )

                case PlannerActionType.SYNTHESIZE:
                    return await self._run_synthesis_action(
                        state=state,
                        planner_action=planner_action,
                        room_id=room_id,
                        user_message_id=user_message_id,
                        token=token,
                    )

                case PlannerActionType.COMPLETE:
                    state = await self._mark_v2_terminal(
                        state,
                        OrchestrationStatus.COMPLETED,
                        reason=planner_action.reasoning,
                    )
                    return await self._log_state_and_return(
                        room_id,
                        state,
                        self._state_run_result(
                            status=RunStatus.COMPLETED,
                            state=state,
                        ),
                    )

                case PlannerActionType.ASK_USER:
                    result = await self._run_ask_user_action(
                        state=state,
                        planner_action=planner_action,
                        agent_registry=agent_registry,
                        room_config=room_config,
                        room_id=room_id,
                        user_message_id=user_message_id,
                        message_text=message_text,
                        conversation_context=conversation_context,
                        request_user_id=request_user_id,
                        quoted_text=quoted_text,
                    )
                    return result

                case PlannerActionType.FAIL:
                    state = await self._mark_v2_terminal(
                        state,
                        OrchestrationStatus.FAILED,
                        reason=(
                            planner_action.failure_reason
                            or planner_action.reasoning
                            or "planner failed the run"
                        ),
                    )
                    return await self._log_state_and_return(
                        room_id,
                        state,
                        self._state_run_result(
                            status=RunStatus.FAILED,
                            state=state,
                        ),
                    )

        state = await self._mark_v2_terminal(
            state,
            OrchestrationStatus.BUDGET_EXHAUSTED,
            reason="step budget exhausted",
        )
        return await self._log_state_and_return(
            room_id,
            state,
            self._state_run_result(status=RunStatus.FAILED, state=state),
        )

    @staticmethod
    def _step_budget_from_request(
        user_message=None,
        room_config: RoomConfig | None = None,
    ) -> int:
        extend_info = getattr(user_message, "extend_info", None)
        if isinstance(extend_info, dict):
            value = extend_info.get("orchestration_step_budget")
            if isinstance(value, int) and value > 0:
                return value
        value = getattr(room_config, "orchestration_step_budget", None)
        if isinstance(value, int) and value > 0:
            return value
        return 8

    async def _load_or_create_run_state_for_run(
        self,
        *,
        room_id: str,
        user_message_id: str,
        message_text: str,
        agent_registry: list[AgentProfile],
        room_config: RoomConfig | None = None,
        user_message=None,
    ) -> OrchestrationRunState:
        existing = await self.run_store.get_run(user_message_id)
        if existing is not None:
            return existing

        envelope = self._orchestration_envelope_from_user_message(user_message)
        state = await self.run_store.reconstruct_from_envelope(
            run_id=user_message_id,
            room_id=room_id,
            user_message_id=user_message_id,
            envelope=envelope,
            goal=message_text,
        )
        if agent_registry:
            state.candidate_scope = candidate_scope_from_legacy_envelope(
                room_id=room_id,
                envelope=envelope,
                selected_agent_set=agent_registry,
            )
            state.candidate_agent_ids = list(state.candidate_scope.agent_ids)
        elif state.candidate_scope is None:
            state.candidate_scope = normalize_candidate_scope(
                room_id=room_id,
                source="room_default",
                selected_agent_set=[],
            )
            state.candidate_agent_ids = []
        state.step_budget = self._step_budget_from_request(user_message, room_config)
        try:
            return await self.run_store.create_run(state)
        except OrchestrationStoreConflict:
            existing = await self.run_store.get_run(user_message_id)
            if existing is not None:
                return existing
            raise

    async def _ensure_v2_running_state(
        self,
        state: OrchestrationRunState,
    ) -> OrchestrationRunState:
        needs_update = (
            state.status != OrchestrationStatus.RUNNING
            or not state.summary_intent_id
            or not state.summary_message_id
        )
        if not needs_update:
            return state

        expected_version = state.state_version
        updated = mark_running(state)
        if not updated.summary_intent_id:
            updated.summary_intent_id = f"{updated.run_id}:summary"
        if not updated.summary_message_id:
            updated.summary_message_id = f"sys-{updated.user_message_id}"
        saved = await self.orchestration_run_store.save_state(
            updated,
            expected_version=expected_version,
        )
        await self._append_v2_event(
            saved,
            OrchestrationEventType.STATE_REDUCED,
            payload={"status": saved.status.value},
        )
        return saved

    async def _ensure_v2_system_task(
        self,
        *,
        room_id: str,
        user_message_id: str,
        request_user_id: str | None,
        state: OrchestrationRunState,
    ) -> OrchestrationRunState:
        if state.system_agent_message_id:
            return state

        sys_message_id = state.summary_message_id or f"sys-{user_message_id}"
        state = await self._save_v2_state(
            state,
            event_type=OrchestrationEventType.STATE_REDUCED,
            payload={"system_agent_message_id": sys_message_id},
            mutate=lambda updated: setattr(
                updated,
                "system_agent_message_id",
                sys_message_id,
            ),
        )
        try:
            sys_msg = self.room_runtime.create_agent_message(
                room_id=room_id,
                related_message_id=user_message_id,
                agent_id=CoordinatorAgentId.SYSTEM_HYBRO,
                content="",
                user_id=request_user_id,
                step_number=0,
                task_content="Orchestrating workflow...",
                client_request_id=state.client_request_id,
            )
            sys_msg.message_id = sys_message_id
            await self.message_writer.add_room_agent_message(sys_msg)
            await self.delivery.send_task_submitted(
                room_id=room_id,
                message_id=sys_message_id,
                task_id=sys_message_id,
                agent_name="HYBRO AI",
                agent_id=CoordinatorAgentId.SYSTEM_HYBRO,
                status="working",
                related_message_id=user_message_id,
                created_at=utcnow().isoformat(),
                task_content="Orchestrating workflow...",
                client_request_id=state.client_request_id,
            )
        except Exception:
            logger.warning("Failed to emit v2 system:hybro task", exc_info=True)
        return state

    async def _record_v2_planner_action(
        self,
        state: OrchestrationRunState,
        planner_action: PlannerAction,
    ) -> OrchestrationRunState:
        def mutate(updated: OrchestrationRunState) -> None:
            updated.decision_log.append(
                {
                    "action": planner_action.action.value,
                    "reasoning": planner_action.reasoning,
                    "targets": [
                        target.model_dump(mode="json")
                        for target in planner_action.targets
                    ],
                    "planner_action": planner_action.model_dump(mode="json"),
                }
            )

        saved = await self._save_v2_state(
            state,
            event_type=OrchestrationEventType.PLANNER_ACTION_PROPOSED,
            payload=planner_action.model_dump(mode="json"),
            mutate=mutate,
        )
        return saved

    @staticmethod
    def _v2_pending_hitl_planner_action(
        state: OrchestrationRunState,
    ) -> PlannerAction | None:
        for decision in reversed(state.decision_log):
            if decision.get("action") != PlannerActionType.ASK_USER.value:
                continue
            raw_action = decision.get("planner_action")
            if not isinstance(raw_action, dict):
                return None
            try:
                action = PlannerAction.model_validate(raw_action)
            except (TypeError, ValueError):
                return None
            return action if action.action == PlannerActionType.ASK_USER else None
        return None

    async def _run_delegate_action(
        self,
        *,
        state: OrchestrationRunState,
        planner_action: PlannerAction,
        agent_registry: list[AgentProfile],
        room_config: RoomConfig,
        room_id: str,
        user_message_id: str,
        message_text: str,
        conversation_context: str | None,
        token: CancellationToken | None,
        request_user_id: str | None,
        quoted_text: str | None,
        user_message,
    ) -> tuple[OrchestrationRunState, RunStatus | None]:
        trajectory = self._compat_trajectory_from_state(state)
        action = self._v2_supervisor_action(planner_action, agent_registry)
        step_number = state.steps_used + 1
        entry = TrajectoryEntry(
            step_number=step_number,
            action=action,
            started_at=utcnow(),
        )
        trajectory.entries.append(entry)

        intents = [
            self._v2_dispatch_intent(
                run_id=state.run_id,
                step_number=step_number,
                target_index=index,
                target=target,
            )
            for index, target in enumerate(action.targets, start=1)
        ]

        state = await self._save_v2_state(
            state,
            event_type=OrchestrationEventType.DISPATCH_INTENT_RECORDED,
            payload={"dispatch_intent_ids": [intent.dispatch_intent_id for intent in intents]},
            mutate=lambda updated: self._apply_v2_dispatch_intents(updated, intents),
        )

        results = await self._dispatch_targets(
            targets=action.targets,
            agent_registry=agent_registry,
            room_id=room_id,
            user_message_id=user_message_id,
            step_number=step_number,
            token=token,
            request_user_id=request_user_id,
            quoted_text=quoted_text,
            planned_message_ids=[
                intent.planned_agent_message_id for intent in intents
            ],
            run_state=state,
            original_attachments=self._user_attachments_from_message(user_message),
        )
        entry.results = results
        entry.completed_at = utcnow()

        for result in results:
            if result.status == StepStatus.SUCCESS and result.success:
                await self._publish_agent_message_committed(
                    room_id=room_id,
                    agent_id=result.agent_id,
                    agent_name=result.agent_name or "Agent",
                    was_successful=True,
                    message_id=result.agent_message_id,
                )

        paused = [result for result in results if result.status == StepStatus.PAUSED]
        awaiting = [
            result for result in results if result.status == StepStatus.AWAITING_INPUT
        ]
        if paused:
            await self._reconcile_paused_results(paused, trajectory, room_id)
            results = entry.results
            paused = [
                result for result in results if result.status == StepStatus.PAUSED
            ]
            awaiting = [
                result
                for result in results
                if result.status == StepStatus.AWAITING_INPUT
            ]

        if awaiting:
            trajectory.status = TrajectoryStatus.AWAITING_INPUT
            if paused:
                saved = await self._save_interrupted_state(
                    kind=InterruptKind.PUSH_NOTIFICATION,
                    trajectory=trajectory,
                    paused_results=paused,
                    room_id=room_id,
                    user_message_id=user_message_id,
                    request_user_id=request_user_id,
                    message_text=message_text,
                    agent_registry=agent_registry,
                    room_config=room_config,
                    conversation_context=conversation_context,
                    quoted_text=quoted_text,
                )
                if not saved:
                    trajectory.status = TrajectoryStatus.FAILED
                    state = await self._mark_v2_terminal(
                        state,
                        OrchestrationStatus.FAILED,
                        reason="failed to save paused v2 continuation",
                    )
                    return state, RunStatus.FAILED
            state, awaiting_status = await self._run_agent_awaiting_input_action(
                state=state,
                results=results,
                awaiting=awaiting,
                trajectory=trajectory,
                agent_registry=agent_registry,
                room_config=room_config,
                room_id=room_id,
                user_message_id=user_message_id,
                message_text=message_text,
                conversation_context=conversation_context,
                request_user_id=request_user_id,
                quoted_text=quoted_text,
            )
            return state, awaiting_status

        if paused:
            trajectory.status = TrajectoryStatus.RUNNING
            saved = await self._save_interrupted_state(
                kind=InterruptKind.PUSH_NOTIFICATION,
                trajectory=trajectory,
                paused_results=paused,
                room_id=room_id,
                user_message_id=user_message_id,
                request_user_id=request_user_id,
                message_text=message_text,
                agent_registry=agent_registry,
                room_config=room_config,
                conversation_context=conversation_context,
                quoted_text=quoted_text,
            )
            if not saved:
                trajectory.status = TrajectoryStatus.FAILED
                state = await self._mark_v2_terminal(
                    state,
                    OrchestrationStatus.FAILED,
                    reason="failed to save paused v2 continuation",
                )
                return state, RunStatus.FAILED
            state = await self._save_v2_state(
                state,
                event_type=OrchestrationEventType.STATE_REDUCED,
                payload={"status": OrchestrationStatus.WAITING_AGENT.value},
                mutate=lambda updated: self._apply_v2_results(
                    updated,
                    results,
                    status=OrchestrationStatus.WAITING_AGENT,
                    advance_step=False,
                ),
            )
            return state, RunStatus.PAUSED

        state = await self._save_v2_state(
            state,
            event_type=OrchestrationEventType.AGENT_RESULT_INGESTED,
            payload={"agent_message_ids": [result.agent_message_id for result in results]},
            mutate=lambda updated: self._apply_v2_results(
                updated,
                results,
                status=OrchestrationStatus.RUNNING,
                advance_step=True,
            ),
        )
        return state, None

    async def _recover_v2_inflight_dispatch(
        self,
        *,
        state: OrchestrationRunState,
        agent_registry: list[AgentProfile],
        room_config: RoomConfig,
        room_id: str,
        user_message_id: str,
        message_text: str,
        conversation_context: str | None,
        token: CancellationToken | None,
        request_user_id: str | None,
        quoted_text: str | None,
        user_message,
    ) -> tuple[OrchestrationRunState, RunStatus | None]:
        trajectory = self._compat_trajectory_from_state(state)
        step_number = state.steps_used + 1
        step_id = f"{state.run_id}:step-{step_number}"
        terminal_statuses = {
            StepStatus.SUCCESS.value,
            StepStatus.FAILED.value,
            "completed",
            "failed",
            "canceled",
            "rejected",
        }
        current_intents = [
            intent
            for intent in state.dispatch_intents
            if intent.step_id == step_id and intent.status not in terminal_statuses
        ]
        if not current_intents:
            return state, None

        agent_names = {agent.agent_id: agent.agent_name for agent in agent_registry}
        outputs_by_message_id = {
            output.agent_message_id: output for output in state.agent_outputs
        }
        results: list[StepResult] = []
        replay_intents: list[DispatchIntent] = []
        unresolved = False

        for intent in current_intents:
            result = self._v2_result_from_output_record(
                intent,
                outputs_by_message_id.get(intent.planned_agent_message_id),
                agent_names,
                step_number,
            )
            if result is None:
                result = await self._v2_result_from_committed_agent_message(
                    intent,
                    agent_names,
                    step_number,
                )
            if result is None:
                msg = await self.message_reader.get_room_agent_message_by_message_id(
                    intent.planned_agent_message_id
                )
                result = self._v2_result_from_agent_message(
                    intent,
                    msg,
                    agent_names,
                    step_number,
                )
                if msg is None and intent.status == "planned":
                    replay_intents.append(intent)
                elif result is not None:
                    results.append(result)
                else:
                    unresolved = True
            else:
                results.append(result)

        if replay_intents:
            replay_targets = [
                DelegateTarget(
                    agent_id=intent.agent_id,
                    agent_name=agent_names.get(intent.agent_id) or intent.agent_id,
                    task=intent.task,
                    context_refs=list(intent.context_refs),
                    artifact_refs=list(intent.artifact_refs),
                    attachment_refs=list(intent.attachment_refs),
                    expected_outputs=list(intent.expected_outputs),
                    attachment_policy=intent.attachment_policy,
                )
                for intent in replay_intents
            ]
            replay_results = await self._dispatch_targets(
                targets=replay_targets,
                agent_registry=agent_registry,
                room_id=room_id,
                user_message_id=user_message_id,
                step_number=step_number,
                token=token,
                request_user_id=request_user_id,
                quoted_text=quoted_text,
                planned_message_ids=[
                    intent.planned_agent_message_id for intent in replay_intents
                ],
                run_state=state,
                original_attachments=self._user_attachments_from_message(user_message),
            )
            results.extend(replay_results)

        if unresolved:
            if results:
                state = await self._save_v2_state(
                    state,
                    event_type=OrchestrationEventType.AGENT_RESULT_INGESTED,
                    payload={
                        "agent_message_ids": [
                            result.agent_message_id for result in results
                        ]
                    },
                    mutate=lambda updated: self._apply_v2_results(
                        updated,
                        results,
                        status=OrchestrationStatus.WAITING_AGENT,
                        advance_step=False,
                    ),
                )
            if state.status != OrchestrationStatus.WAITING_AGENT:
                state = await self._save_v2_state(
                    state,
                    event_type=OrchestrationEventType.STATE_REDUCED,
                    payload={"status": OrchestrationStatus.WAITING_AGENT.value},
                    mutate=lambda updated: setattr(
                        updated,
                        "status",
                        OrchestrationStatus.WAITING_AGENT,
                    ),
                )
            return state, RunStatus.PAUSED

        if not results:
            return state, None

        paused = [result for result in results if result.status == StepStatus.PAUSED]
        awaiting = [
            result for result in results if result.status == StepStatus.AWAITING_INPUT
        ]
        if awaiting:
            trajectory.status = TrajectoryStatus.AWAITING_INPUT
            state, awaiting_status = await self._run_agent_awaiting_input_action(
                state=state,
                results=results,
                awaiting=awaiting,
                trajectory=trajectory,
                agent_registry=agent_registry,
                room_config=room_config,
                room_id=room_id,
                user_message_id=user_message_id,
                message_text=message_text,
                conversation_context=conversation_context,
                request_user_id=request_user_id,
                quoted_text=quoted_text,
            )
            return state, awaiting_status

        if paused:
            trajectory.status = TrajectoryStatus.RUNNING
            saved = await self._save_interrupted_state(
                kind=InterruptKind.PUSH_NOTIFICATION,
                trajectory=trajectory,
                paused_results=paused,
                room_id=room_id,
                user_message_id=user_message_id,
                request_user_id=request_user_id,
                message_text=message_text,
                agent_registry=agent_registry,
                room_config=room_config,
                conversation_context=conversation_context,
                quoted_text=quoted_text,
            )
            if not saved:
                trajectory.status = TrajectoryStatus.FAILED
                state = await self._mark_v2_terminal(
                    state,
                    OrchestrationStatus.FAILED,
                    reason="failed to save paused v2 recovery continuation",
                )
                return state, RunStatus.FAILED
            state = await self._save_v2_state(
                state,
                event_type=OrchestrationEventType.STATE_REDUCED,
                payload={"status": OrchestrationStatus.WAITING_AGENT.value},
                mutate=lambda updated: self._apply_v2_results(
                    updated,
                    results,
                    status=OrchestrationStatus.WAITING_AGENT,
                    advance_step=False,
                ),
            )
            return state, RunStatus.PAUSED

        entry = self._v2_recovered_trajectory_entry(
            step_number=step_number,
            intents=current_intents,
            results=results,
            agent_names=agent_names,
        )
        trajectory.entries.append(entry)

        for result in results:
            if result.status == StepStatus.SUCCESS and result.success:
                await self._publish_agent_message_committed(
                    room_id=room_id,
                    agent_id=result.agent_id,
                    agent_name=result.agent_name or "Agent",
                    was_successful=True,
                    message_id=result.agent_message_id,
                )

        state = await self._save_v2_state(
            state,
            event_type=OrchestrationEventType.AGENT_RESULT_INGESTED,
            payload={"agent_message_ids": [result.agent_message_id for result in results]},
            mutate=lambda updated: self._apply_v2_results(
                updated,
                results,
                status=OrchestrationStatus.RUNNING,
                advance_step=True,
            ),
        )
        return state, None

    @staticmethod
    def _v2_result_from_output_record(
        intent: DispatchIntent,
        output: AgentOutputRecord | None,
        agent_names: dict[str, str],
        step_number: int,
    ) -> StepResult | None:
        if output is None or output.status not in {
            StepStatus.SUCCESS.value,
            StepStatus.FAILED.value,
        }:
            return None
        status = StepStatus(output.status)
        return StepResult(
            step_number=step_number,
            agent_id=output.agent_id or intent.agent_id,
            agent_name=agent_names.get(output.agent_id or intent.agent_id),
            task=intent.task,
            response_text=output.text or "",
            success=status == StepStatus.SUCCESS,
            status=status,
            error_message=output.error,
            agent_message_id=output.agent_message_id,
            completed_at=utcnow(),
        )

    async def _v2_result_from_committed_agent_message(
        self,
        intent: DispatchIntent,
        agent_names: dict[str, str],
        step_number: int,
    ) -> StepResult | None:
        msg = await self.message_reader.get_room_agent_message_by_message_id(
            intent.planned_agent_message_id
        )
        return self._v2_result_from_agent_message(
            intent,
            msg,
            agent_names,
            step_number,
        )

    @staticmethod
    def _v2_result_from_agent_message(
        intent: DispatchIntent,
        msg,
        agent_names: dict[str, str],
        step_number: int,
    ) -> StepResult | None:
        terminal_states = {"completed", "failed", "canceled", "rejected"}
        if not msg or getattr(msg, "last_notified_state", None) not in terminal_states:
            return None

        last_state = msg.last_notified_state
        is_success = last_state == "completed"
        response_text = ""
        message_content = getattr(msg, "message_content", None)
        if message_content and getattr(message_content, "message_text", None):
            response_text = message_content.message_text

        return StepResult(
            step_number=step_number,
            agent_id=intent.agent_id,
            agent_name=agent_names.get(intent.agent_id),
            task=intent.task,
            response_text=response_text,
            success=is_success,
            status=StepStatus.SUCCESS if is_success else StepStatus.FAILED,
            error_message=None if is_success else "Agent task failed",
            agent_message_id=intent.planned_agent_message_id,
            completed_at=utcnow(),
        )

    @staticmethod
    def _v2_recovered_trajectory_entry(
        *,
        step_number: int,
        intents: list[DispatchIntent],
        results: list[StepResult],
        agent_names: dict[str, str],
    ) -> TrajectoryEntry:
        return TrajectoryEntry(
            step_number=step_number,
            action=SupervisorAction(
                action=ActionType.DELEGATE,
                reasoning="Recovered in-flight v2 dispatch from committed agent messages",
                targets=[
                    DelegateTarget(
                        agent_id=intent.agent_id,
                        agent_name=agent_names.get(intent.agent_id)
                        or intent.agent_id,
                        task=intent.task,
                    )
                    for intent in intents
                ],
            ),
            results=results,
            started_at=utcnow(),
            completed_at=utcnow(),
        )

    async def _run_agent_awaiting_input_action(
        self,
        *,
        state: OrchestrationRunState,
        results: list[StepResult],
        awaiting: list[StepResult],
        trajectory: SupervisorTrajectory,
        agent_registry: list[AgentProfile],
        room_config: RoomConfig,
        room_id: str,
        user_message_id: str,
        message_text: str,
        conversation_context: str | None,
        request_user_id: str | None,
        quoted_text: str | None,
    ) -> tuple[OrchestrationRunState, RunStatus]:
        if self.hitl_coordinator is None:
            raise RuntimeError("HITL coordinator has not been bound")

        for extra in awaiting[1:]:
            extra.status = StepStatus.FAILED
            extra.success = False
            extra.error_message = (
                "Deferred: another agent is awaiting human input first. "
                "Will be re-evaluated on resume."
            )

        awaiting_result = awaiting[0]
        continuation_message_id = (
            awaiting_result.paused_message_id
            or awaiting_result.agent_message_id
        )
        display_message_id = (
            awaiting_result.agent_message_id
            or awaiting_result.paused_message_id
        )
        if not continuation_message_id:
            trajectory.status = TrajectoryStatus.FAILED
            state = await self._mark_v2_terminal(
                state,
                OrchestrationStatus.FAILED,
                reason="v2 agent HITL result missing continuation message id",
            )
            return state, RunStatus.FAILED

        request = await self.hitl_coordinator.request_input(
            room_id=room_id,
            user_message_id=user_message_id,
            source="agent",
            prompt=(
                awaiting_result.status_message
                or "The agent needs additional information."
            ),
            agent_id=awaiting_result.agent_id,
            agent_name=awaiting_result.agent_name,
            a2a_task_id=awaiting_result.a2a_task_id,
            a2a_context_id=awaiting_result.a2a_context_id,
            continuation_message_id=continuation_message_id,
            display_message_id=display_message_id,
            orchestration_run_id=state.run_id,
            orchestration_schema_version=state.schema_version,
        )
        if request is None:
            trajectory.status = TrajectoryStatus.FAILED
            state = await self._mark_v2_terminal(
                state,
                OrchestrationStatus.FAILED,
                reason="failed to create v2 agent HITL request",
            )
            return state, RunStatus.FAILED

        saved = await self._save_interrupted_state(
            kind=InterruptKind.HITL_AGENT,
            trajectory=trajectory,
            message_id=continuation_message_id,
            room_id=room_id,
            user_message_id=user_message_id,
            message_text=message_text,
            agent_registry=agent_registry,
            room_config=room_config,
            conversation_context=conversation_context,
            request_user_id=request_user_id,
            quoted_text=quoted_text,
            hitl_request_id=request.request_id,
        )
        if not saved:
            cancel_request = getattr(self.hitl_coordinator, "cancel_request", None)
            if cancel_request is not None:
                try:
                    await cancel_request(request.request_id, room_id)
                except Exception:
                    logger.warning(
                        "Failed to cancel orphaned v2 agent HITL request %s",
                        request.request_id,
                    )
            trajectory.status = TrajectoryStatus.FAILED
            state = await self._mark_v2_terminal(
                state,
                OrchestrationStatus.FAILED,
                reason="failed to save v2 agent HITL continuation",
            )
            return state, RunStatus.FAILED

        def mark_awaiting_user(updated: OrchestrationRunState) -> None:
            self._apply_v2_results(
                updated,
                results,
                status=OrchestrationStatus.AWAITING_USER,
                advance_step=False,
            )
            if request.request_id not in updated.pending_hitl_request_ids:
                updated.pending_hitl_request_ids.append(request.request_id)
            if not any(
                question.get("request_id") == request.request_id
                for question in updated.open_questions
            ):
                updated.open_questions.append(
                    {
                        "request_id": request.request_id,
                        "source": "agent",
                        "agent_id": awaiting_result.agent_id,
                        "prompt": (
                            awaiting_result.status_message
                            or "The agent needs additional information."
                        ),
                        "status": "open",
                        "created_at": utcnow().isoformat(),
                    }
                )

        state = await self._save_v2_state(
            state,
            event_type=OrchestrationEventType.HITL_REQUESTED,
            payload={
                "status": OrchestrationStatus.AWAITING_USER.value,
                "request_ids": [request.request_id],
            },
            mutate=mark_awaiting_user,
        )

        try:
            await self._emit_processing_status(
                room_id=room_id,
                status=SSEProcessingStatus.AWAITING_INPUT,
                message_id=user_message_id,
                lifecycle_message_id=user_message_id,
            )
        except Exception:
            logger.debug("SSE v2 agent HITL notification failed", exc_info=True)

        return state, RunStatus.AWAITING_INPUT

    async def _resolve_v2_hitl_if_answered(
        self,
        state: OrchestrationRunState,
        *,
        user_message=None,
        resumed_trajectory: SupervisorTrajectory | None = None,
    ) -> OrchestrationRunState:
        if not state.pending_hitl_request_ids:
            return state
        answer = self._hitl_answer_from_run_request(
            user_message=user_message,
            resumed_trajectory=resumed_trajectory,
        )
        if not answer:
            return state

        resolved_request_ids = list(state.pending_hitl_request_ids)
        resolved_at = utcnow().isoformat()

        def resolve_hitl(updated: OrchestrationRunState) -> None:
            prompts: list[str] = []
            for question in updated.open_questions:
                if question.get("request_id") not in resolved_request_ids:
                    continue
                question["status"] = "resolved"
                question["answer"] = answer
                question["resolved_at"] = resolved_at
                prompt = question.get("prompt")
                if isinstance(prompt, str) and prompt:
                    prompts.append(prompt)
            updated.facts.append(
                {
                    "fact_id": (
                        f"{state.run_id}:hitl-reply:{state.state_version + 1}"
                    ),
                    "source": "hitl_user_reply",
                    "text": answer,
                    "request_ids": resolved_request_ids,
                    "question_prompts": prompts,
                    "created_at": resolved_at,
                }
            )
            updated.pending_hitl_request_ids.clear()

        resolved_state = await self._save_v2_state(
            state,
            event_type=OrchestrationEventType.HITL_RESOLVED,
            payload={
                "request_ids": resolved_request_ids,
                "answer_recorded": True,
            },
            mutate=resolve_hitl,
        )
        if resumed_trajectory is not None:
            resumed_trajectory.hitl_user_reply = None
            resumed_trajectory.clarify_user_reply = None
        return resolved_state

    @classmethod
    def _hitl_answer_from_run_request(
        cls,
        *,
        user_message=None,
        resumed_trajectory: SupervisorTrajectory | None = None,
    ) -> str | None:
        if resumed_trajectory is not None:
            for value in (
                resumed_trajectory.hitl_user_reply,
                resumed_trajectory.clarify_user_reply,
            ):
                if isinstance(value, str) and value.strip():
                    return value.strip()
        extend_info = getattr(user_message, "extend_info", None)
        if isinstance(extend_info, Mapping):
            for envelope in cls._hitl_answer_envelopes(extend_info):
                answer = cls._hitl_answer_from_mapping(envelope)
                if answer:
                    return answer
        return None

    @staticmethod
    def _hitl_answer_envelopes(
        extend_info: Mapping[str, Any],
    ) -> list[Mapping[str, Any]]:
        envelopes: list[Mapping[str, Any]] = [extend_info]
        for key in ("orchestration", "orchestration_run", "resumed_trajectory"):
            nested = extend_info.get(key)
            if isinstance(nested, Mapping):
                envelopes.append(nested)
        return envelopes

    @staticmethod
    def _hitl_answer_from_mapping(envelope: Mapping[str, Any]) -> str | None:
        for key in (
            "hitl_user_reply",
            "clarify_user_reply",
            "hitl_reply",
            "user_reply",
            "user_input",
        ):
            value = envelope.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    async def _v2_terminal_result_if_done(
        self,
        room_id: str,
        state: OrchestrationRunState,
    ) -> SupervisorRunResult | None:
        if state.status not in TERMINAL_ORCHESTRATION_STATUSES:
            return None

        result = self._state_run_result(
            status=self._run_status_from_orchestration_status(state.status),
            state=state,
        )
        return await self._log_state_and_return(
            room_id,
            state,
            result,
        )

    async def _sync_v2_resumed_trajectory(
        self,
        state: OrchestrationRunState,
        trajectory: SupervisorTrajectory,
    ) -> tuple[OrchestrationRunState, RunStatus | None]:
        """Ingest completed legacy resume entries into the sidecar run state."""
        if not trajectory.entries:
            return state, None

        synced = state
        for entry in sorted(trajectory.entries, key=lambda item: item.step_number):
            if entry.action.action != ActionType.DELEGATE:
                continue
            if not entry.results:
                continue
            if entry.step_number <= synced.steps_used:
                continue

            self._resolve_v2_pending_results_from_outputs(synced, entry)
            pending = [
                result
                for result in entry.results
                if result.status in (StepStatus.PAUSED, StepStatus.AWAITING_INPUT)
            ]
            terminal_results = [
                result
                for result in entry.results
                if result.status not in (StepStatus.PAUSED, StepStatus.AWAITING_INPUT)
            ]
            if terminal_results:
                next_status = (
                    OrchestrationStatus.AWAITING_USER
                    if any(
                        result.status == StepStatus.AWAITING_INPUT
                        for result in pending
                    )
                    else (
                        OrchestrationStatus.WAITING_AGENT
                        if pending
                        else OrchestrationStatus.RUNNING
                    )
                )
                should_advance = not pending

                def ingest_terminal_results(
                    updated: OrchestrationRunState,
                    *,
                    results: list[StepResult] = terminal_results,
                    status: OrchestrationStatus = next_status,
                    advance: bool = should_advance,
                ) -> None:
                    self._apply_v2_results(
                        updated,
                        results,
                        status=status,
                        advance_step=advance,
                    )
                    if advance:
                        updated.pending_hitl_request_ids.clear()

                synced = await self._save_v2_state(
                    synced,
                    event_type=OrchestrationEventType.AGENT_RESULT_INGESTED,
                    payload={
                        "resumed": True,
                        "step_number": entry.step_number,
                        "agent_message_ids": [
                            result.agent_message_id
                            for result in terminal_results
                            if result.agent_message_id
                        ],
                    },
                    mutate=ingest_terminal_results,
                )

            if pending:
                pending_status = (
                    OrchestrationStatus.AWAITING_USER
                    if any(
                        result.status == StepStatus.AWAITING_INPUT
                        for result in pending
                    )
                    else OrchestrationStatus.WAITING_AGENT
                )
                blocking_run_status = (
                    RunStatus.AWAITING_INPUT
                    if pending_status == OrchestrationStatus.AWAITING_USER
                    else RunStatus.PAUSED
                )
                if synced.status != pending_status:
                    synced = await self._save_v2_state(
                        synced,
                        event_type=OrchestrationEventType.STATE_REDUCED,
                        payload={"status": pending_status.value},
                        mutate=lambda updated, status=pending_status: setattr(
                            updated,
                            "status",
                            status,
                        ),
                    )
                return synced, blocking_run_status

        return synced, None

    @staticmethod
    def _resolve_v2_pending_results_from_outputs(
        state: OrchestrationRunState,
        entry: TrajectoryEntry,
    ) -> None:
        outputs_by_message_id = {
            output.agent_message_id: output for output in state.agent_outputs
        }
        for index, result in enumerate(entry.results):
            if result.status not in (StepStatus.PAUSED, StepStatus.AWAITING_INPUT):
                continue
            message_id = result.agent_message_id or result.paused_message_id
            if not message_id:
                continue
            output = outputs_by_message_id.get(message_id)
            if output is None:
                continue
            try:
                output_status = StepStatus(output.status)
            except ValueError:
                continue
            if output_status in (StepStatus.PAUSED, StepStatus.AWAITING_INPUT):
                continue
            entry.results[index] = StepResult(
                step_number=entry.step_number,
                agent_id=result.agent_id,
                agent_name=result.agent_name,
                task=result.task,
                response_text=output.text or "",
                success=output_status == StepStatus.SUCCESS,
                status=output_status,
                error_message=output.error,
                agent_message_id=message_id,
                completed_at=utcnow(),
            )
        if not any(
            result.status in (StepStatus.PAUSED, StepStatus.AWAITING_INPUT)
            for result in entry.results
        ):
            entry.completed_at = entry.completed_at or utcnow()

    async def _run_ask_user_action(
        self,
        *,
        state: OrchestrationRunState,
        planner_action: PlannerAction,
        agent_registry: list[AgentProfile],
        room_config: RoomConfig,
        room_id: str,
        user_message_id: str,
        message_text: str,
        conversation_context: str | None,
        request_user_id: str | None,
        quoted_text: str | None,
        resume_pending_artifacts: bool = False,
    ) -> SupervisorRunResult:
        if self.hitl_coordinator is None:
            raise RuntimeError("HITL coordinator has not been bound")

        trajectory = self._compat_trajectory_from_state(state)
        action = self._v2_supervisor_action(planner_action, agent_registry)
        step_number = (
            state.steps_used if resume_pending_artifacts else state.steps_used + 1
        )
        entry = TrajectoryEntry(
            step_number=step_number,
            action=action,
            started_at=utcnow(),
            completed_at=utcnow(),
        )
        trajectory.entries.append(entry)
        trajectory.status = TrajectoryStatus.AWAITING_INPUT

        questions = action.questions or [
            ClarifyQuestion(
                prompt=(
                    action.clarification_question
                    or "The supervisor needs your input."
                ),
                prompt_type=action.prompt_type,
                choices=action.choices,
            )
        ]
        group_id = (
            f"{state.run_id}:step-{step_number}:supervisor-hitl-group"
            if len(questions) > 1
            else None
        )
        created_messages: list[str] = []
        created_request_ids: list[str] = []
        pending_request_ids = [
            f"{state.run_id}:step-{step_number}:supervisor-hitl-{index}"
            for index in range(1, len(questions) + 1)
        ]
        last_request = None
        client_req_id = state.client_request_id or (
            await self.task_state_store.resolve_client_request_id_for_message_id(
                user_message_id
            )
        )

        def mark_pending_hitl(updated: OrchestrationRunState) -> None:
            updated.status = OrchestrationStatus.INGESTING
            updated.steps_used += 1
            for request_id in pending_request_ids:
                if request_id not in updated.pending_hitl_request_ids:
                    updated.pending_hitl_request_ids.append(request_id)

        if not resume_pending_artifacts:
            state = await self._save_v2_state(
                state,
                event_type=OrchestrationEventType.HITL_REQUESTED,
                payload={
                    "status": OrchestrationStatus.INGESTING.value,
                    "request_ids": pending_request_ids,
                    "pending_artifacts": True,
                },
                mutate=mark_pending_hitl,
            )

        async def cleanup_created_artifacts() -> None:
            cancel_request = getattr(self.hitl_coordinator, "cancel_request", None)
            if cancel_request is not None:
                for request_id in created_request_ids:
                    try:
                        await cancel_request(request_id, room_id)
                    except Exception:
                        logger.warning(
                            "Failed to cancel orphaned v2 HITL request %s",
                            request_id,
                        )
            for message_id in created_messages:
                delete_message = getattr(
                    self.message_writer,
                    "delete_room_agent_message_by_message_id",
                    None,
                )
                if delete_message is None:
                    continue
                try:
                    await delete_message(message_id)
                except Exception:
                    logger.warning(
                        "Failed to delete orphaned v2 HITL agent message %s",
                        message_id,
                    )

        for qi, question in enumerate(questions):
            prompt_type = HITLPromptType.TEXT
            if question.prompt_type:
                try:
                    prompt_type = HITLPromptType(question.prompt_type)
                except ValueError:
                    pass

            hitl_agent_message = self.room_runtime.create_agent_message(
                room_id=room_id,
                related_message_id=user_message_id,
                agent_id=CoordinatorAgentId.SYSTEM_CLARIFIER,
                content=question.prompt,
                user_id=request_user_id,
                step_number=step_number,
                task_content=question.prompt,
                client_request_id=client_req_id,
            )
            hitl_agent_message.message_id = f"{pending_request_ids[qi]}:message"
            await self.message_writer.upsert_room_agent_message(hitl_agent_message)
            created_messages.append(hitl_agent_message.message_id)

            request = await self.hitl_coordinator.request_input(
                room_id=room_id,
                user_message_id=user_message_id,
                source="supervisor",
                prompt=question.prompt,
                request_id=pending_request_ids[qi],
                prompt_type=prompt_type,
                choices=question.choices,
                agent_id=CoordinatorAgentId.SYSTEM_CLARIFIER,
                agent_name="HYBRO AI",
                source_step_id=str(step_number),
                continuation_message_id=user_message_id,
                display_message_id=hitl_agent_message.message_id,
                group_id=group_id,
                group_total=len(questions) if group_id else None,
                group_index=qi if group_id else None,
                orchestration_run_id=state.run_id,
                orchestration_schema_version=state.schema_version,
            )
            if request is None:
                await cleanup_created_artifacts()
                trajectory.status = TrajectoryStatus.FAILED
                state = await self._mark_v2_terminal(
                    state,
                    OrchestrationStatus.FAILED,
                    reason="failed to create v2 HITL request",
                )
                return await self._log_state_and_return(
                    room_id,
                    state,
                    self._state_run_result(
                        status=RunStatus.FAILED,
                        state=state,
                    ),
                )
            created_request_ids.append(request.request_id)
            last_request = request

        saved = await self._save_interrupted_state(
            kind=InterruptKind.HITL_SUPERVISOR,
            trajectory=trajectory,
            room_id=room_id,
            user_message_id=user_message_id,
            message_text=message_text,
            agent_registry=agent_registry,
            room_config=room_config,
            conversation_context=conversation_context,
            request_user_id=request_user_id,
            quoted_text=quoted_text,
            hitl_request_id=last_request.request_id if last_request else None,
            message_id=user_message_id,
        )
        if not saved:
            await cleanup_created_artifacts()
            trajectory.status = TrajectoryStatus.FAILED
            state = await self._mark_v2_terminal(
                state,
                OrchestrationStatus.FAILED,
                reason="failed to save v2 HITL continuation",
            )
            return await self._log_state_and_return(
                room_id,
                state,
                self._state_run_result(
                    status=RunStatus.FAILED,
                    state=state,
                ),
            )

        def mark_awaiting_user(updated: OrchestrationRunState) -> None:
            updated.status = OrchestrationStatus.AWAITING_USER
            updated.pending_hitl_request_ids = [
                request_id
                for request_id in updated.pending_hitl_request_ids
                if request_id not in pending_request_ids
            ]
            for index, request_id in enumerate(created_request_ids):
                if request_id not in updated.pending_hitl_request_ids:
                    updated.pending_hitl_request_ids.append(request_id)
                if any(
                    question.get("request_id") == request_id
                    for question in updated.open_questions
                ):
                    continue
                question = questions[min(index, len(questions) - 1)]
                updated.open_questions.append(
                    {
                        "request_id": request_id,
                        "source": "supervisor",
                        "prompt": question.prompt,
                        "prompt_type": question.prompt_type,
                        "choices": question.choices,
                        "status": "open",
                        "created_at": utcnow().isoformat(),
                    }
                )

        state = await self._save_v2_state(
            state,
            event_type=OrchestrationEventType.HITL_REQUESTED,
            payload={
                "status": OrchestrationStatus.AWAITING_USER.value,
                "request_ids": created_request_ids,
            },
            mutate=mark_awaiting_user,
        )

        try:
            await self._emit_processing_status(
                room_id=room_id,
                status=SSEProcessingStatus.AWAITING_INPUT,
                message_id=user_message_id,
                lifecycle_message_id=user_message_id,
            )
        except Exception:
            logger.debug("SSE v2 awaiting input notification failed", exc_info=True)

        return await self._log_state_and_return(
            room_id,
            state,
            self._state_run_result(
                status=RunStatus.AWAITING_INPUT,
                state=state,
                clarification_question=questions[0].prompt if questions else None,
            ),
        )

    async def _run_synthesis_action(
        self,
        *,
        state: OrchestrationRunState,
        planner_action: PlannerAction,
        room_id: str,
        user_message_id: str,
        token: CancellationToken | None,
    ) -> SupervisorRunResult:
        trajectory = self._compat_trajectory_from_state(state)
        entry = TrajectoryEntry(
            step_number=state.steps_used + 1,
            action=self._v2_supervisor_action(planner_action, []),
            started_at=utcnow(),
        )
        trajectory.entries.append(entry)

        client_req_id = state.client_request_id or (
            await self.task_state_store.resolve_client_request_id_for_message_id(
                user_message_id
            )
        )
        synth_coro = self._stream_supervisor_synthesis(
            room_id=room_id,
            user_message_id=user_message_id,
            trajectory=trajectory,
            synthesis_instruction=planner_action.synthesis_instruction or "",
            client_request_id=client_req_id,
        )
        try:
            synthesis = await token.race(synth_coro) if token else await synth_coro
        except CancellationError:
            trajectory.status = TrajectoryStatus.CANCELED
            state = await self._mark_v2_terminal(
                state,
                OrchestrationStatus.CANCELED,
                reason="request canceled",
            )
            return await self._log_state_and_return(
                room_id,
                state,
                self._state_run_result(
                    status=RunStatus.CANCELED,
                    state=state,
                ),
            )

        if trajectory.system_agent_message_id:
            try:
                db_msg = (
                    await self.message_reader.get_room_agent_message_by_message_id(
                        trajectory.system_agent_message_id
                    )
                )
                if db_msg and db_msg.message_content:
                    db_msg.message_content.message_text = synthesis
                    await self.message_writer.update_room_agent_message_with_new_message_content_by_message_id(
                        db_msg.message_id,
                        db_msg.message_content,
                    )

                await self.delivery.send_agent_response(
                    room_id=room_id,
                    message_id=trajectory.system_agent_message_id,
                    agent_id=CoordinatorAgentId.SYSTEM_HYBRO,
                    content=synthesis,
                    related_message_id=user_message_id,
                    client_request_id=client_req_id,
                )
            except Exception:
                logger.warning(
                    "Failed to emit v2 agent_response for supervisor synthesis",
                    exc_info=True,
                )

        entry.completed_at = utcnow()
        trajectory.status = TrajectoryStatus.COMPLETED
        state = await self._mark_v2_terminal(
            state,
            OrchestrationStatus.COMPLETED,
            reason=planner_action.reasoning,
        )
        return await self._log_state_and_return(
            room_id,
            state,
            self._state_run_result(
                status=RunStatus.COMPLETED,
                state=state,
                synthesis_text=synthesis,
            ),
        )

    async def _save_v2_state(
        self,
        state: OrchestrationRunState,
        *,
        event_type: OrchestrationEventType,
        payload: dict[str, Any],
        mutate,
    ) -> OrchestrationRunState:
        expected_version = state.state_version
        updated = state.model_copy(deep=True)
        mutate(updated)
        updated.state_version = expected_version + 1
        updated.updated_at = utcnow()
        saved = await self.orchestration_run_store.save_state(
            updated,
            expected_version=expected_version,
        )
        await self._append_v2_event(saved, event_type, payload=payload)
        return saved

    async def _mark_v2_terminal(
        self,
        state: OrchestrationRunState,
        status: OrchestrationStatus,
        *,
        reason: str,
    ) -> OrchestrationRunState:
        expected_version = state.state_version
        updated = mark_terminal(state, status, reason=reason)
        saved = await self.orchestration_run_store.save_state(
            updated,
            expected_version=expected_version,
        )
        await self._append_v2_event(
            saved,
            OrchestrationEventType.RUN_TERMINAL,
            payload={"status": saved.status.value, "reason": reason},
        )
        return saved

    async def _append_v2_event(
        self,
        state: OrchestrationRunState,
        event_type: OrchestrationEventType,
        *,
        payload: dict[str, Any],
    ) -> None:
        try:
            await self.orchestration_run_store.append_event(
                OrchestrationRunEvent(
                    run_id=state.run_id,
                    room_id=state.room_id,
                    type=event_type,
                    state_version=state.state_version,
                    payload=payload,
                )
            )
        except Exception:
            logger.debug("Failed to append v2 orchestration event", exc_info=True)

    @staticmethod
    def _apply_v2_dispatch_intents(
        state: OrchestrationRunState,
        intents: list[DispatchIntent],
    ) -> None:
        state.status = OrchestrationStatus.DISPATCHING
        state.dispatch_intents.extend(intents)

    @staticmethod
    def _apply_v2_results(
        state: OrchestrationRunState,
        results: list[StepResult],
        *,
        status: OrchestrationStatus,
        advance_step: bool,
    ) -> None:
        state.status = status
        outputs_by_message_id = {
            output.agent_message_id: output for output in state.agent_outputs
        }
        for result in results:
            matched_intent = next(
                (
                    intent
                    for intent in state.dispatch_intents
                    if (
                        intent.planned_agent_message_id == result.agent_message_id
                        if result.agent_message_id
                        else intent.agent_id == result.agent_id
                        and intent.task == result.task
                        and intent.status == "planned"
                    )
                ),
                None,
            )
            output_message_id = result.agent_message_id or (
                matched_intent.planned_agent_message_id if matched_intent else None
            )
            if output_message_id:
                output = outputs_by_message_id.get(output_message_id)
                if output is None:
                    output = AgentOutputRecord(
                        agent_message_id=output_message_id,
                        agent_id=result.agent_id,
                        status=result.status.value,
                    )
                    state.agent_outputs.append(output)
                    outputs_by_message_id[output_message_id] = output
                output.agent_id = result.agent_id
                output.status = result.status.value
                output.text = result.response_text or None
                output.error = result.error_message
            for intent in state.dispatch_intents:
                if intent is matched_intent or intent.planned_agent_message_id == (
                    result.agent_message_id
                ):
                    intent.status = result.status.value
        if advance_step:
            state.steps_used += 1

    @staticmethod
    def _v2_dispatch_intent(
        *,
        run_id: str,
        step_number: int,
        target_index: int,
        target: DelegateTarget,
    ) -> DispatchIntent:
        step_id = f"{run_id}:step-{step_number}"
        step_target_id = f"{step_id}:target-{target_index}"
        return DispatchIntent(
            step_id=step_id,
            step_target_id=step_target_id,
            dispatch_intent_id=f"{step_target_id}:intent",
            planned_agent_message_id=f"{step_target_id}:message",
            agent_id=target.agent_id,
            task=target.task,
            task_hash=hashlib.sha256(target.task.encode("utf-8")).hexdigest(),
            context_refs=list(target.context_refs),
            artifact_refs=list(target.artifact_refs),
            attachment_refs=list(target.attachment_refs),
            expected_outputs=list(target.expected_outputs),
            attachment_policy=target.attachment_policy,
        )

    @staticmethod
    def _v2_supervisor_action(
        planner_action: PlannerAction,
        agent_registry: list[AgentProfile],
    ) -> SupervisorAction:
        names_by_id = {agent.agent_id: agent.agent_name for agent in agent_registry}
        if planner_action.action == PlannerActionType.DELEGATE:
            return SupervisorAction(
                action=ActionType.DELEGATE,
                reasoning=planner_action.reasoning,
                targets=[
                    DelegateTarget(
                        agent_id=target.agent_id,
                        agent_name=(
                            target.agent_name
                            or names_by_id.get(target.agent_id)
                            or target.agent_id
                        ),
                        task=target.task,
                        context_refs=list(target.context_refs),
                        artifact_refs=list(target.artifact_refs),
                        attachment_refs=list(target.attachment_refs),
                        expected_outputs=list(target.expected_outputs),
                        attachment_policy=target.attachment_policy,
                    )
                    for target in planner_action.targets
                ],
            )
        if planner_action.action == PlannerActionType.SYNTHESIZE:
            return SupervisorAction(
                action=ActionType.SYNTHESIZE,
                reasoning=planner_action.reasoning,
                synthesis_instruction=planner_action.synthesis_instruction,
            )
        if planner_action.action == PlannerActionType.ASK_USER:
            questions = [
                ClarifyQuestion(
                    prompt=question.prompt,
                    prompt_type=question.prompt_type,
                    choices=question.choices,
                )
                for question in planner_action.questions
            ]
            return SupervisorAction(
                action=ActionType.CLARIFY,
                reasoning=planner_action.reasoning,
                clarification_question=questions[0].prompt if questions else None,
                prompt_type=questions[0].prompt_type if questions else None,
                choices=questions[0].choices if questions else None,
                questions=questions,
            )
        return SupervisorAction(
            action=ActionType.DONE,
            reasoning=planner_action.reasoning,
        )

    @staticmethod
    def _v2_candidate_scope(
        state: OrchestrationRunState,
        agent_registry: list[AgentProfile],
    ) -> list[AgentProfile]:
        profiles_by_id = {agent.agent_id: agent for agent in agent_registry}
        return [
            profiles_by_id.get(agent_id)
            or AgentProfile(
                agent_id=agent_id,
                agent_name=agent_id,
                is_healthy=False,
            )
            for agent_id in state.candidate_agent_ids
        ]

    @staticmethod
    def _orchestration_envelope_from_user_message(user_message) -> dict[str, Any]:
        extend_info = getattr(user_message, "extend_info", None)
        if not isinstance(extend_info, Mapping):
            return {}
        envelope = dict(extend_info)
        for nested_key in ("orchestration", "orchestration_run"):
            nested = extend_info.get(nested_key)
            if isinstance(nested, Mapping):
                envelope.update(nested)
        return envelope

    @staticmethod
    def _orchestration_envelope_str(envelope: Mapping[str, Any], key: str) -> str | None:
        value = envelope.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(
        self,
        room_id: str,
        user_message_id: str,
        message_text: str,
        agent_registry: list[AgentProfile],
        room_config: RoomConfig,
        conversation_context: str | None = None,
        token: CancellationToken | None = None,
        request_user_id: str | None = None,
        quoted_text: str | None = None,
        resumed_trajectory: SupervisorTrajectory | None = None,
        user_message=None,
    ) -> SupervisorRunResult:
        """Execute the full supervisor loop for a user message."""
        state = await self._load_or_create_run_state_for_run(
            room_id=room_id,
            user_message_id=user_message_id,
            message_text=message_text,
            agent_registry=agent_registry,
            room_config=room_config,
            user_message=user_message,
        )
        if resumed_trajectory is not None:
            state = await self._resolve_v2_hitl_if_answered(
                state,
                user_message=user_message,
                resumed_trajectory=resumed_trajectory,
            )
            state, blocking_resume_status = await self._sync_v2_resumed_trajectory(
                state,
                resumed_trajectory,
            )
            if blocking_resume_status is not None:
                return self._state_run_result(
                    status=blocking_resume_status,
                    state=state,
                )

        return await self._execute_orchestration_loop(
            state=state,
            room_id=room_id,
            user_message_id=user_message_id,
            message_text=message_text,
            agent_registry=agent_registry,
            room_config=room_config,
            conversation_context=conversation_context,
            token=token,
            request_user_id=request_user_id,
            quoted_text=quoted_text,
            user_message=user_message,
        )

    # ------------------------------------------------------------------
    # Concurrent agent dispatch
    # ------------------------------------------------------------------

    @staticmethod
    def _user_attachments_from_message(user_message) -> list[UserAttachment]:
        message_content = getattr(user_message, "message_content", None)
        attachments = getattr(message_content, "attachments", None)
        if not isinstance(attachments, Sequence) or isinstance(
            attachments,
            str | bytes,
        ):
            return []

        normalized: list[UserAttachment] = []
        for attachment in attachments:
            if isinstance(attachment, UserAttachment):
                normalized.append(attachment)
                continue
            try:
                if isinstance(attachment, Mapping):
                    normalized.append(UserAttachment.model_validate(attachment))
                elif hasattr(attachment, "model_dump"):
                    normalized.append(
                        UserAttachment.model_validate(
                            attachment.model_dump(mode="json")
                        )
                    )
            except Exception:
                logger.warning(
                    "Skipping invalid user attachment while resolving dispatch refs",
                    exc_info=True,
                )
        return normalized

    @staticmethod
    def _step_result_from_existing_agent_message(
        message,
        *,
        target: DelegateTarget,
        step_number: int,
        agent_message_id: str,
    ) -> StepResult | None:
        terminal_states = {"completed", "failed", "canceled", "rejected"}
        last_state = getattr(message, "last_notified_state", None)
        if last_state not in terminal_states:
            return None

        is_success = last_state == "completed"
        response_text = ""
        message_content = getattr(message, "message_content", None)
        if message_content and getattr(message_content, "message_text", None):
            response_text = message_content.message_text

        return StepResult(
            step_number=step_number,
            agent_id=target.agent_id,
            agent_name=target.agent_name,
            task=target.task,
            response_text=response_text,
            success=is_success,
            status=StepStatus.SUCCESS if is_success else StepStatus.FAILED,
            error_message=None if is_success else "Agent task failed",
            agent_message_id=agent_message_id,
            completed_at=utcnow(),
        )

    async def _dispatch_targets(
        self,
        targets: list[DelegateTarget],
        agent_registry: list[AgentProfile],
        room_id: str,
        user_message_id: str,
        step_number: int,
        token: CancellationToken | None,
        request_user_id: str | None,
        quoted_text: str | None,
        planned_message_ids: list[str] | None = None,
        run_state: OrchestrationRunState | None = None,
        original_attachments: list | None = None,
    ) -> list[StepResult]:
        """Dispatch one or more agents, concurrently if multiple targets."""
        valid_ids = {a.agent_id for a in agent_registry}

        def planned_message_id_at(index: int) -> str | None:
            if planned_message_ids is None or index >= len(planned_message_ids):
                return None
            return planned_message_ids[index]

        async def dispatch_one(
            target: DelegateTarget,
            planned_message_id: str | None = None,
        ) -> StepResult:
            try:
                # Validate agent_id against registry before any DB writes
                if target.agent_id not in valid_ids:
                    logger.warning(
                        "Supervisor hallucinated agent_id=%s (valid: %s)",
                        target.agent_id,
                        valid_ids,
                    )
                    return StepResult(
                        step_number=step_number,
                        agent_id=target.agent_id,
                        agent_name=target.agent_name,
                        task=target.task,
                        response_text="",
                        success=False,
                        status=StepStatus.FAILED,
                        error_message="Agent ID not in registry (hallucinated)",
                    )

                agent = await self.agent_dispatcher.resolve_agent(
                    target.agent_id, room_id
                )
                if not agent:
                    # --- Emit failed slot (Phase 1b) ---
                    if getattr(self, "_slot_lifecycle", None) and user_message_id:
                        try:
                            failed_slot_id = f"sup-{target.agent_id}-{step_number}"
                            await self._slot_lifecycle.open_slot(
                                room_id=room_id,
                                turn_id=user_message_id,
                                slot_id=failed_slot_id,
                                slot_type="agent",
                                agent_id=target.agent_id,
                                agent_name=target.agent_name,
                            )
                            await self._slot_lifecycle.terminate_slot(
                                room_id=room_id,
                                turn_id=user_message_id,
                                slot_id=failed_slot_id,
                                status="failed",
                                error="agent_unavailable",
                            )
                        except Exception:
                            logger.warning(
                                "SupervisorExecutor: failed slot emission failed",
                                exc_info=True,
                            )
                    logger.warning(
                        "dispatch_one: agent %s not found or inactive",
                        target.agent_id,
                    )
                    return StepResult(
                        step_number=step_number,
                        agent_id=target.agent_id,
                        agent_name=target.agent_name,
                        task=target.task,
                        response_text="",
                        success=False,
                        status=StepStatus.FAILED,
                        error_message="Agent not found or inactive",
                    )

                # Rate limit check
                if request_user_id and self.rate_limit_service:
                    rate_result = await self.rate_limit_service.check_rate_limit(
                        agent_id=agent.agent_id,
                        user_id=request_user_id,
                        rate_limit_per_user=agent.rate_limit_per_user_per_hour,
                        rate_limit_system=agent.rate_limit_system_per_hour,
                    )
                    if not rate_result.allowed:
                        return StepResult(
                            step_number=step_number,
                            agent_id=target.agent_id,
                            agent_name=target.agent_name,
                            task=target.task,
                            response_text="",
                            success=False,
                            status=StepStatus.FAILED,
                            error_message=f"Rate limited: {rate_result.reason}",
                        )

                resolved_payload: ResolvedDispatchPayload | None = None
                if run_state is not None:
                    try:
                        resolved_payload = await resolve_dispatch_payload_refs(
                            run_state=run_state,
                            target_agent_card=getattr(agent, "agent_card", None)
                            or agent,
                            context_refs=target.context_refs,
                            artifact_refs=target.artifact_refs,
                            attachment_refs=target.attachment_refs,
                            original_attachments=original_attachments or [],
                            resource_provider=self.orchestration_resource_provider,
                        )
                    except DispatchPayloadValidationError as exc:
                        return StepResult(
                            step_number=step_number,
                            agent_id=target.agent_id,
                            agent_name=target.agent_name,
                            task=target.task,
                            response_text="",
                            success=False,
                            status=StepStatus.FAILED,
                            error_message=str(exc),
                        )

                # Create RoomAgentMessage only after validation passes
                message = self.room_runtime.create_agent_message(
                    room_id=room_id,
                    related_message_id=user_message_id,
                    agent_id=target.agent_id,
                    content=target.task,
                    user_id=request_user_id,
                    step_number=step_number,
                    total_steps=None,
                    task_content=target.task,
                    client_request_id=await self.task_state_store.resolve_client_request_id_for_message_id(
                        user_message_id
                    ),
                )
                if planned_message_id:
                    message.message_id = planned_message_id
                if resolved_payload is not None:
                    if not isinstance(message.extend_info, dict):
                        message.extend_info = {}
                    message.extend_info["attachment_forwarding_policy"] = (
                        "explicit_refs_only"
                    )
                    message.extend_info["resolved_dispatch_payload_refs"] = {
                        "context_refs": list(
                            resolved_payload.selected_context_refs
                        ),
                        "artifact_refs": list(
                            resolved_payload.selected_artifact_refs
                        ),
                        "attachment_refs": list(
                            resolved_payload.selected_attachment_refs
                        ),
                        "resource_payloads": [
                            payload.model_dump(mode="json")
                            for payload in resolved_payload.resource_payloads
                        ],
                    }
                    message.extend_info["dispatch_payload_refs"] = {
                        "context_refs": [
                            ref.model_dump(mode="json")
                            for ref in target.context_refs
                        ],
                        "artifact_refs": [
                            ref.model_dump(mode="json")
                            for ref in target.artifact_refs
                        ],
                        "attachment_refs": [
                            ref.model_dump(mode="json")
                            for ref in target.attachment_refs
                        ],
                    }
                inserted = await self.message_writer.add_room_agent_message(message)
                if inserted is False:
                    existing = (
                        await self.message_reader.get_room_agent_message_by_message_id(
                            message.message_id
                        )
                    )
                    if existing is not None:
                        terminal_result = self._step_result_from_existing_agent_message(
                            existing,
                            target=target,
                            step_number=step_number,
                            agent_message_id=message.message_id,
                        )
                        if terminal_result is not None:
                            return terminal_result
                        return StepResult(
                            step_number=step_number,
                            agent_id=target.agent_id,
                            agent_name=target.agent_name,
                            task=target.task,
                            response_text="",
                            success=True,
                            status=StepStatus.PAUSED,
                            paused_message_id=message.message_id,
                            agent_message_id=message.message_id,
                        )
                    return StepResult(
                        step_number=step_number,
                        agent_id=target.agent_id,
                        agent_name=target.agent_name,
                        task=target.task,
                        response_text="",
                        success=False,
                        status=StepStatus.FAILED,
                        error_message="Failed to create agent message",
                        agent_message_id=message.message_id,
                    )

                # --- Emit slot_opened (Phase 1b) ---
                if getattr(self, "_slot_lifecycle", None) and message.turn_id:
                    try:
                        await self._slot_lifecycle.open_slot(
                            room_id=room_id,
                            turn_id=message.turn_id,
                            slot_id=message.message_id,
                            slot_type="agent",
                            agent_id=target.agent_id,
                            agent_name=target.agent_name,
                        )
                    except Exception:
                        logger.warning(
                            "SupervisorExecutor: slot_opened emission failed for %s",
                            message.message_id,
                            exc_info=True,
                        )

                logger.info(
                    "supervisor_agent_dispatching",
                    extra={
                        "room_id": room_id,
                        "step_number": step_number,
                        "agent_id": target.agent_id,
                        "agent_name": target.agent_name,
                        "agent_message_id": message.message_id,
                    },
                )

                result = await self.agent_message_processor.process_single_message(
                    message,
                    room_id,
                    agent,
                    user_message_id,
                    token=token,
                    step_number=step_number,
                    total_steps=None,
                    quoted_text=quoted_text,
                )

                if result.status in (
                    ProcessingStatus.PAUSED,
                    ProcessingStatus.RELAY_DISPATCHED,
                ):
                    return StepResult(
                        step_number=step_number,
                        agent_id=target.agent_id,
                        agent_name=target.agent_name,
                        task=target.task,
                        response_text="",
                        success=True,
                        status=StepStatus.PAUSED,
                        paused_message_id=result.message_id,
                        agent_message_id=message.message_id,
                    )

                if result.status == ProcessingStatus.AWAITING_INPUT:
                    return StepResult(
                        step_number=step_number,
                        agent_id=target.agent_id,
                        agent_name=target.agent_name,
                        task=target.task,
                        response_text="",
                        success=True,
                        status=StepStatus.AWAITING_INPUT,
                        paused_message_id=result.message_id,
                        agent_message_id=message.message_id,
                        a2a_task_id=result.a2a_task_id,
                        a2a_context_id=result.a2a_context_id,
                        status_message=result.status_message,
                    )

                if result.status == ProcessingStatus.SUCCESS and request_user_id and self.rate_limit_service:
                    await self.rate_limit_service.record_request(
                        agent_id=agent.agent_id,
                        user_id=request_user_id,
                    )

                is_success = result.status == ProcessingStatus.SUCCESS
                error_text = None if is_success else (
                    result.response_text or "Agent processing failed"
                )
                preflight_failure = (
                    message.extend_info.get("attachment_preflight_failure")
                    if isinstance(message.extend_info, dict)
                    else None
                )
                error_code = None if preflight_failure is None else (
                    str(preflight_failure.get("code"))
                    if isinstance(preflight_failure, dict)
                    and preflight_failure.get("code")
                    else result.status_message
                )
                if not is_success and preflight_failure is not None:
                    await self.tsm.fail_pre_dispatch_task(
                        message,
                        error=error_text,
                        error_code=error_code,
                    )
                    await self.delivery.send_task_update(
                        room_id=room_id,
                        message_id=message.message_id,
                        status="failed",
                        error=error_text,
                        agent_name=target.agent_name,
                        agent_id=target.agent_id,
                        step_number=step_number,
                        total_steps=None,
                        task_content=target.task,
                        client_request_id=message.client_request_id,
                    )
                step_result = StepResult(
                    step_number=step_number,
                    agent_id=target.agent_id,
                    agent_name=target.agent_name,
                    task=target.task,
                    response_text=result.response_text,
                    success=is_success,
                    status=StepStatus.SUCCESS if is_success else StepStatus.FAILED,
                    error_message=error_text,
                    agent_message_id=message.message_id,
                )

                logger.info(
                    "supervisor_agent_dispatched",
                    extra={
                        "room_id": room_id,
                        "step_number": step_number,
                        "agent_id": target.agent_id,
                        "agent_name": target.agent_name,
                        "success": step_result.success,
                        "status": step_result.status,
                        "error_message": step_result.error_message,
                        "agent_message_id": step_result.agent_message_id,
                    },
                )

                return step_result

            except asyncio.CancelledError:
                logger.warning("dispatch_one cancelled for agent %s", target.agent_id)
                raise
            except Exception as e:
                logger.exception(
                    "dispatch_one failed for agent %s: %s", target.agent_id, e
                )
                return StepResult(
                    step_number=step_number,
                    agent_id=target.agent_id,
                    agent_name=target.agent_name,
                    task=target.task,
                    response_text="",
                    success=False,
                    status=StepStatus.FAILED,
                    error_message=f"Unexpected error: {e}",
                )

        if len(targets) == 1:
            if token:
                work = asyncio.ensure_future(
                    dispatch_one(targets[0], planned_message_id_at(0))
                )
                cancel_waiter = token.wait()
                try:
                    done, _pending = await asyncio.wait(
                        {cancel_waiter, work},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                except BaseException:
                    cancel_waiter.cancel()
                    work.cancel()
                    raise
                if work in done:
                    cancel_waiter.cancel()
                    return [work.result()]
                # Cancellation won — try to salvage the result.
                work.cancel()
                try:
                    await work
                except (asyncio.CancelledError, Exception):
                    pass
                if work.done() and not work.cancelled():
                    try:
                        return [work.result()]
                    except Exception:
                        pass
                return [
                    StepResult(
                        step_number=step_number,
                        agent_id=targets[0].agent_id,
                        agent_name=targets[0].agent_name,
                        task=targets[0].task,
                        response_text="",
                        success=False,
                        status=StepStatus.FAILED,
                        error_message="Agent dispatch was cancelled",
                    )
                ]
            return [await dispatch_one(targets[0], planned_message_id_at(0))]

        if not token:
            return list(
                await asyncio.gather(
                    *(
                        dispatch_one(target, planned_message_id_at(index))
                        for index, target in enumerate(targets)
                    )
                )
            )

        # Manage individual tasks so that when cancellation fires we
        # can still collect results (with agent_message_id) from tasks
        # that already completed -- needed for cancel_descendants cleanup.
        tasks = [
            asyncio.ensure_future(dispatch_one(target, planned_message_id_at(index)))
            for index, target in enumerate(targets)
        ]
        cancel_waiter = token.wait()
        all_work = asyncio.ensure_future(asyncio.gather(*tasks, return_exceptions=True))

        try:
            done, _pending = await asyncio.wait(
                {cancel_waiter, all_work},
                return_when=asyncio.FIRST_COMPLETED,
            )
        except BaseException:
            cancel_waiter.cancel()
            all_work.cancel()
            raise

        if all_work in done:
            cancel_waiter.cancel()
            # All tasks completed normally.
            raw_results = all_work.result()
            return [
                r
                if isinstance(r, StepResult)
                else StepResult(
                    step_number=step_number,
                    agent_id=targets[i].agent_id,
                    agent_name=targets[i].agent_name,
                    task=targets[i].task,
                    response_text="",
                    success=False,
                    status=StepStatus.FAILED,
                    error_message=f"Unexpected error: {r}",
                )
                for i, r in enumerate(raw_results)
            ]

        # Cancellation fired first — collect whatever completed and
        # synthesize FAILED results for the rest.
        all_work.cancel()
        try:
            await all_work
        except (asyncio.CancelledError, Exception):
            pass

        results: list[StepResult] = []
        completed_ids: set[str] = set()
        for task in tasks:
            if task.done() and not task.cancelled():
                try:
                    r = task.result()
                    results.append(r)
                    completed_ids.add(r.agent_id)
                except Exception:
                    pass
            elif not task.done():
                task.cancel()
        for t in targets:
            if t.agent_id not in completed_ids:
                results.append(
                    StepResult(
                        step_number=step_number,
                        agent_id=t.agent_id,
                        agent_name=t.agent_name,
                        task=t.task,
                        response_text="",
                        success=False,
                        status=StepStatus.FAILED,
                        error_message="Agent dispatch was cancelled",
                    )
                )
        return results

    # ------------------------------------------------------------------
    # Clarify-cap fallback: choose the best agent from the trajectory
    # ------------------------------------------------------------------

    @staticmethod
    def _pick_best_fallback_agent(
        trajectory: SupervisorTrajectory,
        agent_registry: list[AgentProfile],
        healthy_ids: set[str],
    ) -> AgentProfile | None:
        """Pick the best agent when the clarify cap forces a delegation.

        Priority order:
        1. An agent that previously succeeded in this trajectory and is
           still healthy (most likely to handle the task correctly).
        2. A healthy agent that has not failed in this trajectory.
        3. Any healthy agent as a last resort.
        """
        healthy_map = {
            a.agent_id: a for a in agent_registry if a.agent_id in healthy_ids
        }
        if not healthy_map:
            return None

        failed_ids: set[str] = set()
        succeeded_id: str | None = None

        for entry in reversed(trajectory.entries):
            if entry.action.action != ActionType.DELEGATE:
                continue
            for result in entry.results:
                if result.success and result.agent_id in healthy_map:
                    succeeded_id = result.agent_id
                if not result.success:
                    failed_ids.add(result.agent_id)

        if succeeded_id:
            return healthy_map[succeeded_id]

        non_failed = [a for aid, a in healthy_map.items() if aid not in failed_ids]
        if non_failed:
            return non_failed[0]

        return next(iter(healthy_map.values()))

    # ------------------------------------------------------------------
    # Logging helper
    # ------------------------------------------------------------------

    async def _log_and_return(
        self,
        room_id: str,
        trajectory: SupervisorTrajectory,
        result: SupervisorRunResult,
        *,
        debate_mode: bool = False,
    ) -> SupervisorRunResult:
        """Log ``supervisor_run_completed`` and return the result."""
        logger.info(
            "supervisor_run_completed",
            extra={
                "room_id": room_id,
                "trajectory_id": trajectory.trajectory_id,
                "status": result.status,
                "total_steps": len(trajectory.entries),
                "total_supervisor_calls": trajectory.total_supervisor_calls,
                "debate_mode": debate_mode,
            },
        )

        # Phase 1: Emit terminal state for system:hybro
        if trajectory.system_agent_message_id and result.status != RunStatus.PAUSED:
            try:
                # If we're done, or failed, or canceled, or awaiting input (HITL)
                task_status = (
                    "completed"
                    if result.status == RunStatus.COMPLETED
                    else result.status.value
                )
                await self.delivery.send_task_update(
                    room_id=room_id,
                    message_id=trajectory.system_agent_message_id,
                    status=task_status,
                )

                # Update DB record
                db_msg = await self.message_reader.get_room_agent_message_by_message_id(
                    trajectory.system_agent_message_id
                )
                if (
                    db_msg
                    and db_msg.message_content
                    and db_msg.message_content.message_task
                ):
                    db_msg.message_content.message_task.status.state = (
                        system_task_state_from_runtime_status(task_status)
                    )
                    await self.message_writer.update_room_agent_message_with_new_message_content_by_message_id(
                        db_msg.message_id, db_msg.message_content
                    )
            except Exception:
                logger.warning(
                    "Failed to update terminal state for system:hybro", exc_info=True
                )

        return result

    # ------------------------------------------------------------------
    # Per-step trajectory checkpoint (crash recovery)
    # ------------------------------------------------------------------

    async def _checkpoint_trajectory(
        self,
        user_message_id: str,
        trajectory: SupervisorTrajectory,
        cached_user_message=None,
    ):
        """Persist the trajectory snapshot to the user message after each step.

        This enables crash recovery: on restart, a recovery job can scan for
        messages with ``supervisor_trajectory.status == "running"`` and
        re-trigger ``SupervisorExecutor.run(resumed_trajectory=...)``.

        Best-effort — checkpoint failures are logged but do not abort the loop.

        Returns the user message object so callers can cache it across steps.
        """
        try:
            user_message = cached_user_message
            if user_message is None:
                user_message = await self.message_reader.get_room_user_message_by_message_id(
                    user_message_id
                )
            if user_message:
                if not isinstance(user_message.extend_info, dict):
                    user_message.extend_info = {}
                envelope = self._orchestration_envelope_from_user_message(user_message)
                if self._orchestration_envelope_str(
                    envelope,
                    "orchestration_run_id",
                ) or envelope.get("orchestration_schema_version") == 2:
                    user_message.extend_info.pop("supervisor_trajectory", None)
                    user_message.extend_info.setdefault(
                        "orchestration_schema_version",
                        2,
                    )
                    run_id = self._orchestration_envelope_str(
                        envelope,
                        "orchestration_run_id",
                    )
                    if run_id:
                        user_message.extend_info["orchestration_run_id"] = run_id
                    user_message.extend_info["orchestration_status"] = (
                        trajectory.status.value
                    )
                    await self.message_writer.update_room_user_message_by_message_id(
                        user_message_id,
                        user_message,
                    )
                    return user_message
                user_message.extend_info["supervisor_trajectory"] = (
                    trajectory.model_dump(mode="json")
                )
                await self.message_writer.update_room_user_message_by_message_id(
                    user_message_id, user_message
                )
            return user_message
        except Exception as e:
            logger.warning(
                "supervisor_checkpoint_failed",
                extra={
                    "user_message_id": user_message_id,
                    "trajectory_id": trajectory.trajectory_id,
                    "error": str(e),
                },
            )
            return cached_user_message

    # ------------------------------------------------------------------
    # Reconcile PAUSED results against actual DB state
    # ------------------------------------------------------------------

    async def _reconcile_paused_results(
        self,
        paused_results: list[StepResult],
        trajectory: SupervisorTrajectory,
        room_id: str,
    ) -> None:
        """Upgrade PAUSED trajectory entries whose agent messages are already terminal in DB.

        This handles the race condition where a relay agent completes (via webhook)
        before the supervisor has saved the continuation. In that case the response
        handler's resume attempt finds no continuation and is a no-op, leaving the
        trajectory entry permanently PAUSED. This method detects and corrects that.
        """
        _TERMINAL = {"completed", "failed", "canceled", "rejected"}

        for pr in paused_results:
            msg_id = pr.agent_message_id or pr.paused_message_id
            if not msg_id:
                continue
            msg = await self.message_reader.get_room_agent_message_by_message_id(msg_id)
            if not msg:
                continue
            if msg.last_notified_state not in _TERMINAL:
                continue

            is_success = msg.last_notified_state == "completed"
            response_text = ""
            if msg.message_content and msg.message_content.message_text:
                response_text = msg.message_content.message_text

            for entry in trajectory.entries:
                for idx, result in enumerate(entry.results):
                    if (
                        result.status == StepStatus.PAUSED
                        and result.agent_message_id == msg_id
                    ):
                        entry.results[idx] = StepResult(
                            step_number=entry.step_number,
                            agent_id=result.agent_id,
                            agent_name=result.agent_name,
                            task=result.task,
                            response_text=response_text,
                            success=is_success,
                            status=StepStatus.SUCCESS
                            if is_success
                            else StepStatus.FAILED,
                            error_message=None if is_success else "Agent task failed",
                            agent_message_id=msg_id,
                            completed_at=utcnow(),
                        )
                        if entry.completed_at is None:
                            still_paused = any(
                                r.status == StepStatus.PAUSED for r in entry.results
                            )
                            if not still_paused:
                                entry.completed_at = utcnow()
                        break
                else:
                    continue
                break

            if is_success and response_text:
                await self._publish_agent_message_committed(
                    room_id=room_id,
                    agent_id=pr.agent_id,
                    agent_name=pr.agent_name or "Agent",
                    was_successful=True,
                    message_id=msg_id,
                )

            logger.info(
                "supervisor_reconciled_paused_result",
                extra={
                    "room_id": room_id,
                    "agent_message_id": msg_id,
                    "agent_name": pr.agent_name,
                    "resolved_state": msg.last_notified_state,
                    "trajectory_id": trajectory.trajectory_id,
                },
            )

    # ------------------------------------------------------------------
    # Unified interrupt state persistence
    # ------------------------------------------------------------------

    async def _save_interrupted_state(
        self,
        kind: InterruptKind,
        *,
        trajectory: SupervisorTrajectory,
        room_id: str,
        user_message_id: str,
        message_text: str,
        agent_registry: list[AgentProfile],
        room_config: RoomConfig,
        conversation_context: str | None,
        request_user_id: str | None,
        quoted_text: str | None = None,
        hitl_request_id: str | None = None,
        # For PUSH_NOTIFICATION / HITL_AGENT: list of paused results
        paused_results: list[StepResult] | None = None,
        # For HITL_AGENT / HITL_SUPERVISOR: single message_id
        message_id: str | None = None,
    ) -> bool:
        """Serialize trajectory + run inputs for any interrupt kind.

        For PUSH_NOTIFICATION: saves on each paused_results[i].paused_message_id.
        For HITL_AGENT: saves on message_id (the paused agent message).
        For HITL_SUPERVISOR: saves on message_id (the user message).

        Returns True if saved successfully.
        """
        interrupted_state = {
            "supervisor": True,
            "interrupt_kind": kind.value,
            "trajectory": trajectory.model_dump(mode="json"),
            "room_id": room_id,
            "user_message_id": user_message_id,
            "message_text": message_text,
            "agent_registry": [p.model_dump(mode="json") for p in agent_registry],
            "room_config": room_config.model_dump(mode="json"),
            "conversation_context": conversation_context,
            "request_user_id": request_user_id,
            "quoted_text": quoted_text,
        }
        if hitl_request_id is not None:
            interrupted_state["hitl_request_id"] = hitl_request_id

        # PUSH_NOTIFICATION: save on each paused agent message
        if kind == InterruptKind.PUSH_NOTIFICATION and paused_results:
            saved_any = False
            for pr in paused_results:
                if not pr.paused_message_id:
                    logger.error(
                        "SupervisorExecutor: PAUSED result has no paused_message_id — "
                        "cannot save continuation. agent=%s",
                        pr.agent_id,
                    )
                    continue
                success = await self.continuation_store.save_continuation_on_message(
                    pr.paused_message_id, interrupted_state
                )
                if success:
                    saved_any = True
                    logger.info(
                        "supervisor_interrupted_state_saved",
                        extra={
                            "room_id": room_id,
                            "message_id": pr.paused_message_id,
                            "interrupt_kind": kind.value,
                            "trajectory_id": trajectory.trajectory_id,
                        },
                    )
                else:
                    logger.error(
                        "SupervisorExecutor: Failed to save interrupted state "
                        "(kind=%s, message_id=%s)",
                        kind.value,
                        pr.paused_message_id,
                    )
            return saved_any

        # HITL_AGENT: save on the agent message
        if kind == InterruptKind.HITL_AGENT:
            if not message_id:
                logger.error("SupervisorExecutor: HITL_AGENT save missing message_id")
                return False
            success = await self.continuation_store.save_continuation_on_message(
                message_id, interrupted_state
            )
            if success:
                logger.info(
                    "supervisor_interrupted_state_saved",
                    extra={
                        "room_id": room_id,
                        "message_id": message_id,
                        "interrupt_kind": kind.value,
                        "trajectory_id": trajectory.trajectory_id,
                    },
                )
            else:
                logger.error(
                    "SupervisorExecutor: Failed to save interrupted state "
                    "(kind=%s, message_id=%s)",
                    kind.value,
                    message_id,
                )
            return success

        # HITL_SUPERVISOR: save on the user message
        if kind == InterruptKind.HITL_SUPERVISOR:
            if not message_id:
                logger.error(
                    "SupervisorExecutor: HITL_SUPERVISOR save missing message_id"
                )
                return False
            success = await self.continuation_store.save_continuation_on_user_message(
                message_id, interrupted_state
            )
            if success:
                logger.info(
                    "supervisor_interrupted_state_saved",
                    extra={
                        "room_id": room_id,
                        "message_id": message_id,
                        "interrupt_kind": kind.value,
                        "trajectory_id": trajectory.trajectory_id,
                    },
                )
            else:
                logger.error(
                    "SupervisorExecutor: Failed to save interrupted state "
                    "(kind=%s, message_id=%s)",
                    kind.value,
                    message_id,
                )
            return success

        logger.error("SupervisorExecutor: Unknown interrupt kind %s", kind)
        return False

    @staticmethod
    def _hitl_orchestration_kwargs(user_message) -> dict[str, object]:
        extend_info = getattr(user_message, "extend_info", None)
        if not isinstance(extend_info, dict):
            return {}

        envelope = extend_info
        for nested_key in ("orchestration", "orchestration_run"):
            nested = extend_info.get(nested_key)
            if isinstance(nested, dict):
                envelope = nested
                break

        kwargs: dict[str, object] = {}
        run_id = envelope.get("orchestration_run_id")
        if isinstance(run_id, str) and run_id.strip():
            kwargs["orchestration_run_id"] = run_id.strip()

        schema_version = envelope.get("orchestration_schema_version")
        if isinstance(schema_version, int):
            kwargs["orchestration_schema_version"] = schema_version

        return kwargs

    @staticmethod
    def _build_debate_task(
        original_task: str,
        prior_responses: list[tuple[str, str]],
        max_chars: int = 3000,
    ) -> str:
        """Build the debate-enriched task for the next agent.

        Delegates to shared SequentialDebateDispatcher.

        Args:
            original_task: The original user task
            prior_responses: List of (agent_name, response_text) tuples
            max_chars: Maximum characters to include from prior response

        Returns:
            The debate-enriched task prompt, or original_task if no prior responses
        """
        if not prior_responses:
            return original_task
        last_name, last_text = prior_responses[-1]
        return SequentialDebateDispatcher.build_debate_prompt(
            original_task=original_task,
            prior_agent_name=last_name,
            prior_response=last_text,
            max_chars=max_chars,
        )

    @staticmethod
    def _snapshot_debate_agents(
        agent_registry: list[AgentProfile],
        trajectory: SupervisorTrajectory,
        debate_rounds: int = 2,
    ) -> list[str]:
        """Initialize or restore debate participant snapshot.

        First call: records healthy agent IDs into trajectory.debate_agent_ids,
        repeated for each debate round (e.g. 2 agents × 2 rounds = [a1, a2, a1, a2]).
        Subsequent calls: returns the existing snapshot (idempotent).
        """
        if trajectory.debate_agent_ids is not None:
            return trajectory.debate_agent_ids
        num_rounds = debate_rounds or 1
        base_ids = [a.agent_id for a in agent_registry if a.is_healthy]
        ids = base_ids * num_rounds
        trajectory.debate_agent_ids = ids
        return ids

    @staticmethod
    def _get_remaining_debate_agent_ids(
        debate_agent_ids: list[str],
        trajectory: SupervisorTrajectory,
    ) -> list[str]:
        """Return agent IDs not yet dispatched (preserving original order).

        Supports multi-round debate where the same agent_id appears multiple
        times in debate_agent_ids (e.g. [a1, a2, a1, a2] for 2 rounds).
        Counts completed dispatches per agent and removes that many from the list.

        Inflight entries (DELEGATE with empty results) are NOT counted as
        dispatched — the crash happened before dispatch completed, so the
        agent needs to be re-dispatched.
        """
        from collections import Counter

        dispatch_counts: Counter[str] = Counter()
        for entry in trajectory.entries:
            if entry.action.action == ActionType.DELEGATE and entry.results:
                for target in entry.action.targets:
                    dispatch_counts[target.agent_id] += 1

        remaining: list[str] = []
        consume_counts: Counter[str] = Counter()
        for aid in debate_agent_ids:
            consume_counts[aid] += 1
            if consume_counts[aid] > dispatch_counts.get(aid, 0):
                remaining.append(aid)
        return remaining

    @staticmethod
    def _collect_prior_debate_responses(
        trajectory: SupervisorTrajectory,
    ) -> list[tuple[str, str]]:
        """Collect (agent_name, response_text) pairs in order, successful results only."""
        responses: list[tuple[str, str]] = []
        for entry in trajectory.entries:
            if entry.action.action != ActionType.DELEGATE:
                continue
            for result in entry.results:
                if (
                    result.success
                    and result.status == StepStatus.SUCCESS
                    and result.response_text
                ):
                    responses.append((result.agent_name, result.response_text))
        return responses
