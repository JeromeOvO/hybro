"""SupervisorExecutor — adaptive step-at-a-time orchestration (V2).

The sole orchestration executor for supervisor-enabled rooms (``use_supervisor``).
``QueueExecutor`` continues to serve non-supervisor rooms and fast-path cases
(direct chat, @mention routing).

Responsibilities:
- Drive the decide → dispatch → record cycle
- Create ``RoomAgentMessage`` records one at a time (no pre-generation)
- Handle push notification pauses (serialize trajectory for resume)
- Enforce cancellation, rate limits, and step budget
- Dispatch concurrent targets via ``asyncio.gather``

See docs/SUPERVISOR_V2_DESIGN.md §6.2–§6.3 for design details.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING
from uuid import uuid4

from common.utils.cancellation import CancellationError, CancellationToken
from common.utils.logger import get_logger
from common.utils.time import utcnow
from models.hitl import InterruptKind
from modules.debate_dispatcher import SequentialDebateDispatcher
from models.supervisor_v2 import (
    ActionType,
    AgentProfile,
    DelegateTarget,
    RoomConfig,
    RunStatus,
    StepStatus,
    SupervisorAction,
    SupervisorRunResult,
    SupervisorTrajectory,
    TrajectoryEntry,
    TrajectoryStatus,
    V2StepResult,
)
from models.processing import ProcessingStatus
from services.a2a_constants import SSEProcessingStatus

if TYPE_CHECKING:
    from modules.AgentDispatcher import AgentDispatcher
    from modules.AgentMessageProcessor import AgentMessageProcessor
    from modules.TaskStateManager import TaskStateManager
    from services.database_service import DatabaseService
    from services.memory_service import RoomMemoryService
    from services.rate_limit_service import RateLimitService
    from services.room_coordinator_service import RoomCoordinatorService
    from services.room_services import RoomServices
    from services.room_supervisor_service import RoomSupervisorService
    from services.sse_services import SSEManager

logger = get_logger(__name__)


class SupervisorExecutor:
    """Executes the Supervisor's adaptive loop for a single user message."""

    MAX_STEPS: int = int(os.environ.get("SUPERVISOR_MAX_STEPS", "8"))

    def __init__(
        self,
        *,
        supervisor_service: RoomSupervisorService,
        room_services: RoomServices,
        tsm: TaskStateManager,
        sse_manager: SSEManager,
        database_service: DatabaseService,
        room_memory_service: RoomMemoryService,
        rate_limit_service: RateLimitService,
        agent_dispatcher: AgentDispatcher,
        agent_message_processor: AgentMessageProcessor,
        room_coordinator_service: RoomCoordinatorService,
    ) -> None:
        self.supervisor_service = supervisor_service
        self.room_services = room_services
        self.tsm = tsm
        self.sse_manager = sse_manager
        self.database_service = database_service
        self.room_memory_service = room_memory_service
        self.rate_limit_service = rate_limit_service
        self.agent_dispatcher = agent_dispatcher
        self.agent_message_processor = agent_message_processor
        self.room_coordinator_service = room_coordinator_service

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
                    return self._log_and_return(
                        room_id, trajectory,
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
            debate_agent_ids = self._snapshot_debate_agents(agent_registry, trajectory)
            effective_max_steps = max(self.MAX_STEPS, len(debate_agent_ids) + 1)

        while step_number < effective_max_steps:

            # --- Cancellation check ---
            if token and token.is_cancelled:
                trajectory.status = TrajectoryStatus.CANCELED
                return self._log_and_return(
                    room_id, trajectory,
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

            # SSE: notify frontend of planning stage
            try:
                await self.sse_manager.send_processing_status(
                    room_id, SSEProcessingStatus.PROCESSING, user_message_id,
                    details="Planning next action...",
                )
            except Exception:
                logger.debug("SSE stage notification failed (planning)", exc_info=True)

            # --- Debate mode fast-path (§8.13) ---
            if inflight_entry is not None:
                action = inflight_entry.action
            elif room_config.is_debate_mode:
                # Sequential debate: dispatch one agent per step
                debate_agent_ids = self._snapshot_debate_agents(agent_registry, trajectory)

                remaining_ids = self._get_remaining_debate_agent_ids(debate_agent_ids, trajectory)

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
                    return self._log_and_return(
                        room_id, trajectory,
                        SupervisorRunResult(
                            status=RunStatus.COMPLETED, trajectory=trajectory
                        ),
                        debate_mode=True,
                    )

                next_id = remaining_ids[0]
                # Find agent profile
                next_profile = next((a for a in agent_registry if a.agent_id == next_id), None)

                if next_profile is None or not next_profile.is_healthy:
                    # Skip unhealthy agent: create FAILED entry, checkpoint, continue
                    skip_entry = TrajectoryEntry(
                        step_number=step_number + 1,
                        action=SupervisorAction(
                            action=ActionType.DELEGATE,
                            reasoning=f"Debate: skipping unhealthy agent {next_id}",
                            targets=[DelegateTarget(agent_id=next_id, agent_name=next_id, task="")],
                        ),
                        started_at=utcnow(),
                        completed_at=utcnow(),
                        results=[V2StepResult(
                            step_number=step_number + 1,
                            agent_id=next_id,
                            agent_name=next_id,
                            task="",
                            response_text="",
                            success=False,
                            status=StepStatus.FAILED,
                            error_message="Agent unhealthy at dispatch time",
                        )],
                    )
                    trajectory.entries.append(skip_entry)
                    _checkpoint_msg = await self._checkpoint_trajectory(
                        user_message_id, trajectory, cached_user_message=_checkpoint_msg,
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
                # --- Ask supervisor for next action ---
                decide_coro = self.supervisor_service.decide_next(
                    message_text=message_text,
                    agent_registry=agent_registry,
                    room_config=room_config,
                    trajectory=trajectory,
                    conversation_context=conversation_context,
                    max_steps=self.MAX_STEPS,
                )
                try:
                    action = (
                        await token.race(decide_coro) if token
                        else await decide_coro
                    )
                except CancellationError:
                    trajectory.status = TrajectoryStatus.CANCELED
                    return self._log_and_return(
                        room_id, trajectory,
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
                    e.action.action == ActionType.DELEGATE
                    for e in trajectory.entries
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
                                    action.reasoning[:120]
                                    if action.reasoning
                                    else ""
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
                            "original_targets": [
                                t.agent_name for t in action.targets
                            ],
                            "deduped_targets": [
                                t.agent_name for t in deduped
                            ],
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
                    1 for e in trajectory.entries
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
                            trajectory, agent_registry, healthy_ids,
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
                        user_message_id, trajectory,
                        cached_user_message=_checkpoint_msg,
                    )

                    # SSE: notify frontend of delegation stage
                    try:
                        await self.sse_manager.send_processing_status(
                            room_id, SSEProcessingStatus.PROCESSING, user_message_id,
                            details=f"Delegating to {len(action.targets)} agent(s)...",
                        )
                    except Exception:
                        logger.debug("SSE stage notification failed (delegating)", exc_info=True)

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
                            await self.room_memory_service.add_agent_response_to_memory(
                                room_id=room_id,
                                agent_id=result.agent_id,
                                agent_name=result.agent_name,
                                response_text=result.response_text,
                                was_successful=result.success,
                            )

                    # Check for PAUSED (push notification agent)
                    paused = [r for r in results if r.status == StepStatus.PAUSED]
                    if paused:
                        entry.results = results
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
                            return self._log_and_return(
                                room_id, trajectory,
                                SupervisorRunResult(
                                    status=RunStatus.FAILED, trajectory=trajectory
                                ),
                            )
                        return self._log_and_return(
                            room_id, trajectory,
                            SupervisorRunResult(
                                status=RunStatus.PAUSED, trajectory=trajectory
                            ),
                        )

                    # Check for AWAITING_INPUT (agent returned input_required)
                    awaiting = [
                        r for r in results
                        if r.status == StepStatus.AWAITING_INPUT
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
                        from services.hitl_service import hitl_service

                        request = await hitl_service.request_input(
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
                            entry.results = results
                            trajectory.status = TrajectoryStatus.FAILED
                            return self._log_and_return(
                                room_id, trajectory,
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
                            hitl_request_id=(
                                request.request_id if request else None
                            ),
                        )
                        if not saved:
                            trajectory.status = TrajectoryStatus.FAILED
                            return self._log_and_return(
                                room_id, trajectory,
                                SupervisorRunResult(
                                    status=RunStatus.FAILED, trajectory=trajectory
                                ),
                            )

                        await self.sse_manager.send_processing_status(
                            room_id,
                            SSEProcessingStatus.AWAITING_INPUT,
                            user_message_id,
                        )
                        return self._log_and_return(
                            room_id, trajectory,
                            SupervisorRunResult(
                                status=RunStatus.AWAITING_INPUT,
                                trajectory=trajectory,
                            ),
                        )

                    entry.results = results
                    entry.completed_at = utcnow()

                    # Post-dispatch checkpoint: persist completed results.
                    _checkpoint_msg = await self._checkpoint_trajectory(
                        user_message_id, trajectory,
                        cached_user_message=_checkpoint_msg,
                    )

                    # SSE: notify frontend of evaluation stage
                    try:
                        await self.sse_manager.send_processing_status(
                            room_id, SSEProcessingStatus.PROCESSING, user_message_id,
                            details="Evaluating agent results...",
                        )
                    except Exception:
                        logger.debug("SSE stage notification failed (evaluating)", exc_info=True)

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
                        return self._log_and_return(
                            room_id, trajectory,
                            SupervisorRunResult(
                                status=RunStatus.COMPLETED, trajectory=trajectory
                            ),
                        )

                    # SSE: notify frontend of synthesis stage
                    try:
                        await self.sse_manager.send_processing_status(
                            room_id, SSEProcessingStatus.PROCESSING, user_message_id,
                            details="Synthesizing responses...",
                        )
                    except Exception:
                        logger.debug("SSE stage notification failed (synthesizing)", exc_info=True)

                    synth_coro = self.supervisor_service.synthesize_v2(
                        trajectory=trajectory,
                        synthesis_instruction=action.synthesis_instruction or "",
                    )
                    try:
                        synthesis = (
                            await token.race(synth_coro) if token
                            else await synth_coro
                        )
                    except CancellationError:
                        trajectory.status = TrajectoryStatus.CANCELED
                        return self._log_and_return(
                            room_id, trajectory,
                            SupervisorRunResult(
                                status=RunStatus.CANCELED, trajectory=trajectory
                            ),
                        )
                    entry.completed_at = utcnow()
                    trajectory.entries.append(entry)
                    trajectory.status = TrajectoryStatus.COMPLETED
                    return self._log_and_return(
                        room_id, trajectory,
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

                    from services.hitl_service import hitl_service
                    from models.hitl import HITLPromptType
                    from models.supervisor_v2 import ClarifyQuestion

                    # Build questions list — prefer structured questions[],
                    # fall back to legacy clarification_question string.
                    questions: list[ClarifyQuestion]
                    if action.questions:
                        questions = action.questions
                    else:
                        legacy_pt = action.prompt_type
                        questions = [ClarifyQuestion(
                            prompt=(
                                action.clarification_question
                                or "The supervisor needs your input."
                            ),
                            prompt_type=legacy_pt,
                            choices=action.choices,
                        )]

                    group_id = uuid4().hex if len(questions) > 1 else None
                    last_request = None
                    created_messages: list[str] = []
                    created_request_ids: list[str] = []

                    async def _cleanup_clarify_artifacts() -> None:
                        """Cancel HITL requests and delete agent messages created in this CLARIFY."""
                        for rid in created_request_ids:
                            try:
                                await hitl_service.cancel_request(rid, room_id)
                            except Exception:
                                logger.warning("Failed to cancel orphaned HITL request %s", rid)
                        for mid in created_messages:
                            try:
                                await self.database_service.delete_room_agent_message_by_message_id(mid)
                            except Exception:
                                logger.warning("Failed to delete orphaned HITL agent message %s", mid)

                    for qi, q in enumerate(questions):
                        q_prompt_type = HITLPromptType.TEXT
                        if q.prompt_type:
                            try:
                                q_prompt_type = HITLPromptType(q.prompt_type)
                            except ValueError:
                                pass

                        hitl_agent_message = self.room_services.create_agent_message(
                            room_id=room_id,
                            related_message_id=user_message_id,
                            agent_id="supervisor_hitl",
                            content=q.prompt,
                            user_id=request_user_id,
                            step_number=step_number + 1,
                            task_content=q.prompt,
                        )
                        await self.database_service.add_room_agent_message(
                            hitl_agent_message
                        )
                        created_messages.append(hitl_agent_message.message_id)

                        request = await hitl_service.request_input(
                            room_id=room_id,
                            user_message_id=user_message_id,
                            source="supervisor",
                            prompt=q.prompt,
                            prompt_type=q_prompt_type,
                            choices=q.choices,
                            agent_id="supervisor_hitl",
                            agent_name="Question & Answer",
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
                                user_message_id, qi + 1, len(questions),
                            )
                            await _cleanup_clarify_artifacts()
                            trajectory.status = TrajectoryStatus.FAILED
                            return self._log_and_return(
                                room_id, trajectory,
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
                            user_message_id, len(created_request_ids),
                        )
                        await _cleanup_clarify_artifacts()
                        trajectory.status = TrajectoryStatus.FAILED
                        return self._log_and_return(
                            room_id, trajectory,
                            SupervisorRunResult(
                                status=RunStatus.FAILED, trajectory=trajectory
                            ),
                        )

                    await self.sse_manager.send_processing_status(
                        room_id,
                        SSEProcessingStatus.AWAITING_INPUT,
                        user_message_id,
                    )
                    return self._log_and_return(
                        room_id, trajectory,
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
                    return self._log_and_return(
                        room_id, trajectory,
                        SupervisorRunResult(
                            status=RunStatus.COMPLETED, trajectory=trajectory
                        ),
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
                return self._log_and_return(
                    room_id, trajectory,
                    SupervisorRunResult(
                        status=RunStatus.FAILED, trajectory=trajectory
                    ),
                )
            # SSE: notify frontend of budget-exhaustion synthesis
            try:
                await self.sse_manager.send_processing_status(
                    room_id, SSEProcessingStatus.PROCESSING, user_message_id,
                    details="Synthesizing responses...",
                )
            except Exception:
                logger.debug("SSE stage notification failed (budget synthesis)", exc_info=True)

            budget_synth_coro = self.supervisor_service.synthesize_v2(
                trajectory=trajectory,
                synthesis_instruction="Budget exhausted. Synthesize available results.",
            )
            try:
                synthesis = (
                    await token.race(budget_synth_coro) if token
                    else await budget_synth_coro
                )
            except CancellationError:
                trajectory.status = TrajectoryStatus.CANCELED
                return self._log_and_return(
                    room_id, trajectory,
                    SupervisorRunResult(
                        status=RunStatus.CANCELED, trajectory=trajectory
                    ),
                )
            trajectory.status = TrajectoryStatus.COMPLETED
            return self._log_and_return(
                room_id, trajectory,
                SupervisorRunResult(
                    status=RunStatus.COMPLETED,
                    trajectory=trajectory,
                    synthesis_text=synthesis,
                ),
            )

        trajectory.status = TrajectoryStatus.FAILED
        return self._log_and_return(
            room_id, trajectory,
            SupervisorRunResult(
                status=RunStatus.FAILED, trajectory=trajectory
            ),
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
    ) -> list[V2StepResult]:
        """Dispatch one or more agents, concurrently if multiple targets."""
        valid_ids = {a.agent_id for a in agent_registry}

        async def dispatch_one(target: DelegateTarget) -> V2StepResult:
            try:
                # Validate agent_id against registry before any DB writes
                if target.agent_id not in valid_ids:
                    logger.warning(
                        "Supervisor hallucinated agent_id=%s (valid: %s)",
                        target.agent_id,
                        valid_ids,
                    )
                    return V2StepResult(
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
                    logger.warning(
                        "dispatch_one: agent %s not found or inactive",
                        target.agent_id,
                    )
                    return V2StepResult(
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
                        return V2StepResult(
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
                message = self.room_services.create_agent_message(
                    room_id=room_id,
                    related_message_id=user_message_id,
                    agent_id=target.agent_id,
                    content=target.task,
                    user_id=request_user_id,
                    step_number=step_number,
                    total_steps=None,
                    task_content=target.task,
                )
                await self.database_service.add_room_agent_message(message)

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
                    return V2StepResult(
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
                    return V2StepResult(
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
                step_result = V2StepResult(
                    step_number=step_number,
                    agent_id=target.agent_id,
                    agent_name=target.agent_name,
                    task=target.task,
                    response_text=result.response_text,
                    success=is_success,
                    status=StepStatus.SUCCESS if is_success else StepStatus.FAILED,
                    error_message=(
                        "Agent processing failed"
                        if not is_success
                        else None
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
                logger.warning(
                    "dispatch_one cancelled for agent %s", target.agent_id
                )
                return V2StepResult(
                    step_number=step_number,
                    agent_id=target.agent_id,
                    agent_name=target.agent_name,
                    task=target.task,
                    response_text="",
                    success=False,
                    status=StepStatus.FAILED,
                    error_message="Agent dispatch was cancelled",
                )
            except Exception as e:
                logger.exception(
                    "dispatch_one failed for agent %s: %s", target.agent_id, e
                )
                return V2StepResult(
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
                return [V2StepResult(
                    step_number=step_number,
                    agent_id=targets[0].agent_id,
                    agent_name=targets[0].agent_name,
                    task=targets[0].task,
                    response_text="",
                    success=False,
                    status=StepStatus.FAILED,
                    error_message="Agent dispatch was cancelled",
                )]
            return [await dispatch_one(targets[0])]

        if not token:
            return list(
                await asyncio.gather(*(dispatch_one(t) for t in targets))
            )

        # Manage individual tasks so that when cancellation fires we
        # can still collect results (with agent_message_id) from tasks
        # that already completed -- needed for cancel_descendants cleanup.
        tasks = [asyncio.ensure_future(dispatch_one(t)) for t in targets]
        cancel_waiter = token.wait()
        all_work = asyncio.ensure_future(
            asyncio.gather(*tasks, return_exceptions=True)
        )

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
                r if isinstance(r, V2StepResult) else V2StepResult(
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

        results: list[V2StepResult] = []
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
                results.append(V2StepResult(
                    step_number=step_number,
                    agent_id=t.agent_id,
                    agent_name=t.agent_name,
                    task=t.task,
                    response_text="",
                    success=False,
                    status=StepStatus.FAILED,
                    error_message="Agent dispatch was cancelled",
                ))
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

        non_failed = [
            a for aid, a in healthy_map.items() if aid not in failed_ids
        ]
        if non_failed:
            return non_failed[0]

        return next(iter(healthy_map.values()))

    # ------------------------------------------------------------------
    # Logging helper
    # ------------------------------------------------------------------

    @staticmethod
    def _log_and_return(
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
                user_message = (
                    await self.database_service.get_room_user_message_by_message_id(
                        user_message_id
                    )
                )
            if user_message:
                if not isinstance(user_message.extend_info, dict):
                    user_message.extend_info = {}
                user_message.extend_info["supervisor_trajectory"] = (
                    trajectory.model_dump(mode="json")
                )
                await self.database_service.update_room_user_message_by_message_id(
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
        paused_results: list[V2StepResult] | None = None,
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
            "supervisor_v2": True,
            "interrupt_kind": kind.value,
            "trajectory": trajectory.model_dump(mode="json"),
            "room_id": room_id,
            "user_message_id": user_message_id,
            "message_text": message_text,
            "agent_registry": [
                p.model_dump(mode="json") for p in agent_registry
            ],
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
                success = await self.database_service.save_continuation_on_message(
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
                logger.error(
                    "SupervisorExecutor: HITL_AGENT save missing message_id"
                )
                return False
            success = await self.database_service.save_continuation_on_message(
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
            success = await self.database_service.save_continuation_on_user_message(
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

        logger.error(
            "SupervisorExecutor: Unknown interrupt kind %s", kind
        )
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
    ) -> list[str]:
        """Initialize or restore debate participant snapshot.

        First call: records healthy agent IDs into trajectory.debate_agent_ids.
        Subsequent calls: returns the existing snapshot (idempotent).
        """
        if trajectory.debate_agent_ids is not None:
            return trajectory.debate_agent_ids
        ids = [a.agent_id for a in agent_registry if a.is_healthy]
        trajectory.debate_agent_ids = ids
        return ids

    @staticmethod
    def _get_remaining_debate_agent_ids(
        debate_agent_ids: list[str],
        trajectory: SupervisorTrajectory,
    ) -> list[str]:
        """Return agent IDs not yet dispatched (preserving original order)."""
        dispatched: set[str] = set()
        for entry in trajectory.entries:
            if entry.action.action == ActionType.DELEGATE:
                for target in entry.action.targets:
                    dispatched.add(target.agent_id)
        return [aid for aid in debate_agent_ids if aid not in dispatched]

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
                if result.success and result.status == StepStatus.SUCCESS and result.response_text:
                    responses.append((result.agent_name, result.response_text))
        return responses
