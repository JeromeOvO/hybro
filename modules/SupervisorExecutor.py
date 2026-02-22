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

from common.utils.cancellation import CancellationError, CancellationToken
from common.utils.logger import get_logger
from common.utils.time import utcnow
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
    V2StepResult,
)
from models.processing import ProcessingStatus

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
        if (
            resumed_trajectory is not None
            and room_config.is_debate_mode
            and step_number > 0
        ):
            still_paused = any(
                r.status == StepStatus.PAUSED
                for entry in trajectory.entries
                for r in entry.results
            )
            if not still_paused:
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
                trajectory.status = "completed"
                return self._log_and_return(
                    room_id, trajectory,
                    SupervisorRunResult(
                        status=RunStatus.COMPLETED, trajectory=trajectory
                    ),
                    debate_mode=True,
                )

        while step_number < self.MAX_STEPS:

            # --- Cancellation check ---
            if token and token.is_cancelled:
                trajectory.status = "canceled"
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

            # --- Debate mode fast-path (§8.13) ---
            if inflight_entry is not None:
                action = inflight_entry.action
            elif room_config.is_debate_mode and step_number == 0:
                healthy_agents = [a for a in agent_registry if a.is_healthy]
                if not healthy_agents:
                    logger.warning(
                        "supervisor_debate_no_healthy_agents",
                        extra={
                            "room_id": room_id,
                            "trajectory_id": trajectory.trajectory_id,
                        },
                    )
                    trajectory.status = "failed"
                    return self._log_and_return(
                        room_id, trajectory,
                        SupervisorRunResult(
                            status=RunStatus.FAILED, trajectory=trajectory
                        ),
                    )
                action = SupervisorAction(
                    action=ActionType.DELEGATE,
                    reasoning="Debate mode: delegating to all agents concurrently",
                    targets=[
                        DelegateTarget(
                            agent_id=a.agent_id,
                            agent_name=a.agent_name,
                            task=message_text,
                        )
                        for a in healthy_agents
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
                    trajectory.status = "canceled"
                    return self._log_and_return(
                        room_id, trajectory,
                        SupervisorRunResult(
                            status=RunStatus.CANCELED, trajectory=trajectory
                        ),
                    )
                trajectory.total_supervisor_calls += 1

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
                            )

                    # Check for PAUSED (push notification agent)
                    paused = [r for r in results if r.status == StepStatus.PAUSED]
                    if paused:
                        entry.results = results
                        trajectory.status = "running"
                        saved = await self._save_pause_state(
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
                            trajectory.status = "failed"
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

                    entry.results = results
                    entry.completed_at = utcnow()

                    # Post-dispatch checkpoint: persist completed results.
                    _checkpoint_msg = await self._checkpoint_trajectory(
                        user_message_id, trajectory,
                        cached_user_message=_checkpoint_msg,
                    )

                    # Debate mode: after all agents respond, done (no synthesis)
                    if room_config.is_debate_mode and step_number == 0:
                        done_entry = TrajectoryEntry(
                            step_number=len(trajectory.entries) + 1,
                            action=SupervisorAction(
                                action=ActionType.DONE,
                                reasoning="Debate mode complete",
                            ),
                            started_at=utcnow(),
                            completed_at=utcnow(),
                        )
                        trajectory.entries.append(done_entry)
                        trajectory.status = "completed"
                        return self._log_and_return(
                            room_id, trajectory,
                            SupervisorRunResult(
                                status=RunStatus.COMPLETED, trajectory=trajectory
                            ),
                            debate_mode=True,
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
                        trajectory.status = "completed"
                        return self._log_and_return(
                            room_id, trajectory,
                            SupervisorRunResult(
                                status=RunStatus.COMPLETED, trajectory=trajectory
                            ),
                        )

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
                        trajectory.status = "canceled"
                        return self._log_and_return(
                            room_id, trajectory,
                            SupervisorRunResult(
                                status=RunStatus.CANCELED, trajectory=trajectory
                            ),
                        )
                    entry.completed_at = utcnow()
                    trajectory.entries.append(entry)
                    trajectory.status = "completed"
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
                    trajectory.status = "clarifying"
                    return self._log_and_return(
                        room_id, trajectory,
                        SupervisorRunResult(
                            status=RunStatus.CLARIFYING,
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
                    trajectory.status = "completed"
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
                trajectory.status = "failed"
                return self._log_and_return(
                    room_id, trajectory,
                    SupervisorRunResult(
                        status=RunStatus.FAILED, trajectory=trajectory
                    ),
                )
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
                trajectory.status = "canceled"
                return self._log_and_return(
                    room_id, trajectory,
                    SupervisorRunResult(
                        status=RunStatus.CANCELED, trajectory=trajectory
                    ),
                )
            trajectory.status = "completed"
            return self._log_and_return(
                room_id, trajectory,
                SupervisorRunResult(
                    status=RunStatus.COMPLETED,
                    trajectory=trajectory,
                    synthesis_text=synthesis,
                ),
            )

        trajectory.status = "failed"
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

                if result.status == ProcessingStatus.PAUSED:
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
    # Push notification pause persistence
    # ------------------------------------------------------------------

    async def _save_pause_state(
        self,
        trajectory: SupervisorTrajectory,
        paused_results: list[V2StepResult],
        room_id: str,
        user_message_id: str,
        request_user_id: str | None,
        message_text: str,
        agent_registry: list[AgentProfile],
        room_config: RoomConfig,
        conversation_context: str | None,
        quoted_text: str | None = None,
    ) -> bool:
        """Serialize the trajectory + inputs for webhook resume.

        Returns ``True`` if at least one pause state was saved successfully,
        ``False`` if all saves failed (webhook resume will not work).
        """
        saved_any = False
        for pr in paused_results:
            if not pr.paused_message_id:
                logger.error(
                    "SupervisorExecutor: PAUSED result has no paused_message_id — "
                    "cannot save continuation. agent=%s, agent_message_id=%s",
                    pr.agent_id,
                    pr.agent_message_id,
                )
                continue

            pause_state = {
                "supervisor_v2": True,
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

            success = await self.database_service.save_continuation_on_message(
                pr.paused_message_id, pause_state
            )
            if not success:
                logger.error(
                    "SupervisorExecutor: Failed to save pause state for message %s",
                    pr.paused_message_id,
                )
            else:
                saved_any = True
                logger.info(
                    "supervisor_pause_saved",
                    extra={
                        "room_id": room_id,
                        "paused_message_id": pr.paused_message_id,
                        "trajectory_id": trajectory.trajectory_id,
                    },
                )

        if not saved_any:
            logger.error(
                "SupervisorExecutor: No pause state was saved for any paused result "
                "— webhook resume will fail. room_id=%s, user_message_id=%s",
                room_id,
                user_message_id,
            )

        return saved_any
