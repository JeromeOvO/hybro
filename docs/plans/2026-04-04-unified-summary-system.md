# Unified Summary System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify the two separate summary mechanisms (supervisor synthesis + coordinator summary) into a single `_emit_unified_summary()` entry point so every user message turn produces at most one "Summary Agent" message.

**Architecture:** A new routing method `_emit_unified_summary()` in `RoomMessageCenter` replaces all direct calls to `room_coordinator_service.on_room_user_message_completed()` and `emit_synthesis_message()`. If the supervisor already generated a synthesis, it's used directly; otherwise OpenAI generates the summary. A deterministic `message_id = f"summary-{user_message_id}"` and DB upsert provide idempotency.

**Tech Stack:** Python 3.12, FastAPI, MongoDB (motor), SSE, pytest

**Design doc:** `docs/UNIFIED_SUMMARY_SYSTEM_DESIGN.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `models/room.py` | Modify | Add `SUMMARY` to `CoordinatorAgentId` enum |
| `database/mongodb.py` | Modify | Add `upsert_room_agent_message()` method |
| `services/database_service.py` | Modify | Add `upsert_room_agent_message()` wrapper |
| `services/room_coordinator_service.py` | Modify | Update BFS filter, deprecate old entry points |
| `modules/RoomMessageCenter.py` | Modify | Add `_emit_unified_summary()`, rewire 3 call sites |
| `modules/SupervisorExecutor.py` | Modify | Remove `synth-placeholder` SSE emissions |
| `../hybro-frontend/src/lib/system-agents.ts` | Modify | Unify all summary IDs to "Summary Agent" |
| `tests/test_models.py` | Modify | Add `SUMMARY` to enum test |
| `tests/test_room_coordinator_service.py` | Modify | Rewrite tests for new unified path |
| `tests/test_unified_summary.py` | Create | New tests for `_emit_unified_summary()` |

---

### Task 1: Add `SUMMARY` to `CoordinatorAgentId` enum

**Files:**
- Modify: `models/room.py:12-25`
- Modify: `tests/test_models.py:167-177`

- [ ] **Step 1: Update the enum**

In `models/room.py`, replace the current `CoordinatorAgentId` class (lines 12-25):

```python
class CoordinatorAgentId(StrEnum):
    """Well-known synthetic agent IDs used for coordinator/system-generated messages.

    These are never real agent IDs in the database; they identify the source of
    messages produced by the orchestration layer (supervisor, debate summary, etc.).
    """

    SUPERVISOR_ERROR = "supervisor_error"
    SUPERVISOR_SYNTHESIS = "supervisor_synthesis"
    SUPERVISOR_CLARIFY = "supervisor_clarify"
    SUMMARY = "summary"
    SYSTEM = "system"
    # Deprecated — kept for historical data backward compatibility.
    # New writes must use SUMMARY instead.
    DEBATE_SUMMARY = "debate_summary"
    NON_DEBATE_SUMMARY = "non_debate_summary"
```

- [ ] **Step 2: Update the enum test**

In `tests/test_models.py`, replace the `TestCoordinatorAgentId` class (lines 167-177):

```python
class TestCoordinatorAgentId:
    """Tests for CoordinatorAgentId enum."""

    def test_coordinator_agent_ids(self):
        """Should have expected coordinator agent IDs."""
        assert CoordinatorAgentId.SUPERVISOR_ERROR == "supervisor_error"
        assert CoordinatorAgentId.SUPERVISOR_SYNTHESIS == "supervisor_synthesis"
        assert CoordinatorAgentId.SUPERVISOR_CLARIFY == "supervisor_clarify"
        assert CoordinatorAgentId.SUMMARY == "summary"
        assert CoordinatorAgentId.SYSTEM == "system"
        # Deprecated but still present for backward compat
        assert CoordinatorAgentId.DEBATE_SUMMARY == "debate_summary"
        assert CoordinatorAgentId.NON_DEBATE_SUMMARY == "non_debate_summary"
```

- [ ] **Step 3: Run test to verify**

Run: `pytest tests/test_models.py::TestCoordinatorAgentId -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add models/room.py tests/test_models.py
git commit -m "feat: add SUMMARY to CoordinatorAgentId, deprecate old summary IDs"
```

---

### Task 2: Add `upsert_room_agent_message` to DB layer

**Files:**
- Modify: `database/mongodb.py:946-953`
- Modify: `services/database_service.py:822-837`

- [ ] **Step 1: Add method to mongodb.py**

In `database/mongodb.py`, add the following method immediately after `add_room_agent_message` (after line 953):

```python
    async def upsert_room_agent_message(self, room_agent_message: RoomAgentMessage) -> None:
        """
        Insert or replace a room agent message by message_id.
        Used for idempotent summary emission — deterministic message_id
        ensures at most one summary per user message turn.
        """
        await self.room_agent_messages_collection.replace_one(
            {"message_id": room_agent_message.message_id},
            room_agent_message.model_dump(mode="json"),
            upsert=True,
        )
```

- [ ] **Step 2: Add wrapper to database_service.py**

In `services/database_service.py`, add the following method immediately after `add_room_agent_message` (after line 837):

```python
    async def upsert_room_agent_message(
        self, room_agent_message: RoomAgentMessage
    ) -> bool:
        """
        Insert or replace a room agent message by message_id (idempotent).
        """
        try:
            await self.mongo.upsert_room_agent_message(room_agent_message)
            return True
        except Exception as e:
            logger.error(
                f"Failed to upsert room agent message {room_agent_message.message_id}: {str(e)}"
            )
            return False
```

- [ ] **Step 3: Verify import exists**

`RoomAgentMessage` is already imported in both files. No import changes needed.

- [ ] **Step 4: Commit**

```bash
git add database/mongodb.py services/database_service.py
git commit -m "feat: add upsert_room_agent_message for idempotent summary writes"
```

---

### Task 3: Update BFS filter in RoomCoordinatorService

**Files:**
- Modify: `services/room_coordinator_service.py:119`

- [ ] **Step 1: Update the filter**

In `services/room_coordinator_service.py`, replace line 119:

```python
                    if msg.agent_id in ("debate_summary", "non_debate_summary"):
                        continue
```

With:

```python
                    # Exclude all synthetic coordinator messages (new + historical IDs)
                    if (
                        msg.extend_info
                        and isinstance(msg.extend_info, dict)
                        and msg.extend_info.get("is_coordinator_summary")
                    ) or msg.agent_id in (
                        "debate_summary",
                        "non_debate_summary",
                        "summary",
                        "supervisor_synthesis",
                        "supervisor_error",
                        "supervisor_clarify",
                    ):
                        continue
```

- [ ] **Step 2: Commit**

```bash
git add services/room_coordinator_service.py
git commit -m "fix: update BFS filter to exclude all synthetic coordinator messages"
```

---

### Task 4: Remove `synth-placeholder` SSE from SupervisorExecutor

**Files:**
- Modify: `modules/SupervisorExecutor.py:661-676,895-907`

- [ ] **Step 1: Remove normal-path placeholder (lines 661-676)**

In `modules/SupervisorExecutor.py`, delete the placeholder block in the normal synthesis path. Replace lines 661-676:

```python
                    # Notify the frontend that synthesis is starting so the
                    # UI shows a progress indicator during the LLM call.
                    # ID uses a predictable pattern so RoomMessageCenter can
                    # clean it up when the real synthesis message arrives.
                    synth_placeholder_id = f"synth-placeholder-{user_message_id}"
                    await self.sse_manager.broadcast_to_room(
                        room_id,
                        "task_submitted",
                        {
                            "message_id": synth_placeholder_id,
                            "status": "working",
                            "agent_name": "HYBRO AI",
                            "task_content": "Synthesizing responses…",
                            "related_message_id": user_message_id,
                        },
                    )
```

With nothing (delete the entire block). The synthesis LLM call on line 678 stays.

- [ ] **Step 2: Remove budget-path placeholder (lines 895-907)**

In `modules/SupervisorExecutor.py`, delete the budget-path placeholder block. Replace lines 895-907:

```python
            # Notify frontend about synthesis (same pattern as normal path)
            synth_placeholder_id = f"synth-placeholder-{user_message_id}"
            await self.sse_manager.broadcast_to_room(
                room_id,
                "task_submitted",
                {
                    "message_id": synth_placeholder_id,
                    "status": "working",
                    "agent_name": "HYBRO AI",
                    "task_content": "Synthesizing responses…",
                    "related_message_id": user_message_id,
                },
            )
```

With nothing (delete the entire block). The budget synthesis LLM call stays.

- [ ] **Step 3: Commit**

```bash
git add modules/SupervisorExecutor.py
git commit -m "refactor: remove synth-placeholder SSE from SupervisorExecutor

Summary placeholder is now owned by _emit_unified_summary() in
RoomMessageCenter."
```

---

### Task 5: Implement `_emit_unified_summary()` in RoomMessageCenter

**Files:**
- Modify: `modules/RoomMessageCenter.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_unified_summary.py`:

```python
"""Tests for RoomMessageCenter._emit_unified_summary."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.room import CoordinatorAgentId


@pytest.fixture
def rmc():
    """Build a RoomMessageCenter with mocked dependencies."""
    from modules.RoomMessageCenter import RoomMessageCenter

    center = RoomMessageCenter.__new__(RoomMessageCenter)
    center.sse_manager = AsyncMock()
    center.database_service = AsyncMock()
    center.room_coordinator_service = AsyncMock()
    center.room_services = AsyncMock()
    center.openai_service = AsyncMock()
    return center


class TestEmitUnifiedSummary:
    """Tests for _emit_unified_summary."""

    @pytest.mark.asyncio
    async def test_supervisor_synthesis_used_directly(self, rmc):
        """When synthesis_text is provided, it's used as-is without calling OpenAI."""
        rmc.database_service.upsert_room_agent_message = AsyncMock(return_value=True)

        await rmc._emit_unified_summary(
            room_id="room-1",
            user_message_id="msg-1",
            synthesis_text="Supervisor generated this.",
        )

        # OpenAI should NOT be called
        rmc.openai_service.summarize_agent_responses.assert_not_awaited()
        # DB upsert should be called with deterministic message_id
        rmc.database_service.upsert_room_agent_message.assert_awaited_once()
        saved_msg = rmc.database_service.upsert_room_agent_message.call_args[0][0]
        assert saved_msg.message_id == "summary-msg-1"
        assert saved_msg.agent_id == CoordinatorAgentId.SUMMARY
        assert saved_msg.extend_info["summary_origin"] == "supervisor"
        # SSE agent_response should be sent
        rmc.sse_manager.send_agent_response.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_openai_fallback_with_trajectory(self, rmc):
        """When no synthesis_text, uses OpenAI with trajectory_responses."""
        rmc.database_service.upsert_room_agent_message = AsyncMock(return_value=True)
        rmc.openai_service.summarize_agent_responses = AsyncMock(
            return_value="OpenAI summary."
        )

        await rmc._emit_unified_summary(
            room_id="room-1",
            user_message_id="msg-1",
            trajectory_responses=[
                {"agent_name": "A", "message": "text A"},
                {"agent_name": "B", "message": "text B"},
            ],
            is_debate=True,
        )

        rmc.openai_service.summarize_agent_responses.assert_awaited_once_with(
            [
                {"agent_name": "A", "message": "text A"},
                {"agent_name": "B", "message": "text B"},
            ],
            mode="debate",
        )
        saved_msg = rmc.database_service.upsert_room_agent_message.call_args[0][0]
        assert saved_msg.extend_info["summary_origin"] == "coordinator"
        assert saved_msg.extend_info["summary_type"] == "debate"

    @pytest.mark.asyncio
    async def test_fewer_than_2_responses_skips(self, rmc):
        """When trajectory has < 2 responses, no summary emitted."""
        await rmc._emit_unified_summary(
            room_id="room-1",
            user_message_id="msg-1",
            trajectory_responses=[
                {"agent_name": "A", "message": "only one"},
            ],
        )

        rmc.database_service.upsert_room_agent_message.assert_not_awaited()
        # Placeholder should be dismissed
        rmc.sse_manager.send_task_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_deterministic_message_id(self, rmc):
        """message_id is always summary-{user_message_id}."""
        rmc.database_service.upsert_room_agent_message = AsyncMock(return_value=True)

        await rmc._emit_unified_summary(
            room_id="room-1",
            user_message_id="msg-abc-123",
            synthesis_text="test",
        )

        rmc.sse_manager.send_task_submitted.assert_awaited_once()
        call_kwargs = rmc.sse_manager.send_task_submitted.call_args[1]
        assert call_kwargs["message_id"] == "summary-msg-abc-123"

    @pytest.mark.asyncio
    async def test_failure_cleans_up_placeholder(self, rmc):
        """On exception, task_update(status=failed) is sent to dismiss spinner."""
        rmc.database_service.upsert_room_agent_message = AsyncMock(
            side_effect=Exception("DB down")
        )

        await rmc._emit_unified_summary(
            room_id="room-1",
            user_message_id="msg-1",
            synthesis_text="will fail on save",
        )

        # Should attempt cleanup
        rmc.sse_manager.send_task_update.assert_awaited()
        cleanup_kwargs = rmc.sse_manager.send_task_update.call_args[1]
        assert cleanup_kwargs["status"] == "failed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_unified_summary.py -v`
Expected: FAIL with `AttributeError: 'RoomMessageCenter' object has no attribute '_emit_unified_summary'`

- [ ] **Step 3: Implement `_emit_unified_summary()`**

In `modules/RoomMessageCenter.py`, add this method before the `# ------------------------------------------------------------------` monitoring section (before line 1765). Also add the `openai_service` import at the top of `__init__`:

First, in `__init__` (around line 58), add:

```python
        self.openai_service = openai_service
```

And add the import at the top (after the existing service imports around line 40):

```python
from services.openai_service import openai_service
```

Then add the method:

```python
    # ------------------------------------------------------------------
    # Unified summary emission
    # ------------------------------------------------------------------

    async def _emit_unified_summary(
        self,
        room_id: str,
        user_message_id: str,
        *,
        synthesis_text: str | None = None,
        trajectory_responses: list[dict[str, str]] | None = None,
        is_debate: bool = False,
    ) -> None:
        """Emit a single unified summary message for a user message turn.

        Routing logic:
        - If synthesis_text is provided (supervisor path), use it directly.
        - Otherwise, collect agent responses and call OpenAI to generate.
        - Deterministic message_id ensures at most one summary per turn.
        """
        summary_message_id = f"summary-{user_message_id}"

        try:
            # 1. Placeholder SSE
            await self.sse_manager.send_task_submitted(
                room_id=room_id,
                message_id=summary_message_id,
                task_id=summary_message_id,
                agent_name="Summary Agent",
                agent_id=CoordinatorAgentId.SUMMARY,
                status="working",
                related_message_id=user_message_id,
                task_content="Summarizing agent responses…",
            )

            # 2. Determine content
            if synthesis_text is not None:
                content = synthesis_text
                origin = "supervisor"
            else:
                # Collect agent responses
                if trajectory_responses:
                    agent_responses = trajectory_responses
                else:
                    agent_messages = await self.room_coordinator_service._collect_agent_messages_for_user_message(
                        user_message_id
                    )
                    agent_responses = []
                    for msg in agent_messages:
                        if (
                            msg.extend_info
                            and isinstance(msg.extend_info, dict)
                            and msg.extend_info.get("is_coordinator_summary")
                        ) or msg.agent_id in (
                            "debate_summary", "non_debate_summary", "summary",
                            "supervisor_synthesis", "supervisor_error", "supervisor_clarify",
                        ):
                            continue
                        task = msg.message_content and msg.message_content.message_task
                        if task and task.status and task.status.state != TaskState.completed:
                            continue
                        from common.utils.a2a_helpers import extract_agent_text_from_room_message
                        text = extract_agent_text_from_room_message(msg)
                        if text and msg.agent_id:
                            agent_name = await self.database_service.get_agent_name_by_agent_id(
                                msg.agent_id
                            )
                            agent_responses.append({
                                "agent_name": agent_name or msg.agent_id,
                                "message": text,
                            })

                if len(agent_responses) < 2:
                    await self.sse_manager.send_task_update(
                        room_id=room_id,
                        message_id=summary_message_id,
                        status="completed",
                        agent_id=CoordinatorAgentId.SUMMARY,
                    )
                    return

                mode = "debate" if is_debate else "non_debate"
                content = await self.openai_service.summarize_agent_responses(
                    agent_responses, mode=mode
                )
                origin = "coordinator"

                if not content:
                    await self.sse_manager.send_task_update(
                        room_id=room_id,
                        message_id=summary_message_id,
                        status="completed",
                        agent_id=CoordinatorAgentId.SUMMARY,
                    )
                    return

            # 3. Build and persist
            from a2a.types import Message, Role, Task, TaskState as A2ATaskState, TaskStatus, TextPart
            from common.utils.time import utcnow
            from models.room import MessageContent, RoomAgentMessage

            summary_a2a_message = Message(
                message_id=summary_message_id,
                role=Role.agent,
                parts=[TextPart(text=content)],
                context_id=summary_message_id,
                metadata={},
            )
            task_status = TaskStatus(
                state=A2ATaskState.completed,
                timestamp=utcnow().isoformat(),
                message=summary_a2a_message,
            )
            summary_task = Task(
                id=summary_message_id,
                context_id=summary_message_id,
                status=task_status,
                history=[summary_a2a_message],
            )

            user_message = await self.database_service.get_room_user_message_by_message_id(
                user_message_id
            )
            user_id = user_message.user_id if user_message else None

            summary_agent_message = RoomAgentMessage(
                room_id=room_id,
                message_id=summary_message_id,
                agent_id=CoordinatorAgentId.SUMMARY,
                related_message_id=user_message_id,
                user_id=user_id,
                message_content=MessageContent(message_task=summary_task),
                message_created_at=utcnow(),
                extend_info={
                    "is_coordinator_summary": True,
                    "source_user_message_id": user_message_id,
                    "summary_type": "debate" if is_debate else "non_debate",
                    "summary_origin": origin,
                },
                task_content=content,
            )

            await self.database_service.upsert_room_agent_message(summary_agent_message)

            # 4. Emit final SSE
            await self.sse_manager.send_agent_response(
                room_id,
                summary_message_id,
                CoordinatorAgentId.SUMMARY,
                content,
                related_message_id=user_message_id,
            )

        except Exception as exc:
            logger.error(
                "RoomMessageCenter: _emit_unified_summary failed for room %s "
                "user message %s: %s",
                room_id, user_message_id, exc, exc_info=True,
            )
            try:
                await self.sse_manager.send_task_update(
                    room_id=room_id,
                    message_id=summary_message_id,
                    status="failed",
                    agent_id=CoordinatorAgentId.SUMMARY,
                )
            except Exception:
                pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_unified_summary.py -v`
Expected: PASS (all 5 tests)

- [ ] **Step 5: Commit**

```bash
git add modules/RoomMessageCenter.py tests/test_unified_summary.py
git commit -m "feat: implement _emit_unified_summary() with idempotent upsert

Single entry point for all summary emission. Deterministic message_id,
placeholder SSE lifecycle, supervisor synthesis passthrough or OpenAI
generation fallback."
```

---

### Task 6: Rewire call sites in RoomMessageCenter

**Files:**
- Modify: `modules/RoomMessageCenter.py:415-418,1369-1433,1754-1761`

- [ ] **Step 1: Rewire QueueExecutor completion (line 415-418)**

Replace lines 415-418:

```python
        # QueueResult.COMPLETED — proceed with coordinator summary + completion.
        await self.room_coordinator_service.on_room_user_message_completed(
            room_id, room_user_message_id
        )
```

With:

```python
        # QueueResult.COMPLETED — emit unified summary + completion.
        is_debate = bool(
            room and room.extend_info and room.extend_info.get("debateMode")
        )
        await self._emit_unified_summary(
            room_id, room_user_message_id, is_debate=is_debate
        )
```

Note: `room` variable is available in this scope — check the method context. If `room` is not in scope, fetch it:

```python
        room = await self.database_service.get_room_by_room_id(room_id)
        is_debate = bool(
            room and room.extend_info and room.extend_info.get("debateMode")
        )
        await self._emit_unified_summary(
            room_id, room_user_message_id, is_debate=is_debate
        )
```

- [ ] **Step 2: Rewire supervisor V2 completion (lines 1369-1433)**

Replace the entire `case RunStatus.COMPLETED:` block (lines 1369-1433):

```python
                synthesis_emitted = False
                if result.synthesis_text:
                    # Reuse the placeholder ID that SupervisorExecutor sent
                    # before synthesis started ("Synthesizing responses…").
                    # The agent_response below will overwrite that placeholder
                    # with the actual synthesis content.
                    synth_message_id = f"synth-placeholder-{user_message_id}"
                    try:
                        await self.sse_manager.send_task_submitted(
                            room_id=room_id,
                            message_id=synth_message_id,
                            task_id=synth_message_id,
                            agent_name="Agent",
                            agent_id=CoordinatorAgentId.SUPERVISOR_SYNTHESIS,
                            status="working",
                            related_message_id=user_message_id,
                            task_content="Summarizing agent responses…",
                        )
                    except Exception:
                        logger.warning(
                            "RoomMessageCenter: Failed to send synthesis task_submitted SSE"
                        )
                    try:
                        await self.room_coordinator_service.emit_synthesis_message(
                            room_id=room_id,
                            room_user_message_id=user_message_id,
                            synthesis_text=result.synthesis_text,
                            coordinator_agent_id=CoordinatorAgentId.SUPERVISOR_SYNTHESIS,
                            message_id=synth_message_id,
                        )
                        synthesis_emitted = True
                    except Exception as e:
                        logger.error(
                            "RoomMessageCenter: V2 synthesis emission failed: %s",
                            e,
                            exc_info=True,
                        )
                if not synthesis_emitted:
                    # Extract agent responses from the trajectory so the coordinator
                    # doesn't need to re-read from DB.  Relay agents may not have
                    # written their message_content.message_task.history yet, which
                    # would cause the BFS path to find empty texts and skip the summary.
                    from models.supervisor_v2 import ActionType  # noqa: PLC0415

                    trajectory_responses = [
                        {"agent_name": step.agent_name, "message": step.response_text}
                        for entry in result.trajectory.entries
                        if entry.action.action == ActionType.DELEGATE
                        for step in entry.results
                        if step.success and step.response_text
                    ]
                    if trajectory_responses:
                        await self.room_coordinator_service.on_room_user_message_completed(
                            room_id,
                            user_message_id,
                            trajectory_responses=trajectory_responses,
                        )
                    else:
                        await self.room_coordinator_service.on_room_user_message_completed(
                            room_id, user_message_id
                        )
                await self.sse_manager.send_processing_status(
                    room_id, SSEProcessingStatus.COMPLETED, user_message_id
                )
```

With:

```python
                from models.supervisor_v2 import ActionType  # noqa: PLC0415

                trajectory_responses = [
                    {"agent_name": step.agent_name, "message": step.response_text}
                    for entry in result.trajectory.entries
                    if entry.action.action == ActionType.DELEGATE
                    for step in entry.results
                    if step.success and step.response_text
                ]
                await self._emit_unified_summary(
                    room_id,
                    user_message_id,
                    synthesis_text=result.synthesis_text,
                    trajectory_responses=trajectory_responses,
                    is_debate=room_config.is_debate_mode if room_config else False,
                )
                await self.sse_manager.send_processing_status(
                    room_id, SSEProcessingStatus.COMPLETED, user_message_id
                )
```

- [ ] **Step 3: Rewire HITL resume completion (lines 1754-1761)**

Replace lines 1754-1761:

```python
        if result.needs_completion and result.room_id and result.user_message_id:
            await self.room_coordinator_service.on_room_user_message_completed(
                result.room_id, result.user_message_id
            )
            await self.sse_manager.send_processing_status(
                result.room_id, SSEProcessingStatus.COMPLETED, result.user_message_id
            )
            await self._log_room_memory_stats(result.room_id)
```

With:

```python
        if result.needs_completion and result.room_id and result.user_message_id:
            room = await self.database_service.get_room_by_room_id(result.room_id)
            is_debate = bool(
                room and room.extend_info and room.extend_info.get("debateMode")
            )
            await self._emit_unified_summary(
                result.room_id, result.user_message_id, is_debate=is_debate
            )
            await self.sse_manager.send_processing_status(
                result.room_id, SSEProcessingStatus.COMPLETED, result.user_message_id
            )
            await self._log_room_memory_stats(result.room_id)
```

- [ ] **Step 4: Run existing tests**

Run: `pytest tests/ -v -k "coordinator or supervisor or room_message" --timeout=30`
Expected: Some tests may need updating (Task 7 handles that). Core tests should still pass.

- [ ] **Step 5: Commit**

```bash
git add modules/RoomMessageCenter.py
git commit -m "refactor: rewire 3 call sites to use _emit_unified_summary()

- QueueExecutor completion path
- Supervisor V2 RunStatus.COMPLETED
- HITL resume completion

Removes synthesis_emitted flag and dual fallback logic."
```

---

### Task 7: Update existing tests

**Files:**
- Modify: `tests/test_room_coordinator_service.py`

- [ ] **Step 1: Update coordinator service tests**

The tests in `tests/test_room_coordinator_service.py` test `on_room_user_message_completed()` which is now deprecated. These tests remain valid for the deprecated method (it still exists, just not called from RoomMessageCenter). Add a note at the top:

In `tests/test_room_coordinator_service.py`, update the module docstring (line 1):

```python
"""Unit tests for RoomCoordinatorService.on_room_user_message_completed.

NOTE: on_room_user_message_completed is deprecated. New summary emission goes
through RoomMessageCenter._emit_unified_summary(). These tests are kept to
verify the deprecated method still works for any direct callers.

Covers the ``trajectory_responses`` fast-path that was added to avoid a
race condition where relay agents' DB messages are not yet written when the
coordinator tries to read them.
"""
```

- [ ] **Step 2: Run all tests**

Run: `pytest tests/test_room_coordinator_service.py tests/test_unified_summary.py tests/test_models.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_room_coordinator_service.py
git commit -m "docs: mark coordinator service tests as testing deprecated path"
```

---

### Task 8: Update frontend system-agents.ts

**Files:**
- Modify: `../hybro-frontend/src/lib/system-agents.ts`

- [ ] **Step 1: Update SYSTEM_AGENTS**

Replace the entire content of `../hybro-frontend/src/lib/system-agents.ts`:

```typescript
/**
 * Built-in system agents that are not real A2A agents in the database.
 * These are synthetic agent IDs created by the backend's orchestration layer
 * to author summary, HITL, and system messages.
 */

export interface SystemAgentInfo {
  name: string
  description: string
}

const SUMMARY_DESCRIPTION =
  'A built-in agent that summarizes responses from multiple agents in a room.'

export const SYSTEM_AGENTS: Record<string, SystemAgentInfo> = {
  supervisor_hitl: {
    name: 'Question & Answer',
    description:
      'A built-in agent that facilitates human-in-the-loop interactions, collecting clarifications and confirmations from the user.',
  },
  // Canonical summary agent ID (all new backend writes use this)
  summary: {
    name: 'Summary Agent',
    description: SUMMARY_DESCRIPTION,
  },
  // Historical backward compatibility — all map to "Summary Agent"
  supervisor_synthesis: {
    name: 'Summary Agent',
    description: SUMMARY_DESCRIPTION,
  },
  debate_summary: {
    name: 'Summary Agent',
    description: SUMMARY_DESCRIPTION,
  },
  non_debate_summary: {
    name: 'Summary Agent',
    description: SUMMARY_DESCRIPTION,
  },
}

export function isSystemAgent(agentId: string | undefined): boolean {
  return !!agentId && agentId in SYSTEM_AGENTS
}

export function getSystemAgentName(agentId: string): string | undefined {
  return SYSTEM_AGENTS[agentId]?.name
}
```

- [ ] **Step 2: Run frontend tests (if available)**

Run: `cd ../hybro-frontend && npm run test -- --run 2>/dev/null || echo "No matching tests"`
Expected: PASS or no matching tests

- [ ] **Step 3: Commit**

```bash
cd ../hybro-frontend
git add src/lib/system-agents.ts
git commit -m "refactor: unify all summary agent IDs to display as Summary Agent"
cd ../hybro-multi-agents-backend
```

---

### Task 9: Deprecation annotations on RoomCoordinatorService

**Files:**
- Modify: `services/room_coordinator_service.py:39,246`

- [ ] **Step 1: Add deprecation warnings**

In `services/room_coordinator_service.py`, update the `on_room_user_message_completed` method docstring (line 39):

Add `import warnings` at top of file if not present.

Update the method starting at line 39:

```python
    async def on_room_user_message_completed(
        self,
        room_id: str,
        room_user_message_id: str,
        trajectory_responses: list[dict[str, str]] | None = None,
    ) -> None:
        """
        .. deprecated::
            Use ``RoomMessageCenter._emit_unified_summary()`` instead.
            This method is kept for backward compatibility but is no longer
            called from the main orchestration paths.
```

Update `emit_synthesis_message` docstring (line 246):

```python
    async def emit_synthesis_message(
        self,
        room_id: str,
        room_user_message_id: str,
        synthesis_text: str,
        coordinator_agent_id: str = CoordinatorAgentId.SUPERVISOR_SYNTHESIS,
        message_id: str | None = None,
    ) -> None:
        """Emit a synthesis/summary message to the room.

        Still used for SUPERVISOR_ERROR and SUPERVISOR_CLARIFY messages.
        For summary emission, use ``RoomMessageCenter._emit_unified_summary()``.
```

- [ ] **Step 2: Commit**

```bash
git add services/room_coordinator_service.py
git commit -m "docs: deprecate old summary entry points in RoomCoordinatorService"
```

---

### Task 10: Final integration verification

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -v --timeout=60`
Expected: All tests PASS

- [ ] **Step 2: Verify no remaining references to old patterns in hot paths**

Run: `grep -rn "on_room_user_message_completed\|emit_synthesis_message.*SUPERVISOR_SYNTHESIS\|synthesis_emitted" modules/ --include="*.py"`
Expected: No results in `modules/RoomMessageCenter.py` (only in deprecated `room_coordinator_service.py`)

- [ ] **Step 3: Start server and smoke test**

Run: `uvicorn main:app --reload`
Send a message to a room with 2+ agents and verify:
- Only one "Summary Agent" message appears
- No stuck spinners
- Summary content is correct

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore: unified summary system refactor complete

See docs/UNIFIED_SUMMARY_SYSTEM_DESIGN.md for design details."
```
