# Supervisor V2 Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve supervisor V2 result quality evaluation (expand response preview + add quality evaluation prompt) and add stage-level SSE notifications so the frontend shows progress during the supervisor loop.

**Architecture:** Two independent changes. Part 1 modifies `room_supervisor_service.py` only (prompt text + truncation constant). Part 2 adds `send_processing_status(details=...)` calls in `SupervisorExecutor.py` and updates the frontend SSE handler + store + placeholder message to display stage details.

**Tech Stack:** Python (FastAPI backend), TypeScript/React (Next.js frontend), Zustand (state management), SSE (real-time events)

**Design spec:** `docs/SUPERVISOR_V2_IMPROVEMENTS_DESIGN.md`

---

### Task 1: Expand response preview from 500 to 3000 chars in trajectory formatting

**Files:**
- Modify: `services/room_supervisor_service.py:428-432`
- Test: `tests/test_supervisor_v2_improvements.py` (create)

- [ ] **Step 1: Write the failing test for 3000-char truncation**

Create `tests/test_supervisor_v2_improvements.py`:

```python
"""
Tests for Supervisor V2 Improvements:
- Part 1: Expanded response preview in trajectory formatting
- Part 2: SSE stage notifications in SupervisorExecutor
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from models.supervisor_v2 import (
    ActionType,
    SupervisorAction,
    SupervisorTrajectory,
    TrajectoryEntry,
    V2StepResult,
    DelegateTarget,
    StepStatus,
)
from services.room_supervisor_service import RoomSupervisorService


class TestTrajectoryResponsePreview:
    """Verify _format_trajectory uses 3000-char preview (not 500)."""

    def _make_trajectory_with_response(self, response_text: str) -> SupervisorTrajectory:
        trajectory = SupervisorTrajectory()
        entry = TrajectoryEntry(
            step_number=1,
            action=SupervisorAction(
                action=ActionType.DELEGATE,
                reasoning="test",
                targets=[
                    DelegateTarget(
                        agent_id="agent-1",
                        agent_name="TestAgent",
                        task="do something",
                    )
                ],
            ),
        )
        entry.results = [
            V2StepResult(
                agent_id="agent-1",
                agent_name="TestAgent",
                success=True,
                status=StepStatus.SUCCESS,
                response_text=response_text,
            )
        ]
        trajectory.entries.append(entry)
        return trajectory

    def test_short_response_not_truncated(self):
        """A response under 3000 chars should appear in full."""
        text = "x" * 2000
        trajectory = self._make_trajectory_with_response(text)
        formatted = RoomSupervisorService._format_trajectory(trajectory)
        assert text in formatted
        assert "truncated" not in formatted

    def test_response_at_3000_chars_not_truncated(self):
        """A response of exactly 3000 chars should not be truncated."""
        text = "y" * 3000
        trajectory = self._make_trajectory_with_response(text)
        formatted = RoomSupervisorService._format_trajectory(trajectory)
        assert text in formatted
        assert "truncated" not in formatted

    def test_response_over_3000_chars_truncated(self):
        """A response over 3000 chars should be truncated with length note."""
        text = "z" * 5000
        trajectory = self._make_trajectory_with_response(text)
        formatted = RoomSupervisorService._format_trajectory(trajectory)
        assert "z" * 3000 in formatted
        assert "z" * 3001 not in formatted
        assert "truncated" in formatted
        assert "5000" in formatted

    def test_old_500_limit_no_longer_applies(self):
        """A 1500-char response must NOT be truncated (old limit was 500)."""
        text = "a" * 1500
        trajectory = self._make_trajectory_with_response(text)
        formatted = RoomSupervisorService._format_trajectory(trajectory)
        assert text in formatted
        assert "truncated" not in formatted
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_supervisor_v2_improvements.py::TestTrajectoryResponsePreview -v`
Expected: `test_response_over_3000_chars_truncated` and `test_old_500_limit_no_longer_applies` fail because the current limit is 500.

- [ ] **Step 3: Change the truncation constant from 500 to 3000**

In `services/room_supervisor_service.py`, change lines 427-432:

**Old (line 428-432):**
```python
                    total_len = len(result.response_text)
                    response_preview = result.response_text[:500]
                    if total_len > 500:
                        response_preview += (
                            f" ... [truncated — full response: {total_len} chars]"
                        )
```

**New:**
```python
                    total_len = len(result.response_text)
                    response_preview = result.response_text[:3000]
                    if total_len > 3000:
                        response_preview += (
                            f" ... [truncated — full response: {total_len} chars]"
                        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_supervisor_v2_improvements.py::TestTrajectoryResponsePreview -v`
Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add services/room_supervisor_service.py tests/test_supervisor_v2_improvements.py
git commit -m "feat: expand supervisor trajectory response preview to 3000 chars

Increases the response text shown to the supervisor LLM from 500 to 3000
characters. This gives the LLM enough context to evaluate result quality
and decide whether to re-delegate or synthesize."
```

---

### Task 2: Add quality evaluation instructions to supervisor system prompt

**Files:**
- Modify: `services/room_supervisor_service.py:93-103` (system prompt)

- [ ] **Step 1: Write the failing test for quality evaluation prompt content**

Add to `tests/test_supervisor_v2_improvements.py`:

```python
class TestQualityEvaluationPrompt:
    """Verify the system prompt includes quality evaluation instructions."""

    def test_system_prompt_contains_quality_evaluation_block(self):
        """The SUPERVISOR_V2_SYSTEM_PROMPT must contain quality evaluation guidance."""
        from services.room_supervisor_service import SUPERVISOR_V2_SYSTEM_PROMPT
        assert "QUALITY EVALUATION" in SUPERVISOR_V2_SYSTEM_PROMPT
        assert "unsatisfactory" in SUPERVISOR_V2_SYSTEM_PROMPT

    def test_quality_evaluation_before_action_types(self):
        """Quality evaluation block should appear in the prompt (order doesn't matter
        for LLM, but it must exist)."""
        from services.room_supervisor_service import SUPERVISOR_V2_SYSTEM_PROMPT
        assert "returned unsatisfactory results" not in SUPERVISOR_V2_SYSTEM_PROMPT or \
               "QUALITY EVALUATION" in SUPERVISOR_V2_SYSTEM_PROMPT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_supervisor_v2_improvements.py::TestQualityEvaluationPrompt -v`
Expected: FAIL because "QUALITY EVALUATION" is not in the current prompt.

- [ ] **Step 3: Add quality evaluation block to the system prompt**

In `services/room_supervisor_service.py`, insert the following block after the `## Rules` section (after line 108, before `## Room Conversation Background`):

**Insert after line 108 (`  proceed with DELEGATE — do not issue another CLARIFY.`):**

```python
## Quality Evaluation — before choosing SYNTHESIZE or DONE
- Review each DELEGATE result for substance. Does it directly address the
  user's request with actionable, specific content?
- A response that repeats the question, returns no data, says it couldn't
  find anything, or contains only generic/templated text should be treated
  as unsatisfactory.
- If one or more agents returned unsatisfactory results while others
  succeeded, you may:
  (a) DELEGATE to the same agent with a more specific/refined task
  (b) DELEGATE to a different agent that might handle it better
  (c) SYNTHESIZE using only the good results, noting which areas had
      insufficient coverage
- Only choose SYNTHESIZE when you are confident the collected results
  adequately address the user's request.
```

Also update the existing rule on lines 100-103 to reinforce:

**Old (lines 100-103):**
```python
- After each agent result, evaluate quality. If the agent returned a successful
  response that addresses the user's question, choose DONE. Only re-delegate if
  the response is clearly wrong, off-topic, or the agent explicitly failed.
  Do NOT re-delegate just to get a "better" or "more refined" answer.
```

**New:**
```python
- After each agent result, evaluate quality per the QUALITY EVALUATION section
  below. If the agent returned a substantive response that addresses the user's
  question, choose DONE. Re-delegate if the response is empty, off-topic, says
  it couldn't find anything, or the agent explicitly failed.
  Do NOT re-delegate just to get a "better" or "more refined" answer when the
  existing response already contains actionable content.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_supervisor_v2_improvements.py::TestQualityEvaluationPrompt -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add services/room_supervisor_service.py tests/test_supervisor_v2_improvements.py
git commit -m "feat: add quality evaluation instructions to supervisor prompt

Adds a QUALITY EVALUATION section to the supervisor V2 system prompt with
concrete criteria for identifying unsatisfactory agent results. The supervisor
LLM can now re-delegate when agents return empty or generic responses."
```

---

### Task 3: Add SSE stage notifications to SupervisorExecutor.run()

**Files:**
- Modify: `modules/SupervisorExecutor.py` (5 insertion points)
- Test: `tests/test_supervisor_v2_improvements.py` (add class)

- [ ] **Step 1: Write the failing test for SSE stage notifications**

Add to `tests/test_supervisor_v2_improvements.py`:

```python
from modules.SupervisorExecutor import SupervisorExecutor
from models.supervisor_v2 import (
    SupervisorRunResult,
    RunStatus,
    TrajectoryStatus,
    RoomConfig,
)


def _make_supervisor_executor():
    """Create a SupervisorExecutor with mocked dependencies."""
    se = object.__new__(SupervisorExecutor)
    se.database_service = AsyncMock()
    se.sse_manager = AsyncMock()
    se.room_services = MagicMock()
    se.supervisor_service = AsyncMock()
    se.tsm = MagicMock()
    se.agent_dispatcher = MagicMock()
    se.agent_message_processor = MagicMock()
    se.room_memory_service = AsyncMock()
    se.rate_limit_service = MagicMock()
    se.room_coordinator_service = MagicMock()
    se.MAX_STEPS = 8
    return se


class TestSupervisorSSEStageNotifications:
    """Verify send_processing_status is called with correct stage details."""

    @pytest.mark.asyncio
    async def test_planning_status_emitted_before_decide_next(self):
        """'Planning next action...' should be emitted at the start of each loop iteration."""
        se = _make_supervisor_executor()
        # Make decide_next return DONE immediately
        se.supervisor_service.decide_next.return_value = SupervisorAction(
            action=ActionType.DONE,
            reasoning="done",
        )
        # Stub checkpoint
        se._checkpoint_trajectory = AsyncMock(return_value=None)
        se.database_service.get_room_user_message_by_message_id.return_value = MagicMock(
            extend_info={}
        )
        se.database_service.update_room_user_message_by_message_id.return_value = True

        result = await se.run(
            room_id="room-1",
            user_message_id="msg-1",
            message_text="hello",
            agent_registry=[MagicMock(agent_id="a1", agent_name="Agent1", status="healthy")],
            room_config=RoomConfig(is_debate_mode=False),
        )

        # Check that "Planning next action..." was sent
        calls = se.sse_manager.send_processing_status.call_args_list
        planning_calls = [
            c for c in calls
            if c.kwargs.get("details") == "Planning next action..."
            or (len(c.args) >= 4 and c.args[3] == "Planning next action...")
        ]
        assert len(planning_calls) >= 1, (
            f"Expected 'Planning next action...' SSE event, got: {calls}"
        )

    @pytest.mark.asyncio
    async def test_delegating_status_emitted_before_dispatch(self):
        """'Delegating to N agent(s)...' should be emitted before dispatch."""
        se = _make_supervisor_executor()
        # First call returns DELEGATE, second returns DONE
        se.supervisor_service.decide_next.side_effect = [
            SupervisorAction(
                action=ActionType.DELEGATE,
                reasoning="delegate",
                targets=[
                    DelegateTarget(agent_id="a1", agent_name="Agent1", task="task1"),
                    DelegateTarget(agent_id="a2", agent_name="Agent2", task="task2"),
                ],
            ),
            SupervisorAction(action=ActionType.DONE, reasoning="done"),
        ]
        # Dispatch returns success results
        se._dispatch_targets = AsyncMock(return_value=[
            V2StepResult(
                agent_id="a1", agent_name="Agent1", success=True,
                status=StepStatus.SUCCESS, response_text="result1",
            ),
            V2StepResult(
                agent_id="a2", agent_name="Agent2", success=True,
                status=StepStatus.SUCCESS, response_text="result2",
            ),
        ])
        se._checkpoint_trajectory = AsyncMock(return_value=None)
        se.room_memory_service.add_agent_response_to_memory = AsyncMock()

        result = await se.run(
            room_id="room-1",
            user_message_id="msg-1",
            message_text="hello",
            agent_registry=[
                MagicMock(agent_id="a1", agent_name="Agent1", status="healthy"),
                MagicMock(agent_id="a2", agent_name="Agent2", status="healthy"),
            ],
            room_config=RoomConfig(is_debate_mode=False),
        )

        calls = se.sse_manager.send_processing_status.call_args_list
        delegate_calls = [
            c for c in calls
            if "details" in (c.kwargs or {})
            and c.kwargs.get("details", "").startswith("Delegating to")
        ]
        assert len(delegate_calls) >= 1, (
            f"Expected 'Delegating to N agent(s)...' SSE event, got: {calls}"
        )
        # Should mention 2 agents
        assert "2" in delegate_calls[0].kwargs["details"]

    @pytest.mark.asyncio
    async def test_evaluating_status_emitted_after_dispatch(self):
        """'Evaluating agent results...' should be emitted after dispatch completes."""
        se = _make_supervisor_executor()
        se.supervisor_service.decide_next.side_effect = [
            SupervisorAction(
                action=ActionType.DELEGATE,
                reasoning="delegate",
                targets=[
                    DelegateTarget(agent_id="a1", agent_name="Agent1", task="task1"),
                ],
            ),
            SupervisorAction(action=ActionType.DONE, reasoning="done"),
        ]
        se._dispatch_targets = AsyncMock(return_value=[
            V2StepResult(
                agent_id="a1", agent_name="Agent1", success=True,
                status=StepStatus.SUCCESS, response_text="result",
            ),
        ])
        se._checkpoint_trajectory = AsyncMock(return_value=None)
        se.room_memory_service.add_agent_response_to_memory = AsyncMock()

        await se.run(
            room_id="room-1",
            user_message_id="msg-1",
            message_text="hello",
            agent_registry=[MagicMock(agent_id="a1", agent_name="Agent1", status="healthy")],
            room_config=RoomConfig(is_debate_mode=False),
        )

        calls = se.sse_manager.send_processing_status.call_args_list
        eval_calls = [
            c for c in calls
            if c.kwargs.get("details") == "Evaluating agent results..."
        ]
        assert len(eval_calls) >= 1, (
            f"Expected 'Evaluating agent results...' SSE event, got: {calls}"
        )

    @pytest.mark.asyncio
    async def test_synthesizing_status_emitted_before_synthesis(self):
        """'Synthesizing responses...' should be emitted before synthesis call."""
        se = _make_supervisor_executor()
        se.supervisor_service.decide_next.side_effect = [
            SupervisorAction(
                action=ActionType.DELEGATE,
                reasoning="delegate",
                targets=[
                    DelegateTarget(agent_id="a1", agent_name="Agent1", task="task1"),
                ],
            ),
            SupervisorAction(
                action=ActionType.SYNTHESIZE,
                reasoning="synthesize",
                synthesis_instruction="combine results",
            ),
        ]
        se._dispatch_targets = AsyncMock(return_value=[
            V2StepResult(
                agent_id="a1", agent_name="Agent1", success=True,
                status=StepStatus.SUCCESS, response_text="result",
            ),
        ])
        se._checkpoint_trajectory = AsyncMock(return_value=None)
        se.room_memory_service.add_agent_response_to_memory = AsyncMock()
        se.supervisor_service.synthesize_v2.return_value = "synthesized response"

        await se.run(
            room_id="room-1",
            user_message_id="msg-1",
            message_text="hello",
            agent_registry=[MagicMock(agent_id="a1", agent_name="Agent1", status="healthy")],
            room_config=RoomConfig(is_debate_mode=False),
        )

        calls = se.sse_manager.send_processing_status.call_args_list
        synth_calls = [
            c for c in calls
            if c.kwargs.get("details") == "Synthesizing responses..."
        ]
        assert len(synth_calls) >= 1, (
            f"Expected 'Synthesizing responses...' SSE event, got: {calls}"
        )

    @pytest.mark.asyncio
    async def test_sse_failure_does_not_crash_loop(self):
        """If send_processing_status raises, the supervisor loop should continue."""
        se = _make_supervisor_executor()
        se.sse_manager.send_processing_status.side_effect = Exception("SSE down")
        se.supervisor_service.decide_next.return_value = SupervisorAction(
            action=ActionType.DONE,
            reasoning="done",
        )
        se._checkpoint_trajectory = AsyncMock(return_value=None)

        # Should not raise
        result = await se.run(
            room_id="room-1",
            user_message_id="msg-1",
            message_text="hello",
            agent_registry=[MagicMock(agent_id="a1", agent_name="Agent1", status="healthy")],
            room_config=RoomConfig(is_debate_mode=False),
        )
        assert result.status in (RunStatus.COMPLETED, RunStatus.FAILED)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_supervisor_v2_improvements.py::TestSupervisorSSEStageNotifications -v`
Expected: Tests fail because no `send_processing_status(details=...)` calls exist yet (except the AWAITING_INPUT ones without details).

- [ ] **Step 3: Add SSE stage notifications to SupervisorExecutor.run()**

In `modules/SupervisorExecutor.py`, add 5 `send_processing_status` calls wrapped in try/except:

**3a. Before `decide_next()` — each loop iteration (before line 240):**

Insert before `decide_coro = self.supervisor_service.decide_next(`:

```python
                # SSE: notify frontend of planning stage
                try:
                    await self.sse_manager.send_processing_status(
                        room_id, SSEProcessingStatus.PROCESSING, user_message_id,
                        details="Planning next action...",
                    )
                except Exception:
                    logger.debug("SSE stage notification failed (planning)", exc_info=True)
```

**3b. After DELEGATE decided, before dispatch (before line 450):**

Insert before `results = await self._dispatch_targets(`:

```python
                    # SSE: notify frontend of delegation stage
                    try:
                        await self.sse_manager.send_processing_status(
                            room_id, SSEProcessingStatus.PROCESSING, user_message_id,
                            details=f"Delegating to {len(action.targets)} agent(s)...",
                        )
                    except Exception:
                        logger.debug("SSE stage notification failed (delegating)", exc_info=True)
```

**3c. After dispatch completes, before next iteration (after line 611, the post-dispatch checkpoint):**

Insert after the post-dispatch checkpoint block (after `_checkpoint_msg = await self._checkpoint_trajectory(...)`):

```python
                    # SSE: notify frontend of evaluation stage
                    try:
                        await self.sse_manager.send_processing_status(
                            room_id, SSEProcessingStatus.PROCESSING, user_message_id,
                            details="Evaluating agent results...",
                        )
                    except Exception:
                        logger.debug("SSE stage notification failed (evaluating)", exc_info=True)
```

**3d. After SYNTHESIZE decided, before synthesis call (before line 661):**

Insert before `synth_coro = self.supervisor_service.synthesize_v2(`:

```python
                    # SSE: notify frontend of synthesis stage
                    try:
                        await self.sse_manager.send_processing_status(
                            room_id, SSEProcessingStatus.PROCESSING, user_message_id,
                            details="Synthesizing responses...",
                        )
                    except Exception:
                        logger.debug("SSE stage notification failed (synthesizing)", exc_info=True)
```

**3e. Budget exhaustion synthesis path (before line 878):**

Insert before `budget_synth_coro = self.supervisor_service.synthesize_v2(`:

```python
            # SSE: notify frontend of budget-exhaustion synthesis
            try:
                await self.sse_manager.send_processing_status(
                    room_id, SSEProcessingStatus.PROCESSING, user_message_id,
                    details="Synthesizing responses...",
                )
            except Exception:
                logger.debug("SSE stage notification failed (budget synthesis)", exc_info=True)
```

Also add the `SSEProcessingStatus` import at the top of the file if not already present:
```python
from services.sse_services import SSEProcessingStatus
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_supervisor_v2_improvements.py::TestSupervisorSSEStageNotifications -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `pytest tests/ -x -q`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add modules/SupervisorExecutor.py tests/test_supervisor_v2_improvements.py
git commit -m "feat: add SSE stage notifications to supervisor V2 loop

Emits processing_status events with details text at 5 points in the
supervisor loop: planning, delegating, evaluating results, synthesizing,
and budget-exhaustion synthesis. All wrapped in try/except so SSE
failures never crash the supervisor."
```

---

### Task 4: Frontend — update SSE handler to pass details to placeholder message

**Files:**
- Modify: `hybro-frontend/src/hooks/room/sse-handlers/index.ts:124-138`

- [ ] **Step 1: Update the PROCESSING handler to read `details` and update placeholder**

In `hybro-frontend/src/hooks/room/sse-handlers/index.ts`, modify the PROCESSING status block.

**Old (lines 124-138):**
```typescript
            if (!lifecycle.isPlaceholderDismissed()) {
              const isSupervisor = getSupervisorMode()
              store.upsertMessage({
                id: lifecycle.placeholderId(roomId),
                roomId,
                messageType: 'agent',
                content: '',
                senderName: 'HYBRO AI',
                taskStatus: TASK_STATE.WORKING,
                taskContent: isSupervisor
                  ? 'Supervisor is analyzing your request…'
                  : 'Processing your request…',
                timestamp: new Date().toISOString(),
                isEphemeral: true,
              }, 'optimistic')
            }
```

**New:**
```typescript
            if (!lifecycle.isPlaceholderDismissed()) {
              const isSupervisor = getSupervisorMode()
              const stageDetails = sseMessage.data.details as string | undefined
              const defaultText = isSupervisor
                ? 'Supervisor is analyzing your request…'
                : 'Processing your request…'
              store.upsertMessage({
                id: lifecycle.placeholderId(roomId),
                roomId,
                messageType: 'agent',
                content: '',
                senderName: 'HYBRO AI',
                taskStatus: TASK_STATE.WORKING,
                taskContent: stageDetails || defaultText,
                timestamp: new Date().toISOString(),
                isEphemeral: true,
              }, 'optimistic')
            }
```

This is the minimal change. The placeholder message's `taskContent` now shows the backend's `details` text when present (e.g., "Planning next action...", "Delegating to 4 agent(s)...", "Evaluating agent results...", "Synthesizing responses..."). When no details are sent, it falls back to the existing default text.

The `message-bubble.tsx` already renders `entity.taskContent` at line 649 — no changes needed there.

- [ ] **Step 2: Verify the change visually**

Start the dev server and trigger a supervisor room message. The processing indicator should cycle through:
1. "Supervisor is analyzing your request…" (initial, no details yet)
2. "Planning next action..." (first SSE with details)
3. "Delegating to N agent(s)..." (before dispatch)
4. "Evaluating agent results..." (after dispatch)
5. "Synthesizing responses..." (if synthesis happens)

Each new `processing_status` with `details` replaces the previous text in-place.

- [ ] **Step 3: Commit**

```bash
cd /Users/caijiangnan/Desktop/Hybro/hybro-frontend
git add src/hooks/room/sse-handlers/index.ts
git commit -m "feat: display supervisor stage details in processing indicator

Reads the details field from processing_status SSE events and shows it
in the placeholder message during supervisor execution. Falls back to
existing default text when no details are provided."
```

---

### Task 5: Run full test suite and verify end-to-end

**Files:**
- No new files

- [ ] **Step 1: Run backend tests**

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass, including the new `test_supervisor_v2_improvements.py` tests.

- [ ] **Step 2: Run frontend tests**

Run: `cd /Users/caijiangnan/Desktop/Hybro/hybro-frontend && npm run test`
Expected: All tests pass. The SSE handler change is backward-compatible — `details` is optional.

- [ ] **Step 3: Smoke test with live server**

Start backend (`uvicorn main:app --reload`) and frontend (`npm run dev`).
Send a message in a supervisor-mode room. Observe:
1. Processing indicator shows stage-level details
2. Supervisor re-delegates when an agent returns empty/generic results (if applicable)
3. No regressions in non-supervisor rooms

- [ ] **Step 4: Final commit (if any fixups needed)**

Only if smoke testing revealed issues that needed fixes.
