# Room Supervisor Pattern Design

**Date**: February 11, 2026
**Updated**: February 20, 2026
**Status**: Implemented (PR #83) — Phases 1–5 complete
**Scope**: Replace fragmented orchestration with a unified Supervisor layer for multi-agent chat rooms

---

## 1. Problem Statement

The current orchestration is split across multiple disconnected layers:

| Current Layer | Responsibility | Limitation |
|---|---|---|
| `openai_service.parse_user_message_by_llm()` | Task decomposition | One-shot, no feedback loop |
| `AgentSelectionService` | Vector search + LLM routing | Stateless, no room context |
| `AgentResolverService` | Runtime agent resolution | No strategic awareness |
| `RoomMessageCenter._process_agent_message_queue` | Sequential execution | Rigid, cannot adapt mid-flow |
| `DebateService` | Inject previous agent response | Only sees one prior agent |
| `RoomCoordinatorService` | Post-hoc summary | After-the-fact, no control |

**Key gaps:**
- Agents have no awareness of each other in the room
- No ability to adapt the plan after an agent responds (e.g., retry with different agent, refine prompt)
- Debate mode and normal mode use entirely separate code paths
- Post-processing summary is disconnected from the planning that created the tasks

---

## 2. Design Goals

1. **Unified intelligence** -- A single Supervisor decides routing, adapts after results, and synthesizes
2. **Preserve existing infrastructure** -- Reuse the message queue, push notification support, SSE streaming, and message models unchanged
3. **Zero additional latency for simple cases** -- Direct chat (1 agent, no debate) must remain zero-LLM-overhead
4. **Structured, deterministic plans** -- Supervisor produces JSON execution plans, not free-form reasoning
5. **Post-step review** -- After agent completion, Supervisor can evaluate and optionally revise the remaining plan
6. **Inter-agent awareness** -- Agents receive context about who else is in the room

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
                         │  Direct chat? (1 agent, no debate)│
                         │  @mention? (deterministic routing) │
                         └──────┬──────────────┬────────────┘
                                │              │
                           YES  │              │ NO
                                ▼              ▼
                  ┌──────────────────┐  ┌───────────────────────┐
                  │ Existing pipeline │  │   SUPERVISOR LLM      │
                  │ (no LLM routing)  │  │   (structured output)  │
                  │                   │  │                        │
                  │ Skip straight to  │  │  Input:                │
                  │ queue execution   │  │  - User message        │
                  └────────┬─────────┘  │  - Agent registry       │
                           │            │  - Conversation context  │
                           │            │  - Room config           │
                           │            │                        │
                           │            │  Output:               │
                           │            │  - SupervisorPlan (JSON)│
                           │            └───────────┬───────────┘
                           │                        │
                           ▼                        ▼
                  ┌─────────────────────────────────────────────┐
                  │  RoomMessageCenter._process_agent_message_queue │
                  │  (UNCHANGED — executes plan as today)            │
                  └──────────────────────┬──────────────────────┘
                                         │
                              ┌───────────┴───────────┐
                              │ After EACH agent step  │
                              │ completes:             │
                              └───────────┬───────────┘
                                          │
                                          ▼
                              ┌────────────────────────┐
                              │  SUPERVISOR REVIEW      │
                              │  (lightweight LLM call) │
                              │                         │
                              │  "Was this result good? │
                              │   Should I adjust the   │
                              │   remaining plan?"      │
                              │                         │
                              │  Output:                │
                              │  - CONTINUE (proceed)   │
                              │  - REVISE (new steps)   │
                              │  - RETRY (same agent)   │
                              │  - SKIP (drop step)     │
                              └───────────┬────────────┘
                                          │
                                          ▼
                              ┌────────────────────────┐
                              │  Continue queue or      │
                              │  apply revised plan     │
                              └───────────┬────────────┘
                                          │
                                          ▼
                              ┌────────────────────────┐
                              │  All steps complete     │
                              │                         │
                              │  SUPERVISOR SYNTHESIS   │
                              │  (replaces both         │
                              │   RoomCoordinatorService│
                              │   and DebateService     │
                              │   summary logic)        │
                              └────────────────────────┘
```

---

## 4. Data Models

### 4.1 SupervisorPlan

The Supervisor always produces this structured output. It is stored alongside the user message for auditability.

```python
class SupervisorPlan(BaseModel):
    """Structured execution plan produced by the Supervisor LLM."""
    plan_id: str = Field(default_factory=lambda: uuid4().hex)
    strategy: Literal["direct", "parallel", "sequential", "debate", "clarify"]
    reasoning: str              # Why this strategy was chosen (for logging only)
    steps: list[SupervisorStep]
    synthesis_instruction: str | None = None  # How to combine results at the end
    created_at: datetime = Field(default_factory=utcnow)


class SupervisorStep(BaseModel):
    """A single step in the execution plan."""
    step_id: str                # e.g., "step_1"
    agent_id: str               # Which agent to delegate to
    agent_name: str             # For display/logging
    task_description: str       # The prompt/task to send to the agent
    depends_on: list[str] = []  # step_ids this depends on
    context_from_steps: list[str] = []  # step_ids whose results should be included in prompt
    priority: int = 0           # For parallel execution ordering
    max_retries: int = 1        # How many times to retry on failure


class SupervisorReview(BaseModel):
    """Result of the Supervisor reviewing a completed step."""
    action: Literal["continue", "revise", "retry", "skip"]
    reasoning: str
    revised_steps: list[SupervisorStep] | None = None  # Only if action == "revise"
    retry_with_refinement: str | None = None            # Refined prompt if action == "retry"
```

### 4.2 Storage: Extend RoomAgentMessage

The plan is stored on the user message's `extend_info` for traceability:

```python
# In send_message_to_room, after Supervisor produces a plan:
user_message.extend_info = {
    ...,
    "supervisor_plan": plan.model_dump(mode="json"),
}
```

Each agent message already has `step_number`, `total_steps`, `related_message_id`, and `task_content`. These continue to be set from the `SupervisorPlan.steps` exactly as today's `_generate_agent_messages_based_on_parsed_result` does.

No new collections. No new message types. The Supervisor is invisible to the frontend -- it just produces better plans and better summaries.

---

## 5. Supervisor LLM Design

### 5.1 Planning Prompt

```python
SUPERVISOR_PLANNING_SYSTEM_PROMPT = """You are a Supervisor that routes user messages to specialist agents in a chat room.

## Available Agents
{agent_registry}

## Your Job
Analyze the user's message and create an execution plan. Output ONLY valid JSON matching the schema below.

## Decision Rules
1. DIRECT: If the message is clearly for one agent, route directly. Set strategy="direct".
2. PARALLEL: If multiple agents can work independently on different aspects, use strategy="parallel".
3. SEQUENTIAL: If Agent B needs Agent A's output, use strategy="sequential" with depends_on.
4. DEBATE: If the room is in debate mode, send the same task to multiple agents for contrasting perspectives. Set strategy="debate".
5. CLARIFY: If the message is ambiguous and you cannot determine which agent(s) to use, set strategy="clarify" with a clarification question.

## Context Passing Rules
- For sequential steps, set context_from_steps to include the step_ids whose results the agent needs.
- Write each step's task_description as a clear, specific instruction. Do NOT just forward the raw user message -- tailor it for the target agent.
- Include relevant conversation context in the task_description when it helps the agent.

## Output Schema
{
  "strategy": "direct" | "parallel" | "sequential" | "debate" | "clarify",
  "reasoning": "Brief explanation of your routing decision",
  "steps": [
    {
      "step_id": "step_1",
      "agent_id": "uuid",
      "agent_name": "Agent Name",
      "task_description": "What this agent should do",
      "depends_on": [],
      "context_from_steps": [],
      "priority": 0,
      "max_retries": 1
    }
  ],
  "synthesis_instruction": "How to combine results (null for single-agent)" | null
}"""
```

### 5.2 Review Prompt (Post-Step)

```python
SUPERVISOR_REVIEW_SYSTEM_PROMPT = """You are reviewing the result of a step in a multi-agent execution plan.

## Completed Step
Agent: {agent_name}
Task: {task_description}
Result: {agent_result}

## Remaining Plan
{remaining_steps}

## Your Decision
Evaluate the result and decide the next action. Output ONLY valid JSON.

Rules:
- "continue": Result is acceptable. Proceed with the remaining plan as-is.
- "revise": Result changes what remaining steps should do. Provide revised_steps.
- "retry": Result is poor/empty. Retry this step with a refined prompt (max {retries_left} retries left).
- "skip": This step's result makes remaining steps unnecessary. Skip them.

For simple cases (single agent, result looks fine), always return "continue".
Only trigger "revise" or "retry" when clearly warranted.

{
  "action": "continue" | "revise" | "retry" | "skip",
  "reasoning": "Brief explanation",
  "revised_steps": [...] | null,
  "retry_with_refinement": "refined prompt" | null
}"""
```

### 5.3 Synthesis Prompt

Replaces both `RoomCoordinatorService` debate/non-debate summary and becomes the Supervisor's final synthesis:

```python
SUPERVISOR_SYNTHESIS_SYSTEM_PROMPT = """You are synthesizing the results from multiple specialist agents into a single coherent response for the user.

## Agent Results
{agent_results}

## Synthesis Instructions
{synthesis_instruction}

## Rules
- Attribute insights to their source agent when helpful: "According to [Agent Name]..."
- Resolve contradictions by noting both perspectives.
- If one agent failed, note what was successfully completed and what was not.
- Be concise. The user has already seen each agent's individual response.
- Focus on the unified answer, not a recap of each agent's full response.
"""
```

---

## 6. Service Design: `RoomSupervisorService`

### 6.1 Class Overview

```python
class RoomSupervisorService:
    """
    Supervisor for multi-agent room orchestration.
    
    Responsibilities:
    1. PLAN: Analyze user message + agent registry -> SupervisorPlan
    2. REVIEW: After each agent step, evaluate result and optionally revise plan
    3. SYNTHESIZE: After all steps complete, generate unified summary
    
    This service is called by RoomServices (for planning) and
    RoomMessageCenter (for review and synthesis), replacing:
    - openai_service.parse_user_message_by_llm() (planning portion)
    - DebateService.inject_short_debate_for_agent_message()
    - RoomCoordinatorService.on_room_user_message_completed()
    """
    
    def __init__(self):
        self.openai_service = openai_service
        self.database_service = db_service
    
    async def create_plan(
        self,
        message_text: str,
        agent_registry: list[AgentProfile],
        room_config: RoomConfig,
        conversation_context: str | None = None,
    ) -> SupervisorPlan:
        """Create an execution plan for a user message."""
        ...
    
    async def review_step(
        self,
        plan: SupervisorPlan,
        completed_step: SupervisorStep,
        agent_result: str,
        remaining_steps: list[SupervisorStep],
    ) -> SupervisorReview:
        """Review a completed step and decide next action."""
        ...
    
    async def synthesize_results(
        self,
        plan: SupervisorPlan,
        step_results: dict[str, StepResult],
        room_config: RoomConfig,
    ) -> str:
        """Synthesize multi-agent results into a unified response."""
        ...
```

### 6.2 AgentProfile (What the Supervisor Knows)

```python
class AgentProfile(BaseModel):
    """Compact agent description for the Supervisor's context window."""
    agent_id: str
    agent_name: str
    description: str          # From agent_card.description
    capabilities: list[str]   # From agent_card.skills
    success_rate: float       # Computed from call_success_count / call_count
    is_healthy: bool          # From last health check
    
    @classmethod
    def from_agent(cls, agent: Agent) -> "AgentProfile":
        card = agent.agent_card
        total = agent.call_count or 1
        return cls(
            agent_id=agent.agent_id,
            agent_name=card.name,
            description=card.description or "",
            capabilities=[s.id for s in (card.skills or [])],
            success_rate=agent.call_success_count / total,
            is_healthy=agent.agent_status == AgentStatus.active,
        )

class RoomConfig(BaseModel):
    """Room configuration relevant to the Supervisor."""
    is_debate_mode: bool = False
    room_agent_set: dict[str, str] = {}  # {agent_id: agent_name}
```

---

## 7. Integration Points (What Changes)

### 7.1 `RoomServices.send_message_to_room` (Planning Phase)

The Supervisor replaces `parse_user_message` for multi-agent cases. The fast-path stays untouched.

```
BEFORE:
  send_message_to_room()
    ├── Direct chat? → skip LLM, create 1 agent message
    ├── @mention? → deterministic fan-out
    └── Multi-agent → parse_user_message_by_llm() → static plan → agent messages

AFTER:
  send_message_to_room()
    ├── Direct chat? → skip LLM, create 1 agent message         [UNCHANGED]
    ├── @mention? → deterministic fan-out                        [UNCHANGED]
    └── Multi-agent → supervisor_service.create_plan()           [NEW]
                    → _generate_agent_messages_from_plan()       [NEW, maps SupervisorPlan → RoomAgentMessages]
```

The output of `create_plan()` is a `SupervisorPlan` which is converted to `RoomAgentMessage` records using the same pattern as `_generate_agent_messages_based_on_parsed_result`. The message queue infrastructure is **unchanged**.

### 7.2 `RoomMessageCenter._process_agent_message_queue` (Review Phase)

After each agent step completes successfully, the Supervisor reviews the result. This is a new call inserted after the existing memory update.

```
BEFORE (line ~1141-1152 of RoomMessageCenter.py):
  # After agent completes:
  1. Store response in room memory
  2. Queue next messages in chain
  
AFTER:
  # After agent completes:
  1. Store response in room memory                              [UNCHANGED]
  2. IF supervisor plan exists:
     a. supervisor_service.review_step(plan, step, result)      [NEW]
     b. IF review.action == "continue" → proceed as today
     c. IF review.action == "revise"  → replace remaining queue with revised steps
     d. IF review.action == "retry"   → re-queue current step with refined prompt
     e. IF review.action == "skip"    → drain remaining queue
  3. Queue next messages in chain                               [UNCHANGED]
```

The review is **opt-in and lightweight**. For direct chat (single agent), it is skipped entirely. For multi-step plans, it only fires between steps. It uses a fast model (e.g., gpt-4o-mini) with a small prompt.

### 7.3 `RoomCoordinatorService` (Synthesis Phase)

The current `on_room_user_message_completed` is replaced by the Supervisor's synthesis.

```
BEFORE:
  RoomCoordinatorService.on_room_user_message_completed()
    ├── Collect all agent messages (BFS)
    ├── If 2+ responses: summarize_agent_responses() with "debate" or "non_debate" mode
    └── Emit summary as pseudo-agent message

AFTER:
  supervisor_service.on_execution_completed()
    ├── Collect all agent results (same BFS)
    ├── If 2+ responses: synthesize_results() using plan.synthesis_instruction   [NEW]
    └── Emit summary as pseudo-agent message                                     [SAME]
```

The synthesis is now **guided** by `synthesis_instruction` from the original plan, so the Supervisor's summary is coherent with its original intent.

### 7.4 Inter-Agent Context Injection

When preparing an agent's task prompt in `RoomServices.process_agent_message`, inject room awareness:

```python
# NEW: Add to the agent's context (in build_context_for_agent or process_agent_message)
room_awareness = (
    f"\n[Room Context]\n"
    f"You are working in a team with these other agents:\n"
    + "\n".join(
        f"- {name}: {desc}" 
        for agent_id, name, desc in agent_profiles
        if agent_id != current_agent_id
    )
    + f"\nYour specific role in this task: {step.task_description}\n"
)
```

This is the simplest change that gives agents awareness of each other. It requires no protocol changes -- just prompt engineering.

---

## 8. Handling Edge Cases

### 8.1 Push Notification Agents

Push notification agents cause the queue to PAUSE. The Supervisor plan is serialized as part of `pending_continuation`:

```python
# In _save_queue_continuation, add:
continuation_data = {
    "remaining_queue": serialized_queue,
    "room_id": room_id,
    "user_message_id": user_message_id,
    "request_user_id": request_user_id,
    "supervisor_plan": plan.model_dump(mode="json"),  # NEW
    "completed_step_results": step_results,             # NEW
    ...
}
```

When the webhook resumes the queue (`resume_queue_from_continuation`), the Supervisor plan and results are restored. The review step runs normally on the push notification agent's result.

No changes to the push notification flow itself. The queue still pauses and resumes exactly as today.

### 8.2 Cancellation

Cancellation checks remain in the queue loop. If the user cancels mid-execution:
- Queue drains as today (QueueResult.CANCELED)
- Supervisor synthesis is skipped (no results to synthesize)
- No change from current behavior

### 8.3 Rate Limiting

Rate limits are checked per-agent in the queue loop, before processing. If rate-limited:
- Supervisor review is not triggered (agent never ran)
- Queue returns CANCELED as today
- No change needed

### 8.4 Direct Chat Fast-Path

The Supervisor is **never invoked** for direct chat:

```python
# In send_message_to_room:
direct_chat = not is_debate_mode and len(selected_agent_set) == 1

if direct_chat:
    # Existing fast-path: skip ALL LLM routing, no Supervisor involvement
    ...
```

This preserves the zero-overhead guarantee for the most common case.

### 8.5 @Mention Routing

@mentions bypass the Supervisor entirely, using the existing deterministic fan-out in `_handle_mentions_flow`. No change.

### 8.6 Supervisor LLM Failure

If the Supervisor LLM call fails (API error, timeout, malformed JSON):

```python
async def create_plan(self, ...) -> SupervisorPlan:
    try:
        plan = await self._call_supervisor_llm(...)
        return SupervisorPlan.model_validate_json(plan)
    except Exception:
        logger.warning("Supervisor planning failed, falling back to legacy parser")
        # Fall back to existing parse_user_message_by_llm
        parsed = await self.openai_service.parse_user_message_by_llm(...)
        return self._convert_parsed_result_to_plan(parsed)
```

The legacy parser becomes the fallback, ensuring the system never fails due to Supervisor issues.

Similarly, if `review_step` fails, default to `action="continue"` (proceed as planned).

---

## 9. Supervisor Review: When to Skip

The review step adds latency (~300-800ms per LLM call). The implemented `_should_review_step` method in `RoomSupervisorService` is more conservative than originally designed — it skips review unless a downstream step explicitly lists this step in its `context_from_steps`:

```python
def _should_review_step(
    self,
    plan: SupervisorPlan,
    completed_step: SupervisorStep,
) -> bool:
    total_steps = len(plan.steps)
    
    # Skip review for single-step plans
    if total_steps <= 1:
        return False
    
    # Skip review for the last step
    step_index = next(
        (i for i, s in enumerate(plan.steps) if s.step_id == completed_step.step_id), -1
    )
    if step_index >= total_steps - 1:
        return False
    
    # Only review if a downstream step depends on this step's output
    has_dependencies = any(
        completed_step.step_id in s.context_from_steps
        for s in plan.steps[step_index + 1:]
    )
    return has_dependencies
```

**Key difference from the original design**: The implementation skips the "result quality" heuristic (`len(result_text) > 50 and "error" not in result_text.lower()`). Reviews are only triggered when downstream steps explicitly declare `context_from_steps` dependency — not based on result content. This means `revise` and `retry` actions are not triggered for poor-quality results in parallel or independent plans. There is also a dead public `should_review_step` method (with the original heuristic signature) that is unused — the private `_should_review_step` (called from `QueueExecutor`) is the active one.

This means the review LLM call is only made when:
1. It's a multi-step plan, AND
2. It's not the last step, AND
3. A later step explicitly names this step in its `context_from_steps`

---

## 10. Migration Plan

### Phase 1: Add `RoomSupervisorService` (Non-breaking) ✅ DONE

1. ✅ Created `services/room_supervisor_service.py` with `create_plan()`, `review_step()`, `synthesize_results()`
2. ✅ Added `SupervisorPlan`, `SupervisorStep`, `SupervisorReview`, `AgentProfile`, `RoomConfig`, `StepResult` models to `models/supervisor.py`
3. ✅ Added room-level flag: `room.extend_info.use_supervisor = true/false`
4. ✅ Added `SupervisorStrategy` and `ReviewAction` as `StrEnum` types

### Phase 2: Wire in Planning (Replace `parse_user_message_by_llm`) ✅ DONE

1. ✅ Added `RoomServices._parse_with_supervisor()` — checks `use_supervisor` flag, calls `supervisor_service.create_plan()`, stores plan in `user_message.extend_info.supervisor_plan`, calls `_generate_agent_messages_from_plan()`
2. ✅ Added `RoomServices._generate_agent_messages_from_plan()` — converts `SupervisorPlan.steps` to `RoomAgentMessage` records
3. ✅ Added `OpenAIService.call_supervisor_llm_json()` and `call_supervisor_llm_text()` — use `SUPERVISOR_MODEL` or `LEAD_AI_MODEL` env var (default: `gpt-4o-mini`)
4. ✅ Direct chat fast-path preserved: `_parse_with_supervisor` skips Supervisor for single-agent non-debate rooms
5. ✅ A/B flag: rooms with `extend_info.use_supervisor = true` use Supervisor; all others use legacy parser

### Phase 3: Wire in Review (Post-step evaluation) ✅ DONE

1. ✅ Moved queue execution from `RoomMessageCenter` into `QueueExecutor.process_queue()` which now accepts `supervisor_plan`, `completed_step_results`, and `step_retry_counts`
2. ✅ `QueueExecutor._supervisor_review_step()` runs after each successful step; handles CONTINUE / REVISE / RETRY / SKIP
3. ✅ `_handle_revise_action()` clears remaining queue, updates `supervisor_plan.steps` in-place, generates new `RoomAgentMessage` records
4. ✅ `_handle_retry_action()` prepends refined-prompt message to queue front; enforces `max_retries`
5. ✅ `QueueExecutor._save_queue_continuation()` and `resume_queue_from_continuation()` serialize/restore Supervisor state for push notification pauses
6. ✅ `process_queue()` returns `QueueProcessingResult` (replaces bare `QueueResult`) carrying `step_results` and `supervisor_plan` for synthesis

### Phase 4: Wire in Synthesis (Replace `RoomCoordinatorService`) ✅ DONE

1. ✅ `RoomMessageCenter._handle_completion()` — if Supervisor plan + 2+ results: calls `synthesize_results()` and emits via `room_coordinator_service.emit_synthesis_message()`; else falls back to legacy `on_room_user_message_completed()`
2. ✅ `RoomCoordinatorService.emit_synthesis_message()` added as public API for external callers
3. ⚠️ `RoomCoordinatorService` and `DebateService` are **not yet deprecated** — they remain active for non-Supervisor rooms and as fallback

### Phase 5: Inter-Agent Awareness ✅ DONE

1. ✅ Added `RoomServices._build_room_awareness()` — builds `[Room Context]` block listing peer agents and this agent's specific role
2. ✅ `agent_profiles` stored in `extend_info` at plan generation time to avoid repeated DB lookups per agent call
3. ✅ Room awareness injected in all three context-building paths in `process_agent_message` (structured MemoryContent, raw string, empty)
4. ✅ `build_context_for_agent()` in `context_utils.py` accepts `room_awareness` parameter and injects it before `[Current request]`
5. ✅ Awareness skipped for direct chat (`task_content is None`) and for rooms without `use_supervisor` flag

---

## 11. Cost Analysis

### LLM Calls Per Request

| Scenario | Current System | With Supervisor |
|---|---|---|
| Direct chat (1 agent) | 0 LLM routing calls | 0 (fast-path, unchanged) |
| @mention | 0 | 0 (bypassed, unchanged) |
| Multi-agent (2 agents) | 1 (parse_user_message_by_llm) + 1 (summary) = 2 | 1 (plan) + 0-1 (review) + 1 (synthesis) = 2-3 |
| Multi-agent (3 agents, sequential) | 1 (parse) + 1 (summary) = 2 | 1 (plan) + 0-2 (reviews) + 1 (synthesis) = 2-4 |

The additional cost is 0-2 review calls per multi-agent request, using a fast model (gpt-4o-mini) with a small prompt (~500 tokens). At $0.15/1M input tokens, this is ~$0.0001 per review call -- negligible.

### Latency Impact

| Phase | Estimated Latency | When It Runs |
|---|---|---|
| Planning | ~500-1000ms | Once per multi-agent request (replaces existing LLM parse) |
| Review | ~300-500ms | Only between dependent steps, skipped for simple cases |
| Synthesis | ~500-1000ms | Once at end (replaces existing summary call) |

Net impact for the common multi-agent case: +0-1000ms total, spread across the execution. For direct chat: zero impact.

---

## 12. Observability

### Structured Logging

Every Supervisor decision is logged with structured data:

```python
logger.info(
    "Supervisor plan created",
    extra={
        "room_id": room_id,
        "user_message_id": user_message_id,
        "strategy": plan.strategy,
        "num_steps": len(plan.steps),
        "agents": [s.agent_id for s in plan.steps],
        "reasoning": plan.reasoning,
    },
)

logger.info(
    "Supervisor review completed",
    extra={
        "room_id": room_id,
        "step_id": step.step_id,
        "agent_id": step.agent_id,
        "action": review.action,
        "reasoning": review.reasoning,
    },
)
```

### Plan Stored on User Message

The full `SupervisorPlan` is stored in `user_message.extend_info.supervisor_plan`, so it can be inspected in the database for debugging. Each review's decision is appended to `extend_info.supervisor_reviews`.

---

## 13. What This Does NOT Change

| Component | Status |
|---|---|
| `Room` model | Unchanged |
| `RoomAgentMessage` model | Unchanged |
| `RoomUserMessage` model | Unchanged (plan stored in extend_info) |
| Message queue execution loop | Unchanged (except optional review hook) |
| SSE streaming to frontend | Unchanged |
| Push notification / webhook flow | Unchanged (plan serialized in continuation) |
| `a2a_service` (agent communication) | Unchanged |
| `rate_limit_service` | Unchanged |
| `stale_task_checker` | Unchanged |
| Frontend (`useRoomWebhook.ts`) | Unchanged |
| Room memory (`RoomMemoryService`) | Unchanged |
| `DebateService` | Still active (not yet deprecated; used for non-Supervisor rooms) |
| `RoomCoordinatorService` | Still active (fallback for non-Supervisor rooms and Supervisor synthesis failures) |

---

## 14. Known Issues and Gaps

These were discovered during PR #83 review and remain open for future work.

---

### 14.1 `context_from_steps` Not Consumed at Execution Time

**Location**: `RoomServices._generate_agent_messages_from_plan()` (marked with `TODO`)

**Problem**: The Supervisor LLM populates `step.context_from_steps` to indicate which prior step results a downstream agent needs. However, `_generate_agent_messages_from_plan` runs at planning time (before any agent has responded), so completed results are not yet available. The field is stored in the plan but never injected into the downstream agent's prompt.

**Impact**: Sequential plans where Agent B explicitly needs Agent A's output must rely on `context_utils.build_context_for_agent` reading room memory. The `context_from_steps` mechanism provides no additional value beyond what room memory already supplies, and the Supervisor's intent to inject specific prior results is silently lost.

**Fix**: Inject referenced step results just before each message is dispatched to the agent. The right place is `QueueExecutor._supervisor_review_step`, which already has access to the accumulated `step_results` list. After recording the current step's result, scan the *next* queued message to check if its corresponding `SupervisorStep.context_from_steps` references any completed steps. If so, augment the message's `task_content` or `extend_info` with those results before the loop advances.

Concretely:

1. After appending the current `StepResult` to `step_results`, find the `SupervisorStep` for the *next* message in the queue (using the same `_find_step_for_message` lookup, applied to `message_queue[0]`).
2. For each `step_id` listed in that step's `context_from_steps`, look up the matching entry in `step_results`.
3. Prepend a `[Prior Results]` block to that message's `task_content`:

```python
prior_context = "\n\n".join(
    f"[Result from {r.agent_name}]\n{r.response_text}"
    for r in step_results
    if r.step_id in next_step.context_from_steps
)
if prior_context:
    next_message.task_content = (
        f"[Prior Results]\n{prior_context}\n\n"
        f"[Your Task]\n{next_message.task_content}"
    )
    await database_service.update_room_agent_message(next_message)
```

This keeps `context_from_steps` semantics intact without changing the planning API or the message model.

---

### 14.2 Legacy Fallback Does Not Produce `SupervisorPlan`

**Location**: `RoomServices._parse_with_supervisor()` — both `SupervisorPlanningError` catch block and the "plan produced no messages" fallback

**Problem**: When the Supervisor LLM fails, `_parse_with_supervisor` silently falls back to `parse_user_message()` (legacy path). No `SupervisorPlan` is stored on the user message. As a result, `RoomMessageCenter` finds no `supervisor_plan` in `extend_info`, disables Supervisor review entirely, and falls back to legacy `RoomCoordinatorService` synthesis — even for rooms with `use_supervisor = true`.

`convert_parsed_result_to_plan()` exists on `RoomSupervisorService` precisely to bridge this gap, but is currently unused (noted with a `TODO` comment in the service).

**Impact**: Supervisor review and guided synthesis are silently skipped after any planning failure, making the behaviour of a `use_supervisor` room indistinguishable from a legacy room during an outage.

**Fix**: Wire `convert_parsed_result_to_plan()` into both fallback paths inside `_parse_with_supervisor`:

```python
except SupervisorPlanningError as e:
    logger.warning("Supervisor planning failed, falling back to legacy parser: %s", e)
    parse_result = await self.parse_user_message(...)   # existing fallback

    # NEW: convert legacy result into a SupervisorPlan so review/synthesis still work
    if parse_result.success and parse_result.parsed_result:
        fallback_plan = room_supervisor_service.convert_parsed_result_to_plan(
            parse_result.parsed_result
        )
        if user_message.extend_info is None:
            user_message.extend_info = {}
        user_message.extend_info["supervisor_plan"] = fallback_plan.model_dump(mode="json")
        user_message.extend_info["supervisor_plan_source"] = "legacy_fallback"
        await self.database_service.update_room_user_message(user_message)

    return parse_result
```

This requires `parse_user_message` to expose the raw `parsed_result` dict on `ParseResult` (it currently only returns `success` / `canceled`). The same pattern applies to the "plan produced no messages" fallback. Adding `supervisor_plan_source` to `extend_info` allows observability tooling to distinguish native Supervisor plans from converted fallbacks.

---

### 14.3 Dead Public `should_review_step` Method

**Location**: `RoomSupervisorService.should_review_step()` (lines ~475–521)

**Problem**: There are two separate methods on `RoomSupervisorService`:

- `_should_review_step(plan, completed_step)` — private, called by `QueueExecutor`. Uses a dependency-only heuristic: only reviews if a later step lists this step in its `context_from_steps`.
- `should_review_step(plan, step_index, total_steps, result_text)` — public, matches the original design spec, includes a result-quality heuristic (skip for long non-error responses with no dependencies). **Never called anywhere.**

The result-quality heuristic in the dead method would also skip reviews for short or error-containing responses when dependencies exist — which is the opposite of what's needed. The current private method always reviews when dependencies exist, regardless of result quality.

**Fix**: Delete the public `should_review_step` method. Merge the result-quality heuristic from it into `_should_review_step` as an *additional* short-circuit (not a gate on the dependency check), so the final logic is:

```python
def _should_review_step(self, plan, completed_step) -> bool:
    # ... existing single-step and last-step short-circuits ...

    step_index = ...  # find index of completed_step

    # Check if downstream steps depend on this step's output
    has_dependencies = any(
        completed_step.step_id in s.context_from_steps
        for s in plan.steps[step_index + 1:]
    )

    # Always review if downstream steps depend on this step
    if has_dependencies:
        return True

    # For independent steps, skip review to reduce latency
    return False
```

The result-quality heuristic (skip for long non-error responses) is intentionally not added here — that heuristic belongs in the `review_step` *prompt* (instruct the LLM: "for clearly successful results with no downstream dependencies, always return CONTINUE") rather than as a pre-LLM gate.

---

### 14.4 `supervisor_reviews` Not Persisted

**Location**: `QueueExecutor._supervisor_review_step()` — after receiving the `SupervisorReview` result

**Problem**: The design doc (Section 12) specified that each review decision would be appended to `user_message.extend_info.supervisor_reviews` for database-level debugging. The implementation only logs via `logger.info`. This makes it impossible to audit which steps triggered REVISE or RETRY in production without log correlation.

**Fix**: After a non-CONTINUE review action, load the user message, append the review to `extend_info["supervisor_reviews"]`, and write it back. To avoid a DB round-trip on every step, only persist for actionable reviews (REVISE, RETRY, SKIP):

```python
# In QueueExecutor._supervisor_review_step, after receiving `review`:
if review.action != ReviewAction.CONTINUE:
    await self._persist_review(
        user_message_id=user_message_id,
        step_id=current_step.step_id,
        review=review,
    )

async def _persist_review(
    self,
    user_message_id: str,
    step_id: str,
    review: SupervisorReview,
) -> None:
    user_message = await self.database_service.get_room_user_message_by_message_id(
        user_message_id
    )
    if not user_message:
        return
    if not isinstance(user_message.extend_info, dict):
        user_message.extend_info = {}
    reviews = user_message.extend_info.setdefault("supervisor_reviews", [])
    reviews.append({
        "step_id": step_id,
        "action": review.action,
        "reasoning": review.reasoning,
        "timestamp": utcnow().isoformat(),
    })
    await self.database_service.update_room_user_message_by_message_id(
        user_message_id, user_message
    )
```

CONTINUE reviews are not persisted — they are logged but do not need a DB write since they leave the plan unchanged.

---

### 14.5 `create_plan` Logging Missing `room_id` / `user_message_id`

**Location**: `RoomSupervisorService.create_plan()` — the `logger.info("Supervisor plan created", ...)` call

**Problem**: The design doc specified `room_id` and `user_message_id` in the structured log entry for plan creation. These are missing because `create_plan` does not receive them. Without them, plan logs cannot be joined to specific user messages in log aggregation tools.

**Fix**: The simplest approach is to log them at the call site in `_parse_with_supervisor` rather than inside `create_plan` — no API change needed:

```python
# In RoomServices._parse_with_supervisor, after create_plan() returns:
plan = await room_supervisor_service.create_plan(...)
logger.info(
    "Supervisor plan created",
    extra={
        "room_id": room.room_id,
        "user_message_id": user_message.message_id,
        "plan_id": plan.plan_id,
        "strategy": plan.strategy,
        "num_steps": len(plan.steps),
    },
)
```

The existing `logger.info` inside `create_plan` can be demoted to `logger.debug` to avoid double-logging. Alternatively, add optional `room_id: str | None = None` and `user_message_id: str | None = None` parameters to `create_plan` and pass them through to the existing log call — this keeps all plan-related logging in one place.

---

### 14.6 RETRY Does Not Remove the Prior Failed StepResult

**Location**: `QueueExecutor._supervisor_review_step()` — the block that appends `StepResult` and then checks for RETRY

**Problem**: The `StepResult` for the current step is appended to `step_results` unconditionally before the Supervisor review is called. If the review returns RETRY, `step_results` already contains a "success=True" entry for the step that is about to be re-run. When the retry completes and another entry is appended, the synthesis prompt receives two results for the same `step_id`, potentially confusing the Supervisor.

The issue is compounded because the initial append sets `success=True` even though the Supervisor is requesting a retry, implying the result was unsatisfactory.

**Fix**: Defer the `StepResult` append until after the review decision. Record the result tentatively, then commit or discard based on the action:

```python
# Build the result but don't append yet
pending_result = StepResult(
    step_id=current_step.step_id,
    agent_id=...,
    ...
    response_text=response_text or "",
    success=True,
)

# ... call supervisor review ...

if review.action == ReviewAction.RETRY:
    # Discard this result — it will be re-recorded after the retry completes
    pending_result = None

if pending_result:
    step_results.append(pending_result)
```

If the RETRY itself is later superseded (e.g., retries exhausted and CONTINUE is forced), the fallback path should append a result marked `success=False` with `error_message` set to the retry reasoning so the synthesis prompt has accurate status.

---

### 14.7 `_handle_revise_action` Cancels Old Messages Without Notifying Frontend

**Location**: `QueueExecutor._handle_revise_action()` — the `TaskState.canceled, notify=False` loop

**Problem**: When the Supervisor revises the plan, existing pending queue messages are canceled with `notify=False`. If the frontend tracks step progress via `step_number`/`total_steps` fields on agent messages (e.g., to render "Step 2 of 3…" indicators), those pending steps will appear to hang indefinitely because no terminal event is sent for them.

**Fix**: There are two options depending on frontend behaviour:

**Option A (preferred if frontend ignores canceled steps)**: Send `notify=True` so the frontend receives a CANCELED task state event. The frontend already handles task cancellations (for user-initiated cancels), so superseded steps should render as canceled rather than pending. No frontend changes needed.

```python
# In _handle_revise_action:
for msg in message_queue:
    await self.tsm.transition_task(
        msg, TaskState.canceled, persist=True, notify=True  # changed from False
    )
```

**Option B (if frontend must not show canceled steps)**: Instead of canceling with notify=False, replace the canceled steps' DB records with a `TaskState.skipped` state (if such a state exists or can be added) that the frontend knows to hide silently.

The simpler fix is Option A. If the canceled step bubbles appear in the UI, a frontend-side filter on task state can suppress them.

---

### 14.8 `_find_step_for_message` Uses Position Index, Not `step_id`

**Location**: `QueueExecutor._find_step_for_message()`

**Problem**: The method resolves a `RoomAgentMessage` to its `SupervisorStep` by treating `message.step_number - 1` as the index into `supervisor_plan.steps`. This breaks after a REVISE action, which mutates `supervisor_plan.steps` in-place (replacing tail steps with revised ones). A message created before the revision carries the original `step_number`, but after the revision that index may point to a completely different step. The wrong `SupervisorStep` is then passed to `review_step`, giving the Supervisor incorrect context.

**Fix**: Store the `step_id` in `RoomAgentMessage.extend_info` when the message is created, and match by `step_id` in `_find_step_for_message`.

**Step 1** — set `step_id` at message creation in `_generate_agent_messages_from_plan` and `_handle_revise_action` / `_handle_retry_action`:

```python
# In _generate_agent_messages_from_plan, when building extend_info:
step_extend_info = dict(extend_info or {})
step_extend_info["supervisor_step_id"] = step.step_id

agent_message = self._generate_new_agent_message(
    ...,
    extend_info=step_extend_info,
)
```

**Step 2** — update `_find_step_for_message` to prefer `extend_info["supervisor_step_id"]`:

```python
def _find_step_for_message(
    self,
    plan: SupervisorPlan,
    message: RoomAgentMessage,
) -> SupervisorStep | None:
    # Prefer explicit step_id stored in extend_info (set at creation time)
    step_id = None
    if isinstance(message.extend_info, dict):
        step_id = message.extend_info.get("supervisor_step_id")

    if step_id:
        return next((s for s in plan.steps if s.step_id == step_id), None)

    # Fallback: positional match (pre-fix messages or legacy paths)
    if message.step_number is not None:
        step_index = message.step_number - 1
        if 0 <= step_index < len(plan.steps):
            return plan.steps[step_index]

    return None
```

The fallback preserves backward compatibility for messages created before this fix is deployed.

---

### 14.9 NEW BUG — REVISE / RETRY Actions Conflict with `_queue_next_messages`

**Discovered during fix review. This is a pre-existing bug in PR #83, not just a fix concern.**

**Location**: `QueueExecutor.process_queue()` — the ordering of `_supervisor_review_step` → `_queue_next_messages`

**Problem**: Both `_handle_revise_action` and `_handle_retry_action` return `ReviewAction.CONTINUE` to the caller. After the review hook returns, `process_queue` always calls `_queue_next_messages(current_message, message_queue, room_id)` for non-direct-chat scenarios. This fetches all DB messages whose `related_message_id == current_message.message_id` and appends them to the queue.

For REVISE: The revised steps are already in the queue after `_handle_revise_action`. Then `_queue_next_messages` adds the *original* dependent messages from the DB (which were created at planning time but whose tasks were just canceled by the revise handler). These zombie messages re-enter the queue, producing duplicate or contradictory work.

For RETRY: The retry message is prepended to the queue front. Then `_queue_next_messages` adds the original dependent messages from DB. These dependents should not run until the retry completes, but they are now queued alongside (or after) the retry message.

**Impact**: Any REVISE or RETRY action in a sequential plan will corrupt the queue with stale messages. This makes both actions unreliable in production for plans with `depends_on` relationships.

**Fix**: Return a distinct action from `_supervisor_review_step` for REVISE and RETRY (e.g., `ReviewAction.REVISE` and `ReviewAction.RETRY` directly) and skip `_queue_next_messages` for those actions in the main loop:

```python
# In process_queue, after the review hook:
if review_action == ReviewAction.SKIP:
    message_queue.clear()
    last_popped.clear()
    break

# Skip _queue_next_messages when the Supervisor already modified the queue
if review_action in (ReviewAction.REVISE, ReviewAction.RETRY):
    continue  # next iteration of while loop

# Normal flow: queue dependent messages
if not is_direct_chat:
    await self._queue_next_messages(current_message, message_queue, room_id)
```

Update `_handle_revise_action` and `_handle_retry_action` to return their respective `ReviewAction` instead of `CONTINUE`.

---

### 14.10 Risks and Architectural Concerns with Proposed Fixes

This subsection reviews each proposed fix from 14.1–14.8 for implementation risks, correctness concerns, and architectural trade-offs.

#### Fix 14.1 — `context_from_steps` injection: timing mismatch with `_queue_next_messages`

**Risk**: The proposed fix places the injection inside `_supervisor_review_step`, but at that point the *next* message may not be in the queue yet. For sequential plans, the dependent message is only loaded by `_queue_next_messages` (which runs *after* the review hook). So `message_queue[0]` may not be the right message — it could be a parallel sibling or empty.

**Revised approach**: Instead of injecting inside the review hook, inject inside `_queue_next_messages` (or immediately after it). After `_queue_next_messages` appends a new message to the queue, check whether its corresponding `SupervisorStep.context_from_steps` lists any step IDs present in `step_results`. If so, mutate its `task_content` with the prior results and persist the change.

This requires `_queue_next_messages` to receive (or have access to) `supervisor_plan` and `step_results`. These are already available in the `process_queue` scope and can be passed down.

**Architectural note**: Mutating a message's `task_content` in the DB after initial creation couples the queue chaining logic to the Supervisor. A cleaner alternative is to inject context *at dispatch time* inside `_process_single_message` (where `ResponseProcessor` builds the agent's context). This keeps DB messages immutable and injects context as a transient prompt layer. However, that requires threading `step_results` deeper into the dispatch chain.

#### Fix 14.2 — Legacy fallback plan conversion: `ParseResult` does not carry `parsed_result`

**Risk**: The proposed fix assumes `parse_user_message` exposes `parsed_result` on `ParseResult`. It does not — `ParseResult` is a simple `(success, canceled)` dataclass, and the raw `parsed_result` dict is consumed internally inside `parse_user_message`.

**Revised approach**: Two options:

1. **Extend `ParseResult`** to carry an optional `parsed_result: dict | None` field, and return it from `parse_user_message`. This is a small model change, but it leaks a legacy internal format through the return type. The existing callers (only `send_message_to_room` and `_parse_with_supervisor`) would need to tolerate the new field.

2. **Refactor `_parse_with_supervisor` to call `parse_user_message_by_llm` directly** (instead of delegating to `parse_user_message`) and then call both `_generate_agent_messages_based_on_parsed_result` and `convert_parsed_result_to_plan` on the raw result. This avoids changing `ParseResult` but duplicates some logic from `parse_user_message`.

Option 1 is cleaner. The change to `ParseResult` is backward-compatible (new field is optional/None by default).

#### Fix 14.4 — Persisting reviews: DB write on hot path adds latency and failure modes

**Risk**: The proposed fix calls `get_room_user_message_by_message_id` + `update_room_user_message_by_message_id` inside the queue loop. This adds a DB round-trip (~5-20ms) per non-CONTINUE review. More importantly, if the update fails (network issue, write conflict), the review is lost but the queue continues — which is acceptable for logging but creates silent data gaps.

**Mitigation**: The fix already limits persistence to non-CONTINUE actions. To further reduce risk:
- Wrap the persist in a fire-and-forget `asyncio.create_task` so it does not block the queue loop.
- Accept that review persistence is best-effort — the `logger.info` call is the authoritative record; DB persistence is for convenience queries only.

**Architectural note**: The user message's `extend_info` is also written by `_parse_with_supervisor` (to store the plan) and could be overwritten if another concurrent process modifies the same document. Since rooms are single-writer (one queue loop per user message), this is unlikely but worth noting for future parallel execution.

#### Fix 14.5 — Logging at call site: minor but introduces logging duplication

**Risk**: Logging at the `_parse_with_supervisor` call site while keeping the existing `logger.info` inside `create_plan` creates duplicate log entries for every successful plan. The proposed mitigation (demote inner log to `debug`) is correct but easy to forget. No functional risk.

#### Fix 14.6 — Deferred StepResult append: interaction with REVISE and `completed_step_ids`

**Risk**: The current code computes `completed_step_ids` from `step_results` immediately after appending. If the append is deferred until after the review, then `completed_step_ids` will not include the current step when computing `remaining_steps` for the review. This means the just-completed step will appear in `remaining_steps` — which the Supervisor review prompt would see as an unfinished step, confusing the LLM.

**Revised approach**: Still record the result eagerly to keep `completed_step_ids` correct, but *remove* it if RETRY is triggered:

```python
# Record eagerly
step_results.append(pending_result)

# ... run review ...

if review.action == ReviewAction.RETRY:
    # Remove the result we just appended — it will be re-recorded after retry
    step_results.pop()
```

This preserves the correct `completed_step_ids` / `remaining_steps` for the review prompt while still cleaning up duplicates for the RETRY path.

#### Fix 14.7 — Notify=True for canceled steps: `transition_task` requires `ctx` for notification

**Risk**: The proposed fix changes `notify=False` to `notify=True` in `_handle_revise_action`. However, `transition_task` only sends a notification when both `notify=True` *and* `ctx` is provided. The `_handle_revise_action` call passes no `ctx`, so `notify=True` alone is a **no-op** — no SSE event is actually sent.

**Revised approach**: To actually notify the frontend, the code must either:

1. **Construct a `ProcessingContext`** for each canceled message and pass it to `transition_task`. This requires resolving `agent_card` and other fields for each message, adding non-trivial complexity and DB lookups.

2. **Call `notification_service.send_task_update` directly** after the `transition_task` call, bypassing the `ctx` requirement:

```python
for msg in message_queue:
    await self.tsm.transition_task(msg, TaskState.canceled, persist=True, notify=False)
    await self.notification_service.send_task_update(
        room_id=room_id,
        message_id=msg.message_id,
        status=TaskState.canceled,
        step_number=msg.step_number,
        total_steps=msg.total_steps,
        task_content=msg.task_content,
    )
```

Option 2 is simpler and more explicit. Note that the notification service requires `notification_service` to be available on `QueueExecutor` — it currently is not (it's accessed via `TaskStateManager` which holds a reference). Either inject it or access it through `self.tsm.notification_service`.

**Additional risk**: Notifying the frontend of canceled steps may cause UI flicker if the frontend renders a "canceled" state for steps that the user never saw start. This should be tested with the frontend before enabling.

#### Fix 14.8 — Step ID matching: `extend_info` mutation and extend_info structure assumptions

**Risk**: The proposed fix stores `supervisor_step_id` in `RoomAgentMessage.extend_info`. However, `extend_info` is typed as `Any | None` and is currently a shared dict used for `allowed_agent_ids`, `target_group`, `is_direct_chat`, and `agent_profiles`. Adding another field is fine structurally, but:

- `_handle_revise_action` and `_handle_retry_action` both call `_generate_new_agent_message` without passing `extend_info`, so revised/retried messages would not get the `supervisor_step_id` unless those methods are also updated.
- The fix must be applied in three places: `_generate_agent_messages_from_plan`, `_handle_revise_action`, and `_handle_retry_action`.

**Mitigation**: This is straightforward — just ensure all three message-creation paths set `supervisor_step_id`. The fallback to positional matching handles any messages missed.

---

## 15. Future Extensions

Once the Supervisor pattern is stable, it enables:

1. **Agent-initiated transfer**: An agent can return `{"transfer_to": "agent_id", "reason": "..."}` in its response metadata. The Supervisor review detects this and revises the plan to route to the requested agent.

2. **Supervisor memory**: Track which routing decisions worked well (agent A succeeded, agent B failed for this type of task) and use this history to improve future plans.

3. **Multi-round Supervisor**: For complex workflows, the Supervisor can create a plan that includes "checkpoints" where it re-evaluates the full situation (not just the last step) and generates a fresh plan for the remaining work.

4. **User-visible plan**: Show the execution plan in the frontend ("I'll ask Research Agent to find the data, then Translation Agent to translate it") so the user knows what's happening.

---

## 16. Summary

The Supervisor Pattern upgrades the existing orchestration from a one-shot pipeline to an intelligent plan-execute-review cycle, while preserving all existing infrastructure. PR #83 implemented all five phases:

- **Planning**: Replaces `parse_user_message_by_llm()` with a smarter, context-aware Supervisor that knows all agents (behind `use_supervisor` room flag)
- **Review**: Adds optional post-step evaluation that can adapt the plan mid-execution (only fires when downstream steps declare `context_from_steps` dependencies)
- **Synthesis**: Replaces disconnected `RoomCoordinatorService` / `DebateService` with plan-aware synthesis for Supervisor-enabled rooms
- **Awareness**: Injects room agent roster into each agent's context via `[Room Context]` block

The key design constraint remains: **the Supervisor only touches the decision layer**. All execution (queue, streaming, push notifications, SSE) remains unchanged. The feature is gated by a room-level `use_supervisor` flag, with the legacy system as a permanent fallback.

Open items for follow-up work are tracked in Section 14 (Known Issues and Gaps).
