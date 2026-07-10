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
import copy
import hashlib
from collections.abc import Callable, Mapping, Sequence
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from common.a2a_constants import SSEProcessingStatus
from common.config import settings as _settings
from common.message_commit_events import publish_message_committed
from common.utils.a2a_helpers import artifacts_to_dicts
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
from execution.orchestration.outcome_evaluator import (
    DelegationOutcomeEvaluator,
    canonical_content_fingerprint,
    invalidate_required_evidence,
)
from execution.orchestration.outcome_policy import OutcomeHistoryView
from execution.orchestration.planner import (
    OrchestrationPlanner,
    RoomSupervisorPlannerAdapter,
)
from execution.orchestration.planner_recovery import (
    record_recoverable_planner_rejection,
    resolve_open_planner_validation_failures,
)
from execution.orchestration.resources import OrchestrationResourceProvider
from execution.orchestration.result_ingestor import (
    AgentResultIngestor,
    AgentResultRead,
    related_open_failure_for_dispatch_intent,
)
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
    ParticipantSnapshot,
    PlannedDelegateTarget,
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


DEFAULT_DEBATE_ROUNDS = 2
DISPATCH_REF_PROJECTION_MAX_CHARS = 1600


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
        delegation_outcome_evaluator: DelegationOutcomeEvaluator | None = None,
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
        self.delegation_outcome_evaluator = (
            delegation_outcome_evaluator or DelegationOutcomeEvaluator()
        )
        self.result_ingestor = AgentResultIngestor()
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
                            SupervisorExecutor._delegate_target_from_intent(
                                intent,
                                agent_names.get(intent.agent_id) or intent.agent_id,
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
        status = SupervisorExecutor._step_status_from_state_output_status(
            output.status
        )
        if status is None:
            return None
        agent_id = output.agent_id or (intent.agent_id if intent else "")
        if not agent_id:
            return None
        task = intent.task if intent is not None else "Agent response"
        error_message = output.error
        if status == StepStatus.FAILED and not error_message:
            error_message = f"Agent output status: {output.status}"
        return StepResult(
            step_number=step_number,
            agent_id=agent_id,
            agent_name=agent_names.get(agent_id) or agent_id,
            task=task,
            response_text=output.text or "",
            success=status == StepStatus.SUCCESS,
            status=status,
            error_message=error_message,
            agent_message_id=output.agent_message_id,
            completed_at=utcnow(),
            a2a_task_id=output.a2a_task_id,
            a2a_context_id=output.a2a_context_id,
            status_message=output.status_message,
        )

    @staticmethod
    def _step_status_from_state_output_status(status: str) -> StepStatus | None:
        normalized = (status or "").strip().lower()
        if normalized in {
            StepStatus.SUCCESS.value,
            "completed",
            "complete",
            "succeeded",
            "done",
        }:
            return StepStatus.SUCCESS
        if normalized in {
            StepStatus.FAILED.value,
            "failure",
            "error",
            "errored",
            "canceled",
            "cancelled",
            "rejected",
            "expired",
            "timeout",
            "timed_out",
        }:
            return StepStatus.FAILED
        if normalized == StepStatus.PAUSED.value:
            return StepStatus.PAUSED
        if normalized == StepStatus.AWAITING_INPUT.value:
            return StepStatus.AWAITING_INPUT
        return None

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

    @property
    def result_ingestor(self) -> AgentResultIngestor:
        if not hasattr(self, "_result_ingestor"):
            self._result_ingestor = AgentResultIngestor()
        return self._result_ingestor

    @result_ingestor.setter
    def result_ingestor(self, value: AgentResultIngestor) -> None:
        self._result_ingestor = value

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

    async def _emit_supervisor_stage(
        self,
        *,
        room_id: str,
        user_message_id: str,
        details: str,
        stage: str,
        client_request_id: str | None = None,
        agents: list[dict] | None = None,
    ) -> None:
        try:
            await self._emit_processing_status(
                room_id=room_id,
                status=SSEProcessingStatus.PROCESSING,
                message_id=user_message_id,
                lifecycle_message_id=user_message_id,
                client_request_id=client_request_id,
                details=details,
                agents=agents,
            )
        except Exception:
            logger.debug("SSE stage notification failed (%s)", stage, exc_info=True)

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
        allow_awaiting_user_recovery: bool = False,
        user_message=None,
    ) -> SupervisorRunResult:
        """Execute the supervisor loop using persisted orchestration run state."""

        terminal_result = await self._v2_terminal_result_if_done(room_id, state)
        if terminal_result is not None:
            return terminal_result
        if (
            state.status == OrchestrationStatus.INGESTING
            and state.pending_hitl_request_ids
        ):
            pending_action = self._v2_pending_hitl_planner_action(state)
            if pending_action is not None:
                return await self._run_ask_user_action(
                    state=state,
                    planner_action=pending_action,
                    agent_registry=agent_registry,
                    room_config=room_config,
                    room_id=room_id,
                    user_message_id=user_message_id,
                    message_text=message_text,
                    conversation_context=conversation_context,
                    request_user_id=request_user_id,
                    quoted_text=quoted_text,
                    resume_pending_artifacts=True,
                )
            state = await self._mark_v2_terminal(
                state,
                OrchestrationStatus.FAILED,
                reason=(
                    "INGESTING checkpoint has pending HITL requests but no valid "
                    "ASK_USER planner action"
                ),
            )
            return await self._log_state_and_return(
                room_id,
                state,
                self._state_run_result(status=RunStatus.FAILED, state=state),
            )
        if (
            state.status == OrchestrationStatus.AWAITING_USER
            and (
                self._has_open_pending_hitl(state)
                or self._has_recoverable_supervisor_hitl_question(state)
            )
        ):
            return await self._log_state_and_return(
                room_id,
                state,
                self._state_run_result(
                    status=RunStatus.AWAITING_INPUT,
                    state=state,
                ),
                    )

        if (
            state.status == OrchestrationStatus.AWAITING_USER
            and not self._has_current_step_recoverable_intents(state)
            and not self._has_open_pending_hitl(state)
            and not allow_awaiting_user_recovery
        ):
            state = await self._mark_v2_terminal(
                state,
                OrchestrationStatus.FAILED,
                reason="awaiting_user_without_open_hitl",
            )
            return await self._log_state_and_return(
                room_id,
                state,
                self._state_run_result(
                    status=RunStatus.FAILED,
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
                await self._emit_supervisor_stage(
                    room_id=room_id,
                    user_message_id=user_message_id,
                    client_request_id=state.client_request_id,
                    details="Planning next action...",
                    stage="planning",
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
                if (
                    isinstance(exc, PlannerActionValidationError)
                    and not exc.recoverable
                ):
                    terminal_status = (
                        OrchestrationStatus.BUDGET_EXHAUSTED
                        if exc.code == "step_budget_exhausted"
                        else OrchestrationStatus.FAILED
                    )
                    state = await self._mark_v2_terminal(
                        state,
                        terminal_status,
                        reason=str(exc),
                    )
                    return await self._log_state_and_return(
                        room_id,
                        state,
                        self._state_run_result(status=RunStatus.FAILED, state=state),
                    )
                state, exhausted = await self._record_v2_planner_rejection(
                    state,
                    error_code=getattr(exc, "code", "planner_output_invalid"),
                    error_message=str(exc),
                    planner_action=None,
                    stage="adapter",
                )
                if exhausted or state.steps_used >= state.step_budget:
                    state = await self._mark_v2_terminal(
                        state,
                        OrchestrationStatus.FAILED,
                        reason=str(exc),
                    )
                    return await self._log_state_and_return(
                        room_id,
                        state,
                        self._state_run_result(status=RunStatus.FAILED, state=state),
                    )
                continue

            planner_action = self._apply_participant_turn_policy(
                state,
                planner_action,
            )
            try:
                planner_action = PlannerActionValidator.validate(
                    planner_action,
                    run_state=state,
                )
            except PlannerActionValidationError as exc:
                if not exc.recoverable:
                    terminal_status = (
                        OrchestrationStatus.BUDGET_EXHAUSTED
                        if exc.code == "step_budget_exhausted"
                        else OrchestrationStatus.FAILED
                    )
                    state = await self._mark_v2_terminal(
                        state,
                        terminal_status,
                        reason=str(exc),
                    )
                    return await self._log_state_and_return(
                        room_id,
                        state,
                        self._state_run_result(status=RunStatus.FAILED, state=state),
                    )
                state, exhausted = await self._record_v2_planner_rejection(
                    state,
                    error_code=exc.code,
                    error_message=str(exc),
                    planner_action=planner_action,
                    stage="state_validation",
                )
                if exhausted or state.steps_used >= state.step_budget:
                    state = await self._mark_v2_terminal(
                        state,
                        OrchestrationStatus.FAILED,
                        reason=str(exc),
                    )
                    return await self._log_state_and_return(
                        room_id,
                        state,
                        self._state_run_result(status=RunStatus.FAILED, state=state),
                    )
                continue
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
                    def record_completion_evidence(
                        updated: OrchestrationRunState,
                        evidence=planner_action.completion_evidence,
                    ) -> None:
                        updated.completion_evidence = evidence

                    state = await self._mark_v2_terminal(
                        state,
                        OrchestrationStatus.COMPLETED,
                        reason=planner_action.reasoning,
                        mutate=record_completion_evidence,
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
    def _debate_participant_snapshot(
        agent_registry: list[AgentProfile],
        *,
        debate_rounds: int,
    ) -> ParticipantSnapshot | None:
        ordered_once = [
            agent.agent_id
            for agent in agent_registry
            if agent.is_healthy and agent.agent_id
        ]
        if not ordered_once:
            return None
        rounds = max(debate_rounds, 1)
        return ParticipantSnapshot(
            mode="debate",
            ordered_agent_ids=ordered_once * rounds,
            max_rounds=rounds,
            turn_policy="debate_rounds",
        )

    def _configured_debate_rounds(self) -> int:
        value = getattr(self, "debate_rounds", None)
        if isinstance(value, int) and value > 0:
            return value
        return DEFAULT_DEBATE_ROUNDS

    @staticmethod
    def _next_participant_agent_id(state: OrchestrationRunState) -> str | None:
        snapshot = state.participant_snapshot
        if snapshot is None or snapshot.turn_policy not in {
            "debate_rounds",
            "sequential_rounds",
        }:
            return None

        completed_counts: dict[str, int] = {}
        if state.agent_outputs:
            for output in state.agent_outputs:
                status = SupervisorExecutor._step_status_from_state_output_status(
                    output.status
                )
                if status in {StepStatus.SUCCESS, StepStatus.FAILED}:
                    completed_counts[output.agent_id] = (
                        completed_counts.get(output.agent_id, 0) + 1
                    )
        else:
            for agent_id in snapshot.completed_agent_ids:
                completed_counts[agent_id] = completed_counts.get(agent_id, 0) + 1

        remaining_completed_counts = dict(completed_counts)
        for agent_id in snapshot.ordered_agent_ids:
            if remaining_completed_counts.get(agent_id, 0) > 0:
                remaining_completed_counts[agent_id] -= 1
                continue
            return agent_id
        return None

    @staticmethod
    def _participant_agent_name(
        state: OrchestrationRunState,
        agent_id: str,
    ) -> str:
        if state.candidate_scope is not None:
            for candidate in state.candidate_scope.agents:
                if candidate.agent_id == agent_id:
                    return candidate.name or agent_id
        return agent_id

    @staticmethod
    def _policy_task_for_next_participant(
        state: OrchestrationRunState,
        planner_action: PlannerAction,
    ) -> str:
        for target in planner_action.targets:
            if target.task.strip():
                return target.task
        if state.goal.strip():
            return state.goal
        if planner_action.reasoning.strip():
            return planner_action.reasoning
        return "Respond to the user's request"

    @staticmethod
    def _delegate_action_for_next_participant(
        state: OrchestrationRunState,
        planner_action: PlannerAction,
        next_agent_id: str,
    ) -> PlannerAction:
        reasoning = planner_action.reasoning.strip()
        if reasoning:
            reasoning = (
                f"{reasoning} Routed to next required participant "
                f"{next_agent_id} by turn policy."
            )
        else:
            reasoning = (
                f"Routed to next required participant {next_agent_id} "
                "by turn policy."
            )
        return planner_action.model_copy(
            update={
                "action": PlannerActionType.DELEGATE,
                "reasoning": reasoning,
                "targets": [
                    PlannedDelegateTarget(
                        agent_id=next_agent_id,
                        agent_name=SupervisorExecutor._participant_agent_name(
                            state,
                            next_agent_id,
                        ),
                        task=SupervisorExecutor._policy_task_for_next_participant(
                            state,
                            planner_action,
                        ),
                    )
                ],
                "questions": [],
                "synthesis_instruction": None,
                "failure_reason": None,
                "completion_evidence": None,
            }
        )

    @staticmethod
    def _apply_participant_turn_policy(
        state: OrchestrationRunState,
        planner_action: PlannerAction,
    ) -> PlannerAction:
        next_agent_id = SupervisorExecutor._next_participant_agent_id(state)
        if next_agent_id is None:
            return planner_action

        if planner_action.action == PlannerActionType.DELEGATE:
            for target in planner_action.targets:
                if target.agent_id == next_agent_id:
                    return planner_action.model_copy(update={"targets": [target]})
            return SupervisorExecutor._delegate_action_for_next_participant(
                state,
                planner_action,
                next_agent_id,
            )

        if planner_action.action in {
            PlannerActionType.COMPLETE,
            PlannerActionType.SYNTHESIZE,
        }:
            return SupervisorExecutor._delegate_action_for_next_participant(
                state,
                planner_action,
                next_agent_id,
            )

        return planner_action

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

    @staticmethod
    def _validate_run_binding(
        state: OrchestrationRunState,
        *,
        room_id: str,
        user_message_id: str,
    ) -> OrchestrationRunState:
        if state.room_id != room_id or state.user_message_id != user_message_id:
            raise ValueError(
                "orchestration run binding mismatch: "
                f"run_id={state.run_id!r}, room_id={state.room_id!r}, "
                f"user_message_id={state.user_message_id!r}"
            )
        return state

    async def _reconcile_loaded_run_state(
        self,
        state: OrchestrationRunState,
        *,
        room_id: str,
        user_message_id: str,
        agent_registry: list[AgentProfile],
        room_config: RoomConfig | None,
    ) -> OrchestrationRunState:
        state = self._validate_run_binding(
            state,
            room_id=room_id,
            user_message_id=user_message_id,
        )
        if (
            state.participant_snapshot is not None
            or not room_config
            or not getattr(room_config, "is_debate_mode", False)
            or not agent_registry
        ):
            return state

        updated = state.model_copy(deep=True)
        updated.participant_snapshot = self._debate_participant_snapshot(
            self._v2_candidate_scope(updated, agent_registry),
            debate_rounds=self._configured_debate_rounds(),
        )
        if updated.participant_snapshot is None:
            return state
        updated.step_budget = max(
            updated.step_budget,
            len(updated.participant_snapshot.ordered_agent_ids) + 1,
        )
        updated.state_version = state.state_version + 1
        updated.updated_at = utcnow()
        try:
            return await self.run_store.save_state(
                updated,
                expected_version=state.state_version,
            )
        except OrchestrationStoreConflict:
            latest = await self.run_store.get_run(state.run_id)
            if latest is None:
                raise
            return self._validate_run_binding(
                latest,
                room_id=room_id,
                user_message_id=user_message_id,
            )

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
        envelope = self._orchestration_envelope_from_user_message(user_message)
        effective_run_id = (
            self._orchestration_envelope_str(envelope, "orchestration_run_id")
            or user_message_id
        )
        existing = await self.run_store.get_run(effective_run_id)
        if existing is not None:
            return await self._reconcile_loaded_run_state(
                existing,
                room_id=room_id,
                user_message_id=user_message_id,
                agent_registry=agent_registry,
                room_config=room_config,
            )
        existing = await self.run_store.get_latest_by_user_message_id(
            user_message_id
        )
        if existing is not None:
            return await self._reconcile_loaded_run_state(
                existing,
                room_id=room_id,
                user_message_id=user_message_id,
                agent_registry=agent_registry,
                room_config=room_config,
            )

        state = await self.run_store.reconstruct_from_envelope(
            run_id=effective_run_id,
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
        if (
            room_config
            and getattr(room_config, "is_debate_mode", False)
            and agent_registry
        ):
            state.participant_snapshot = self._debate_participant_snapshot(
                agent_registry,
                debate_rounds=self._configured_debate_rounds(),
            )
            debate_agent_ids = [
                agent_id
                for agent_id in (
                    state.participant_snapshot.ordered_agent_ids
                    if state.participant_snapshot is not None
                    else []
                )
            ]
            state.step_budget = max(state.step_budget, len(debate_agent_ids) + 1)
        try:
            return await self.run_store.create_run(state)
        except OrchestrationStoreConflict:
            existing = await self.run_store.get_run(effective_run_id)
            if existing is not None:
                return await self._reconcile_loaded_run_state(
                    existing,
                    room_id=room_id,
                    user_message_id=user_message_id,
                    agent_registry=agent_registry,
                    room_config=room_config,
                )
            existing = await self.run_store.get_latest_by_user_message_id(
                user_message_id
            )
            if existing is not None:
                return await self._reconcile_loaded_run_state(
                    existing,
                    room_id=room_id,
                    user_message_id=user_message_id,
                    agent_registry=agent_registry,
                    room_config=room_config,
                )
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
        logger.info(
            "supervisor_planner_decision",
            extra={
                "run_id": state.run_id,
                "room_id": state.room_id,
                "user_message_id": state.user_message_id,
                "action": planner_action.action.value,
                "target_agent_ids": [
                    target.agent_id for target in planner_action.targets
                ],
                "artifact_refs": [
                    ref.ref_id
                    for target in planner_action.targets
                    for ref in target.artifact_refs
                ],
                "attachment_refs": [
                    ref.ref_id
                    for target in planner_action.targets
                    for ref in target.attachment_refs
                ],
                "open_failure_count": len(
                    [
                        failure
                        for failure in state.open_failures
                        if failure.status == "open"
                    ]
                ),
            },
        )

        def mutate(updated: OrchestrationRunState) -> None:
            resolve_open_planner_validation_failures(updated)
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

    async def _record_v2_planner_rejection(
        self,
        state: OrchestrationRunState,
        *,
        error_code: str,
        error_message: str,
        planner_action: PlannerAction | None,
        stage: str,
    ) -> tuple[OrchestrationRunState, bool]:
        payload: dict[str, Any] = {
            "stage": stage,
            "error_code": error_code,
        }
        outcome: dict[str, bool] = {}

        def mutate(updated: OrchestrationRunState) -> None:
            failure, exhausted = record_recoverable_planner_rejection(
                updated,
                error_code=error_code,
                error_message=error_message,
                planner_action=planner_action,
                stage=stage,
            )
            payload.update(
                {
                    "failure_id": failure.failure_id,
                    "retry_count": failure.retry_count,
                    "exhausted": exhausted,
                }
            )
            if planner_action is not None:
                payload["action"] = planner_action.action.value
            outcome["exhausted"] = exhausted

        saved = await self._save_v2_state(
            state,
            event_type=OrchestrationEventType.PLANNER_ACTION_REJECTED,
            payload=payload,
            mutate=mutate,
        )
        return saved, outcome["exhausted"]

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
        exhausted_failure = self._exhausted_recoverable_failure_for_intents(
            state,
            intents,
        )
        if exhausted_failure is not None:
            logger.info(
                "orchestration_recovery_retry_blocked",
                extra={
                    "run_id": state.run_id,
                    "failure_id": exhausted_failure.failure_id,
                    "dispatch_intent_id": exhausted_failure.dispatch_intent_id,
                    "retry_count": exhausted_failure.retry_count,
                    "max_retries": exhausted_failure.max_retries,
                    "error_code": exhausted_failure.error_code,
                },
            )
            state = await self._mark_v2_terminal(
                state,
                OrchestrationStatus.FAILED,
                reason="recoverable failure retry budget exhausted",
            )
            return state, RunStatus.FAILED

        await self._emit_supervisor_stage(
            room_id=room_id,
            user_message_id=user_message_id,
            client_request_id=state.client_request_id,
            details=f"Delegating to {len(action.targets)} agent(s)...",
            stage="delegating",
            agents=[
                {
                    "agent_id": target.agent_id,
                    "agent_name": target.agent_name,
                }
                for target in action.targets
            ],
        )

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
        await self._emit_supervisor_stage(
            room_id=room_id,
            user_message_id=user_message_id,
            client_request_id=state.client_request_id,
            details="Evaluating agent results...",
            stage="evaluating",
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
            state = await self._ingest_v2_results(
                state,
                results,
                status=OrchestrationStatus.WAITING_AGENT,
                advance_step=False,
            )
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
            state = await self._ingest_v2_results(
                state,
                results,
                status=OrchestrationStatus.WAITING_AGENT,
                advance_step=False,
            )
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
            return state, RunStatus.PAUSED

        state = await self._ingest_v2_results(
            state,
            results,
            status=OrchestrationStatus.RUNNING,
            advance_step=True,
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
            "expired",
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
                self._delegate_target_from_intent(
                    intent,
                    agent_names.get(intent.agent_id) or intent.agent_id,
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
                state = await self._ingest_v2_results(
                    state,
                    results,
                    status=OrchestrationStatus.WAITING_AGENT,
                    advance_step=False,
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
            state = await self._ingest_v2_results(
                state,
                results,
                status=OrchestrationStatus.WAITING_AGENT,
                advance_step=False,
            )
            if paused:
                trajectory.status = TrajectoryStatus.AWAITING_INPUT
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
            state = await self._ingest_v2_results(
                state,
                results,
                status=OrchestrationStatus.WAITING_AGENT,
                advance_step=False,
            )
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

        state = await self._ingest_v2_results(
            state,
            results,
            status=OrchestrationStatus.RUNNING,
            advance_step=True,
        )
        return state, None

    @staticmethod
    def _v2_result_from_output_record(
        intent: DispatchIntent,
        output: AgentOutputRecord | None,
        agent_names: dict[str, str],
        step_number: int,
    ) -> StepResult | None:
        if output is None:
            return None
        status = SupervisorExecutor._step_status_from_state_output_status(
            output.status
        )
        if status not in {
            StepStatus.SUCCESS,
            StepStatus.FAILED,
            StepStatus.PAUSED,
            StepStatus.AWAITING_INPUT,
        }:
            return None
        error_message = output.error
        if status == StepStatus.FAILED and not error_message:
            error_message = f"Agent output status: {output.status}"
        return StepResult(
            step_number=step_number,
            agent_id=output.agent_id or intent.agent_id,
            agent_name=agent_names.get(output.agent_id or intent.agent_id),
            task=intent.task,
            response_text=output.text or "",
            success=status == StepStatus.SUCCESS,
            status=status,
            error_message=error_message,
            agent_message_id=output.agent_message_id,
            completed_at=utcnow(),
            a2a_task_id=output.a2a_task_id,
            a2a_context_id=output.a2a_context_id,
            status_message=output.status_message,
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
        if not msg:
            return None

        task = getattr(msg.message_content, "message_task", None) if msg else None

        def _field_from_task(
            payload: object,
            *keys: str,
        ) -> object | None:
            value = payload
            for key in keys:
                if value is None:
                    return None
                if isinstance(value, Mapping):
                    value = value.get(key)
                else:
                    value = getattr(value, key, None)
            return value

        terminal_states = {"completed", "failed", "canceled", "rejected", "expired"}
        interactive_states = {
            "input-required",
            "auth-required",
        }

        last_state = getattr(msg, "last_notified_state", None)
        if last_state is None:
            last_state = _field_from_task(task, "status", "state")
        if not isinstance(last_state, str):
            return None
        normalized_state = last_state.strip().lower()
        if normalized_state not in terminal_states | interactive_states:
            return None

        last_state = normalized_state
        is_input_required = last_state in interactive_states
        is_success = last_state == "completed"
        response_text = ""
        message_content = getattr(msg, "message_content", None)
        if message_content and getattr(message_content, "message_text", None):
            response_text = message_content.message_text

        task_metadata = (
            _field_from_task(task, "metadata")
            if task is not None
            else None
        )
        task_metadata_dict = task_metadata if isinstance(task_metadata, Mapping) else {}
        status_message = _field_from_task(task, "status", "message", "message_text")
        if not isinstance(status_message, str):
            status_message = _field_from_task(task, "metadata", "hitl_prompt")
        if not isinstance(status_message, str):
            status_message = None
        response_status_message = (
            status_message.strip() if isinstance(status_message, str) else None
        )

        return StepResult(
            step_number=step_number,
            agent_id=intent.agent_id,
            agent_name=agent_names.get(intent.agent_id),
            task=intent.task,
            response_text=response_text,
            success=is_success,
            status=(
                StepStatus.AWAITING_INPUT
                if is_input_required
                else StepStatus.SUCCESS if is_success else StepStatus.FAILED
            ),
            error_message=None
            if is_success or is_input_required
            else "Agent task failed",
            status_message=response_status_message,
            a2a_task_id=_field_from_task(task_metadata_dict, "hitl_a2a_task_id"),
            a2a_context_id=_field_from_task(task_metadata_dict, "hitl_a2a_context_id"),
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
                    SupervisorExecutor._delegate_target_from_intent(
                        intent,
                        agent_names.get(intent.agent_id) or intent.agent_id,
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

        awaiting_result = awaiting[0]
        continuation_message_id = (
            awaiting_result.paused_message_id
            or awaiting_result.agent_message_id
        )
        display_message_id = (
            awaiting_result.agent_message_id
            or awaiting_result.paused_message_id
        )
        hitl_prompt = (
            awaiting_result.status_message
            or "The agent needs additional information."
        )
        if not continuation_message_id:
            trajectory.status = TrajectoryStatus.FAILED
            state = await self._mark_v2_terminal(
                state,
                OrchestrationStatus.FAILED,
                reason="v2 agent HITL result missing continuation message id",
            )
            return state, RunStatus.FAILED

        request: SimpleNamespace | None = None

        async def cleanup_hitl_request(request_id: str) -> bool:
            cancel_request = getattr(self.hitl_coordinator, "cancel_request", None)
            if cancel_request is None:
                return True
            try:
                await cancel_request(request_id, room_id)
            except Exception:
                logger.warning(
                    "Failed to cancel orphaned v2 agent HITL request %s",
                    request_id,
                )
                return False
            return True

        def failed_agent_cleanup_mutation(
            failed_cancel_request_ids: list[str],
        ):
            created_request_ids = [request.request_id] if request is not None else []
            prompt_by_request_id = {
                request_id: hitl_prompt for request_id in created_request_ids
            }
            extra_by_request_id = {
                request_id: {
                    "agent_id": awaiting_result.agent_id,
                    "agent_name": awaiting_result.agent_name,
                    "continuation_message_id": continuation_message_id,
                    "display_message_id": display_message_id,
                    "a2a_task_id": awaiting_result.a2a_task_id,
                    "a2a_context_id": awaiting_result.a2a_context_id,
                }
                for request_id in created_request_ids
            }

            def mutate(updated: OrchestrationRunState) -> None:
                self._mark_failed_hitl_cleanup_state(
                    updated,
                    created_request_ids=created_request_ids,
                    failed_cancel_request_ids=failed_cancel_request_ids,
                    source="agent",
                    prompt_by_request_id=prompt_by_request_id,
                    extra_by_request_id=extra_by_request_id,
                )

            return mutate

        try:
            request = await self.hitl_coordinator.request_input(
                room_id=room_id,
                user_message_id=user_message_id,
                source="agent",
                prompt=hitl_prompt,
                agent_id=awaiting_result.agent_id,
                agent_name=awaiting_result.agent_name,
                a2a_task_id=awaiting_result.a2a_task_id,
                a2a_context_id=awaiting_result.a2a_context_id,
                continuation_message_id=continuation_message_id,
                display_message_id=display_message_id,
                orchestration_run_id=state.run_id,
                orchestration_schema_version=state.schema_version,
            )
        except Exception as exc:
            request_id = getattr(exc, "request_id", None)
            if request_id is not None:
                request = SimpleNamespace(request_id=request_id)
            failed_cancel_request_ids: list[str] = []
            if request is not None:
                canceled = await cleanup_hitl_request(request.request_id)
                if not canceled:
                    failed_cancel_request_ids.append(request.request_id)
            trajectory.status = TrajectoryStatus.FAILED
            state = await self._mark_v2_terminal(
                state,
                OrchestrationStatus.FAILED,
                reason="failed to create v2 agent HITL request",
                mutate=failed_agent_cleanup_mutation(failed_cancel_request_ids),
            )
            return state, RunStatus.FAILED
        if request is None:
            trajectory.status = TrajectoryStatus.FAILED
            state = await self._mark_v2_terminal(
                state,
                OrchestrationStatus.FAILED,
                reason="failed to create v2 agent HITL request",
            )
            return state, RunStatus.FAILED

        try:
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
        except Exception:
            failed_cancel_request_ids: list[str] = []
            if request is not None:
                await self._clear_continuation_state(
                    message_id=continuation_message_id,
                )
                canceled = await cleanup_hitl_request(request.request_id)
                if not canceled:
                    failed_cancel_request_ids.append(request.request_id)
            trajectory.status = TrajectoryStatus.FAILED
            state = await self._mark_v2_terminal(
                state,
                OrchestrationStatus.FAILED,
                reason="failed to save v2 agent HITL continuation",
                mutate=failed_agent_cleanup_mutation(failed_cancel_request_ids),
            )
            return state, RunStatus.FAILED

        if not saved:
            failed_cancel_request_ids: list[str] = []
            if request is not None:
                await self._clear_continuation_state(
                    message_id=continuation_message_id,
                )
                canceled = await cleanup_hitl_request(request.request_id)
                if not canceled:
                    failed_cancel_request_ids.append(request.request_id)
            trajectory.status = TrajectoryStatus.FAILED
            state = await self._mark_v2_terminal(
                state,
                OrchestrationStatus.FAILED,
                reason="failed to save v2 agent HITL continuation",
                mutate=failed_agent_cleanup_mutation(failed_cancel_request_ids),
            )
            return state, RunStatus.FAILED

        def mark_awaiting_user(updated: OrchestrationRunState) -> None:
            updated.status = OrchestrationStatus.AWAITING_USER
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
            self._clear_stale_pending_hitl_request_ids(updated)

        try:
            state = await self._save_v2_state(
                state,
                event_type=OrchestrationEventType.HITL_REQUESTED,
                payload={
                    "status": OrchestrationStatus.AWAITING_USER.value,
                    "request_ids": [request.request_id],
                },
                mutate=mark_awaiting_user,
            )
        except Exception:
            failed_cancel_request_ids: list[str] = []
            canceled = await cleanup_hitl_request(request.request_id)
            if not canceled:
                failed_cancel_request_ids.append(request.request_id)
            await self._clear_continuation_state(
                message_id=continuation_message_id,
            )
            trajectory.status = TrajectoryStatus.FAILED
            state = await self._mark_v2_terminal(
                state,
                OrchestrationStatus.FAILED,
                reason="failed to persist v2 agent HITL state",
                mutate=failed_agent_cleanup_mutation(failed_cancel_request_ids),
            )
            return state, RunStatus.FAILED

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

    async def _clear_continuation_state(
        self,
        *,
        message_id: str,
        to_user_message: bool = False,
    ) -> None:
        """Best-effort clear a continuation record saved for HITL rollback."""
        if not message_id:
            return
        try:
            clear_callback = (
                getattr(self.continuation_store, "get_and_clear_continuation_on_user_message", None)
                if to_user_message
                else getattr(self.continuation_store, "get_and_clear_continuation_on_message", None)
            )
            if clear_callback is None:
                return
            await clear_callback(message_id)
        except Exception:
            logger.warning(
                "Failed to clear continuation state",
                extra={
                    "message_id": message_id,
                    "to_user_message": to_user_message,
                },
                exc_info=True,
            )

    @staticmethod
    def _supervisor_hitl_questions_from_resumed_trajectory(
        resumed_trajectory: SupervisorTrajectory | None,
    ) -> list[dict[str, Any]]:
        if resumed_trajectory is None:
            return []

        recovered: list[dict[str, Any]] = []
        for entry in resumed_trajectory.entries:
            action = entry.action
            if action.action != ActionType.CLARIFY:
                continue
            questions = list(action.questions or [])
            if not questions and action.clarification_question:
                questions.append(
                    ClarifyQuestion(
                        prompt=action.clarification_question,
                        prompt_type=action.prompt_type,
                        choices=action.choices,
                    )
                )
            for question in questions:
                if not question.prompt:
                    continue
                recovered.append(
                    {
                        "source": "supervisor",
                        "step": entry.step_number,
                        "prompt": question.prompt,
                        "prompt_type": question.prompt_type,
                        "choices": question.choices,
                        "status": "open",
                        "created_at": (
                            entry.started_at.isoformat()
                            if entry.started_at
                            else utcnow().isoformat()
                        ),
                    }
                )
        return recovered

    @classmethod
    def _recoverable_supervisor_hitl_questions(
        cls,
        state: OrchestrationRunState,
        resumed_trajectory: SupervisorTrajectory | None,
    ) -> list[dict[str, Any]]:
        recovered = [
            dict(question)
            for question in state.open_questions
            if isinstance(question, Mapping)
            and question.get("source") == "supervisor"
            and question.get("status") in {"open", "creating"}
            and not question.get("resolved")
        ]
        if len(recovered) == 1:
            return recovered
        trajectory_questions = cls._supervisor_hitl_questions_from_resumed_trajectory(
            resumed_trajectory
        )
        if len(trajectory_questions) != 1:
            return []
        if not recovered:
            return trajectory_questions
        recovered_prompt = trajectory_questions[0].get("prompt")
        matching = [
            question
            for question in recovered
            if question.get("prompt") == recovered_prompt
        ]
        return matching if len(matching) == 1 else []

    async def _resolve_v2_hitl_if_answered(
        self,
        state: OrchestrationRunState,
        *,
        user_message=None,
        resumed_trajectory: SupervisorTrajectory | None = None,
    ) -> OrchestrationRunState:
        if state.status in TERMINAL_ORCHESTRATION_STATUSES:
            return state
        answer = self._hitl_answer_from_run_request(
            user_message=user_message,
            resumed_trajectory=resumed_trajectory,
        )
        if not answer:
            return state

        resolved_request_ids = self._hitl_request_ids_to_resolve_from_answer(
            state,
            request_id=self._hitl_request_id_from_run_request(
                user_message=user_message,
                resumed_trajectory=resumed_trajectory,
            ),
        )
        recovered_supervisor_questions: list[dict[str, Any]] = []
        if not resolved_request_ids:
            recovered_supervisor_questions = (
                self._recoverable_supervisor_hitl_questions(
                    state,
                    resumed_trajectory,
                )
            )
            if not recovered_supervisor_questions:
                return state

        resolved_at = utcnow().isoformat()
        resolved_recoverable_status = (
            OrchestrationStatus.WAITING_AGENT
            if self._has_current_step_recoverable_intents(state)
            else OrchestrationStatus.RUNNING
        )

        def resolve_hitl(updated: OrchestrationRunState) -> None:
            prompts: list[str] = []
            recovered_prompts: list[str] = []
            for recovered_question in recovered_supervisor_questions:
                prompt = recovered_question.get("prompt")
                if isinstance(prompt, str) and prompt:
                    recovered_prompts.append(prompt)
            resolved_existing = False
            for question in updated.open_questions:
                if resolved_request_ids:
                    matches_question = (
                        question.get("request_id") in resolved_request_ids
                    )
                else:
                    recovered_question = recovered_supervisor_questions[0]
                    matches_question = (
                        question.get("source") == "supervisor"
                        and question.get("status") in {"open", "creating"}
                        and not question.get("resolved")
                        and question.get("prompt")
                        == recovered_question.get("prompt")
                        and question.get("step") == recovered_question.get("step")
                    )
                if not matches_question:
                    continue
                question["status"] = "resolved"
                question["resolved"] = True
                question["answer"] = answer
                question["resolved_at"] = resolved_at
                resolved_existing = True
                prompt = question.get("prompt")
                if isinstance(prompt, str) and prompt:
                    prompts.append(prompt)
            if not resolved_request_ids and not resolved_existing:
                for recovered_question in recovered_supervisor_questions:
                    resolved_question = dict(recovered_question)
                    resolved_question["status"] = "resolved"
                    resolved_question["resolved"] = True
                    resolved_question["answer"] = answer
                    resolved_question["resolved_at"] = resolved_at
                    updated.open_questions.append(resolved_question)
                prompts.extend(recovered_prompts)
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
            updated.pending_hitl_request_ids = [
                request_id
                for request_id in updated.pending_hitl_request_ids
                if request_id not in resolved_request_ids
            ]
            self._clear_stale_pending_hitl_request_ids(updated)
            if self._has_open_pending_hitl(updated):
                updated.status = OrchestrationStatus.AWAITING_USER
            else:
                updated.status = resolved_recoverable_status

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
    def _hitl_request_id_from_run_request(
        cls,
        *,
        user_message=None,
        resumed_trajectory: SupervisorTrajectory | None = None,
    ) -> str | None:
        extend_info = getattr(user_message, "extend_info", None)
        if isinstance(extend_info, Mapping):
            for envelope in cls._hitl_answer_envelopes(extend_info):
                for key in ("hitl_request_id", "hitl_id"):
                    value = envelope.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
        if resumed_trajectory is not None:
            value = getattr(resumed_trajectory, "hitl_request_id", None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _hitl_request_ids_to_resolve_from_answer(
        state: OrchestrationRunState,
        *,
        request_id: str | None,
    ) -> list[str]:
        pending_request_ids = list(state.pending_hitl_request_ids)
        if not pending_request_ids:
            return []
        if request_id and request_id in pending_request_ids:
            return [request_id]
        pending_set = set(pending_request_ids)
        open_pending_questions = [
            question
            for question in state.open_questions
            if isinstance(question, Mapping)
            and question.get("status") == "open"
            and question.get("request_id") in pending_set
        ]
        if (
            open_pending_questions
            and len(open_pending_questions) == len(pending_set)
            and all(
                question.get("source") == "supervisor"
                for question in open_pending_questions
            )
        ):
            return pending_request_ids
        if len(pending_request_ids) == 1:
            return pending_request_ids
        return []

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
        *,
        agent_registry: list[AgentProfile] | None = None,
        room_config: RoomConfig | None = None,
        room_id: str | None = None,
        user_message_id: str | None = None,
        message_text: str | None = None,
        conversation_context: str | None = None,
        request_user_id: str | None = None,
        quoted_text: str | None = None,
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
            has_awaiting_input = any(
                result.status == StepStatus.AWAITING_INPUT for result in pending
            )
            terminal_results = [
                result
                for result in entry.results
                if result.status not in (StepStatus.PAUSED, StepStatus.AWAITING_INPUT)
            ]
            if terminal_results:
                synced = await self._clear_resolved_agent_hitl_for_terminal_results(
                    synced,
                    terminal_results,
                )
                has_open_pending_hitl = self._has_open_pending_hitl(synced)
                next_status = (
                    OrchestrationStatus.AWAITING_USER
                    if has_awaiting_input and has_open_pending_hitl
                    else (
                        OrchestrationStatus.WAITING_AGENT
                        if pending
                        else OrchestrationStatus.RUNNING
                    )
                )
                should_advance = not pending

                synced = await self._ingest_v2_results(
                    synced,
                    terminal_results,
                    status=next_status,
                    advance_step=should_advance,
                    clear_pending_hitl_request_ids=should_advance,
                )

            if pending:
                synced_output_ids = {output.agent_message_id for output in synced.agent_outputs}
                pending_to_ingest: list[StepResult] = []
                for result in pending:
                    result_message_id = result.agent_message_id or result.paused_message_id
                    if result_message_id and result_message_id in synced_output_ids:
                        continue
                    pending_to_ingest.append(result)
                pending_status = (
                    OrchestrationStatus.AWAITING_USER
                    if has_awaiting_input and self._has_open_pending_hitl(synced)
                    else OrchestrationStatus.WAITING_AGENT
                )
                if pending_to_ingest:
                    synced = await self._ingest_v2_results(
                        synced,
                        pending_to_ingest,
                        status=pending_status,
                        advance_step=False,
                    )
                blocking_run_status = (
                    RunStatus.AWAITING_INPUT
                    if has_awaiting_input
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
                can_rehydrate_hitl = (
                    has_awaiting_input
                    and agent_registry is not None
                    and room_config is not None
                    and room_id is not None
                    and user_message_id is not None
                    and message_text is not None
                    and self.hitl_coordinator is not None
                )
                if can_rehydrate_hitl:
                    synced, awaiting_status = await self._run_agent_awaiting_input_action(
                        state=synced,
                        results=entry.results,
                        awaiting=[
                            result
                            for result in pending
                            if result.status == StepStatus.AWAITING_INPUT
                        ],
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
                    return synced, awaiting_status
                return synced, blocking_run_status

        return synced, None

    async def _clear_resolved_agent_hitl_for_terminal_results(
        self,
        state: OrchestrationRunState,
        terminal_results: list[StepResult],
    ) -> OrchestrationRunState:
        resolved_request_ids = self._resolved_agent_hitl_request_ids_for_results(
            state,
            terminal_results,
        )
        if not resolved_request_ids:
            return state

        resolved_at = utcnow().isoformat()
        response_by_message_id: dict[str, str] = {}
        for result in terminal_results:
            for message_id in (result.agent_message_id, result.paused_message_id):
                if message_id:
                    response_by_message_id[message_id] = result.response_text or ""

        def resolve_hitl(updated: OrchestrationRunState) -> None:
            for question in updated.open_questions:
                if not isinstance(question, Mapping):
                    continue
                request_id = question.get("request_id")
                if request_id not in resolved_request_ids:
                    continue
                question["status"] = "resolved"
                question["resolved"] = True
                question["resolved_at"] = resolved_at
                display_message_id = question.get("display_message_id")
                if isinstance(display_message_id, str):
                    answer = response_by_message_id.get(display_message_id)
                    if answer:
                        question["answer"] = answer
            updated.pending_hitl_request_ids = [
                request_id
                for request_id in updated.pending_hitl_request_ids
                if request_id not in resolved_request_ids
            ]
            self._clear_stale_pending_hitl_request_ids(updated)

        return await self._save_v2_state(
            state,
            event_type=OrchestrationEventType.HITL_RESOLVED,
            payload={
                "request_ids": sorted(resolved_request_ids),
                "answer_recorded": False,
                "source": "agent_terminal_result",
            },
            mutate=resolve_hitl,
        )

    @staticmethod
    def _resolved_agent_hitl_request_ids_for_results(
        state: OrchestrationRunState,
        terminal_results: list[StepResult],
    ) -> set[str]:
        if not state.pending_hitl_request_ids:
            return set()
        terminal_message_ids = {
            message_id
            for result in terminal_results
            for message_id in (result.agent_message_id, result.paused_message_id)
            if message_id
        }
        terminal_agent_ids = {
            result.agent_id for result in terminal_results if result.agent_id
        }
        pending_request_ids = set(state.pending_hitl_request_ids)
        fallback_request_ids_by_agent: dict[str, set[str]] = {}
        for question in state.open_questions:
            if not isinstance(question, Mapping):
                continue
            request_id = question.get("request_id")
            agent_id = question.get("agent_id")
            if (
                not isinstance(request_id, str)
                or request_id not in pending_request_ids
                or not isinstance(agent_id, str)
                or question.get("source") != "agent"
                or question.get("status") != "open"
            ):
                continue
            has_message_id = any(
                isinstance(question.get(key), str) and question.get(key)
                for key in ("display_message_id", "continuation_message_id")
            )
            if not has_message_id:
                fallback_request_ids_by_agent.setdefault(agent_id, set()).add(
                    request_id
                )
        resolved: set[str] = set()
        for question in state.open_questions:
            if not isinstance(question, Mapping):
                continue
            request_id = question.get("request_id")
            if not isinstance(request_id, str) or request_id not in pending_request_ids:
                continue
            if question.get("source") != "agent" or question.get("status") != "open":
                continue
            message_ids = {
                value
                for value in (
                    question.get("display_message_id"),
                    question.get("continuation_message_id"),
                )
                if isinstance(value, str) and value
            }
            if message_ids & terminal_message_ids:
                resolved.add(request_id)
                continue
            agent_id = question.get("agent_id")
            if (
                isinstance(agent_id, str)
                and agent_id in terminal_agent_ids
                and fallback_request_ids_by_agent.get(agent_id) == {request_id}
            ):
                resolved.add(request_id)
        return resolved

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
            output_status = SupervisorExecutor._step_status_from_state_output_status(
                output.status
            )
            if output_status is None:
                continue
            if output_status in (StepStatus.PAUSED, StepStatus.AWAITING_INPUT):
                continue
            error_message = output.error
            if output_status == StepStatus.FAILED and not error_message:
                error_message = f"Agent output status: {output.status}"
            entry.results[index] = StepResult(
                step_number=entry.step_number,
                agent_id=result.agent_id,
                agent_name=result.agent_name,
                task=result.task,
                response_text=output.text or "",
                success=output_status == StepStatus.SUCCESS,
                status=output_status,
                error_message=error_message,
                agent_message_id=message_id,
                completed_at=utcnow(),
                a2a_task_id=output.a2a_task_id,
                a2a_context_id=output.a2a_context_id,
                status_message=output.status_message,
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
        prompt_by_request_id: dict[str, str | None] = {}
        extra_by_request_id: dict[str, dict[str, Any]] = {}
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

        async def cleanup_created_artifacts() -> dict[str, list[str]]:
            failed_request_ids: list[str] = []
            failed_message_ids: list[str] = []
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
                        failed_request_ids.append(request_id)
            for message_id in created_messages:
                delete_message = getattr(
                    self.message_writer,
                    "delete_room_agent_message_by_message_id",
                    None,
                )
                if delete_message is None:
                    continue
                try:
                    deleted = await delete_message(message_id)
                    if deleted is False:
                        failed_message_ids.append(message_id)
                except Exception:
                    logger.warning(
                        "Failed to delete orphaned v2 HITL agent message %s",
                        message_id,
                    )
                    failed_message_ids.append(message_id)
            return {
                "request_ids": failed_request_ids,
                "message_ids": failed_message_ids,
            }

        def mark_failed_supervisor_cleanup(
            updated: OrchestrationRunState,
            cleanup_failures: Mapping[str, list[str]],
        ) -> None:
            self._mark_failed_hitl_cleanup_state(
                updated,
                created_request_ids=[
                    *pending_request_ids,
                    *created_request_ids,
                ],
                failed_cancel_request_ids=list(
                    cleanup_failures.get("request_ids", [])
                ),
                source="supervisor",
                prompt_by_request_id=prompt_by_request_id,
                extra_by_request_id=extra_by_request_id,
                created_message_ids=created_messages,
                failed_delete_message_ids=list(
                    cleanup_failures.get("message_ids", [])
                ),
            )

        def mark_supervisor_request_open(
            updated: OrchestrationRunState,
            *,
            request_id: str,
            question: ClarifyQuestion,
            message_id: str,
        ) -> None:
            # The request can be persisted before its continuation. Keep the run
            # non-answerable until continuation recovery is durable.
            updated.status = OrchestrationStatus.INGESTING
            updated.steps_used = max(updated.steps_used, step_number)
            if request_id not in updated.pending_hitl_request_ids:
                updated.pending_hitl_request_ids.append(request_id)
            existing = next(
                (
                    item
                    for item in updated.open_questions
                    if isinstance(item, Mapping)
                    and (
                        item.get("request_id") == request_id
                        or (
                            not item.get("request_id")
                            and item.get("display_message_id") == message_id
                            and item.get("source") == "supervisor"
                        )
                    )
                ),
                None,
            )
            if existing is None:
                updated.open_questions.append(
                    {
                        "request_id": request_id,
                        "source": "supervisor",
                        "step": step_number,
                        "prompt": question.prompt,
                        "prompt_type": question.prompt_type,
                        "choices": question.choices,
                        "status": "creating",
                        "display_message_id": message_id,
                        "created_at": utcnow().isoformat(),
                    }
                )
            else:
                existing["request_id"] = request_id
                existing["status"] = "creating"
                existing["source"] = "supervisor"
                existing["step"] = step_number
                existing["prompt"] = question.prompt
                existing["prompt_type"] = question.prompt_type
                existing["choices"] = question.choices
                existing["display_message_id"] = message_id
            self._clear_stale_pending_hitl_request_ids(updated)

        def mark_supervisor_request_creating(
            updated: OrchestrationRunState,
            *,
            question: ClarifyQuestion,
            message_id: str,
        ) -> None:
            updated.status = OrchestrationStatus.AWAITING_USER
            updated.steps_used = max(updated.steps_used, step_number)
            existing = next(
                (
                    item
                    for item in updated.open_questions
                    if isinstance(item, Mapping)
                    and item.get("source") == "supervisor"
                    and item.get("display_message_id") == message_id
                ),
                None,
            )
            if existing is None:
                updated.open_questions.append(
                    {
                        "source": "supervisor",
                        "step": step_number,
                        "prompt": question.prompt,
                        "prompt_type": question.prompt_type,
                        "choices": question.choices,
                        "status": "creating",
                        "display_message_id": message_id,
                        "created_at": utcnow().isoformat(),
                    }
                )
            else:
                existing["status"] = "creating"
                existing["step"] = step_number
                existing["prompt"] = question.prompt
                existing["prompt_type"] = question.prompt_type
                existing["choices"] = question.choices
                existing["display_message_id"] = message_id

        for qi, question in enumerate(questions):
            prompt_type = HITLPromptType.TEXT
            if question.prompt_type:
                try:
                    prompt_type = HITLPromptType(question.prompt_type)
                except ValueError:
                    pass

            request = None
            hitl_agent_message = None
            try:
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
                created_messages.append(hitl_agent_message.message_id)
                await self.message_writer.upsert_room_agent_message(
                    hitl_agent_message
                )

                def persist_request_creating(
                    updated: OrchestrationRunState,
                    *,
                    question: ClarifyQuestion = question,
                    message_id: str = hitl_agent_message.message_id,
                ) -> None:
                    mark_supervisor_request_creating(
                        updated,
                        question=question,
                        message_id=message_id,
                    )

                state = await self._save_v2_state(
                    state,
                    event_type=OrchestrationEventType.HITL_REQUESTED,
                    payload={
                        "status": OrchestrationStatus.AWAITING_USER.value,
                        "phase": "request_creating",
                        "display_message_id": hitl_agent_message.message_id,
                    },
                    mutate=persist_request_creating,
                )

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
                    raise RuntimeError("failed to create v2 HITL request")
                created_request_ids.append(request.request_id)
                prompt_by_request_id[request.request_id] = question.prompt
                extra_by_request_id[request.request_id] = {
                    "step": step_number,
                    "prompt_type": question.prompt_type,
                    "choices": question.choices,
                    "display_message_id": hitl_agent_message.message_id,
                }
                def persist_request_open(
                    updated: OrchestrationRunState,
                    *,
                    request_id: str = request.request_id,
                    question: ClarifyQuestion = question,
                    message_id: str = hitl_agent_message.message_id,
                ) -> None:
                    mark_supervisor_request_open(
                        updated,
                        request_id=request_id,
                        question=question,
                        message_id=message_id,
                    )

                state = await self._save_v2_state(
                    state,
                    event_type=OrchestrationEventType.HITL_REQUESTED,
                    payload={
                        "status": OrchestrationStatus.INGESTING.value,
                        "request_ids": [request.request_id],
                        "phase": "request_created",
                    },
                    mutate=persist_request_open,
                )
            except Exception as exc:
                request_id = getattr(exc, "request_id", None)
                if isinstance(request_id, str):
                    created_request_ids.append(request_id)
                    prompt_by_request_id[request_id] = question.prompt
                    extra_by_request_id[request_id] = {
                        "step": step_number,
                        "prompt_type": question.prompt_type,
                        "choices": question.choices,
                        "display_message_id": (
                            hitl_agent_message.message_id
                            if hitl_agent_message is not None
                            else None
                        ),
                    }
                await self._clear_continuation_state(
                    message_id=user_message_id,
                    to_user_message=True,
                )
                cleanup_failures = await cleanup_created_artifacts()
                trajectory.status = TrajectoryStatus.FAILED

                def mark_failed_create(
                    updated: OrchestrationRunState,
                    cleanup_failures: Mapping[str, list[str]] = cleanup_failures,
                ) -> None:
                    mark_failed_supervisor_cleanup(updated, cleanup_failures)

                state = await self._mark_v2_terminal(
                    state,
                    OrchestrationStatus.FAILED,
                    reason="failed to create v2 supervisor HITL request",
                    mutate=mark_failed_create,
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
                hitl_request_id=created_request_ids[-1],
                message_id=user_message_id,
            )
        except Exception:
            await self._clear_continuation_state(
                message_id=user_message_id,
                to_user_message=True,
            )
            cleanup_failures = await cleanup_created_artifacts()
            trajectory.status = TrajectoryStatus.FAILED

            def mark_failed_continuation_save(
                updated: OrchestrationRunState,
            ) -> None:
                mark_failed_supervisor_cleanup(updated, cleanup_failures)

            state = await self._mark_v2_terminal(
                state,
                OrchestrationStatus.FAILED,
                reason="failed to save v2 supervisor HITL continuation",
                mutate=mark_failed_continuation_save,
            )
            return await self._log_state_and_return(
                room_id,
                state,
                self._state_run_result(status=RunStatus.FAILED, state=state),
            )
        if not saved:
            cleanup_failures = await cleanup_created_artifacts()
            trajectory.status = TrajectoryStatus.FAILED

            def mark_failed_unsaved_continuation(
                updated: OrchestrationRunState,
            ) -> None:
                mark_failed_supervisor_cleanup(updated, cleanup_failures)

            state = await self._mark_v2_terminal(
                state,
                OrchestrationStatus.FAILED,
                reason="failed to save v2 HITL continuation",
                mutate=mark_failed_unsaved_continuation,
            )
            return await self._log_state_and_return(
                room_id,
                state,
                self._state_run_result(status=RunStatus.FAILED, state=state),
            )

        def mark_awaiting_user(updated: OrchestrationRunState) -> None:
            updated.status = OrchestrationStatus.AWAITING_USER
            updated.steps_used = max(updated.steps_used, step_number)
            for index, request_id in enumerate(created_request_ids):
                if request_id not in updated.pending_hitl_request_ids:
                    updated.pending_hitl_request_ids.append(request_id)
                question = questions[min(index, len(questions) - 1)]
                existing = next(
                    (
                        item
                        for item in updated.open_questions
                        if isinstance(item, Mapping)
                        and (
                            item.get("request_id") == request_id
                            or (
                                not item.get("request_id")
                                and item.get("source") == "supervisor"
                                and item.get("prompt") == question.prompt
                            )
                        )
                    ),
                    None,
                )
                if existing is None:
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
                else:
                    existing["request_id"] = request_id
                    existing["source"] = "supervisor"
                    existing["prompt"] = question.prompt
                    existing["prompt_type"] = question.prompt_type
                    existing["choices"] = question.choices
                    existing["status"] = "open"
            self._clear_stale_pending_hitl_request_ids(updated)

        try:
            state = await self._save_v2_state(
                state,
                event_type=OrchestrationEventType.HITL_REQUESTED,
                payload={
                    "status": OrchestrationStatus.AWAITING_USER.value,
                    "request_ids": created_request_ids,
                },
                mutate=mark_awaiting_user,
            )
        except Exception:
            await self._clear_continuation_state(
                message_id=user_message_id,
                to_user_message=True,
            )
            cleanup_failures = await cleanup_created_artifacts()
            trajectory.status = TrajectoryStatus.FAILED
            failed_reason = "failed to persist v2 supervisor HITL state"

            def mark_failed(updated: OrchestrationRunState) -> None:
                updated.status = OrchestrationStatus.FAILED
                updated.terminal_reason = failed_reason
                mark_failed_supervisor_cleanup(updated, cleanup_failures)

            try:
                state = await self._save_v2_state(
                    state,
                    event_type=OrchestrationEventType.RUN_TERMINAL,
                    payload={
                        "status": OrchestrationStatus.FAILED.value,
                        "reason": failed_reason,
                    },
                    mutate=mark_failed,
                )
            except Exception:
                fallback_state = state.model_copy(deep=True)
                mark_failed(fallback_state)
                state = fallback_state
                logger.warning(
                    "Failed to persist failed v2 supervisor HITL state",
                    exc_info=True,
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
        try:
            PlannerActionValidator.validate(planner_action, run_state=state)
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
        await self._emit_supervisor_stage(
            room_id=room_id,
            user_message_id=user_message_id,
            client_request_id=client_req_id,
            details="Synthesizing responses...",
            stage="synthesizing",
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

    async def _ingest_agent_results_serially(
        self,
        state: OrchestrationRunState,
        results: list[AgentResultRead],
    ) -> OrchestrationRunState:
        current = state
        for result in results:
            expected_version = current.state_version
            next_state = self.result_ingestor.ingest(current, result)
            current = await self.run_store.save_state(
                next_state,
                expected_version=expected_version,
            )
            await self._append_v2_event(
                current,
                OrchestrationEventType.AGENT_RESULT_INGESTED,
                payload={
                    "agent_message_id": result.agent_message_id,
                    "agent_id": result.agent_id,
                    "status": result.status,
                },
            )
        return current

    @staticmethod
    def _normalize_awaiting_input_results(results: list[StepResult]) -> None:
        awaiting = [result for result in results if result.status == StepStatus.AWAITING_INPUT]
        for extra in awaiting[1:]:
            extra.success = False
            extra.error_message = (
                "Deferred: another agent is awaiting human input first. "
                "Will be re-evaluated on resume."
            )

    @staticmethod
    def _v2_fallback_intent_for_result(
        result: StepResult,
        fallback_intents: list[DispatchIntent],
    ) -> DispatchIntent | None:
        if result.agent_message_id:
            return next(
                (
                    intent
                    for intent in fallback_intents
                    if intent.planned_agent_message_id == result.agent_message_id
                ),
                None,
            )
        return next(
            (
                intent
                for intent in fallback_intents
                if intent.agent_id == result.agent_id
                and intent.task == result.task
                and intent.status == "planned"
            ),
            None,
        )

    @staticmethod
    def _v2_output_message_id_for_result(
        result: StepResult,
        fallback_intents: list[DispatchIntent],
    ) -> str | None:
        if result.agent_message_id:
            return result.agent_message_id

        fallback = SupervisorExecutor._v2_fallback_intent_for_result(
            result,
            fallback_intents,
        )
        return fallback.planned_agent_message_id if fallback else None

    @staticmethod
    def _v2_artifacts_for_result(
        state: OrchestrationRunState,
        output_message_id: str | None,
    ) -> list[dict[str, Any]]:
        if not output_message_id:
            return []
        output_by_message_id = {
            output.agent_message_id: output for output in state.agent_outputs
        }
        output = output_by_message_id.get(output_message_id)
        if output is None or not output.artifact_keys:
            return []
        artifact_keys = set(output.artifact_keys)
        return [
            copy.deepcopy(artifact)
            for artifact in state.artifacts
            if isinstance(artifact, dict) and artifact.get("artifact_key") in artifact_keys
        ]

    async def _v2_artifacts_for_output_message(
        self,
        state: OrchestrationRunState,
        output_message_id: str | None,
    ) -> list[dict[str, Any]]:
        if not output_message_id:
            return []
        persisted_artifacts = await self._v2_persisted_artifacts_for_agent_message(
            output_message_id
        )
        if persisted_artifacts:
            return persisted_artifacts
        return self._v2_artifacts_for_result(state, output_message_id)

    async def _v2_persisted_artifacts_for_agent_message(
        self,
        output_message_id: str,
    ) -> list[dict[str, Any]]:
        get_message = getattr(
            self.message_reader,
            "get_room_agent_message_by_message_id",
            None,
        )
        if get_message is None:
            return []
        message = await get_message(output_message_id)
        if message is None:
            return []
        return self._v2_artifacts_from_agent_message(message)

    @staticmethod
    def _v2_artifacts_from_agent_message(message) -> list[dict[str, Any]]:
        message_content = getattr(message, "message_content", None)
        task = getattr(message_content, "message_task", None)
        if task is None:
            return []
        artifacts = (
            task.get("artifacts")
            if isinstance(task, Mapping)
            else getattr(task, "artifacts", None)
        )
        return artifacts_to_dicts(artifacts if isinstance(artifacts, list) else None)

    @staticmethod
    def _apply_v2_result_metadata(
        state: OrchestrationRunState,
        result: StepResult,
        *,
        status: OrchestrationStatus,
        advance_step: bool,
        matched_intent_id: str | None,
    ) -> None:
        state.status = status
        for intent in state.dispatch_intents:
            if intent.dispatch_intent_id == matched_intent_id:
                intent.status = result.status.value
        if advance_step:
            state.steps_used += 1

    @staticmethod
    def _v2_result_status_to_agent_result_status(
        result: StepResult,
    ) -> str:
        if result.status == StepStatus.SUCCESS:
            return "completed"
        return result.status.value

    async def _ingest_v2_results(
        self,
        state: OrchestrationRunState,
        results: list[StepResult],
        *,
        status: OrchestrationStatus,
        advance_step: bool,
        clear_pending_hitl_request_ids: bool = False,
    ) -> OrchestrationRunState:
        if not results:
            return state

        self._normalize_awaiting_input_results(results)
        # Use the latest in-memory intent state for each loop iteration so no-message-id
        # results consume exactly one matching planned intent in order.

        current = state
        for index, result in enumerate(results):
            expected_version = current.state_version
            next_state = current.model_copy(deep=True)
            matched_intent = self._v2_fallback_intent_for_result(
                result,
                next_state.dispatch_intents,
            )
            self._apply_v2_result_metadata(
                next_state,
                result,
                status=status,
                advance_step=advance_step and index == len(results) - 1,
                matched_intent_id=(
                    matched_intent.dispatch_intent_id if matched_intent else None
                ),
            )
            if clear_pending_hitl_request_ids and index == len(results) - 1:
                next_state.pending_hitl_request_ids.clear()

            output_message_id = (
                result.agent_message_id
                or (
                    matched_intent.planned_agent_message_id
                    if matched_intent is not None
                    else None
                )
                or self._v2_output_message_id_for_result(
                    result,
                    fallback_intents=next_state.dispatch_intents,
                )
            )
            if output_message_id:
                artifacts = await self._v2_artifacts_for_output_message(
                    current,
                    output_message_id,
                )
                next_state = self.result_ingestor.ingest(
                    next_state,
                    AgentResultRead(
                        agent_message_id=output_message_id,
                        agent_id=result.agent_id,
                        status=self._v2_result_status_to_agent_result_status(result),
                        text=result.response_text,
                        error=result.error_message,
                        artifacts=artifacts,
                        a2a_task_id=result.a2a_task_id,
                        a2a_context_id=result.a2a_context_id,
                        status_message=result.status_message,
                    ),
                )

            next_state.state_version = expected_version + 1
            next_state.updated_at = utcnow()

            outcome = None
            if matched_intent is not None and output_message_id:
                output = next(
                    (
                        candidate
                        for candidate in next_state.agent_outputs
                        if candidate.agent_message_id == output_message_id
                    ),
                    None,
                )
                if output is not None:
                    history = OutcomeHistoryView.from_state(current)
                    evaluated = self.delegation_outcome_evaluator.evaluate(
                        current,
                        next_state,
                        matched_intent,
                        output,
                        selected_resource_fingerprints=[],
                    )
                    outcome = evaluated.model_copy(
                        update={
                            "outcome_id": "outcome:"
                            + canonical_content_fingerprint(
                                {
                                    "run_id": current.run_id,
                                    "dispatch_intent_id": (
                                        matched_intent.dispatch_intent_id
                                    ),
                                    "output_message_id": output_message_id,
                                    "result_status": result.status.value,
                                    "result_fingerprint": (
                                        evaluated.result_fingerprint
                                    ),
                                }
                            )[:20]
                        }
                    )
                    if any(
                        existing.outcome_id == outcome.outcome_id
                        for existing in history.outcomes
                    ):
                        outcome = None
                    else:
                        next_state.delegation_outcomes.append(outcome)

            current = await self.run_store.save_state(
                next_state,
                expected_version=expected_version,
            )
            if output_message_id:
                await self._append_v2_event(
                    current,
                    OrchestrationEventType.AGENT_RESULT_INGESTED,
                    payload={
                        "agent_message_id": output_message_id,
                        "agent_id": result.agent_id,
                        "status": self._v2_result_status_to_agent_result_status(
                            result
                        ),
                    },
                )
            if outcome is not None:
                await self._append_v2_event(
                    current,
                    OrchestrationEventType.OUTCOME_EVALUATED,
                    payload={
                        "outcome_id": outcome.outcome_id,
                        "dispatch_intent_id": outcome.dispatch_intent_id,
                        "agent_message_id": output_message_id,
                        "status": outcome.status,
                    },
                )

        return current

    async def _invalidate_v2_required_evidence(
        self,
        state: OrchestrationRunState,
        *,
        evidence_key: str,
        obligation_keys: list[str],
        reason: str,
        source_event_id: str,
    ) -> OrchestrationRunState:
        expected_version = state.state_version
        updated, payload = invalidate_required_evidence(
            state,
            evidence_key=evidence_key,
            obligation_keys=obligation_keys,
            reason=reason,
            source_event_id=source_event_id,
        )
        saved = await self.run_store.save_state(
            updated,
            expected_version=expected_version,
        )
        await self._append_v2_event(
            saved,
            OrchestrationEventType.REQUIRED_EVIDENCE_INVALIDATED,
            payload=payload,
        )
        return saved

    async def _mark_v2_terminal(
        self,
        state: OrchestrationRunState,
        status: OrchestrationStatus,
        *,
        reason: str,
        mutate: Callable[[OrchestrationRunState], None] | None = None,
    ) -> OrchestrationRunState:
        expected_version = state.state_version
        updated = mark_terminal(state, status, reason=reason)
        if mutate is not None:
            mutate(updated)
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
                        a2a_task_id=result.a2a_task_id,
                        a2a_context_id=result.a2a_context_id,
                        status_message=result.status_message,
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
            depends_on=list(target.depends_on),
            parallel_group=target.parallel_group,
            required_resource_refs=list(target.required_resource_refs),
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
                        depends_on=list(target.depends_on),
                        parallel_group=target.parallel_group,
                        required_resource_refs=list(target.required_resource_refs),
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
    def _delegate_target_from_intent(
        intent: DispatchIntent,
        agent_name: str,
    ) -> DelegateTarget:
        return DelegateTarget(
            agent_id=intent.agent_id,
            agent_name=agent_name,
            task=intent.task,
            depends_on=list(intent.depends_on),
            parallel_group=intent.parallel_group,
            required_resource_refs=list(intent.required_resource_refs),
            context_refs=list(intent.context_refs),
            artifact_refs=list(intent.artifact_refs),
            attachment_refs=list(intent.attachment_refs),
            expected_outputs=list(intent.expected_outputs),
            attachment_policy=intent.attachment_policy,
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

    def _has_open_pending_hitl(self, state: OrchestrationRunState) -> bool:
        if not state.pending_hitl_request_ids:
            return False
        pending_request_ids = set(state.pending_hitl_request_ids)
        for question in state.open_questions:
            if not isinstance(question, Mapping):
                continue
            if question.get("status") != "open":
                continue
            request_id = question.get("request_id")
            if isinstance(request_id, str) and request_id in pending_request_ids:
                return True
        return False

    @staticmethod
    def _has_recoverable_supervisor_hitl_question(
        state: OrchestrationRunState,
    ) -> bool:
        return any(
            isinstance(question, Mapping)
            and question.get("source") == "supervisor"
            and question.get("status") in {"open", "creating"}
            and not question.get("resolved")
            for question in state.open_questions
        )

    def _has_current_step_recoverable_intents(self, state: OrchestrationRunState) -> bool:
        step_id = f"{state.run_id}:step-{state.steps_used + 1}"
        terminal_statuses = {
            "completed",
            "failed",
            "canceled",
            "rejected",
            StepStatus.SUCCESS.value,
            StepStatus.FAILED.value,
        }
        return any(
            intent.status not in terminal_statuses and intent.step_id == step_id
            for intent in state.dispatch_intents
        )

    @staticmethod
    def _exhausted_recoverable_failure_for_intents(
        state: OrchestrationRunState,
        intents: list[DispatchIntent],
    ):
        for intent in intents:
            related_failures = [
                failure
                for failure in state.open_failures
                if failure.recoverable
                and failure.status in {"open", "abandoned"}
                if related_open_failure_for_dispatch_intent(
                    [failure],
                    retry_intent=intent,
                    dispatch_intents=state.dispatch_intents,
                    statuses={failure.status},
                )
                is not None
            ]
            blocking_failure = SupervisorExecutor._find_blocking_recoverable_failure(
                related_failures
            )
            if blocking_failure is not None:
                return blocking_failure
        return None

    @staticmethod
    def _find_blocking_recoverable_failure(
        related_failures,
    ):
        retryable_error_codes = {
            failure.error_code
            for failure in related_failures
            if failure.status == "open" and failure.retry_count < failure.max_retries
        }
        if retryable_error_codes:
            return None
        for failure in related_failures:
            if failure.retry_count >= failure.max_retries:
                return failure
        return None

    @staticmethod
    def _remove_hitl_request_refs(
        state: OrchestrationRunState,
        request_ids: set[str],
    ) -> None:
        if not request_ids:
            return
        state.pending_hitl_request_ids = [
            request_id
            for request_id in state.pending_hitl_request_ids
            if request_id not in request_ids
        ]
        state.open_questions = [
            question
            for question in state.open_questions
            if not (
                isinstance(question, Mapping)
                and question.get("request_id") in request_ids
            )
        ]

    @staticmethod
    def _remove_hitl_message_refs(
        state: OrchestrationRunState,
        message_ids: set[str],
    ) -> None:
        if not message_ids:
            return
        state.open_questions = [
            question
            for question in state.open_questions
            if not (
                isinstance(question, Mapping)
                and question.get("display_message_id") in message_ids
            )
        ]

    @staticmethod
    def _record_hitl_cleanup_failed_refs(
        state: OrchestrationRunState,
        *,
        request_ids: list[str],
        pending_request_ids: set[str] | None = None,
        source: str,
        prompt_by_request_id: Mapping[str, str | None] | None = None,
        extra_by_request_id: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        if not request_ids:
            return
        failed_at = utcnow().isoformat()
        existing_by_request_id = {
            question.get("request_id"): question
            for question in state.open_questions
            if isinstance(question, Mapping)
            and isinstance(question.get("request_id"), str)
        }
        pending_request_ids = pending_request_ids or set()
        for request_id in request_ids:
            if (
                request_id in pending_request_ids
                and request_id not in state.pending_hitl_request_ids
            ):
                state.pending_hitl_request_ids.append(request_id)
            question = existing_by_request_id.get(request_id)
            if question is None:
                question = {
                    "request_id": request_id,
                    "source": source,
                    "created_at": failed_at,
                }
                state.open_questions.append(question)
            question["status"] = "cleanup_failed"
            question["cleanup_failed"] = True
            question["cleanup_failed_at"] = failed_at
            # This is an operational recovery breadcrumb. It is intentionally
            # not treated as an answerable question by _has_open_pending_hitl.
            if prompt_by_request_id is not None:
                prompt = prompt_by_request_id.get(request_id)
                if isinstance(prompt, str) and prompt:
                    question["prompt"] = prompt
            if extra_by_request_id is not None:
                extra = extra_by_request_id.get(request_id)
                if isinstance(extra, Mapping):
                    question.update(dict(extra))

    @staticmethod
    def _record_hitl_cleanup_failed_message_refs(
        state: OrchestrationRunState,
        *,
        message_ids: list[str],
        source: str,
    ) -> None:
        if not message_ids:
            return
        failed_at = utcnow().isoformat()
        existing_by_message_id = {
            question.get("display_message_id"): question
            for question in state.open_questions
            if isinstance(question, Mapping)
            and isinstance(question.get("display_message_id"), str)
        }
        for message_id in message_ids:
            question = existing_by_message_id.get(message_id)
            if question is None:
                question = {
                    "source": source,
                    "display_message_id": message_id,
                    "created_at": failed_at,
                }
                state.open_questions.append(question)
            question["status"] = "cleanup_failed"
            question["cleanup_failed"] = True
            question["cleanup_failed_at"] = failed_at
            question["cleanup_failed_message_delete"] = True
        state.facts.append(
            {
                "fact_id": (
                    f"{state.run_id}:hitl-cleanup-failed:"
                    f"{state.state_version + 1}:{len(state.facts) + 1}"
                ),
                "source": "hitl_cleanup_failed",
                "message_ids": list(message_ids),
                "created_at": failed_at,
            }
        )

    @classmethod
    def _mark_failed_hitl_cleanup_state(
        cls,
        state: OrchestrationRunState,
        *,
        created_request_ids: list[str],
        failed_cancel_request_ids: list[str],
        source: str,
        prompt_by_request_id: Mapping[str, str | None] | None = None,
        extra_by_request_id: Mapping[str, Mapping[str, Any]] | None = None,
        created_message_ids: list[str] | None = None,
        failed_delete_message_ids: list[str] | None = None,
    ) -> None:
        failed_cancel_set = set(failed_cancel_request_ids)
        removable_request_ids = set(created_request_ids) - failed_cancel_set
        failed_delete_set = set(failed_delete_message_ids or [])
        removable_message_ids = set(created_message_ids or []) - failed_delete_set
        cls._remove_hitl_request_refs(state, removable_request_ids)
        cls._remove_hitl_message_refs(state, removable_message_ids)
        cleanup_extra_by_request_id = {
            request_id: dict(extra)
            for request_id, extra in (extra_by_request_id or {}).items()
        }
        failed_ref_ids = list(failed_cancel_request_ids)
        for message_id in failed_delete_message_ids or []:
            request_id = next(
                (
                    candidate_request_id
                    for candidate_request_id, extra in cleanup_extra_by_request_id.items()
                    if extra.get("display_message_id") == message_id
                ),
                (
                    message_id.removesuffix(":message")
                    if message_id.endswith(":message")
                    else message_id
                ),
            )
            if request_id not in failed_ref_ids:
                failed_ref_ids.append(request_id)
            cleanup_extra = cleanup_extra_by_request_id.setdefault(request_id, {})
            cleanup_extra.setdefault("display_message_id", message_id)
            failed_message_ids = cleanup_extra.setdefault(
                "cleanup_failed_message_ids", []
            )
            if message_id not in failed_message_ids:
                failed_message_ids.append(message_id)
        cls._record_hitl_cleanup_failed_refs(
            state,
            request_ids=failed_ref_ids,
            pending_request_ids=failed_cancel_set,
            source=source,
            prompt_by_request_id=prompt_by_request_id,
            extra_by_request_id=cleanup_extra_by_request_id,
        )
        cls._record_hitl_cleanup_failed_message_refs(
            state,
            message_ids=list(failed_delete_set),
            source=source,
        )

    def _clear_stale_pending_hitl_request_ids(
        self,
        state: OrchestrationRunState,
    ) -> None:
        if not state.pending_hitl_request_ids:
            return
        retained_request_ids = {
            question.get("request_id")
            for question in state.open_questions
            if isinstance(question, Mapping)
            and question.get("status") in {"open", "creating", "cleanup_failed"}
            and isinstance(question.get("request_id"), str)
        }
        state.pending_hitl_request_ids = [
            request_id
            for request_id in state.pending_hitl_request_ids
            if request_id in retained_request_ids
        ]

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
        if not hasattr(self, "orchestration_resource_provider"):
            self.orchestration_resource_provider = OrchestrationResourceProvider()
        state = await self._load_or_create_run_state_for_run(
            room_id=room_id,
            user_message_id=user_message_id,
            message_text=message_text,
            agent_registry=agent_registry,
            room_config=room_config,
            user_message=user_message,
        )
        resolved_hitl_reply = False
        if resumed_trajectory is not None:
            resolved_hitl_reply = bool(
                self._hitl_answer_from_run_request(
                    user_message=user_message,
                    resumed_trajectory=resumed_trajectory,
                )
            )
            state = await self._resolve_v2_hitl_if_answered(
                state,
                user_message=user_message,
                resumed_trajectory=resumed_trajectory,
            )
            state, blocking_resume_status = await self._sync_v2_resumed_trajectory(
                state,
                resumed_trajectory,
                agent_registry=agent_registry,
                room_config=room_config,
                room_id=room_id,
                user_message_id=user_message_id,
                message_text=message_text,
                conversation_context=conversation_context,
                request_user_id=request_user_id,
                quoted_text=quoted_text,
            )
            should_return_waiting_input = (
                state.status == OrchestrationStatus.AWAITING_USER
                and self._has_open_pending_hitl(state)
            )
            if blocking_resume_status is not None and (
                blocking_resume_status != RunStatus.AWAITING_INPUT
                or should_return_waiting_input
            ):
                return await self._log_state_and_return(
                    room_id,
                    state,
                    self._state_run_result(
                        status=blocking_resume_status,
                        state=state,
                    ),
                )

        try:
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
                allow_awaiting_user_recovery=resolved_hitl_reply,
                user_message=user_message,
            )
        except CancellationError:
            raise
        except Exception:
            await self._mark_current_run_failed_after_unhandled_exception(
                state.run_id,
                reason="supervisor execution failed unexpectedly",
            )
            raise

    async def _mark_current_run_failed_after_unhandled_exception(
        self,
        run_id: str,
        *,
        reason: str,
    ) -> None:
        try:
            current = await self.run_store.get_run(run_id)
            if current is None or current.status in TERMINAL_ORCHESTRATION_STATUSES:
                return
            await self._mark_v2_terminal(
                current,
                OrchestrationStatus.FAILED,
                reason=reason,
            )
        except Exception:
            logger.warning(
                "Failed to terminalize orchestration run after supervisor error",
                extra={"run_id": run_id},
                exc_info=True,
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

    @staticmethod
    def _raw_dispatch_payload_refs(target: DelegateTarget) -> dict[str, list[dict]]:
        return {
            "context_refs": [
                ref.model_dump(mode="json")
                for ref in getattr(target, "context_refs", [])
            ],
            "artifact_refs": [
                ref.model_dump(mode="json")
                for ref in getattr(target, "artifact_refs", [])
            ],
            "attachment_refs": [
                ref.model_dump(mode="json")
                for ref in getattr(target, "attachment_refs", [])
            ],
            "expected_outputs": [
                output.model_dump(mode="json")
                for output in getattr(target, "expected_outputs", [])
            ],
        }

    @staticmethod
    def _resolved_dispatch_payload_refs(
        payload: ResolvedDispatchPayload | None,
    ) -> dict[str, list]:
        if payload is None:
            return {
                "context_refs": [],
                "artifact_refs": [],
                "attachment_refs": [],
                "resource_payloads": [],
            }
        return {
            "context_refs": list(payload.selected_context_refs),
            "artifact_refs": list(payload.selected_artifact_refs),
            "attachment_refs": list(payload.selected_attachment_refs),
            "resource_payloads": [
                resource.model_dump(mode="json")
                for resource in payload.resource_payloads
            ],
        }

    @staticmethod
    def _dispatch_payload_failure_result(
        *,
        target: DelegateTarget,
        step_number: int,
        planned_message_id: str | None,
        error_message: str,
        status_message: str,
    ) -> StepResult:
        return StepResult(
            step_number=step_number,
            agent_id=target.agent_id,
            agent_name=target.agent_name,
            task=target.task,
            response_text="",
            success=False,
            status=StepStatus.FAILED,
            error_message=error_message,
            agent_message_id=planned_message_id,
            status_message=status_message,
        )

    @staticmethod
    def _dispatch_task_with_ref_projection(
        *,
        task: str,
        target: DelegateTarget,
        run_state: OrchestrationRunState | None,
        resolved_payload: ResolvedDispatchPayload | None,
    ) -> str:
        if run_state is None or resolved_payload is None:
            return task

        lines: list[str] = []
        context_lines = SupervisorExecutor._context_ref_projection_lines(
            target,
            resolved_payload,
            run_state,
        )
        artifact_lines = SupervisorExecutor._artifact_ref_projection_lines(
            resolved_payload,
            run_state,
        )
        if context_lines:
            lines.append("Selected context refs:")
            lines.extend(context_lines)
        if artifact_lines:
            lines.append("Selected artifact refs:")
            lines.extend(artifact_lines)
        if not lines:
            return task

        projection = "\n".join(lines)
        if len(projection) > DISPATCH_REF_PROJECTION_MAX_CHARS:
            projection = (
                projection[: DISPATCH_REF_PROJECTION_MAX_CHARS - 3].rstrip()
                + "..."
            )
        return f"{task.rstrip()}\n\n[Backend-selected references]\n{projection}"

    @staticmethod
    def _context_ref_projection_lines(
        target: DelegateTarget,
        payload: ResolvedDispatchPayload,
        run_state: OrchestrationRunState,
    ) -> list[str]:
        fact_by_id = {
            str(fact.get("fact_id")): fact
            for fact in run_state.facts
            if isinstance(fact, dict) and fact.get("fact_id") is not None
        }
        selected = set(payload.selected_context_refs)
        lines: list[str] = []
        for ref in getattr(target, "context_refs", []):
            if ref.ref_id not in selected:
                continue
            fact = fact_by_id.get(ref.ref_id)
            parts = [f"ref={ref.ref_id}"]
            if ref.source_agent_message_id:
                parts.append(f"source={ref.source_agent_message_id}")
            if ref.mime_type:
                parts.append(f"mime={ref.mime_type}")
            if fact is not None:
                summary = fact.get("summary") or fact.get("text")
                if summary is not None:
                    parts.append(
                        "summary="
                        + SupervisorExecutor._bounded_projection_value(summary, 240)
                    )
            lines.append("- " + "; ".join(parts))
        return lines

    @staticmethod
    def _artifact_ref_projection_lines(
        payload: ResolvedDispatchPayload,
        run_state: OrchestrationRunState,
    ) -> list[str]:
        artifact_by_key = {
            str(artifact.get("artifact_key")): artifact
            for artifact in run_state.artifacts
            if isinstance(artifact, dict) and artifact.get("artifact_key") is not None
        }
        lines: list[str] = []
        for artifact_key in payload.selected_artifact_refs:
            artifact = artifact_by_key.get(artifact_key)
            if artifact is None:
                continue
            fields = [
                ("key", artifact.get("artifact_key")),
                ("name", artifact.get("name") or artifact.get("title")),
                (
                    "mime",
                    artifact.get("mime_type") or artifact.get("mimeType"),
                ),
                ("summary", artifact.get("summary")),
                (
                    "source",
                    artifact.get("source_agent_message_id")
                    or artifact.get("source_agent_id"),
                ),
            ]
            parts = [
                f"{name}={SupervisorExecutor._bounded_projection_value(value, 240)}"
                for name, value in fields
                if value is not None and str(value).strip()
            ]
            if parts:
                lines.append("- " + "; ".join(parts))
        return lines

    @staticmethod
    def _bounded_projection_value(value: Any, max_chars: int) -> str:
        text = " ".join(str(value).split())
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3].rstrip() + "..."

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
                            required_resource_refs=target.required_resource_refs,
                            resource_provider=self.orchestration_resource_provider,
                        )
                    except DispatchPayloadValidationError as exc:
                        return self._dispatch_payload_failure_result(
                            target=target,
                            step_number=step_number,
                            planned_message_id=planned_message_id,
                            error_message=str(exc),
                            status_message=exc.code,
                        )

                dispatch_task = self._dispatch_task_with_ref_projection(
                    task=target.task,
                    target=target,
                    run_state=run_state,
                    resolved_payload=resolved_payload,
                )

                # Create RoomAgentMessage only after validation passes
                message = self.room_runtime.create_agent_message(
                    room_id=room_id,
                    related_message_id=user_message_id,
                    agent_id=target.agent_id,
                    content=dispatch_task,
                    user_id=request_user_id,
                    step_number=step_number,
                    total_steps=None,
                    task_content=dispatch_task,
                    client_request_id=await self.task_state_store.resolve_client_request_id_for_message_id(
                        user_message_id
                    ),
                )
                if not isinstance(message.extend_info, dict):
                    message.extend_info = {}
                message.extend_info["attachment_forwarding_policy"] = (
                    target.attachment_policy
                    if getattr(target, "attachment_policy", None)
                    else "explicit_refs_only"
                )
                message.extend_info["dispatch_payload_refs"] = (
                    self._raw_dispatch_payload_refs(target)
                )
                message.extend_info["resolved_dispatch_payload_refs"] = (
                    self._resolved_dispatch_payload_refs(resolved_payload)
                )
                if planned_message_id:
                    message.message_id = planned_message_id
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

    async def _checkpoint_run_reference(
        self,
        user_message,
        state: OrchestrationRunState,
    ) -> None:
        if user_message is None:
            return
        if not isinstance(user_message.extend_info, dict):
            user_message.extend_info = {}
        user_message.extend_info["orchestration_run_id"] = state.run_id
        user_message.extend_info["orchestration_status"] = state.status.value
        user_message.extend_info.pop("supervisor_trajectory", None)
        if state.client_request_id:
            user_message.extend_info["client_request_id"] = state.client_request_id
        if state.candidate_scope is not None:
            user_message.extend_info["candidate_scope_snapshot_id"] = (
                state.candidate_scope.snapshot_id
            )
            user_message.extend_info["candidate_scope_source"] = (
                state.candidate_scope.source
            )

        message_id = getattr(user_message, "message_id", None) or state.user_message_id
        if not isinstance(message_id, str):
            return
        await self.message_writer.update_room_user_message_by_message_id(
            message_id,
            user_message,
        )


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
