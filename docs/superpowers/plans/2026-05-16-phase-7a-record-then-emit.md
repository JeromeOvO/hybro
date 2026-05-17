# Phase 7a Record Then Emit Implementation Plan

> **For agentic workers:** REQUIRED: Use `superpowers:subagent-driven-development` if subagents are available, or `superpowers:executing-plans` to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move run-lifecycle recording out of `SSEManager.send_processing_status()` and into every production processing-status caller so Delivery can later become a pure transport.

**Architecture:** Add temporary lifecycle helpers in `services/run_lifecycle_service.py` that record processing status, return the `run_event` payload, and emit the legacy `run_event` SSE when enabled through the same `SSEManager` instance used by the caller. Each migrated lifecycle owner invokes that helper before the existing `sse_manager.send_processing_status()` call. `services/sse_services.py` keeps terminal deduplication, client request ID resolution, frame construction, and broadcast, but stops importing or calling `run_command_handler` and stops emitting `run_event`.

**Tech Stack:** Python 3.11+, pytest, pytest-asyncio, existing FastAPI/SSE test helpers, AST-based gate tests, no new dependencies.

---

## Scope

Include:
- Create branch `phase-7a-record-then-emit` from current `main`.
- Update `RunLifecycleService.record_processing_status()` to return the `dict | None` payload from `RunCommandHandler.record_processing_status()`.
- Add `record_and_maybe_broadcast_run_event()` and `broadcast_run_event_payload()` in `services/run_lifecycle_service.py`.
- Remove lifecycle recording and `run_event` broadcasting from `SSEManager.send_processing_status()`.
- Migrate all production `send_processing_status()` callers under `modules/`, `services/`, `api/`, and `jobs/` that own run lifecycle state to call the lifecycle helper before sending.
- Add a manifest-backed AST gate for production `send_processing_status()` call sites.
- Add golden/order tests for the `sendMessage` path and HITL/resolve queue-resume path.
- Preserve existing frontend SSE frame format and Phase 7a legacy ordering: `record_processing_status()` -> optional `run_event` SSE -> `processing_status` SSE.

Exclude:
- Do not extract `delivery/`; Phase 6 owns that.
- Do not introduce `execution/`, `ExecutionFacade`, or `RunLifecyclePort`; Phase 7b owns that.
- Do not replace `sse_manager.send_processing_status()` with `EventPublisher.emit()`.
- Do not change API route request/response shapes.
- Do not modify `services/run_command_handler.py` behavior.
- Do not add new dependencies or a new event bus.
- Do not hide migrated sends behind broad new abstractions. The only allowed temporary helper is the lifecycle helper described here.
- Do not convert legacy Workflow into an Execution run-lifecycle owner. Per `docs/MODULAR_DECOUPLING_DESIGN.md`, legacy Workflow is decommissioned, not wrapped.
- Do not claim full Delivery extraction readiness for every non-run business side effect. Phase 7a removes the embedded run-lifecycle write from Delivery; a separate pre-Phase-6 gate must audit or reorder other business writes that still occur after `processing_status` emits.

## Design Alignment Review Repairs

This plan was reviewed against `docs/MODULAR_DECOUPLING_DESIGN.md` and repaired for the following Phase 7a constraints: section 3.3 Rule 6, section 4.5 EventPublisher side-effect ordering, section 5.4 Processing Status Call Flow, section 10 invariant 6, and decision 14 in section 11.

**Pass 1: Rule 6 / A1 ownership**
- Issue found: the original plan allowed `run_lifecycle_service.record_processing_status(...)` as a valid preceding call even when the legacy `run_event` SSE was expected. That could satisfy "record before send" while dropping the existing `run_event` stream.
- Fix: production lifecycle entries with `expects_run_event_sse=true` must call `record_and_maybe_broadcast_run_event(...)`; bare `record_processing_status(...)` is not sufficient for migrated callers.
- Issue found: the helper always imported the global `services.sse_services.sse_manager`, while many current callers use `self.sse_manager`. That can split `run_event` and `processing_status` across different managers in tests or future injected wiring.
- Fix: helpers accept an optional `sse` argument and callers pass the same manager object used for `send_processing_status()`.
- Issue found: section 5.4's final EventPublisher pseudocode lists `ProcessingStatusEvent` before `RunEventNotification`, while the current legacy `SSEManager` emits `run_event` before `processing_status`.
- Fix: Phase 7a explicitly preserves the existing legacy SSE order to avoid frontend behavior changes while still satisfying the hard design invariant: business side effects complete before Delivery emits. Phase 7b owns any final EventPublisher order reconciliation.

**Pass 2: Module ownership**
- Issue found: `modules/WorkflowCenter.py` was treated as an Execution lifecycle caller. The design document explicitly says legacy Workflow is deleted, not wrapped, and Workflow is not part of the Phase 7 Execution boundary.
- Fix: keep the legacy Workflow cancellation `processing_status` as a manifest-covered transport-only exception with a concrete decommission reason. Do not write run lifecycle state for it.
- Issue found: the Jobs watchdog path was described but not covered by the AST gate. Jobs are a separate module in the design, but `_fail_stale_runs()` already owns a timeout-specific run event.
- Fix: include `jobs/` in AST discovery and manifest coverage. The watchdog uses `append_run_timeout_failure()` once in the enabled-dual-write path; only when that returns a payload does it broadcast the run event and send the failed `processing_status`. A separate dual-write-disabled compatibility branch may send transport-only `FAILED` without lifecycle recording. It must not call generic `record_processing_status()` again.

**Pass 3: Run identity and final Delivery extraction**
- Issue found: task-notification and agent-response paths can send frontend `processing_status` for an agent message while the canonical orchestration run is the root user message. Blindly matching helper `message_id` to the SSE `message_id` preserves the old Delivery side effect, but conflicts with the design's "orchestration run_id == trigger user message_id" model.
- Fix: `services/task_notification_service.py` remains transport-only because it reports per-agent task status and root completion is emitted later by `RoomMessageCenter`. Generic `agent_response_handler._on_processing_status()` records only when `AgentEvent.lifecycle_message_id` is explicitly populated with a validated canonical root user message id. Relay processing-status events may populate that field from hub `data.user_message_id` only after validating it against `msg.turn_id` or a chain-resolved canonical root; non-canonical agent processing-status events remain transport-only unless a tested resolver walks the message chain to the root user message.
- Issue found: the manifest keyed call sites by line number, making the gate fragile after harmless edits.
- Fix: the manifest uses a stable `call_id` plus normalized expressions. Line numbers remain audit metadata only.

**Pass 4: Delivery dedup is not lifecycle control**
- Issue found: a previous draft moved terminal dedup into a caller-side reservation before lifecycle recording. That made Delivery a precondition for business writes and created partial-failure suppression risk.
- Fix: remove the reservation API. Callers record first; `RunCommandHandler` idempotency decides whether a new run event payload exists. `SSEManager` terminal dedup remains a pure transport concern inside `send_processing_status()`.
- Issue found: some frontend `COMPLETED` statuses are soft spinner-clears while the run remains awaiting input, for example clarification prompts and clarify-resume retry failures.
- Fix: mark those soft-complete `processing_status` sends pure transport-only. Do not place any lifecycle helper immediately before the frontend `COMPLETED` send; the AST gate's negative assertion must remain simple and strict for these call sites.
- Issue found: `create_and_parse_user_message()` emitted `PROCESSING` but does not start the terminalizing execution path; it saves/parses a user message and creates agent messages, while the old external processing endpoint is deprecated.
- Fix: remove the create/parse `PROCESSING` send. Preserve `client_request_id` correlation on the persisted message, but do not emit a processing-status frame or record run lifecycle for this path unless a future change adds a real terminalizing execution flow.

**Pass 5: Non-run business side effects after emit**
- Issue found: Phase 7a's record-then-emit migration fixes the run-lifecycle side effect, but other business side effects can still occur after a `processing_status` emit, for example `RoomMessageCenter._notify_all_non_terminal_tasks_failed(...)` after a failed room-lock send and `turn_event_appender.append(...)` after terminal sends.
- Fix: explicitly mark these non-run side effects out of Phase 7a scope and add a Delivery-extraction blocker. Before Phase 6 extracts Delivery, run a separate post-emit business-side-effect audit: either move those writes before the `processing_status` emit, or classify them as best-effort non-blocking notifications with tests proving Delivery extraction does not call back into business modules. Phase 7a completion alone is not sufficient evidence for the broader invariant.
- Artifact: write this blocker to `docs/superpowers/plans/2026-05-16-phase-7a-delivery-extraction-handoff.md` and include that file in the Phase 7a proof commit. The handoff file is the concrete input Phase 6 must clear before extracting Delivery.

## Current Repo Check

Branch setup performed for this plan:
- Started on `main...origin/main`.
- Created `phase-7a-record-then-emit` with `git switch -c phase-7a-record-then-emit`.
- Existing untracked file before branch creation: `docs/superpowers/plans/2026-05-15-phase-6-delivery-module-extraction.md`. Leave it untouched unless explicitly asked.

Current blocking state:
- `services/sse_services.py` imports `run_command_handler` and `run_event_sse_enabled` from `services.run_command_handler`.
- `SSEManager.send_processing_status()` currently performs terminal dedup first, then calls `run_command_handler.record_processing_status()`, then optionally broadcasts `run_event`, then broadcasts `processing_status`.
- `services/run_lifecycle_service.py` has a wrapper that delegates to `run_command_handler.record_processing_status()` but returns `None`; Phase 7a must make it return `dict | None`.
- `docs/superpowers/plans/2026-05-15-phase-6-delivery-module-extraction.md` already blocks Phase 6 until Phase 7a adds `tests/test_phase7a_processing_status_gate.py` and `tests/fixtures/phase7a_processing_status_callers.json`.

Pre-migration production call sites found with `rg -n "send_processing_status\\(" --glob '*.py'`:
- `api/sse.py`: cancellation endpoint emits `canceled`.
- `modules/RoomMessageCenter.py`: supervisor flow, queue flow, resume flow, cancellation, completion, debate.
- `modules/SupervisorExecutor.py`: planning, delegation, HITL, resume, cancellation, failure.
- `modules/QueueExecutor.py`: queue HITL pause, deferred terminal status, direct-chat completion/failure.
- `modules/agent_event.py`: add explicit `lifecycle_message_id: str | None = None` for processing-status events that already carry a canonical root user message id.
- `modules/agent_response_handler.py`: async HITL records against continuation `user_message_id`; relay processing-status events record only when `AgentEvent.lifecycle_message_id` is populated from validated hub `data.user_message_id`; other generic agent processing-status events remain transport-only unless the code resolves the canonical root user message by walking the message chain.
- `modules/transports/relay.py`: preserve hub `data.user_message_id` as `AgentEvent.lifecycle_message_id` for processing-status events only after validation against `msg.turn_id` or a chain-resolved canonical root.
- `modules/WorkflowCenter.py`: legacy workflow cancellation. Treat as transport-only because the design decommissions Workflow instead of wrapping it in Execution.
- `services/room_services.py`: sendMessage initial processing, parse/selection/memory failures, fallback completion.
- `services/task_notification_service.py`: A2A task state to per-agent frontend processing-status mapping. Treat these sends as transport-only; root orchestration terminal lifecycle is emitted later by `RoomMessageCenter` after the full queue completes.
- `jobs/stale_task_checker.py`: watchdog already calls `append_run_timeout_failure()` before sending. Treat this as a Jobs lifecycle path, not an Execution caller; when the append returns a timeout payload, broadcast it when the run-event SSE flag is enabled and do not call generic `record_processing_status()` again. Preserve the dual-write-disabled transport-only `FAILED` compatibility branch separately.

Use AST discovery for the final manifest. The grep list above is only a human cross-check and includes tests/comments when run broadly.

## File Inventory

Create:
- `tests/test_phase7a_processing_status_gate.py`: AST gate that discovers production `send_processing_status()` calls and validates each manifest entry.
- `tests/fixtures/phase7a_processing_status_callers.json`: manifest generated from AST-discovered call expressions after migration.
- `tests/test_phase7a_processing_status_golden.py`: focused event-order tests for sendMessage initial processing and HITL/resolve queue-resume completion.
- `tests/test_stale_task_checker_run_lifecycle.py`: watchdog-specific record/broadcast/send ordering tests.
- `docs/superpowers/plans/2026-05-16-phase-7a-delivery-extraction-handoff.md`: concrete Phase 6 blocker artifact for non-run business side effects that still occur after `processing_status` emits.

Modify:
- `services/run_lifecycle_service.py`: return lifecycle payloads and add temporary record-plus-run-event helpers.
- `services/sse_services.py`: later make `send_processing_status()` transport-only by removing `run_command_handler` and `run_event_sse_enabled` usage.
- `services/room_services.py`: record before lifecycle processing-status sends in sendMessage helper paths; remove the `create_and_parse_user_message()` processing-status send because it does not trigger terminalizing execution.
- `api/sse.py`: record before cancellation processing-status send after required root cancellation persistence; leave later paused-agent cleanup documented as separate best-effort per-agent work.
- `modules/RoomMessageCenter.py`: record before lifecycle processing-status sends; keep soft spinner-clear `COMPLETED` sends pure transport-only.
- `modules/SupervisorExecutor.py`: record before lifecycle processing-status sends while preserving existing best-effort delivery blocks.
- `modules/QueueExecutor.py`: record before lifecycle processing-status sends while preserving deferred terminal and HITL semantics.
- `modules/agent_event.py`: add `lifecycle_message_id` for processing-status events with a proven root user message id.
- `modules/transports/relay.py`: set `lifecycle_message_id` from hub `data.user_message_id` on processing-status events only after validating it belongs to the canonical root for that agent message.
- `modules/agent_response_handler.py`: record async HITL and relay-normalized processing statuses with explicit `lifecycle_message_id`; leave other generic agent processing-status sends transport-only unless canonical root resolution is implemented and tested.
- `jobs/stale_task_checker.py`: emit the returned watchdog run-event payload before the transport-only failed processing-status send.
- Existing tests that patch `services.sse_services.run_command_handler.record_processing_status`: remove those patches or move expectations to `services.run_lifecycle_service`.
- Existing unit tests that assert only `send_processing_status()` calls: update to assert record-before-send where the caller now owns lifecycle recording.

Reference-only:
- `docs/MODULAR_DECOUPLING_DESIGN.md` section 3.3 Rule 6 and section 5.4.
- `docs/superpowers/plans/2026-05-15-phase-6-delivery-module-extraction.md` Phase 7a prerequisite gate.
- `services/run_command_handler.py`: lifecycle writer and `run_event_sse_enabled()`.
- `services/a2a_constants.py`: `SSEProcessingStatus` and `PROCESSING_DONE_STATUSES`.
- `common/utils/cancellation.py`: cancellation token behavior around terminal sends.
- `modules/WorkflowCenter.py`: manifest-covered transport-only exception; do not add lifecycle recording.
- `tests/test_service_sse.py`, `tests/test_sse_event_broker.py`, `tests/test_run_lifecycle_service.py`, `tests/test_scope_validation.py`, `tests/test_agent_response_handler.py`, `tests/test_service_task_notification.py`.
- `services/task_notification_service.py`: reference for transport-only A2A task state to per-agent frontend processing-status mapping.

## Tasks

### Task 1: Baseline and Manifest Shape

**Files:**
- Create: `tests/test_phase7a_processing_status_gate.py`
- Create: `tests/fixtures/phase7a_processing_status_callers.json`
- Reference: `docs/superpowers/plans/2026-05-15-phase-6-delivery-module-extraction.md:420`

- [ ] **Step 1: Record baseline status**

Run:

```bash
git status --short --branch
rg -n "send_processing_status\\(" modules services api jobs --glob '*.py'
rg -n "record_processing_status|run_command_handler|run_event_sse_enabled" modules services api jobs services/sse_services.py
```

Expected: current branch is `phase-7a-record-then-emit`; `services/sse_services.py` still has the embedded lifecycle side effect before implementation.

- [ ] **Step 2: Write the failing AST gate**

Create a gate test that:
- Parses production files under `modules/`, `services/`, `api/`, and `jobs/`.
- Discovers real AST call expressions whose function attribute is `send_processing_status`.
- Loads `tests/fixtures/phase7a_processing_status_callers.json`.
- Fails on unlisted calls.
- For `requires_recording=true`, asserts a preceding sibling statement awaits `record_and_maybe_broadcast_run_event(...)` before the send in the same simple statement block. A bare un-awaited helper call must fail the gate.
- Validates matching expressions for `room_id`, `status`, `sse_message_id`, `lifecycle_message_id`, `client_request_id`, `details`, and the delivery manager expression passed as `sse=...`.
- Allows awaited `broadcast_run_event_payload(...)` before a send only for manifest entries whose `recording_kind` is `"pre_recorded_payload"` (the watchdog timeout path).
- For `recording_kind="pre_recorded_payload"`, validates `pre_record_call_expression`, `payload_variable`, `payload_none_guard`, and `run_event_broadcast_expression`.
- Allows transport-only entries only with `transport_only_reason`, and asserts they have no preceding lifecycle helper or run-event payload broadcast in the same enclosing block.
- Separately AST-parses `services/sse_services.py`. By default it fails if that file imports `run_command_handler`, imports `run_event_sse_enabled`, calls `.record_processing_status(...)`, or calls `broadcast_to_room(..., "run_event", ...)` from inside `send_processing_status()`.
- Supports pre-removal mode with `PHASE7A_ALLOW_LEGACY_SSE_MANAGER=1`: in that mode only the `services/sse_services.py` embedded lifecycle/run-event checks are skipped, and all production caller manifest checks still run. This is used after caller migration and before Task 6 removes the old embedded manager write.
- Uses `call_id` as the stable manifest key. `line` and `record_call_line` are audit metadata and must match the current file after manifest regeneration, but they are not used as the only identity.

Manifest entry shape:

```json
{
  "call_id": "services.room_services.RoomServices._send_processing_status.processing-start.1",
  "path": "services/room_services.py",
  "function_or_method": "RoomServices._send_processing_status",
  "line": 2528,
  "room_id_expression": "room_id",
  "status_expression": "SSEProcessingStatus.PROCESSING",
  "sse_message_id_expression": "message_id",
  "lifecycle_message_id_expression": "message_id",
  "client_request_id_expression": "client_request_id",
  "details_expression": null,
  "delivery_expression": "sse_manager",
  "recording_kind": "record_processing_status",
  "requires_recording": true,
  "record_call_line": 2527,
  "expects_run_event_sse": true
}
```

Terminal lifecycle manifest entry shape:

```json
{
  "call_id": "modules.RoomMessageCenter.RoomMessageCenter._handle_v2_run_result.completed.1",
  "path": "modules/RoomMessageCenter.py",
  "function_or_method": "RoomMessageCenter._handle_v2_run_result",
  "line": 1690,
  "room_id_expression": "room_id",
  "status_expression": "SSEProcessingStatus.COMPLETED",
  "sse_message_id_expression": "user_message_id",
  "lifecycle_message_id_expression": "user_message_id",
  "client_request_id_expression": null,
  "details_expression": null,
  "delivery_expression": "self.sse_manager",
  "recording_kind": "record_processing_status",
  "requires_recording": true,
  "terminal_status": true,
  "record_call_line": 1686,
  "expects_run_event_sse": true
}
```

Terminal validation rule: terminal entries must still record before send. Delivery terminal dedup remains inside `SSEManager.send_processing_status()` and must not be represented as a lifecycle precondition in the manifest.

Transport-only manifest entry shape:

```json
{
  "call_id": "modules.WorkflowCenter.WorkflowCenter.execute_workflow.legacy-cancel.1",
  "path": "modules/WorkflowCenter.py",
  "function_or_method": "WorkflowCenter.execute_workflow",
  "line": 477,
  "room_id_expression": "base_task_id",
  "status_expression": "SSEProcessingStatus.CANCELED",
  "sse_message_id_expression": "message_id",
  "lifecycle_message_id_expression": null,
  "client_request_id_expression": null,
  "details_expression": null,
  "delivery_expression": "self.sse_manager",
  "recording_kind": "transport_only",
  "requires_recording": false,
  "expects_run_event_sse": false,
  "transport_only_reason": "Legacy Workflow is decommissioned by MODULAR_DECOUPLING_DESIGN.md and base_task_id is not an Execution run room_id."
}
```

Soft-complete transport-only manifest entry shape:

```json
{
  "call_id": "modules.RoomMessageCenter.RoomMessageCenter._handle_v2_run_result.clarifying-soft-complete.1",
  "path": "modules/RoomMessageCenter.py",
  "function_or_method": "RoomMessageCenter._handle_v2_run_result",
  "line": 1736,
  "room_id_expression": "room_id",
  "status_expression": "SSEProcessingStatus.COMPLETED",
  "sse_message_id_expression": "user_message_id",
  "lifecycle_message_id_expression": null,
  "client_request_id_expression": null,
  "details_expression": null,
  "delivery_expression": "self.sse_manager",
  "recording_kind": "transport_only",
  "requires_recording": false,
  "expects_run_event_sse": false,
  "transport_only_reason": "Frontend-only spinner clear for CLARIFYING; run lifecycle remains awaiting input and must not be terminalized as completed."
}
```

Pre-recorded watchdog manifest entry shape:

```json
{
  "call_id": "jobs.stale_task_checker.StaleTaskChecker._fail_stale_runs.timeout-failed.1",
  "path": "jobs/stale_task_checker.py",
  "function_or_method": "StaleTaskChecker._fail_stale_runs",
  "line": 249,
  "room_id_expression": "room_id",
  "status_expression": "SSEProcessingStatus.FAILED",
  "sse_message_id_expression": "str(tid)",
  "lifecycle_message_id_expression": "run_id",
  "client_request_id_expression": "client_request_id",
  "details_expression": "\"Run watchdog: stale non-terminal run timed out\"",
  "delivery_expression": "sse_manager",
  "recording_kind": "pre_recorded_payload",
  "requires_recording": false,
  "pre_record_call_expression": "run_command_handler.append_run_timeout_failure(room_id, run_id, stale_minutes=stale_mins)",
  "payload_variable": "payload",
  "payload_none_guard": "if payload is None: continue",
  "run_event_broadcast_expression": "broadcast_run_event_payload(room_id, payload, client_request_id=client_request_id, sse=sse_manager)",
  "expects_run_event_sse": true
}
```

Pre-recorded payload validation rule: the AST gate must prove `tid = doc.get("trigger_message_id") or run_id`, `client_request_id = doc.get("client_request_id")`, the payload variable is assigned from `append_run_timeout_failure(...)`, guarded by `payload is None` before both metric increment and delivery, and passed to awaited `broadcast_run_event_payload(...)` before `send_processing_status(...)`.

Watchdog dual-write-disabled manifest entry shape:

```json
{
  "call_id": "jobs.stale_task_checker.StaleTaskChecker._fail_stale_runs.timeout-failed-dual-write-disabled.1",
  "path": "jobs/stale_task_checker.py",
  "function_or_method": "StaleTaskChecker._fail_stale_runs",
  "line": 249,
  "room_id_expression": "room_id",
  "status_expression": "SSEProcessingStatus.FAILED",
  "sse_message_id_expression": "str(tid)",
  "lifecycle_message_id_expression": null,
  "client_request_id_expression": "client_request_id",
  "details_expression": "\"Run watchdog: stale non-terminal run timed out\"",
  "delivery_expression": "sse_manager",
  "recording_kind": "transport_only",
  "requires_recording": false,
  "expects_run_event_sse": false,
  "transport_only_reason": "FEATURE_RUN_DUAL_WRITE is disabled, so append_run_timeout_failure cannot produce a run_event payload; preserve the existing watchdog FAILED frontend clear without lifecycle recording."
}
```

- [ ] **Step 3: Run the gate and verify it fails**

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_phase7a_processing_status_gate.py
```

Expected: FAIL because callers are not migrated and `services/sse_services.py` still contains lifecycle recording.

- [ ] **Step 4: Commit the failing gate**

```bash
git add tests/test_phase7a_processing_status_gate.py tests/fixtures/phase7a_processing_status_callers.json
git commit -m "test: add phase 7a processing status gate"
```

### Task 2: Lifecycle Helper

**Files:**
- Modify: `services/run_lifecycle_service.py`
- Modify: `tests/test_run_lifecycle_service.py`

- [ ] **Step 1: Write failing lifecycle tests**

Add tests that prove:
- `RunLifecycleService.record_processing_status()` returns the exact payload from `run_command_handler.record_processing_status()`.
- `record_and_maybe_broadcast_run_event()` calls the lifecycle writer first.
- When `FEATURE_RUN_EVENT_SSE=1` and a payload exists, it broadcasts `run_event` through the provided `sse` manager with `event_id`, `run_id`, `seq`, `type`, `payload`, and `correlation_id=client_request_id`.
- When the feature flag is disabled or the lifecycle writer returns `None`, no `run_event` broadcast occurs.
- Duplicate terminal lifecycle attempts do not emit a second `run_event`: fake the writer to return a terminal payload on the first call and `None` on the second call, then assert only one `run_event` broadcast.
- `broadcast_run_event_payload()` broadcasts an already-recorded payload without calling `record_processing_status()`; this is the watchdog path.
- If no `sse` manager is provided, helpers lazily import the singleton `services.sse_services.sse_manager` for backward-compatible callers.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_run_lifecycle_service.py
```

Expected: FAIL because the wrapper returns `None` and the helper does not exist.

- [ ] **Step 2: Implement the helper**

Use this implementation shape:

```python
from services.run_command_handler import run_command_handler, run_event_sse_enabled


class RunLifecycleService:
    async def record_processing_status(...) -> dict[str, Any] | None:
        if not _feature_run_dual_write_enabled():
            return None
        return await run_command_handler.record_processing_status(...)


async def record_and_maybe_broadcast_run_event(
    room_id: str,
    status: Any,
    message_id: str | None,
    *,
    client_request_id: str | None = None,
    details: str | None = None,
    sse: Any | None = None,
) -> dict[str, Any] | None:
    payload = await run_lifecycle_service.record_processing_status(
        room_id=room_id,
        status=status,
        message_id=message_id,
        client_request_id=client_request_id,
        details=details,
    )
    await broadcast_run_event_payload(
        room_id,
        payload,
        client_request_id=client_request_id,
        sse=sse,
    )
    return payload


async def broadcast_run_event_payload(
    room_id: str,
    payload: dict[str, Any] | None,
    *,
    client_request_id: str | None = None,
    sse: Any | None = None,
) -> None:
    if run_event_sse_enabled() and payload:
        if sse is None:
            from services.sse_services import sse_manager

            sse = sse_manager
        await sse.broadcast_to_room(
            room_id,
            "run_event",
            {
                "event_id": payload.get("event_id"),
                "run_id": payload.get("run_id"),
                "seq": payload.get("seq"),
                "type": payload.get("type"),
                "payload": payload.get("payload") or {},
                "correlation_id": client_request_id,
            },
        )
```

Keep the `sse_manager` import local to the helper fallback to reduce import-order risk during startup. Callers that already hold `self.sse_manager` must pass `sse=self.sse_manager` so `run_event` and `processing_status` reach the same connection registry.

- [ ] **Step 3: Run lifecycle tests**

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_run_lifecycle_service.py
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add services/run_lifecycle_service.py tests/test_run_lifecycle_service.py
git commit -m "feat: add processing status lifecycle helper"
```

### Task 3: Migrate RoomServices SendMessage Callers and Classify Create/Parse

**Files:**
- Modify: `services/room_services.py`
- Modify: `tests/test_scope_validation.py`
- Modify: `tests/test_phase7a_processing_status_golden.py`
- Modify: `tests/test_create_and_parse.py`
- Reference: `tests/test_api_orchestration.py`

Migration rule for Tasks 3-5: do not remove the embedded `SSEManager.send_processing_status()` lifecycle write yet. These tasks intentionally run against the old manager, making its internal record call redundant/dead for migrated paths. Task 6 removes the internal write after callers are covered.

Transport-only terminology before Task 6 means "caller-owned transport-only": the caller must not invoke the lifecycle helper, but the old `SSEManager.send_processing_status()` may still perform the embedded legacy record until Task 6. Tests in Tasks 3-5 should assert caller behavior only. True no-lifecycle transport assertions are reserved for Task 6 and later, after `SSEManager` is made transport-only. The create/parse path is the exception: remove its processing-status emit entirely because it does not start a terminalizing execution path.

Best-effort delivery rule for Tasks 3-5: when an existing `send_processing_status()` call is inside a `try`/`except`, best-effort helper, or delivery-failure-swallowing block, place `record_and_maybe_broadcast_run_event()` in that same protected block immediately before the send. Do not move run-event broadcast failure outside existing best-effort error handling. This applies globally, including `SupervisorExecutor` stage notifications.

- [ ] **Step 1: Write failing sendMessage order test**

Add `test_golden_send_message_processing_status_order` using a real `SSEManager` connection and monkeypatched lifecycle payload:
- Patch `services.run_lifecycle_service.run_command_handler.record_processing_status` with a side-effect function that returns `payload` once, then `None` for every later invocation, where `payload` is `{"event_id": "evt-1", "run_id": "msg-1", "seq": 2, "type": "RUN_STARTED", "payload": {}}`. The first return is for the new helper; all later `None` values prevent the old embedded `SSEManager` write from emitting duplicate `run_event` frames while Tasks 3-5 still run against the old manager.
- Set `FEATURE_RUN_EVENT_SSE=1`.
- Patch the module-level `services.room_services.sse_manager` singleton to a real test `SSEManager`, because `_send_processing_status()` calls the imported singleton directly. Use that same test manager for helper `sse=` and `send_processing_status()` so both frames land in the same queue.
- Spy on the imported helper with `AsyncMock(wraps=record_and_maybe_broadcast_run_event)` at the `services.room_services` import site and `create=True`, then assert it is awaited exactly once before the send. This is what makes the test fail before migration; current `SSEManager.send_processing_status()` alone already emits `run_event` before `processing_status`.
- Call `RoomServices._send_processing_status("room-1", "msg-1", "cr-1")`.
- Assert queue order is `run_event` first, `processing_status` second.
- Assert the processing-status frame still contains `client_request_id="cr-1"`.

Add `tests/test_create_and_parse.py::test_create_and_parse_persists_client_request_without_processing_status_lifecycle`:
- Build a `RoomServices` instance with successful `add_room_user_message()` and successful memory initialization.
- Set `room_memory_service.initialize_or_update_room_memory()` to return success.
- Set `database_service.get_room_by_room_id = AsyncMock(return_value=None)` for a controlled early return immediately after memory update, or explicitly stub `parse_agent_mentions()` and `parse_user_message_with_mentions()` if testing the full fanout path.
- Patch the module-level `services.room_services.sse_manager` singleton so the test proves no legacy processing-status frame is emitted.
- Spy on `services.room_services.record_and_maybe_broadcast_run_event` and assert it is not awaited.
- Assert `message.client_request_id = request.client_request_id` is persisted before fanout and that `send_processing_status()` is not awaited.
- Do not classify this call as transport-only in the manifest. There is no processing-status call site after the create/parse emit is removed.

Also verify the deprecated external processing endpoint remains unavailable:
- Run or add a focused `tests/test_api_orchestration.py` assertion that `/orchestrationCenter/processRoomUserMessage` returns HTTP 410.
- This is the guard that create/parse is not paired with a separate active endpoint that starts terminalizing execution.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_phase7a_processing_status_golden.py::test_golden_send_message_processing_status_order
PYTHONPATH=. uv run pytest -q tests/test_create_and_parse.py::test_create_and_parse_persists_client_request_without_processing_status_lifecycle
PYTHONPATH=. uv run pytest -q tests/test_api_orchestration.py
```

Expected: FAIL before migration.

- [ ] **Step 2: Migrate `services/room_services.py`**

Import:

```python
from services.run_lifecycle_service import record_and_maybe_broadcast_run_event
```

For every direct `send_processing_status()` call that represents run lifecycle state, add the helper immediately before it with matching arguments. Do not call Delivery dedup APIs before recording; lifecycle/domain idempotency is what decides whether a run write or run_event payload exists. Delivery dedup only suppresses duplicate `processing_status` delivery.

Example:

```python
await record_and_maybe_broadcast_run_event(
    request.room_id,
    SSEProcessingStatus.FAILED,
    user_message.message_id,
    details="Failed to initialize room memory",
    sse=self.sse_manager,
)
await self.sse_manager.send_processing_status(
    request.room_id,
    SSEProcessingStatus.FAILED,
    user_message.message_id,
    details="Failed to initialize room memory",
)
```

For `_send_processing_status()`, record before the global singleton send:

```python
await record_and_maybe_broadcast_run_event(
    room_id,
    SSEProcessingStatus.PROCESSING,
    message_id,
    client_request_id=client_request_id,
    sse=sse_manager,
)
await sse_manager.send_processing_status(
    room_id,
    SSEProcessingStatus.PROCESSING,
    message_id,
    client_request_id=client_request_id,
)
```

For `create_and_parse_user_message()`, do not add the lifecycle helper and do not emit a processing-status frame. Preserve client request correlation by assigning it before `add_room_user_message()`. This path persists/parses/fans out messages but does not start the terminalizing execution path, so a lone `PROCESSING` frame or run record would create unowned/stuck state.

- [ ] **Step 3: Update room-service assertions**

Update tests that previously asserted only `send_processing_status()` to also assert the lifecycle helper is awaited before the send, or use the golden queue-order test when using the real manager.

- [ ] **Step 4: Run room/sendMessage tests**

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_scope_validation.py tests/test_service_room.py tests/test_create_and_parse.py tests/test_api_orchestration.py tests/test_phase7a_processing_status_golden.py::test_golden_send_message_processing_status_order
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/room_services.py tests/test_scope_validation.py tests/test_service_room.py tests/test_create_and_parse.py tests/test_api_orchestration.py tests/test_phase7a_processing_status_golden.py
git commit -m "refactor: record sendMessage processing status before emit"
```

### Task 4: Migrate RoomMessageCenter Lifecycle and HITL/Resolve Resume Paths

**Files:**
- Modify: `modules/RoomMessageCenter.py`
- Modify: `tests/test_phase7a_processing_status_golden.py`
- Modify: `tests/test_module_room_message_center.py`
- Modify: existing RoomMessageCenter/Supervisor integration tests as needed

- [ ] **Step 1: Write failing HITL/resolve order test**

Add `test_golden_hitl_resolve_resume_completion_order`:
- Build `RoomMessageCenter` via `object.__new__(RoomMessageCenter)`.
- Attach a real `SSEManager` with a connection for `room-1`.
- Patch `record_processing_status` with a side-effect function that returns a `RUN_COMPLETED` payload once, then `None` for every later invocation, and enable `FEATURE_RUN_EVENT_SSE=1`. The later `None` values prevent the old embedded manager from emitting extra `run_event` frames during the migration window.
- Spy on the imported helper with `AsyncMock(wraps=record_and_maybe_broadcast_run_event)` at the `modules.RoomMessageCenter` import site and `create=True`, then assert it is awaited before the send. This is what makes the test fail before migration; old `SSEManager` behavior alone is not enough.
- Stub `database_service.save_continuation_on_message`, `database_service.get_room_by_room_id`, `_emit_unified_summary`, and `_log_room_memory_stats`.
- Stub `queue_executor.resume_from_continuation()` to return an object with `success=True`, `needs_completion=True`, `room_id="room-1"`, and `user_message_id="msg-1"`.
- Call `_resume_continuation_locked({"supervisor_v2": False}, "agent-msg-1", "answer")`.
- Assert queue order is `run_event` then `processing_status`.

Add a duplicate-terminal caller test for a root completion path:
- Invoke the migrated terminal caller twice for the same `room_id` and `user_message_id`.
- Fake `record_processing_status()` to return a terminal payload once and `None` for every later call, matching `RunCommandHandler` behavior after a run is already terminal and covering both helper and old embedded manager invocations during Tasks 3-5.
- Assert only one `run_event` frame is emitted because the second lifecycle record returns `None`, and the second terminal `processing_status` is still suppressed by `SSEManager` terminal dedup.
- Add a Redis/L2 variant using two `SSEManager` instances that share the same `MockRedisService`: the first manager sends the terminal status and sets the Redis terminal key, then the second manager attempts the duplicate terminal send and is suppressed through Redis. Do not pre-populate the key before the first send, because that would validate the wrong behavior. Assert no extra `run_event` frame is emitted because lifecycle idempotency returned `None`, not because Delivery dedup controlled recording.
- Add soft-complete clarification tests:
- `RunStatus.CLARIFYING` in `_handle_v2_run_result()` sends frontend `COMPLETED` only to clear the spinner; assert the send is manifest transport-only and has no preceding lifecycle helper in the same enclosing block.
- Assert the CLARIFYING branch's later `turn_event_appender.append("turn_completed", ...)` is not treated as run lifecycle terminalization in Phase 7a, and that this post-emit turn event is listed in the Phase 6 handoff blocker artifact.
- The clarify-resume failure path that restores pending clarification and sends `COMPLETED` with "please answer again" must also be pure transport-only for that frontend `COMPLETED`.

Run:

```bash
PYTHONPATH=. uv run pytest -q \
  tests/test_phase7a_processing_status_golden.py::test_golden_hitl_resolve_resume_completion_order \
  tests/test_phase7a_processing_status_golden.py::test_golden_duplicate_terminal_root_completion_does_not_emit_extra_run_event \
  tests/test_module_room_message_center.py::test_clarifying_completed_spinner_clear_is_transport_only \
  tests/test_module_room_message_center.py::test_clarify_resume_retry_completed_spinner_clear_is_transport_only
```

Expected: FAIL before migration.

- [ ] **Step 2: Migrate `RoomMessageCenter` lifecycle sends**

Import `record_and_maybe_broadcast_run_event`.

For each direct `await self.sse_manager.send_processing_status(...)` that is a true lifecycle transition, insert the helper immediately before the send with identical lifecycle arguments. Preserve existing cancellation-token logic and cleanup order. Do not record for non-processing-status SSE methods or frontend-only spinner-clears.

Important call groups:
- early process failure before queue execution,
- queue result `FAILED`, `CANCELED`, and `COMPLETED`,
- supervisor V2 preparation and resume failures,
- `_handle_v2_run_result()` true terminal statuses,
- `_resume_continuation_locked()` queue-resume completion.

Transport-only or true-state exceptions:
- `RunStatus.CLARIFYING` frontend `COMPLETED` is a soft spinner clear. Do not record lifecycle `COMPLETED` for this send, and do not place any lifecycle helper immediately before it. It is pure transport-only in Phase 7a.
- Clarify-resume failure that restores pending clarification and sends frontend `COMPLETED` with "please answer again" follows the same rule: no lifecycle helper immediately before the frontend `COMPLETED`; manifest it as transport-only.

- [ ] **Step 3: Run focused tests**

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_phase7a_processing_status_golden.py tests/test_module_room_message_center.py tests/test_phase5_supervisor_integration.py tests/test_scope_validation.py
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add modules/RoomMessageCenter.py tests/test_phase7a_processing_status_golden.py tests/test_module_room_message_center.py tests/test_phase5_supervisor_integration.py tests/test_scope_validation.py
git commit -m "refactor: record room message processing status before emit"
```

### Task 5: Migrate Supervisor, Queue, Agent Event, API, and Jobs Callers

**Files:**
- Modify: `modules/SupervisorExecutor.py`
- Modify: `modules/QueueExecutor.py`
- Modify: `modules/agent_event.py`
- Modify: `modules/agent_response_handler.py`
- Modify: `modules/transports/relay.py`
- Modify: `api/sse.py`
- Modify: `jobs/stale_task_checker.py`
- Create: `tests/test_stale_task_checker_run_lifecycle.py`
- Modify: related unit tests
- Modify: `tests/test_module_supervisor_executor.py`
- Modify: `tests/test_module_room_message_center.py`
- Modify: `tests/test_api_relay.py`
- Modify: `tests/test_agent_event_turn_id.py`
- Modify: `tests/test_turn_id_passthrough.py`
- Modify: `tests/test_transport_parity.py`
- Reference: `services/task_notification_service.py`
- Reference: `modules/WorkflowCenter.py`

- [ ] **Step 1: Write/update focused caller tests**

For each file, update an existing unit test or add a small test that proves the correct lifecycle behavior:
- `modules/SupervisorExecutor.py`: stage notification and HITL/awaiting-input sends.
- `modules/QueueExecutor.py`: deferred terminal status and HITL `AWAITING_INPUT`.
- `modules/agent_event.py`, `modules/transports/relay.py`, and `modules/agent_response_handler.py`: add `AgentEvent.lifecycle_message_id: str | None = None`; relay processing-status normalization must set it from hub `data.user_message_id` only when the candidate equals `msg.turn_id` or a chain-resolved canonical root. If a hub supplies a mismatched `user_message_id`, reject/drop the processing-status event before handler dispatch. Do not emit a transport-only `processing_status` using the mismatched hub id, because that can clear or update the wrong frontend message. `_on_processing_status()` records only when that explicit validated lifecycle id is present. Add one handler test where `lifecycle_message_id="umsg-001"` proves record-before-send, one where only `related_message_id` is present proves transport-only behavior, and a relay mismatch test where `data.user_message_id` does not match the agent message's canonical root and neither handler dispatch nor SSE emission occurs for `"other-msg"`. Also keep `_maybe_create_hitl_for_async_interactive()` recording `user_message_id` from the saved continuation. Generic processing-status events without `lifecycle_message_id` remain transport-only; never record against raw `e.related_message_id` because it can point to a previous agent message.
- `services/task_notification_service.py`: mapped terminal and interactive task states remain transport-only. Add or update tests to assert these per-agent notifications do not call the lifecycle helper, preventing premature root run terminalization before `RoomMessageCenter` emits actual completion.
- `api/sse.py`: cancel endpoint emits lifecycle `canceled` after required root cancellation persistence. The later paused-agent task-state update and remote cancel loop is documented and tested as separate best-effort per-agent cleanup; failures there must not prevent the root cancellation lifecycle record or frontend clear.
- `jobs/stale_task_checker.py`: `_fail_stale_runs()` broadcasts the payload returned by `append_run_timeout_failure()` before sending `FAILED`; it never calls generic `record_processing_status()` afterward. Add one compatibility test where `FEATURE_RUN_DUAL_WRITE=0` preserves both the transport-only watchdog `FAILED` send and the existing `run_watchdog_forced_failure_total` increment, and one enabled-dual-write test where `append_run_timeout_failure()` returning `None` suppresses metric, run_event, and `FAILED` delivery.
- `modules/WorkflowCenter.py`: no lifecycle recording is added; the manifest marks this legacy Workflow send as transport-only with the decommission reason.
- Soft-complete frontend `COMPLETED` statuses for clarification remain transport-only and do not terminalize the run.

For lifecycle callers, use `AsyncMock` side effects to append `"record"` and `"send"` to a list, then assert order. For caller-owned transport-only paths in Tasks 3-5, assert the lifecycle helper is not awaited and only the existing send occurs; do not assert true no-lifecycle behavior until Task 6 removes the embedded manager write. For create/parse specifically, assert the lifecycle helper and processing-status send are both not awaited. For existing best-effort send blocks, assert helper failures are swallowed by the same block rather than escaping.

Run the focused tests and expect failures before migration:

```bash
PYTHONPATH=. uv run pytest -q tests/test_agent_response_handler.py tests/test_agent_event_turn_id.py tests/test_turn_id_passthrough.py tests/test_transport_parity.py tests/test_api_relay.py tests/test_service_task_notification.py tests/test_module_queue_executor.py tests/test_module_supervisor_executor.py tests/test_module_room_message_center.py tests/test_supervisor_v2_improvements.py tests/test_api_sse.py tests/test_module_workflow_center.py tests/test_stale_task_checker_run_lifecycle.py
```

- [ ] **Step 2: Migrate callers**

Add the helper import needed by each file:

```python
from services.run_lifecycle_service import (
    broadcast_run_event_payload,
    record_and_maybe_broadcast_run_event,
)
```

Only `jobs/stale_task_checker.py` needs `broadcast_run_event_payload`; most migrated execution callers only need `record_and_maybe_broadcast_run_event`. Do not add either helper to `services/task_notification_service.py` or legacy `modules/WorkflowCenter.py`.

Then insert the helper immediately before every lifecycle processing-status send. Do not gate lifecycle recording on `SSEManager` terminal dedup. The lower-level lifecycle writer is idempotent and returns `None` when no new run event should be broadcast; the later `send_processing_status()` call remains responsible only for delivery dedup.

Global best-effort rule: if the existing send is inside a `try`/`except` or best-effort block, put the helper in that same block. This is required for `SupervisorExecutor` stage notifications such as planning, where SSE failure is intentionally non-fatal.

For API cancellation:
- Required root cancellation side effects are `sse_manager.cancel_message_and_broadcast(message_id)`, `hitl_service.cancel_requests_for_message(message_id)`, and successful `mongodb.cancel_message(message_id, user.user_id)`. Record and emit the root `canceled` processing status only after those complete.
- The later paused-agent task-state update, `notify_task_update()`, and remote agent cancel loop remain separate best-effort per-agent cleanup. Document this in code/tests and assert failures in that block are swallowed and do not prevent the root cancellation lifecycle record or frontend clear.
- If implementation decides those paused-agent task-state updates are required persistence for root cancellation, then move the root lifecycle record/send after that update loop instead of relying on the best-effort classification.

For delivery manager identity:
- Pass `sse=self.sse_manager` when the send uses `self.sse_manager`.
- Pass `sse=sse_manager` when the send uses the module-level singleton.
- Pass the same test manager in golden tests so `run_event` and `processing_status` are read from the same connection queue.

For client request IDs:
- Preserve existing `client_request_id` kwargs when present.
- If a caller builds `kw = {"client_request_id": ...}`, pass `client_request_id=kw.get("client_request_id")` to the helper.
- If a caller resolves client request ID from DB for the send path, pass the same resolved value to the helper.

For details:
- Pass the same `details` expression to both helper and send.

For lifecycle message IDs:
- Root user-message lifecycle sends use the same `message_id` as the SSE frame.
- Async HITL continuation sends record against the continuation `user_message_id`, while preserving any existing frontend display message ID.
- Relay-normalized processing-status sends record against `e.lifecycle_message_id`, which `modules/transports/relay.py` sets from hub `data.user_message_id` only after validation. First accept `data.user_message_id` when it equals `msg.turn_id`; if `msg.turn_id` is missing, resolve the canonical root by walking the `related_message_id` chain before setting `lifecycle_message_id`. Because `_normalize()` is synchronous, implement the chain walk in an async helper called by `handle_publish_event()` before normalization, for example `_resolve_processing_status_lifecycle_id(msg, data) -> str | None`. Pass the prevalidated lifecycle id into `_normalize(...)`; `_normalize()` must not query the DB.
- Add `tests/test_api_relay.py::test_processing_status_mismatched_user_message_id_is_dropped`: construct an agent message whose `turn_id` or resolved root is `"umsg-001"`, publish `data.user_message_id="other-msg"`, and assert the relay does not dispatch the event to `AgentResponseHandler` and no SSE is emitted with `message_id="other-msg"`. If implementation keeps a transport-only fallback for missing user ids, it must use the agent message id or validated canonical id, never the mismatched hub-supplied id.
- Implement `_on_processing_status()` with explicit branches so the AST gate can classify both paths: one branch with an awaited helper immediately before `send_processing_status()` when `e.lifecycle_message_id` is truthy, and one transport-only branch when it is absent. Do not use one shared trailing send after a conditional helper.
- The final manifest must contain separate entries for these branches, for example `modules.agent_response_handler.AgentResponseHandler._on_processing_status.relay-lifecycle.1` with `lifecycle_message_id_expression="e.lifecycle_message_id"` and `modules.agent_response_handler.AgentResponseHandler._on_processing_status.transport-only.1` with a `transport_only_reason`.
- Generic agent processing-status sends may record only when `AgentEvent.lifecycle_message_id` is populated, or after resolving the canonical root user message by walking `related_message_id` through agent messages, following the pattern in `services/room_services.py` around the chained mention flow. If neither is true, mark the send transport-only.
- Per-agent task notification sends in `services/task_notification_service.py` are transport-only. Do not record `COMPLETED`, `FAILED`, `CANCELED`, or `AWAITING_INPUT` from that module against the root run.
- The manifest records both SSE and lifecycle message expressions for true lifecycle sends so the gate does not force a false match.

- [ ] **Step 3: Handle watchdog path explicitly**

Inspect `jobs/stale_task_checker.py`. It already records a timeout-specific run event through `run_command_handler.append_run_timeout_failure()` before sending `FAILED`.

Use the returned payload from `append_run_timeout_failure()` to emit the legacy `run_event` frame when enabled, then keep `send_processing_status()` transport-only without calling `record_processing_status()` again.

Preserve the current frontend clear and metric when run dual-write is disabled: in that mode no run-event payload can exist, so increment `run_watchdog_forced_failure_total` and send the watchdog `FAILED` frame as an explicit transport-only compatibility branch. When dual-write is enabled and `append_run_timeout_failure()` returns `None`, do not emit `FAILED` or increment the metric because the run was healed, already terminal, missing/mismatched, or otherwise produced no new timeout event.

```python
tid = doc.get("trigger_message_id") or run_id
client_request_id = doc.get("client_request_id")

if os.environ.get("FEATURE_RUN_DUAL_WRITE", "1").strip().lower() in (
    "0",
    "false",
    "no",
    "off",
):
    increment_counter("run_watchdog_forced_failure_total")
    await sse_manager.send_processing_status(
        room_id,
        SSEProcessingStatus.FAILED,
        str(tid),
        client_request_id=client_request_id,
        details="Run watchdog: stale non-terminal run timed out",
    )
    continue

payload = await run_command_handler.append_run_timeout_failure(
    room_id, run_id, stale_minutes=stale_mins,
)
if payload is None:
    continue
increment_counter("run_watchdog_forced_failure_total")
await broadcast_run_event_payload(
    room_id,
    payload,
    client_request_id=client_request_id,
    sse=sse_manager,
)
await sse_manager.send_processing_status(
    room_id,
    SSEProcessingStatus.FAILED,
    str(tid),
    client_request_id=client_request_id,
    details="Run watchdog: stale non-terminal run timed out",
)
```

Do not emit `FAILED` or increment `run_watchdog_forced_failure_total` when dual-write is enabled and `append_run_timeout_failure()` returns `None`. That return can mean the head was healed, the append was a no-op, the run was already terminal, or the run was missing/mismatched. In those cases there is no newly appended timeout event to mirror to SSE.

Do not add a second generic `record_processing_status()` call after `append_run_timeout_failure()`.

Add manifest entries for both branches: `recording_kind="pre_recorded_payload"` for the enabled-dual-write payload path and `recording_kind="transport_only"` for the dual-write-disabled compatibility send.

- [ ] **Step 4: Mark legacy Workflow transport-only**

Do not modify `modules/WorkflowCenter.py` to record lifecycle. Add/keep tests only if needed to prove no helper is called on the legacy cancellation path. The manifest entry must include:

```json
"transport_only_reason": "Legacy Workflow is decommissioned by MODULAR_DECOUPLING_DESIGN.md and base_task_id is not an Execution run room_id."
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_agent_response_handler.py tests/test_agent_event_turn_id.py tests/test_turn_id_passthrough.py tests/test_transport_parity.py tests/test_api_relay.py tests/test_service_task_notification.py tests/test_module_queue_executor.py tests/test_module_supervisor_executor.py tests/test_module_room_message_center.py tests/test_supervisor_v2_improvements.py tests/test_api_sse.py tests/test_module_workflow_center.py tests/test_stale_task_checker_run_lifecycle.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add modules/SupervisorExecutor.py modules/QueueExecutor.py modules/agent_event.py modules/agent_response_handler.py modules/transports/relay.py api/sse.py jobs/stale_task_checker.py tests
git commit -m "refactor: record execution processing statuses before emit"
```

- [ ] **Step 7: Run pre-removal AST gate**

Before removing the embedded `SSEManager` lifecycle write, run the Phase 7a gate in legacy-SSE mode:

```bash
PHASE7A_ALLOW_LEGACY_SSE_MANAGER=1 PYTHONPATH=. uv run pytest -q tests/test_phase7a_processing_status_gate.py
```

Expected: PASS for all production caller manifest checks. The only behavior intentionally allowed by this mode is the still-present embedded lifecycle/run-event branch inside `services/sse_services.py`. If this fails for any caller under `modules/`, `services/`, `api/`, or `jobs/`, stop before Task 6 because removing the embedded manager write would make that caller silently stop recording lifecycle.

### Task 6: Make `send_processing_status()` Transport-Only

**Files:**
- Modify: `services/sse_services.py`
- Modify: `tests/test_service_sse.py`
- Modify: `tests/test_sse_event_broker.py`

- [ ] **Step 1: Write/update failing SSE tests**

Update SSE tests so they no longer monkeypatch `services.sse_services.run_command_handler.record_processing_status`.

Add assertions that:
- `send_processing_status()` still includes/omits `client_request_id` correctly.
- terminal dedup still suppresses duplicate terminal frames.
- `send_processing_status()` does not call lifecycle recording.
- `send_processing_status()` does not emit `run_event`.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_service_sse.py tests/test_sse_event_broker.py
```

Expected before implementation: FAIL on no-lifecycle/no-run-event assertions.

- [ ] **Step 2: Remove embedded lifecycle work**

Do this only after Tasks 3-5 have migrated or explicitly marked all production callers. This preserves the design-doc sequence: callers first record against the old `sse_manager`, where its internal recording is redundant, then this task removes the redundant internal write.

Hard precondition: the Task 5 pre-removal AST gate must have passed with `PHASE7A_ALLOW_LEGACY_SSE_MANAGER=1`. Do not perform this edit if the gate reports any unmigrated or unclassified production caller.

In `services/sse_services.py`:
- Change `from services.run_command_handler import run_command_handler, run_event_sse_enabled` to no import from `services.run_command_handler`.
- Delete the block that calls `run_command_handler.record_processing_status(...)`.
- Delete the block that conditionally broadcasts `"run_event"`.
- Keep terminal dedup exactly where it is.
- Keep `_resolve_client_request_id()`, SSE payload construction, optional `agents`, and `broadcast_to_room(room_id, "processing_status", data)`.

- [ ] **Step 3: Run SSE tests**

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_service_sse.py tests/test_sse_event_broker.py
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add services/sse_services.py tests/test_service_sse.py tests/test_sse_event_broker.py
git commit -m "refactor: make processing status SSE transport only"
```

### Task 7: Finalize Manifest Gate

**Files:**
- Modify: `tests/fixtures/phase7a_processing_status_callers.json`
- Modify: `tests/test_phase7a_processing_status_gate.py`
- Create: `docs/superpowers/plans/2026-05-16-phase-7a-delivery-extraction-handoff.md`

- [ ] **Step 1: Regenerate or update manifest**

After code migration, run AST discovery and update the manifest with final `line`, `record_call_line`, expression fields, `recording_kind`, `delivery_expression`, and `expects_run_event_sse`.

Every production call under `modules/`, `services/`, `api/`, and `jobs/` must be either:
- `requires_recording=true` with a preceding awaited helper call, or
- `recording_kind="pre_recorded_payload"` with a preceding awaited `broadcast_run_event_payload(...)` call, or
- `requires_recording=false` with a specific `transport_only_reason` and no preceding lifecycle helper or run-event payload broadcast in the same enclosing block.

- [ ] **Step 2: Run gate**

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_phase7a_processing_status_gate.py
```

Expected: PASS.

- [ ] **Step 3: Run grep acceptance checks**

Run:

```bash
rg "record_processing_status" services/sse_services.py
rg "run_command_handler|run_event_sse_enabled" services/sse_services.py
PYTHONPATH=. uv run pytest -q tests/test_phase7a_processing_status_gate.py
```

Expected:
- First command has no output.
- Second command has no output.
- The AST gate passes and is the authoritative caller acceptance check. Do not use raw `rg "send_processing_status\\("` as a gate because it also matches the method definition in `services/sse_services.py` and non-call references in comments/docstrings such as `modules/transports/direct.py`.

- [ ] **Step 4: Create Phase 6 handoff blocker artifact**

Create `docs/superpowers/plans/2026-05-16-phase-7a-delivery-extraction-handoff.md` with:
- A short statement that Phase 7a removed the run-lifecycle side effect from `SSEManager.send_processing_status()`, but does not prove all non-run business side effects are ordered before Delivery.
- The remaining post-emit side-effect audit list, including `RoomMessageCenter._notify_all_non_terminal_tasks_failed(...)`, terminal `turn_event_appender.append(...)`, and the CLARIFYING soft-complete `turn_event_appender.append(...)`.
- The Phase 6 gate: each listed side effect must be moved before `processing_status` emit, or explicitly classified as best-effort/non-blocking with tests before Delivery extraction proceeds.

- [ ] **Step 5: Commit**

```bash
git add tests/test_phase7a_processing_status_gate.py tests/fixtures/phase7a_processing_status_callers.json docs/superpowers/plans/2026-05-16-phase-7a-delivery-extraction-handoff.md
git commit -m "test: prove phase 7a processing status migration"
```

### Task 8: Full Verification

**Files:**
- Reference: all modified files

- [ ] **Step 1: Run Phase 7a focused suite**

Run:

```bash
PYTHONPATH=. uv run pytest -q \
  tests/test_phase7a_processing_status_gate.py \
  tests/test_phase7a_processing_status_golden.py \
  tests/test_run_lifecycle_service.py \
  tests/test_service_sse.py \
  tests/test_sse_event_broker.py \
  tests/test_agent_response_handler.py \
  tests/test_agent_event_turn_id.py \
  tests/test_turn_id_passthrough.py \
  tests/test_transport_parity.py \
  tests/test_api_relay.py \
  tests/test_module_supervisor_executor.py \
  tests/test_module_room_message_center.py \
  tests/test_create_and_parse.py \
  tests/test_service_task_notification.py \
  tests/test_stale_task_checker_run_lifecycle.py
```

Expected: PASS.

- [ ] **Step 2: Run existing run lifecycle and SSE suites**

Run:

```bash
PYTHONPATH=. uv run pytest -q \
  tests/test_run_reducer.py \
  tests/test_run_projector.py \
  tests/test_heal_head_from_events.py \
  tests/test_get_room_ids_non_terminal_runs.py \
  tests/test_api_sse.py \
  tests/test_api_orchestration.py \
  tests/test_api_relay.py \
  tests/test_service_room.py \
  tests/test_scope_validation.py
```

Expected: PASS.

- [ ] **Step 3: Run broader Execution smoke tests**

Run:

```bash
PYTHONPATH=. uv run pytest -q \
  tests/test_module_queue_executor.py \
  tests/test_module_supervisor_executor.py \
  tests/test_module_room_message_center.py \
  tests/test_module_workflow_center.py \
  tests/test_supervisor_v2_improvements.py \
  tests/test_phase5_supervisor_integration.py \
  tests/test_transport_parity.py \
  tests/test_turn_id_passthrough.py \
  tests/test_flow_contracts.py
```

Expected: PASS or record any unrelated pre-existing failure with file/test name and reason.

- [ ] **Step 4: Verify Delivery extraction blocker artifact**

Before handing Phase 7a to Phase 6, verify `docs/superpowers/plans/2026-05-16-phase-7a-delivery-extraction-handoff.md` exists and documents the remaining non-run post-emit side-effect audit as a blocker for Delivery extraction. The audit must include at least:
- `modules/RoomMessageCenter.py`: failed room-lock path sends `FAILED` before `_notify_all_non_terminal_tasks_failed(...)`.
- `modules/RoomMessageCenter.py`: terminal completion sends `COMPLETED` before `turn_event_appender.append(...)` in the root completion and V2 result paths.
- `modules/RoomMessageCenter.py`: CLARIFYING soft-complete path sends frontend `COMPLETED` before `turn_event_appender.append("turn_completed", ...)`; this must not be treated as run lifecycle terminalization, but it is still a post-emit business side effect for Phase 6 to audit.

Phase 6 must either move those business writes before the `processing_status` emit or prove with tests that they are best-effort notifications that do not require Delivery to call back into business modules.

- [ ] **Step 5: Final repo check**

Run:

```bash
git status --short
git log --oneline -5
```

Expected: only intended Phase 7a files changed on `phase-7a-record-then-emit`; unrelated untracked Phase 6 plan remains untouched unless the user asked to include it.

## Implementation Gate Checklist

- [ ] `services/sse_services.py` has no `record_processing_status` matches.
- [ ] `services/sse_services.py` has no `run_command_handler` or `run_event_sse_enabled` dependency.
- [ ] `SSEManager.send_processing_status()` no longer broadcasts `run_event`.
- [ ] `RunLifecycleService.record_processing_status()` returns `dict | None`.
- [ ] `record_and_maybe_broadcast_run_event()` preserves the old `run_event` SSE payload and ordering through the same `SSEManager` instance as the later `processing_status` send.
- [ ] `broadcast_run_event_payload()` supports the Jobs watchdog pre-recorded timeout event without a second generic record call.
- [ ] Every production `send_processing_status()` call under `modules/`, `services/`, `api/`, and `jobs/` is manifest-covered.
- [ ] Every lifecycle caller records before sending, with correct `room_id`, `status`, `lifecycle_message_id`, `sse_message_id`, `client_request_id`, `details`, and `delivery_expression` fields.
- [ ] Async HITL paths record against the continuation root user run; relay processing-status paths record only with validated explicit `AgentEvent.lifecycle_message_id`; mismatched hub `data.user_message_id` values are rejected/dropped before handler dispatch; other generic agent processing-status paths are transport-only unless they resolve and test the canonical root user message chain.
- [ ] Clarification soft-complete frontend `COMPLETED` sends are pure transport-only and have no preceding lifecycle helper in the same enclosing block.
- [ ] `services/task_notification_service.py` remains transport-only so per-agent terminal task updates cannot prematurely terminalize the root orchestration run.
- [ ] Transport-only exceptions are documented with concrete reasons and have no preceding lifecycle helper in the same enclosing block.
- [ ] Legacy Workflow remains transport-only and is not converted into an Execution lifecycle owner.
- [ ] Golden sendMessage and HITL/resolve tests prove `run_event` before `processing_status`.
- [ ] Duplicate terminal caller tests prove lifecycle idempotency prevents extra `run_event` frames while SSE L1 and Redis/L2 dedup still suppress duplicate `processing_status` delivery.
- [ ] Existing SSE terminal dedup behavior still passes.
- [ ] Existing run lifecycle tests still pass.
- [ ] No frontend SSE event format changed.
- [ ] Follow-up blocker is documented in `docs/superpowers/plans/2026-05-16-phase-7a-delivery-extraction-handoff.md`: remaining non-run business side effects after `processing_status` emits, such as RoomMessageCenter task-failure notifications, terminal turn-event appends, and the CLARIFYING soft-complete turn-event append, must be audited/reordered or explicitly classified before Delivery extraction.
