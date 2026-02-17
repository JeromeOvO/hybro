# Room Supervisor Pattern Design

**Date**: February 11, 2026
**Status**: Proposal
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

The review step adds latency (~300-800ms per LLM call). It should be skipped when unnecessary:

```python
async def _should_review_step(
    self,
    plan: SupervisorPlan,
    step_index: int,
    total_steps: int,
    result_text: str,
) -> bool:
    """Decide whether to invoke the Supervisor review after a step."""
    
    # Skip review for single-step plans (nothing to adjust)
    if total_steps <= 1:
        return False
    
    # Skip review for the last step (nothing remaining to adjust)
    if step_index >= total_steps - 1:
        return False
    
    # Skip review if result is clearly successful (non-empty, no error markers)
    if result_text and len(result_text) > 50 and "error" not in result_text.lower():
        # Heuristic: substantial result with no error = probably fine
        # Only review if the plan has complex dependencies
        has_dependencies = any(
            s.context_from_steps for s in plan.steps[step_index + 1:]
        )
        if not has_dependencies:
            return False
    
    return True
```

This means the review LLM call is only made when:
1. It's a multi-step plan, AND
2. It's not the last step, AND
3. The result is either empty, short, or contains error signals, OR downstream steps depend on this step's output

---

## 10. Migration Plan

### Phase 1: Add `RoomSupervisorService` (Non-breaking)

1. Create `services/room_supervisor_service.py` with `create_plan()`, `review_step()`, `synthesize_results()`
2. Add `SupervisorPlan`, `SupervisorStep`, `SupervisorReview` models to `models/supervisor.py`
3. Add room-level flag: `room.extend_info.use_supervisor = true/false`
4. No existing code changes yet

### Phase 2: Wire in Planning (Replace `parse_user_message_by_llm`)

1. In `RoomServices.send_message_to_room`, check `use_supervisor` flag
2. If true: call `supervisor_service.create_plan()` instead of `parse_user_message()`
3. Convert `SupervisorPlan` to `RoomAgentMessage` records (new helper method)
4. All downstream queue processing is unchanged
5. A/B test: some rooms use Supervisor, others use legacy parser

### Phase 3: Wire in Review (Post-step evaluation)

1. In `RoomMessageCenter._process_agent_message_queue`, after each step completes:
   - Check if Supervisor plan exists on the user message
   - If yes and `_should_review_step()` returns true, call `review_step()`
   - Apply the review action (continue/revise/retry/skip)
2. Extend `_save_queue_continuation` to include Supervisor state

### Phase 4: Wire in Synthesis (Replace `RoomCoordinatorService`)

1. After queue completes, call `supervisor_service.synthesize_results()` instead of `room_coordinator_service.on_room_user_message_completed()`
2. Use `plan.synthesis_instruction` to guide the synthesis
3. Deprecate `RoomCoordinatorService` and `DebateService`

### Phase 5: Inter-Agent Awareness

1. In `RoomServices.process_agent_message` / `build_context_for_agent`, inject room agent roster
2. Include the agent's specific role from `step.task_description`
3. This is a prompt-only change, no infrastructure work

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

---

## 14. Future Extensions

Once the Supervisor pattern is stable, it enables:

1. **Agent-initiated transfer**: An agent can return `{"transfer_to": "agent_id", "reason": "..."}` in its response metadata. The Supervisor review detects this and revises the plan to route to the requested agent.

2. **Supervisor memory**: Track which routing decisions worked well (agent A succeeded, agent B failed for this type of task) and use this history to improve future plans.

3. **Multi-round Supervisor**: For complex workflows, the Supervisor can create a plan that includes "checkpoints" where it re-evaluates the full situation (not just the last step) and generates a fresh plan for the remaining work.

4. **User-visible plan**: Show the execution plan in the frontend ("I'll ask Research Agent to find the data, then Translation Agent to translate it") so the user knows what's happening.

---

## 15. Summary

The Supervisor Pattern upgrades the existing orchestration from a one-shot pipeline to an intelligent plan-execute-review cycle, while preserving all existing infrastructure:

- **Planning**: Replaces `parse_user_message_by_llm()` with a smarter, context-aware Supervisor that knows all agents
- **Review**: Adds an optional post-step evaluation that can adapt the plan mid-execution
- **Synthesis**: Replaces disconnected `RoomCoordinatorService` / `DebateService` with plan-aware synthesis
- **Awareness**: Injects room agent roster into each agent's context

The key design constraint is: **the Supervisor only touches the decision layer**. All execution (queue, streaming, push notifications, SSE) remains unchanged. This means the feature can be built and deployed incrementally with a room-level feature flag, with the legacy system as a permanent fallback.
