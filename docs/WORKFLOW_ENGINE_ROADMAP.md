# Workflow Engine Roadmap: From Adaptive Supervisor to Durable Workflows

**Date**: February 20, 2026 (updated March 6, 2026)
**Status**: Proposal
**Scope**: Product roadmap for supporting long-running tasks and multi-step workflows
**Prerequisites**: [SUPERVISOR_V2_DESIGN.md](./SUPERVISOR_V2_DESIGN.md), [LONG_RUNNING_TASKS_DESIGN.md](./LONG_RUNNING_TASKS_DESIGN.md)
**A2A Protocol Version:** v0.3 (current SDK: `a2a-sdk >=0.3, <1.0`)

---

## 1. Problem Statement

The V2 Supervisor (SUPERVISOR_V2_DESIGN.md) handles **conversational multi-agent orchestration** well: a user sends a message, 2-4 agents respond in seconds to minutes, and the supervisor synthesizes the result. The existing long-running tasks infrastructure (LONG_RUNNING_TASKS_DESIGN.md) handles **individual A2A agents** that take hours via webhook pause/resume.

Neither system supports **multi-step workflows** — sequences of agent tasks with data dependencies, fan-out over lists, retries, and durable execution spanning hours or days. Users increasingly need this for automation use cases.

### 1.1 Motivating Use Case: Cold Outreach

A user wants to automate cold outreach to content creators:

1. **YouTube Agent** searches for creators matching criteria (e.g., "crypto content, 10k+ subscribers") → returns a list of 50 creators
2. **Contact Agent** finds email addresses for each creator → returns 50 email addresses
3. **Email Agent** composes and sends a personalized email to each creator → returns send/fail status per email

This workflow has characteristics that neither V2 Supervisor nor the current infrastructure handles:

| Requirement | V2 Supervisor | Current A2A Tasks | Gap |
|---|---|---|---|
| Sequential agent chain (YouTube → Contact → Email) | Handles via adaptive loop | N/A (single agent) | None for V2 |
| Fan-out over 50 creators (Contact Agent × 50) | No fan-out primitive; MAX_STEPS=8 | Single task per agent | **Major gap** |
| Structured data passing (list of creators → list of emails) | Unstructured text in LLM prompt, 500-char truncation | N/A | **Major gap** |
| Total duration: 30-60 minutes | Server restart loses in-flight execution | Webhook resume per task | **Major gap** |
| Retry failed emails (10/50 failed → retry those 10) | LLM must parse failure report and re-delegate | N/A | **Moderate gap** |
| Repeatable (run same workflow with different criteria) | Every run requires LLM routing from scratch | N/A | **Moderate gap** |

### 1.2 Other Use Cases

| Use Case | Key Requirements |
|---|---|
| **Multi-source research** | Parallel dispatch to 3-5 search agents, merge results, summarize |
| **Content pipeline** | Research → Draft → Review → Publish (sequential with human approval gate) |
| **Data enrichment** | Take a CSV of leads, fan-out to enrich each with company data, aggregate |
| **Scheduled monitoring** | Every day, check competitor pricing and alert if changes detected |
| **Approval workflows** | Agent drafts proposal → Human approves → Agent sends to client |

### 1.3 Design Principles

1. **The Supervisor is the intelligence layer, not the execution layer.** V2 Supervisor decides *which* agents to call and *in what order* when the path is ambiguous. The workflow engine handles *durable execution*, *fan-out*, and *retry* when the path is known.
2. **Build incrementally on existing infrastructure.** Every phase reuses `AgentMessageProcessor`, SSE streaming, room memory, push notification pause/resume, and MongoDB persistence. No new infrastructure dependencies until proven necessary.
3. **Build vs. buy: build now, adopt later if needed.** A thin purpose-built workflow executor integrated with the agent stack is less work than integrating Temporal for Phases 1-3. Re-evaluate at Phase 5 if workflow complexity or scale demands it.
4. **Workflows are room-scoped.** A workflow belongs to a room and its agents. Workflow execution writes to room memory and streams SSE to room subscribers, just like supervisor execution.

---

## 2. Architecture: Two Execution Tiers

```
                    ┌────────────────────────────────────────────┐
                    │              User sends message              │
                    └──────────────────┬─────────────────────────┘
                                       │
                              ┌────────┴────────┐
                              │  What kind of    │
                              │  request is this? │
                              └──┬─────┬─────┬──┘
                                 │     │     │
                    Direct/      │     │     │  Trigger a
                    @mention     │     │     │  defined workflow
                         │       │     │
                         ▼       │     ▼
                    ┌─────────┐  │  ┌──────────────────────────────┐
                    │ Existing│  │  │      WORKFLOW ENGINE          │
                    │ pipeline│  │  │  (deterministic, durable)     │
                    │ (fast)  │  │  │                               │
                    └─────────┘  │  │  Step 1 → Step 2 → Fan-out   │
                                 │  │       → Fan-in → Step N       │
                                 ▼  │                               │
                    ┌─────────────┐ │  Each step can be:            │
                    │ SUPERVISOR  │ │  • Agent dispatch              │
                    │ V2 LOOP     │ │  • Supervisor sub-execution    │
                    │ (adaptive,  │ │  • Human approval gate         │
                    │  LLM-routed)│ │  • Fan-out over a list         │
                    └─────────────┘ └──────────────────────────────┘
                         │                        │
                         ▼                        ▼
                    ┌─────────────────────────────────────────────┐
                    │         Shared Infrastructure                 │
                    │  AgentMessageProcessor | SSE | Room Memory    │
                    │  Push Notification Resume | MongoDB           │
                    └─────────────────────────────────────────────┘
```

**Tier 1: V2 Supervisor** — LLM-mediated adaptive routing for ambiguous requests. 2-8 steps, seconds to minutes. Non-durable (crash recovery is best-effort).

**Tier 2: Workflow Engine** — Deterministic step execution for defined pipelines. Unlimited steps, minutes to days. Durable (persisted after every step, crash-recoverable).

The two tiers compose: a workflow step can invoke the V2 Supervisor when intelligent routing is needed within a single step.

> **A2A Version Note**: The workflow engine dispatches agent tasks through
> `AgentMessageProcessor.process_single_message`, which uses `a2a.types`
> (v0.3) for message construction and response parsing. The workflow executor
> itself has no direct A2A type dependencies — all protocol-specific concerns
> are encapsulated in the dispatch layer. When A2A v1.0 is adopted, only
> `a2a_service.py`, `ResponseProcessor.py`, and `a2a_helpers.py` need updates;
> the `WorkflowExecutor` requires no changes.

---

## 3. Phased Roadmap

### Phase 0: V2 Supervisor (Current — SUPERVISOR_V2_DESIGN.md)

**Delivers**: Adaptive multi-agent orchestration for conversational use cases.

**Target use cases**: "Ask my room of agents a question and get a synthesized answer."

**Limitations that motivate future phases**:
- No fan-out (MAX_STEPS=8, no same-agent parallelism)
- No structured data passing between agents (response text truncated to 500 chars in LLM context — full text is stored, but the supervisor LLM only sees a preview)
- No repeatable/templated execution
- Every run requires LLM routing from scratch

---

### Phase 1: Robust Long-Running Agent Support

**Goal**: Make V2 Supervisor reliable enough for agents that take minutes to hours, without adding a workflow engine yet.

**Timeline**: Immediately after V2 ships.

#### 1a. Per-Step Trajectory Checkpointing — ✅ IMPLEMENTED

Per-step checkpointing is already in production. `SupervisorExecutor._checkpoint_trajectory()` persists the `SupervisorTrajectory` to `user_message.extend_info["supervisor_trajectory"]` after every DELEGATE step — both pre-dispatch (empty results) and post-dispatch (completed results). Implementation is best-effort: failures are logged but don't abort the loop.

Crash recovery is also in production. `StaleTaskChecker._recover_stuck_supervisor_trajectories()` (in `jobs/stale_task_checker.py`) scans for messages with `extend_info.supervisor_trajectory.status == "running"` older than a configurable threshold, claims them atomically, respects cancellation, and re-triggers `process_room_user_message`. The `SupervisorExecutor.run()` method detects in-flight DELEGATE entries with empty results and re-dispatches them.

**What to build**:

#### 1b. Agent Timeout with Auto-PAUSE

Add a configurable timeout to `_dispatch_targets`. If an agent doesn't respond within the timeout, treat it as PAUSED and serialize the trajectory for webhook resume.

```python
AGENT_DISPATCH_TIMEOUT_SECONDS = int(os.environ.get("AGENT_DISPATCH_TIMEOUT", "120"))

async def dispatch_one(target: DelegateTarget, sub_step: int) -> V2StepResult:
    try:
        result = await asyncio.wait_for(
            self.agent_message_processor.process_single_message(...),
            timeout=AGENT_DISPATCH_TIMEOUT_SECONDS,
        )
        ...
    except asyncio.TimeoutError:
        logger.warning(
            "Agent %s timed out after %ds, treating as PAUSED",
            target.agent_name, AGENT_DISPATCH_TIMEOUT_SECONDS,
        )
        return V2StepResult(
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
```

This converts any slow agent into a long-running agent automatically, without requiring the agent itself to implement the PAUSED pattern. When the agent eventually responds, the existing webhook/stale-task-checker infrastructure picks it up.

#### 1c. Progress Reporting via SSE

The frontend currently shows per-agent progress via `task_submitted` and `task_update` SSE events — these render as status cards with spinners and elapsed timers. A dedicated `supervisor_progress` event could add step-level visibility ("Step 2 of 4: Delegating to Contact Agent..."), but this is a UX enhancement, not a gap.

If implemented, the event would be sent via `sse_manager.broadcast_to_room()`:

```python
await self.sse_manager.broadcast_to_room(
    room_id=room_id,
    message={
        "type": "supervisor_progress",
        "data": {
            "step_number": step_number + 1,
            "action": action.action,
            "agent_names": [t.agent_name for t in action.targets],
            "message": f"Delegating to {', '.join(t.agent_name for t in action.targets)}...",
        },
    },
)
```

The frontend would need a new handler case in `handleSSEMessage` (`useRoomWebhook.ts`) and a UI component for step-level progress.

**What Phase 1 unlocks**: Individual agents can reliably take minutes to hours. Users see progress during multi-step execution.

**What Phase 1 does NOT solve**: Fan-out, batch operations, repeatable templates, multi-hour pipelines with many steps.

---

### Phase 2: User-Defined Workflow Templates

**Goal**: Move from "chat with agents" to "automate things with agents." Users define repeatable multi-step pipelines that execute deterministically without LLM routing.

**Timeline**: After Phase 1 is stable.

#### 2.0 Relationship to Existing MetaTask Workflow System

The codebase already has a complete MetaTask-based workflow system:

- **Backend**: `modules/WorkflowCenter.py` (~1195 lines) — task decomposition, agent assignment, sequential execution, result summarization. API at `api/orchestration_center.py` (7 endpoints under `/orchestrationCenter/`).
- **Frontend**: `src/components/workflow-message.tsx`, `workflow-container.tsx`, `src/hooks/useWorkflow.ts` — multi-stage UI (DECOMPOSED → AGENTS_ASSIGNED → RUNNING → COMPLETED), uses polling-based status checking.
- **Models**: `models/task.py` — `MetaTask` and `BaseTask` models.

**Migration strategy**: The new `WorkflowExecutor` (Phase 2) supersedes `WorkflowCenter` for user-defined pipelines. `WorkflowCenter` remains for LLM-decomposed workflows until Phase 4 (Supervisor as workflow step) makes it redundant. The existing frontend workflow components will be replaced with SSE-driven components that receive `workflow_progress` events.

The existing `/orchestrationCenter/` API surface coexists with the new `/api/rooms/{room_id}/workflows` endpoints. Deprecation of the old endpoints happens after the new system has real users.

> **NOTE on API conventions**: The existing backend uses flat camelCase paths (`/orchestrationCenter/runWorkflow`). The new workflow API uses RESTful nested resources (`/api/rooms/{room_id}/workflows`). This is a deliberate shift toward REST conventions. Both styles will coexist until a full API migration.

#### 2a. Data Models

```python
# models/workflow.py

class RetryPolicy(BaseModel):
    max_retries: int = 2
    backoff_seconds: float = 5.0
    backoff_multiplier: float = 2.0

class WorkflowStepType(StrEnum):
    AGENT = "agent"             # Dispatch to a specific agent
    SUPERVISOR = "supervisor"   # Delegate to V2 Supervisor (Phase 4)
    APPROVAL = "approval"       # Wait for human approval (Phase 5)

class WorkflowStep(BaseModel):
    step_id: str                        # Unique within workflow, e.g. "youtube_search"
    step_type: WorkflowStepType = WorkflowStepType.AGENT
    agent_id: str                       # Which agent to call
    task_template: str                  # Jinja2/f-string: "Find {trigger.content_type} creators"
    input_mapping: dict[str, str] = {}  # JSONPath refs to prior step outputs
    timeout_seconds: int = 300
    retry_policy: RetryPolicy | None = None
    depends_on: list[str] = []          # Step IDs that must complete first

class Workflow(BaseModel):
    workflow_id: str = Field(default_factory=lambda: uuid4().hex)
    room_id: str
    name: str                           # "Cold Outreach Pipeline"
    description: str = ""
    trigger_schema: dict                # JSON Schema for trigger inputs
    steps: list[WorkflowStep]
    created_by: str
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    is_active: bool = True
```

Workflow execution state:

```python
# models/workflow_execution.py

# NOTE: These are Hybro's internal workflow enums (StrEnum), NOT A2A
# TaskState enums. They are decoupled from the A2A SDK and will not
# change when A2A v1.0 migrates TaskState values to SCREAMING_SNAKE_CASE.

class StepExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"       # Waiting for webhook
    SKIPPED = "skipped"     # Dependency failed, skip policy applied
    RETRYING = "retrying"

class StepExecution(BaseModel):
    step_id: str
    status: StepExecutionStatus = StepExecutionStatus.PENDING
    agent_message_id: str | None = None
    input_data: dict = {}               # Resolved input for this step
    output_data: dict | None = None     # Structured JSON output from agent
    error_message: str | None = None
    retry_count: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None

class WorkflowExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"       # A step is waiting for webhook/approval
    CANCELED = "canceled"

class WorkflowExecution(BaseModel):
    execution_id: str = Field(default_factory=lambda: uuid4().hex)
    workflow_id: str
    room_id: str
    trigger_input: dict                 # User-provided inputs matching trigger_schema
    status: WorkflowExecutionStatus = WorkflowExecutionStatus.PENDING
    step_executions: dict[str, StepExecution] = {}  # step_id -> execution state
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_by: str
```

#### 2b. WorkflowExecutor

The core executor: iterate steps in dependency order, dispatch agents, persist after each step.

```python
# modules/WorkflowExecutor.py

class WorkflowExecutor:
    """Deterministic step-by-step workflow execution.

    Unlike SupervisorExecutor (LLM-driven, adaptive), this executor
    follows a predefined step sequence. No LLM routing decisions.
    """

    def __init__(
        self,
        agent_message_processor: AgentMessageProcessor,
        agent_dispatcher: AgentDispatcher,
        database_service: DatabaseService,
        room_services: RoomServices,
        room_memory_service: RoomMemoryService,
        rate_limit_service: RateLimitService,
        tsm: TaskStateManager,
        sse_manager: SSEManager,
    ) -> None:
        # room_services is needed to create RoomAgentMessage records
        # before calling agent_message_processor.process_single_message().
        # tsm (TaskStateManager) is needed for task state transitions
        # during fan-out items (Phase 3).
        ...

    async def run(
        self,
        workflow: Workflow,
        execution: WorkflowExecution,
    ) -> WorkflowExecution:
        """Execute a workflow from its current state.

        Supports resume: if execution has completed steps, skips them.
        Persists to database after every step completion.
        """
        execution.status = WorkflowExecutionStatus.RUNNING
        execution.started_at = execution.started_at or utcnow()
        await self._persist_execution(execution)

        ordered_steps = self._topological_sort(workflow.steps)

        for step in ordered_steps:
            step_exec = execution.step_executions.get(step.step_id)

            if step_exec and step_exec.status == StepExecutionStatus.COMPLETED:
                continue

            if not self._dependencies_met(step, execution):
                execution.step_executions[step.step_id] = StepExecution(
                    step_id=step.step_id,
                    status=StepExecutionStatus.SKIPPED,
                    error_message="Dependency not met",
                )
                await self._persist_execution(execution)
                continue

            resolved_input = self._resolve_inputs(
                step, execution.trigger_input, execution.step_executions
            )

            step_exec = await self._execute_step(
                step, resolved_input, workflow.room_id, execution,
            )
            execution.step_executions[step.step_id] = step_exec
            await self._persist_execution(execution)

            if step_exec.status == StepExecutionStatus.PAUSED:
                execution.status = WorkflowExecutionStatus.PAUSED
                await self._persist_execution(execution)
                return execution

            if step_exec.status == StepExecutionStatus.FAILED:
                while (
                    step.retry_policy
                    and step_exec.retry_count < step.retry_policy.max_retries
                    and step_exec.status == StepExecutionStatus.FAILED
                ):
                    step_exec.status = StepExecutionStatus.RETRYING
                    step_exec.retry_count += 1
                    await self._persist_execution(execution)
                    backoff = (
                        step.retry_policy.backoff_seconds
                        * (step.retry_policy.backoff_multiplier ** (step_exec.retry_count - 1))
                    )
                    await asyncio.sleep(backoff)
                    step_exec = await self._execute_step(
                        step, resolved_input, workflow.room_id, execution,
                    )
                    execution.step_executions[step.step_id] = step_exec
                    await self._persist_execution(execution)

                if step_exec.status == StepExecutionStatus.FAILED:
                    execution.status = WorkflowExecutionStatus.FAILED
                    execution.completed_at = utcnow()
                    await self._persist_execution(execution)
                    return execution

            await self.sse_manager.send_event(
                room_id=workflow.room_id,
                event_type="workflow_progress",
                data={
                    "execution_id": execution.execution_id,
                    "step_id": step.step_id,
                    "step_status": step_exec.status,
                    "completed_steps": sum(
                        1 for s in execution.step_executions.values()
                        if s.status == StepExecutionStatus.COMPLETED
                    ),
                    "total_steps": len(workflow.steps),
                },
            )

        execution.status = WorkflowExecutionStatus.COMPLETED
        execution.completed_at = utcnow()
        await self._persist_execution(execution)
        return execution
```

> **Cancellation during backoff**: The `await asyncio.sleep(backoff)` call
> inside the retry loop is not interruptible by `CancellationToken`. If a
> user cancels during backoff, the cancel takes effect only after the sleep
> completes and the next `_execute_step` checks the token. To fix this, the
> implementation should replace `asyncio.sleep` with a cancellation-aware
> wait: `await asyncio.wait_for(token.wait_cancelled(), timeout=backoff)`,
> or poll `token.is_cancelled` in a short-interval loop.

#### 2c. Structured Data Passing

The critical difference from V2 Supervisor: agent outputs are **structured JSON**, not freeform text. Each step's `output_data` is a parsed dict that subsequent steps reference via `input_mapping`.

Agents designed for workflows should return JSON. Example YouTube Agent output:

```json
{
  "creators": [
    {"name": "CryptoGuru", "channel_url": "https://youtube.com/...", "subscribers": 45000},
    {"name": "BlockchainBob", "channel_url": "https://youtube.com/...", "subscribers": 12000}
  ],
  "total_found": 50
}
```

The next step references this:

```json
{
  "step_id": "find_emails",
  "agent_id": "contact-agent-id",
  "task_template": "Find email addresses for these creators: {creators}",
  "input_mapping": {
    "creators": "$.steps.youtube_search.output.creators"
  }
}
```

Input resolution uses JSONPath-like references:
- `$.trigger.content_type` → `trigger_input["content_type"]`
- `$.steps.youtube_search.output.creators` → prior step's structured output

#### 2c-i. Structured Output Extraction

`AgentMessageProcessor.process_single_message` returns a `ProcessingResult`
with `response_text: str`. Agents may return JSON in various forms:

- Pure JSON: `{"creators": [...]}`
- Markdown-wrapped: `` ```json\n{"creators": [...]}\n``` ``
- Mixed text and JSON: `"Here are the results:\n{"creators": [...]}`

The `WorkflowExecutor` needs a `_parse_structured_output` method to handle
all cases:

```python
import json
import re

def _parse_structured_output(self, response_text: str) -> dict:
    """Extract structured JSON from agent response text.

    Tries multiple strategies in order:
    1. Direct JSON parse (agent returned pure JSON)
    2. Extract from markdown code fence (```json ... ```)
    3. Find first JSON object/array in the text via brace matching
    4. Fall back to wrapping the entire text as {"text": response_text}
    """
    text = response_text.strip()

    # Strategy 1: direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: markdown code fence
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Strategy 3: first JSON object in text
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        idx = text.find(start_char)
        if idx != -1:
            depth = 0
            for i in range(idx, len(text)):
                if text[i] == start_char:
                    depth += 1
                elif text[i] == end_char:
                    depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[idx:i + 1])
                    except json.JSONDecodeError:
                        break

    # Strategy 4: fallback
    return {"text": text}
```

Workflow step definitions can optionally declare an `output_schema` (JSON
Schema) to validate extracted output. If validation fails, the step is
marked FAILED with a clear error message indicating the agent returned
non-conforming output.

#### 2d. Recovery on Startup

Workflow recovery follows the same pattern as supervisor trajectory recovery — it is a method on `StaleTaskChecker`, not a standalone function:

```python
# In jobs/stale_task_checker.py, add to the check cycle:

async def _recover_interrupted_workflows(self):
    """Recover workflow executions stuck in RUNNING status."""
    executions = await self.db_service.find_workflow_executions_by_status(
        WorkflowExecutionStatus.RUNNING
    )
    for execution in executions:
        workflow = await self.db_service.get_workflow(execution.workflow_id)
        if not workflow:
            continue
        if await self.db_service.is_message_cancelled(execution.execution_id):
            continue
        asyncio.create_task(
            workflow_executor.run(workflow, execution)  # Resumes from last completed step
        )
```

**What Phase 2 unlocks**: Repeatable multi-step pipelines with structured data passing. Crash-recoverable. No LLM routing overhead.

**What Phase 2 does NOT solve**: Fan-out over lists, parallel branches, human approval gates.

---

### Phase 3: Fan-Out and Parallel Branches

**Goal**: Handle the "50 creators" problem — a step that takes a list and spawns one sub-execution per item, with concurrency control.

**Timeline**: After Phase 2 has real users.

#### 3a. Fan-Out Step Type

Add a new step type that iterates over a list from a prior step's output:

```python
class FanOutStep(BaseModel):
    step_id: str
    step_type: Literal["fan_out"] = "fan_out"
    source: str                     # JSONPath to list: "$.steps.youtube_search.output.creators"
    agent_id: str
    task_template: str              # "Find email for {item.name} at {item.channel_url}"
    max_concurrency: int = 10       # Semaphore limit
    timeout_per_item_seconds: int = 120
    retry_policy: RetryPolicy | None = None
    on_partial_failure: Literal["continue", "abort"] = "continue"
```

Execution produces a list of sub-results:

```python
class FanOutExecution(BaseModel):
    step_id: str
    status: StepExecutionStatus
    items_total: int
    items_completed: int = 0
    items_failed: int = 0
    results: list[dict] = []            # One output_data per item
    errors: list[dict] = []             # {index, item, error_message}
```

#### 3b. Concurrency-Controlled Dispatch

```python
async def _execute_fan_out(
    self, step: FanOutStep, source_list: list, room_id: str,
) -> FanOutExecution:
    semaphore = asyncio.Semaphore(step.max_concurrency)
    fan_exec = FanOutExecution(step_id=step.step_id, items_total=len(source_list))

    async def process_item(index: int, item: dict) -> None:
        async with semaphore:
            task_text = self._render_template(step.task_template, {"item": item})
            agent = await self.agent_dispatcher.resolve_agent(step.agent_id, room_id)
            try:
                result = await asyncio.wait_for(
                    self.agent_message_processor.process_single_message(...),
                    timeout=step.timeout_per_item_seconds,
                )
                if result.status == ProcessingStatus.SUCCESS:
                    fan_exec.results.append(self._parse_structured_output(result.response_text))
                    fan_exec.items_completed += 1
                else:
                    fan_exec.errors.append({"index": index, "item": item, "error": "Failed"})
                    fan_exec.items_failed += 1
            except Exception as e:
                fan_exec.errors.append({"index": index, "item": item, "error": str(e)})
                fan_exec.items_failed += 1

            # Periodic progress update
            if (fan_exec.items_completed + fan_exec.items_failed) % 5 == 0:
                await self.sse_manager.send_event(room_id=room_id, ...)

    await asyncio.gather(*(process_item(i, item) for i, item in enumerate(source_list)))

    if fan_exec.items_failed > 0 and step.on_partial_failure == "abort":
        fan_exec.status = StepExecutionStatus.FAILED
    else:
        fan_exec.status = StepExecutionStatus.COMPLETED

    return fan_exec
```

#### 3c. Parallel Branches

Two steps with no dependency between them can run concurrently. The `WorkflowExecutor` detects this from the dependency graph:

> **Breaking interface change from Phase 2**: In Phase 2, `_topological_sort`
> returns `list[WorkflowStep]` (flat sequential list) and `run()` iterates
> with `for step in ordered_steps`. In Phase 3, the return type changes to
> `list[list[WorkflowStep]]` (grouped by level) and `run()` iterates with
> `for level in ...`. This requires refactoring the `run()` method. To ease
> this transition, Phase 2 should implement `_topological_sort` with the
> `list[list[WorkflowStep]]` return type from the start, where each level
> is a single-element list. The `run()` method below is the Phase 3 version.

```python
def _topological_sort(self, steps: list[WorkflowStep]) -> list[list[WorkflowStep]]:
    """Return steps grouped into levels. Steps in the same level can run in parallel."""
    # Level 0: steps with no dependencies
    # Level 1: steps that depend only on level-0 steps
    # etc.
    ...

async def run(self, workflow, execution):
    for level in self._topological_sort(workflow.steps):
        if len(level) == 1:
            await self._execute_step(level[0], ...)
        else:
            await asyncio.gather(*(self._execute_step(s, ...) for s in level))
```

**Cold outreach example with fan-out**:

```yaml
workflow: "Cold Outreach"
trigger_schema:
  content_type: string
  min_subscribers: integer

steps:
  - step_id: youtube_search
    agent_id: youtube-agent
    task_template: "Find {trigger.content_type} creators with {trigger.min_subscribers}+ subs"

  - step_id: find_emails
    step_type: fan_out
    source: "$.steps.youtube_search.output.creators"
    agent_id: contact-agent
    task_template: "Find email for {item.name} at {item.channel_url}"
    max_concurrency: 10
    depends_on: [youtube_search]

  - step_id: send_emails
    step_type: fan_out
    source: "$.steps.find_emails.output.results"
    agent_id: email-agent
    task_template: "Send cold outreach to {item.name} at {item.email} about crypto partnerships"
    max_concurrency: 5
    on_partial_failure: continue
    depends_on: [find_emails]
```

**What Phase 3 unlocks**: Full cold outreach pipeline. Multi-source parallel research. Any workflow that processes a list of items.

---

### Phase 4: Supervisor as a Workflow Step

**Goal**: Combine deterministic workflow execution with LLM-mediated routing. A workflow step can delegate to V2 Supervisor when intelligent agent selection is needed.

**Timeline**: After Phase 3.

#### 4a. Supervisor Step

```python
class SupervisorWorkflowStep(BaseModel):
    step_id: str
    step_type: Literal["supervisor"] = "supervisor"
    task_template: str              # What to ask the supervisor to do
    agent_pool: list[str] = []      # Restrict which agents supervisor can use (empty = all room agents)
    max_supervisor_steps: int = 4   # Cap supervisor LLM calls within this step
    depends_on: list[str] = []
```

The workflow executor calls `SupervisorExecutor.run()` for this step type, passing the resolved input as the message text and restricting the agent registry to `agent_pool` if specified.

#### 4b. Use Case: Hybrid Workflow

```yaml
steps:
  - step_id: research
    step_type: supervisor
    task_template: "Research {trigger.topic} using the best available agents"
    # Supervisor decides: Web Agent? Academic Agent? News Agent?

  - step_id: enrich
    step_type: fan_out
    source: "$.steps.research.output.sources"
    agent_id: enrichment-agent
    task_template: "Get detailed data for {item.url}"
    depends_on: [research]

  - step_id: report
    agent_id: report-agent
    task_template: "Generate a report from: {enriched_data}"
    depends_on: [enrich]
```

The research step uses intelligent routing (the user doesn't know which agents are best); the enrich step uses deterministic fan-out (known agent, known list); the report step is a simple sequential dispatch.

**What Phase 4 unlocks**: "Figure out the best agents, then batch-process the results." Combines the intelligence of V2 with the durability of the workflow engine.

---

### Phase 5: Scheduling, Triggers, and Approval Gates

**Goal**: Workflows that run on a schedule, trigger from external events, and pause for human approval.

**Timeline**: When workflow users are requesting it.

#### 5a. Scheduled Triggers

```python
class WorkflowTrigger(BaseModel):
    trigger_type: Literal["manual", "schedule", "webhook"]
    # Schedule: cron expression
    cron_expression: str | None = None      # "0 9 * * MON" = every Monday 9am
    # Webhook: external event
    webhook_path: str | None = None         # "/webhooks/workflows/{workflow_id}"
    # Both need default trigger inputs
    default_inputs: dict = {}
```

Implementation: The codebase uses hand-rolled `asyncio` background loops (see `StaleTaskChecker`) rather than a third-party scheduler. For scheduled workflows, add a `WorkflowScheduler` background task using the same `asyncio` loop pattern — or introduce APScheduler as a new dependency if cron-expression parsing is needed.

#### 5b. Human Approval Gates

```python
class ApprovalStep(BaseModel):
    step_id: str
    step_type: Literal["approval"] = "approval"
    prompt_template: str        # "Review these 50 emails before sending. Approve?"
    approvers: list[str] = []   # User IDs who can approve (empty = any room member)
    timeout_hours: int = 24
    depends_on: list[str] = []
```

When execution reaches an approval step:
1. Emit an SSE event with the approval prompt and context from prior steps
2. Set execution status to PAUSED
3. Persist to database
4. Wait for a user to call `POST /api/workflows/{execution_id}/approve` or `/reject`
5. On approval, resume execution from the next step
6. On rejection or timeout, mark execution as FAILED

#### 5c. Build vs. Adopt Decision Point

At Phase 5, the workflow engine has:
- Step execution with persistence
- Fan-out with concurrency control
- Parallel branches
- Retries with backoff
- Human approval gates
- Scheduled triggers
- Webhook triggers

This is approaching the feature set of Temporal/Airflow. **Evaluate whether to continue building or adopt Temporal as the execution backend.** Key criteria:

| Factor | Build | Adopt Temporal |
|---|---|---|
| Current scale (<1000 concurrent workflows) | Build is fine | Overkill |
| Growing scale (>10,000 concurrent workflows) | MongoDB may struggle | Temporal scales natively |
| Cross-service orchestration needed | Build a message bus | Temporal handles natively |
| Team has Temporal ops experience | N/A | Reduces risk |
| Deep agent/SSE integration needed | Build has tighter integration | Glue code required |

**Recommendation**: If you reach Phase 5 and are still under 1000 concurrent workflows, continue building. If scale is growing fast or you need cross-service orchestration, adopt Temporal and migrate the workflow definitions (the `Workflow` model) to Temporal workflow code. The agent dispatch logic (`AgentMessageProcessor`) stays the same regardless.

---

## 4. Cold Outreach Example: End-to-End

Here's how the motivating use case works at each phase:

| Phase | How Cold Outreach Works | Duration | Reliability |
|---|---|---|---|
| **0 (V2 only)** | Supervisor calls YouTube→Contact→Email agents sequentially. Contact/Email agents must handle batching internally. 500-char trajectory truncation may lose creator list. | 1-5 min | Fragile — server restart loses everything |
| **1 (Robust V2)** | Same as Phase 0, but with crash recovery and agent timeout auto-PAUSE. User sees progress. | 1-5 min | Crash-recoverable |
| **2 (Workflows)** | User defines a 3-step workflow template. Triggers with `{content_type: "crypto"}`. Each step persisted. Structured JSON passing. | 5-15 min | Durable, resumable |
| **3 (Fan-out)** | Workflow fans out over 50 creators for email lookup (10 concurrent), then fans out for email sending (5 concurrent). Partial failures tracked. | 15-30 min | Durable, handles partial failure |
| **4 (Hybrid)** | Research step uses Supervisor to pick best search agents. Fan-out for emails. | 15-30 min | Durable + intelligent |
| **5 (Scheduled)** | Runs every Monday. Human reviews email list before sending. | Scheduled | Fully automated |

---

## 5. API Surface (Phase 2-3)

### 5.1 Workflow CRUD

```
POST   /api/rooms/{room_id}/workflows          Create a workflow
GET    /api/rooms/{room_id}/workflows          List workflows
GET    /api/workflows/{workflow_id}             Get workflow
PUT    /api/workflows/{workflow_id}             Update workflow
DELETE /api/workflows/{workflow_id}             Delete workflow
```

### 5.2 Workflow Execution

```
POST   /api/workflows/{workflow_id}/run         Trigger a workflow with inputs
GET    /api/workflows/{workflow_id}/executions   List executions
GET    /api/executions/{execution_id}            Get execution status + step details
POST   /api/executions/{execution_id}/cancel     Cancel a running execution
POST   /api/executions/{execution_id}/approve    Approve an approval gate (Phase 5)
POST   /api/executions/{execution_id}/reject     Reject an approval gate (Phase 5)
```

### 5.3 SSE Events

```typescript
// New SSE event types for workflow execution
interface WorkflowProgressEvent {
  type: "workflow_progress";
  data: {
    execution_id: string;
    step_id: string;
    step_status: string;
    completed_steps: number;
    total_steps: number;
    message?: string;  // "Finding emails for 50 creators (23/50 complete)..."
  };
}

interface WorkflowCompletedEvent {
  type: "workflow_completed";
  data: {
    execution_id: string;
    status: "completed" | "failed" | "canceled";
    summary: string;
    duration_seconds: number;
  };
}

interface WorkflowApprovalEvent {
  type: "workflow_approval_required";
  data: {
    execution_id: string;
    step_id: string;
    prompt: string;
    context: dict;  // Prior step outputs for reviewer
  };
}
```

---

## 6. What Not to Build

To keep scope manageable, the following are explicitly **out of scope** for this roadmap:

| Feature | Why Not | When to Reconsider |
|---|---|---|
| Visual workflow builder (drag-and-drop UI) | High frontend effort, low initial value. YAML/JSON definition is sufficient for power users. | When non-technical users need to create workflows |
| Workflow versioning (deploy new definition without breaking in-flight executions) | Complex state migration. In-flight executions finish under old definition. | When workflows change frequently and in-flight executions are common |
| Cross-room workflows | Workflows are room-scoped. Cross-room adds auth complexity. | When users need workflows that span multiple agent teams |
| Sub-workflows (workflow step that triggers another workflow) | Adds recursion complexity. Flatten into a single workflow instead. | When workflow definitions become repetitively nested |
| Real-time streaming within fan-out items | SSE progress events per fan-out item would flood the frontend. Aggregate progress (23/50) is sufficient. | When users need to monitor individual fan-out items |

---

## 7. Implementation Effort Estimates

| Phase | Backend (est.) | Frontend (est.) | Shared Code Reused | New Dependencies | Calendar Time |
|---|---|---|---|---|---|
| **1: Robust V2** | ~100 lines (1a done, 1b remaining) | ~50 lines (SSE handler for supervisor_progress) | SupervisorExecutor, AgentMessageProcessor | None | 1 week |
| **2: Workflow Templates** | ~1200 lines | ~800 lines (workflow creation UI, SSE handlers, progress component) | AgentMessageProcessor, AgentDispatcher, SSE | None | 4-6 weeks |
| **3: Fan-Out** | ~500 lines | ~200 lines (fan-out progress display) | Phase 2 executor, AgentMessageProcessor | None | 2-3 weeks |
| **4: Supervisor Step** | ~200 lines | ~0 (reuses existing supervisor UI) | SupervisorExecutor, Phase 2 executor | None | 1 week |
| **5: Scheduling/Approval** | ~600 lines | ~400 lines (approval gate UI, schedule config) | Phase 2 executor | APScheduler (new, if cron needed) | 2-3 weeks |

Total: ~2600 lines backend + ~1450 lines frontend across all phases. Frontend effort is significant for Phase 2 (workflow builder/progress UI) and Phase 5 (approval gates).

### 7.1 Cancellation Support

The `WorkflowExecution` model includes a `CANCELED` status, but the cancellation flow requires explicit design:

- **Single-step cancel**: User cancels → backend marks current step as FAILED → workflow status becomes CANCELED → SSE broadcasts `workflow_completed` with status `canceled`.
- **Fan-out cancel** (Phase 3): User cancels during fan-out → cancel signal propagated to all in-flight concurrent tasks via `CancellationToken` → partial results preserved → workflow status becomes CANCELED.
- **Reuse existing infrastructure**: `SSEManager.cancel_message()` + `CancellationToken` (from `common/utils/cancellation.py`) + MongoDB change stream propagation.

### 7.2 Integration with Hub Design and Trust Layer

When implementing, the `WorkflowExecutor` dispatch path must account for:

- **Hub agents** (HYBRO_HUB_DESIGN.md): When a workflow step targets a hub-sourced agent (`agent.source == "hub"`), `AgentMessageProcessor` must route via the relay instead of direct A2A. This routing is transparent to the WorkflowExecutor — it dispatches through `AgentMessageProcessor` which handles transport selection.
- **Trust policies** (HYBRO_TRUST_LAYER_DESIGN.md): Policy evaluation and token issuance happen in the dispatch middleware (inside `AgentMessageProcessor`), not in the WorkflowExecutor. The executor doesn't need trust-layer awareness — it delegates through the same `process_single_message` interface.
- **SSE events**: New workflow SSE event types (`workflow_progress`, `workflow_completed`, `workflow_approval_required`) must be registered in the frontend's `handleSSEMessage` switch and the `SSEMessage` type union. When a workflow step targets a hub agent, the frontend will receive *both* workflow-level events and hub relay events — these coexist at different abstraction levels (see HYBRO_HUB_DESIGN.md §15 "SSE Event Composition").
- **Middleware architecture**: Hub routing and trust-layer logic will be added to `AgentMessageProcessor` via a middleware chain pattern (see HYBRO_HUB_DESIGN.md §15 "Middleware Architecture"). The `WorkflowExecutor` does not need awareness of this — it delegates through the same `process_single_message` interface.
- **Agent model**: The `WorkflowExecutor` references agents by `agent_id` in step definitions. The canonical combined `Agent` model (with Hub and Trust Layer fields) is defined in HYBRO_HUB_DESIGN.md §15 "Shared Agent Model."
- **A2A v1.0 migration** `[v1.0-MIGRATION]`: The `WorkflowExecutor` has no direct A2A type imports. All A2A version-sensitive code lives in the dispatch layer (`a2a_service.py`, `ResponseProcessor.py`, `a2a_helpers.py`). When migrating to v1.0, no changes are needed to `WorkflowExecutor`, `WorkflowExecution`, or workflow models. The v1.0 `blocking` parameter on `SendMessage` may simplify the timeout/auto-PAUSE logic in Phase 1b — agents that support blocking mode can signal sync completion directly.

---

## 8. Summary

| Component | Role | Execution Model | Durability |
|---|---|---|---|
| **V2 Supervisor** | Intelligence layer — decides which agents to call when the path is ambiguous | LLM-driven adaptive loop, 2-8 steps | Best-effort (Phase 1 adds checkpointing) |
| **Workflow Engine** | Execution layer — runs defined pipelines reliably with fan-out and retry | Deterministic step execution, unlimited steps | Durable (persisted after every step) |
| **Shared Infrastructure** | Agent dispatch, SSE streaming, room memory, push notification resume | Reused by both tiers | Existing |

The V2 Supervisor and Workflow Engine are complementary, not competing. The supervisor handles the ambiguous decisions; the workflow engine handles the reliable execution. Together, they cover the spectrum from "ask my agents a quick question" to "run this 50-item batch pipeline every Monday."

**Related design documents:**
- [HYBRO_HUB_DESIGN.md](./HYBRO_HUB_DESIGN.md) — Hub agent routing (transparent to the workflow executor via `AgentMessageProcessor`)
- [HYBRO_TRUST_LAYER_DESIGN.md](./HYBRO_TRUST_LAYER_DESIGN.md) — Policy and token middleware (transparent to the workflow executor via `AgentMessageProcessor`)

**A2A version note:** All three design documents target A2A v0.3. The shared
dispatch path (`AgentMessageProcessor`) encapsulates all A2A-specific concerns.
The v1.0 migration (see HYBRO_HUB_DESIGN.md §9) affects only the dispatch
layer — workflow definitions, execution models, and trust policies are
protocol-version-agnostic.
