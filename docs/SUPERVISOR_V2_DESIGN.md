# Supervisor V2: Adaptive Loop Design

**Date**: February 20, 2026
**Status**: Phase 5 Complete
**Scope**: Replace the plan-then-execute Supervisor (V1) with a step-at-a-time adaptive loop
**Predecessor**: [SUPERVISOR_PATTERN_DESIGN.md](./SUPERVISOR_PATTERN_DESIGN.md) (PR #83)

---

## 1. Why V2

The V1 Supervisor (PR #83) generates a **full execution plan upfront**, then hands it to the existing `QueueExecutor` for sequential processing. An optional review hook can revise or retry steps mid-execution. After extensive code review (see SUPERVISOR_PATTERN_DESIGN.md §14), this architecture has fundamental issues:

| V1 Problem | Root Cause |
|---|---|
| REVISE/RETRY corrupt the queue (§14.9) | Adaptive actions bolted onto a static-queue loop |
| `context_from_steps` never consumed (§14.1) | Plan generated before any agent has responded |
| `strategy` declared but never enforced (§arch-2) | QueueExecutor is strategy-agnostic; "parallel" = sequential with no deps |
| `clarify` strategy has no handler (§arch-2) | No way to pause mid-plan and ask the user |
| Dual state: `SupervisorPlan.steps` vs `RoomAgentMessage` records (§arch-6) | Two representations of intent that diverge after REVISE |
| Plan mutated in-place across 5 modules (§arch-3) | No single lifecycle owner |

All of these trace back to one design choice: **generating the entire plan before execution starts**. V2 eliminates this by replacing the plan-then-execute model with an adaptive loop where the Supervisor decides one action at a time after each agent returns.

---

## 2. Design Goals

1. **Adaptive by default** — Every step is an implicit review. No separate REVISE/RETRY/SKIP mechanics.
2. **Single source of truth** — The trajectory (what happened) is the only state. No parallel plan vs. message representations.
3. **Strategy as behavior, not metadata** — "parallel" means concurrent dispatch. "clarify" means pause and ask. Enforced by the executor, not just declared.
4. **Preserve existing infrastructure** — Reuse `_process_single_message`, `ResponseProcessor`, SSE streaming, push notification pause/resume, `RoomAgentMessage`, room memory. No frontend changes.
5. **Zero overhead for simple cases** — Direct chat (1 agent, no debate) and @mention routing remain unchanged.
6. **Cost-bounded** — Hard cap on supervisor LLM calls per user message.

### 2.1 Known Trade-Offs vs. V1

V2 is not a strict improvement in every dimension. The following regressions are accepted as worthwhile trade-offs:

| Trade-Off | V1 Behavior | V2 Behavior | Acceptable Because |
|---|---|---|---|
| **Time-to-first-agent-message** | All agent messages pre-generated; frontend shows pending states immediately | First agent message created only after `decide_next` completes (~400ms delay) | The pre-generated messages in V1 were speculative — they often showed wrong step counts after REVISE |
| **Supervisor call count** | In practice ~2 calls (plan + synthesis); review almost never fired due to §14.1 bug | Always ~3-4 calls for 2-agent case | Extra calls are small (gpt-4o-mini, ~100 output tokens); V1's 2-call number reflected broken review, not correct behavior |
| **Explicit dependency graph** | `depends_on` and `context_from_steps` declared upfront; clear graph of intent | No explicit graph; supervisor infers dependencies from trajectory | V1's `depends_on` was declared but context injection never worked (§14.1); the graph was aspirational, not functional |
| **Plan visibility / predictability** | Could theoretically show user the full plan upfront ("Agent A then Agent B") | Cannot know the full plan until execution unfolds | V1 plan was frequently revised mid-execution anyway; user-visible plans would have been misleading |

### 2.2 Where V2 Leads Industry Frameworks

Compared to LangGraph, AutoGen, CrewAI, and the OpenAI Agents SDK, V2 has genuine advantages in three areas:

| Advantage | V2 Approach | Framework Gap |
|---|---|---|
| **Native SSE streaming** | Reuses `_process_single_message` / `ResponseProcessor` which stream directly to the frontend SSE connection | Frameworks emit to graph state; connecting that to a live SSE stream requires additional plumbing |
| **Async push notification agents** | Full execution loop can pause, serialize the trajectory, wait for a webhook that arrives hours later, and resume exactly where it left off | Frameworks support synchronous human-in-the-loop interrupts but not async external webhooks with indefinite wait times |
| **Room-scoped persistent memory** | Every agent result is written to `RoomMemoryService` during the loop; subsequent agents in the same room conversation automatically have cross-session context | Frameworks scope memory to the current thread/run; cross-conversation room memory requires custom implementation |

---

## 3. Architecture Overview

```
                         ┌──────────────────────────────────┐
                         │        User sends message         │
                         └──────────────┬───────────────────┘
                                        │
                                        ▼
                         ┌──────────────────────────────────┐
                         │     Fast-path check:              │
                         │  Direct chat? @mention?           │
                         └──────┬──────────────┬────────────┘
                           YES  │              │ NO
                                ▼              ▼
                  ┌──────────────────┐  ┌───────────────────────┐
                  │ Existing pipeline │  │   SUPERVISOR LOOP      │
                  │ (no LLM routing)  │  │                        │
                  └────────┬─────────┘  │  ┌─────────────────┐   │
                           │            │  │ Gather context   │   │
                           │            │  │ (trajectory +    │   │
                           │            │  │  room memory +   │   │
                           │            │  │  agent registry) │   │
                           │            │  └────────┬────────┘   │
                           │            │           ▼            │
                           │            │  ┌─────────────────┐   │
                           │            │  │ Supervisor LLM   │   │
                           │            │  │ → NextAction     │   │
                           │            │  └────────┬────────┘   │
                           │            │           │            │
                           │            │     ┌─────┴─────┐     │
                           │            │     │           │     │
                           │            │  delegate   synthesize │
                           │            │  clarify    done       │
                           │            │     │           │     │
                           │            │     ▼           ▼     │
                           │            │  ┌──────────┐  EXIT   │
                           │            │  │ Dispatch  │         │
                           │            │  │ agent(s)  │         │
                           │            │  │ Collect   │         │
                           │            │  │ result    │         │
                           │            │  └─────┬────┘         │
                           │            │        │  loop back    │
                           │            │        └───────────┘   │
                           │            └───────────────────────┘
                           │
                           ▼
                  ┌─────────────────────────────────────────────┐
                  │  SSE: COMPLETED                              │
                  └─────────────────────────────────────────────┘
```

The key difference: there is no `SupervisorPlan` artifact. The Supervisor maintains a **trajectory** (list of actions taken and results received) and makes one decision per iteration.

---

## 4. Data Models

### 4.1 Supervisor Action (LLM output — one per iteration)

```python
class ActionType(StrEnum):
    DELEGATE = "delegate"       # Send task to one or more agents
    SYNTHESIZE = "synthesize"   # Produce final combined answer
    CLARIFY = "clarify"         # Ask the user for more information
    DONE = "done"               # Nothing more to do (single-agent result is sufficient)


class DelegateTarget(BaseModel):
    """A single agent delegation within a DELEGATE action."""
    agent_id: str
    agent_name: str
    task: str                   # Tailored instruction for this agent


class SupervisorAction(BaseModel):
    """Single next-action decision produced by the Supervisor LLM."""
    action: ActionType
    reasoning: str              # Why this action (for logging)

    # DELEGATE fields
    targets: list[DelegateTarget] = []  # 1 target = serial, 2+ = concurrent

    # SYNTHESIZE fields
    synthesis_instruction: str | None = None

    # CLARIFY fields
    clarification_question: str | None = None
```

When `targets` has multiple entries, the executor dispatches them **concurrently** with `asyncio.gather`. This is how "parallel" execution is expressed — not as a strategy label, but as a multi-target delegate action.

### 4.2 Trajectory (accumulates during execution)

```python
class TrajectoryEntry(BaseModel):
    """One step in the execution trajectory.

    Created for ALL action types (DELEGATE, SYNTHESIZE, CLARIFY, DONE),
    not just DELEGATE. This ensures the trajectory is a complete audit log
    of every supervisor decision.
    """
    step_number: int
    action: SupervisorAction
    results: list[StepResult] = []      # One per target in a delegate action
    started_at: datetime
    completed_at: datetime | None = None


class SupervisorTrajectory(BaseModel):
    """Full execution trajectory for a user message.
    
    Stored in user_message.extend_info.supervisor_trajectory for auditability.
    """
    trajectory_id: str = Field(default_factory=lambda: uuid4().hex)
    entries: list[TrajectoryEntry] = []
    status: Literal["running", "completed", "failed", "canceled", "clarifying"] = "running"
    total_supervisor_calls: int = 0
    created_at: datetime = Field(default_factory=utcnow)

    clarify_user_reply: str | None = None          # User's reply to a CLARIFY question (Phase 4)
    clarify_original_message_id: str | None = None  # Original user_message_id that triggered CLARIFY;
                                                     # survives pause/resume serialization
```

### 4.3 StepResult (new V2 model — separate from V1)

V1's `StepResult` (in `models/supervisor.py`) uses `step_id`, `task_description`, and has no `status` field. V2 uses a different shape. To avoid conflicts during migration, V2 models live in a **new file** `models/supervisor_v2.py`. V1 models remain untouched until Phase 5 deprecation.

**Phase 5 (completed)**: `models/supervisor.py` has been deleted. `models/supervisor_v2.py` is the sole supervisor model module and contains all shared models (`AgentProfile`, `RoomConfig`) that were previously in the V1 file.

```python
# models/supervisor_v2.py

class StepStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    PAUSED = "paused"       # Push notification agent — waiting for webhook


class V2StepResult(BaseModel):
    """Result of a completed (or paused) agent delegation.

    Named V2StepResult to avoid import conflicts with V1's StepResult
    in models/supervisor.py during the migration period.
    """
    step_number: int
    agent_id: str
    agent_name: str
    task: str
    response_text: str
    success: bool = True
    error_message: str | None = None
    completed_at: datetime = Field(default_factory=utcnow)

    status: StepStatus = StepStatus.SUCCESS
    paused_message_id: str | None = None    # RoomAgentMessage.id for the paused step
    agent_message_id: str | None = None     # RoomAgentMessage.id created during dispatch
```

> **Note**: Throughout the rest of this document, `StepResult` refers to `V2StepResult` from `models/supervisor_v2.py` unless otherwise noted. The `agent_message_id` field is added to link results back to the `RoomAgentMessage` record, needed for push notification resume matching (open question #1).

### 4.4 What's Removed from V1

| V1 Model | V2 Status |
|---|---|
| `SupervisorPlan` | **Eliminated.** No upfront plan. |
| `SupervisorStep` | **Eliminated.** Replaced by `DelegateTarget`. |
| `SupervisorReview` | **Eliminated.** Every loop iteration is an implicit review. |
| `ReviewAction` | **Eliminated.** No separate CONTINUE/REVISE/RETRY/SKIP. |
| `SupervisorStrategy` | **Eliminated.** Strategy is expressed by the action sequence, not a label. |
| `AgentProfile` | **Kept.** Used to build the agent registry for the LLM prompt. |
| `RoomConfig` | **Kept.** |

---

## 5. Supervisor LLM Prompt

A single prompt template serves all iterations. The trajectory grows with each step.

```python
SUPERVISOR_SYSTEM_PROMPT = """You are a Supervisor coordinating specialist agents in a chat room.

## Available Agents
{agent_registry}

## Your Job
Decide the NEXT action. You will be called repeatedly — once after each agent responds.
Output ONLY valid JSON matching the schema below.

## Action Types
1. DELEGATE: Send a task to one or more agents.
   - Single target: the agent works alone.
   - Multiple targets: they work concurrently on independent sub-tasks.
   - Write each task as a clear, specific instruction tailored for that agent.
   - Include relevant context from prior results when the agent needs it.
2. SYNTHESIZE: All needed agent results are collected. Produce a unified answer.
   - Only use when 2+ agents have responded and their results need combining.
3. CLARIFY: The user's message is ambiguous. Ask a clarification question.
   - Use sparingly — only when you truly cannot determine which agent to use.
4. DONE: The work is complete. No synthesis needed (e.g., single agent already answered fully).

## Rules
- Prefer DELEGATE with a single target unless sub-tasks are truly independent.
- After each agent result, evaluate quality. If inadequate, you can delegate to the
  same agent with a refined task — no special "retry" mechanism needed.
- If an agent's result changes what you planned to do next, simply adapt.
- Do NOT delegate to agents that are unhealthy (status: unhealthy).
- You have a maximum of {max_steps} actions. Use SYNTHESIZE or DONE before the limit.

## Output Schema
{{
  "action": "delegate" | "synthesize" | "clarify" | "done",
  "reasoning": "Brief explanation",
  "targets": [
    {{"agent_id": "uuid", "agent_name": "Name", "task": "What to do"}}
  ],
  "synthesis_instruction": "How to combine results" | null,
  "clarification_question": "What to ask the user" | null
}}"""

SUPERVISOR_USER_PROMPT = """## Conversation Context
{conversation_context}

{debate_mode_note}

## User Message
{message_text}

## Execution So Far
{trajectory_summary}

## What should happen next?"""
```

The `trajectory_summary` is built from `SupervisorTrajectory.entries`. When the trajectory has more than `_TRAJECTORY_WINDOW` entries (default: 5), older entries are collapsed into a one-line summary to keep the prompt within reasonable token limits. The user's clarification reply (if any) is rendered as a top-level section at the end, outside the windowed loop, so it's always visible to the LLM regardless of window position.

```python
_TRAJECTORY_WINDOW: int = 5

@classmethod
def _format_trajectory(cls, trajectory: SupervisorTrajectory, *, window: int | None = None) -> str:
    if window is None:
        window = cls._TRAJECTORY_WINDOW
    if not trajectory.entries:
        return "No actions taken yet."

    entries = trajectory.entries
    lines: list[str] = []

    # Collapse older entries into a one-line summary
    if len(entries) > window:
        older = entries[: len(entries) - window]
        summary_parts: list[str] = []
        for e in older:
            action_type = e.action.action.upper()
            if e.results:
                for r in e.results:
                    if r.status == StepStatus.PAUSED:
                        tag = f"{r.agent_name}(PAUSED)"
                    elif r.success:
                        tag = r.agent_name
                    else:
                        tag = f"{r.agent_name}(FAILED)"
                    summary_parts.append(tag)
            elif action_type == "CLARIFY":
                summary_parts.append("CLARIFY asked")
            elif action_type == "DONE":
                summary_parts.append("DONE")
            else:
                summary_parts.append(action_type)
        summary_text = ", ".join(summary_parts) if summary_parts else "no actions"
        lines.append(f"Steps 1–{older[-1].step_number}: [{summary_text}]")
        entries = entries[len(entries) - window :]

    for entry in entries:
        lines.append(f"### Step {entry.step_number}: {entry.action.action.upper()}")
        if entry.action.action == ActionType.DELEGATE:
            for target in entry.action.targets:
                lines.append(f"  Delegated to {target.agent_name}: {target.task}")
            for result in entry.results:
                if result.status == StepStatus.PAUSED:
                    status = "PAUSED (awaiting external response)"
                elif result.success:
                    status = "SUCCESS"
                else:
                    status = f"FAILED: {result.error_message}"
                response_preview = result.response_text[:500]
                if len(result.response_text) > 500:
                    response_preview += " [truncated]"
                lines.append(f"  → {result.agent_name} [{status}]: {response_preview}")
        elif entry.action.action == ActionType.CLARIFY:
            lines.append(f"  Asked user: {entry.action.clarification_question}")
        elif entry.action.action == ActionType.SYNTHESIZE:
            lines.append(f"  Instruction: {entry.action.synthesis_instruction}")
        elif entry.action.action == ActionType.DONE:
            lines.append(f"  Reasoning: {entry.action.reasoning}")

    if trajectory.clarify_user_reply:
        lines.append(f"\n### User's Clarification Reply\n{trajectory.clarify_user_reply}")

    return "\n".join(lines)
```

---

## 6. Service Design

### 6.1 `RoomSupervisorService` (V2)

The service is stateless. All state lives in the `SupervisorTrajectory` passed between calls.

```python
class RoomSupervisorService:
    """Supervisor V2: adaptive step-at-a-time orchestration."""

    async def decide_next(
        self,
        message_text: str,
        agent_registry: list[AgentProfile],
        room_config: RoomConfig,
        trajectory: SupervisorTrajectory,
        conversation_context: str | None = None,
    ) -> SupervisorAction:
        """Ask the Supervisor LLM for the next action.

        Called once per loop iteration by the SupervisorExecutor.

        Returns:
            SupervisorAction — what to do next.
            On LLM failure, returns a DONE action (fail-open).
        """
        ...

    async def synthesize(
        self,
        trajectory: SupervisorTrajectory,
        synthesis_instruction: str,
    ) -> str:
        """Produce a synthesis from collected results.

        Called when decide_next returns SYNTHESIZE.
        Reuses the same synthesis prompt as V1.
        """
        ...
```

Key changes from V1:
- `create_plan()` → **eliminated**, replaced by `decide_next()`
- `review_step()` → **eliminated**, absorbed into the loop
- `synthesize_results(plan, step_results_dict, room_config)` → **removed in Phase 5**. V2's `synthesize(trajectory, synthesis_instruction)` is the sole synthesis method.
- `_should_review_step()` → **eliminated**, no review gating needed
- `convert_parsed_result_to_plan()` → **eliminated**, no plan to convert
- `MAX_STEPS` moved to `SupervisorExecutor` (see §6.2)

### 6.2 `SupervisorExecutor` (new module)

This is a new module that replaces `QueueExecutor` for supervisor-enabled rooms. `QueueExecutor` continues to serve legacy rooms and fast-path cases unchanged.

```python
class SupervisorExecutor:
    """Executes the Supervisor's adaptive loop for a single user message.

    Responsibilities:
    - Drive the decide → dispatch → record cycle
    - Create RoomAgentMessage records one at a time (no pre-generation)
    - Handle push notification pauses (serialize trajectory for resume)
    - Enforce cancellation, rate limits, and step budget
    - Dispatch concurrent targets via asyncio.gather
    """

    # Hard cap on supervisor decisions per user message.
    # Overridable via SUPERVISOR_MAX_STEPS env var (read at construction time).
    MAX_STEPS: int = int(os.environ.get("SUPERVISOR_MAX_STEPS", "8"))

    def __init__(
        self,
        supervisor_service: RoomSupervisorService,
        room_services: RoomServices,
        tsm: TaskStateManager,
        sse_manager: SSEManager,
        database_service: DatabaseService,
        room_memory_service: RoomMemoryService,
        rate_limit_service: RateLimitService,
        agent_dispatcher: AgentDispatcher,
        agent_message_processor: AgentMessageProcessor,  # shared dispatch logic (§6.4)
        room_coordinator_service: RoomCoordinatorService,  # for synthesis/clarify message emission
    ) -> None:
        # Note: ResponseProcessor is NOT a direct dependency — it lives inside
        # AgentMessageProcessor. NotificationService is also NOT a direct dependency.
        # does not need to call notification_service directly.
        ...

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
        # For resume after push notification pause:
        resumed_trajectory: SupervisorTrajectory | None = None,
        user_message=None,  # cached RoomUserMessage for checkpoint efficiency
    ) -> SupervisorRunResult:
        """Execute the full supervisor loop for a user message.

        Returns SupervisorRunResult with status, trajectory, and optional
        synthesis text.
        """
        trajectory = resumed_trajectory or SupervisorTrajectory()
        step_number = len(trajectory.entries)
        _checkpoint_msg = user_message

        # --- Debate mode resume: skip to DONE if all paused results filled ---
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
                trajectory.status = "completed"
                return self._log_and_return(room_id, trajectory, SupervisorRunResult(
                    status=RunStatus.COMPLETED, trajectory=trajectory
                ), debate_mode=True)

        while step_number < self.MAX_STEPS:

            # --- Cancellation check ---
            if token and token.is_cancelled:
                trajectory.status = "canceled"
                return SupervisorRunResult(
                    status=RunStatus.CANCELED, trajectory=trajectory
                )

            # --- Crash recovery: resume in-flight DELEGATE step ---
            # If the last entry has action=DELEGATE and empty results, the
            # previous server crashed mid-dispatch. Re-use its action.
            inflight_entry: TrajectoryEntry | None = None
            if (
                trajectory.entries
                and trajectory.entries[-1].action.action == ActionType.DELEGATE
                and not trajectory.entries[-1].results
            ):
                inflight_entry = trajectory.entries.pop()
                step_number = len(trajectory.entries)

            # --- Debate mode fast-path (§8.13) ---
            if inflight_entry is not None:
                action = inflight_entry.action
            elif room_config.is_debate_mode and step_number == 0:
                # ... synthetic DELEGATE to all healthy agents (see §8.13)
                ...
            else:
                # --- Ask supervisor for next action (cancellation-aware) ---
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
                    return self._log_and_return(room_id, trajectory, SupervisorRunResult(
                        status=RunStatus.CANCELED, trajectory=trajectory
                    ))
                trajectory.total_supervisor_calls += 1

            # --- Guard: empty targets → convert to DONE ---
            if action.action == ActionType.DELEGATE and not action.targets:
                action = SupervisorAction(
                    action=ActionType.DONE,
                    reasoning="DELEGATE had no targets — treating as DONE",
                )

            # --- Guard: deduplicate identical targets (same agent_id + task) ---
            if action.action == ActionType.DELEGATE and len(action.targets) > 1:
                seen: set[tuple[str, str]] = set()
                deduped: list[DelegateTarget] = []
                for t in action.targets:
                    key = (t.agent_id, t.task)
                    if key not in seen:
                        seen.add(key)
                        deduped.append(t)
                if len(deduped) < len(action.targets):
                    action = SupervisorAction(
                        action=action.action, reasoning=action.reasoning,
                        targets=deduped, synthesis_instruction=action.synthesis_instruction,
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

                    # Pre-dispatch checkpoint: persist entry with empty results
                    # so crash recovery can detect in-flight steps
                    trajectory.entries.append(entry)
                    _checkpoint_msg = await self._checkpoint_trajectory(
                        user_message_id, trajectory,
                        cached_user_message=_checkpoint_msg,
                    )

                    results = await self._dispatch_targets(
                        targets=action.targets,
                        agent_registry=agent_registry,
                        room_id=room_id, user_message_id=user_message_id,
                        step_number=step_number + 1,
                        token=token, request_user_id=request_user_id,
                        quoted_text=quoted_text,
                    )

                    # Store results in room memory (SUCCESS only)
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
                            trajectory=trajectory, paused_results=paused,
                            room_id=room_id, user_message_id=user_message_id,
                            request_user_id=request_user_id,
                            message_text=message_text,
                            agent_registry=agent_registry,
                            room_config=room_config,
                            conversation_context=conversation_context,
                            quoted_text=quoted_text,
                        )
                        return SupervisorRunResult(
                            status=RunStatus.PAUSED, trajectory=trajectory
                        )

                    entry.results = results
                    entry.completed_at = utcnow()

                case ActionType.SYNTHESIZE:
                    # ... (unchanged from §6.2 above, but synthesis LLM call
                    #      is also cancellation-aware via token.race)
                    ...

                case ActionType.CLARIFY:
                    # ... (unchanged)
                    ...

                case ActionType.DONE:
                    # ... (unchanged)
                    ...

            step_number += 1

        # Budget exhausted — force synthesis (cancellation-aware)
        if trajectory.entries:
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
                return self._log_and_return(room_id, trajectory, SupervisorRunResult(
                    status=RunStatus.CANCELED, trajectory=trajectory
                ))
            trajectory.status = "completed"
            return self._log_and_return(room_id, trajectory, SupervisorRunResult(
                status=RunStatus.COMPLETED, trajectory=trajectory, synthesis_text=synthesis,
            ))

        trajectory.status = "failed"
        return SupervisorRunResult(
            status=RunStatus.FAILED, trajectory=trajectory
        )
```

### 6.3 `_dispatch_targets` (concurrent agent dispatch)

```python
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
    """Dispatch one or more agents, concurrently if multiple targets.

    Creates a RoomAgentMessage per target, dispatches via
    AgentMessageProcessor.process_single_message, and returns results.

    Cancellation-aware: when a CancellationToken is provided, dispatch
    races agent work against cancellation. If cancellation fires first,
    already-completed results are still collected (their agent_message_ids
    are needed for cancel_descendants cleanup). Incomplete targets get
    synthetic FAILED results.

    Note on step/total_steps: V2 cannot know total steps upfront (adaptive).
    Agent messages are created with step_number but total_steps=None.
    The frontend should handle None total_steps gracefully (e.g., show
    "Step N" instead of "Step N/M").
    """
    # Validate agent IDs against registry before dispatch
    valid_ids = {a.agent_id for a in agent_registry}
    for target in targets:
        if target.agent_id not in valid_ids:
            logger.warning(
                "Supervisor hallucinated agent_id=%s (valid: %s)",
                target.agent_id, valid_ids,
            )

    async def dispatch_one(target: DelegateTarget, sub_step: int) -> StepResult:
        try:
            # ... (resolve agent, rate limit, create message, dispatch)
            ...
        except asyncio.CancelledError:
            return StepResult(
                step_number=step_number,
                agent_id=target.agent_id, agent_name=target.agent_name,
                task=target.task, response_text="", success=False,
                status=StepStatus.FAILED,
                error_message="Agent dispatch was cancelled",
            )
        except Exception as e:
            return StepResult(
                step_number=step_number,
                agent_id=target.agent_id, agent_name=target.agent_name,
                task=target.task, response_text="", success=False,
                error_message=f"Unexpected error: {e}",
            )

    # Single target: race against cancellation token
    if len(targets) == 1:
        if token:
            work = asyncio.ensure_future(dispatch_one(targets[0]))
            cancel_waiter = token.wait()
            done, _pending = await asyncio.wait(
                {cancel_waiter, work}, return_when=asyncio.FIRST_COMPLETED
            )
            if work in done:
                cancel_waiter.cancel()
                return [work.result()]
            # Cancellation won — try to salvage the result
            work.cancel()
            # ... collect partial result or return synthetic FAILED
        return [await dispatch_one(targets[0])]

    # Multiple targets: race gather against cancellation token
    if not token:
        return list(await asyncio.gather(*(dispatch_one(t, i+1) for i, t in enumerate(targets))))

    tasks = [asyncio.ensure_future(dispatch_one(t)) for t in targets]
    cancel_waiter = token.wait()
    all_work = asyncio.ensure_future(asyncio.gather(*tasks, return_exceptions=True))
    done, _pending = await asyncio.wait(
        {cancel_waiter, all_work}, return_when=asyncio.FIRST_COMPLETED
    )
    if all_work in done:
        cancel_waiter.cancel()
        return [r if isinstance(r, V2StepResult) else ... for r in all_work.result()]
    # Cancellation fired — collect completed results, synthesize FAILED for the rest
    all_work.cancel()
    results = []
    completed_ids = set()
    for task in tasks:
        if task.done() and not task.cancelled():
            r = task.result()
            results.append(r)
            completed_ids.add(r.agent_id)
    for t in targets:
        if t.agent_id not in completed_ids:
            results.append(StepResult(
                step_number=step_number, agent_id=t.agent_id,
                agent_name=t.agent_name, task=t.task, response_text="",
                success=False, status=StepStatus.FAILED,
                error_message="Agent dispatch was cancelled",
            ))
    return results
```

### 6.4 Shared Agent Dispatch: `AgentMessageProcessor` (extracted)

`_process_single_message` currently lives on `QueueExecutor` (lines 579-713). Both `QueueExecutor` and `SupervisorExecutor` need it. Rather than duplicating or subclassing, extract it into a shared module.

```python
# modules/AgentMessageProcessor.py

class AgentMessageProcessor:
    """Shared single-message dispatch logic used by both QueueExecutor and SupervisorExecutor.

    Extracted from QueueExecutor._process_single_message to avoid duplication.
    Contains zero orchestration logic — only the mechanics of:
    1. Building the A2A message via room_services.process_agent_message
    2. Choosing streaming vs sync dispatch
    3. Handling PAUSED (push notification) results
    4. Returning a ProcessingResult
    """

    def __init__(
        self,
        tsm: TaskStateManager,
        sse_manager: SSEManager,
        response_processor: ResponseProcessor,
        a2a_service: A2AService,
        room_services: RoomServices,
        database_service: DatabaseService,
    ) -> None:
        ...

    async def process_single_message(
        self,
        current_message: RoomAgentMessage,
        room_id: str,
        agent: Agent,
        user_message_id: str,
        *,
        token: CancellationToken | None = None,
        step_number: int | None = None,
        total_steps: int | None = None,
        quoted_text: str | None = None,
    ) -> ProcessingResult:
        """Process a single agent message. Identical to the current
        QueueExecutor._process_single_message implementation.
        """
        ...
```

**Migration**: In Phase 1, create `AgentMessageProcessor` by moving the body of `QueueExecutor._process_single_message` into it. Then update `QueueExecutor._process_single_message` to delegate to it:

```python
# In QueueExecutor (backward-compatible wrapper)
async def _process_single_message(self, ...):
    return await self._agent_message_processor.process_single_message(...)
```

`SupervisorExecutor` takes `AgentMessageProcessor` as a constructor dependency and calls it directly.

### 6.5 Agent Resolution in `_dispatch_targets`

The current `AgentDispatcher` only has `assign_agent_for_queue(message: RoomAgentMessage)`, which takes a full message object. V2's `_dispatch_targets` needs to resolve an agent by `agent_id` directly. Add a lightweight method:

```python
# In AgentDispatcher (new method)
async def resolve_agent(self, agent_id: str, room_id: str) -> Agent | None:
    """Resolve an agent by ID. Returns None if not found or inactive.

    Unlike assign_agent_for_queue, this does NOT attempt re-assignment.
    The supervisor handles failures via the next decide_next iteration.
    """
    agent = await self.database_service.get_agent_by_agent_id(agent_id)
    if agent is None:
        return None
    if agent.agent_status != AgentStatus.active:
        logger.warning(
            "AgentDispatcher: Agent %s is %s, returning None",
            agent_id, agent.agent_status,
        )
        return None
    return agent
```

`_dispatch_targets` calls `self.agent_dispatcher.resolve_agent(target.agent_id, room_id)`. If it returns `None`, the dispatch returns a `StepResult(success=False, error_message="Agent not found or inactive")`. The supervisor sees this failure in the next trajectory and can decide to delegate to a different agent.

### 6.6 Result Types

```python
class RunStatus(StrEnum):
    COMPLETED = "completed"     # All done, optional synthesis_text
    FAILED = "failed"           # Unrecoverable error
    CANCELED = "canceled"       # User canceled
    PAUSED = "paused"           # Push notification agent — waiting for webhook
    CLARIFYING = "clarifying"   # Asked user a question — waiting for response


class SupervisorRunResult(BaseModel):
    status: RunStatus
    trajectory: SupervisorTrajectory
    synthesis_text: str | None = None
    clarification_question: str | None = None
```

---

## 7. Integration Points

### 7.0 The `supervisor_v2` Flag

V2 is controlled by a **room-level flag** on the `Room` model:

```python
# In room.extend_info (dict stored in MongoDB)
{
    "use_supervisor": True,       # V1 flag — already exists
    "supervisor_v2": True,        # NEW — enables V2 adaptive loop
}
```

**How it's set**: Per-room toggle, set via the room settings API (same admin UI that sets `use_supervisor`). During migration, rooms can be individually upgraded to V2.

**How it's checked**:
- `send_message_to_room` reads `room.extend_info.get("use_supervisor", False)` to decide the V2 path.
- `process_room_user_message` reads `user_message.extend_info.get("supervisor_v2", False)` (set by `send_message_to_room`).

**Phase 5 (completed)**: The room-level `supervisor_v2` toggle has been removed. All rooms with `use_supervisor = True` use V2 exclusively. The `supervisor_v2` key on user message `extend_info` persists as an internal data-readiness marker (confirms that `agent_registry`, `room_config`, etc. were prepared), not as a room configuration flag. A safety guard in `process_room_user_message` prevents `use_supervisor` rooms from silently falling through to `QueueExecutor` if this marker is absent.

### 7.1 `RoomServices.send_message_to_room` — V2 Preparation Path

For V2, `send_message_to_room` replaces the `_parse_with_supervisor` call with a lightweight preparation step. A new method `_prepare_for_supervisor_v2` handles this:

```python
async def _prepare_for_supervisor_v2(
    self,
    room: Room,
    user_message: RoomUserMessage,
    message_text: str,
) -> None:
    """Prepare extend_info for V2 supervisor execution.

    Unlike _parse_with_supervisor (V1), this method:
    - Does NOT call the supervisor LLM
    - Does NOT create any RoomAgentMessage records
    - Does NOT build a SupervisorPlan
    - ONLY stores the data needed for SupervisorExecutor.run()
    """
    # 1. Build agent_registry from the room's agents
    room_agents = await self.database_service.get_agents_by_room_id(room.room_id)
    agent_registry = [
        AgentProfile.from_agent(agent)
        for agent in room_agents
        if agent.agent_status == AgentStatus.active
    ]

    # 2. Build RoomConfig
    room_config = RoomConfig(
        room_id=room.room_id,
        is_debate_mode=room.extend_info.get("is_debate_mode", False),
        # ... other room config fields
    )

    # 3. Build conversation_context for the supervisor LLM prompt
    room_memory = await self.room_memory_service.get_context_for_agent(
        room_id=room.room_id,
        agent_id=None,  # supervisor context, not agent-specific
    )
    conversation_context = room_memory if room_memory else None

    # 4. Store in extend_info — this is what RoomMessageCenter reads
    user_message.extend_info = {
        **(user_message.extend_info or {}),
        "supervisor_v2": True,
        "agent_registry": [p.model_dump(mode="json") for p in agent_registry],
        "room_config": room_config.model_dump(mode="json"),
        "conversation_context": conversation_context,
    }
    await self.database_service.update_room_user_message(user_message)
```

**Flow comparison**:

```
BEFORE (V1):
  send_message_to_room()
    ├── Direct chat? → skip, create 1 agent message
    ├── @mention? → deterministic fan-out
    └── Multi-agent → _parse_with_supervisor()
                    → supervisor_service.create_plan()
                    → _generate_agent_messages_from_plan()
                    → store plan in user_message.extend_info

AFTER (V2):
  send_message_to_room()
    ├── Direct chat? → skip, create 1 agent message         [UNCHANGED]
    ├── @mention? → deterministic fan-out                    [UNCHANGED]
    └── Multi-agent + supervisor_v2?
          YES → _prepare_for_supervisor_v2()                 [NEW, lightweight, no LLM call]
          NO  → _parse_with_supervisor()                     [V1, unchanged]
```

Agent messages are now created one at a time inside `SupervisorExecutor._dispatch_targets`, not pre-generated during planning.

### 7.2 `RoomMessageCenter` — Entry Point (V2 Branch)

`process_room_user_message` gains a concrete V2 branch. Here is the pseudocode against the real method structure:

```python
async def process_room_user_message(self, user_message: RoomUserMessage) -> None:
    room_id = user_message.room_id

    # --- V2 branch ---
    if user_message.extend_info.get("supervisor_v2"):
        # Deserialize from extend_info (set by send_message_to_room)
        agent_registry = [
            AgentProfile(**p) for p in user_message.extend_info["agent_registry"]
        ]
        room_config = RoomConfig(**user_message.extend_info["room_config"])
        conversation_context = user_message.extend_info.get("conversation_context")

        # Skip inquiry_agent_messages_by_related_message_id entirely —
        # V2 has no pre-generated agent messages.

        result = await self.supervisor_executor.run(
            room_id=room_id,
            user_message_id=user_message.message_id,
            message_text=user_message.content,
            agent_registry=agent_registry,
            room_config=room_config,
            conversation_context=conversation_context,
            token=self._get_cancellation_token(room_id),
            request_user_id=user_message.user_id,
            quoted_text=user_message.extend_info.get("quoted_text"),
        )

        # Handle all 5 RunStatus variants
        match result.status:
            case RunStatus.COMPLETED:
                # Store trajectory for auditability
                user_message.extend_info["supervisor_trajectory"] = (
                    result.trajectory.model_dump(mode="json")
                )
                await self.database_service.update_room_user_message(user_message)

                if result.synthesis_text:
                    await self.room_coordinator_service.emit_synthesis_message(
                        room_id=room_id,
                        synthesis_text=result.synthesis_text,
                        related_message_id=user_message.message_id,
                    )
                await self.sse_manager.send_status(
                    room_id, SSEProcessingStatus.COMPLETED
                )

            case RunStatus.PAUSED:
                # Push notification pause — state already saved by _save_pause_state
                pass  # No SSE event; webhook resume will continue

            case RunStatus.CLARIFYING:
                user_message.extend_info["supervisor_trajectory"] = (
                    result.trajectory.model_dump(mode="json")
                )
                await self.database_service.update_room_user_message(user_message)

                # Set room-level flag for clarification resume
                room = await self.database_service.get_room_by_id(room_id)
                room.extend_info["pending_clarification_message_id"] = (
                    user_message.message_id
                )
                await self.database_service.update_room(room)

                # Emit clarification as pseudo-agent message
                await self.room_coordinator_service.emit_clarification_message(
                    room_id=room_id,
                    question=result.clarification_question,
                    related_message_id=user_message.message_id,
                )
                await self.sse_manager.send_status(
                    room_id, SSEProcessingStatus.COMPLETED
                )

            case RunStatus.CANCELED:
                await self.sse_manager.send_status(
                    room_id, SSEProcessingStatus.CANCELED
                )

            case RunStatus.FAILED:
                await self.sse_manager.send_status(
                    room_id, SSEProcessingStatus.FAILED
                )

        return  # V2 path complete

    # --- V1 / legacy path (unchanged) ---
    # Extract supervisor_plan from user_message.extend_info
    # Build message_queue from inquiry_agent_messages_by_related_message_id
    # Pass to QueueExecutor.process_queue()
    ...
```

**Key differences from V1 path**:
1. **No `inquiry_agent_messages_by_related_message_id`** — V2 has no pre-generated agent messages to query.
2. **`agent_registry` and `room_config` come from `extend_info`** — not from a `SupervisorPlan`.
3. **`conversation_context` comes from `extend_info`** — built by `send_message_to_room._prepare_for_supervisor_v2`, using `RoomMemoryService.get_context_for_agent`. This is the context for the **supervisor LLM prompt**, not the per-agent context (which is handled separately inside `_process_single_message`).
4. **All 5 `RunStatus` variants are handled** — V1 only had COMPLETED.

### 7.3 Push Notification Pause/Resume

When `_dispatch_targets` encounters a PAUSED result (push notification agent), the executor serializes the full `SupervisorTrajectory` plus the inputs needed to resume:

```python
pause_state = {
    "supervisor_v2": True,
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
```

`_save_pause_state` returns `True` if at least one pause state was saved successfully, `False` if all saves failed (webhook resume will not work).

On webhook resume, `RoomMessageCenter` detects `supervisor_v2: True` in the continuation data, reconstructs the trajectory with the push notification result appended, and calls `SupervisorExecutor.run(..., resumed_trajectory=trajectory)`. The loop picks up exactly where it left off.

**Stale agent registry on resume**: The serialized `agent_registry` in `pause_state` may be hours or days old by the time the webhook arrives. Agent health, availability, or configuration may have changed. The resume path **must refresh the agent registry from the database** rather than using the serialized version:

```python
# In resume_queue_from_continuation (when supervisor_v2 is True):
# Deserialize pause_state
trajectory = SupervisorTrajectory(**pause_state["trajectory"])
room_id = pause_state["room_id"]

# Refresh agent registry from current database state (not serialized)
room_agents_items = list((room.room_agent_set or {}).items())
if room_agents_items:
    agents = await asyncio.gather(
        *(database_service.get_agent_by_agent_id(aid) for aid, _ in room_agents_items)
    )
    agent_registry = []
    for (aid, aname), agent in zip(room_agents_items, agents, strict=True):
        if agent:
            agent_registry.append(AgentProfile.from_agent(agent))
        else:
            agent_registry.append(AgentProfile(
                agent_id=aid, agent_name=aname, description="", is_healthy=False,
            ))

# Resume with fresh registry
result = await supervisor_executor.run(
    ...,
    agent_registry=agent_registry,  # fresh, not from pause_state
    resumed_trajectory=trajectory,
)
```

The serialized `agent_registry` is kept in `pause_state` as a fallback if the database query fails, but the primary path always refreshes.

### 7.4 Clarify Action — User Interaction Pause

When the supervisor returns `CLARIFY`, the executor:

1. Emits a clarification message to the room as a pseudo-agent message (similar to synthesis messages).
2. Returns `RunStatus.CLARIFYING` to `RoomMessageCenter`.
3. `RoomMessageCenter` sends `SSEProcessingStatus.COMPLETED` (from the user's perspective, processing is done — they see a question).
4. The trajectory is stored in `user_message.extend_info.supervisor_trajectory`.
5. A **room-level flag** `room.extend_info.pending_clarification_message_id` is set to the `user_message_id` of the message that triggered the clarification. This is how `send_message_to_room` knows a clarification is in progress.

When the user replies, `send_message_to_room`:
1. Checks `room.extend_info.pending_clarification_message_id`.
2. If set, loads the original user message's `extend_info.supervisor_trajectory`.
3. Appends the user's reply to the conversation context.
4. Clears `pending_clarification_message_id` on the room.
5. Calls `SupervisorExecutor.run(..., resumed_trajectory=trajectory)` to continue the loop.

This room-level flag approach avoids scanning message history and works correctly even if the user takes time to respond or sends multiple messages.

### 7.5 Inter-Agent Context (Room Awareness)

Unchanged from V1. `_build_room_awareness` and `build_context_for_agent` inject the `[Room Context]` block. The difference is that in V2, the Supervisor itself handles context passing between agents by including prior results in each `DelegateTarget.task` — the `context_from_steps` field is no longer needed.

### 7.6 Synthesis and Result Emission

`RoomMessageCenter` handles each `RunStatus` as follows:

| `RunStatus` | `synthesis_text` | Action |
|---|---|---|
| `COMPLETED` | present | Call `room_coordinator_service.emit_synthesis_message(synthesis_text)` then send `SSEProcessingStatus.COMPLETED` |
| `COMPLETED` | `None` (DONE) | No synthesis message. The individual agent message(s) already streamed are the response. Send `SSEProcessingStatus.COMPLETED`. |
| `PAUSED` | — | Save pause state. Send nothing to the user yet (push notification will resume). |
| `CLARIFYING` | — | Emit clarification question as a pseudo-agent message. Send `SSEProcessingStatus.COMPLETED`. |
| `CANCELED` | — | Cancel all in-flight agent messages (via `cancel_agent_messages_by_ids` and `cancel_descendants`). Send `SSEProcessingStatus.CANCELED`. Clear cancellation token. |
| `FAILED` | — | Cancel all in-flight agent messages (same cleanup as CANCELED). Send `SSEProcessingStatus.FAILED`. |

**Cancellation token cleanup**: For all terminal statuses except PAUSED, `_handle_v2_run_result` calls `sse_manager.remove_token(user_message_id)` to prevent stale tokens from accumulating. PAUSED runs keep their token alive for the webhook resume path.

**DONE with multiple agent results**: If the supervisor returns `DONE` after 2+ agents have responded, no synthesis is emitted. The individual agent responses are already visible to the user. This is intentional — the supervisor should only use DONE when it judges that no synthesis is needed (e.g., the agents answered different parts of the question and each response stands on its own). The prompt rules discourage this; the prompt instructs the LLM to use SYNTHESIZE when 2+ agents have responded.

---

## 8. Edge Cases

### 8.1 Direct Chat Fast-Path

Unchanged. Single agent + no debate → skip supervisor entirely. Zero LLM overhead.

### 8.2 @Mention Routing

Unchanged. Deterministic fan-out bypasses the supervisor.

### 8.3 Supervisor LLM Failure

If `decide_next` fails (API error, timeout, malformed JSON), the service returns `ActionType.DONE` as a fail-open default. If the failure happens on the first iteration (no agents have been called yet), a `SupervisorPlanningError` is raised. Since Phase 5 removed V1, this error is caught by `RoomMessageCenter._process_supervisor_v2` which emits a user-facing error synthesis message and returns `FAILED` — there is no longer a legacy `QueueExecutor` fallback.

Both `decide_next` and `synthesize_v2` LLM calls have a **30-second timeout** (`timeout=30.0` on the OpenAI API call) to prevent indefinite hangs from upstream API issues.

```python
class SupervisorPlanningError(Exception):
    """Raised by decide_next when the very first supervisor call fails.

    Caught by RoomMessageCenter to emit an error message and return FAILED.
    Not raised on subsequent iterations — those fail open with DONE.
    """


async def decide_next(self, ...) -> SupervisorAction:
    try:
        response = await self._call_supervisor_llm(...)
        return self._parse_action(response)
    except Exception as e:
        logger.warning("Supervisor decide_next failed: %s", e)
        if not trajectory.entries:
            raise SupervisorPlanningError(str(e))  # caller returns FAILED
        # If 2+ agents have succeeded, synthesize rather than silently stopping
        completed_results = [r for e in trajectory.entries for r in e.results if r.success and r.status == StepStatus.SUCCESS]
        if len(completed_results) >= 2:
            return SupervisorAction(
                action=ActionType.SYNTHESIZE,
                reasoning=f"Supervisor failed ({e}), synthesizing available results",
                synthesis_instruction="The supervisor encountered an error. Synthesize the available agent results into a coherent response.",
            )
        return SupervisorAction(
            action=ActionType.DONE,
            reasoning=f"Supervisor failed ({e}), stopping with current results",
        )
```

### 8.4 Budget Exhaustion

`MAX_STEPS = 8` hard cap. If reached, the executor forces a synthesis from whatever results have been collected. The budget prevents runaway LLM costs from adversarial inputs or confused models. The value is configurable per environment via `SUPERVISOR_MAX_STEPS` env var.

### 8.5 Cancellation

Cancellation is enforced at three levels:

1. **Loop iteration check**: At the top of every `while` iteration, `token.is_cancelled` is checked synchronously. If signaled, the executor returns `CANCELED` immediately.

2. **LLM call racing**: `decide_next` and `synthesize_v2` calls are raced against the cancellation token via `token.race(coro)`. If cancellation fires during an LLM call, the call is abandoned and the executor returns `CANCELED` without waiting for the response.

3. **Agent dispatch racing**: `_dispatch_targets` races each agent dispatch (or the `asyncio.gather` for multi-target) against `token.wait()` using `asyncio.wait(FIRST_COMPLETED)`. When cancellation wins the race, already-completed agent results are still collected — their `agent_message_id` values are needed for `cancel_descendants` cleanup in `_handle_v2_run_result`. Incomplete targets get synthetic `StepResult(success=False, status=FAILED)` entries.

`CancellationToken.wait()` (new method) returns a future that resolves when cancellation is signaled, allowing it to be used with `asyncio.wait` without accessing the internal `_event` directly.

### 8.6 Rate Limiting

Checked per-agent before dispatch in `_dispatch_targets`. If rate-limited, the `StepResult` is marked `success=False` with `error_message="Rate limited"`. The supervisor sees this in the next iteration's trajectory and can decide to delegate to a different agent or stop.

### 8.7 Agent ID Validation

Unlike V1 (which trusted the LLM's agent IDs without validation), V2 validates each `DelegateTarget.agent_id` against the `agent_registry` before dispatching:

```python
valid_ids = {a.agent_id for a in agent_registry}
for target in action.targets:
    if target.agent_id not in valid_ids:
        logger.warning("Supervisor hallucinated agent_id: %s", target.agent_id)
        # Return error result for this target — supervisor sees it next iteration
```

### 8.8 Concurrent Dispatch Partial Failure

When dispatching multiple targets concurrently, some may succeed and others fail. The executor collects all results (including failures) and feeds them to the supervisor in the next iteration. The supervisor decides whether to retry the failed agents or proceed. The `try/except` wrapper in `dispatch_one` ensures exceptions never propagate to `asyncio.gather` — every coroutine returns a `StepResult`, possibly with `success=False`.

### 8.9 Concurrent Dispatch — SSE Event Ordering

When multiple agents execute concurrently (multi-target DELEGATE), each agent streams its own SSE events. The `SSEManager` must tolerate interleaved events from different `agent_message_id` values within the same room stream. The frontend already handles this: it assigns each message its own rendering slot keyed by `agent_message_id`, so interleaved chunks render correctly in parallel.

**Assumption**: `_process_single_message` / `ResponseProcessor` already tags each SSE event with its `agent_message_id`. If this is not the case, concurrent dispatch will corrupt the frontend stream and must be gated until the SSE tagging is confirmed.

### 8.10 Concurrent Room Memory Writes

`_dispatch_targets` runs multiple agents concurrently via `asyncio.gather`. After all return, the `run()` loop writes each result to room memory sequentially (the `for result in results` loop). Because the writes happen **after** `gather` completes (not during concurrent execution), there is no race condition **within a single `run()` call**.

However, `RoomMemoryService.add_agent_response_to_memory` performs a non-atomic read-modify-write cycle:

```python
room_memory = await self.database_service.get_room_memory_by_room_id(room_id)  # READ
room_memory.memory_content = add_turn_to_history(...)                           # MODIFY
await self.database_service.update_room_memory_by_room_id(room_id, room_memory) # WRITE
```

If an **external writer** (e.g., another user message in the same room, a different WebSocket handler) writes to room memory concurrently with V2's sequential writes, the last writer wins and earlier writes are lost. The V1 `QueueExecutor` has the same pattern (it processes sequentially, so it can't race with itself, but it can race with external writers).

**Mitigation (Phase 2)**:
1. **Short-term**: Accept the risk. Room-level write contention is rare in practice because `process_room_user_message` holds a per-room async lock (the `room_processing_lock` in `RoomMessageCenter`). As long as only one user message is processed per room at a time, the sequential writes in `run()` are safe.
2. **Medium-term**: If contention is observed, wrap the memory writes in a retry-on-conflict loop: re-read before each write, or use MongoDB's `$push` operator to append atomically instead of read-modify-write.

### 8.11 DONE After Multiple Agent Responses

The supervisor can return `DONE` after 2+ agents have responded, skipping synthesis. In V1, synthesis was always forced for 2+ agent results. V2 relies on the LLM to choose `SYNTHESIZE` vs `DONE` correctly.

**Risk**: A confused supervisor model returns `DONE` when `SYNTHESIZE` was intended, leaving the user with unsynthesized parallel responses and no summary.

**Mitigations already in place**:
- The system prompt explicitly instructs: "Only use DONE for single-agent results."
- §7.6 documents that DONE with multiple results is intentionally supported for cases where each agent answered an independent question.

**If this becomes a problem in production**: Add a guard in `run()` that forces SYNTHESIZE if `action == DONE` and `len(completed_results) >= 2`. This is a safety override of the supervisor's choice. The tradeoff is losing the intentional "two independent answers" case, which is probably rare.

### 8.12 Local Optimization Trap (Reactive Greedy Decisions)

Academic benchmarks and industry experience (notably LangChain's own research blog) have identified a known failure mode of reactive/ReAct-style supervisors: the LLM makes each decision based on the current trajectory without considering the global optimum. The result can be a sequence of individually-reasonable decisions that leads to a worse outcome than an upfront plan would have produced.

**Example**: The user asks for a travel itinerary covering flights, hotels, and local activities.
- Iteration 1: Supervisor delegates to Flight Agent → gets flight options.
- Iteration 2: Supervisor delegates to Hotel Agent with flight context → gets hotel options.
- Iteration 3: Supervisor sees two results and decides to SYNTHESIZE.
- The Activities Agent is never called because the supervisor didn't "plan ahead" for it.

An upfront planner would have recognized three parallel sub-tasks immediately.

**Mitigations built into V2**:
1. The system prompt instructs: "Include relevant context from prior results when the agent needs it" — this nudges the supervisor to look ahead.
2. The supervisor sees the full user message at every iteration, not just the last result — it can re-read the original intent.
3. The `MAX_STEPS` budget is generous (8 steps) so the supervisor has room to course-correct.

**If this becomes a pattern**: Add an optional `planning_hint` field to `SupervisorAction` that the supervisor populates on the first iteration to declare intended future steps. This is not enforced but gives the LLM a mechanism to "commit" its high-level plan, reducing greedy local decisions on subsequent iterations. This is the hybrid ReAct+Plan approach emerging in recent research.

### 8.13 Debate Mode Fast-Path

When `room_config.is_debate_mode` is true, `SupervisorExecutor.run()` bypasses the supervisor LLM on the first iteration and constructs a synthetic `DELEGATE` action targeting all healthy agents:

```python
# At the top of the run() while loop, before calling decide_next:
if room_config.is_debate_mode and step_number == 0:
    healthy_agents = [a for a in agent_registry if a.is_healthy]
    action = SupervisorAction(
        action=ActionType.DELEGATE,
        reasoning="Debate mode: delegating to all agents concurrently",
        targets=[
            DelegateTarget(
                agent_id=a.agent_id,
                agent_name=a.agent_name,
                task=message_text,  # same task for all
            )
            for a in healthy_agents
        ],
    )
    # Skip decide_next — use synthetic action directly
else:
    action = await self.supervisor_service.decide_next(...)
```

After all agents respond in debate mode, the executor returns `DONE` (no synthesis). This replaces V1's `DebateService.inject_short_debate_for_agent_message()` which modified agent message content inside `_queue_next_messages`. Since V2 eliminates `_queue_next_messages`, the debate injection is moved to the executor level.

**Cost**: 0 supervisor LLM calls for debate mode (vs. at least 1 `decide_next` if we relied on prompt instructions alone).

### 8.14 Crash Recovery

When the server process restarts while `SupervisorExecutor.run()` is mid-loop, per-step checkpointing and a background recovery job ensure the execution is resumed rather than silently lost.

**Checkpointing** (`_checkpoint_trajectory`):
After each DELEGATE `TrajectoryEntry` is created (but before dispatch begins), the trajectory is persisted to `user_message.extend_info.supervisor_trajectory` with `status="running"`. The entry has empty `results` at this point — this is the marker for in-flight recovery. Checkpointing is best-effort: failures are logged but don't abort the loop.

```python
async def _checkpoint_trajectory(
    self,
    user_message_id: str,
    trajectory: SupervisorTrajectory,
    cached_user_message=None,
):
    """Persist trajectory snapshot to user message after each step.

    Returns the user message object so callers can cache it across steps.
    """
    user_message = cached_user_message or await self.database_service.get_room_user_message_by_message_id(...)
    if user_message:
        user_message.extend_info["supervisor_trajectory"] = trajectory.model_dump(mode="json")
        await self.database_service.update_room_user_message_by_message_id(...)
    return user_message
```

**Recovery job** (`StaleTaskChecker._recover_stuck_supervisor_trajectories`):
Runs as part of the existing `StaleTaskChecker` periodic check cycle. Scans MongoDB for user messages where:
- `extend_info.supervisor_trajectory.status == "running"`
- `extend_info.supervisor_v2 == True`
- `message_created_at < now - orphan_threshold_minutes`

The age threshold prevents racing with actively-running trajectories.

**Atomic claim** (`claim_stuck_supervisor_trajectory`):
Uses MongoDB `find_one_and_update` with a status precondition (`"running"` → `"recovering"`) so only one recovery worker (even across multiple server instances) can claim a given stuck trajectory. Workers that lose the race see `False` and skip.

**Cancellation awareness**: Before re-triggering, the recovery job checks `is_message_cancelled` to skip messages the user canceled during the crash window (the in-memory cancellation token was lost, but the DB record survives).

**In-flight step recovery in `run()`**: When the executor detects the last trajectory entry has `action=DELEGATE` and empty `results`, it pops the entry and re-uses its `SupervisorAction` instead of calling `decide_next`. This avoids creating duplicate agent dispatches.

**Failed trajectory persistence**: When `_process_supervisor_v2` catches exceptions (`SupervisorPlanningError` or general exceptions), it calls `_persist_failed_trajectory` to mark the trajectory as `"failed"` in the database. This prevents the recovery job from endlessly retrying a permanently-broken execution.

**Files changed**:

| File | Change |
|---|---|
| `modules/SupervisorExecutor.py` | `_checkpoint_trajectory` method; in-flight recovery logic in `run()` |
| `modules/RoomMessageCenter.py` | Crash-recovery trajectory deserialization; `_persist_failed_trajectory` helper |
| `jobs/stale_task_checker.py` | `_recover_stuck_supervisor_trajectories`, `_process_recovered_supervisor_message` |
| `database/mongodb.py` | `claim_stuck_supervisor_trajectory` (atomic claim with `find_one_and_update`) |
| `services/database_service.py` | `claim_stuck_supervisor_trajectory`, `is_message_cancelled` wrappers |
| `common/utils/cancellation.py` | `CancellationToken.wait()` method |

---

## 9. Cost Analysis

### LLM Calls Per Request

| Scenario | V1 (Plan-Execute-Review) | V2 (Adaptive Loop) |
|---|---|---|
| Direct chat (1 agent) | 0 | 0 (fast-path, unchanged) |
| @mention | 0 | 0 (bypassed, unchanged) |
| Multi-agent (2 agents, independent) | 1 plan + 0-1 review + 1 synthesis = 2-3 | 1 decide + 1 decide + 1 decide(synthesize) + 1 synthesis = 3-4 |
| Multi-agent (2 agents, sequential) | 1 plan + 0-1 review + 1 synthesis = 2-3 | 1 decide + 1 decide + 1 decide(synthesize) + 1 synthesis = 3-4 |
| Multi-agent (3 agents, mixed) | 1 plan + 0-2 review + 1 synthesis = 2-4 | 1-3 decide + 1 synthesis = 2-4 |

**Important caveat on the V1 numbers**: V1's review is gated by the `context_from_steps` check (`_should_review_step`). Because `context_from_steps` was never consumed at execution time (§14.1 of V1), the supervisor rarely populated it in practice. In the field, V1 effectively ran at **1 plan + 0 reviews + 1 synthesis = 2 calls** for most requests. V2's common-case count of **3-4 calls** is therefore a genuine regression of ~1-2 LLM calls vs. the broken-but-faster V1.

V2 makes ~1 more LLM call for the common 2-agent case. But each call is smaller (single action output vs. full plan schema), and the model can be smaller/cheaper (e.g., `gpt-4o-mini` with ~200 output tokens per call).

### Per-Call Token Budget

| Phase | V1 Tokens (est.) | V2 Tokens (est.) |
|---|---|---|
| Plan creation | ~800 input + ~300 output | N/A |
| decide_next (iteration 1) | N/A | ~600 input + ~100 output |
| decide_next (iteration 2+) | N/A | ~800 input + ~100 output |
| Review | ~500 input + ~100 output | N/A |
| Synthesis | ~600 input + ~300 output | ~600 input + ~300 output |

Net cost difference: ~$0.0001–0.0003 more per multi-agent request at gpt-4o-mini pricing. Negligible.

### Latency

Each `decide_next` call adds ~300-500ms. For a 2-agent sequential task:
- V1: 500ms (plan) + 0-500ms (review) + agent latency + 500ms (synthesis)
- V2: 400ms (decide) + agent latency + 400ms (decide) + agent latency + 400ms (decide→synthesize) + 500ms (synthesis)

V2 adds ~400ms between agents (the decide call). In exchange, the supervisor sees the actual result before routing the next agent, enabling adaptation. For parallel dispatch, the decide calls are the same but agent latency is concurrent.

---

## 10. What Changes vs. V1

| Component | V1 Status | V2 Status |
|---|---|---|
| `RoomSupervisorService` | Plan + Review + Synthesize | `decide_next` + `synthesize` only |
| `SupervisorPlan` / `SupervisorStep` | Core data model | Eliminated |
| `SupervisorReview` / `ReviewAction` | Post-step bolt-on | Eliminated |
| `QueueExecutor.process_queue` | Drives execution for supervisor rooms | Only used for legacy / fast-path rooms |
| `SupervisorExecutor` | N/A | New module — drives the adaptive loop |
| `_generate_agent_messages_from_plan` | Pre-generates all messages | Eliminated (messages created one at a time) |
| `_parse_with_supervisor` | Creates plan + messages in send_message_to_room | Reduced to storing agent_registry in extend_info |
| `_supervisor_review_step` | Complex review hook in queue loop | Eliminated |
| `_handle_revise_action` / `_handle_retry_action` | Queue mutation logic | Eliminated |
| `_find_step_for_message` | Position-index matching (buggy) | Eliminated |
| `_should_review_step` / `should_review_step` | Review gating heuristic | Eliminated |
| `context_from_steps` | Declared but never consumed | Eliminated (supervisor injects context in task text) |

## 11. What Does NOT Change

| Component | Status |
|---|---|
| `Room` model | Unchanged |
| `RoomAgentMessage` model | Unchanged (created one at a time instead of batch) |
| `RoomUserMessage` model | Unchanged (trajectory stored in extend_info) |
| `_process_single_message` / `ResponseProcessor` | Unchanged (core agent dispatch) |
| SSE streaming to frontend | Unchanged |
| Push notification / webhook flow | Unchanged (trajectory serialized in continuation) |
| `a2a_service` (agent communication) | Unchanged |
| `rate_limit_service` | Unchanged |
| `stale_task_checker` | Extended: new `_recover_stuck_supervisor_trajectories` method scans for V2 trajectories stuck in `"running"` status and re-triggers them (see §8.14) |
| Frontend (`useRoomWebhook.ts`) | Unchanged |
| Room memory (`RoomMemoryService`) | Unchanged |
| `build_context_for_agent` / `_build_room_awareness` | Unchanged |
| `AgentProfile.from_agent` | Unchanged |
| `room.extend_info.use_supervisor` flag | Reused (V2 is the new supervisor implementation) |
| Direct chat fast-path | Unchanged |
| @mention routing | Unchanged |

---

## 12. V1 Issues Resolved by V2

| V1 Issue (§14) | How V2 Resolves It |
|---|---|
| 14.1 `context_from_steps` never consumed | Eliminated. Supervisor injects prior results into `DelegateTarget.task` directly — it has the full trajectory. |
| 14.2 Legacy fallback produces no plan | No plan to produce. V2-only path returns FAILED on `SupervisorPlanningError`. |
| 14.3 Dead `should_review_step` method | No review mechanism at all. Every iteration is an implicit review. |
| 14.4 `supervisor_reviews` not persisted | Trajectory entries record every decision. The full trajectory is persisted in `user_message.extend_info`. |
| 14.5 Logging missing `room_id` | `SupervisorExecutor.run` has `room_id` in scope for all logging. |
| 14.6 RETRY duplicates StepResult | No RETRY action. Supervisor just delegates again — a new trajectory entry with a new result. |
| 14.7 REVISE cancels without notifying frontend | No REVISE action. No messages are pre-generated, so nothing to cancel. |
| 14.8 `_find_step_for_message` position mismatch | No step-to-message matching needed. Messages are created and dispatched atomically. |
| 14.9 REVISE/RETRY conflict with `_queue_next_messages` | No queue. No `_queue_next_messages`. The supervisor loop is the only execution driver. |
| Arch-2: `strategy` not enforced | No strategy label. "parallel" = multi-target delegate. "clarify" = CLARIFY action. Behavior is the action, not a label. |
| Arch-5: Agent ID not validated | `_dispatch_targets` validates against `agent_registry` before dispatch. |
| Arch-6: Dual state (plan vs messages) | Single source of truth: the trajectory. Messages are a side effect of dispatch, not a pre-built plan. |

---

## 13. Migration Plan

### Phase 1: Build `SupervisorExecutor` (non-breaking) — COMPLETED

**Completed**: February 21, 2026

**Prerequisites**: Resolve #2 (extract `_process_single_message` into `AgentMessageProcessor`) and #1 (add `AgentDispatcher.resolve_agent`).

1. ~~Extract `QueueExecutor._process_single_message` into `modules/AgentMessageProcessor.py` (§6.4). Update `QueueExecutor._process_single_message` to delegate to it (backward-compatible wrapper).~~ **Done** — `AgentMessageProcessor` created; `QueueExecutor._process_single_message` delegates to it when injected, with inline fallback for backward compatibility. `RoomMessageCenter` constructs and injects `AgentMessageProcessor` into both.
2. ~~Add `AgentDispatcher.resolve_agent()` method (§6.5).~~ **Done** — lightweight resolve-by-ID method added, returns `None` for inactive/missing agents.
3. ~~Create `models/supervisor_v2.py` with `V2StepResult`, `StepStatus`, `SupervisorAction`, `DelegateTarget`, `SupervisorTrajectory`, `TrajectoryEntry`, `SupervisorRunResult`, `RunStatus` (§4.3, §6.6). V1 models in `models/supervisor.py` remain untouched.~~ **Done**.
4. ~~Create `modules/SupervisorExecutor.py` with `run()`, `_dispatch_targets()`, `_save_pause_state()` (§6.2, §6.3).~~ **Done** — includes debate mode fast-path (§8.13), budget exhaustion handling, structured logging per §17.
5. ~~Add `decide_next()` and V2 `synthesize()` to `RoomSupervisorService` (§6.1). Keep `create_plan()` and `synthesize_results()` for V1 compat.~~ **Done** — `decide_next()`, `synthesize_v2()`, `_format_trajectory()`, and `_parse_v2_action()` added. V1 methods untouched.
6. ~~Add structured logging (§17) and metrics hooks (§18) to `SupervisorExecutor`.~~ **Done** — structured logging with `room_id`, `trajectory_id`, `step_number` at all decision points. Metrics hooks deferred to Phase 2 wiring (no caller yet to emit from).
7. No callers yet — pure addition.

**Implementation notes**:
- `emit_synthesis_message` actual parameter name is `room_user_message_id` (not `related_message_id` as written in pseudocode). Phase 2 code must use the correct kwarg.
- `AgentMessageProcessor` is injected as optional into `QueueExecutor` (`agent_message_processor: AgentMessageProcessor | None = None`) to avoid breaking existing construction patterns. The inline fallback (`_process_single_message_inline`) is the original implementation kept verbatim.

**Post-review fixes** (February 21, 2026):
- **Hallucinated agent ID guard**: `_dispatch_targets.dispatch_one` now validates `target.agent_id` against the registry **before** creating a `RoomAgentMessage` in the database. Previously, the validation logged a warning but still proceeded into `dispatch_one`, which created an orphaned DB record before `resolve_agent` returned `None`. The guard returns a `V2StepResult(success=False, status=FAILED)` immediately.
- **`V2StepResult.status` consistency**: All error `V2StepResult` instances now explicitly set `status=StepStatus.FAILED`. Previously they relied on the default `status=StepStatus.SUCCESS` even when `success=False`, creating an internal inconsistency that downstream code could misinterpret.
- **Debate mode completion log**: Added `supervisor_run_completed` structured log before the debate mode fast-path return. Previously this exit path was invisible to production logging.

### Phase 2: Wire into `RoomMessageCenter` — COMPLETED

**Completed**: February 21, 2026

**Prerequisites**: Resolve #9 (supervisor_v2 flag — §7.0), #5 (extend_info format — §7.1), #4 (V2 branch logic — §7.2), #11 (conversation_context origin — §7.1).

1. ~~Add `_prepare_for_supervisor_v2()` to `RoomServices.send_message_to_room` (§7.1).~~ **Done** — lightweight method stores `agent_registry`, `room_config`, and `conversation_context` in `user_message.extend_info` without any LLM calls or pre-generated agent messages. `send_message_to_room` checks both `use_supervisor` and `supervisor_v2` flags: `use_supervisor + supervisor_v2` → V2 path, `use_supervisor` only → V1 path.
2. ~~Add `supervisor_executor` and `agent_message_processor` to `RoomMessageCenter.__init__`.~~ **Done** — `SupervisorExecutor` constructed with all required dependencies (`supervisor_service`, `room_services`, `tsm`, `sse_manager`, `database_service`, `room_memory_service`, `rate_limit_service`, `agent_dispatcher`, `agent_message_processor`, `room_coordinator_service`).
3. ~~Add V2 branch in `process_room_user_message` (§7.2): if `user_message.extend_info.supervisor_v2`, skip `QueueExecutor.process_queue`, call `supervisor_executor.run()`, handle all 5 `RunStatus` variants.~~ **Done** — `_process_supervisor_v2` method handles COMPLETED (with optional synthesis), PAUSED (no-op), CLARIFYING (stores trajectory + sets room-level `pending_clarification_message_id` + emits clarification message), CANCELED, and FAILED. On `SupervisorPlanningError` (first `decide_next` fails), emits an error synthesis message and returns `FAILED`.
4. ~~Add debate mode fast-path in `SupervisorExecutor.run()` (§8.13).~~ **Already done in Phase 1** — confirmed working.
5. A/B test: rooms with `supervisor_v2 = true` use the new loop; rooms with `use_supervisor = true` only stay on V1. **Wiring complete** — flag checks implemented in `send_message_to_room` and `process_room_user_message`.
6. P0 integration tests (§16.2) — deferred to manual testing.

**Implementation notes**:
- `emit_synthesis_message` is used for both synthesis and clarification messages (with `coordinator_agent_id="supervisor_clarify"` for clarifications). A dedicated `emit_clarification_message` is not needed since the existing method handles it.
- The `database_service.update_room_user_message` method referenced in the design pseudocode does not exist. The actual method is `update_room_user_message_by_message_id(message_id, user_message)`. Similarly, `update_room(room)` is `update_room_by_room_id(room_id, room)`.
- The V2 branch in `process_room_user_message` skips the `inquiry_agent_messages_by_related_message_id` call entirely — V2 has no pre-generated agent messages.
- ~~The fallback to V1 on `SupervisorPlanningError` queries for pre-generated agent messages (which won't exist for V2-prepared messages). This means the fallback will fail with "no pre-generated agent messages found". In practice, the V2 fallback is a safety net — if the supervisor LLM is unreachable on the first call, the room should be reported as failed rather than silently degrading to a different execution model.~~ **Resolved in Phase 5**: The V1 fallback has been removed. `SupervisorPlanningError` now causes `_process_supervisor_v2` to emit a user-facing error synthesis message and return `FAILED`.

**Post-review fixes** (February 21, 2026):
- **Empty targets guard**: `SupervisorExecutor.run()` now guards against `DELEGATE` actions with an empty `targets` list (e.g., LLM returns `{"action": "delegate", "targets": []}` or debate mode has zero healthy agents). Empty targets are converted to a `DONE` action with a warning log. Previously, empty targets would silently burn a loop iteration via `asyncio.gather` with zero coroutines.
- **Debate mode zero-healthy-agents guard**: When `is_debate_mode` is true and no agents pass the `is_healthy` check, the executor now returns `RunStatus.FAILED` immediately instead of constructing an empty `DELEGATE` action.
- **Cancellation token cleanup**: The `RunStatus.CANCELED` handler in `_process_supervisor_v2` now calls `self.sse_manager.clear_cancellation(room_user_message_id)` to remove the stale token. Previously the token was never cleared, which could pre-cancel future messages sharing the same ID.
- **`_save_pause_state` missing `paused_message_id` guard**: When a `PAUSED` result has no `paused_message_id` (possible if `handle_sync_response` returns a paused status without an ID), `_save_pause_state` now logs an `ERROR` instead of silently skipping. A final `ERROR` is logged if no pause state was saved at all, alerting that webhook resume will fail.
- **Synthesis/clarify emission error handling**: `emit_synthesis_message` calls in `_process_supervisor_v2` (both for synthesis and clarification) are now wrapped in `try/except`. On failure, the error is logged but the SSE `COMPLETED` status is still sent so the frontend doesn't hang.
- **`supervisor_run_completed` log on all exit paths**: All return paths from `SupervisorExecutor.run()` now emit a structured `supervisor_run_completed` log via the new `_log_and_return` helper method. Previously only the debate mode fast-path had this log; SYNTHESIZE, CLARIFY, DONE, CANCELED, budget exhaustion, and FAILED exits were invisible to production logging.

**Second review fixes** (February 21, 2026):
- **`_process_supervisor_v2` return value correctness**: The method now returns `success=False, status_code=500` for `RunStatus.FAILED`. Previously all statuses returned `success=True, status_code=200`, which misled backend callers into thinking a failed execution had succeeded.
- **Trajectory persisted on FAILED/CANCELED**: Trajectories are now saved to `user_message.extend_info.supervisor_trajectory` for all terminal statuses (COMPLETED, CLARIFYING, FAILED, CANCELED), not just COMPLETED and CLARIFYING. This preserves the audit log even when executions fail.
- **`SupervisorTrajectory.status` Literal type expanded**: Added `"canceled"` to the status Literal type (`"running" | "completed" | "failed" | "canceled" | "clarifying"`). The cancellation handler now sets `trajectory.status = "canceled"` instead of `"failed"`, eliminating the status/RunStatus mismatch.
- **Failed agent results excluded from room memory**: `SupervisorExecutor.run()` now checks `result.status == StepStatus.SUCCESS and result.success and result.response_text` before writing to room memory. This explicitly excludes PAUSED and FAILED results rather than relying on `response_text` being empty.
- **`asyncio.CancelledError` safety in `_dispatch_targets`**: `dispatch_one` now catches `asyncio.CancelledError` in a dedicated except clause (separate from the `except Exception` catch-all). This prevents cancellation during `asyncio.gather` from losing all coroutine results.
- **KeyError guard in `_process_supervisor_v2`**: Deserialization of `extend["agent_registry"]` and `extend["room_config"]` is now wrapped in `try/except (KeyError, TypeError)`. If the keys are missing (e.g., partial write in `_prepare_for_supervisor_v2`), the method returns a clear FAILED response instead of crashing.
- **V2 guard in `resume_queue_from_continuation`**: The method now peeks at the continuation data before delegating to `QueueExecutor`. If `supervisor_v2: True` is present, it logs an error and returns `False` with a FAILED SSE event, instead of passing V2-shaped data to the V1 resume path where it would fail silently. Full V2 resume is Phase 3 work.
- **Public `create_agent_message` wrapper**: Added `RoomServices.create_agent_message()` as a public wrapper around `_generate_new_agent_message`. `SupervisorExecutor` now calls the public method instead of accessing the private one via `# noqa: SLF001`.

### Phase 3: Push notification resume — COMPLETED

**Completed**: February 21, 2026

**Prerequisites**: Resolve #15 (stale agent registry on resume — §7.3), open question #1 (partial pause matching).

1. ~~Update `resume_queue_from_continuation` to detect `supervisor_v2` in continuation data.~~ **Done** — Phase 2 guard replaced with real resume logic. `resume_queue_from_continuation` detects `supervisor_v2: True` in continuation data and delegates to `_resume_supervisor_v2`.
2. ~~Refresh agent registry from database on resume (§7.3) — do not use serialized registry.~~ **Done** — agent registry refreshed by iterating `room.room_agent_set` and calling `get_agent_by_agent_id` for each agent to get current status. `AgentProfile.from_agent()` used for full agents; agents not found in DB are added as `is_healthy=False` placeholders. Serialized registry kept as fallback if the room's agent set is empty.
3. ~~Reconstruct trajectory, append push notification result, call `supervisor_executor.run(..., resumed_trajectory=...)`.~~ **Done** — `_append_paused_result_to_trajectory` finds the trajectory entry with a missing result (the paused agent was excluded during `_save_pause_state`) and creates a completed `V2StepResult` with the webhook's `task_result_text`. `_find_paused_agent` resolves the agent identity before the append (since the append fills in the gap). Room memory is also updated with the agent's response.
4. Pause/resume round-trip integration test (§16.2) passes. — **Deferred to manual testing.**

**Implementation notes**:
- **Partial pause matching (open question #1)**: Resolved. PAUSED `V2StepResult` entries are preserved in the serialized trajectory (with `status=PAUSED` and `agent_message_id` set). On resume, `_find_paused_agent` matches by `result.agent_message_id == paused_message_id` to identify the exact paused agent. `_append_paused_result_to_trajectory` finds the PAUSED result by the same key and replaces it in-place with a completed result. This is correct for both single-target and multi-target DELEGATE actions, including scenarios where multiple agents in the same entry are paused.
- **Shared result handling**: Extracted `_handle_v2_run_result` from the duplicated post-run logic in `_process_supervisor_v2`. Both `_process_supervisor_v2` and `_resume_supervisor_v2` now delegate to this shared method for trajectory persistence, synthesis/clarification emission, and SSE status broadcasting. The Room object is optionally passed to avoid a redundant DB fetch when the caller already has it.
- **Cancellation token on resume**: A fresh cancellation token is created for the resumed execution (via `sse_manager.get_token` / `create_token`), so users can cancel a resumed loop the same way they cancel an initial loop.
- **`_process_supervisor_v2` refactored**: The inline result handling was replaced with a call to `_handle_v2_run_result`, reducing the method's complexity from 15 to ~5. The shared helper handles all 5 `RunStatus` variants identically for both initial and resumed executions.

**Post-review fixes** (February 21, 2026):
- **PAUSED results preserved in trajectory**: `SupervisorExecutor.run()` no longer strips PAUSED `V2StepResult` entries from `entry.results` before serialization. Previously, paused results were excluded (`[r for r in results if r.status != StepStatus.PAUSED]`), which destroyed the `agent_message_id` needed to correlate webhook responses with the correct agent. Now all results (including PAUSED) are kept, and the resume path matches by `agent_message_id`.
- **`_find_paused_agent` uses `paused_message_id`**: Previously accepted `paused_message_id` as a parameter but never used it — returned the first target missing a non-PAUSED result, which is wrong when multiple agents in the same multi-target DELEGATE are paused. Now matches `result.agent_message_id == paused_message_id` on PAUSED results to identify the exact agent.
- **`_append_paused_result_to_trajectory` replaces by `agent_message_id`**: Previously scanned targets for missing results (position-based), which attributed the webhook response to the wrong agent in multi-pause scenarios. Now finds the PAUSED `V2StepResult` by `agent_message_id` and replaces it in-place with the completed result. Also only marks `entry.completed_at` when no PAUSED results remain in the entry.
- **Continuation re-saved on V2 resume failure**: `resume_queue_from_continuation` now re-saves the continuation **before** attempting `_resume_supervisor_v2`, so a process crash mid-resume doesn't permanently lose the execution state. On successful resume, the continuation is cleared. This eliminates the race window where `get_and_clear` consumed the continuation but a crash before the except-block's `save_continuation_on_message` lost it permanently.
- **`room_config` on resume preserves all fields**: Previously reconstructed `RoomConfig` from scratch with only `is_debate_mode` and `room_agent_set`, silently defaulting any other fields. Now deserializes the full `room_config` from the serialized continuation data and selectively refreshes mutable fields (`is_debate_mode`, `room_agent_set`) from the live room state.

### Phase 4: Clarify action

**Prerequisites**: Resolve #14 (CLARIFY trajectory entry — done in §6.2), open question #7 (stale clarify TTL).

1. Implement `CLARIFY` handling in `RoomMessageCenter` (§7.2, §7.4): emit clarification message, store trajectory, set room-level `pending_clarification_message_id`.
2. Update `send_message_to_room`: detect in-progress trajectory on room, resume with user reply (§7.4).
3. Implement clarify TTL (open question #7) — expire `pending_clarification_message_id` after 1 hour.

**Implementation notes** (completed):

- **`SupervisorTrajectory.clarify_user_reply`** (`models/supervisor_v2.py`): New optional field on the trajectory model. Set by the clarify-resume path before calling `SupervisorExecutor.run(resumed_trajectory=...)`. The supervisor prompt formatter (`_format_trajectory`) appends a "User's Clarification Reply" section so the LLM sees the answer.

- **`_format_trajectory` updated** (`services/room_supervisor_service.py`): When `trajectory.clarify_user_reply` is non-empty, the trajectory summary includes a final `### User's Clarification Reply` section with the user's reply text. This gives the supervisor LLM the context it needs to proceed after CLARIFY.

- **`_prepare_clarify_resume_v2`** (`services/room_services.py`): New method on `RoomServices`. Called from `send_message_to_room` when both `use_supervisor` and `supervisor_v2` are enabled. Checks `room.extend_info.pending_clarification_message_id`, validates the original message's trajectory (must exist, must be in `"clarifying"` status, must not be stale), sets `trajectory.clarify_user_reply = message_text`, and prepares the new user message's `extend_info` with `supervisor_v2_clarify_resume=True` + the resumed trajectory. Returns `True` if resume was prepared, `False` if the flag was stale/invalid (caller falls through to fresh run).

- **Clarify TTL: 1 hour** (`RoomServices.CLARIFY_TTL_SECONDS = 3600`): The age of the last trajectory entry's `started_at` is compared to the current time. If older than 3600s, the pending clarification is treated as stale, the room flag is cleared, and a fresh supervisor run is started. This resolves open question #7.

- **`_clear_pending_clarification`** (`services/room_services.py`): Helper that pops the `pending_clarification_message_id` key from `room.extend_info` and persists the room update.

- **`_process_supervisor_v2` updated** (`modules/RoomMessageCenter.py`): Now checks for `supervisor_v2_clarify_resume` in the user message's `extend_info`. If present, deserializes the `resumed_trajectory` and passes it to `SupervisorExecutor.run(resumed_trajectory=...)`. The executor's adaptive loop resumes from where it left off, with the clarification context available in the trajectory.

- **Existing Phase 2/3 `_handle_v2_run_result` handles CLARIFYING**: The `RunStatus.CLARIFYING` branch was already implemented — it sets `room.extend_info.pending_clarification_message_id`, stores the trajectory, emits the clarification as a pseudo-agent message via `emit_synthesis_message(coordinator_agent_id="supervisor_clarify")`, and sends `SSEProcessingStatus.COMPLETED`.

**Post-review fixes** (February 21, 2026):
- **@mention routing clears stale clarify flag**: `send_message_to_room` now clears `pending_clarification_message_id` when the user @mentions an agent in a V2 supervisor room with a pending clarification. Previously the pending flag survived, causing the next non-mention message to incorrectly resume the old trajectory.
- **Empty `trajectory.entries` guard in TTL check**: `_prepare_clarify_resume_v2` now explicitly rejects trajectories with no entries (possible from data corruption) instead of silently skipping the TTL check and proceeding with an empty trajectory.
- **Clarify-resume failure restores pending flag**: When a clarify-resume triggers `SupervisorPlanningError`, the pending clarification flag is restored on the room and the trajectory status is reverted to `"clarifying"`. The user can retry the clarification reply. The SSE status is sent as `COMPLETED` (not `FAILED`) with a "please answer the clarification question again" message so the user sees the original clarification question still needs answering.
- **Original message trajectory updated on resume**: `_handle_v2_run_result` accepts a new `original_clarify_message_id` parameter. When set, the original message's serialized trajectory status is updated to match the final run status, preventing it from staying permanently in `"clarifying"` in the database.
- **User reply rendered as top-level section**: `_format_trajectory` renders `clarify_user_reply` as a dedicated `### User's Clarification Reply` section at the end of the trajectory summary, outside the windowed loop. This ensures the reply is always visible to the LLM regardless of how many steps have elapsed since the CLARIFY action (previous approach of rendering inline after the CLARIFY entry would lose the reply when the entry scrolled out of the trajectory window).
- **`_build_agent_registry` shared helper**: Extracted duplicate agent registry construction logic from `_prepare_for_supervisor_v2` and `_prepare_clarify_resume_v2` into `RoomServices._build_agent_registry()`.

### Phase 5: Deprecate V1

**Prerequisites**: Ensure #6 (concurrent memory writes — §8.10), #8 (synthesis signature — §6.1), #13 (debate mode — §8.13) are all resolved. Crash recovery (open question #8) should be implemented before removing V1 as a fallback.

1. Remove `_parse_with_supervisor`, `_generate_agent_messages_from_plan`
2. Remove `SupervisorPlan`, `SupervisorStep`, `SupervisorReview`, `ReviewAction` models from `models/supervisor.py`
3. Remove V1 review hook from `QueueExecutor` (`_supervisor_review_step`, `_handle_revise_action`, `_handle_retry_action`)
4. Remove `synthesize_results()` from `RoomSupervisorService` (V1 synthesis signature)
5. Collapse `use_supervisor` + `supervisor_v2` flags into a single `use_supervisor` flag
6. Remove `models/supervisor.py` V1 `StepResult` (only `supervisor_v2.V2StepResult` remains)

**Implementation notes** (completed):

- **`models/supervisor.py` deleted**: The entire V1 model file (`SupervisorPlan`, `SupervisorStep`, `SupervisorReview`, `ReviewAction`, `StepResult`, `AgentProfile`, `RoomConfig`) has been removed. `models/supervisor_v2.py` is now the sole supervisor model module. The `AgentProfile` and `RoomConfig` classes that were shared between V1 and V2 now live exclusively in `supervisor_v2.py`.

- **V1 methods removed from `RoomSupervisorService`**: `create_plan()`, `review_step()`, `synthesize_results()`, `_should_review_step()`, `convert_parsed_result_to_plan()`, and the V1 prompt templates (`SUPERVISOR_SYSTEM_PROMPT`, `SUPERVISOR_REVIEW_SYSTEM_PROMPT`, `SUPERVISOR_SYNTHESIS_SYSTEM_PROMPT`) are all deleted. Only V2 methods remain: `decide_next()`, `synthesize_v2()`, and their helpers.

- **V1 review hook removed from `QueueExecutor`**: `_supervisor_review_step`, `_handle_revise_action`, and `_handle_retry_action` are deleted. `QueueExecutor` now only handles non-supervisor rooms (direct chat, @mention routing) and fast-path cases. Supervisor-enabled rooms are exclusively served by `SupervisorExecutor`.

- **`_parse_with_supervisor` and `_generate_agent_messages_from_plan` removed from `RoomServices`**: The V1 plan-generation path that called the supervisor LLM during message parsing is gone. `send_message_to_room` now goes directly to `_prepare_for_supervisor_v2` for all `use_supervisor=True` rooms.

- **Flag collapse**: At the room level, only `use_supervisor` exists — the room-level `supervisor_v2` toggle is no longer needed. All `use_supervisor=True` rooms take the V2 path. The `supervisor_v2` key on user message `extend_info` is retained as an internal marker confirming that V2 preparation data (agent_registry, room_config, conversation_context) was stored during parsing. It is no longer a room-level opt-in flag.

- **`process_room_user_message` safety net**: A guard was added so that if a `use_supervisor=True` room's message arrives without the `supervisor_v2` marker in `extend_info` (indicating the preparation step was skipped), the request fails fast with an error instead of silently falling through to `QueueExecutor` (which no longer has supervisor hooks).

- **`_handle_v2_run_result` coordinator callback**: Added `room_coordinator_service.on_room_user_message_completed()` call for `RunStatus.COMPLETED`, matching the V1/QueueExecutor completion path. Without this, post-completion logic (room summary generation, notification triggers) was being skipped.

- **PAUSED trajectory persistence**: `_handle_v2_run_result` now persists the trajectory to `user_message.extend_info.supervisor_trajectory` for `RunStatus.PAUSED` as well (previously only COMPLETED, CLARIFYING, FAILED, CANCELED). The pause state is still saved via `_save_pause_state` on the continuation, but the user message now also reflects the trajectory snapshot at pause time for debugging visibility.

- **Cancellation token cleanup on all terminal statuses**: `_handle_v2_run_result` now calls `sse_manager.remove_token(user_message_id)` for all terminal statuses except PAUSED. Previously, tokens were only cleaned up on CANCELED via `clear_cancellation`, causing stale tokens to accumulate for COMPLETED, FAILED, and CLARIFYING runs.

**Files changed** (net: −1,616 lines, then +914 lines post-Phase 5 fixes):

| File | Change |
|---|---|
| `models/supervisor.py` | **Deleted** (143 lines) |
| `models/supervisor_v2.py` | Added `AgentProfile`, `RoomConfig` (moved from V1); added `clarify_original_message_id` field; now sole supervisor model module |
| `modules/QueueExecutor.py` | Removed V1 review hook, `_supervisor_review_step`, `_handle_revise_action`, `_handle_retry_action` |
| `modules/SupervisorExecutor.py` | Updated docstring to reflect sole-executor status; added `_checkpoint_trajectory`, in-flight recovery, cancellation-aware dispatch, debate-mode resume, target deduplication |
| `modules/RoomMessageCenter.py` | Removed V1 fallback path; added `use_supervisor` safety net; added coordinator callback for COMPLETED; added PAUSED trajectory persistence; crash-recovery resume; `_persist_failed_trajectory` helper; SSE token cleanup; continuation re-save before V2 resume |
| `services/room_services.py` | Removed `_parse_with_supervisor`, `_generate_agent_messages_from_plan`; `send_message_to_room` now routes `use_supervisor` directly to V2 |
| `services/room_supervisor_service.py` | Removed V1 methods and prompts; only V2 `decide_next`/`synthesize_v2` remain; `_format_trajectory` windowed with PAUSED rendering; `_parse_v2_action` malformed target logging; `decide_next` fail-open SYNTHESIZE; `_fallback_v2_synthesis` PAUSED exclusion |
| `services/openai_service.py` | Added 30-second timeout to `decide_next` and `synthesize_v2` LLM calls |
| `services/database_service.py` | Added `claim_stuck_supervisor_trajectory`, `is_message_cancelled`, `cancel_agent_messages_by_ids` |
| `database/mongodb.py` | Added `cancel_agent_messages_by_ids`, `claim_stuck_supervisor_trajectory` (atomic find-and-update) |
| `jobs/stale_task_checker.py` | Added `_recover_stuck_supervisor_trajectories`, `_process_recovered_supervisor_message` |
| `common/utils/cancellation.py` | Added `CancellationToken.wait()` for racing cancellation against multiple tasks |

**Post-Phase 5 review fixes** (February 21, 2026):
- **PAUSED results rendered correctly in trajectory**: `_format_trajectory` now renders PAUSED results as `[PAUSED (awaiting external response)]` instead of `[SUCCESS]`. Previously, PAUSED results had `success=True` and were shown as `[SUCCESS]` with empty response text, which misled the supervisor LLM on resume into thinking the agent had responded with nothing. The collapsed summary for older entries also now tags paused agents as `AgentName(PAUSED)`.
- **`decide_next` fail-open synthesizes when partial results exist**: When `decide_next` fails mid-loop and 2+ agents have succeeded, it now returns `SYNTHESIZE` instead of `DONE`. Previously a mid-loop LLM failure silently stopped execution with partial unsynthesized results and no explanation to the user. Single-agent results still use `DONE` (the agent's response stands on its own).
- **Malformed targets logged in `_parse_v2_action`**: Targets missing `agent_id` are now logged at `WARNING` level instead of silently dropped. If all targets in a DELEGATE action are malformed, an additional warning is logged before the empty-targets guard converts the action to DONE. This aids debugging when the supervisor LLM returns unexpected target formats.
- **Target deduplication uses (agent_id, task) tuple**: Previously, targets were deduplicated by `agent_id` alone, which silently dropped legitimate same-agent-different-task delegations (e.g., a search agent queried for two different things concurrently). Now only targets with identical `(agent_id, task)` pairs are deduplicated.
- **Debate mode + push notification resume**: When a debate-mode trajectory resumes after a push notification webhook (step > 0), the executor now checks if all PAUSED results have been filled in and immediately returns DONE. Previously, the debate fast-path only fired on `step_number == 0`, so resumed debate trajectories fell through to `decide_next`, which didn't know about debate mode semantics and could make incorrect routing decisions.
- **Design doc cancellation status corrected**: The §6.2 pseudocode showed `trajectory.status = "failed"` in the cancellation handler, contradicting the actual implementation's `"canceled"`. Fixed to match.
- **PAUSED results excluded from `r.success`-based filters**: Three code paths used `r.success` to identify completed agent results, which incorrectly included PAUSED results (which have `success=True` but empty `response_text`): (1) `decide_next` fail-open threshold for triggering SYNTHESIZE, (2) the SYNTHESIZE handler's guard against synthesizing with zero results, (3) `_fallback_v2_synthesis` rendering. All three now also check `r.status == StepStatus.SUCCESS` to exclude PAUSED results. Without this fix, a trajectory with 1 completed agent and 1 paused agent could trigger synthesis over essentially one response plus an empty placeholder.
- **`clarify_original_message_id` survives pause/resume**: Added `clarify_original_message_id` field to `SupervisorTrajectory`. When a clarify-resume trajectory hits a push notification pause and later resumes via the webhook path, the original clarification message ID was previously lost because `_resume_supervisor_v2` never passed it to `_handle_v2_run_result`. The original message's trajectory would stay stuck in `"clarifying"` status forever. The fix stores the ID on the trajectory itself (set by `_process_supervisor_v2` before calling `run()`), so it survives serialization through `_save_pause_state` → continuation → `_resume_supervisor_v2`. Both `_process_supervisor_v2` and `_resume_supervisor_v2` now read from `result.trajectory.clarify_original_message_id` instead of (or in addition to) the user message `extend_info`.

---

## 14. Open Questions

1. **Concurrent dispatch + push notification** (partial pause — **resolved in Phase 3**): If a multi-target DELEGATE has 2 agents and one is a push notification agent (PAUSED) and the other completes, the current design keeps all results (including PAUSED) in the trajectory entry. PAUSED `V2StepResult` entries retain their `agent_message_id`, which is the key used for webhook correlation. On resume, `_find_paused_agent` and `_append_paused_result_to_trajectory` match by `agent_message_id == paused_message_id` to identify and replace the exact PAUSED result. This is correct even when multiple agents in the same entry are paused — each webhook fires for a specific `paused_message_id` and replaces the corresponding PAUSED result.

2. **Clarify UX** (frontend message type): When the supervisor returns CLARIFY, the clarification question is emitted as a pseudo-agent message. The frontend needs to distinguish it from a normal agent response (e.g., to render it differently or suppress the "agent name" header). Options: (a) use a distinct `agent_id` like `"__supervisor_clarify__"`, (b) add a `message_type: "clarification"` field to the SSE event, (c) set a flag in `extend_info` on the pseudo-agent message. Option (a) is simplest and requires no frontend changes.

3. **Debate mode** (resolved): When `room_config.is_debate_mode` is true, V2 uses a **two-layer approach**:

   **Layer 1 — Prompt injection** (§5): The `{debate_mode_note}` placeholder includes explicit debate instructions: _"This room is in debate mode. Delegate the SAME user message to ALL agents concurrently as a single multi-target DELEGATE. Each agent must respond independently. Do NOT synthesize — use DONE after all agents respond."_

   **Layer 2 — Code-level enforcement** (§8.13): As a safety net against prompt non-compliance, `SupervisorExecutor.run()` includes a debate mode fast-path. When `room_config.is_debate_mode` is true, the executor **bypasses `decide_next` entirely** on the first iteration and constructs a synthetic `DELEGATE` action targeting all healthy agents with the original user message as the task. After all agents respond, it returns `DONE` (no synthesis). This replicates what V1's `DebateService.inject_short_debate_for_agent_message()` did, but without the `_queue_next_messages` hook.

   **Why both layers**: Layer 1 is for cases where debate mode is soft (the supervisor could choose to delegate to a subset). Layer 2 is for strict debate (all agents must participate). The default in V2 is Layer 2 (code-enforced). Layer 1 becomes relevant if we later introduce a "soft debate" mode where the supervisor can exclude agents it deems irrelevant.

4. **Trajectory prompt size** (**resolved**): The `_format_trajectory` method truncates each agent response to 500 characters (§5) and uses a sliding window (default: 5 entries). Older entries are collapsed into a one-line summary that includes action types (DELEGATE agent names, CLARIFY, DONE) so the supervisor doesn't lose context about non-DELEGATE actions when they scroll out of the window.

5. **Observability**: The full trajectory is stored in `user_message.extend_info.supervisor_trajectory` (persisted for all terminal statuses including PAUSED since Phase 5). For production debugging (e.g., "why did the supervisor delegate to the wrong agent?"), a dedicated `supervisor_trajectories` collection would enable cross-message queries. This is a nice-to-have for post-launch.

6. **`room_coordinator_service` synthesis message emission** (confirmed): `room_coordinator_service` is now listed as a `SupervisorExecutor` dependency (§6.2). The emission call is `room_coordinator_service.emit_synthesis_message(synthesis_text)` — this matches the V1 call signature and requires no changes to `RoomCoordinatorService`.

7. **Stale clarify state / TTL** (**resolved in Phase 4**): The room-level `pending_clarification_message_id` flag (§7.4) now has a 1-hour TTL enforced in `RoomServices._prepare_clarify_resume_v2`. When the user's next message arrives, the age of the last trajectory entry's `started_at` is checked against `CLARIFY_TTL_SECONDS` (3600s). If stale, the flag is cleared and the message is treated as a fresh request. This avoids the scenario where an ignored clarification blocks all subsequent messages.

8. **Crash recovery / durable execution** (**resolved**): If the server process restarts while `SupervisorExecutor.run()` is mid-loop, the in-flight execution is recovered via per-step trajectory checkpointing + a background recovery job. See §8.14 for the full design.

   **Per-step checkpointing**: `_checkpoint_trajectory` persists the trajectory to `user_message.extend_info.supervisor_trajectory` after each DELEGATE entry is created but before dispatch begins (pre-dispatch checkpoint). This creates a recoverable entry with empty results — crash recovery can detect it and re-dispatch using the same action. Checkpointing is best-effort; failures are logged but do not abort the loop.

   **Recovery job**: `StaleTaskChecker._recover_stuck_supervisor_trajectories` scans for user messages where `extend_info.supervisor_trajectory.status == "running"` and `message_created_at` is older than the orphan threshold. Each stuck trajectory is atomically claimed via `claim_stuck_supervisor_trajectory` (MongoDB `find_one_and_update` with status precondition: `"running"` → `"recovering"`) so only one worker can recover it. The recovery job re-triggers `process_room_user_message`, which detects the checkpointed trajectory and resumes via `SupervisorExecutor.run(resumed_trajectory=...)`.

   **In-flight step recovery**: When `run()` detects the last trajectory entry has `action=DELEGATE` and empty results (indicating a crash mid-dispatch), it pops the entry and re-uses its action instead of calling `decide_next`. This avoids duplicate dispatches.

---

## 15. Industry Comparison

As of early 2026, the dominant multi-agent orchestration frameworks (LangGraph, AutoGen, CrewAI, OpenAI Agents SDK) have converged on architectures that are directly comparable to V2's design. This section documents where V2 aligns with that consensus, where it lags, and where it leads.

### 15.1 Architectural Alignment

The reactive adaptive loop — **see the full history, decide one action at a time** — is now the industry standard for supervisor orchestration:

| Framework | Supervisor Approach | Alignment with V2 |
|---|---|---|
| **LangGraph `create_supervisor`** | LLM sees full message history, selects next agent via tool call, loops until terminal state | ✅ Identical pattern. LangGraph now recommends this over their own plan-and-execute tutorial. |
| **AutoGen `SelectorGroupChat`** | LLM selects next speaker after each message based on full conversation context | ✅ Equivalent to `decide_next` with trajectory |
| **Anthropic internal research system** | Orchestrator (Claude Opus) iteratively decides which subagent to spawn next based on collected results | ✅ Equivalent. Their system also found ~15x token overhead vs. single-agent, consistent with V2's LLM call count increase |
| **OpenAI Agents SDK** | Agents declare handoffs as tools; routing is distributed, not centralized | ⚠️ Different model (no central supervisor LLM), but same reactive principle |
| **CrewAI hierarchical process** | Manager LLM dynamically allocates tasks based on agent roles | ⚠️ Similar intent but documented failures: often executes sequentially despite declaring parallel; same enforcement gap as V1's `strategy` |
| **Plan-and-execute (LangGraph tutorial, early CrewAI)** | Upfront planner + separate executor + optional re-planner | ❌ V1's model; industry is moving away from this for supervisor use cases |

V2's core loop is **mainstream**. The design decision to abandon plan-and-execute is validated by the industry trajectory.

### 15.2 Where V2 Lags Frameworks

| Gap | Framework Capability | V2 Status | Priority |
|---|---|---|---|
| **Crash recovery** | LangGraph checkpointers (Postgres/Redis) snapshot state after every step; execution resumes automatically on restart | V2 now checkpoints the trajectory to MongoDB after each DELEGATE step and recovers stuck trajectories via `StaleTaskChecker` (§8.14). Not as seamless as LangGraph's built-in checkpointers but functionally equivalent. | ~~Medium~~ **Resolved** |
| **Trajectory context management** | LangGraph's `SummarizationMiddleware` automatically compresses history when approaching token limits | V2 uses a sliding window (default: 5 entries) with one-line summaries for older entries (§5). Agent responses truncated to 500 chars. Not automatic summarization but effective for typical step counts. | ~~Medium~~ **Resolved** |
| **Infrastructure-level retries** | `ToolRetryMiddleware` retries failed agent calls with exponential backoff transparently | V2 surfaces failures as `StepResult(success=False)` and burns a `decide_next` call to handle them | Low — LLM-mediated retry is workable; infra retry would reduce LLM call overhead |
| **Visual observability** | LangGraph renders agent graphs as visual diagrams; breakpoints at specific nodes | V2 is a Python while loop; no visual topology | Low — nice-to-have for debugging; trajectory logs partially compensate |

### 15.3 Where V2 Leads Frameworks

(See §2.2 for details.)

1. **Native SSE streaming** — frameworks require additional plumbing to connect graph state to live SSE streams
2. **Async push notification resume** — frameworks support synchronous human-in-the-loop but not indefinite async webhook waits
3. **Room-scoped persistent memory** — frameworks scope memory to the current run; V2's `RoomMemoryService` provides cross-conversation room context automatically

### 15.4 The Hybrid ReAct+Plan Trend

Recent research (early 2026) is converging on a **hybrid** approach: reactive decisions per step, but with an optional upfront "intent declaration" that gives the LLM a mechanism to reason about the full task before making greedy per-step decisions. This directly addresses the local optimization trap (§8.12).

V2 can adopt this incrementally without restructuring. The simplest form is an optional `planned_steps` hint in the first `decide_next` response:

```python
class SupervisorAction(BaseModel):
    ...
    # Optional: supervisor's high-level plan for the remaining steps.
    # Not enforced — the supervisor can deviate — but helps avoid greedy decisions.
    planned_steps: list[str] | None = None   # e.g., ["delegate Flight Agent", "delegate Hotel Agent", "synthesize"]
```

This is a V3 consideration, not a V2 requirement. V2's current design is adequate for the majority of use cases in production today.

---

## 16. Testing Strategy

### 16.1 Unit Tests

| Test Area | What to Test | Priority |
|---|---|---|
| `decide_next` JSON parsing | Malformed JSON, missing required fields, hallucinated `agent_id` values not in registry, unexpected `action` values, extra fields (LLM verbosity) | **P0** |
| Supervisor loop state machine | DELEGATE → DONE, DELEGATE → DELEGATE → SYNTHESIZE, DELEGATE → CLARIFY, budget exhaustion (MAX_STEPS), immediate DONE (no agents needed) | **P0** |
| `_dispatch_targets` | Single target, multiple targets (concurrent), partial failure (1 of 2 agents fails), all targets fail, rate-limited target, hallucinated agent_id target | **P0** |
| `V2StepResult` / `TrajectoryEntry` | Serialization round-trip (`model_dump` → reconstruct), status transitions, all `StepStatus` variants | **P1** |
| `format_trajectory` | Empty trajectory, single entry, multi-entry, 500-char truncation boundary, entries for all action types (not just DELEGATE) | **P1** |
| Debate mode fast-path | Synthetic DELEGATE targets all healthy agents, skips `decide_next`, returns DONE after dispatch | **P1** |
| Agent registry validation | Supervisor returns agent_id not in registry → error result, supervisor returns duplicate agent_ids → handled | **P1** |

### 16.2 Integration Tests

| Test Area | What to Test | Priority |
|---|---|---|
| Concurrent dispatch | 2-3 agents dispatched concurrently, all succeed; verify SSE events are correctly tagged by `agent_message_id`; verify room memory contains all agent responses | **P0** |
| Concurrent dispatch partial failure | 1 agent fails, 1 succeeds; supervisor sees failure in trajectory and adapts (retries or proceeds) | **P0** |
| Pause/resume round-trip | Serialize trajectory with PAUSED step → reconstruct from `pause_state` → resume with `resumed_trajectory` → verify loop continues from correct step | **P0** |
| Clarify round-trip | Supervisor returns CLARIFY → trajectory stored → user replies → trajectory resumed → supervisor continues | **P1** |
| V2 branch in `process_room_user_message` | `use_supervisor` room → `supervisor_v2` marker set → `SupervisorExecutor.run()` called; `use_supervisor` room without marker → fails fast with error (safety guard) | **P1** |
| Rate limit recording | Agent dispatched → succeeds → `record_request` called; agent dispatched → fails → `record_request` NOT called | **P1** |
| Stale agent registry on resume | Agent becomes inactive between pause and resume → refreshed registry excludes it → supervisor adapts | **P2** |

### 16.3 End-to-End Tests

| Test Area | What to Test | Priority |
|---|---|---|
| Full V2 flow | `send_message_to_room` → `_prepare_for_supervisor_v2` → `process_room_user_message` (V2 branch) → `SupervisorExecutor.run()` → agents respond → coordinator callback → synthesis emitted → SSE COMPLETED | **P0** |
| Non-supervisor rooms unaffected | Room without `use_supervisor` → `QueueExecutor` processes messages normally, no V2 code paths triggered | **P0** |
| Cancellation | User cancels mid-loop → executor returns CANCELED → SSE CANCELED sent | **P1** |

---

## 17. Structured Logging

V2 must log at every decision point with structured fields for production debugging. All log entries include `room_id`, `trajectory_id`, `user_message_id`, and `step_number` as structured fields (not interpolated into message strings).

### 17.1 Required Log Points in `SupervisorExecutor.run()`

```python
# At loop start (each iteration)
logger.info(
    "supervisor_loop_iteration",
    room_id=room_id,
    trajectory_id=trajectory.trajectory_id,
    step_number=step_number,
    total_supervisor_calls=trajectory.total_supervisor_calls,
)

# After decide_next returns
logger.info(
    "supervisor_action_decided",
    room_id=room_id,
    trajectory_id=trajectory.trajectory_id,
    step_number=step_number,
    action_type=action.action,
    reasoning=action.reasoning,
    target_count=len(action.targets),
    target_agents=[t.agent_name for t in action.targets],
)

# After each dispatch_one completes
logger.info(
    "supervisor_agent_dispatched",
    room_id=room_id,
    trajectory_id=trajectory.trajectory_id,
    step_number=step_number,
    agent_id=target.agent_id,
    agent_name=target.agent_name,
    success=result.success,
    status=result.status,
    error_message=result.error_message,
    agent_message_id=result.agent_message_id,
)

# On loop exit
logger.info(
    "supervisor_run_completed",
    room_id=room_id,
    trajectory_id=trajectory.trajectory_id,
    status=run_result.status,
    total_steps=len(trajectory.entries),
    total_supervisor_calls=trajectory.total_supervisor_calls,
)
```

### 17.2 Required Log Points in `_dispatch_targets`

- Agent resolution failure (WARN)
- Rate limit check failure (WARN)
- Rate limit recording (DEBUG)
- RoomAgentMessage creation (INFO)
- Push notification pause detected (INFO)

### 17.3 Log Level Guidelines

| Level | When |
|---|---|
| ERROR | Unhandled exception in `dispatch_one`, supervisor LLM call failure (first iteration → `SupervisorPlanningError`) |
| WARNING | Hallucinated agent_id, agent not found/inactive, rate limited, supervisor returned SYNTHESIZE with no results |
| INFO | Loop iteration start, action decided, agent dispatched, run completed, pause/resume, debate mode fast-path |
| DEBUG | Trajectory serialization, rate limit recording, memory writes |

---

## 18. Metrics and Observability

For production monitoring, V2 should emit the following metrics. These can be implemented as Prometheus counters/histograms or equivalent.

### 18.1 Counters

| Metric | Labels | Description |
|---|---|---|
| `supervisor_v2_runs_total` | `status` (completed/failed/canceled/paused/clarifying) | Total V2 supervisor runs |
| `supervisor_v2_loop_iterations_total` | `action_type` (delegate/synthesize/clarify/done) | Total loop iterations by action type |
| `supervisor_v2_agent_dispatches_total` | `success` (true/false), `reason` (rate_limited/not_found/error/ok) | Total agent dispatch attempts |
| `supervisor_v2_budget_exhaustions_total` | — | Times MAX_STEPS was reached |
| `supervisor_v2_debate_fastpath_total` | — | Times debate mode fast-path was used |

### 18.2 Histograms

| Metric | Labels | Description |
|---|---|---|
| `supervisor_v2_decide_next_duration_seconds` | — | Time per `decide_next` LLM call |
| `supervisor_v2_run_duration_seconds` | `status` | Total wall-clock time for `run()` |
| `supervisor_v2_agent_dispatch_duration_seconds` | `agent_id` | Time per agent dispatch (including LLM response) |
| `supervisor_v2_synthesis_duration_seconds` | — | Time for synthesis LLM call |
| `supervisor_v2_loop_iterations_per_run` | — | Distribution of iteration counts per run |
| `supervisor_v2_supervisor_calls_per_run` | — | Distribution of supervisor LLM calls per run |

### 18.3 Implementation Note

Metrics should be emitted at the `SupervisorExecutor` level, not inside `RoomSupervisorService` or `AgentMessageProcessor`. This keeps the metrics boundary clean — the executor owns the loop and is the natural place to measure its behavior. Use `time.monotonic()` for duration measurements, not `datetime.utcnow()`.

---

## 19. Summary

Supervisor V2 replaces the plan-then-execute architecture with an adaptive loop:

- **One decision at a time**: The supervisor sees the actual result of each agent before deciding what to do next
- **No plan artifact**: The trajectory (what happened) is the only state — no dual plan/messages representation
- **No bolt-on mechanisms**: REVISE, RETRY, SKIP, and the review hook are eliminated. Adaptation is the default behavior of the loop.
- **True concurrency**: Multi-target delegate actions execute concurrently via `asyncio.gather`
- **Clarify works**: The supervisor can pause and ask the user for more information
- **Budget-bounded**: Hard cap on supervisor LLM calls prevents runaway costs
- **Cancellation-aware**: LLM calls and agent dispatches are raced against the cancellation token; partial results are collected for cleanup
- **Crash-recoverable**: Per-step trajectory checkpointing + a background recovery job ensure mid-loop crashes are detected and resumed

The migration is complete: V1 has been deprecated and removed (Phase 5). All supervisor-enabled rooms (`use_supervisor = True`) use V2 exclusively. The `QueueExecutor` continues to serve non-supervisor rooms (direct chat, @mention routing). All existing infrastructure (agent dispatch, SSE, push notifications, room memory) is preserved.
