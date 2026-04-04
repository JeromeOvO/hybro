# Supervisor V2 Improvements Design

**Date:** 2026-04-04
**Status:** Approved
**Scope:** Result quality evaluation + stage SSE notifications

## Problem

### 1. Supervisor blindly accepts low-quality agent results

The supervisor V2 loop (`SupervisorExecutor.run()`) dispatches agents and collects results. The supervisor LLM (`room_supervisor_service.decide_next()`) sees each agent's response in the trajectory summary — but only the first 500 characters. This is insufficient to assess quality.

When an agent returns a technically "successful" response (success=true, non-empty text) that contains no actionable content — e.g., a Twitch agent that says "I searched but couldn't find relevant creators" — the supervisor LLM sees the truncated preview and decides to SYNTHESIZE rather than re-delegate.

The result: the final synthesis includes placeholders like "Twitch results were limited" instead of making a second attempt with refined instructions.

### 2. No intermediate progress feedback during supervisor execution

The supervisor loop runs 30-90 seconds per user message turn. During this time, the frontend shows only "Processing..." with no indication of what the system is doing. Users don't know if the system is planning, waiting for agents, evaluating results, or synthesizing.

The only SSE events currently emitted during the supervisor loop are `AWAITING_INPUT` (for HITL/clarify). No stage-level progress is communicated.

This creates dead time that can lead to user confusion and misoperations (e.g., sending another message, refreshing, or canceling prematurely).

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Quality evaluation method | LLM evaluates with expanded context | The supervisor LLM is the right judge of result quality — programmatic checks (length, keywords) are too brittle |
| Response preview size | 3000 chars (up from 500) | Covers most responses in full while capping cost on verbose agents. 200K context window has room. |
| Quality evaluation prompt | Add explicit instructions to system prompt | The current prompt says "evaluate quality" but gives no criteria. Need concrete guidance. |
| Re-delegation budget | Share existing MAX_STEPS (default 8) | Re-delegations are just normal DELEGATE actions. Existing guards prevent infinite loops. No separate cap needed. |
| New action types | None | Re-delegation is already expressible as DELEGATE. No model changes needed. |
| SSE event type | Extend existing `processing_status` with `details` | Backend already supports `details` param, frontend already reads it for FAILED. Minimal changes. |
| SSE granularity | Stage-level only | 4-5 events per turn. Per-agent counting and re-delegation transparency are out of scope. |

## Architecture

### Part 1: Result Quality Evaluation

#### 1a. Expand response preview in trajectory summary

**File:** `services/room_supervisor_service.py`
**Method:** `_format_trajectory()` (~line 400)

**Current:**
```python
response_text[:500]
```

**New:**
```python
response_text[:3000]
```

This applies to the trajectory entries shown to the supervisor LLM when it calls `decide_next()`. The trajectory window (`_TRAJECTORY_WINDOW = 5`) limits how many entries are shown in full detail — older entries are collapsed to one-line summaries regardless of this change.

**Cost impact:** With 4 agents at ~2000-5000 chars each, the trajectory adds ~8-12K chars per `decide_next()` call (up from ~2K). Claude Opus has a 200K context window, so this fits comfortably. Cost increase is ~4-6x on the trajectory portion of the prompt, which is a small fraction of total supervisor cost.

#### 1b. Add quality evaluation instructions to system prompt

**File:** `services/room_supervisor_service.py`
**Method:** `decide_next()` — system prompt construction

Add the following block to the system prompt, before the action descriptions:

```
QUALITY EVALUATION — before choosing SYNTHESIZE or DONE:
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

#### 1c. No structural changes

- No new `ActionType` values
- No new models
- No new guards beyond the existing `_guard_consecutive_redelegation()`
- No separate re-delegation budget — uses existing `MAX_STEPS` (default 8)
- Existing guards remain:
  - Failure guard: agent removed after 2 consecutive failures
  - Success guard: agent removed after 3 consecutive successes

### Part 2: Supervisor Stage SSE Notifications

#### 2a. Backend: Emit stage details in SupervisorExecutor

**File:** `modules/SupervisorExecutor.py`
**Method:** `run()` — main loop

Add `send_processing_status()` calls with `details` at 5 points:

```python
# 1. Before decide_next() — each loop iteration (~line 239)
await self.sse_manager.send_processing_status(
    room_id, SSEProcessingStatus.PROCESSING, user_message_id,
    details="Planning next action...",
)

# 2. After DELEGATE action decided, before dispatch (~line 433)
await self.sse_manager.send_processing_status(
    room_id, SSEProcessingStatus.PROCESSING, user_message_id,
    details=f"Delegating to {len(action.targets)} agent(s)...",
)

# 3. After dispatch completes, before next iteration (~line 604)
await self.sse_manager.send_processing_status(
    room_id, SSEProcessingStatus.PROCESSING, user_message_id,
    details="Evaluating agent results...",
)

# 4. After SYNTHESIZE action decided, before synthesis call (~line 634)
await self.sse_manager.send_processing_status(
    room_id, SSEProcessingStatus.PROCESSING, user_message_id,
    details="Synthesizing responses...",
)

# 5. Budget exhaustion synthesis path (~line 880)
await self.sse_manager.send_processing_status(
    room_id, SSEProcessingStatus.PROCESSING, user_message_id,
    details="Synthesizing responses...",
)
```

These are best-effort notifications — if any `send_processing_status` call fails, it is logged and swallowed (existing pattern in the codebase). No exception propagation.

#### 2b. Frontend: Display details during PROCESSING

**File:** `hybro-frontend/src/hooks/room/sse-handlers/index.ts`
**Case:** `processing_status` handler, PROCESSING status

Currently the handler sets processing state but doesn't use `details` for PROCESSING (only for FAILED). Extend to store `details` when status is PROCESSING.

**File:** `hybro-frontend/src/stores/room-ui-store.ts`

The per-room state currently has `processing: boolean`. Add `processingDetails: string | null` alongside it. Set it when a PROCESSING status arrives with `details`. Clear it on terminal statuses (COMPLETED, FAILED, CANCELED) and when `setProcessing(false)` is called.

**File:** The UI component that renders the "Processing..." indicator

Read `processingDetails` and display as a subtitle:

```
Processing...
Delegating to 4 agent(s)...
```

Then updates to:

```
Processing...
Evaluating agent results...
```

Then:

```
Processing...
Synthesizing responses...
```

The details text transitions in place — no stacking, no history. Each new `processing_status` with `details` replaces the previous.

## Unchanged

- `ActionType` enum (DELEGATE, SYNTHESIZE, CLARIFY, DONE)
- `SupervisorAction`, `V2StepResult`, `TrajectoryEntry` models
- `SupervisorRunResult` model
- `MAX_STEPS` default (8)
- `_TRAJECTORY_WINDOW` (5 entries shown in full)
- `_guard_consecutive_redelegation()` logic
- Clarification cap (1 CLARIFY per message)
- Debate mode fast-path
- Crash recovery logic
- Checkpoint persistence
- Interrupt state handling (PUSH_NOTIFICATION, HITL_AGENT, HITL_SUPERVISOR)
- `synthesize_v2()` implementation
- `_fallback_v2_synthesis()` fallback

## Test Impact

### Part 1
- Tests for `decide_next()` that mock the LLM response: no change (prompt changes don't affect test structure)
- Tests for `_format_trajectory()`: update expected output to reflect 3000-char cap
- New test: verify truncation at 3000 chars for long responses
- No new integration tests needed — re-delegation is already a supported flow

### Part 2
- New tests for SupervisorExecutor: verify `send_processing_status` is called with correct `details` at each stage
- Frontend tests: verify `processingDetails` state updates on PROCESSING events with details
- No changes to existing `processing_status` tests (backward compatible — `details` is optional)

## Migration

No data migration needed. Both changes are purely behavioral:
- Part 1: Prompt and truncation changes take effect on next supervisor call
- Part 2: New SSE events are additive — old frontends that don't read `details` during PROCESSING are unaffected
