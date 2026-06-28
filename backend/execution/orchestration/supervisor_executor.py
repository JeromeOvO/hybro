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
from typing import TYPE_CHECKING
from uuid import uuid4

from common.a2a_constants import SSEProcessingStatus
from common.config import settings as _settings
from common.utils.cancellation import CancellationError, CancellationToken
from common.utils.logger import get_logger
from common.utils.time import utcnow
from execution.orchestration.debate_dispatcher import SequentialDebateDispatcher
from execution.state.task_status_mapping import system_task_state_from_runtime_status
from models.hitl import InterruptKind
from models.processing import ProcessingStatus
from models.room import CoordinatorAgentId
from models.supervisor import (
    ActionType,
    AgentProfile,
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
    from execution.dispatch.agent_dispatcher import AgentDispatcher
    from execution.dispatch.agent_message_processor import AgentMessageProcessor
    from execution.orchestration.room_supervisor_service import RoomSupervisorService
    from execution.ports import (
        ExecutionDeliveryPort,
        HITLCoordinator,
        RateLimitPort,
        RoomContinuationStore,
        RoomMemoryPort,
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
        room_memory: RoomMemoryPort,
        rate_limit_service: RateLimitPort,
        agent_dispatcher: AgentDispatcher,
        agent_message_processor: AgentMessageProcessor,
        slot_lifecycle=None,
        hitl_coordinator: HITLCoordinator | None = None,
        debate_rounds: int = 2,
    ) -> None:
        self.supervisor_service = supervisor_service
        self.room_runtime = room_runtime
        self.tsm = tsm
        self.delivery = delivery
        self.message_reader = message_reader
        self.message_writer = message_writer
        self.task_state_store = task_state_store
        self.continuation_store = continuation_store
        self.room_memory = room_memory
        self.rate_limit_service = rate_limit_service
        self.agent_dispatcher = agent_dispatcher
        self.agent_message_processor = agent_message_processor
        self._slot_lifecycle = slot_lifecycle
        self.hitl_coordinator = hitl_coordinator
        self.debate_rounds = debate_rounds
        self._processing_status_emitter = None

    def bind_execution_event_deps(self, processing_status_emitter) -> None:
        self._processing_status_emitter = processing_status_emitter

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
        trajectory = resumed_trajectory or SupervisorTrajectory()
        step_number = len(trajectory.entries)
        _checkpoint_msg = user_message

        # Phase 1: Emit system:hybro task on start if not already emitted
        if not trajectory.system_agent_message_id:
            client_req_id = (
                getattr(user_message, "client_request_id", None)
                if user_message
                else None
            )
            sys_message_id = f"sys-{user_message_id}"
            trajectory.system_agent_message_id = sys_message_id

            try:
                sys_msg = self.room_runtime.create_agent_message(
                    room_id=room_id,
                    related_message_id=user_message_id,
                    agent_id=CoordinatorAgentId.SYSTEM_HYBRO,
                    content="",
                    user_id=request_user_id,
                    step_number=0,
                    task_content="Orchestrating workflow...",
                    client_request_id=client_req_id,
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
                    client_request_id=client_req_id,
                )
            except Exception:
                logger.warning("Failed to emit system:hybro task", exc_info=True)

        logger.info(
            "supervisor_run_started",
            extra={
                "room_id": room_id,
                "trajectory_id": trajectory.trajectory_id,
                "resumed": resumed_trajectory is not None,
                "step_offset": step_number,
            },
        )

        # Debate mode resume: if all paused results have been filled in,
        # the debate is complete — skip straight to DONE without calling
        # decide_next (which wouldn't know about debate mode at step > 0).
        # Also block if any result is non-success (e.g. deferred agents
        # marked FAILED during multi-agent HITL) — those need re-evaluation.
        if (
            resumed_trajectory is not None
            and room_config.is_debate_mode
            and step_number > 0
        ):
            still_unresolved = any(
                r.status in (StepStatus.PAUSED, StepStatus.AWAITING_INPUT)
                or not r.success
                for entry in trajectory.entries
                for r in entry.results
            )
            if not still_unresolved:
                # Check if there are remaining debate agents to dispatch
                remaining_ids = self._get_remaining_debate_agent_ids(
                    trajectory.debate_agent_ids or [],
                    trajectory,
                )
                if not remaining_ids:
                    # All agents done — complete
                    done_entry = TrajectoryEntry(
                        step_number=len(trajectory.entries) + 1,
                        action=SupervisorAction(
                            action=ActionType.DONE,
                            reasoning="Debate mode complete (resumed after push notification)",
                        ),
                        started_at=utcnow(),
                        completed_at=utcnow(),
                    )
                    trajectory.entries.append(done_entry)
                    trajectory.status = TrajectoryStatus.COMPLETED
                    return await self._log_and_return(
                        room_id,
                        trajectory,
                        SupervisorRunResult(
                            status=RunStatus.COMPLETED, trajectory=trajectory
                        ),
                        debate_mode=True,
                    )
                # Still have agents to dispatch — fall through to main loop
                logger.info(
                    "supervisor_debate_resume_continuing",
                    extra={
                        "room_id": room_id,
                        "remaining_agents": len(remaining_ids),
                    },
                )

        clarify_fallback_count = 0

        # Debate mode: expand step budget to accommodate all agents + 1 (for DONE)
        effective_max_steps = self.MAX_STEPS
        if room_config.is_debate_mode:
            debate_agent_ids = self._snapshot_debate_agents(
                agent_registry,
                trajectory,
                debate_rounds=getattr(self, "debate_rounds", 2),
            )
            effective_max_steps = max(self.MAX_STEPS, len(debate_agent_ids) + 1)

        while step_number < effective_max_steps:
            # --- Cancellation check ---
            if token and token.is_cancelled:
                trajectory.status = TrajectoryStatus.CANCELED
                return await self._log_and_return(
                    room_id,
                    trajectory,
                    SupervisorRunResult(
                        status=RunStatus.CANCELED, trajectory=trajectory
                    ),
                )

            logger.info(
                "supervisor_loop_iteration",
                extra={
                    "room_id": room_id,
                    "trajectory_id": trajectory.trajectory_id,
                    "step_number": step_number,
                    "total_supervisor_calls": trajectory.total_supervisor_calls,
                },
            )

            # --- Crash recovery: resume an in-flight DELEGATE step ---
            # If the last entry has action=DELEGATE and empty results, the
            # previous server instance crashed mid-dispatch.  Re-use its
            # action instead of calling decide_next (which would produce a
            # duplicate dispatch).
            inflight_entry: TrajectoryEntry | None = None
            if (
                trajectory.entries
                and trajectory.entries[-1].action.action == ActionType.DELEGATE
                and not trajectory.entries[-1].results
            ):
                inflight_entry = trajectory.entries.pop()
                step_number = len(trajectory.entries)
                logger.info(
                    "supervisor_inflight_recovery",
                    extra={
                        "room_id": room_id,
                        "trajectory_id": trajectory.trajectory_id,
                        "recovered_step": inflight_entry.step_number,
                        "target_count": len(inflight_entry.action.targets),
                    },
                )

            # SSE: notify frontend of planning stage.
            # Skip if already cancelled — avoids duplicate PROCESSING after cancel.
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

            # --- Debate mode fast-path (§8.13) ---
            if inflight_entry is not None:
                action = inflight_entry.action
            elif room_config.is_debate_mode:
                # Sequential debate: dispatch one agent per step
                # (debate_agent_ids already computed before the loop; snapshot is idempotent)
                remaining_ids = self._get_remaining_debate_agent_ids(
                    debate_agent_ids, trajectory
                )

                if not remaining_ids:
                    # All agents dispatched — done
                    done_entry = TrajectoryEntry(
                        step_number=step_number + 1,
                        action=SupervisorAction(
                            action=ActionType.DONE,
                            reasoning="Debate mode complete: all agents have responded",
                        ),
                        started_at=utcnow(),
                        completed_at=utcnow(),
                    )
                    trajectory.entries.append(done_entry)
                    trajectory.status = TrajectoryStatus.COMPLETED
                    return await self._log_and_return(
                        room_id,
                        trajectory,
                        SupervisorRunResult(
                            status=RunStatus.COMPLETED, trajectory=trajectory
                        ),
                        debate_mode=True,
                    )

                next_id = remaining_ids[0]
                # Find agent profile
                next_profile = next(
                    (a for a in agent_registry if a.agent_id == next_id), None
                )

                if next_profile is None or not next_profile.is_healthy:
                    # Skip unhealthy agent: create FAILED entry, checkpoint, continue
                    skip_entry = TrajectoryEntry(
                        step_number=step_number + 1,
                        action=SupervisorAction(
                            action=ActionType.DELEGATE,
                            reasoning=f"Debate: skipping unhealthy agent {next_id}",
                            targets=[
                                DelegateTarget(
                                    agent_id=next_id, agent_name=next_id, task=""
                                )
                            ],
                        ),
                        started_at=utcnow(),
                        completed_at=utcnow(),
                        results=[
                            StepResult(
                                step_number=step_number + 1,
                                agent_id=next_id,
                                agent_name=next_id,
                                task="",
                                response_text="",
                                success=False,
                                status=StepStatus.FAILED,
                                error_message="Agent unhealthy at dispatch time",
                            )
                        ],
                    )
                    trajectory.entries.append(skip_entry)
                    _checkpoint_msg = await self._checkpoint_trajectory(
                        user_message_id,
                        trajectory,
                        cached_user_message=_checkpoint_msg,
                    )
                    step_number += 1
                    continue

                # Build debate task with prior responses
                prior_responses = self._collect_prior_debate_responses(trajectory)
                task_text = self._build_debate_task(message_text, prior_responses)

                action = SupervisorAction(
                    action=ActionType.DELEGATE,
                    reasoning=f"Debate mode: dispatching agent {next_profile.agent_name} ({len(prior_responses)} prior responses)",
                    targets=[
                        DelegateTarget(
                            agent_id=next_profile.agent_id,
                            agent_name=next_profile.agent_name,
                            task=task_text,
                        )
                    ],
                )
            else:
                # --- Guard: still-PAUSED concurrent agents from a prior DELEGATE ---
                # When multiple hub relay agents are dispatched concurrently and
                # one returns before the others, the trajectory has a mix of
                # SUCCESS and PAUSED results for the same step.  The resume
                # continuation is saved on every paused agent, so each return
                # triggers a separate resume call.  Without this guard the LLM
                # would be called with a partially-complete trajectory and might
                # choose SYNTHESIZE / DONE before the remaining agents finish.
                #
                # Fix: if any result across all entries is still PAUSED, update
                # the continuation for each remaining PAUSED agent (so it
                # carries the now-partially-resolved trajectory) and pause again.
                all_still_paused: list[StepResult] = [
                    r
                    for entry in trajectory.entries
                    for r in entry.results
                    if r.status == StepStatus.PAUSED and r.paused_message_id
                ]
                if all_still_paused:
                    # Reconcile against DB: a relay agent may have completed
                    # before its continuation was saved (race between webhook
                    # response and _save_interrupted_state during concurrent
                    # dispatch). Check actual DB state and upgrade if terminal.
                    await self._reconcile_paused_results(
                        all_still_paused, trajectory, room_id
                    )
                    # Re-check after reconciliation
                    all_still_paused = [
                        r
                        for entry in trajectory.entries
                        for r in entry.results
                        if r.status == StepStatus.PAUSED and r.paused_message_id
                    ]

                if all_still_paused:
                    logger.info(
                        "supervisor_resume_still_paused",
                        extra={
                            "room_id": room_id,
                            "trajectory_id": trajectory.trajectory_id,
                            "still_paused_count": len(all_still_paused),
                            "still_paused_agents": [
                                r.agent_name for r in all_still_paused
                            ],
                        },
                    )
                    trajectory.status = TrajectoryStatus.RUNNING
                    saved = await self._save_interrupted_state(
                        kind=InterruptKind.PUSH_NOTIFICATION,
                        trajectory=trajectory,
                        paused_results=all_still_paused,
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
                        return await self._log_and_return(
                            room_id,
                            trajectory,
                            SupervisorRunResult(
                                status=RunStatus.FAILED, trajectory=trajectory
                            ),
                        )
                    return await self._log_and_return(
                        room_id,
                        trajectory,
                        SupervisorRunResult(
                            status=RunStatus.PAUSED, trajectory=trajectory
                        ),
                    )

                # --- Ask supervisor for next action ---
                decide_coro = self.supervisor_service.decide_next(
                    message_text=message_text,
                    agent_registry=agent_registry,
                    room_config=room_config,
                    trajectory=trajectory,
                    conversation_context=conversation_context,
                    quoted_text=quoted_text,
                    max_steps=self.MAX_STEPS,
                )
                try:
                    action = (
                        await token.race(decide_coro) if token else await decide_coro
                    )
                except CancellationError:
                    trajectory.status = TrajectoryStatus.CANCELED
                    return await self._log_and_return(
                        room_id,
                        trajectory,
                        SupervisorRunResult(
                            status=RunStatus.CANCELED, trajectory=trajectory
                        ),
                    )
                trajectory.total_supervisor_calls += 1

            # Clear one-shot HITL reply after it has been consumed by decide_next
            # so it doesn't re-appear in subsequent loop iterations.
            if trajectory.hitl_user_reply:
                trajectory.hitl_user_reply = None
            if trajectory.clarify_user_reply:
                trajectory.clarify_user_reply = None

            logger.info(
                "supervisor_action_decided",
                extra={
                    "room_id": room_id,
                    "trajectory_id": trajectory.trajectory_id,
                    "step_number": step_number,
                    "action_type": action.action,
                    "reasoning": action.reasoning[:100] if action.reasoning else "",
                    "target_count": len(action.targets),
                    "target_agents": [t.agent_name for t in action.targets],
                },
            )

            # --- Guard: DONE/SYNTHESIZE before any delegation ---
            # The supervisor should not skip all delegations.  If it chose
            # DONE or SYNTHESIZE before any agent has been dispatched, override
            # to DELEGATE so the user sees at least one agent response.
            if action.action in (ActionType.DONE, ActionType.SYNTHESIZE):
                has_any_delegation = any(
                    e.action.action == ActionType.DELEGATE for e in trajectory.entries
                )
                if not has_any_delegation:
                    healthy_agents = [a for a in agent_registry if a.is_healthy]
                    if healthy_agents:
                        logger.warning(
                            "supervisor_premature_%s_override",
                            action.action.value,
                            extra={
                                "room_id": room_id,
                                "trajectory_id": trajectory.trajectory_id,
                                "step_number": step_number,
                                "original_reasoning": (
                                    action.reasoning[:120] if action.reasoning else ""
                                ),
                            },
                        )
                        action = SupervisorAction(
                            action=ActionType.DELEGATE,
                            reasoning=(
                                f"Auto-override: supervisor chose "
                                f"{action.action.value} before any agent "
                                f"responded — delegating to available agents"
                            ),
                            targets=[
                                DelegateTarget(
                                    agent_id=a.agent_id,
                                    agent_name=a.agent_name,
                                    task=message_text,
                                )
                                for a in healthy_agents
                            ],
                        )

            # --- Guard: DELEGATE with empty targets is a no-op ---
            if action.action == ActionType.DELEGATE and not action.targets:
                logger.warning(
                    "supervisor_delegate_empty_targets",
                    extra={
                        "room_id": room_id,
                        "trajectory_id": trajectory.trajectory_id,
                        "step_number": step_number,
                    },
                )
                action = SupervisorAction(
                    action=ActionType.DONE,
                    reasoning="DELEGATE had no targets — treating as DONE",
                )

            # --- Guard: deduplicate identical targets (same agent + same task) ---
            if action.action == ActionType.DELEGATE and len(action.targets) > 1:
                seen: set[tuple[str, str]] = set()
                deduped: list[DelegateTarget] = []
                for t in action.targets:
                    key = (t.agent_id, t.task)
                    if key not in seen:
                        seen.add(key)
                        deduped.append(t)
                if len(deduped) < len(action.targets):
                    logger.warning(
                        "supervisor_duplicate_targets_removed",
                        extra={
                            "room_id": room_id,
                            "trajectory_id": trajectory.trajectory_id,
                            "original_targets": [t.agent_name for t in action.targets],
                            "deduped_targets": [t.agent_name for t in deduped],
                        },
                    )
                    action = SupervisorAction(
                        action=action.action,
                        reasoning=action.reasoning,
                        targets=deduped,
                        synthesis_instruction=action.synthesis_instruction,
                        clarification_question=action.clarification_question,
                    )

            # --- Guard: CLARIFY cap — only one round per user message ---
            if action.action == ActionType.CLARIFY:
                prior_clarifies = sum(
                    1
                    for e in trajectory.entries
                    if e.action.action == ActionType.CLARIFY
                )
                if prior_clarifies >= 1:
                    clarify_fallback_count += 1
                    logger.warning(
                        "supervisor_clarify_cap_reached",
                        extra={
                            "room_id": room_id,
                            "trajectory_id": trajectory.trajectory_id,
                            "prior_clarifies": prior_clarifies,
                            "clarify_fallback_count": clarify_fallback_count,
                        },
                    )
                    if clarify_fallback_count > 1:
                        action = SupervisorAction(
                            action=ActionType.DONE,
                            reasoning=(
                                "Supervisor repeatedly requested clarification "
                                "after cap was reached. Ending to avoid "
                                "infinite delegation loop."
                            ),
                        )
                    else:
                        healthy_ids = {
                            a.agent_id for a in agent_registry if a.is_healthy
                        }
                        target_agent = self._pick_best_fallback_agent(
                            trajectory,
                            agent_registry,
                            healthy_ids,
                        )
                        if target_agent:
                            action = SupervisorAction(
                                action=ActionType.DELEGATE,
                                reasoning=(
                                    "Clarification cap reached. Re-delegating "
                                    f"to {target_agent.agent_name} based on "
                                    "prior trajectory success."
                                ),
                                targets=[
                                    DelegateTarget(
                                        agent_id=target_agent.agent_id,
                                        agent_name=target_agent.agent_name,
                                        task=message_text,
                                    )
                                ],
                            )
                        else:
                            action = SupervisorAction(
                                action=ActionType.DONE,
                                reasoning=(
                                    "Clarification cap reached and no suitable "
                                    "healthy agents available."
                                ),
                            )

            # --- Execute the action ---
            match action.action:
                case ActionType.DELEGATE:
                    entry = TrajectoryEntry(
                        step_number=step_number + 1,
                        action=action,
                        started_at=utcnow(),
                    )

                    # Pre-dispatch checkpoint: persist the entry with empty
                    # results so crash recovery can detect an in-flight step
                    # and re-dispatch using the same action instead of calling
                    # decide_next (which would create duplicate dispatches).
                    trajectory.entries.append(entry)
                    _checkpoint_msg = await self._checkpoint_trajectory(
                        user_message_id,
                        trajectory,
                        cached_user_message=_checkpoint_msg,
                    )

                    # SSE: notify frontend of delegation stage
                    if not (token and token.is_cancelled):
                        try:
                            await self._emit_processing_status(
                                room_id=room_id,
                                status=SSEProcessingStatus.PROCESSING,
                                message_id=user_message_id,
                                lifecycle_message_id=user_message_id,
                                details=f"Delegating to {len(action.targets)} agent(s)...",
                                agents=[
                                    {"agent_id": t.agent_id, "agent_name": t.agent_name}
                                    for t in action.targets
                                ],
                            )
                        except Exception:
                            logger.debug(
                                "SSE stage notification failed (delegating)",
                                exc_info=True,
                            )

                    results = await self._dispatch_targets(
                        targets=action.targets,
                        agent_registry=agent_registry,
                        room_id=room_id,
                        user_message_id=user_message_id,
                        step_number=step_number + 1,
                        token=token,
                        request_user_id=request_user_id,
                        quoted_text=quoted_text,
                    )

                    # Write completed results to room memory regardless of
                    # whether some targets are PAUSED — this ensures subsequent
                    # agents (after resume) have cross-agent context.
                    for result in results:
                        if (
                            result.status == StepStatus.SUCCESS
                            and result.success
                            and result.response_text
                        ):
                            await self.room_memory.add_agent_response_to_memory(
                                room_id=room_id,
                                agent_id=result.agent_id,
                                agent_name=result.agent_name,
                                response_text=result.response_text,
                                was_successful=result.success,
                                message_id=getattr(result, "agent_message_id", None),
                            )

                    # Attach results to entry now so reconciliation mutates them correctly
                    entry.results = results

                    # Check for PAUSED (push notification agent)
                    paused = [r for r in entry.results if r.status == StepStatus.PAUSED]
                    if paused:
                        # Reconcile against DB: a relay agent may have completed
                        # before its continuation was saved (race between webhook
                        # response and _save_interrupted_state during concurrent
                        # dispatch). Check actual DB state and upgrade if terminal.
                        await self._reconcile_paused_results(
                            paused, trajectory, room_id
                        )
                        # Re-evaluate paused after reconciliation
                        paused = [r for r in entry.results if r.status == StepStatus.PAUSED]
                        
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
                            return await self._log_and_return(
                                room_id,
                                trajectory,
                                SupervisorRunResult(
                                    status=RunStatus.FAILED, trajectory=trajectory
                                ),
                            )
                        return await self._log_and_return(
                            room_id,
                            trajectory,
                            SupervisorRunResult(
                                status=RunStatus.PAUSED, trajectory=trajectory
                            ),
                        )

                    # Check for AWAITING_INPUT (agent returned input_required)
                    awaiting = [
                        r for r in entry.results if r.status == StepStatus.AWAITING_INPUT
                    ]
                    if awaiting:
                        # Mark non-first awaiting agents as FAILED so the
                        # supervisor gets clean state on resume and can
                        # re-dispatch them (they may or may not request
                        # input again). Only the first agent gets an HITL
                        # request to avoid trajectory race conditions.
                        for extra in awaiting[1:]:
                            extra.status = StepStatus.FAILED
                            extra.success = False
                            extra.error_message = (
                                "Deferred: another agent is awaiting human input first. "
                                "Will be re-evaluated on resume."
                            )

                        entry.results = results
                        trajectory.status = TrajectoryStatus.AWAITING_INPUT

                        # Only create HITL for the FIRST awaiting agent
                        ar = awaiting[0]
                        if self.hitl_coordinator is None:
                            raise RuntimeError("HITL coordinator has not been bound")
                        request = await self.hitl_coordinator.request_input(
                            room_id=room_id,
                            user_message_id=user_message_id,
                            source="agent",
                            prompt=(
                                ar.status_message
                                or "The agent needs additional information."
                            ),
                            agent_id=ar.agent_id,
                            agent_name=ar.agent_name,
                            a2a_task_id=ar.a2a_task_id,
                            a2a_context_id=ar.a2a_context_id,
                            continuation_message_id=ar.paused_message_id,
                        )

                        if request is None:
                            logger.warning(
                                "Max HITL rounds exceeded for message %s — failing trajectory",
                                user_message_id,
                            )
                            trajectory.status = TrajectoryStatus.FAILED
                            return await self._log_and_return(
                                room_id,
                                trajectory,
                                SupervisorRunResult(
                                    status=RunStatus.FAILED, trajectory=trajectory
                                ),
                            )

                        saved = await self._save_interrupted_state(
                            kind=InterruptKind.HITL_AGENT,
                            trajectory=trajectory,
                            message_id=ar.paused_message_id,
                            room_id=room_id,
                            user_message_id=user_message_id,
                            message_text=message_text,
                            agent_registry=agent_registry,
                            room_config=room_config,
                            conversation_context=conversation_context,
                            request_user_id=request_user_id,
                            quoted_text=quoted_text,
                            hitl_request_id=(request.request_id if request else None),
                        )
                        if not saved:
                            trajectory.status = TrajectoryStatus.FAILED
                            return await self._log_and_return(
                                room_id,
                                trajectory,
                                SupervisorRunResult(
                                    status=RunStatus.FAILED, trajectory=trajectory
                                ),
                            )

                        await self._emit_processing_status(
                            room_id=room_id,
                            status=SSEProcessingStatus.AWAITING_INPUT,
                            message_id=user_message_id,
                            lifecycle_message_id=user_message_id,
                        )
                        return await self._log_and_return(
                            room_id,
                            trajectory,
                            SupervisorRunResult(
                                status=RunStatus.AWAITING_INPUT,
                                trajectory=trajectory,
                            ),
                        )

                    entry.completed_at = utcnow()

                    # Post-dispatch checkpoint: persist completed results.
                    _checkpoint_msg = await self._checkpoint_trajectory(
                        user_message_id,
                        trajectory,
                        cached_user_message=_checkpoint_msg,
                    )

                    # SSE: notify frontend of evaluation stage
                    if not (token and token.is_cancelled):
                        try:
                            await self._emit_processing_status(
                                room_id=room_id,
                                status=SSEProcessingStatus.PROCESSING,
                                message_id=user_message_id,
                                lifecycle_message_id=user_message_id,
                                details="Evaluating agent results...",
                            )
                        except Exception:
                            logger.debug(
                                "SSE stage notification failed (evaluating)",
                                exc_info=True,
                            )

                case ActionType.SYNTHESIZE:
                    entry = TrajectoryEntry(
                        step_number=step_number + 1,
                        action=action,
                        started_at=utcnow(),
                    )

                    completed_results = [
                        r
                        for e in trajectory.entries
                        for r in e.results
                        if r.success and r.status == StepStatus.SUCCESS
                    ]
                    if not completed_results:
                        logger.warning(
                            "Supervisor returned SYNTHESIZE with no agent results — treating as DONE"
                        )
                        entry.completed_at = utcnow()
                        trajectory.entries.append(entry)
                        trajectory.status = TrajectoryStatus.COMPLETED
                        return await self._log_and_return(
                            room_id,
                            trajectory,
                            SupervisorRunResult(
                                status=RunStatus.COMPLETED, trajectory=trajectory
                            ),
                        )

                    client_req_id = (
                        await self.task_state_store.resolve_client_request_id_for_message_id(
                            user_message_id
                        )
                    )
                    # SSE: notify frontend of synthesis stage
                    if not (token and token.is_cancelled):
                        try:
                            await self._emit_processing_status(
                                room_id=room_id,
                                status=SSEProcessingStatus.PROCESSING,
                                message_id=user_message_id,
                                lifecycle_message_id=user_message_id,
                                details={
                                    "turn_phase": "synthesizing",
                                    "message": "Synthesizing responses...",
                                },
                            )
                        except Exception:
                            logger.debug(
                                "SSE stage notification failed (synthesizing)",
                                exc_info=True,
                            )
                        try:
                            if trajectory.system_agent_message_id:
                                await self.delivery.send_task_update(
                                    room_id=room_id,
                                    message_id=trajectory.system_agent_message_id,
                                    status="working",
                                    task_content="Summarizing agent responses\u2026",
                                    client_request_id=client_req_id,
                                )
                        except Exception:
                            logger.debug(
                                "SSE summary working card update failed", exc_info=True
                            )

                    synth_coro = self._stream_supervisor_synthesis(
                        room_id=room_id,
                        user_message_id=user_message_id,
                        trajectory=trajectory,
                        synthesis_instruction=action.synthesis_instruction or "",
                        client_request_id=client_req_id,
                    )
                    try:
                        synthesis = (
                            await token.race(synth_coro) if token else await synth_coro
                        )
                    except CancellationError:
                        trajectory.status = TrajectoryStatus.CANCELED
                        return await self._log_and_return(
                            room_id,
                            trajectory,
                            SupervisorRunResult(
                                status=RunStatus.CANCELED, trajectory=trajectory
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
                                    db_msg.message_id, db_msg.message_content
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
                                "Failed to emit agent_response for supervisor synthesis",
                                exc_info=True,
                            )
                    entry.completed_at = utcnow()
                    trajectory.entries.append(entry)
                    trajectory.status = TrajectoryStatus.COMPLETED
                    return await self._log_and_return(
                        room_id,
                        trajectory,
                        SupervisorRunResult(
                            status=RunStatus.COMPLETED,
                            trajectory=trajectory,
                            synthesis_text=synthesis,
                        ),
                    )

                case ActionType.CLARIFY:
                    entry = TrajectoryEntry(
                        step_number=step_number + 1,
                        action=action,
                        started_at=utcnow(),
                        completed_at=utcnow(),
                    )
                    trajectory.entries.append(entry)
                    trajectory.status = TrajectoryStatus.AWAITING_INPUT

                    from models.hitl import HITLPromptType
                    from models.supervisor import ClarifyQuestion

                    if self.hitl_coordinator is None:
                        raise RuntimeError("HITL coordinator has not been bound")

                    # Build questions list — prefer structured questions[],
                    # fall back to legacy clarification_question string.
                    questions: list[ClarifyQuestion]
                    if action.questions:
                        questions = action.questions
                    else:
                        legacy_pt = action.prompt_type
                        questions = [
                            ClarifyQuestion(
                                prompt=(
                                    action.clarification_question
                                    or "The supervisor needs your input."
                                ),
                                prompt_type=legacy_pt,
                                choices=action.choices,
                            )
                        ]

                    group_id = uuid4().hex if len(questions) > 1 else None
                    last_request = None
                    created_messages: list[str] = []
                    created_request_ids: list[str] = []

                    async def _cleanup_clarify_artifacts(
                        request_ids: list[str] = created_request_ids,
                        message_ids: list[str] = created_messages,
                    ) -> None:
                        """Cancel HITL requests and delete agent messages created in this CLARIFY."""
                        for rid in request_ids:
                            try:
                                await self.hitl_coordinator.cancel_request(rid, room_id)
                            except Exception:
                                logger.warning(
                                    "Failed to cancel orphaned HITL request %s", rid
                                )
                        for mid in message_ids:
                            try:
                                await (
                                    self.message_writer.delete_room_agent_message_by_message_id(
                                        mid
                                    )
                                )
                            except Exception:
                                logger.warning(
                                    "Failed to delete orphaned HITL agent message %s",
                                    mid,
                                )

                    for qi, q in enumerate(questions):
                        q_prompt_type = HITLPromptType.TEXT
                        if q.prompt_type:
                            try:
                                q_prompt_type = HITLPromptType(q.prompt_type)
                            except ValueError:
                                pass

                        hitl_agent_message = self.room_runtime.create_agent_message(
                            room_id=room_id,
                            related_message_id=user_message_id,
                            agent_id=CoordinatorAgentId.SYSTEM_CLARIFIER,
                            content=q.prompt,
                            user_id=request_user_id,
                            step_number=step_number + 1,
                            task_content=q.prompt,
                            client_request_id=await self.task_state_store.resolve_client_request_id_for_message_id(
                                user_message_id
                            ),
                        )
                        await self.message_writer.add_room_agent_message(hitl_agent_message)
                        created_messages.append(hitl_agent_message.message_id)

                        request = await self.hitl_coordinator.request_input(
                            room_id=room_id,
                            user_message_id=user_message_id,
                            source="supervisor",
                            prompt=q.prompt,
                            prompt_type=q_prompt_type,
                            choices=q.choices,
                            agent_id=CoordinatorAgentId.SYSTEM_CLARIFIER,
                            agent_name="HYBRO AI",
                            source_step_id=str(step_number + 1),
                            continuation_message_id=user_message_id,
                            display_message_id=hitl_agent_message.message_id,
                            group_id=group_id,
                            group_total=len(questions) if group_id else None,
                            group_index=qi if group_id else None,
                        )

                        if request is None:
                            logger.warning(
                                "HITL request_input failed for message %s (q %d/%d) — cleaning up",
                                user_message_id,
                                qi + 1,
                                len(questions),
                            )
                            await _cleanup_clarify_artifacts()
                            trajectory.status = TrajectoryStatus.FAILED
                            return await self._log_and_return(
                                room_id,
                                trajectory,
                                SupervisorRunResult(
                                    status=RunStatus.FAILED, trajectory=trajectory
                                ),
                            )
                        created_request_ids.append(request.request_id)
                        last_request = request

                    saved = await self._save_interrupted_state(
                        kind=InterruptKind.HITL_SUPERVISOR,
                        trajectory=trajectory,
                        message_id=user_message_id,
                        room_id=room_id,
                        user_message_id=user_message_id,
                        message_text=message_text,
                        agent_registry=agent_registry,
                        room_config=room_config,
                        conversation_context=conversation_context,
                        request_user_id=request_user_id,
                        quoted_text=quoted_text,
                        hitl_request_id=(
                            last_request.request_id if last_request else None
                        ),
                    )
                    if not saved:
                        logger.warning(
                            "Failed to save continuation for message %s — cleaning up %d requests",
                            user_message_id,
                            len(created_request_ids),
                        )
                        await _cleanup_clarify_artifacts()
                        trajectory.status = TrajectoryStatus.FAILED
                        return await self._log_and_return(
                            room_id,
                            trajectory,
                            SupervisorRunResult(
                                status=RunStatus.FAILED, trajectory=trajectory
                            ),
                        )

                    await self._emit_processing_status(
                        room_id=room_id,
                        status=SSEProcessingStatus.AWAITING_INPUT,
                        message_id=user_message_id,
                        lifecycle_message_id=user_message_id,
                    )
                    return await self._log_and_return(
                        room_id,
                        trajectory,
                        SupervisorRunResult(
                            status=RunStatus.AWAITING_INPUT,
                            trajectory=trajectory,
                            clarification_question=action.clarification_question,
                        ),
                    )

                case ActionType.DONE:
                    entry = TrajectoryEntry(
                        step_number=step_number + 1,
                        action=action,
                        started_at=utcnow(),
                        completed_at=utcnow(),
                    )
                    trajectory.entries.append(entry)
                    trajectory.status = TrajectoryStatus.COMPLETED
                    return await self._log_and_return(
                        room_id,
                        trajectory,
                        SupervisorRunResult(
                            status=RunStatus.COMPLETED, trajectory=trajectory
                        ),
                        debate_mode=room_config.is_debate_mode,
                    )

            step_number += 1

        # Budget exhausted — force synthesis from whatever we have
        logger.warning(
            "supervisor_budget_exhausted",
            extra={
                "room_id": room_id,
                "trajectory_id": trajectory.trajectory_id,
                "max_steps": self.MAX_STEPS,
            },
        )
        if trajectory.entries:
            has_completed_results = any(
                r.success and r.status == StepStatus.SUCCESS
                for e in trajectory.entries
                for r in e.results
            )
            if not has_completed_results:
                trajectory.status = TrajectoryStatus.FAILED
                return await self._log_and_return(
                    room_id,
                    trajectory,
                    SupervisorRunResult(status=RunStatus.FAILED, trajectory=trajectory),
                )
            budget_client_req_id = (
                await self.task_state_store.resolve_client_request_id_for_message_id(
                    user_message_id
                )
            )
            # SSE: notify frontend of budget-exhaustion synthesis
            if not (token and token.is_cancelled):
                try:
                    await self._emit_processing_status(
                        room_id=room_id,
                        status=SSEProcessingStatus.PROCESSING,
                        message_id=user_message_id,
                        lifecycle_message_id=user_message_id,
                        details={
                            "turn_phase": "synthesizing",
                            "message": "Synthesizing responses...",
                        },
                    )
                except Exception:
                    logger.debug(
                        "SSE stage notification failed (budget synthesis)",
                        exc_info=True,
                    )
                try:
                    summary_message_id = f"summary-{user_message_id}"
                    await self.delivery.send_task_submitted(
                        room_id=room_id,
                        message_id=summary_message_id,
                        task_id=summary_message_id,
                        agent_name="HYBRO AI",
                        agent_id=CoordinatorAgentId.SYSTEM_HYBRO,
                        status="working",
                        related_message_id=user_message_id,
                        created_at=utcnow().isoformat(),
                        task_content="Summarizing agent responses\u2026",
                        client_request_id=budget_client_req_id,
                    )
                except Exception:
                    logger.debug(
                        "SSE summary working card failed (budget)", exc_info=True
                    )

            budget_synth_coro = self._stream_supervisor_synthesis(
                room_id=room_id,
                user_message_id=user_message_id,
                trajectory=trajectory,
                synthesis_instruction="Budget exhausted. Synthesize available results.",
                client_request_id=budget_client_req_id,
            )
            try:
                synthesis = (
                    await token.race(budget_synth_coro)
                    if token
                    else await budget_synth_coro
                )
            except CancellationError:
                trajectory.status = TrajectoryStatus.CANCELED
                return await self._log_and_return(
                    room_id,
                    trajectory,
                    SupervisorRunResult(
                        status=RunStatus.CANCELED, trajectory=trajectory
                    ),
                )
            trajectory.status = TrajectoryStatus.COMPLETED
            return await self._log_and_return(
                room_id,
                trajectory,
                SupervisorRunResult(
                    status=RunStatus.COMPLETED,
                    trajectory=trajectory,
                    synthesis_text=synthesis,
                ),
            )

        trajectory.status = TrajectoryStatus.FAILED
        return await self._log_and_return(
            room_id,
            trajectory,
            SupervisorRunResult(status=RunStatus.FAILED, trajectory=trajectory),
        )

    # ------------------------------------------------------------------
    # Concurrent agent dispatch
    # ------------------------------------------------------------------

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
    ) -> list[StepResult]:
        """Dispatch one or more agents, concurrently if multiple targets."""
        valid_ids = {a.agent_id for a in agent_registry}

        async def dispatch_one(target: DelegateTarget) -> StepResult:
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
                if request_user_id:
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
                await self.message_writer.add_room_agent_message(message)

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

                if result.status == ProcessingStatus.SUCCESS and request_user_id:
                    await self.rate_limit_service.record_request(
                        agent_id=agent.agent_id,
                        user_id=request_user_id,
                    )

                is_success = result.status == ProcessingStatus.SUCCESS
                step_result = StepResult(
                    step_number=step_number,
                    agent_id=target.agent_id,
                    agent_name=target.agent_name,
                    task=target.task,
                    response_text=result.response_text,
                    success=is_success,
                    status=StepStatus.SUCCESS if is_success else StepStatus.FAILED,
                    error_message=(
                        "Agent processing failed" if not is_success else None
                    ),
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
                work = asyncio.ensure_future(dispatch_one(targets[0]))
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
            return [await dispatch_one(targets[0])]

        if not token:
            return list(await asyncio.gather(*(dispatch_one(t) for t in targets)))

        # Manage individual tasks so that when cancellation fires we
        # can still collect results (with agent_message_id) from tasks
        # that already completed -- needed for cancel_descendants cleanup.
        tasks = [asyncio.ensure_future(dispatch_one(t)) for t in targets]
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
                await self.room_memory.add_agent_response_to_memory(
                    room_id=room_id,
                    agent_id=pr.agent_id,
                    agent_name=pr.agent_name or "Agent",
                    response_text=response_text,
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
