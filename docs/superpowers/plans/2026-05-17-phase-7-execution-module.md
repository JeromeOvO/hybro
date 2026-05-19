# Phase 7 Execution Module Implementation Plan

> **For agentic workers:** REQUIRED: Use `superpowers:subagent-driven-development` if subagents are available, or `superpowers:executing-plans` to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the remaining Phase 7 work after Phase 7a by extracting Execution into an `execution/` module, wiring it through Common protocols, migrating supported Execution-owned SSE emits to typed `EventPublisher` events, and keeping unsupported legacy processing-status values on an explicit compatibility port.

**Architecture:** Phase 7b starts only after Phase 6 Delivery extraction is landed. Move the current orchestration implementation out of `modules/` and `services/hitl_service.py` into focused `execution/` subpackages with temporary compatibility shims at the old import paths. By the end of Phase 7b, `ExecutionFacade` implements `ExecutionEngine`, `HITLManager`, and `HubAgentResponseSink`; Task 6 exposes only the first two protocols, and Task 11 adds the Hub sink after `handle_hub_agent_response(...)` is specified. The facade owns in-flight orchestration tasks and run lifecycle writes, emits supported frontend-visible events through the injected Common `EventPublisher` protocol, and emits unsupported legacy processing-status frames through an injected `LegacyProcessingStatusPublisher`.

**Tech Stack:** Python 3.11+, FastAPI, pytest, pytest-asyncio, existing Mongo/Redis DAL protocols, Common DTOs/protocols, typed Delivery DTOs, AST import-boundary tests, no new dependencies.

---

## Scope

Include:
- Create branch `phase-7-execution-module` from the branch where Phase 6 Delivery extraction has already passed.
- Treat Phase 7a as complete and preserve its run-lifecycle invariant: record run state before frontend-visible processing-status delivery.
- Extract Execution-owned code from `modules/RoomMessageCenter.py`, `modules/QueueExecutor.py`, `modules/SupervisorExecutor.py`, `modules/debate_dispatcher.py`, `modules/AgentDispatcher.py`, `modules/AgentMessageProcessor.py`, `modules/TaskStateManager.py`, `modules/agent_event.py`, `modules/dispatch_middleware.py`, `modules/middleware/*`, `modules/agent_response_handler.py`, `modules/transports/base.py`, `modules/transports/direct.py`, `modules/transports/relay.py`, and `modules/transports/webhook.py` unless webhook migration is explicitly deferred with a manifest entry.
- Extract HITL runtime behavior from `services/hitl_service.py` into `execution/hitl/service.py`.
- Add `ExecutionFacade` and `ExecutionDeps` wiring in `container.py`.
- Migrate `api/room_center.py`, `api/hitl.py`, and `api/sse.py` to use `ExecutionEngine` / `HITLManager` protocols for Execution-owned actions.
- Replace Execution-owned `sse_manager.send_processing_status()` and `record_and_maybe_broadcast_run_event()` call sites with an Execution event helper that records lifecycle state, emits optional `RunEventNotification`, then emits either typed `ProcessingStatusEvent` for supported statuses with structured details or a documented Delivery compatibility processing-status frame for unsupported statuses / legacy string-detail frames.
- Preserve old import paths as compatibility shims until Phase 9 cleanup.
- Preserve current API request/response shapes and frontend SSE frame shapes.

Exclude:
- Do not implement Phase 6 Delivery extraction here. If `delivery/` source files are absent, stop and land Phase 6 first.
- Do not implement Phase 8 HubRuntimeBridge. Add `HubAgentResponseSink` support, but keep current relay paths working until Phase 8 rewires Hub to internal events.
- Do not delete legacy `modules/` or `services/hitl_service.py`; leave shims for Phase 9.
- Do not decommission legacy workflow endpoints here. That remains governed by the design document's Phase 0d/8/9 cleanup path.
- Do not migrate `api/a2a_tasks.py` in Phase 7b. The design maps it to the
  target Execution API package, but this plan defers that route because current
  long-running A2A task endpoints still depend directly on service/database
  details outside the sendMessage/HITL/cancel migration surface.
- Do not change frontend event schemas, route URLs, auth behavior, room ownership checks, or run state transition semantics.
- Do not move `task_notification_service.py` transport-only task update behavior into Execution unless a test proves it is part of orchestration run lifecycle.

## Current Repo Check

As of 2026-05-17 on `main`:
- Phase 7a artifacts are present:
  - `services/run_lifecycle_service.py`
  - `tests/test_phase7a_processing_status_gate.py`
  - `tests/test_phase7a_processing_status_golden.py`
  - `tests/fixtures/phase7a_processing_status_callers.json`
  - `docs/superpowers/plans/2026-05-16-phase-7a-delivery-extraction-handoff.md`
- `docs/superpowers/plans/2026-05-16-phase-7a-delivery-extraction-handoff.md` says remaining audit items are cleared.
- `common/protocols/execution_protocols.py` already exports `ExecutionEngine`, `HITLManager`, and `HubAgentResponseSink`.
- `common/dto/execution.py` exists, but `ExecutionRequest` does not yet model the full `/roomCenter/sendMessage` payload shape.
- `delivery/` and `execution/` currently contain only stale `__pycache__` files on `main`; no Phase 6 or Phase 7b source has landed.
- `container.py` wires Agent, Room, and Context & Memory deps, but no Delivery or Execution deps.
- `main.py` still owns `_heal_diverged_runs_on_startup()` and directly wires `services.sse_services.sse_manager`.

Phase 7b implementation must start from a post-Phase-6 branch where:
- `delivery/facade.py`, `delivery/event_publisher.py`, and `delivery/sse/manager.py` exist.
- `container.py` exports `DeliveryDeps` and a Delivery facade/deps factory.
- `services/sse_services.py` is a C3 adapter over Delivery and does not call `run_command_handler`.
- Phase 6 Delivery tests pass.

## File Inventory

Create:
- `execution/__init__.py`: exports `ExecutionFacade`, protocol helpers, and compatibility constructors.
- `execution/facade.py`: implements `ExecutionEngine` and `HITLManager` in Task 6, then `HubAgentResponseSink` in Task 11; owns tracked orchestration tasks.
- `execution/events.py`: lifecycle-first typed event helper for `ProcessingStatusEvent` and `RunEventNotification`.
- `execution/ports.py`: module-private protocols (`HITLCoordinator`, `AgentDispatchPort`, `RunLifecyclePort`, `LegacyProcessingStatusPublisher`, task runner type aliases).
- `execution/legacy_processing_status.py`: narrow adapter from the Execution compatibility port to the bound Phase 6 C3 SSE adapter.
- `execution/run_lifecycle.py`: adapter around `services.run_command_handler.run_command_handler` plus payload-to-DTO translation.
- `execution/run_queries.py`: adapter for `ExecutionEngine.get_run()` / `get_runs_for_room()` over existing run persistence until repositories land.
- `execution/cancellation.py`: cancellation orchestration ports/helpers plus adapters that accept already-bound callables; it must use domain string states and must not import A2A SDK types directly.
- `execution/orchestration/room_message_center.py`: moved implementation from `modules/RoomMessageCenter.py`.
- `execution/orchestration/factory.py`: `create_room_message_center(...)` plus a bindable legacy proxy for the old `modules.RoomMessageCenter.room_message_center` singleton.
- `execution/orchestration/queue_executor.py`: moved implementation from `modules/QueueExecutor.py`.
- `execution/orchestration/supervisor_executor.py`: moved implementation from `modules/SupervisorExecutor.py`.
- `execution/orchestration/debate_dispatcher.py`: moved implementation from `modules/debate_dispatcher.py`.
- `execution/dispatch/agent_dispatcher.py`: moved implementation from `modules/AgentDispatcher.py`.
- `common/a2a_constants.py`: shared SDK-free status helper constants moved out of `services/a2a_constants.py`; leave `services/a2a_constants.py` as a compatibility adapter until cleanup so non-Execution services do not depend on Execution.
- `execution/dispatch/agent_event.py`: moved implementation from `modules/agent_event.py`.
- `execution/dispatch/agent_message_processor.py`: moved implementation from `modules/AgentMessageProcessor.py`.
- `execution/dispatch/dispatch_middleware.py`: moved implementation from `modules/dispatch_middleware.py`.
- `execution/dispatch/task_notifications.py`: adapter from Execution domain string task states to the app-shell notification callable.
- `execution/dispatch/middleware/__init__.py`: moved package from `modules/middleware/__init__.py`.
- `execution/dispatch/middleware/cloud_health.py`: moved implementation from `modules/middleware/cloud_health.py`.
- `execution/dispatch/middleware/hub_transport.py`: moved implementation from `modules/middleware/hub_transport.py`.
- `execution/dispatch/response_handler.py`: moved implementation from `modules/agent_response_handler.py`.
- `execution/dispatch/transports/base.py`: moved implementation from `modules/transports/base.py`.
- `execution/dispatch/transports/direct.py`: moved implementation from `modules/transports/direct.py`.
- `execution/dispatch/transports/relay.py`: moved implementation from `modules/transports/relay.py`.
- `execution/dispatch/transports/webhook.py`: moved implementation from `modules/transports/webhook.py`, or explicitly deferred with an import-boundary manifest entry if webhook migration is assigned to a follow-up.
- `execution/state/task_state_manager.py`: moved implementation from `modules/TaskStateManager.py`.
- `execution/state/locking.py`: room-level local + distributed lock wrapper extracted from `RoomMessageCenter`.
- `execution/hitl/service.py`: moved HITL runtime behavior from `services/hitl_service.py`.
- `execution/hitl/adapters.py`: constructor-injected HITL port adapters over existing DB, SSE, A2A continuation, and task notification services; adapters receive concrete dependencies instead of importing service singletons.
- `execution/hitl/exceptions.py`: Execution-owned HITL exceptions replacing `fastapi.HTTPException` in moved HITL code.
- `execution/hitl/factory.py`: `create_hitl_service(...)` and a bindable legacy proxy used by `services/hitl_service.py`.
- `execution/hitl/detector.py`: prompt type detection extracted from `services/hitl_service.py`.
- `execution/translators.py`: translations between legacy response/request models and Common Execution DTOs.
- `tests/test_execution_protocols.py`: protocol conformance, package export, and import-boundary tests.
- `tests/test_execution_facade.py`: facade scheduling, cancellation, run lookup, HITL adapter, and hub-response sink tests.
- `tests/test_phase7_execution_event_gate.py`: AST gate for typed EventPublisher migration and legacy SSE usage exceptions.
- `tests/fixtures/phase7_execution_event_callers.json`: manifest for Execution-owned processing-status typed emits.

Modify:
- `common/dto/execution.py`: make `ExecutionRequest`, `RunInfo`, and HITL DTOs represent the current frontend/API payloads without breaking existing tests.
- `common/protocols/execution_protocols.py`: update `ExecutionEngine.cancel(...)` to carry cancellation audit identity; update HITL resolve/cancel protocols to carry `room_id`.
- `common/dto/delivery.py`: keep the Phase 6 `ProcessingStatusEvent` status set unless a separate DTO/translator widening task is approved.
- `common/protocols/__init__.py`: export any new protocol if added in Common.
- `container.py`: add `ExecutionDeps`, `create_execution_facade(...)`, and `create_execution_deps(...)`.
- `main.py`: wire Execution after Delivery/Agent/Room/Context Memory; register hub internal handler when available; call `execution_engine.heal_diverged_runs()` on startup and `cancel_inflight_tasks()` during shutdown.
- `api/room_center.py`: route `/roomCenter/sendMessage` and `/roomCenter/inquiryActiveRuns` through `ExecutionEngine`.
- `api/hitl.py`: route pending/respond/cancel through `HITLManager`.
- `api/sse.py`: route message cancellation through `ExecutionEngine.cancel()` while leaving stream connect/status on `SSETransport` / Delivery.
- `pyproject.toml`: add `execution`, `execution.orchestration`, `execution.dispatch`, `execution.dispatch.middleware`, `execution.dispatch.transports`, and `execution.state` packages when those packages are created; add `execution.hitl` in Task 5 when the HITL package is created. Keep legacy nested packages `modules.transports` and `modules.middleware` packaged until Phase 9 so old import paths still work in built artifacts.
- Moved legacy files under `modules/`, `modules/transports/`, and `modules/middleware/`: replace only those moved files with compatibility imports; unrelated legacy modules remain real implementations.
- `services/a2a_constants.py`: keep as a compatibility adapter over `common.a2a_constants` plus A2A SDK enum conversion for legacy callers.
- `services/hitl_service.py`: replace implementation with a compatibility shim over `execution.hitl` wiring; keep singleton import compatibility.
- Existing tests for `modules.*`, `api.hitl`, `api.sse`, `api.room_center`, `services.hitl_service`, and Phase 7a processing-status gates.
- `docs/MODULAR_DECOUPLING_DESIGN.md`: update Phase 7 status and reference this plan.

Reference-only:
- `docs/MODULAR_DECOUPLING_DESIGN.md` sections 3.3, 4.4, 4.5, 5.2, 5.4, 5.5, 6.3, 8.2, and 14.
- `docs/superpowers/plans/2026-05-15-phase-6-delivery-module-extraction.md`
- `docs/superpowers/plans/2026-05-16-phase-7a-record-then-emit.md`
- `docs/superpowers/plans/2026-05-16-phase-7a-delivery-extraction-handoff.md`
- `services/run_command_handler.py`
- `services/run_lifecycle_service.py`
- `services/sse_services.py`
- `models/request.py`, `models/response.py`, `models/hitl.py`, `models/run.py`

## Dependency Shape

```text
api.room_center / api.hitl / api.sse
  -> container-bound ExecutionDeps protocols
    -> execution.facade.ExecutionFacade
      -> execution.orchestration.RoomMessageCenter
      -> execution.hitl.service.HITLService
      -> execution.run_lifecycle.RunLifecycleAdapter
      -> common.protocols.EventPublisher
      -> Agent / Room / Context & Memory / A2A / Hub protocols

execution/**
  -> common.dto / common.protocols
  -> injected protocols and execution-local modules
  -> temporary legacy services only inside named compatibility adapters called out by tests
  -> no direct modules.*, database.*, services.*, main, api, container, or concrete delivery imports outside that allowlist

Delivery remains pure:
  execution.events records lifecycle before calling EventPublisher.emit(...)
  delivery/** never imports execution, modules, services, run_command_handler, or hitl_service
```

## Known Deviations / Deferred Target Architecture

Phase 7b is an Execution module extraction and typed-delivery migration, not the final
Execution target architecture from `docs/MODULAR_DECOUPLING_DESIGN.md §5`.

- **Execution repositories are deferred.** The target `execution/repository/`
  package (`RunRepository`, `RunEventRepository`, `HITLRepository`) remains future
  work. Phase 7b uses adapter-backed persistence over the existing run command
  handler, HITL service behavior, and Mongo collections so the module boundary can
  land without rewriting persistence semantics.
- **RunLifecyclePort is intentionally reduced.** The target design includes
  `create_run`, `start_run`, `complete_run`, `fail_run`, `pause_run`, `cancel_run`,
  and `emit_event`. Current production code is still driven by processing-status
  lifecycle writes, so Phase 7b starts with `record_processing_status()`,
  `heal_diverged_runs()`, and the watchdog-specific
  `append_run_timeout_failure()` adapter. Full lifecycle command methods remain
  a follow-up task once run persistence is owned by Execution repositories.
- **HITLCoordinator starts with prompt and cleanup methods only.** Current
  `SupervisorExecutor` / `QueueExecutor` / response-handler call sites create
  HITL prompts through `request_input()`, and `SupervisorExecutor` also cancels
  clarification requests during cleanup through `cancel_request()`. Phase 7b
  includes those two methods; query helpers such as `is_hitl_pending()` and
  `get_active_hitl()` remain target-design follow-up methods until runtime call
  sites need them.
- **Event ordering preserves Phase 7a wire behavior.** Typed Phase 7b emits use
  `record_processing_status()` -> optional `RunEventNotification` ->
  `ProcessingStatusEvent`. Changing to a different frontend-visible order requires
  a separate frontend-coordinated migration.
- **Legacy processing statuses stay on a compatibility path.** The current
  `ProcessingStatusEvent` DTO accepts only `queued`, `processing`, `completed`,
  `failed`, and `canceled`. Existing Execution code also emits legacy frontend
  statuses such as `awaiting_input`, `rejected`, `rate_limited`, and `error`.
  Phase 7b must not coerce those into an invalid typed DTO. It records lifecycle
  state first, emits optional `RunEventNotification`, then sends unsupported
  processing-status values through a manifest-covered Delivery compatibility
  frame that preserves the current SSE shape. A future DTO/translator widening
  can migrate these statuses to typed `ProcessingStatusEvent`.
- **Legacy processing-status detail shapes can also require compatibility.**
  Some current supported statuses still send frontend `details` as a raw string
  (for example `"Planning next action..."`). Phase 7b must preserve that wire
  shape through the compatibility publisher unless the typed DTO/translator is
  explicitly widened in a frontend-compatible migration.
- **Legacy compatibility wiring is explicit.** `DeliveryDeps` intentionally does
  not expose `DeliveryFacade.compat`. Phase 7b reaches the Phase 6 compatibility
  path only through an injected Execution-local `LegacyProcessingStatusPublisher`
  port, backed by the already-bound C3 `services.sse_services.sse_manager`
  adapter. No Execution code should reach into concrete Delivery or assume a
  `delivery_compat` attribute exists.
- **Some file layout names remain Phase 7b adapter names.** The target design
  still shows `execution/run/`, `execution/hitl/hitl_service.py`, and flat
  `execution/dispatch/direct_transport.py` names. This plan uses
  `execution/run_lifecycle.py`, `execution/hitl/service.py`,
  `execution/hitl/detector.py`, `execution/dispatch/agent_dispatcher.py`, and
  `execution/dispatch/transports/direct.py` to minimize mechanical churn while
  moving current files. Aligning the package layout fully with the target tree is
  follow-up cleanup unless it becomes necessary during implementation.
- **Legacy concrete imports require path-level allowlists.** Copied files must
  not carry `modules.*`, `database.*`, concrete `delivery.*`, or broad
  `services.*` imports into `execution/**`. Replace them with execution-local
  imports and injected ports. If an adapter-backed legacy dependency is
  unavoidable in Phase 7b, put it in a named compatibility adapter, add an expiry
  note, and include that exact path in the import-boundary tests.
- **Active-run and HITL frontend shapes are part of the contract.** Phase 7b
  must not narrow `/roomCenter/inquiryActiveRuns` or `/rooms/{room_id}/hitl/*`
  responses to simplified DTOs. `RunInfo` must carry `trigger_message_id`, and
  Common HITL DTOs must carry the current pending-response fields including the
  computed `message_id`.
- **High-risk route cutover needs shadow evidence.** `/roomCenter/sendMessage`
  and HITL resolve/cancel must have a shadow/diff gate before direct protocol
  cutover. Do not replace route handlers outright until fixtures prove
  response-shape parity and side-effect ordering.
- **A2A SDK leakage is a Phase 7b shortcut, not the target boundary.** The
  target architecture keeps A2A SDK types inside the A2A adapter. Phase 7b may
  temporarily allow A2A imports in explicitly listed moved dispatch/orchestration
  files to preserve behavior during extraction. Do not expand that allowlist
  silently; every allowed path needs an expiry note, and new Execution-owned
  cancellation/HITL code must use string/domain-state ports instead of importing
  `a2a.types`.
- **Shared A2A status constants are not Execution-owned in Phase 7b.** Current
  non-Execution services import `services.a2a_constants`, so moving the canonical
  constants to `execution/dispatch/a2a_constants.py` would create a reverse
  dependency from Delivery/Run services into Execution. Phase 7b moves the
  canonical SDK-free string constants to `common/a2a_constants.py`; both
  Execution and legacy `services/a2a_constants.py` use Common until a later A2A
  adapter cleanup owns the constants. `common/a2a_constants.py` must not import
  `a2a.types`; any `TaskState` enum conversion stays in app-shell,
  `services/a2a_constants.py`, or A2A adapter callables. Existing
  `common/utils/a2a_helpers.py` still imports A2A SDK types and is a documented
  legacy utility exception, not part of the new SDK-free constants boundary.
  Moved Execution response handling may continue to call it during Phase 7b, but
  the import-boundary manifest must list that exception and a follow-up A2A
  adapter task must move the conversion out of Common. The legacy
  `services.a2a_constants` surface must keep the current enum-like contract for
  existing callers, including
  `[s.value for s in TERMINAL_STATES]` and
  `[s.value for s in NON_TERMINAL_STATES]`.
- **`api/a2a_tasks.py` route migration is deferred.** The target design classifies
  A2A long-running task routes as Execution API, but Phase 7b only migrates
  `/roomCenter/sendMessage`, active-run reads, HITL routes, and cancellation.
  `api/a2a_tasks.py` remains a documented legacy API bridge until a follow-up
  Execution A2A task API migration can replace its direct service/database
  dependencies and add route/static gates.
- **RoomServices receives temporary Execution-owned callables.** This plan binds
  `hitl_runtime.get_pending_requests`, an app-shell active-run reader, and an
  app-shell processing-status emitter into `services.room_services` while
  sendMessage persistence, inquiryRoomSetting assembly, and validation still
  live there. That is a deliberate Phase 7b bridge, not target architecture: it
  creates a narrow RoomServices -> Execution/app-shell callable dependency so
  Phase 7b can preserve behavior without moving the full RoomServices response
  assembly path. Cleanup owner: the follow-up Execution/Room boundary task must
  move pending-HITL gating, embedded active-run enrichment, and sendMessage
  lifecycle/status emission fully behind `ExecutionFacade` or a Room-owned
  protocol, then remove `bind_hitl_pending_checker(...)`,
  `bind_active_run_reader(...)`, and `bind_execution_event_deps(...)` from
  `services.room_services`.
- **Runtime construction uses bridge objects in Phase 7b.** The target
  architecture wants Execution to depend on Agent/Room/Context/A2A/Hub
  protocols. Phase 7b does not introduce every one of those protocol ports while
  moving the large current graph. The compatibility factories may accept
  concrete legacy services as `Any` in app-shell wiring, including
  `RoomMessageCenterDeps` and the facade constructor's temporary `room_center`,
  `room_message_center`, and `hitl_service` bridge objects. `RoomMessageCenter`
  may also receive the current concrete `sse_manager` through the factory until
  its event paths are fully ported. Moved orchestration classes should receive
  explicit constructor dependencies from the factories, and follow-up work must
  replace those concrete bridge deps with module protocols.
- **Direct settings imports are deferred for exact moved orchestration files.**
  The target design injects module-scoped config instead of importing
  `config.settings` or reading process environment variables directly. Phase 7b
  may temporarily preserve current direct settings imports in moved
  `RoomMessageCenter` / `SupervisorExecutor` code and the existing
  `SupervisorExecutor` `os.environ` read for `SUPERVISOR_MAX_STEPS` while the
  large graph is being moved, but those paths must be explicitly allowlisted in
  the import-boundary test with an expiry note. New Execution code should prefer
  factory-injected scalar settings.
- **Hub response ownership and idempotency are Phase 8 target behavior.** Phase
  7b may register `HubAgentResponseInternal` handling as a seam, but it does not
  add `_owned_hub_tasks`, response idempotency persistence, duplicate detection,
  or durable replay. Those require the HubRuntimeBridge response-processing port
  and idempotency repository planned for Phase 8.

## Tasks

### Task 1: Baseline Gates And Phase 6 Prerequisite

**Files:**
- Reference: `docs/superpowers/plans/2026-05-15-phase-6-delivery-module-extraction.md`
- Reference: `docs/superpowers/plans/2026-05-16-phase-7a-delivery-extraction-handoff.md`

- [ ] **Step 1: Create the implementation branch**

Run:

```bash
git status --short --branch
git switch -c phase-7-execution-module
```

Expected: clean worktree before branch creation.

- [ ] **Step 2: Verify Phase 7a is still complete**

Run:

```bash
PYTHONPATH=. uv run pytest -q \
  tests/test_phase7a_processing_status_gate.py \
  tests/test_phase7a_processing_status_golden.py \
  tests/test_run_lifecycle_service.py \
  tests/test_stale_task_checker_run_lifecycle.py
```

Expected: all selected Phase 7a tests pass.

- [ ] **Step 3: Verify Phase 6 source exists**

Run:

```bash
find delivery -maxdepth 3 -type f -not -path '*/__pycache__/*' | sort
```

Expected: includes `delivery/facade.py`, `delivery/event_publisher.py`, `delivery/translator.py`, `delivery/sse/manager.py`, and `delivery/event_bus/cross_instance.py`.

If the command returns no source files, stop. Phase 7b cannot start from the current `main`; land Phase 6 first.

- [ ] **Step 4: Verify Phase 6 tests**

Run:

```bash
PYTHONPATH=. uv run pytest -q \
  tests/test_delivery_protocols.py \
  tests/test_delivery_translator.py \
  tests/test_delivery_event_publisher.py \
  tests/test_sse_adapter_delivery.py
```

Expected: all selected Delivery tests pass.

- [ ] **Step 5: Commit the baseline marker only if needed**

Do not commit if no files changed.

### Task 2: Tighten Execution DTOs And Public Protocol Tests

**Files:**
- Modify: `common/dto/execution.py`
- Modify: `common/protocols/execution_protocols.py`
- Modify: `tests/test_common_foundation.py`
- Create: `tests/test_execution_protocols.py`

- [ ] **Step 1: Write failing DTO coverage**

Add tests proving `ExecutionRequest` can represent the real `/roomCenter/sendMessage` payload:

```python
def test_execution_request_matches_send_message_payload_shape():
    req = ExecutionRequest(
        room_id="room-1",
        sender_id="user-1",
        sender_name="User",
        message={"message_content": {"message_text": "hello"}},
        attachments=[{"file_id": "file-1"}],
        inline_file_ids=["file-inline"],
        client_request_id="cr-1",
        target_group="room_team",
        target_group_id=None,
        mentioned_agent_ids=["agent-1"],
        mode="supervisor",
    )
    assert req.message["message_content"]["message_text"] == "hello"
    assert req.client_request_id == "cr-1"
```

Keep this sample valid under the current route contract: do not set a non-null
`message_target_mode` and `mentioned_agent_ids` in the same request.

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_common_foundation.py::test_common_foundation_dtos_can_be_instantiated tests/test_execution_protocols.py
```

Expected before implementation: fails because the fields do not exist.

Also add DTO coverage for current active-runs and HITL pending response shapes:

```python
def test_run_info_preserves_active_run_ref_shape():
    info = RunInfo(
        run_id="run-1",
        room_id="room-1",
        state="processing",
        trigger_message_id="user-msg-1",
        agent_id="agent-1",
        seq=3,
    )
    assert info.trigger_message_id == "user-msg-1"

def test_hitl_request_preserves_pending_api_shape():
    req = HITLRequest(
        request_id="hitl-1",
        room_id="room-1",
        user_message_id="user-msg-1",
        message_id="display-msg-1",
        source="supervisor",
        prompt="Choose",
        prompt_type="choice",
        choices=["A", "B"],
        agent_id="agent-1",
        agent_name="Researcher",
        display_message_id="display-msg-1",
        group_id="group-1",
        group_total=2,
        group_index=1,
        status="pending",
    )
    assert req.message_id == "display-msg-1"
    assert req.choices == ["A", "B"]

def test_execution_request_preserves_missing_message_as_none():
    req = ExecutionRequest(
        room_id="room-1",
        sender_id="user-1",
        message=None,
    )
    assert req.message is None

def test_execution_ack_preserves_missing_message_error_shape():
    ack = ExecutionAck(
        message_id=None,
        message=None,
        success=False,
        error="Message is required",
        status_code=400,
    )
    assert ack.message_id is None
    assert ack.message is None

def test_common_agent_event_preserves_legacy_compatibility_shape():
    event = AgentEvent(
        room_id="r1",
        agent_id="a1",
        message_id="m1",
        event_type="final",
        payload={"text": "hello"},
        hub_id="hub-1",
    )
    assert event.event_type == "final"
    assert event.payload == {"text": "hello"}
    assert event.hub_id == "hub-1"
```

- [ ] **Step 2: Update `ExecutionRequest` conservatively**

Implement a backward-compatible DTO shape:

```python
from pydantic import model_validator

class ExecutionRequest(FrozenDTO):
    room_id: str
    sender_id: str
    sender_name: str | None = None
    message: dict[str, Any] | None = None
    message_text: str | None = None
    attachments: list[dict[str, Any]] | None = None
    inline_file_ids: list[str] | None = None
    target_agent_ids: list[str] | None = None
    target_group: str | None = None
    target_group_id: str | None = None
    message_target_mode: str | None = None
    mentioned_agent_ids: list[str] | None = None
    parent_message_id: str | None = None
    client_request_id: str | None = None
    mode: Literal["direct", "supervisor", "debate"] = "direct"

class ExecutionAck(FrozenDTO):
    room_id: str | None = None
    message_id: str | None = None
    dispatch_root_message_id: str | None = None
    user_id: str | None = None
    user_name: str | None = None
    message: dict[str, Any] | None = None
    message_list: list[dict[str, Any]] | None = None
    scope_resolution_error: dict[str, Any] | None = None
    success: bool = True
    error: str | None = None
    status_code: int = 200

class ExecutionResult(FrozenDTO):
    # Existing Common DTO; preserve export and defaults for current callers/tests.
    success: bool
    run_id: str | None = None
    message_id: str | None = None
    error: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

class WorkflowState(FrozenDTO):
    # Existing Common DTO; preserve export and defaults for current callers/tests.
    run_id: str
    room_id: str
    state: str
    updated_at: datetime
    current_agent_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

class RunInfo(FrozenDTO):
    run_id: str
    room_id: str
    state: RunState | str
    trigger_message_id: str | None = None
    agent_id: str | None = None
    parent_run_id: str | None = None
    seq: int = 0
    error_code: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    error: str | None = None

class HITLRequest(FrozenDTO):
    request_id: str
    room_id: str
    user_message_id: str
    source: Literal["agent", "supervisor"]
    prompt: str
    message_id: str | None = None
    source_step_id: str | None = None
    agent_id: str | None = None
    agent_name: str | None = None
    a2a_task_id: str | None = None
    a2a_context_id: str | None = None
    continuation_message_id: str | None = None
    display_message_id: str | None = None
    prompt_type: Literal["text", "choice", "confirmation"] = "text"
    choices: list[str] | None = None
    group_id: str | None = None
    group_total: int | None = None
    group_index: int | None = None
    status: Literal["pending", "processing", "responded", "resolved", "expired", "canceled"] = "pending"
    expires_at: datetime | None = None
    created_at: datetime | None = None
    user_input: str | None = None
    responded_at: datetime | None = None
    responded_by_user_id: str | None = None

    @model_validator(mode="after")
    def populate_message_id(self) -> "HITLRequest":
        if self.message_id is None:
            object.__setattr__(
                self,
                "message_id",
                self.display_message_id
                or self.continuation_message_id
                or self.user_message_id,
            )
        return self

class HITLResponse(FrozenDTO):
    request_id: str
    status: str = "ok"
    response_text: str | None = None
    responder_id: str | None = None
    resolved_at: datetime | None = None
    reclaimed: bool | None = None
    error: str | None = None

class AgentEvent(FrozenDTO):
    # Deprecated Common compatibility DTO; preserve the current event_type/payload
    # shape for existing imports and tests. Phase 7b runtime normalized events use
    # execution.dispatch.agent_event.AgentEvent instead.
    room_id: str
    agent_id: str
    message_id: str
    event_type: Literal["partial", "final", "status_update", "error", "input_required"]
    payload: dict[str, Any] = Field(default_factory=dict)
    hub_id: str | None = None
```

Keep `message_text` optional so existing tests and future simplified callers
continue to work. Keep `ExecutionRequest.message` nullable and pass it through
unchanged; `/roomCenter/sendMessage` currently lets RoomServices produce the
existing `"Message is required"` response for missing messages, and coercing
`None` to `{}` can change validation behavior. `HITLRequest.message_id` is the
pending API display id: `display_message_id or continuation_message_id or
user_message_id`; implement that with a model validator/computed field, not by
requiring every caller to duplicate the fallback.
`HITLResponse` must be able to represent the current route response
`{"status": "ok", "request_id": ...}` as well as richer typed response fields.
`ExecutionAck` must preserve current `/roomCenter/sendMessage` response shapes,
including error responses with `room_id=None`, `message_id=None`, `user_id=None`,
and `message=None`. Do not coerce nullable response fields to empty strings or
`message=None` to `{}`. Keep `success: bool = True` so existing
`tests/test_common_foundation.py` instantiations remain valid.
Do not drop or rename existing Common exports such as `ExecutionResult` and
`WorkflowState`; preserve their defaults and update field-set tests only for
intentional new/changed fields.
Do not remove or reshape `common.dto.execution.AgentEvent` in Phase 7b; keep the
existing `event_type` / `payload` compatibility contract and existing
`tests/test_common_foundation.py` field-set assertions valid. The Phase 7b
runtime owner for normalized agent events is
`execution.dispatch.agent_event.AgentEvent`, because the response handler is
Execution-owned and its event dataclass is tightly coupled to dispatch side
effects. Mark the Common DTO deprecated for new Execution code, remove it from
new public protocols, and add Task 11 tests asserting the Hub adapter imports
`execution.dispatch.agent_event.AgentEvent` directly and does not import or
instantiate `common.dto.AgentEvent`.

- [ ] **Step 3: Add protocol conformance tests**

In `tests/test_execution_protocols.py`, assert `ExecutionFacade` will satisfy public protocols once created:

```python
def test_execution_protocols_exported():
    from common.protocols import ExecutionEngine, HITLManager, HubAgentResponseSink
    assert ExecutionEngine.__name__ == "ExecutionEngine"
    assert HITLManager.__name__ == "HITLManager"
    assert HubAgentResponseSink.__name__ == "HubAgentResponseSink"
    assert getattr(ExecutionEngine, "_is_runtime_protocol", False)
    assert getattr(HITLManager, "_is_runtime_protocol", False)
    assert getattr(HubAgentResponseSink, "_is_runtime_protocol", False)
```

Add protocol coverage that proves cancellation carries the audit user:

```python
def test_execution_engine_cancel_requires_requested_by_user_id():
    import inspect

    from common.protocols import ExecutionEngine

    sig = inspect.signature(ExecutionEngine.cancel)
    assert "requested_by_user_id" in sig.parameters
    assert sig.parameters["requested_by_user_id"].kind == inspect.Parameter.KEYWORD_ONLY
```

Update `common/protocols/execution_protocols.py`. Preserve
`@runtime_checkable` on every public protocol; Task 6 uses runtime
`isinstance(...)` checks, and omitting the decorator makes those checks raise
`TypeError` instead of proving conformance:

```python
from typing import Protocol, runtime_checkable


@runtime_checkable
class ExecutionEngine(Protocol):
    async def execute(self, request: ExecutionRequest) -> ExecutionAck: ...
    async def start_orchestration(
        self,
        request: ExecutionRequest,
        ack: ExecutionAck,
    ) -> None: ...
    async def cancel(
        self,
        room_id: str,
        message_id: str,
        *,
        requested_by_user_id: str,
    ) -> bool: ...
    async def get_run(self, run_id: str) -> RunInfo | None: ...
    async def get_runs_for_room(self, room_id: str) -> list[RunInfo]: ...
    async def cancel_inflight_tasks(self) -> int: ...
    async def heal_diverged_runs(self, limit: int = 500) -> int: ...
```

`start_orchestration(...)` is separate from `execute(...)` to preserve current
`/roomCenter/sendMessage` scheduling semantics. Today the route adds
`room_message_center.process_room_user_message(...)` to FastAPI
`BackgroundTasks`, so orchestration starts only after the response path is handed
off. Phase 7b must keep that after-response boundary: `execute()` persists and
returns `ExecutionAck`, while the route schedules
`ExecutionEngine.start_orchestration(request, ack)` through `BackgroundTasks`.
`start_orchestration()` then calls the facade's `_spawn_orchestration()` so the
task is still tracked by Execution once the background callback runs. Do not
start SSE/task side effects before returning the HTTP ack unless a deliberate
frontend/API migration documents the ordering change.

`requested_by_user_id` is required because current `api/sse.py` persists the
cancelling user through `mongodb.cancel_message(message_id, user.user_id)`, and
Execution must preserve that audit field after route migration.
Keep the rest of the public `ExecutionEngine` surface intact: later Phase 7b
tasks depend on run lookup, graceful shutdown cancellation, and divergent-run
healing through the same protocol.

Add protocol coverage that proves sensitive HITL operations keep room binding:
also prove `ExecutionEngine` exposes `start_orchestration(request, ack)`
separately from `execute(request)` so the API layer can preserve after-response
scheduling. Add a signature test that `HITLManager.create_hitl_request()`
accepts the current public HITL metadata fields (`source_step_id`, `agent_name`,
`display_message_id`, `prompt_type`, `choices`, and group fields) so the
protocol does not silently narrow pending-request shape.

```python
def test_hitl_manager_sensitive_methods_require_room_id():
    import inspect

    from common.protocols import HITLManager

    resolve_sig = inspect.signature(HITLManager.resolve_hitl)
    cancel_sig = inspect.signature(HITLManager.cancel_hitl)
    assert "room_id" in resolve_sig.parameters
    assert "room_id" in cancel_sig.parameters
```

Update `common/protocols/execution_protocols.py`:

```python
@runtime_checkable
class HITLManager(Protocol):
    async def create_hitl_request(
        self,
        room_id: str,
        user_message_id: str,
        prompt: str,
        source: Literal["agent", "supervisor"],
        source_step_id: str | None = None,
        agent_id: str | None = None,
        agent_name: str | None = None,
        a2a_task_id: str | None = None,
        a2a_context_id: str | None = None,
        continuation_message_id: str | None = None,
        display_message_id: str | None = None,
        prompt_type: Literal["text", "choice", "confirmation"] = "text",
        choices: list[str] | None = None,
        group_id: str | None = None,
        group_total: int | None = None,
        group_index: int | None = None,
    ) -> HITLRequest | None: ...

    async def resolve_hitl(
        self,
        room_id: str,
        request_id: str,
        response: str,
        responder_id: str,
    ) -> HITLResponse: ...

    async def get_pending_hitl(self, room_id: str) -> list[HITLRequest]: ...
    async def cancel_hitl(self, room_id: str, request_id: str) -> bool: ...


@runtime_checkable
class HubAgentResponseSink(Protocol):
    async def handle_hub_agent_response(
        self,
        event: HubAgentResponseInternal,
    ) -> None: ...
```

`room_id` is required because the current HITL service validates request/room
matches before responding or canceling; the protocol path must preserve that
cross-room guard.
`create_hitl_request()` is widened to the public/common HITL DTO shape so route
and facade paths can preserve source step, display-message, prompt type,
choices, and group metadata instead of silently narrowing to the current
minimal agent/task/context fields. Internal execution call sites may still use
the module-private `HITLCoordinator.request_input(..., **kwargs)` seam, but the
public `HITLManager` must not discard fields that appear in current pending
API responses.

Keep runtime conformance assertions skipped until `ExecutionFacade` exists in Task 6, then enable them.

- [ ] **Step 4: Run tests**

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_common_foundation.py tests/test_execution_protocols.py
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add common/dto/execution.py common/protocols/execution_protocols.py tests/test_common_foundation.py tests/test_execution_protocols.py
git commit -m "feat: align execution DTO with room send payload"
```

### Task 3: Add Execution Ports, Run Lifecycle/Read Adapters, And Event Helper

**Files:**
- Create: `execution/__init__.py`
- Create: `execution/ports.py`
- Create: `execution/run_lifecycle.py`
- Create: `execution/run_queries.py`
- Create: `execution/events.py`
- Create: `execution/legacy_processing_status.py`
- Modify: `common/dto/delivery.py` only if `RunEventNotification` lacks `correlation_id`
- Modify: `delivery/**` translator files if `RunEventNotification.correlation_id` is added
- Modify: `main.py` / `container.py` to construct the app-shell adapter instances reused by later tasks
- Modify: `pyproject.toml`
- Modify: `tests/test_execution_protocols.py`
- Modify: `tests/test_delivery_translator.py`
- Modify: `tests/test_delivery_event_publisher.py`
- Modify: `tests/test_sse_event_broker.py`
- Modify: `tests/test_common_foundation.py` if `RunEventNotification.correlation_id` is added
- Create: `tests/test_phase7_execution_event_gate.py`

- [ ] **Step 1: Write failing tests for lifecycle-first typed events**

Add a unit test that uses `AsyncMock` lifecycle and publisher dependencies:

```python
def make_client_request_id_resolver():
    resolver = AsyncMock()
    resolver.resolve_client_request_id = AsyncMock(
        side_effect=lambda message_id, provided: provided or f"resolved-{message_id}"
    )
    return resolver

async def test_emit_processing_status_records_run_event_then_processing_status():
    lifecycle = AsyncMock()
    lifecycle.record_processing_status.return_value = {
        "event_id": "evt-1",
        "run_id": "msg-1",
        "seq": 2,
        "type": "run_started",
        "payload": {"state": "processing"},
    }
    publisher = AsyncMock()
    compat = AsyncMock()
    resolver = make_client_request_id_resolver()

    await emit_processing_status(
        room_id="room-1",
        status="processing",
        message_id="msg-1",
        client_request_id="cr-1",
        run_lifecycle=lifecycle,
        event_publisher=publisher,
        legacy_processing_status_publisher=compat,
        run_event_enabled=lambda: True,
        client_request_id_resolver=resolver,
    )

    lifecycle.record_processing_status.assert_awaited_once()
    compat.emit_processing_status.assert_not_awaited()
    assert [call.args[0].event_type for call in publisher.emit.await_args_list] == [
        "run_event",
        "processing_status",
    ]
```

Also add a compatibility-path test for an unsupported legacy status:

```python
async def test_emit_processing_status_routes_awaiting_input_to_compat_frame():
    lifecycle = AsyncMock()
    publisher = AsyncMock()
    compat = AsyncMock()
    resolver = make_client_request_id_resolver()

    await emit_processing_status(
        room_id="room-1",
        status="awaiting_input",
        message_id="msg-1",
        details={"prompt": "Need input"},
        run_lifecycle=lifecycle,
        event_publisher=publisher,
        legacy_processing_status_publisher=compat,
        run_event_enabled=lambda: False,
        client_request_id_resolver=resolver,
    )

    lifecycle.record_processing_status.assert_awaited_once()
    publisher.emit.assert_not_awaited()
    compat.emit_processing_status.assert_awaited_once()

async def test_emit_processing_status_preserves_error_message_details_as_legacy_string():
    lifecycle = AsyncMock()
    publisher = AsyncMock()
    compat = AsyncMock()
    resolver = make_client_request_id_resolver()

    await emit_processing_status(
        room_id="room-1",
        status="failed",
        message_id="msg-1",
        legacy_details="agent failed",
        error_message="agent failed",
        run_lifecycle=lifecycle,
        event_publisher=publisher,
        legacy_processing_status_publisher=compat,
        run_event_enabled=lambda: False,
        client_request_id_resolver=resolver,
    )

    publisher.emit.assert_not_awaited()
    compat.emit_processing_status.assert_awaited_once()
    assert compat.emit_processing_status.await_args.kwargs["details"] == "agent failed"

async def test_emit_processing_status_resolves_client_request_id_when_omitted():
    lifecycle = AsyncMock()
    publisher = AsyncMock()
    compat = AsyncMock()
    resolver = make_client_request_id_resolver()

    await emit_processing_status(
        room_id="room-1",
        status="processing",
        message_id="msg-1",
        run_lifecycle=lifecycle,
        event_publisher=publisher,
        legacy_processing_status_publisher=compat,
        run_event_enabled=lambda: False,
        client_request_id_resolver=resolver,
    )

    resolver.resolve_client_request_id.assert_awaited_once_with("msg-1", None)
    event = publisher.emit.await_args.args[0]
    assert event.client_request_id == "resolved-msg-1"

async def test_emit_processing_status_keeps_run_event_correlation_explicit_only():
    lifecycle = AsyncMock()
    lifecycle.record_processing_status.return_value = {
        "event_id": "evt-1",
        "run_id": "msg-1",
        "seq": 1,
        "type": "run_started",
        "payload": {"state": "processing"},
    }
    publisher = AsyncMock()
    compat = AsyncMock()
    resolver = make_client_request_id_resolver()

    await emit_processing_status(
        room_id="room-1",
        status="processing",
        message_id="msg-1",
        client_request_id=None,
        run_lifecycle=lifecycle,
        event_publisher=publisher,
        legacy_processing_status_publisher=compat,
        run_event_enabled=lambda: True,
        client_request_id_resolver=resolver,
    )

    run_event, processing_status = [call.args[0] for call in publisher.emit.await_args_list]
    assert run_event.correlation_id is None
    assert processing_status.client_request_id == "resolved-msg-1"

async def test_emit_processing_status_resolver_failure_does_not_skip_lifecycle():
    lifecycle = AsyncMock()
    publisher = AsyncMock()
    compat = AsyncMock()
    resolver = AsyncMock()
    resolver.resolve_client_request_id.side_effect = RuntimeError("db down")

    await emit_processing_status(
        room_id="room-1",
        status="processing",
        message_id="msg-1",
        client_request_id=None,
        run_lifecycle=lifecycle,
        event_publisher=publisher,
        legacy_processing_status_publisher=compat,
        run_event_enabled=lambda: False,
        client_request_id_resolver=resolver,
    )

    lifecycle.record_processing_status.assert_awaited_once()
    assert lifecycle.record_processing_status.await_args.kwargs["client_request_id"] is None
    event = publisher.emit.await_args.args[0]
    assert event.client_request_id is None

async def test_emit_processing_status_separates_frontend_and_lifecycle_ids():
    lifecycle = AsyncMock()
    lifecycle.record_processing_status.return_value = {
        "event_id": "evt-1",
        "run_id": "user-msg-1",
        "seq": 1,
        "type": "run_started",
        "payload": {},
    }
    publisher = AsyncMock()
    compat = AsyncMock()
    resolver = make_client_request_id_resolver()

    await emit_processing_status(
        room_id="room-1",
        status="processing",
        message_id="agent-msg-1",
        lifecycle_message_id="user-msg-1",
        run_lifecycle=lifecycle,
        event_publisher=publisher,
        legacy_processing_status_publisher=compat,
        run_event_enabled=lambda: False,
        client_request_id_resolver=resolver,
    )

    lifecycle.record_processing_status.assert_awaited_once()
    assert lifecycle.record_processing_status.await_args.args[2] == "user-msg-1"
    event = publisher.emit.await_args.args[0]
    assert event.message_id == "agent-msg-1"

async def test_emit_processing_status_can_skip_lifecycle_for_legacy_send_only_paths():
    lifecycle = AsyncMock()
    publisher = AsyncMock()
    compat = AsyncMock()
    resolver = make_client_request_id_resolver()

    await emit_processing_status(
        room_id="room-1",
        status="processing",
        message_id="agent-msg-1",
        lifecycle_message_id=None,
        record_lifecycle=False,
        run_lifecycle=lifecycle,
        event_publisher=publisher,
        legacy_processing_status_publisher=compat,
        run_event_enabled=lambda: True,
        client_request_id_resolver=resolver,
    )

    lifecycle.record_processing_status.assert_not_awaited()
    assert publisher.emit.await_args.args[0].message_id == "agent-msg-1"

def test_run_event_notification_from_payload_maps_legacy_payload():
    payload = {
        "event_id": "evt-1",
        "run_id": "run-1",
        "seq": 7,
        "type": "run_completed",
        "payload": {"state": "completed"},
        "correlation_id": "payload-cr",
    }

    event = run_event_notification_from_payload(
        room_id="room-1",
        payload=payload,
        correlation_id="fallback-cr",
    )

    assert event.event_id == "evt-1"
    assert event.run_id == "run-1"
    assert event.seq == 7
    assert event.run_event_type == "run_completed"
    assert event.payload == {"state": "completed"}
    assert event.correlation_id == "payload-cr"

def test_run_event_delivery_translation_preserves_correlation_id():
    event = RunEventNotification(
        room_id="room-1",
        event_id="evt-1",
        run_id="run-1",
        seq=1,
        run_event_type="run_started",
        payload={"state": "processing"},
        correlation_id="cr-1",
    )
    # Use the actual Phase 6 Delivery translator entrypoint in the
    # implementation branch. At the time of writing the Phase 6 plan calls this
    # `to_sse_frame(...)`; if the branch exposes a different public name, import
    # that name here rather than pinning a stale alias.
    sse = to_sse_frame(event)
    assert sse.event == "run_event"
    assert sse.data["event_id"] == "evt-1"
    assert sse.data["run_id"] == "run-1"
    assert sse.data["seq"] == 1
    assert sse.data["type"] == "run_started"
    assert sse.data["payload"] == {"state": "processing"}
    assert sse.data["correlation_id"] == "cr-1"

def test_run_event_delivery_translation_preserves_null_correlation_key():
    event = RunEventNotification(
        room_id="room-1",
        event_id="evt-1",
        run_id="run-1",
        seq=1,
        run_event_type="run_started",
        payload={},
        correlation_id=None,
    )
    sse = to_sse_frame(event)
    assert "correlation_id" in sse.data
    assert sse.data["correlation_id"] is None

def test_processing_status_delivery_translation_preserves_legacy_sse_shape():
    event = ProcessingStatusEvent(
        room_id="room-1",
        message_id="msg-1",
        status="processing",
        details=None,
        client_request_id="cr-1",
        agents=[{"agent_id": "agent-1", "agent_name": "Agent One"}],
    )
    sse = to_sse_frame(event)
    assert sse.event == "processing_status"
    assert sse.data["status"] == "processing"
    assert sse.data["message_id"] == "msg-1"
    assert "details" in sse.data
    assert sse.data["details"] is None
    assert isinstance(sse.data["timestamp"], str)
    assert sse.data["client_request_id"] == "cr-1"
    assert sse.data["agents"] == [{"agent_id": "agent-1", "agent_name": "Agent One"}]

def test_processing_status_delivery_translation_preserves_structured_details():
    event = ProcessingStatusEvent(
        room_id="room-1",
        message_id="msg-1",
        status="failed",
        details={"message": "agent failed"},
        client_request_id=None,
        agents=None,
    )
    sse = to_sse_frame(event)
    assert sse.event == "processing_status"
    assert sse.data["details"] == {"message": "agent failed"}
    assert "timestamp" in sse.data
    assert "client_request_id" not in sse.data
    assert "agents" not in sse.data

def test_normalize_processing_status_accepts_string_and_enum_values():
    assert _normalize_processing_status("processing") == "processing"
    assert _normalize_processing_status(SSEProcessingStatus.COMPLETED) == "completed"

def test_unsupported_processing_status_stays_on_compat_path():
    assert _is_legacy_processing_status("awaiting_input") is True
    with pytest.raises(ValueError):
        _normalize_processing_status("awaiting_input")

def test_run_event_notification_from_payload_rejects_missing_required_fields():
    with pytest.raises(ValueError, match="event_id"):
        run_event_notification_from_payload(
            room_id="room-1",
            payload={
                "run_id": "run-1",
                "seq": 1,
                "type": "run_started",
                "payload": {},
            },
        )

async def test_emit_processing_status_rejects_missing_frontend_message_id_for_typed_status():
    lifecycle = AsyncMock()
    publisher = AsyncMock()
    with pytest.raises(ValueError, match="frontend message_id"):
        await emit_processing_status(
            room_id="room-1",
            status="processing",
            message_id=None,
            lifecycle_message_id="run-1",
            run_lifecycle=lifecycle,
            event_publisher=publisher,
            legacy_processing_status_publisher=AsyncMock(),
            run_event_enabled=lambda: False,
            client_request_id_resolver=make_client_request_id_resolver(),
        )
    lifecycle.record_processing_status.assert_not_awaited()
    publisher.emit.assert_not_awaited()
```

Expected before implementation: import failure.

- [ ] **Step 2: Define module-private ports**

Create `execution/ports.py`:

```python
import asyncio
from enum import Enum
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol

from common.dto import RunInfo

ProcessingStatusLike = str | Enum

# Phase 7b model boundary:
# - Public Execution/HITL protocols and API adapters expose common DTOs.
# - The moved HITL runtime may continue to use models.hitl.HITLRequest,
#   HITLStatus, and HITLPromptType internally while it is being extracted.
# - Boundary adapters must translate explicitly in execution/hitl/translators.py;
#   do not treat the internal models.hitl.HITLRequest as interchangeable with
#   common.dto.HITLRequest.

class TaskFactory(Protocol):
    def __call__(
        self,
        coro: Awaitable[Any],
        *,
        name: str | None = None,
    ) -> asyncio.Task[Any]: ...

RunEventEnabled = Callable[[], bool]
RunDualWriteEnabled = Callable[[], bool]

class HITLCoordinator(Protocol):
    async def request_input(
        self,
        room_id: str,
        user_message_id: str,
        source: str,
        prompt: str,
        **kwargs: Any,
    ) -> Any | None: ...

    async def cancel_request(self, request_id: str, room_id: str) -> None: ...

# Keep request_input positional order aligned with current HITLService:
# room_id, user_message_id, source, prompt. Moved call sites should still use
# source=... and prompt=... keywords to avoid positional drift.

class HITLPersistencePort(Protocol):
    """Persistence/query operations currently reached through database_service."""
    async def count_hitl_requests_for_message(self, message_id: str) -> int: ...
    async def create_hitl_request(self, doc: dict[str, Any]) -> bool: ...
    async def claim_hitl_request(
        self,
        request_id: str,
        *,
        status: str,
        claim_id: str,
        user_input: str,
        responded_at: Any,
        responded_by_user_id: str,
    ) -> dict[str, Any] | None: ...
    async def get_hitl_request(self, request_id: str) -> dict[str, Any] | None: ...
    async def update_hitl_request(self, request_id: str, **updates: Any) -> bool: ...
    async def fenced_update_hitl_request(
        self,
        request_id: str,
        claim_id: str,
        *update_docs: dict[str, Any],
        **updates: Any,
    ) -> bool: ...
    async def count_pending_in_hitl_group(self, group_id: str) -> int: ...
    async def get_hitl_group_requests(self, group_id: str) -> list[dict[str, Any]]: ...
    async def get_pending_hitl_requests(self, room_id: str) -> list[dict[str, Any]]: ...
    async def get_pending_hitl_requests_for_message(self, message_id: str) -> list[dict[str, Any]]: ...
    async def update_agent_message_task_state(self, message_id: str, state: str) -> None: ...
    async def persist_hitl_user_answer(self, message_id: str, user_input: str | None) -> None: ...
    async def persist_hitl_group_metadata(
        self,
        message_id: str,
        *,
        group_id: str,
        group_total: int | None,
        group_index: int | None,
    ) -> None: ...
    async def get_room_agent_message_by_message_id(self, message_id: str) -> Any | None: ...
    async def get_pending_continuation_on_message(self, message_id: str) -> dict[str, Any] | None: ...
    async def save_continuation_on_user_message(self, message_id: str, continuation: dict[str, Any]) -> bool: ...
    async def get_and_clear_continuation_on_message(self, message_id: str) -> dict[str, Any] | None: ...
    async def get_and_clear_continuation_on_user_message(self, message_id: str) -> dict[str, Any] | None: ...
    async def get_room_user_message_by_message_id(self, message_id: str) -> Any | None: ...
    async def resolve_client_request_id_for_message_id(self, message_id: str) -> str | None: ...
    async def reset_last_notified_state(self, message_id: str) -> None: ...
    async def iter_stale_processing_hitl_requests(self, cutoff: Any) -> AsyncIterator[dict[str, Any]]: ...
    async def cas_update_hitl_request(
        self,
        request_id: str,
        *,
        expected_status: str,
        **updates: Any,
    ) -> bool: ...

class HITLContinuationPort(Protocol):
    """Resume/cancel agent or supervisor continuations without importing A2A or RoomMessageCenter singletons."""
    async def reply_to_agent_task(
        self,
        *,
        request: Any,
        user_input: str,
    ) -> dict[str, Any]: ...
    async def resume_queue_from_continuation(
        self,
        continuation_message_id: str,
        *,
        task_result_text: str | None = None,
        failed: bool = False,
    ) -> bool: ...

class HITLTaskNotificationPort(Protocol):
    """Notify task state changes for HITL resume/cancel paths."""
    async def notify_task_update(
        self,
        message_id: str,
        state: str,
        *,
        room_id: str,
        user_id: str,
    ) -> bool: ...

class AgentTaskNotificationPort(Protocol):
    """Handler-owned terminal task notification after agent responses."""
    async def notify_task_update(
        self,
        message_id: str,
        state: str,
        *,
        room_id: str,
        user_id: str,
        error: str | None = None,
        parts: list[dict] | None = None,
    ) -> bool: ...

class AgentResponseHandlerPort(Protocol):
    """Shared response side-effect path used by direct, relay, webhook, and Hub."""
    async def handle(self, event: Any) -> None: ...

class HITLDeliveryPort(Protocol):
    async def emit_hitl_event(
        self,
        *,
        room_id: str,
        message_type: str,
        payload: dict[str, Any],
    ) -> None: ...

# HITLDeliveryPort receives the exact frontend SSE payload already built by the
# HITL runtime. The runtime, using HITLPersistencePort, must preserve the current
# client_request_id fallback behavior before calling delivery: first read the
# user message's client_request_id, then fall back to resolving by display /
# continuation / user message id. Legacy delivery must not try to reconstruct
# this payload from a narrow Common DTO.

class AgentDispatchPort(Protocol):
    async def dispatch(self, command: Any) -> Any: ...
    async def cancel(self, agent_id: str, task_id: str) -> bool: ...

class RunReadPort(Protocol):
    async def get_run(self, run_id: str) -> RunInfo | None: ...
    async def get_runs_for_room(self, room_id: str) -> list[RunInfo]: ...

class CancellationStatePort(Protocol):
    async def cancel_message_and_broadcast(self, message_id: str) -> None: ...
    def clear_cancellation(self, message_id: str) -> None: ...

class CancellationStorePort(Protocol):
    async def cancel_message(
        self,
        message_id: str,
        requested_by_user_id: str,
    ) -> bool: ...

class HITLMessageCancellationPort(Protocol):
    async def cancel_requests_for_message(self, message_id: str) -> None: ...

class AgentTaskCleanupPort(Protocol):
    async def cleanup_cancelled_message_tasks(
        self,
        *,
        room_id: str,
        message_id: str,
    ) -> None: ...

class LegacyProcessingStatusPublisher(Protocol):
    async def emit_processing_status(
        self,
        *,
        room_id: str,
        status: ProcessingStatusLike,
        message_id: str | None,
        details: dict[str, Any] | str | None = None,
        client_request_id: str | None = None,
        agents: list[dict] | None = None,
    ) -> None: ...

class ClientRequestIdResolver(Protocol):
    async def resolve_client_request_id(
        self,
        message_id: str | None,
        provided_client_request_id: str | None,
    ) -> str | None: ...

class RunLifecyclePort(Protocol):
    async def record_processing_status(
        self,
        room_id: str,
        status: ProcessingStatusLike,
        message_id: str | None,
        *,
        client_request_id: str | None = None,
        details: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any] | None: ...

    async def heal_diverged_runs(self, limit: int = 500) -> int: ...

    async def append_run_timeout_failure(
        self,
        room_id: str,
        run_id: str,
        *,
        stale_minutes: int,
    ) -> dict[str, Any] | None: ...
```

`HITLPersistencePort` must cover the current multi-round and recovery paths, not
only the request/response path. In particular, `reset_last_notified_state()`
preserves repeated `input_required` behavior, and
`iter_stale_processing_hitl_requests()` plus `cas_update_hitl_request()` preserve
`recover_stale_processing()` behavior still reached by
`jobs/stale_task_checker.py`.

Task notification and cancellation ports use domain string states such as
`"completed"`, `"failed"`, `"canceled"`, and `"rejected"`. Any conversion to
`a2a.types.TaskState` belongs in the concrete adapter or startup-bound callable,
not in Execution HITL/cancellation business code.

`RunEventEnabled` is the injected feature-flag seam for optional run-event SSE
frames. Execution code must receive it through constructors/binders and must not
import `run_event_sse_enabled` from `services.run_command_handler`.
`RunDualWriteEnabled` is a separate injected seam for persistence dual-write
behavior. Do not reuse `RunEventEnabled` for watchdog lifecycle writes: the
watchdog must skip `append_run_timeout_failure()` when run dual-write is off
even if run-event SSE emission would otherwise be enabled.

- [ ] **Step 3: Implement run lifecycle and read adapters**

Create `execution/run_lifecycle.py` with an adapter over the existing writer:

```python
from typing import Any, Callable

from execution.ports import ProcessingStatusLike
from common.utils.logger import get_logger
from models.run import NON_TERMINAL_RUN_STATE_VALUES

logger = get_logger(__name__)

class RunLifecycleAdapter:
    def __init__(self, command_handler, runs_collection) -> None:
        self._command_handler = command_handler
        self._runs_collection = runs_collection

    async def record_processing_status(
        self,
        room_id: str,
        status: ProcessingStatusLike,
        message_id: str | None,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        status_value = getattr(status, "value", status)
        error_message = kwargs.get("error_message")
        details = kwargs.get("details")
        if error_message is None and isinstance(details, dict):
            error_message = details.get("message") or details.get("error")
        return await self._command_handler.record_processing_status(
            room_id=room_id,
            status=status_value,
            message_id=message_id,
            client_request_id=kwargs.get("client_request_id"),
            details=error_message,
        )

    async def heal_diverged_runs(self, limit: int = 500) -> int:
        try:
            cursor = self._runs_collection.find(
                {"state": {"$in": list(NON_TERMINAL_RUN_STATE_VALUES)}},
            ).limit(limit)
            docs = await cursor.to_list(length=limit)
        except Exception:
            logger.warning("startup heal: failed to query non-terminal runs", exc_info=True)
            return 0

        healed = 0
        for doc in docs:
            run_id = str(doc.get("run_id", ""))
            if not run_id:
                continue
            try:
                if await self._command_handler.heal_head_from_events(run_id):
                    healed += 1
            except Exception:
                logger.warning("startup heal: error healing run %s", run_id, exc_info=True)
        return healed

    async def append_run_timeout_failure(
        self,
        room_id: str,
        run_id: str,
        *,
        stale_minutes: int,
    ) -> dict[str, Any] | None:
        return await self._command_handler.append_run_timeout_failure(
            room_id,
            run_id,
            stale_minutes=stale_minutes,
        )
```

`heal_diverged_runs()` may still use the existing Mongo collection during Phase
7b, but all callers must go through `ExecutionEngine`. Preserve the current
startup-heal fault isolation: catch/log query failures and return `0`, and
catch/log per-run failures while continuing the sweep.
`append_run_timeout_failure()` is watchdog-specific and must preserve the current
timeout failure lifecycle semantics; do not replace that path with generic
`record_processing_status("failed")`. The current
`run_command_handler.append_run_timeout_failure()` signature accepts only
`room_id`, `run_id`, and `stale_minutes`; do not pass `client_request_id` unless
that command-handler signature is explicitly changed in the same task.

Create `execution/run_queries.py` with a read adapter over the existing run
collection/services:

```python
from models.run import NON_TERMINAL_RUN_STATE_VALUES


class RunQueryAdapter:
    def __init__(self, runs_collection) -> None:
        self._runs_collection = runs_collection

    async def get_run(self, run_id: str) -> RunInfo | None:
        try:
            doc = await self._runs_collection.find_one({"run_id": run_id})
        except Exception:
            logger.warning("run lookup failed for run_id=%s", run_id, exc_info=True)
            return None
        return run_doc_to_run_info(doc) if doc else None

    async def get_runs_for_room(self, room_id: str) -> list[RunInfo]:
        try:
            cursor = self._runs_collection.find({
                "room_id": room_id,
                "state": {"$in": list(NON_TERMINAL_RUN_STATE_VALUES)},
            }).sort("updated_at", -1)
            docs = await cursor.to_list(length=None)
            return [run_doc_to_run_info(doc) for doc in docs]
        except Exception:
            logger.warning("active-run lookup failed for room_id=%s", room_id, exc_info=True)
            return []
```

`run_doc_to_run_info()` must preserve `trigger_message_id` so
`/roomCenter/inquiryActiveRuns` keeps the current `ActiveRunRef` shape. The
room active-run query must match current `mongodb.get_active_runs_by_room_id()`:
non-terminal states only, sorted by `updated_at` newest first.
It must also preserve the current public failure behavior: DB/query errors are
logged and active-run list endpoints receive an empty list, not an exception.
Add `RunQueryAdapter` tests for:
- non-terminal room query filter and `updated_at` descending sort;
- `trigger_message_id` preservation;
- `get_runs_for_room()` returning `[]` on collection/cursor errors;
- `get_run()` returning `None` on lookup errors.

- [ ] **Step 4: Implement typed event helper**

Create `execution/events.py`:

```python
import logging
from typing import Any, Callable

from execution.ports import ProcessingStatusLike

logger = logging.getLogger(__name__)

# The helper accepts raw strings and enum-like values such as SSEProcessingStatus.
# Normalization reads `.value` when present before validating the typed DTO set.

def _typed_processing_status_details(
    details: dict[str, Any] | None,
    error_message: str | None,
) -> dict[str, Any] | None:
    if details is not None:
        return details
    if error_message:
        return {"message": error_message}
    return None

def _legacy_processing_status_details(
    details: dict[str, Any] | None,
    legacy_details: str | None,
    error_message: str | None,
) -> dict[str, Any] | str | None:
    if details is not None:
        return details
    if legacy_details is not None:
        return legacy_details
    return error_message

def _requires_legacy_processing_status_frame(
    status: ProcessingStatusLike,
    legacy_details: str | None,
) -> bool:
    return (
        _is_legacy_processing_status(status)
        or legacy_details is not None
    )

SUPPORTED_TYPED_PROCESSING_STATUSES = {
    "queued",
    "processing",
    "completed",
    "failed",
    "canceled",
}

def _processing_status_value(status: ProcessingStatusLike) -> str:
    return getattr(status, "value", status)

def _normalize_processing_status(status: ProcessingStatusLike) -> str:
    value = _processing_status_value(status)
    if value not in SUPPORTED_TYPED_PROCESSING_STATUSES:
        raise ValueError(f"Unsupported ProcessingStatusEvent status: {value}")
    return value

def _is_legacy_processing_status(status: ProcessingStatusLike) -> bool:
    return _processing_status_value(status) not in SUPPORTED_TYPED_PROCESSING_STATUSES

def _require_payload_field(payload: dict[str, Any], key: str) -> Any:
    value = payload.get(key)
    if value is None or value == "":
        raise ValueError(f"Run event payload missing required field: {key}")
    return value

def _require_frontend_message_id(message_id: str | None) -> str:
    if not message_id:
        raise ValueError("ProcessingStatusEvent requires frontend message_id")
    return message_id

async def _resolve_processing_status_client_request_id(
    resolver: ClientRequestIdResolver,
    message_id: str | None,
    client_request_id: str | None,
) -> str | None:
    try:
        return await resolver.resolve_client_request_id(message_id, client_request_id)
    except Exception:
        logger.warning(
            "processing status client_request_id resolution failed for message_id=%s",
            message_id,
            exc_info=True,
        )
        return client_request_id

def run_event_notification_from_payload(
    *,
    room_id: str,
    payload: dict[str, Any],
    correlation_id: str | None = None,
) -> RunEventNotification:
    return RunEventNotification(
        room_id=room_id,
        event_id=str(_require_payload_field(payload, "event_id")),
        run_id=str(_require_payload_field(payload, "run_id")),
        seq=int(_require_payload_field(payload, "seq")),
        run_event_type=str(_require_payload_field(payload, "type")),
        payload=payload.get("payload") or {},
        correlation_id=payload.get("correlation_id") or correlation_id,
    )

async def emit_processing_status(
    *,
    room_id: str,
    status: ProcessingStatusLike,
    message_id: str | None,
    run_lifecycle: RunLifecyclePort,
    event_publisher: EventPublisher,
    legacy_processing_status_publisher: LegacyProcessingStatusPublisher,
    run_event_enabled: Callable[[], bool],
    client_request_id_resolver: ClientRequestIdResolver,
    lifecycle_message_id: str | None = None,
    record_lifecycle: bool = True,
    client_request_id: str | None = None,
    details: dict[str, Any] | None = None,
    legacy_details: str | None = None,
    error_message: str | None = None,
    agents: list[dict] | None = None,
) -> dict[str, Any] | None:
    status_value = _processing_status_value(status)
    uses_legacy_frame = _requires_legacy_processing_status_frame(status, legacy_details)
    frontend_message_id = None if uses_legacy_frame else _require_frontend_message_id(message_id)
    payload = None
    if record_lifecycle:
        payload = await run_lifecycle.record_processing_status(
            room_id,
            status_value,
            lifecycle_message_id or message_id,
            client_request_id=client_request_id,
            details=details,
            error_message=error_message,
        )
    resolved_client_request_id = await _resolve_processing_status_client_request_id(
        client_request_id_resolver,
        message_id,
        client_request_id,
    )
    if payload and run_event_enabled():
        await event_publisher.emit(run_event_notification_from_payload(
            room_id=room_id,
            payload=payload,
            correlation_id=client_request_id,
        ))
    typed_details = _typed_processing_status_details(details, error_message)
    if uses_legacy_frame:
        await legacy_processing_status_publisher.emit_processing_status(
            room_id=room_id,
            status=status_value,
            message_id=message_id,
            details=_legacy_processing_status_details(details, legacy_details, error_message),
            client_request_id=resolved_client_request_id,
            agents=agents,
        )
        return payload
    await event_publisher.emit(ProcessingStatusEvent(
        room_id=room_id,
        message_id=frontend_message_id,
        status=_normalize_processing_status(status),
        details=typed_details,
        client_request_id=resolved_client_request_id,
        agents=agents,
    ))
    return payload
```

Keep the resolver best-effort and after lifecycle recording. Legacy behavior
records run lifecycle first, then SSE send-side client-request resolution may
fall back through the database; a resolver outage must not skip lifecycle
persistence. If the resolver raises, log and continue with the explicit
`client_request_id` value, which may be `None`.

`message_id` is the frontend-visible SSE message id. `lifecycle_message_id` is
the run/lifecycle id used for `record_processing_status()`. Do not collapse
these fields: Phase 7a callers such as `AgentResponseHandler._on_processing_status`
can record against a user/root message while emitting frontend status for an
agent message. For legacy send-only paths that did not record lifecycle state,
pass `record_lifecycle=False`; the helper must then skip run-event emission and
only emit the frontend typed/compat status frame.

If `RunEventNotification` does not yet expose `correlation_id`, add
`correlation_id: str | None = None` to `common/dto/delivery.py` so the typed
event can preserve the current run-event SSE correlation payload. Preserve the
current `payload: dict = Field(default_factory=dict)` default while editing the
DTO; do not change it to a required mutable-looking plain `dict` field. Update the
Delivery translator in the same task so the emitted SSE data still includes
`correlation_id`, and keep `tests/test_delivery_translator.py` coverage for that
field. Also update `tests/test_common_foundation.py` exact DTO field-set
assertions for `RunEventNotification` so the schema test reflects the new
optional field.

The Delivery translator must also preserve the current
`SSEManager.send_processing_status()` wire shape for typed
`ProcessingStatusEvent`: event name `processing_status`, data keys `status`,
`message_id`, `details`, and `timestamp` always present, `client_request_id`
present when non-null, and `agents` present when provided. Add translator tests
for `details=None`, structured `details`, timestamp presence, `client_request_id`,
and `agents`.

Terminal processing-status deduplication must remain equivalent to the legacy
`SSEManager.send_processing_status()` path. Today completed/failed/canceled
status frames are suppressed through the L1 and Redis terminal-status dedup
layer. Supported typed terminal statuses (`completed`, `failed`, `canceled`)
must route through the same Delivery/SSE dedup layer or an equivalent shared
dedup helper before clients receive frames. Add Delivery/EventPublisher tests
covering first terminal typed status delivery and duplicate terminal typed
status suppression across the same `(room_id, message_id)` key, reusing the
existing Redis/L1 behavior tested for the legacy SSE manager.

`_is_legacy_processing_status()` must return true for statuses that the current
DTO does not accept, including `awaiting_input`, `rejected`, `rate_limited`, and
`error`. Keep `run_event` before any `processing_status` frame for Phase 7b to
preserve Phase 7a/legacy wire order. If a different order is desired, handle it
as a separate frontend-coordinated migration.

The helper owns frontend detail normalization. Current frontend
`processing_status` frames sometimes carry string details even for statuses that
are otherwise supported by `ProcessingStatusEvent`, for example
`"Planning next action..."`. Do not convert those strings to
`{"message": ...}` and do not drop them. Migrated callers with old raw string
details must pass `legacy_details=...`; the helper records lifecycle state but
sends the frontend status through `LegacyProcessingStatusPublisher` so the wire
`details` value remains a string. Use structured `details: dict[str, Any]` only
for callers that already had structured frontend details. Use `error_message`
for run-lifecycle failure text; if the old frontend details were also a string,
pass both `error_message=...` and `legacy_details=...`.

When this helper is needed from legacy non-Execution services such as
`services.room_services`, inject it as a callable from the app shell. Do not make
RoomServices import `execution.events`; that would create a reverse dependency
from Room into Execution.

The helper also owns client-request-id fallback. Current `SSEManager` resolves a
missing `client_request_id` from the message id before sending SSE. Phase 7b must
preserve that behavior by injecting `ClientRequestIdResolver` and using the
resolved value for `ProcessingStatusEvent.client_request_id` and legacy
compatibility frames. Keep `RunEventNotification.correlation_id` tied to the
explicit `client_request_id` or to a `correlation_id` already returned in the
legacy run-event payload. Current run-event SSE broadcasting does not perform
the DB fallback that `send_processing_status()` performs, so Phase 7b must not
invent a run-event correlation id when the lifecycle write recorded `None`.

Create `execution/legacy_processing_status.py`:

```python
from typing import Any

from execution.ports import ProcessingStatusLike


class LegacyProcessingStatusC3Adapter:
    def __init__(self, sse_manager) -> None:
        self._sse_manager = sse_manager

    async def emit_processing_status(
        self,
        *,
        room_id: str,
        status: ProcessingStatusLike,
        message_id: str | None,
        details: dict[str, Any] | str | None = None,
        client_request_id: str | None = None,
        agents: list[dict] | None = None,
    ) -> None:
        status_value = getattr(status, "value", status)
        await self._sse_manager.send_processing_status(
            room_id,
            status_value,
            message_id,
            details=details,
            client_request_id=client_request_id,
            agents=agents,
        )

class SSEClientRequestIdResolver:
    def __init__(self, sse_manager) -> None:
        self._sse_manager = sse_manager

    async def resolve_client_request_id(
        self,
        message_id: str | None,
        provided_client_request_id: str | None,
    ) -> str | None:
        return await self._sse_manager._resolve_client_request_id(
            message_id,
            provided_client_request_id,
        )
```

This adapter is the only Phase 7b path from Execution to the Phase 6 C3
processing-status compatibility API. It must be injected by `container.py` /
`main.py`; do not source it from `DeliveryDeps`.
`SSEClientRequestIdResolver` is the temporary Phase 7b bridge for the existing
client-request-id DB fallback. It is app-shell wiring over current SSEManager
behavior; moved Execution code depends only on the `ClientRequestIdResolver`
port.

- [ ] **Step 5: Construct app-shell event dependencies**

After Delivery/SSE and the existing run command handler are available in the app
shell, construct these objects once and reuse these exact names in later tasks:

```python
run_lifecycle = RunLifecycleAdapter(
    command_handler=run_command_handler,
    runs_collection=mongodb.runs_collection,
)
legacy_processing_status_publisher = LegacyProcessingStatusC3Adapter(
    sse_manager=sse_manager,
)
app_shell_client_request_id_resolver = SSEClientRequestIdResolver(
    sse_manager=sse_manager,
)
```

Task 7 and Task 8 wiring must reference these objects rather than recreating
adapters or reaching into `DeliveryDeps`. If construction is centralized in
`container.py`, export a small holder/factory from there and bind it in
`main.py`; keep the runtime ownership in the app shell, not in Execution.

- [ ] **Step 6: Add package metadata**

Add the packages created in this task to `pyproject.toml`. Do not add
`execution.hitl` here; Task 5 creates that package and updates package metadata
there.

- [ ] **Step 7: Run tests**

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_execution_protocols.py tests/test_phase7_execution_event_gate.py
PYTHONPATH=. uv run pytest -q tests/test_delivery_translator.py
PYTHONPATH=. uv run pytest -q tests/test_delivery_event_publisher.py tests/test_sse_event_broker.py
PYTHONPATH=. uv run pytest -q tests/test_common_foundation.py
```

Expected: pass.

- [ ] **Step 8: Commit**

```bash
git add execution common/dto/delivery.py delivery main.py container.py pyproject.toml tests/test_execution_protocols.py tests/test_phase7_execution_event_gate.py tests/test_delivery_translator.py tests/test_delivery_event_publisher.py tests/test_sse_event_broker.py tests/test_common_foundation.py
git commit -m "feat: add execution lifecycle event helper"
```

### Task 4: Move Execution-Owned Modules With Compatibility Shims

**Files:**
- Create: `execution/orchestration/room_message_center.py`
- Create: `execution/orchestration/factory.py`
- Create: `execution/orchestration/queue_executor.py`
- Create: `execution/orchestration/supervisor_executor.py`
- Create: `execution/orchestration/debate_dispatcher.py`
- Create: `execution/dispatch/agent_dispatcher.py`
- Create: `common/a2a_constants.py`
- Create: `execution/dispatch/agent_event.py`
- Create: `execution/dispatch/agent_message_processor.py`
- Create: `execution/dispatch/dispatch_middleware.py`
- Create: `execution/dispatch/task_notifications.py`
- Create: `execution/dispatch/middleware/cloud_health.py`
- Create: `execution/dispatch/middleware/hub_transport.py`
- Create: `execution/dispatch/response_handler.py`
- Create: `execution/dispatch/transports/base.py`
- Create: `execution/dispatch/transports/direct.py`
- Create: `execution/dispatch/transports/relay.py`
- Create: `execution/dispatch/transports/webhook.py` unless explicitly deferred with a manifest entry
- Create: `execution/state/task_state_manager.py`
- Create: `execution/state/locking.py`
- Create: package `__init__.py` files under the new subpackages
- Modify: matching files under `modules/`
- Modify: matching files under `modules/transports/` and `modules/middleware/`
- Modify: `services/a2a_constants.py`
- Modify: `api/webhooks.py` if `modules/transports/webhook.py` is moved in this task
- Modify: `services/relay_service.py`
- Modify: `main.py`
- Modify: `container.py` only if construction helpers live there
- Modify: `pyproject.toml`
- Modify: existing tests importing `modules.*` only if shim behavior requires updates
- Create: `tests/test_common_a2a_constants.py`

- [ ] **Step 1: Add package export tests**

Add tests proving old and new import paths resolve to the same classes:

```python
def test_room_message_center_legacy_import_points_to_execution_class():
    from execution.orchestration.room_message_center import RoomMessageCenter as New
    from modules.RoomMessageCenter import RoomMessageCenter as Old
    assert Old is New

def test_room_message_center_legacy_proxy_requires_binding():
    from execution.orchestration.factory import BoundRoomMessageCenterProxy

    proxy = BoundRoomMessageCenterProxy()
    with pytest.raises(RuntimeError):
        proxy.process_room_user_message

def test_room_message_center_legacy_proxy_forwards_after_binding():
    from execution.orchestration.factory import BoundRoomMessageCenterProxy

    target = SimpleNamespace(
        bind_facade=object(),
        process_room_user_message=object(),
        resume_queue_from_continuation=object(),
        set_redis_service=object(),
    )
    proxy = BoundRoomMessageCenterProxy()
    proxy.bind(target)
    assert proxy.bind_facade is target.bind_facade
    assert proxy.process_room_user_message is target.process_room_user_message
    assert proxy.resume_queue_from_continuation is target.resume_queue_from_continuation
    assert proxy.set_redis_service is target.set_redis_service
```

Expected before move: import failure.

- [ ] **Step 2: Move one file at a time**

For each source file, copy the implementation to its new `execution/` path, update imports to execution-local paths, then reduce the old module to a shim:

```python
from execution.orchestration.room_message_center import (
    ROOM_LOCK_HOLD_TTL_SECONDS,
    ROOM_LOCK_TIMEOUT_SECONDS,
    RoomMessageCenter,
    RunStatus,
)
from execution.orchestration.factory import (
    bind_room_message_center,
    create_room_message_center,
    require_room_message_center,
    room_message_center,
)

__all__ = [
    "ROOM_LOCK_HOLD_TTL_SECONDS",
    "ROOM_LOCK_TIMEOUT_SECONDS",
    "RoomMessageCenter",
    "RunStatus",
    "bind_room_message_center",
    "create_room_message_center",
    "require_room_message_center",
    "room_message_center",
]
```

Each legacy shim must preserve the public symbols that tests and downstream
callers currently import from the old path, not only the primary class. For the
initial moved set this includes, at minimum: `modules.RoomMessageCenter.RunStatus`,
`modules.QueueExecutor.QueueProcessingResult`, `modules.QueueExecutor.QueueResult`,
`modules.transports.direct.MessageStreamingState`, and
`modules.TaskStateManager.state_str`. Either re-export those names from the shim
or update every importer/test in the same task; do not leave old imports broken
mid-plan.

Audit and update old-path monkeypatch users before replacing modules with shims.
Some tests patch module globals rather than imported classes, for example
`modules.RoomMessageCenter.db_service` and `modules.transports.relay.mongodb`.
For each moved legacy path, `rg` for `patch("modules.<old_path>` and
`monkeypatch.setattr(modules.<old_path>` and either preserve the patched global
as a shim alias that forwards into the moved implementation, or update the test
to patch the new injected dependency/new module path in the same task. Add this
audit to Task 4's focused compatibility tests so old-path monkeypatch drift does
not surface only in the final full suite.

Before reducing `modules/RoomMessageCenter.py` to a shim, add
`execution/orchestration/factory.py` so the moved implementation does not keep a
no-argument constructor that captures service singletons:

Task 4 also creates `execution/dispatch/task_notifications.py` because the moved
`AgentResponseHandler` and `RoomMessageCenter` factory require a concrete
`AgentTaskNotificationPort` before Task 6 exists:

```python
from collections.abc import Awaitable, Callable

class AgentTaskNotificationAdapter:
    def __init__(self, notify_task_update: Callable[..., Awaitable[bool]]) -> None:
        self._notify_task_update = notify_task_update

    async def notify_task_update(
        self,
        message_id: str,
        state: str,
        *,
        room_id: str,
        user_id: str,
        error: str | None = None,
        parts: list[dict] | None = None,
    ) -> bool:
        return await self._notify_task_update(
            message_id=message_id,
            state=state,
            room_id=room_id,
            user_id=user_id,
            error=error,
            parts=parts,
        )
```

Task 6 reuses this adapter for cancellation cleanup wiring; it must not define a
second incompatible class in `execution/cancellation.py`.
Define the app-shell `notify_task_update_with_string_state(...)` wrapper in Task
4 as well, before constructing `RoomMessageCenterDeps`; Task 5 and Task 7 reuse
the same callable. The wrapper must use hyphenated A2A wire states
(`"input-required"`, `"auth-required"`) and forward `error` / `parts`.

```python
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class RoomMessageCenterDeps:
    room_services: Any
    database_service: Any
    sse_manager: Any
    room_coordinator_service: Any
    openai_service: Any
    notification_service: Any
    agent_resolver_service: Any
    a2a_service: Any
    task_service: Any
    room_memory_service: Any
    debate_service: Any
    rate_limit_service: Any
    room_supervisor_service: Any
    relay_service_provider: Callable[[], Any | None]
    agent_health_service: Any
    dispatch_chain_factory: Callable[[], Any]
    cloud_health_middleware_factory: Callable[[Any], Any]
    hub_transport_middleware_factory: Callable[[Any], Any]
    context_assembly_service: Any
    memory_search_service: Any
    compaction_service: Any
    s3_service_provider: Callable[[], Any]
    capability_issue_service: Any
    build_turn_content: Callable[..., str]
    supervisor_planning_error_type: type[Exception]
    safety_net_task_notifier: AgentTaskNotificationPort
    a2a_service_error_type: type[Exception]
    hitl_coordinator: HITLCoordinator
    agent_response_task_notifier: AgentTaskNotificationPort
    run_lifecycle: RunLifecyclePort
    event_publisher: EventPublisher
    legacy_processing_status_publisher: LegacyProcessingStatusPublisher
    run_event_enabled: RunEventEnabled
    client_request_id_resolver: ClientRequestIdResolver

def create_room_message_center(deps: RoomMessageCenterDeps) -> RoomMessageCenter:
    ...

class BoundRoomMessageCenterProxy:
    def __init__(self) -> None:
        self._service: RoomMessageCenter | None = None

    def bind(self, service: RoomMessageCenter) -> None:
        self._service = service

    def _require_service(self) -> RoomMessageCenter:
        if self._service is None:
            raise RuntimeError("RoomMessageCenter has not been bound at startup")
        return self._service

    def __getattr__(self, name: str) -> Any:
        return getattr(self._require_service(), name)

room_message_center = BoundRoomMessageCenterProxy()

def bind_room_message_center(service: RoomMessageCenter) -> None:
    room_message_center.bind(service)

def require_room_message_center() -> RoomMessageCenter:
    return room_message_center._require_service()
```

The factory owns the current nested object graph: `TaskStateManager`,
`AgentDispatcher`, `AgentResponseHandler`, `DirectTransport`,
`AgentMessageProcessor`, `QueueExecutor`, and `SupervisorExecutor`. Pass
`hitl_coordinator` into `QueueExecutor`, `SupervisorExecutor`, and
`AgentResponseHandler` instead of leaving lazy `services.hitl_service` imports.
Pass `agent_response_task_notifier` into `AgentResponseHandler.notify_task_update()` so
the moved handler no longer imports concrete notification services. The moved
handler may still receive `a2a.types.TaskState` from existing response code, but
it must convert to `state.value` before calling `AgentTaskNotificationPort`;
`Execution` notification ports use domain strings only.
Pass `safety_net_task_notifier` only to RoomMessageCenter safety-net/background
paths that currently call task notification outside the response handler. Add
factory tests proving handler-owned notifications use
`agent_response_task_notifier` and safety-net paths use
`safety_net_task_notifier`; the two fields intentionally share a protocol but
represent different call sites.
Add a factory/helper for inbound response handling, for example
`create_agent_response_handler(deps: RoomMessageCenterDeps)`, and use it from
every path that constructs an `AgentResponseHandler`. Current inbound callers in
`api/webhooks.py` and `services/relay_service.py` instantiate the handler
directly with only `db`, `sse`, and `room_message_center`; after this task those
paths must call the same factory or receive the fully constructed handler so
they get `hitl_coordinator`, event dependencies, and task-notifier ports. Do not
leave relay/webhook inbound responses on the old constructor while moved
transports use the new constructor. Add tests covering `_get_webhook_transport()`
and relay-service transport construction with the new dependency shape.
The runtime must expose one stable shared handler seam for the facade Hub sink,
for example `room_message_center_runtime.agent_response_handler` or an
equivalent `require_agent_response_handler()` factory accessor. Task 11 must use
that same `AgentResponseHandlerPort` instance rather than constructing a fourth
handler path. Add a factory test proving direct/RMC, relay, webhook, and facade
Hub wiring all point at the shared response-handler seam or at handlers built by
the same factory with identical injected ports.
Build the relay/health dispatch chain from injected dependencies:
`relay_service_provider`, `agent_health_service`, `dispatch_chain_factory`,
`cloud_health_middleware_factory`, and `hub_transport_middleware_factory`.
`AgentMessageProcessor` must not lazily import `services.relay_service`,
`services.agent_health_service`, `modules.middleware.cloud_health`, or
`modules.middleware.hub_transport` after the move.
For `CloudHealthMiddleware`, inject the settings values it currently reads from
`config.settings` (`cloud_health_cache_ttl` and `cloud_health_check_timeout`)
through `cloud_health_middleware_factory`. If scalar injection is deferred, add
`execution/dispatch/middleware/cloud_health.py` to the exact temporary
`config.settings` allowlist with the same expiry note as the orchestration
files.
Pass the event helper dependencies and `run_event_enabled` into moved
orchestration components that emit processing status.

Use a bindable legacy proxy for `modules.RoomMessageCenter.room_message_center`
if startup cannot construct the real object at import time. Do not recreate
a hidden-global RoomMessageCenter singleton after the move.

Task 4 owns `RoomMessageCenter` runtime construction. Update `main.py` so
startup constructs the runtime with `create_room_message_center(...)`, passing
the Task 3 event dependencies (`run_lifecycle`,
`event_publisher`, `legacy_processing_status_publisher`, `run_event_enabled`, and a
`SSEClientRequestIdResolver(sse_manager=sse_manager)`) into
`RoomMessageCenterDeps`. Then call
`modules.RoomMessageCenter.bind_room_message_center(room_message_center_runtime)`,
then calls `room_message_center_runtime.bind_facade(_room_facade)` and
`room_message_center_runtime.set_redis_service(_redis_service)` where the old
singleton was used. Until Task 5 moves HITL, pass the existing legacy
`services.hitl_service.hitl_service` as the `hitl_coordinator` dependency. Later
tasks must reuse this bound runtime through `require_room_message_center()` and
must not recreate the RMC graph.

Move dependency leaves before dependents: `common/a2a_constants.py`,
`agent_event`, `dispatch/dispatch_middleware.py`, `dispatch/middleware/*`,
`dispatch/transports/base.py`, `state/locking.py`, and
`state/task_state_manager.py` first; then move direct/relay/webhook transports,
response handling, message processing, and orchestration files. Extract
room-level local/distributed lock helpers from `RoomMessageCenter` into
`execution/state/locking.py` in this task, or remove the file from the inventory
and add an explicit deferred-layout note. Do not leave `from modules...` or
`from services.a2a_constants` imports in `execution/**`; import shared SDK-free
  status constants from `common.a2a_constants` instead. A2A `TaskState` enum
conversion remains outside Common and outside new HITL/cancellation code.

`common/a2a_constants.py` must be SDK-free but still enum-like where callers need
`.value`; use `str, Enum` values for shared states/statuses. Do not make
`services/a2a_constants.py` a blind `from common.a2a_constants import *` shim:
current legacy callers import `TERMINAL_STATES` / `NON_TERMINAL_STATES` and
expect A2A `TaskState` members with `.value`. Preserve that contract in
`services/a2a_constants.py` by mapping the Common string values back to
`a2a.types.TaskState`, or update every `.value` caller in the same task and add
tests for `jobs/stale_task_checker.py` and `services/database_service.py`.
Moved Execution files that still receive A2A `TaskState` enum members must not
compare those enum objects directly against plain Common string constants.
Provide an SDK-free Common normalizer such as
`normalize_task_state_value(value: Any) -> str | None` that uses
`getattr(value, "value", value)` without importing `a2a.types`, and require all
Common helper predicates (`is_terminal_state`, `is_interactive_state`,
`is_failure_state`, and set-membership helpers) to accept both A2A enum-like
inputs and strings. Add tests covering `TaskState.completed` / `"completed"`,
`TaskState.input_required` / `"input-required"`, and invalid strings before
replacing `services.a2a_constants` imports in moved direct/relay/webhook
transports or `TaskStateManager`. If those normalization tests are not added in
Task 4, keep `services.a2a_constants` as a path-level allowlisted adapter for
the moved transport/state files until conversion is complete; do not silently
switch to string-only Common sets.
For example, rewrite `modules.dispatch_middleware` to
`execution.dispatch.dispatch_middleware` and `modules.middleware.*` to
`execution.dispatch.middleware.*`.

For copied files that currently import concrete services or singletons, replace
those imports with constructor-injected dependencies or module-private ports.
This includes lazy imports that are not obvious at file top level: context
assembly, memory search, compaction, S3 upload/download access, and agent
capability issue recording currently reached by `RoomMessageCenter` and direct
transport paths. Also account for `build_turn_content`,
`SupervisorPlanningError`, the RoomMessageCenter `safety_net_task_notifier`,
webhook cancel-path task notification, and `A2AServiceError`. Either add
explicit factory deps/ports for these services/types, as shown above, or isolate
the concrete dependency in a named adapter with a Task 12 allowlist and expiry
note.
Direct `config.settings` imports and direct environment reads in current
`RoomMessageCenter` / `SupervisorExecutor` code should be replaced with
factory-injected scalar config where practical. If that is too large for Phase
7b, keep only exact moved-file settings/env-read imports under a documented Task
12 allowlist and add follow-up cleanup, including the existing
`SUPERVISOR_MAX_STEPS` `os.environ` read.
Allowed temporary legacy imports must live only in named compatibility adapters
such as `execution/run_lifecycle.py` or `execution/legacy_processing_status.py`
and must be listed in the Task 12 allowlist with an expiry note.
Task 4 and Task 8 split responsibilities deliberately: Task 4 moves files and
removes broad service imports, but it may leave existing processing-status call
sites semantically unchanged by routing them through injected compatibility
ports/factory deps. Task 8 then replaces those call sites with
`emit_processing_status(...)` and tightens the manifest/static gate. Do not move
files into `execution/**` with direct `services.sse_services` or
`record_and_maybe_broadcast_run_event` imports just because Task 8 owns the
typed-event migration.
Also replace `fastapi.HTTPException` in moved business/dispatch code with
Execution-owned domain exceptions and map those exceptions back to HTTP in API
routes; `execution/**` must not import `fastapi`.
If the webhook transport is moved, update `api/webhooks.py` in the same task:
the route currently returns `transport.handle_webhook(...)` directly, so it must
catch the new Execution-owned webhook/domain exceptions and translate them back
to the existing HTTP status/detail behavior. If webhook migration is deferred,
record the deferral in the import-boundary manifest instead of moving a transport
that still raises `HTTPException`.

Update package metadata in the same task: add the new `execution.*` packages
created by Task 4, but do not add `execution.hitl` until Task 5 creates that
subpackage. Also add legacy nested shim packages `modules.transports` and
`modules.middleware` to `pyproject.toml` until Phase 9 removes the old import
surface.

Do this in small commits in this order:
- shared `common/a2a_constants`
- `TaskStateManager`
- `agent_event`
- `dispatch_middleware`
- `middleware/cloud_health` and `middleware/hub_transport`
- `transports/base`
- `AgentDispatcher`
- `agent_response_handler`
- `AgentMessageProcessor`
- `transports/direct`
- `transports/relay`
- `transports/webhook` if not deferred
- `debate_dispatcher`
- `QueueExecutor`
- `SupervisorExecutor`
- `RoomMessageCenter`

- [ ] **Step 3: Run focused tests after each move**

Before running `tests/test_module_room_message_center.py` after the
`RoomMessageCenter` move, update its implementation AST helpers to inspect
`execution/orchestration/room_message_center.py` instead of the legacy shim path.
Keep separate shim assertions for `modules/RoomMessageCenter.py`.
Also update direct no-arg construction fixtures in this task, including the
`RoomMessageCenter()` fixture in `tests/test_phase5_supervisor_integration.py`,
to use `create_room_message_center(...)` or a focused factory fixture with the
required dependencies. Do not rely on full-suite discovery to catch removed
no-arg construction.

Run the narrow test for the moved component, for example:

```bash
PYTHONPATH=. uv run pytest -q tests/test_module_queue_executor.py
PYTHONPATH=. uv run pytest -q tests/test_module_supervisor_executor.py
PYTHONPATH=. uv run pytest -q tests/test_module_room_message_center.py
PYTHONPATH=. uv run pytest -q tests/test_common_a2a_constants.py
```

Expected: pass after each moved component.

- [ ] **Step 4: Run the combined execution compatibility suite**

Run:

```bash
PYTHONPATH=. uv run pytest -q \
  tests/test_agent_response_handler.py \
  tests/test_agent_event_turn_id.py \
  tests/test_common_a2a_constants.py \
  tests/test_dispatch_middleware.py \
  tests/test_module_agent_dispatcher.py \
  tests/test_direct_transport.py \
  tests/test_relay_streams.py \
  tests/test_transport_parity.py \
  tests/test_turn_id_passthrough.py \
  tests/test_module_task_state.py \
  tests/test_module_queue_executor.py \
  tests/test_module_supervisor_executor.py \
  tests/test_phase5_supervisor_integration.py \
  tests/test_sdr_wave1.py \
  tests/test_unified_summary.py \
  tests/test_flow_contracts.py \
  tests/test_debate_dispatcher.py \
  tests/test_api_webhooks.py \
  tests/test_api_relay.py \
  tests/test_pipeline_integration.py \
  tests/test_module_room_message_center.py \
  tests/test_distributed_room_lock.py \
  tests/test_distributed_room_lock_integration.py
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add execution common/a2a_constants.py modules services/a2a_constants.py services/relay_service.py main.py container.py api/webhooks.py pyproject.toml tests
git commit -m "refactor: move execution modules behind compatibility shims"
```

### Task 5: Extract HITL Into Execution

**Files:**
- Create: `execution/hitl/service.py`
- Create: `execution/hitl/adapters.py`
- Create: `execution/hitl/exceptions.py`
- Create: `execution/hitl/factory.py`
- Create: `execution/hitl/translators.py`
- Create: `execution/hitl/detector.py`
- Create: `execution/hitl/__init__.py`
- Modify: `services/hitl_service.py`
- Modify: `services/room_services.py`
- Modify: `container.py` / `main.py` only as needed to bind the legacy HITL proxy during startup
- Modify: `pyproject.toml`
- Modify: `api/hitl.py` in this task to translate moved HITL exceptions before Task 7 route cutover
- Modify: `tests/test_api_hitl.py`
- Modify: `tests/test_service_hitl.py`
- Modify: `tests/test_flow_contracts.py`
- Modify: `tests/test_scope_validation.py`
- Modify: `tests/test_models.py` only if model imports need adjustment

Update `pyproject.toml` in this task to add the `execution.hitl` package after
the package files exist. Task 4 intentionally does not add `execution.hitl`
before this directory is created.

- [ ] **Step 1: Write detector tests**

Move prompt detection into `execution/hitl/detector.py` and add direct tests:

```python
def test_infer_prompt_type_detects_approve_reject():
    assert infer_prompt_type("Approve or reject this action").value == "confirmation"
```

- [ ] **Step 2: Move `HITLService` implementation behind ports**

Move the current service class into `execution/hitl/service.py`, but do not move
its lazy imports unchanged. First replace current references to
`services.database_service`, `services.sse_services`, `services.a2a_service`,
`modules.RoomMessageCenter`, and `services.task_notification_service` with
constructor-injected ports from `execution/ports.py`:
- HITL persistence/query port for the current database operations.
- HITL delivery/event port for current SSE/HITL event emission. Task 9
  deliberately keeps HITL frames on this legacy compatibility path because the
  current typed HITL delivery DTOs are too narrow for the frontend payload.
- HITL continuation/resume port for supervisor and A2A resume paths.
- HITL task notification port for task-state frontend notifications.

The moved constructor must take all required ports up front:

```python
class HITLService:
    def __init__(
        self,
        *,
        persistence: HITLPersistencePort,
        delivery: HITLDeliveryPort,
        continuation: HITLContinuationPort,
        task_notifier: HITLTaskNotificationPort,
    ) -> None:
        self._persistence = persistence
        self._delivery = delivery
        self._continuation = continuation
        self._task_notifier = task_notifier
```

The moved service may keep using `models.hitl.HITLRequest`, `HITLStatus`, and
`HITLPromptType` internally during Phase 7b because the existing persistence and
resume logic is built around those models. Do not expose that model as the
public `common.protocols.HITLManager` DTO. Add explicit boundary translators,
for example `model_hitl_request_to_common(...)`, for API/facade protocol
responses. Internal ports that pass a request object should type it as `Any` or
a local internal alias and document that it is the moved runtime model, not
`common.dto.HITLRequest`.

Create `execution/hitl/translators.py` with the concrete common-DTO boundary
mapping; do not put an ellipsis/stub implementation in `execution/ports.py`:

```python
from typing import Any

from common.dto import (
    HITLRequest as CommonHITLRequest,
    HITLResponse as CommonHITLResponse,
)


def model_hitl_request_to_common(request: Any) -> CommonHITLRequest:
    message_id = (
        getattr(request, "display_message_id", None)
        or getattr(request, "continuation_message_id", None)
        or getattr(request, "user_message_id", None)
    )
    return CommonHITLRequest(
        request_id=request.request_id,
        room_id=request.room_id,
        user_message_id=request.user_message_id,
        message_id=message_id,
        source=request.source,
        prompt=request.prompt,
        prompt_type=getattr(request.prompt_type, "value", request.prompt_type),
        choices=getattr(request, "choices", None),
        agent_id=getattr(request, "agent_id", None),
        agent_name=getattr(request, "agent_name", None),
        source_step_id=getattr(request, "source_step_id", None),
        a2a_task_id=getattr(request, "a2a_task_id", None),
        a2a_context_id=getattr(request, "a2a_context_id", None),
        continuation_message_id=getattr(request, "continuation_message_id", None),
        display_message_id=getattr(request, "display_message_id", None),
        group_id=getattr(request, "group_id", None),
        group_total=getattr(request, "group_total", None),
        group_index=getattr(request, "group_index", None),
        status=getattr(getattr(request, "status", None), "value", getattr(request, "status", "pending")),
        expires_at=getattr(request, "expires_at", None),
        created_at=getattr(request, "created_at", None),
        user_input=getattr(request, "user_input", None),
        responded_at=getattr(request, "responded_at", None),
        responded_by_user_id=getattr(request, "responded_by_user_id", None),
    )

def hitl_response_dict_to_common(result: dict[str, Any]) -> CommonHITLResponse:
    return CommonHITLResponse(
        request_id=str(result["request_id"]),
        status=str(result.get("status") or "ok"),
        response_text=result.get("response_text"),
        responder_id=result.get("responder_id"),
        resolved_at=result.get("resolved_at"),
        reclaimed=result.get("reclaimed"),
        error=result.get("error"),
    )

def hitl_cancel_none_to_success(result: None) -> bool:
    return True
```

Keep persistence-first behavior and route-resume semantics unchanged, but the
moved `execution/hitl/service.py` must already satisfy the Task 12 import
boundary. Only `services/hitl_service.py` may remain a compatibility shim.
The facade must use these translators at the public protocol boundary:
`resolve_hitl()` converts the current raw dict returned by
`handle_response(...)` into `common.dto.HITLResponse`, and `cancel_hitl()`
converts the current `cancel_request(...) -> None` success/no-op return into
`True`. Exceptions still propagate as Execution-owned HITL exceptions for the
API layer to translate.
Replace current `HTTPException` raises with Execution-owned exceptions from
`execution/hitl/exceptions.py`, for example:

```python
from typing import Any

from common.errors import AppError

class HITLError(AppError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "HITL_ERROR",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details)
class HITLNotFoundError(HITLError): ...
class HITLConflictError(HITLError): ...
class HITLRoomMismatchError(HITLError): ...
class HITLContinuationLostError(HITLError): ...
ContinuationLostError = HITLContinuationLostError  # legacy exported alias
class HITLRoutingFailedError(HITLError): ...
```

Use the existing Common error hierarchy. `HITLError` should subclass
`common.errors.AppError` / `HybroError`, and specialized HITL errors should
either subclass `HITLError` with stable `code` values or wrap the appropriate
Common subtype (`NotFoundError`, `ConflictError`, `AuthorizationError`,
`UpstreamError`) while preserving the route mappings below. Do not introduce
plain `Exception` protocol errors unless this is explicitly documented as a
temporary Phase 7b deviation.

Preserve the current semantic statuses: not found maps to 404, already-claimed
or already-terminal maps to 409, room mismatch maps to 403, lost continuation
maps to 410, and downstream routing failure maps to 502 in the API layer.
Task 5 must not create a mid-plan behavior regression where existing `api/hitl.py`
routes see raw `HITLError` exceptions and return 500. Update `api/hitl.py` in
this same task to translate the new exceptions while leaving the route bound to
the legacy shim; Task 7 can later replace the route dependency with
`HITLManager`.
The Task 5 tests must cover the legacy API error codes for 404, 409, 403, 410,
and 502 before the route protocol cutover happens.

- [ ] **Step 3: Add explicit HITL factory and port adapters**

Create `execution/hitl/factory.py` with a real factory and a bindable proxy for
legacy imports:

```python
from typing import Any

class BoundHITLServiceProxy:
    def __init__(self) -> None:
        self._service: HITLService | None = None

    def bind(self, service: HITLService) -> None:
        self._service = service

    def _require_service(self) -> HITLService:
        if self._service is None:
            raise RuntimeError("HITLService has not been bound at startup")
        return self._service

    def __getattr__(self, name: str) -> Any:
        return getattr(self._require_service(), name)

def create_hitl_service(
    *,
    persistence: HITLPersistencePort,
    delivery: HITLDeliveryPort,
    continuation: HITLContinuationPort,
    task_notifier: HITLTaskNotificationPort,
) -> HITLService:
    return HITLService(
        persistence=persistence,
        delivery=delivery,
        continuation=continuation,
        task_notifier=task_notifier,
    )
```

Create `execution/hitl/adapters.py` with concrete adapter classes for the
current services:
- `DatabaseHITLPersistenceAdapter(database_service)` implements every
  `HITLPersistencePort` method, including `reset_last_notified_state()`,
  `iter_stale_processing_hitl_requests()`, and `cas_update_hitl_request()`.
- `LegacyHITLDeliveryAdapter(sse_manager)` preserves current HITL SSE frames
  through the Phase 7b compatibility path. It accepts the exact
  prebuilt SSE `message_type` and payload from the HITL runtime; it does not
  rebuild payloads or resolve `client_request_id` itself.
- `A2AHITLContinuationAdapter(a2a_service, room_message_center_provider)`
  handles `reply_to_agent_task()` and queue resume without importing service
  singletons. Use a provider callable such as
  `modules_room_message_center.require_room_message_center` so startup ordering
  does not require constructing HITL after the concrete RoomMessageCenter
  instance is already available.
- `HITLTaskNotificationAdapter(notify_task_update)` accepts domain string states
  and performs any `TaskState` conversion inside the injected callable boundary,
  not inside `execution/hitl/service.py`.

These adapters receive concrete objects/callables from `main.py` or
`container.py`; they must not import `services.database_service`,
`services.sse_services`, `services.a2a_service`, `modules.RoomMessageCenter`, or
`services.task_notification_service` themselves.

Bind the proxy in this task before running the Task 5 test gate. Update startup
and test fixtures to construct the real service through `create_hitl_service(...)`
and call `services.hitl_service.bind_hitl_service(hitl_runtime)`. API/HITL tests
must not run against an unbound `BoundHITLServiceProxy`.

Also remove the current reverse dependency from `services/room_services.py` to
`services.hitl_service` before replacing `services/hitl_service.py` with an
Execution shim. `RoomServices.send_message_to_room()` currently lazy-imports
`services.hitl_service.hitl_service` to check pending HITL; after the shim move
that would create `Execution -> RoomServices -> services.hitl_service shim ->
Execution`. Add an app-shell bound callable or small Room-owned port, for
example:

```python
HITLPendingChecker = Callable[[str], Awaitable[list[Any]]]

def bind_hitl_pending_checker(checker: HITLPendingChecker) -> None: ...
```

Bind it in the same startup step as the HITL proxy:
`room_services.bind_hitl_pending_checker(hitl_runtime.get_pending_requests)`.
`RoomServices` should call the injected checker and should not import
`services.hitl_service` directly. Add a focused test that fails if
`services/room_services.py` still imports the HITL shim and a behavior test that
the pending-HITL rejection path still returns the current response shape. Also
pin the current failure behavior: if the injected pending checker raises,
RoomServices logs the warning and proceeds with message handling instead of
turning the sendMessage request into a 5xx or HITL rejection.

Add tests that prove:
- `BoundHITLServiceProxy` raises before startup binding and forwards after
  binding, including `recover_stale_processing()`.
- `model_hitl_request_to_common(...)` preserves the current pending-response
  fields, including computed `message_id`, display/group fields, choices, and
  responded metadata that API routes expose.
- `DatabaseHITLPersistenceAdapter` exposes the recovery methods used by
  `recover_stale_processing()` without direct Mongo access from
  `execution/hitl/service.py`.
- Multi-round input reset calls `reset_last_notified_state()` before replying to
  an A2A task.
- Legacy HITL delivery preserves `client_request_id` exactly as the current
  service does: user-message value first, then DB resolver fallback for the
  display/continuation/user message id, before calling
  `HITLDeliveryPort.emit_hitl_event(message_type=..., payload=...)`.

- [ ] **Step 4: Replace `services/hitl_service.py` with a shim**

Use:

```python
from execution.hitl.exceptions import (
    ContinuationLostError,
    HITLContinuationLostError,
)
from execution.hitl.service import (
    HITLService,
    MAX_HITL_ROUNDS,
)
from execution.hitl.factory import BoundHITLServiceProxy, create_hitl_service

hitl_service = BoundHITLServiceProxy()

def bind_hitl_service(service: HITLService) -> None:
    hitl_service.bind(service)

def require_hitl_service() -> HITLService:
    return hitl_service._require_service()

__all__ = [
    "ContinuationLostError",
    "HITLContinuationLostError",
    "HITLService",
    "MAX_HITL_ROUNDS",
    "bind_hitl_service",
    "create_hitl_service",
    "hitl_service",
    "require_hitl_service",
]
```

Keep `ContinuationLostError` as a deliberate legacy alias for
`HITLContinuationLostError` so existing imports continue to work while new
Execution-owned code uses the namespaced exception.

Do not leave `hitl_service = HITLService()` anywhere after this task. The
singleton-shaped export is a bound proxy so existing imports, including
`jobs/stale_task_checker.py`, still resolve while startup owns the real
constructor arguments.

- [ ] **Step 5: Bind HITL proxy for startup and tests**

Update `main.py` / container startup and the relevant test fixtures before
running the Task 5 tests. Define the string-state notification wrapper in this
same step if it does not already exist:

```python
async def notify_task_update_with_string_state(
    message_id: str,
    state: str,
    *,
    room_id: str,
    user_id: str,
    error: str | None = None,
    parts: list[dict] | None = None,
) -> bool:
    state_map = {
        "completed": TaskState.completed,
        "failed": TaskState.failed,
        "canceled": TaskState.canceled,
        "rejected": TaskState.rejected,
        "input-required": TaskState.input_required,
        "auth-required": TaskState.auth_required,
    }
    if state not in state_map:
        raise ValueError(f"Unsupported task notification state: {state}")
    return await notify_task_update(
        message_id=message_id,
        state=state_map[state],
        room_id=room_id,
        user_id=user_id,
        error=error,
        parts=parts,
    )

# Use the current A2A wire values ("input-required" and "auth-required"), not
# underscore variants. Forward error/parts because AgentTaskNotificationPort and
# the current notification service both support those fields.

hitl_runtime = create_hitl_service(
    persistence=DatabaseHITLPersistenceAdapter(database_service=db_service),
    delivery=LegacyHITLDeliveryAdapter(sse_manager=sse_manager),
    continuation=A2AHITLContinuationAdapter(
        a2a_service=a2a_service,
        room_message_center_provider=modules_room_message_center.require_room_message_center,
    ),
    task_notifier=HITLTaskNotificationAdapter(
        notify_task_update=notify_task_update_with_string_state,
    ),
)
services_hitl_service.bind_hitl_service(hitl_runtime)
```

If a Task 5 test starts the HITL API without full `main.py` startup, its fixture
must bind a fake or adapter-backed `hitl_runtime` explicitly. Do not let tests
pass by relying on a no-argument singleton. Task 5 owns HITL runtime
construction; later tasks must reuse `services.hitl_service.require_hitl_service()`
and must not recreate the HITL graph. Update `tests/test_service_hitl.py` and
`tests/test_flow_contracts.py` fixtures that currently instantiate
`HITLService()` directly so they pass fake ports into the constructor or use
`create_hitl_service(...)`. Also update `tests/test_scope_validation.py`
fixtures that patch the old HITL singleton path so they bind the new
`RoomServices` HITL pending checker or the `services.hitl_service` proxy
explicitly.

- [ ] **Step 6: Run HITL tests**

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_service_hitl.py tests/test_flow_contracts.py tests/test_scope_validation.py tests/test_api_hitl.py tests/test_api_integration.py::TestHITLHTTPIntegration tests/test_module_supervisor_executor.py
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add execution/hitl services/hitl_service.py services/room_services.py api/hitl.py container.py main.py pyproject.toml tests/test_service_hitl.py tests/test_flow_contracts.py tests/test_scope_validation.py tests/test_api_hitl.py tests/test_api_integration.py tests/test_module_supervisor_executor.py
git commit -m "refactor: move hitl runtime into execution module"
```

### Task 6: Add ExecutionFacade And Container Wiring

**Files:**
- Create: `execution/facade.py`
- Create: `execution/cancellation.py`
- Create or modify: `execution/translators.py`
- Modify: `execution/ports.py` if `AgentResponseHandlerPort` was not already added in Task 3
- Modify: `execution/__init__.py`
- Modify: `common/observability/tracing.py` if `traced_create_task()` is not already available.
- Modify: `container.py`
- Modify: `tests/test_execution_facade.py`
- Modify: `tests/test_execution_protocols.py`

- [ ] **Step 1: Write failing facade tests**

Add tests for protocol behavior:
- `execute()` persists a user message through the existing Room send-message path and returns the ack without tracking or spawning orchestration.
- `execute()` builds `RoomCenterUserMessageRequest.user_id` from
  `ExecutionRequest.sender_id`, not from payload data.
- `execute()` passes `ExecutionRequest.target_group` through unchanged to
  `room_center.send_message_to_room(...)`, including explicit `None` and
  explicit `""`; route-level migration tests own the default-only-when-missing
  behavior.
- `start_orchestration()` builds the `OrchestrationRequest` with
  `user_id=request.sender_id` and
  `client_request_id=request.client_request_id`.
- `schedule_recovery_orchestration(request, reason=...)` schedules stale-task
  orphan/supervisor recovery work through the same tracked task path. This is an
  app-shell helper for `jobs/stale_task_checker.py`, not part of the public
  `ExecutionEngine` protocol.
- `execute()` does not start orchestration before returning the ack.
  `/roomCenter/sendMessage` must continue to schedule
  `start_orchestration(request, ack)` through FastAPI `BackgroundTasks`, and the
  facade should only call and await `_spawn_orchestration()` from that
  after-response background callback.
- `cancel(..., requested_by_user_id=...)` calls the cancellation broadcaster first, cancels HITL requests for the message, persists the cancelling user id, clears cancellation state on persistence failure, records/emits canceled processing status, and delegates best-effort agent task cleanup through injected ports.
- `AgentTaskCleanupAdapter.cleanup_cancelled_message_tasks(...)` preserves the
  current best-effort cleanup behavior: query agent messages by related root
  message id, skip non-tracked messages, update each tracked DB task state to
  `"canceled"` with `"Task was canceled"`, notify the frontend with domain state
  `"canceled"`, look up the agent card for messages with `agent_url`, and
  best-effort cancel the remote task id without failing root cancellation.
- `_spawn_orchestration()` uses the injected task factory, whose default is `traced_create_task`, not bare `asyncio.create_task`.
- `_spawn_orchestration()` done callback handles canceled tasks without calling
  `task.exception()`; cancellation should remove the task from `_inflight`
  without raising from the callback during shutdown.
- `get_run()` / `get_runs_for_room()` delegate to `RunReadPort` and preserve `trigger_message_id`.
- `cancel_inflight_tasks()` cancels tracked tasks, awaits them with
  `asyncio.gather(..., return_exceptions=True)`, then returns the count. Do not
  return immediately after `task.cancel()`: shutdown must give orchestration
  cleanup/finalizers a chance to run. Snapshot `tasks = set(self._inflight)`
  before cancel/gather so done callbacks can mutate `_inflight` without racing
  iteration.
- `heal_diverged_runs()` delegates to `RunLifecyclePort`.
- `create_hitl_request()` delegates to the HITL runtime and preserves
  `room_id`, `user_message_id`, `prompt`, `source`, source step, agent/task/context ids,
  continuation/display message ids, prompt type, choices, and group metadata.
- `resolve_hitl(room_id, ...)` delegates to HITL route handling, preserves the
  request/room mismatch guard, and translates the current raw
  `{"status": "ok", "request_id": ...}` dict into `common.dto.HITLResponse`
  before returning through the public protocol.
- `get_pending_hitl(room_id)` delegates to the HITL runtime, translates current
  model-backed pending requests to Common `HITLRequest`, and preserves the
  computed `message_id` plus the current `{"requests": [...]}` route wrapper
  through the API translator in Task 7.
- `cancel_hitl(room_id, request_id)` delegates to the HITL runtime and
  translates the current `cancel_request(...) -> None` success/no-op return
  into `True`; HITL not-found, room-mismatch, and conflict cases still surface
  through Execution-owned exceptions for API translation.
- canceled status emission calls `emit_processing_status(..., run_event_enabled=self._run_event_enabled, client_request_id_resolver=self._client_request_id_resolver)` rather than importing the flag or skipping client-request-id fallback.
- `room_response_to_execution_ack()` preserves current send-message error
  response shape; a missing-message response remains `message_id=None` and
  `message=None`, not `""` and `{}`.

- [ ] **Step 2: Implement translation helpers**

Create translation helpers, keeping legacy models at the edges:

```python
def room_response_to_execution_ack(response: RoomCenterUserMessageResponse) -> ExecutionAck:
    return ExecutionAck(
        room_id=response.room_id,
        message_id=response.message_id,
        dispatch_root_message_id=response.dispatch_root_message_id,
        user_id=response.user_id,
        user_name=response.user_name,
        message=response.message.model_dump(mode="json") if response.message else None,
        message_list=(
            [m.model_dump(mode="json") for m in response.message_list]
            if response.message_list is not None
            else None
        ),
        scope_resolution_error=response.scope_resolution_error.model_dump(mode="json") if response.scope_resolution_error else None,
        success=response.success,
        error=response.error,
        status_code=response.status_code,
    )
```

Also import/use the HITL translators from `execution.hitl.translators` here or
next to the facade methods. Add focused tests that:
- raw `{"status": "ok", "request_id": "req-1"}` from the moved HITL runtime
  becomes `HITLResponse(status="ok", request_id="req-1")`;
- optional `reclaimed`, `error`, `response_text`, `responder_id`, and
  `resolved_at` fields pass through when present;
- `cancel_request(...) -> None` maps to public `cancel_hitl(...) is True`.

- [ ] **Step 3: Implement `ExecutionFacade`**

Constructor dependencies:

```python
class ExecutionFacade:
    def __init__(
        self,
        *,
        room_center,
        room_message_center,
        hitl_service,
        run_lifecycle: RunLifecyclePort,
        run_reader: RunReadPort,
        cancellation_state: CancellationStatePort,
        cancellation_store: CancellationStorePort,
        hitl_message_cancellation: HITLMessageCancellationPort,
        agent_task_cleanup: AgentTaskCleanupPort,
        agent_response_handler: AgentResponseHandlerPort,
        event_publisher: EventPublisher,
        legacy_processing_status_publisher: LegacyProcessingStatusPublisher,
        run_event_enabled: RunEventEnabled,
        client_request_id_resolver: ClientRequestIdResolver,
        task_factory: TaskFactory = traced_create_task,
    ) -> None:
        self._room_center = room_center
        self._room_message_center = room_message_center
        self._hitl_service = hitl_service
        self._run_lifecycle = run_lifecycle
        self._run_reader = run_reader
        self._cancellation_state = cancellation_state
        self._cancellation_store = cancellation_store
        self._hitl_message_cancellation = hitl_message_cancellation
        self._agent_task_cleanup = agent_task_cleanup
        self._agent_response_handler = agent_response_handler
        self._event_publisher = event_publisher
        self._legacy_processing_status_publisher = legacy_processing_status_publisher
        self._run_event_enabled = run_event_enabled
        self._client_request_id_resolver = client_request_id_resolver
        self._task_factory = task_factory
        self._inflight: set[asyncio.Task] = set()
```

If `common.observability.tracing.traced_create_task()` does not exist in the
implementation branch, add it before wiring the facade. Phase 7b must keep this
helper dependency-free; do not add OpenTelemetry just for task creation. The
initial helper only preserves `contextvars` context and task naming:

```python
import asyncio
import contextvars
from collections.abc import Awaitable
from typing import TypeVar

T = TypeVar("T")

def traced_create_task(coro: Awaitable[T], *, name: str | None = None) -> asyncio.Task[T]:
    ctx = contextvars.copy_context()

    async def _runner() -> T:
        return await coro

    return asyncio.create_task(_runner(), name=name, context=ctx)
```

Future OpenTelemetry support may wrap spans behind the same helper, but that is
outside the no-new-dependencies Phase 7b scope. `_spawn_orchestration()` must
call the factory with a stable task name, for example:

```python
task = self._task_factory(coro, name=f"execution-orchestrate-{message_id}")
```

Do not use bare `asyncio.create_task` inside `ExecutionFacade`; the design
requires background orchestration tasks to preserve context through the helper.

`execute()` should:
- validate `client_request_id` at the API layer before calling the facade;
- build `RoomCenterUserMessageRequest` with the authenticated identity pinned:
  `room_id=request.room_id`, `user_id=request.sender_id`,
  `message=request.message`, `attachments=request.attachments`,
  `inline_file_ids=request.inline_file_ids`, and
  `client_request_id=request.client_request_id`. Do not derive `user_id` from
  message payloads or `sender_name`; current RoomServices uses
  `RoomCenterUserMessageRequest.user_id` for canonical mention validation,
  scope resolution, and all-agent filtering.
- call the existing
  `room_center.send_message_to_room(room_center_request, request.target_group, request.mentioned_agent_ids)`
  so target-group resolution and mentions preserve current route behavior. The
  route, not the facade, owns legacy target-group defaulting: when the
  `target_group` key is absent it should populate `"room_team"` before building
  `ExecutionRequest`; when the key is present with `null` or an empty string,
  that value must pass through unchanged. Do not default `None` or use a
  truthiness fallback inside `ExecutionFacade.execute()`.
- if successful, return `ExecutionAck` without spawning orchestration yet.

`start_orchestration(request, ack)` should:
- no-op when `ack.success` is false or `ack.message_id` is missing;
- build `OrchestrationRequest` with `room_id=request.room_id`,
  `room_user_message_id=ack.message_id`, `user_id=request.sender_id`,
  `client_request_id=request.client_request_id`, and
  `room_related_message_id` from `ExecutionRequest.parent_message_id`;
- call `_spawn_orchestration(room_message_center.process_room_user_message(...))`.

This split preserves the current FastAPI `BackgroundTasks` behavior: the route
hands off the HTTP response first, then invokes the Execution-owned background
starter. `start_orchestration()` must await the tracked task it creates, so the
FastAPI background task's lifetime and exception observation still correspond to
the full orchestration coroutine. `_spawn_orchestration()` supplies tracking,
done-callback cleanup, and exception logging; it must not be used here as a
fire-and-forget detach that lets the background callback return immediately.

`schedule_recovery_orchestration(request, *, reason)` should:
- accept an existing `OrchestrationRequest` from stale-task orphan/supervisor
  recovery;
- call `_spawn_orchestration(room_message_center.process_room_user_message(request))`;
- return the tracked `asyncio.Task` so `StaleTaskChecker` can release its
  recovery semaphore from the task done callback;
- use a stable task name such as
  `execution-recovery-{reason}-{request.room_user_message_id}`.

Do not add this helper to `common.protocols.ExecutionEngine`; it is app-shell
wiring for the current stale-recovery job. If a future phase needs public
recovery scheduling, introduce a Common protocol deliberately.

`_spawn_orchestration()` must register a done callback that removes the task from
`_inflight` and logs exceptions. The callback must check `task.cancelled()`
before calling `task.exception()`:

```python
def _on_done(task: asyncio.Task) -> None:
    self._inflight.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "execution orchestration task failed",
            exc_info=(type(exc), exc, exc.__traceback__),
        )
```

`get_run()` and `get_runs_for_room()` must delegate to `self._run_reader`; do
not reach into Mongo directly from the facade.

`cancel()` should preserve the current `api/sse.py` ordering and best-effort
semantics:
1. `cancellation_state.cancel_message_and_broadcast(message_id)`.
2. `hitl_message_cancellation.cancel_requests_for_message(message_id)`.
3. `cancellation_store.cancel_message(message_id, requested_by_user_id)`.
4. On persistence failure, `cancellation_state.clear_cancellation(message_id)`
   and return `False`.
5. Emit the canceled processing status via `emit_processing_status(...)`, passing
   `run_event_enabled=self._run_event_enabled`.
6. Await `agent_task_cleanup.cleanup_cancelled_message_tasks(...)` inside a
   best-effort `try`/log block so remote cleanup failure does not fail root
   cancellation.

Implement `AgentTaskCleanupAdapter` in `execution/cancellation.py` with injected
dependencies only:

```python
class CancellationStateC3Adapter:
    def __init__(self, sse_manager) -> None:
        self._sse_manager = sse_manager

    async def cancel_message_and_broadcast(self, message_id: str) -> None:
        await self._sse_manager.cancel_message_and_broadcast(message_id)

    def clear_cancellation(self, message_id: str) -> None:
        self._sse_manager.clear_cancellation(message_id)


class MongoCancellationStoreAdapter:
    def __init__(self, mongodb) -> None:
        self._mongodb = mongodb

    async def cancel_message(
        self,
        message_id: str,
        requested_by_user_id: str,
    ) -> bool:
        return await self._mongodb.cancel_message(message_id, requested_by_user_id)


class HITLMessageCancellationAdapter:
    def __init__(self, hitl_service) -> None:
        self._hitl_service = hitl_service

    async def cancel_requests_for_message(self, message_id: str) -> None:
        await self._hitl_service.cancel_requests_for_message(message_id)


class AgentTaskCleanupAdapter:
    def __init__(
        self,
        *,
        db_service,
        get_agent_card_from_url,
        cancel_remote_task,
        notify_task_update: Callable[..., Awaitable[bool]],
    ) -> None: ...

    async def cleanup_cancelled_message_tasks(
        self,
        *,
        room_id: str,
        message_id: str,
    ) -> None:
        agent_msgs = await self._db.get_room_agent_messages_by_related_message_id(message_id)
        for agent_msg in agent_msgs:
            if not agent_msg.has_task_tracking:
                continue
            await self._db.update_task_state_on_message(
                agent_msg.message_id,
                "canceled",
                message_text="Task was canceled",
            )
            await self._notify_task_update(
                message_id=agent_msg.message_id,
                state="canceled",
                room_id=agent_msg.room_id,
                user_id=agent_msg.user_id or "",
            )
            task = agent_msg.message_content.message_task if agent_msg.message_content else None
            if agent_msg.agent_url and task and task.id:
                try:
                    agent_card = await self._get_agent_card_from_url(agent_msg.agent_url)
                    await self._cancel_remote_task(agent_card, task.id)
                except Exception:
                    logger.debug("remote cancellation failed", exc_info=True)
```

Reuse the `AgentTaskNotificationAdapter` created in Task 4 from
`execution.dispatch.task_notifications`; do not create a second cancellation-local
task-notification adapter. The cancellation adapter must not import
`a2a_service`, `TaskState`, or notification singletons. The app shell supplies
callables that perform those conversions.

- [ ] **Step 4: Add container deps**

Add:

```python
@dataclass(frozen=True)
class ExecutionDeps:
    execution_engine: ExecutionEngine
    hitl_manager: HITLManager
```

Add `create_execution_facade(...)` and `create_execution_deps(facade)`.
`create_execution_facade(...)` must accept
`run_reader`, cancellation ports, and
`legacy_processing_status_publisher: LegacyProcessingStatusPublisher` and
`run_event_enabled: RunEventEnabled`, and
`client_request_id_resolver: ClientRequestIdResolver` explicitly and pass them to
`ExecutionFacade`; it must not read `DeliveryFacade.compat` from `DeliveryDeps`,
import `run_event_sse_enabled`, or read concrete globals.

- [ ] **Step 5: Enable runtime protocol tests**

Assert:

```python
assert isinstance(facade, ExecutionEngine)
assert isinstance(facade, HITLManager)
```

Do not expose `HubAgentResponseSink` from `ExecutionDeps` and do not assert
`isinstance(facade, HubAgentResponseSink)` in Task 6. The facade does not satisfy
that public protocol until Task 11 specifies and tests
`handle_hub_agent_response(...)`. Task 6 must satisfy only
`ExecutionEngine` and `HITLManager`, including `get_pending_hitl(...)` because
Task 7 routes `/hitl/pending` through that method.

- [ ] **Step 6: Run tests**

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_execution_facade.py tests/test_execution_protocols.py tests/test_common_foundation.py
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add execution/facade.py execution/cancellation.py execution/ports.py execution/run_queries.py execution/translators.py execution/__init__.py common/observability/tracing.py container.py tests/test_execution_facade.py tests/test_execution_protocols.py tests/test_common_foundation.py
git commit -m "feat: add execution facade and deps"
```

### Task 7: Migrate API Routes To Execution Protocols

**Files:**
- Modify: `api/room_center.py`
- Modify: `api/hitl.py`
- Modify: `api/sse.py`
- Modify: `services/room_services.py`
- Modify: `main.py`
- Modify: `tests/test_api_room_center.py`
- Modify: `tests/test_api_hitl.py`
- Modify: `tests/test_api_sse.py`

- [ ] **Step 1: Write failing route tests**

For `api/room_center.py`, prove `/roomCenter/sendMessage` calls the injected
`ExecutionEngine.execute()` and returns the same response shape as before, but
only after the route performs `await verify_room_ownership(room_id, user)`.
Pin the validation order for `client_request_id`: missing or blank
`client_request_id` must return the current `"client_request_id is required"`
response before room ownership lookup, matching today's route. Add a
missing/blank `client_request_id` test on an unauthorized room and assert the
ownership verifier and injected Execution engine are not called.
Add an unauthorized-room test proving the route rejects before constructing
`ExecutionRequest` or calling `ExecutionEngine.execute()`. Do the same for
`/roomCenter/inquiryActiveRuns`: it must verify room ownership before calling
`ExecutionEngine.get_runs_for_room(room_id)`.
Also add a sendMessage authorization-order test with inline/top-level
attachments that would otherwise be invalid or over the attachment limit. The
test should prove unauthorized requests reject before `_extract_attachments(...)`
runs or mutates `message["message_content"]["attachments"]` with `pop()`, and
that the injected Execution engine is not called.

Add shadow/diff tests for `/roomCenter/sendMessage`, HITL respond, and HITL
cancel. The tests must compare legacy route output and protocol-route output
using isolated fakes or transaction-scoped test data so no request is
double-persisted in production code.

For `api/hitl.py`, prove respond/pending/cancel calls `HITLManager` instead of importing `services.hitl_service`, and that respond/cancel pass the `room_id` path parameter into the protocol method.

For `api/sse.py`, prove message cancellation calls
`ExecutionEngine.cancel(..., requested_by_user_id=user.user_id)` after ownership
verification, and stream/status remain Delivery-owned.

- [ ] **Step 2: Add route-level binding helpers**

Use a module-level dependency object or setter consistent with existing facade binding style:

```python
execution_engine: ExecutionEngine | None = None
hitl_manager: HITLManager | None = None

def bind_execution_deps(deps: ExecutionDeps) -> None:
    global execution_engine, hitl_manager
    execution_engine = deps.execution_engine
    hitl_manager = deps.hitl_manager
```

Fail fast if startup did not bind dependencies.

- [ ] **Step 3: Add shadow/diff gate before cutover**

Add a route-level feature flag or test-only harness that runs the legacy
translation and the Execution protocol translation against the same fixtures and
diffs:
- response JSON shape for `/roomCenter/sendMessage`;
- `/roomCenter/sendMessage` target-group resolution for key-absent, explicit
  `null`, explicit `""`, saved-group, room-default payloads, and explicit
  `message_target_mode=""`;
- `/roomCenter/sendMessage` mixed-payload rejection when
  `mentioned_agent_ids` is present together with any
  `message_target_mode`, preserving the current route-level
  `"Cannot specify both mentioned_agent_ids and message_target_mode"` response;
- pending/response/cancel HITL JSON shapes;
- active-run payloads, including `trigger_message_id`;
- cancellation response shape and side-effect call order.

Do not direct-cut over `/roomCenter/sendMessage`, HITL respond/cancel, or
cancellation until this shadow gate is green. For mutating endpoints, shadow
mode must not double-write production data; use isolated fakes, rollback-scoped
test transactions, or a dry-run comparison adapter.

- [ ] **Step 4: Migrate `/roomCenter/sendMessage`**

Keep request validation in the route, then call:

```python
client_request_id = request_data.get("client_request_id")
if not isinstance(client_request_id, str) or not client_request_id.strip():
    return RoomCenterUserMessageResponse(
        message_id=None,
        message=None,
        success=False,
        error="client_request_id is required",
        status_code=400,
    )

related_message_id = ""
if isinstance(message, dict):
    related_message_id = message.get("related_message_id") or ""

# Preserve current route behavior: default only when target_group is absent.
# Explicit null and explicit "" must pass through to RoomServices unchanged.
if message_target_mode is not None:
    # Keep the current saved-group / room-default / mode mapping branch.
    ...
else:
    target_group = request_data.get("target_group", "room_team")

await verify_room_ownership(room_id, user)

attachments, inline_file_ids, err = _extract_attachments(request_data, message)
if err is not None:
    return err

execution_request = ExecutionRequest(
    room_id=room_id,
    sender_id=user.user_id,
    sender_name=user.username or user.email,
    message=message,
    attachments=attachments,
    inline_file_ids=inline_file_ids,
    client_request_id=client_request_id,
    target_group=target_group,
    target_group_id=target_group_id,
    message_target_mode=message_target_mode,
    mentioned_agent_ids=mentioned_agent_ids,
    parent_message_id=related_message_id or None,
)
ack = await _require_execution_engine().execute(execution_request)
if ack.success and ack.message_id:
    background_tasks.add_task(
        _require_execution_engine().start_orchestration,
        execution_request,
        ack,
    )
return RoomCenterUserMessageResponse(**ack.model_dump())
```

Preserve `target_group` resolution, attachment extraction, and quoted-message
propagation exactly. Current `/roomCenter/sendMessage` defaults `target_group`
only when the key is absent; explicit `null` and explicit `""` flow through to
RoomServices. Current `/roomCenter/sendMessage` also copies
`message["related_message_id"]` into `OrchestrationRequest.room_related_message_id`;
the protocol path must carry the same value through
`ExecutionRequest.parent_message_id`. Do not coerce a missing request `message`
to `{}`; pass `message` through unchanged so RoomServices keeps returning the
current `"Message is required"` response for missing messages.
Preserve the route-side ordering as well: compute target group, verify room
ownership, then call `_extract_attachments(request_data, message)`, then build
`ExecutionRequest`. `_extract_attachments()` mutates inline attachment data with
`pop()`, so do not move it before authorization or hide it inside the facade.
Preserve the earlier `client_request_id` validation ordering too: missing/blank
`client_request_id` returns before ownership verification, target-group work
that can require room data, attachment extraction, or Execution calls.
Preserve after-response scheduling: do not call
`start_orchestration(request, ack)` inline before returning the ack. The migrated
route must keep using FastAPI `BackgroundTasks` so orchestration SSE/task side
effects cannot race ahead of the HTTP response differently from today's route.

- [ ] **Step 5: Migrate active run lookup**

Use `execution_engine.get_runs_for_room(room_id)` and translate `RunInfo` into
the existing `RoomCenterActiveRunsResponse` / `ActiveRunRef` shape, preserving
`trigger_message_id`.
Keep the current authorization boundary in the route:
`await verify_room_ownership(room_id, user)` must run before
`get_runs_for_room(...)`. Add a route test that an unauthorized room returns the
current auth failure and does not call the injected Execution engine.

Also migrate the embedded active-run list returned by
`/roomCenter/inquiryRoomSetting`. Current `RoomServices.inquiry_room_setting()`
calls `database_service.get_active_runs_by_room_id(...)` directly when
assembling `RoomCenterRoomSettingResponse.active_runs`, so Phase 7b must not
leave run-read ownership split between RoomServices and Execution. Add a
RoomServices binder that accepts a pre-bound app-shell callable, for example:

```python
def bind_active_run_reader(
    reader: Callable[[str], Awaitable[list[dict[str, Any]]]],
) -> None: ...
```

The app shell should bind a wrapper over
`ExecutionEngine.get_runs_for_room(room_id)` plus the existing `RunInfo` ->
`ActiveRunRef`/dict translator. `RoomServices` must use that callable for both
`inquiry_room_setting()` and the legacy `inquiry_active_runs()` implementation
until the route has fully moved to Execution; it must not import
`execution.facade`, `execution.ports`, or reach into Mongo for run reads. Tests
must cover:
- `/roomCenter/inquiryRoomSetting` still verifies room ownership in the route
  before RoomServices work;
- the room-setting response includes `active_runs` from the injected Execution
  reader;
- reader exceptions preserve current public behavior by logging and returning an
  empty active-run list rather than failing the whole room-setting response;
- `database_service.get_active_runs_by_room_id(...)` is no longer called from
  RoomServices active-run paths after the binder is in place.

- [ ] **Step 6: Migrate HITL routes**

Route:
- `GET /rooms/{room_id}/hitl/pending` -> `HITLManager.get_pending_hitl(room_id)`
- `POST /rooms/{room_id}/hitl/respond` -> `HITLManager.resolve_hitl(room_id, request_id, ...)`
- `POST /rooms/{room_id}/hitl/{request_id}/cancel` -> `HITLManager.cancel_hitl(room_id, request_id)`

Keep room ownership verification in the API layer and keep the service-level
request/room mismatch guard behind the protocol.
Task 7 assumes Task 6 has already translated HITL runtime returns to public
protocol values: `resolve_hitl(...)` returns `HITLResponse`, and
`cancel_hitl(...)` returns `True` for the current runtime's `None` success/no-op
return. Do not let API routes inspect raw HITL service dicts or `None` returns
directly.
For respond, translate the returned `HITLResponse` back to the current route dict
shape, including at minimum
`{"status": response.status, "request_id": response.request_id}` and `reclaimed`
when present; do not expose a new response shape during Phase 7b unless a
frontend migration is coordinated. For cancel, the public protocol returns
`bool` and the current API returns `{"status": "canceled"}`; keep that exact
route shape when `cancel_hitl(...)` succeeds. If `cancel_hitl(...)` returns
`False`, map it to the current failure semantics instead of trying to synthesize
a `HITLResponse`.
For pending, keep the current wrapper shape exactly: `{"requests": [...]}`.
Add an API test asserting the wrapper key, that each request is translated from
the moved runtime model to the public/common shape, and that `message_id` uses
the current fallback `display_message_id or continuation_message_id or
user_message_id`.
Also translate Execution-owned HITL exceptions back to the current HTTP
semantics: `HITLNotFoundError` -> 404, `HITLConflictError` -> 409,
`HITLRoomMismatchError` -> 403, `HITLContinuationLostError` -> 410, and
`HITLRoutingFailedError` -> 502. The moved HITL service must not raise
`fastapi.HTTPException` directly.

- [ ] **Step 7: Migrate cancel route**

`api/sse.py` should call:

```python
success = await _require_execution_engine().cancel(
    room_id=message.room_id,
    message_id=message_id,
    requested_by_user_id=user.user_id,
)
if not success:
    raise HTTPException(
        status_code=500,
        detail="Failed to persist cancellation to database",
    )
```

The API layer still performs room ownership verification before calling
Execution. The facade owns cancellation token propagation, HITL cancellation,
`mongodb.cancel_message(message_id, requested_by_user_id)` audit persistence,
run lifecycle recording, and typed/compat status emit dependencies.

- [ ] **Step 8: Wire in `main.py`**

Before `main.py` calls `room_services.bind_execution_event_deps(...)`, add that
minimal binder to `services/room_services.py` in this task. Task 7 only stores
the injected event dependencies and keeps current sendMessage behavior intact;
Task 8 later uses the binder to replace the direct lifecycle/SSE pairs. Do not
wire a binder in startup before the function exists.

Extend the app-shell wiring created by Tasks 3-5. Do not construct a second HITL
or `RoomMessageCenter` graph in Task 7. Reuse the bound runtimes:

```python
room_message_center_runtime = modules_room_message_center.require_room_message_center()
hitl_runtime = services_hitl_service.require_hitl_service()
client_request_id_resolver = app_shell_client_request_id_resolver  # same resolver created for RoomMessageCenterDeps in Task 4
agent_response_handler = room_message_center_runtime.agent_response_handler
room_services.bind_hitl_pending_checker(hitl_runtime.get_pending_requests)

execution_facade = create_execution_facade(
    room_center=room_center.room_center,
    room_message_center=room_message_center_runtime,
    hitl_service=hitl_runtime,
    run_lifecycle=run_lifecycle,
    run_reader=RunQueryAdapter(runs_collection=mongodb.runs_collection),
    cancellation_state=CancellationStateC3Adapter(sse_manager=sse_manager),
    cancellation_store=MongoCancellationStoreAdapter(mongodb=mongodb),
    hitl_message_cancellation=HITLMessageCancellationAdapter(hitl_service=hitl_runtime),
    agent_task_cleanup=AgentTaskCleanupAdapter(
        db_service=db_service,
        get_agent_card_from_url=a2a_service.get_agent_card_from_url,
        cancel_remote_task=a2a_service.cancel_remote_task,
        notify_task_update=notify_task_update_with_string_state,
    ),
    agent_response_handler=agent_response_handler,
    event_publisher=_delivery_deps.event_publisher,
    legacy_processing_status_publisher=legacy_processing_status_publisher,
    run_event_enabled=run_event_sse_enabled,
    client_request_id_resolver=client_request_id_resolver,
)
_execution_deps = create_execution_deps(execution_facade)

async def read_room_active_runs(room_id: str) -> list[dict[str, Any]]:
    runs = await execution_facade.get_runs_for_room(room_id)
    return [run_info_to_active_run_payload(run) for run in runs]

room_services.bind_active_run_reader(read_room_active_runs)

async def emit_room_processing_status(**kwargs):
    return await emit_processing_status(
        **kwargs,
        run_lifecycle=run_lifecycle,
        event_publisher=_delivery_deps.event_publisher,
        legacy_processing_status_publisher=legacy_processing_status_publisher,
        run_event_enabled=run_event_sse_enabled,
        client_request_id_resolver=client_request_id_resolver,
    )

room_services.bind_execution_event_deps(
    processing_status_emitter=emit_room_processing_status,
)
room_center.bind_execution_deps(_execution_deps)
hitl.bind_execution_deps(_execution_deps)
sse.bind_execution_deps(_execution_deps)
```

`emit_room_processing_status`, `run_lifecycle`,
`legacy_processing_status_publisher`, `notify_task_update_with_string_state`,
`run_info_to_active_run_payload`, and `run_event_sse_enabled` are the same
app-shell dependencies created and bound by Tasks 3-5. `services.room_services`
stores the active-run reader and emitter callables but does not import
`execution.events`, `execution.facade`, or private Execution ports. If
implementation chooses to centralize those variables in `container.py`, Task 7
should import them through that container helper; it must not reconstruct HITL
or RoomMessageCenter locally.

`sse_manager` here is the already-bound Phase 6 C3 adapter from
`services.sse_services`, not a concrete Delivery object. This keeps the
unsupported-status compatibility path explicit while preserving the DeliveryDeps
contract.
`run_event_sse_enabled` is read only in the app shell and passed as a
`RunEventEnabled` dependency; no `execution/**` file imports it from
`services.run_command_handler`.
`services_hitl_service` is the legacy shim module from `services/hitl_service.py`;
startup must bind its proxy to the real port-injected `hitl_runtime` before
`jobs/stale_task_checker.py` or legacy imports call methods such as
`recover_stale_processing()`. The task cleanup callables must convert string
domain states to `TaskState` outside `execution/**`.

- [ ] **Step 9: Run API tests**

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_api_room_center.py tests/test_api_hitl.py tests/test_api_sse.py tests/test_api_integration.py
```

Expected: pass.

- [ ] **Step 10: Commit**

```bash
git add api/room_center.py api/hitl.py api/sse.py services/room_services.py main.py tests/test_api_room_center.py tests/test_api_hitl.py tests/test_api_sse.py tests/test_api_integration.py
git commit -m "refactor: route execution APIs through execution protocols"
```

### Task 8: Replace Execution-Owned Processing Status Emits With EventPublisher

**Files:**
- Modify: `execution/orchestration/room_message_center.py`
- Modify: `execution/orchestration/factory.py`
- Modify: `execution/orchestration/queue_executor.py`
- Modify: `execution/orchestration/supervisor_executor.py`
- Modify: `execution/dispatch/response_handler.py`
- Modify: `execution/dispatch/transports/relay.py`
- Modify: `jobs/stale_task_checker.py`
- Modify: `services/room_services.py` only for sendMessage lifecycle sends still owned by Execution
- Modify: `api/sse.py` if cancellation send remains there after Task 7
- Modify: `main.py`
- Modify: `container.py` only if stale-checker wiring lives there
- Create: `tests/fixtures/phase7_execution_event_callers.json`
- Modify: `tests/test_phase7_execution_event_gate.py`
- Modify: `tests/test_stale_task_checker_run_lifecycle.py`
- Create or modify: `tests/test_stale_task_checker_recovery.py`
- Modify: existing Phase 7a static/golden tests that still inspect legacy
  `modules/*` call sites directly

- [ ] **Step 1: Write the typed event AST gate**

The gate must:
- Parse `execution/**/*.py`, `api/sse.py`, and all remaining Execution-owned
  `services/room_services.py` sendMessage lifecycle/status paths, plus
  `jobs/stale_task_checker.py` watchdog recovery paths that emit run events or
  processing status.
- Fail on `record_and_maybe_broadcast_run_event`.
- Fail on `broadcast_run_event_payload` and direct `services.run_lifecycle_service`
  imports in `jobs/stale_task_checker.py` and other Execution-owned migration
  paths; the watchdog must use the injected lifecycle/event ports after this
  task.
- Fail on `send_processing_status(` in Execution-owned files except the exact
  allowlisted adapter path `execution/legacy_processing_status.py`.
- Load `tests/fixtures/phase7_execution_event_callers.json`.
- For every `ProcessingStatusEvent` emit, assert a preceding awaited
  `emit_processing_status(...)` or an explicitly manifest-covered transport-only
  exception. The helper implementation in `execution/events.py` is the only
  allowlisted direct `ProcessingStatusEvent(...)` production construction site.
- For every compatibility processing-status frame, assert the manifest records
  `recording_kind: "legacy_processing_status_compat"` and a
  `legacy_status_reason` explaining why the status is not representable by
  `ProcessingStatusEvent` or a `legacy_detail_shape_reason` explaining why a
  representable status still needs the compatibility frame to preserve legacy
  frontend details shape.
- Assert `execution/**` does not import `services.sse_services` or concrete `delivery.*`.
- Assert `delivery/**` does not import `execution`, `modules`, `services`, or `run_command_handler`.
- Assert `services/room_services.py` does not import `execution.events`; it must
  use the app-shell injected processing-status emitter callable from
  `bind_execution_event_deps(...)`.

Update the old Phase 7a processing-status gate in the same task. After Task 4,
legacy `modules/*` files are shims, so the gate must stop requiring every
Phase 7a fixture entry to correspond to a production `send_processing_status`
call in the old module path. It should either read
`tests/fixtures/phase7_execution_event_callers.json` or map old fixture entries
to their moved `execution/**` call sites, while allowing shim-only legacy files.

`jobs/stale_task_checker.py` is not an Execution module, but it currently emits
Execution-owned lifecycle/status events. Do not migrate its timeout path through
generic `record_processing_status("failed")`: the current behavior uses
`append_run_timeout_failure(...)` and broadcasts that exact run event. Add a
watchdog-specific helper/adapter path exposed to the job as pre-bound callables:
call `deps.append_run_timeout_failure(...)`, then `deps.emit_run_event(...)` for
the returned raw lifecycle payload, then `deps.emit_legacy_processing_status(...)`
for the failed frontend status with the existing details text. The app-shell
`emit_run_event` wrapper is responsible for converting the raw payload to
`RunEventNotification` with `run_event_notification_from_payload(room_id=...,
payload=..., correlation_id=...)`. Cover this path in the manifest and final
static scans so watchdog recovery cannot reintroduce Delivery callback coupling.

Preserve the dual-write-disabled branch exactly: when run dual-write is off,
the watchdog must not call `append_run_timeout_failure()` or emit a run event.
It should send only the failed frontend processing-status frame through the
injected legacy processing-status callable, preserving the
current `"Run watchdog: stale non-terminal run timed out"` details text and
`client_request_id`.

Add an explicit stale-watchdog dependency binding in this task; banning the old
imports without wiring replacement ports is not implementable. Keep the current
scalar constructor arguments and add a binder/setter consistent with
`set_leader_election(...)`. Because `execution/ports.py` is module-private, the
job must not import `RunLifecyclePort`, `EventPublisher`, or other
Execution-private types. Use a job-local dataclass whose fields are pre-bound
app-shell callables:

```python
@dataclass(frozen=True)
class StaleRunWatchdogEventDeps:
    append_run_timeout_failure: Callable[..., Awaitable[dict[str, Any] | None]]
    emit_run_event: Callable[..., Awaitable[None]]
    emit_legacy_processing_status: Callable[..., Awaitable[None]]
    run_event_enabled: Callable[[], bool]
    run_dual_write_enabled: Callable[[], bool]

def set_execution_event_deps(self, deps: StaleRunWatchdogEventDeps) -> None:
    self._execution_event_deps = deps
```

The stale checker also has orphan and supervisor recovery scheduling paths that
currently import the `RoomMessageCenter` singleton and call
`asyncio.create_task(room_message_center.process_room_user_message(...))`
directly. Those are Execution-owned orchestration runs. Phase 7b migrates them
through Execution task tracking in this task:

```python
@dataclass(frozen=True)
class StaleRecoveryDeps:
    schedule_recovery: Callable[..., asyncio.Task[Any]]

def set_execution_recovery_deps(self, deps: StaleRecoveryDeps) -> None:
    self._execution_recovery_deps = deps
```

`ExecutionFacade` should expose an app-shell method such as
`schedule_recovery_orchestration(request: OrchestrationRequest, *, reason: str)
-> asyncio.Task[Any]`. This method is not part of the public
`ExecutionEngine` protocol; it exists so the app shell can migrate the stale
checker without widening Common. It must create the coroutine through the same
`_spawn_orchestration(...)` / `traced_create_task` path used by
`start_orchestration(...)`, with a stable task name that includes the recovery
reason and message id.

Update orphan and supervisor recovery to call the injected scheduler instead of
importing `modules.RoomMessageCenter.room_message_center` or calling bare
`asyncio.create_task(...)`. Keep the current bounded-concurrency semaphore: add
the semaphore-release done callback to the returned tracked task, and keep the
current success/failure logging either inside the scheduled coroutine wrapper or
in a task done callback that inspects the result safely. Add tests proving:
- orphan and supervisor recovery skip before DB claim/scheduling and log a
  clear warning when `_execution_recovery_deps` has not been bound yet;
- orphan recovery schedules through `StaleRecoveryDeps.schedule_recovery`;
- supervisor recovery schedules through the same injected scheduler after the
  claim/cancellation checks pass;
- `jobs/stale_task_checker.py` no longer imports
  `modules.RoomMessageCenter.room_message_center`;
- no `asyncio.create_task(` calls remain in the stale checker recovery paths.

`main.py` must bind this on the existing `StaleTaskChecker` instance after the
same app-shell event dependencies are created for ExecutionFacade and
RoomServices. The watchdog timeout path and stale recovery scheduling paths must
fail fast or skip with an explicit log if they run before binding. Recovery
pre-bind checks must happen before acquiring recovery semaphores, claiming stuck
supervisor messages, or importing any legacy `RoomMessageCenter` singleton, so
startup calls to `check_stale_tasks()` cannot mutate recovery state before
Execution wiring exists. The job must not lazily import
`services.run_lifecycle_service`, `services.sse_services`,
`services.run_command_handler`, or `modules.RoomMessageCenter` as fallbacks
after this task.

Example startup binding:

```python
async def emit_watchdog_run_event(
    *,
    room_id: str,
    payload: dict[str, Any],
    message_id: str | None,
    client_request_id: str | None,
) -> None:
    await _delivery_deps.event_publisher.emit(
        run_event_notification_from_payload(
            room_id=room_id,
            payload=payload,
            correlation_id=client_request_id,
        )
    )

stale_task_checker.set_execution_event_deps(StaleRunWatchdogEventDeps(
    append_run_timeout_failure=run_lifecycle.append_run_timeout_failure,
    emit_run_event=emit_watchdog_run_event,
    emit_legacy_processing_status=legacy_processing_status_publisher.emit_processing_status,
    run_event_enabled=run_event_sse_enabled,
    run_dual_write_enabled=feature_run_dual_write_enabled,
))

stale_task_checker.set_execution_recovery_deps(StaleRecoveryDeps(
    schedule_recovery=execution_facade.schedule_recovery_orchestration,
))
```

The watchdog dual-write branch must consult
`deps.run_dual_write_enabled()` before calling
`deps.append_run_timeout_failure(...)`. If dual-write is disabled, preserve the
current feature-off behavior: increment the forced-failure counter, emit only
the failed frontend processing-status compatibility frame through
`deps.emit_legacy_processing_status`, and do not call the lifecycle port,
`RunEventNotification`, or `EventPublisher`. If dual-write is enabled but the
append call returns `None`, log and skip the run-event emit rather than passing
`None` to `deps.emit_run_event`. `deps.run_event_enabled()` controls only whether
the run-event SSE is emitted after a lifecycle payload exists. When enabled,
call `deps.emit_run_event(room_id=room_id, payload=lifecycle_payload,
message_id=tid, client_request_id=client_request_id)` so the app-shell wrapper
preserves explicit correlation and converts the raw command-handler payload with
`run_event_notification_from_payload(...)` before calling `EventPublisher.emit`.
The watchdog must not bind `_delivery_deps.event_publisher.emit` directly to a
raw lifecycle payload, and it must not call
`run_event_notification_from_payload(...)` positionally. The wrapper must pass
`room_id=` and preserve run-event `client_request_id` / `correlation_id` parity
with the old `broadcast_run_event_payload(...)` path: use the explicit
`client_request_id` only, or the `correlation_id` already present in the
returned lifecycle payload. Do not apply the SSE processing-status DB fallback
to run-event correlation. The flag does not authorize lifecycle persistence.

- [ ] **Step 2: Use RoomServices event binding**

`services/room_services.py` still owns several send-message lifecycle/status
pairs during Phase 7b, not only `_send_processing_status()`. Task 7 introduced
the explicit binder below so Task 8 can use an injected callable instead of
importing Execution globals or module-private `execution.ports`:

```python
def bind_execution_event_deps(
    *,
    processing_status_emitter: Callable[..., Awaitable[dict[str, Any] | None]],
) -> None: ...
```

`main.py` already calls this after creating Execution deps, passing an app-shell
callable that closes over the same `RunLifecycleAdapter`,
`_delivery_deps.event_publisher`, `LegacyProcessingStatusC3Adapter`,
`RunEventEnabled` flag, and `ClientRequestIdResolver` used by the facade. This
avoids `services.room_services` importing `execution.events` or private
Execution ports directly. Replace every
sendMessage-owned direct
`record_and_maybe_broadcast_run_event(...)` / `send_processing_status(...)` pair
in `services/room_services.py` with the injected processing-status emitter,
including current direct pairs outside `RoomServices._send_processing_status()`.
If this is deferred, add an explicit manifest-covered compatibility exception
and include `services/room_services.py` in the final static scans.

- [ ] **Step 3: Add initial manifest**

Use stable `call_id` keys and exact expression fields:

```json
{
  "call_id": "execution.orchestration.RoomMessageCenter._handle_v2_run_result.completed.1",
  "path": "execution/orchestration/room_message_center.py",
  "function_or_method": "RoomMessageCenter._handle_v2_run_result",
  "status_expression": "SSEProcessingStatus.COMPLETED",
  "room_id_expression": "room_id",
  "message_id_expression": "user_message_id",
  "lifecycle_message_id_expression": "user_message_id",
  "record_lifecycle_expression": true,
  "client_request_id_expression": null,
  "details_expression": null,
  "legacy_details_expression": null,
  "error_message_expression": null,
  "agents_expression": null,
  "recording_kind": "emit_processing_status",
  "expects_run_event_sse": true
}
```

Legacy-status manifest entry shape:

```json
{
  "call_id": "execution.orchestration.QueueExecutor.process_queue.awaiting-input.1",
  "path": "execution/orchestration/queue_executor.py",
  "function_or_method": "QueueExecutor.process_queue",
  "status_expression": "SSEProcessingStatus.AWAITING_INPUT",
  "room_id_expression": "room_id",
  "message_id_expression": "user_message_id",
  "lifecycle_message_id_expression": "user_message_id",
  "record_lifecycle_expression": true,
  "client_request_id_expression": null,
  "details_expression": "{\"prompt\": prompt_text}",
  "legacy_details_expression": null,
  "error_message_expression": null,
  "agents_expression": null,
  "recording_kind": "legacy_processing_status_compat",
  "expects_run_event_sse": true,
  "legacy_status_reason": "ProcessingStatusEvent currently excludes awaiting_input; preserve frontend SSE shape through Delivery compatibility path."
}
```

Representable-status legacy-details entry shape:

```json
{
  "call_id": "execution.orchestration.SupervisorExecutor.plan_next_action.processing.1",
  "path": "execution/orchestration/supervisor_executor.py",
  "function_or_method": "SupervisorExecutor._plan_next_action",
  "status_expression": "SSEProcessingStatus.PROCESSING",
  "room_id_expression": "room_id",
  "message_id_expression": "user_message_id",
  "lifecycle_message_id_expression": "user_message_id",
  "record_lifecycle_expression": true,
  "client_request_id_expression": null,
  "details_expression": null,
  "legacy_details_expression": "\"Planning next action...\"",
  "error_message_expression": null,
  "agents_expression": null,
  "recording_kind": "legacy_processing_status_compat",
  "expects_run_event_sse": true,
  "legacy_detail_shape_reason": "processing is representable, but current frontend SSE details is a string; ProcessingStatusEvent.details is structured, so preserve the legacy frame until a frontend-compatible DTO widening lands."
}
```

The manifest must distinguish frontend compatibility details from lifecycle
error text. `legacy_details_expression` preserves the raw SSE `details` value,
while `error_message_expression` feeds
`RunLifecyclePort.record_processing_status(..., error_message=...)`. Terminal
failure/cancel migrations must set `error_message_expression` when the old call
carried failure text, even if the frontend frame uses legacy string details. Add
a gate assertion that failure and cancel manifest entries with old
`details=error_message` calls still pass that value to the lifecycle adapter; do
not rely on `legacy_details_expression` alone for lifecycle persistence.

- [ ] **Step 4: Migrate one component at a time**

Replace:

```python
await record_and_maybe_broadcast_run_event(
    room_id,
    SSEProcessingStatus.COMPLETED,
    user_message_id,
    client_request_id=client_request_id,
    details=error_message,
    sse=self.sse_manager,
)
await self.sse_manager.send_processing_status(
    room_id,
    SSEProcessingStatus.COMPLETED,
    user_message_id,
    details=error_message,
    client_request_id=client_request_id,
)
```

with:

```python
from common.a2a_constants import SSEProcessingStatus

await emit_processing_status(
    room_id=room_id,
    status=SSEProcessingStatus.COMPLETED,
    message_id=user_message_id,
    lifecycle_message_id=user_message_id,
    client_request_id=client_request_id,
    legacy_details=error_message,
    error_message=error_message,
    agents=agents,
    run_lifecycle=self.run_lifecycle,
    event_publisher=self.event_publisher,
    legacy_processing_status_publisher=self._legacy_processing_status_publisher,
    run_event_enabled=self._run_event_enabled,
    client_request_id_resolver=self._client_request_id_resolver,
)
```

For migrated callers with distinct frontend and lifecycle ids, pass the old
`sse_message_id_expression` as `message_id` and the old
`lifecycle_message_id_expression` as `lifecycle_message_id`.

When replacing legacy calls that used string details, do not drop the text and
do not change the frontend wire type to a dict. Pass the old raw string as
`legacy_details=...`; if it is also lifecycle failure text, also pass
`error_message=...`. Only pass `details=...` when the old call already had a
structured details dict that should remain frontend-visible. Examples such as
`details="Planning next action..."` must stay as string details through the
compatibility publisher.

Inject `run_lifecycle`, `event_publisher`,
`legacy_processing_status_publisher`, `run_event_enabled`, and
`client_request_id_resolver` into constructors and store all five on every
moved class that replaces lifecycle/SSE processing-status calls. This explicitly
includes `execution/dispatch/response_handler.py`, not only the moved
orchestration classes: current `AgentResponseHandler._on_interactive()` and
`_on_processing_status()` call run lifecycle/SSE helpers directly and must be
migrated in Task 8 rather than left for the final static gate to catch. Use exact
attribute names:
`self.run_lifecycle`, `self.event_publisher`,
`self._legacy_processing_status_publisher`, `self._run_event_enabled`, and
`self._client_request_id_resolver`. Use those same names in examples and tests
so `object.__new__` fixtures can initialize the required dependencies without
guessing.
Existing tests that instantiate with `object.__new__` must set those attributes
directly or use a helper fixture.

- [ ] **Step 5: Preserve transport-only exceptions**

Do not add lifecycle recording for:
- legacy Workflow cancellation paths marked decommissioned;
- legacy WorkflowCenter/orchestration-center paths that remain outside Phase 7b
  execution extraction;
- task-notification per-agent task state frames that are not root orchestration lifecycle;
- frontend-only soft clears that do not transition run lifecycle.

All exceptions must be documented in the Phase 7 manifest with a `transport_only_reason`.
The final gates must not phrase this as a global backend SSE ban: active
workflow routes may still reach `modules/WorkflowCenter.py` and its legacy
`send_processing_status(...)` calls until the separate Workflow
decommission/migration phase. Those paths need explicit manifest classification
as out of Phase 7b scope.

- [ ] **Step 6: Run ordering tests**

Run:

```bash
PYTHONPATH=. uv run pytest -q \
  tests/test_phase7_execution_event_gate.py \
  tests/test_phase7a_processing_status_gate.py \
  tests/test_phase7a_processing_status_golden.py \
  tests/test_module_room_message_center.py \
  tests/test_module_queue_executor.py \
  tests/test_module_supervisor_executor.py \
  tests/test_agent_response_handler.py \
  tests/test_stale_task_checker_run_lifecycle.py \
  tests/test_stale_task_checker_recovery.py
```

Expected: pass; golden tests now assert lifecycle -> optional typed `RunEventNotification` -> typed or compatibility `processing_status` order rather than depending on the old direct `sse_manager.broadcast_to_room(...)` call sites.
`tests/test_stale_task_checker_run_lifecycle.py` must also assert the watchdog
wrapper calls `run_event_notification_from_payload(room_id=..., payload=...,
correlation_id=...)` with the stale run's room id and the explicit
`client_request_id` only, unless the lifecycle payload itself already includes
`correlation_id`.
`tests/test_stale_task_checker_recovery.py` must assert orphan/supervisor
recovery scheduling calls the injected Execution recovery scheduler and does not
use the legacy `RoomMessageCenter` singleton or bare recovery
`asyncio.create_task(...)`.
The event gate must also assert `error_message_expression` is present and wired
for terminal failure/cancel migrations that previously passed
`details=error_message` to lifecycle recording.

- [ ] **Step 7: Commit**

```bash
git add execution api services jobs/stale_task_checker.py main.py container.py tests/fixtures/phase7_execution_event_callers.json tests/test_phase7_execution_event_gate.py tests/test_phase7a_processing_status_gate.py tests/test_phase7a_processing_status_golden.py tests/test_stale_task_checker_run_lifecycle.py tests/test_stale_task_checker_recovery.py tests
git commit -m "refactor: emit execution processing status through event publisher"
```

### Task 9: Keep HITL SSE On Legacy Compatibility Path

**Files:**
- Modify: `execution/hitl/service.py`
- Modify: `execution/hitl/factory.py`
- Modify: `execution/facade.py`
- Modify: `container.py`
- Modify: `main.py`
- Modify: `tests/test_api_hitl.py`
- Modify: `tests/test_execution_facade.py`
- Modify: `tests/test_service_hitl.py`
- Modify: `tests/test_phase7_execution_event_gate.py`
- Do not modify: `common/dto/delivery.py` or `delivery/**` for HITL typed DTO
  widening in Phase 7b.

- [ ] **Step 1: Write failing tests**

Phase 7b explicitly chooses the compatibility branch for HITL SSE. The current
`HITLRequestEvent` / `HITLResolvedEvent` DTOs are too narrow for the existing
frontend payload, so this task must not widen those DTOs or emit narrower typed
HITL events.

Write tests that assert:
- `request_input()` persists first, then calls
  `HITLDeliveryPort.emit_hitl_event(message_type=..., payload=...)` with the
  exact current frontend payload shape.
- `handle_response()` / `resolve_hitl()` persist state first, then emit the
  current resolved/canceled/error HITL frames through `HITLDeliveryPort`.
- The emitted payload includes the current fields: `request_id`, computed
  `message_id`, `source`, `client_request_id`, `prompt`, `prompt_type`,
  `choices`, `agent_id`, `agent_name`, `source_step_id`, `group_id`,
  `group_total`, `group_index`, `status`, and `error_message` when present.
- `execution/hitl/**` does not construct `HITLRequestEvent` or
  `HITLResolvedEvent` in Phase 7b.
- `tests/test_phase7_execution_event_gate.py` documents the path-level
  compatibility exception for HITL SSE frames.

- [ ] **Step 2: Keep the Task 5 injected-port constructor**

Do not add an `EventPublisher` dependency to `HITLService` in Phase 7b. Keep the
Task 5 constructor and route HITL SSE through the injected `HITLDeliveryPort`:

```python
class HITLService:
    def __init__(
        self,
        *,
        persistence: HITLPersistencePort,
        delivery: HITLDeliveryPort,
        continuation: HITLContinuationPort,
        task_notifier: HITLTaskNotificationPort,
    ) -> None:
        self._persistence = persistence
        self._delivery = delivery
        self._continuation = continuation
        self._task_notifier = task_notifier
```

Wire this explicitly in `container.py` / `main.py`: construct or bind the moved
`execution.hitl.service.HITLService` with `LegacyHITLDeliveryAdapter(sse_manager)`
and pass that runtime into `create_execution_facade(...)`. Do not rely on an
implicit global or a vague startup-side "may bind" hook, and do not import
concrete Delivery from `execution.hitl`.

- [ ] **Step 3: Keep `_emit_hitl_event()` compatibility-only**

Keep `_emit_hitl_event()` behind `HITLDeliveryPort`. It should build the same
prebuilt `message_type` and payload as the current service, then call
`self._delivery.emit_hitl_event(...)`. Do not instantiate `HITLRequestEvent`,
`HITLResolvedEvent`, or any other typed HITL delivery DTO in Phase 7b. A future
frontend-coordinated task may widen `common/dto/delivery.py` and Delivery
translators, but that is not part of this plan.

- [ ] **Step 4: Run tests**

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_api_hitl.py tests/test_service_hitl.py tests/test_execution_facade.py tests/test_phase7_execution_event_gate.py
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add execution/hitl/service.py execution/hitl/factory.py execution/facade.py container.py main.py tests/test_api_hitl.py tests/test_service_hitl.py tests/test_execution_facade.py tests/test_phase7_execution_event_gate.py
git commit -m "refactor: keep hitl events on compatibility path"
```

### Task 10: Move Startup Healing And Shutdown Cancellation Into Execution

**Files:**
- Modify: `execution/facade.py`
- Modify: `execution/run_lifecycle.py`
- Modify: `main.py`
- Modify: `tests/test_execution_facade.py`
- Modify: `tests/test_multi_worker_safety.py`
- Modify: tests covering `main.compute_health_status` only if imports change

- [ ] **Step 1: Write tests**

Add tests:
- lifespan/startup wiring calls `ExecutionEngine.heal_diverged_runs(limit=500)`
  after `create_execution_deps(execution_facade)` has populated
  `_execution_deps`, after run lifecycle indexes are created, and before
  background services start or traffic can be served. This must not be guarded in
  a way that silently skips healing when Execution wiring is expected to exist.
- `ExecutionFacade.heal_diverged_runs()` delegates to run lifecycle and returns healed count.
- `ExecutionFacade.cancel_inflight_tasks()` cancels spawned tasks, awaits
  `asyncio.gather(..., return_exceptions=True)`, and returns count only after
  cancelled orchestration cleanup has run. Tests must prove it snapshots
  `self._inflight` before iterating/gathering so done callbacks that discard
  tasks cannot mutate the set being iterated.
- canceled in-flight orchestration tasks transition their run lifecycle to
  canceled before shutdown completes. Cover an orchestration entrypoint that
  receives `CancelledError` and prove it calls the same canceled
  lifecycle/status path used by user-initiated cancellation.
- the normal post-yield shutdown path calls `_require_execution_deps()` and then
  `cancel_inflight_tasks()` before Delivery/SSE drain. Missing Execution deps may
  be tolerated only in an explicit startup-failure cleanup branch before
  Execution wiring completed. Update `tests/test_multi_worker_safety.py` or the
  relevant lifespan test to assert ordering relative to `set_draining(True)`:
  Execution task cancellation must happen first so cancellation lifecycle/status
  emits can still use Delivery before the server starts rejecting/draining
  frontend streams.

- [ ] **Step 2: Remove direct startup heal helper from `main.py`**

Replace:

```python
await _heal_diverged_runs_on_startup()
```

Do not leave this call at the current early startup location. Relocate startup
healing to immediately after the Task 7 app-shell wiring has created
`execution_facade` and assigned `_execution_deps = create_execution_deps(execution_facade)`,
and after run lifecycle indexes have been created. It must still run before
background services start and before serving traffic. The startup path should
fail loudly in tests if `_execution_deps` is unexpectedly missing at that point;
do not use `if _execution_deps is not None` in a way that can silently skip
healing on a partially wired startup.

Use:

```python
execution_deps = _require_execution_deps()
try:
    healed = await execution_deps.execution_engine.heal_diverged_runs(limit=500)
except Exception:
    logger.warning("startup heal: failed; continuing startup", exc_info=True)
else:
    if healed:
        logger.info("startup heal: healed %s diverged run(s)", healed)
```

- [ ] **Step 3: Add shutdown cancellation**

Before relying on `cancel_inflight_tasks()`, add explicit outer
`asyncio.CancelledError` handlers around the orchestration entrypoints that can
own a run (`RoomMessageCenter.process_room_user_message`, queue execution, and
supervisor execution as applicable). The handler must record/emit canceled
run lifecycle status through injected `RunLifecyclePort` /
`emit_processing_status(...)` dependencies and then re-raise the cancellation.
Do not treat "task was canceled and counted" as sufficient evidence; the design
requires the run state to become canceled too.

Before Delivery/SSE drain. On the normal post-yield shutdown path, require
Execution deps instead of using an optional guard. Missing deps may be tolerated
only in explicit startup-failure cleanup before Execution wiring completed. Do
not call `sse_transport.set_draining(True)` or start Delivery drain before this
block; cancellation may emit final lifecycle and processing-status frames, so
the lifespan test must prove `cancel_inflight_tasks()` runs before
`set_draining(True)`:

```python
execution_deps = _require_execution_deps()
cancelled = await execution_deps.execution_engine.cancel_inflight_tasks()
if cancelled:
    logger.info("shutdown: cancelled %s in-flight execution task(s)", cancelled)
```

- [ ] **Step 4: Run tests**

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_execution_facade.py tests/test_run_lifecycle_service.py tests/test_heal_head_from_events.py tests/test_multi_worker_safety.py
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add execution/facade.py execution/run_lifecycle.py main.py tests/test_execution_facade.py tests/test_multi_worker_safety.py
git commit -m "refactor: move execution lifecycle startup and shutdown hooks"
```

### Task 11: Register Hub Internal Handler Without Implementing Phase 8

**Files:**
- Modify: `execution/facade.py`
- Modify: `container.py`
- Modify: `main.py`
- Modify: `tests/test_execution_facade.py`
- Modify: `tests/test_execution_protocols.py`
- Modify: `tests/test_api_relay.py`
- Modify: `tests/test_api_webhooks.py`
- Modify: `tests/test_delivery_event_publisher.py`

- [ ] **Step 1: Write sink tests**

Assert `ExecutionFacade.handle_hub_agent_response(event)` delegates to the same response handling path currently reached by relay/webhook callbacks.
The facade must receive an explicit `AgentResponseHandlerPort` dependency from
Task 6 wiring and call `await self._agent_response_handler.handle(agent_event)`
after converting `HubAgentResponseInternal` into the execution-local
`AgentEvent`. Do not instantiate `AgentResponseHandler` inside
`handle_hub_agent_response()`, and do not reach through concrete Relay/Webhook
modules from the facade. Tests should assert the converted event is passed to
the injected port and that the app-shell wiring uses the same shared
response-handler seam described in Task 4.
Add Delivery event-publisher coverage in this task, not optionally: registered
internal handlers must run from `EventPublisher.emit_internal(...)`, and
frontend-visible `emit(...)` must not dispatch internal handlers. This keeps
Hub response handling on the internal-event entry point described by the Common
protocol instead of overloading frontend delivery.
Do not add Hub response ownership, idempotency, or durable replay behavior in
Phase 7b. The current Phase 7b `ExecutionFacade` constructor has no owner map,
response-idempotency repository, or persisted-response port, and the Hub runtime
bridge is a Phase 8 concern. Task 11 only registers the internal handler seam and
validates the adapter into the existing response-handler path.

Defer these target behaviors to Phase 8 unless that phase first introduces an
explicit `HubResponseProcessingPort` / idempotency repository:
- `_owned_hub_tasks` maps `task_id -> run_id` for tasks dispatched by this facade.
- Responses for unowned `task_id` values are ignored.
- The handler computes an idempotency key from `(event.hub_id, event.task_id, response_seq)`.
- Duplicate idempotency keys already persisted in the run event log are skipped.
- Accepted responses are persisted/marked before invoking the response handler path.

Define the internal adapter explicitly. It must use the execution-local
normalized event dataclass moved to `execution.dispatch.agent_event.AgentEvent`,
not `common.dto.AgentEvent`:

```python
from typing import Any
from collections.abc import Mapping
from copy import deepcopy

AGENT_EVENT_KINDS = {
    "artifact_update",
    "response",
    "error",
    "canceled",
    "task_submitted",
    "status_update",
    "interactive",
    "processing_status",
}

LEGACY_COMMON_AGENT_EVENT_KIND_MAP = {
    # common.dto.execution.AgentEvent legacy values:
    "final": "response",
    "input_required": "interactive",
    # Already normalized values:
    "status_update": "status_update",
    "error": "error",
}

UNSUPPORTED_PHASE7B_HUB_EVENT_TYPES = {
    # A partial is non-terminal, while AgentResponseHandler._on_response()
    # marks the task completed and resumes orchestration. Phase 7b rejects it
    # instead of guessing a non-terminal mapping.
    "partial",
}

def _require_hub_payload_field(payload: dict[str, Any], key: str) -> Any:
    value = payload.get(key)
    if value is None or value == "":
        raise ValueError(f"HubAgentResponseInternal payload missing required field: {key}")
    return value

def _hub_payload_kind(payload: dict[str, Any]) -> str:
    raw = payload.get("kind") or payload.get("event_type")
    if raw is None or raw == "":
        raise ValueError("HubAgentResponseInternal payload missing required field: kind")
    raw_kind = str(raw)
    if raw_kind in UNSUPPORTED_PHASE7B_HUB_EVENT_TYPES:
        raise ValueError(f"Unsupported non-terminal Hub AgentEvent event_type: {raw_kind}")
    kind = LEGACY_COMMON_AGENT_EVENT_KIND_MAP.get(raw_kind, raw_kind)
    if kind not in AGENT_EVENT_KINDS:
        raise ValueError(f"Unsupported AgentEvent kind from Hub payload: {kind}")
    return kind

def _hub_payload_message_id(payload: dict[str, Any]) -> str:
    value = payload.get("message_id")
    if value is None:
        value = payload.get("continuation_message_id")
    if not isinstance(value, str) or not value:
        raise ValueError(
            "HubAgentResponseInternal payload requires non-empty string message_id or continuation_message_id"
        )
    return value

def _agent_event_details(value: Any) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return str(value)

def _thaw_hub_payload_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_hub_payload_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_hub_payload_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_thaw_hub_payload_value(item) for item in value]
    return deepcopy(value)

def _optional_hub_str(
    payload: dict[str, Any],
    key: str,
    *,
    default: str | None = None,
) -> str | None:
    value = payload.get(key, default)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Hub AgentEvent field {key} must be a string")
    return value

def _optional_hub_bool(
    payload: dict[str, Any],
    key: str,
    *,
    default: bool,
) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"Hub AgentEvent field {key} must be a boolean")
    return value

def _optional_hub_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Hub AgentEvent field {key} must be an integer")
    return value

TERMINAL_AGENT_EVENT_KINDS = {
    "response",
    "error",
    "canceled",
}

VALID_ERROR_TASK_STATES = {
    "failed",
    "canceled",
    "rejected",
}

LEGACY_TASK_STATE_VALUE_MAP = {
    "input_required": "input-required",
    "auth_required": "auth-required",
}

VALID_INTERACTIVE_TASK_STATES = {
    "input-required",
    "auth-required",
}

VALID_PROCESSING_STATUS_STATES = {
    "queued",
    "processing",
    "awaiting_input",
    "completed",
    "failed",
    "canceled",
    "rejected",
    "rate_limited",
    "error",
}

def _normalize_hub_state(kind: str, value: str | None) -> str | None:
    if value is None:
        return None
    normalized = LEGACY_TASK_STATE_VALUE_MAP.get(value, value)
    if kind == "processing_status":
        allowed = VALID_PROCESSING_STATUS_STATES
    elif kind == "error":
        allowed = VALID_ERROR_TASK_STATES
    elif kind == "interactive":
        allowed = VALID_INTERACTIVE_TASK_STATES
    else:
        return normalized
    if normalized not in allowed:
        raise ValueError(f"Unsupported Hub AgentEvent state for {kind}: {value}")
    return normalized

def _hub_payload_state(kind: str, payload: dict[str, Any]) -> str | None:
    return _normalize_hub_state(kind, _optional_hub_str(payload, "state"))

def _optional_hub_list_of_dicts(
    payload: dict[str, Any],
    key: str,
) -> list[dict[str, Any]] | None:
    value = _thaw_hub_payload_value(payload.get(key))
    if value is None:
        return None
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"Hub AgentEvent field {key} must be a list of objects")
    return value

def _validate_hub_payload_for_kind(kind: str, payload: dict[str, Any]) -> None:
    state = _hub_payload_state(kind, payload)
    text = _optional_hub_str(payload, "text", default="")
    error_text = _optional_hub_str(payload, "error_text")
    if kind == "processing_status" and not state:
        raise ValueError("processing_status Hub payload requires state")
    if kind == "error" and not (error_text or text):
        raise ValueError("error Hub payload requires error_text or text")
    payload_is_final = payload.get("is_final")
    if payload_is_final is not None and not isinstance(payload_is_final, bool):
        raise ValueError("Hub AgentEvent field is_final must be a boolean")
    lifecycle_verified = payload.get("lifecycle_message_id_verified")
    if lifecycle_verified is not None and not isinstance(lifecycle_verified, bool):
        raise ValueError("Hub AgentEvent field lifecycle_message_id_verified must be a boolean")

def _validate_hub_event_consistency(
    event: HubAgentResponseInternal,
    kind: str,
    payload: dict[str, Any],
) -> None:
    payload_task_id = payload.get("task_id")
    if payload_task_id is not None and payload_task_id != event.task_id:
        raise ValueError("Hub payload task_id conflicts with event.task_id")
    if not event.task_id:
        raise ValueError("HubAgentResponseInternal requires top-level task_id")
    if kind in TERMINAL_AGENT_EVENT_KINDS and not event.is_terminal:
        raise ValueError(f"Hub AgentEvent kind {kind} requires terminal internal event")
    if kind not in TERMINAL_AGENT_EVENT_KINDS and event.is_terminal:
        raise ValueError(f"Hub AgentEvent kind {kind} must not use a terminal internal event")
    payload_is_final = payload.get("is_final")
    if kind in TERMINAL_AGENT_EVENT_KINDS and payload_is_final is False:
        raise ValueError(f"Hub AgentEvent kind {kind} cannot set is_final=False")

def _hub_payload_lifecycle_message_id(kind: str, payload: dict[str, Any]) -> str | None:
    value = _optional_hub_str(payload, "lifecycle_message_id")
    if value is None:
        return None
    if kind == "processing_status" and payload.get("lifecycle_message_id_verified") is not True:
        raise ValueError(
            "Hub processing_status lifecycle_message_id requires upstream turn/root validation"
        )
    return value

def hub_agent_response_internal_to_agent_event(
    event: HubAgentResponseInternal,
) -> AgentEvent:
    payload = event.payload
    kind = _hub_payload_kind(payload)
    _validate_hub_event_consistency(event, kind, payload)
    _validate_hub_payload_for_kind(kind, payload)
    return AgentEvent(
        kind=kind,
        room_id=event.room_id,
        message_id=_hub_payload_message_id(payload),
        agent_id=event.agent_id,
        task_id=event.task_id,
        turn_id=_optional_hub_str(payload, "turn_id"),
        text=_optional_hub_str(payload, "text", default="") or "",
        state=_hub_payload_state(kind, payload),
        parts=_optional_hub_list_of_dicts(payload, "parts"),
        artifacts=_optional_hub_list_of_dicts(payload, "artifacts"),
        context_id=_optional_hub_str(payload, "context_id"),
        error_text=_optional_hub_str(payload, "error_text"),
        related_message_id=_optional_hub_str(payload, "related_message_id"),
        user_id=_optional_hub_str(payload, "user_id"),
        client_request_id=_optional_hub_str(payload, "client_request_id"),
        lifecycle_message_id=_hub_payload_lifecycle_message_id(kind, payload),
        append=_optional_hub_bool(payload, "append", default=False),
        last_chunk=_optional_hub_bool(payload, "last_chunk", default=False),
        is_final=_optional_hub_bool(payload, "is_final", default=event.is_terminal),
        agent_name=_optional_hub_str(payload, "agent_name"),
        step_number=_optional_hub_int(payload, "step_number"),
        total_steps=_optional_hub_int(payload, "total_steps"),
        skip_persist=_optional_hub_bool(payload, "skip_persist", default=False),
        s3_converted=_optional_hub_bool(payload, "s3_converted", default=False),
        details=_agent_event_details(_thaw_hub_payload_value(payload.get("details"))),
    )
```

The real `AgentEvent` dataclass uses `kind`, not `event_type`, and has no
`content` field. If Hub payloads still carry legacy `event_type`, this adapter
must map it through an explicit table: `final` becomes terminal `response`,
`input_required` becomes `interactive`, while already-normalized
`status_update` and `error` stay unchanged. Reject legacy `partial` in Phase 7b:
`AgentResponseHandler._on_response()` completes the task and resumes
orchestration, so mapping non-terminal partial output to `response` would
prematurely complete the run. If a later phase needs internal partial handling,
it must introduce a tested non-terminal mapping rather than reusing terminal
`response`. Test this table; do not treat legacy `event_type` values as
already-normalized `AgentEvent.kind` values.
Because `AgentEvent`
is a dataclass and `Literal` is not runtime-enforced, the adapter must validate
`kind` against the allowed set before constructing the event. Required
response-handler fields such as `kind` and message identity must be validated by
the adapter; do not substitute empty strings or pass raw hub payloads directly to
`AgentResponseHandler`. The accepted Phase 7b internal payload schema is
`kind` plus either `message_id` or `continuation_message_id`, with optional
fields matching the normalized `AgentEvent` dataclass. `message_id` and
`continuation_message_id` must be non-empty strings; reject malformed scalar
values instead of coercing integers, dicts, or other truthy objects through
`str(...)`. `task_id` is already a
top-level `HubAgentResponseInternal` field; construct `AgentEvent.task_id` from
that field. If payload also includes `task_id`, validate that it matches the
top-level value instead of requiring duplicate payload data.
The adapter must also perform kind-specific validation before constructing the
dataclass: `processing_status` requires a string `state`, and `error` requires
`error_text` or `text` because `AgentResponseHandler._on_error()` ignores
`details`. State validation must target the normalized event kinds that the
handler actually interprets: `processing_status` uses SSE/lifecycle status
values, `interactive` passes `state` into `TaskState(state)`, and `error` may
pass `state` into `TaskState(state)`. Normalize legacy underscore task states
such as `input_required` and `auth_required` to current A2A wire values
`input-required` and `auth-required` before constructing `AgentEvent`. Do not
validate pre-normalization transport event names after the allowed-kind check
has already converted/rejected input. Do not reuse the A2A task-state allowlist for
`processing_status`:
relay/webhook processing-status frames use SSE/lifecycle status values such as
`processing`, `awaiting_input`, `rate_limited`, and `error`, while task-status
or error paths use A2A-shaped values only where the handler actually converts
them to `TaskState`. Add any other kind-specific requirements discovered from
`AgentResponseHandler.handle()` in the same adapter instead of relying on
handler fallthrough. Terminal handler branches must be guarded explicitly:
`response`, `error`, and `canceled` require
`HubAgentResponseInternal.is_terminal is True`, and payload `is_final=False`
for those kinds must be rejected because the current `AgentResponseHandler`
completes/resumes tasks for those branches regardless of `AgentEvent.is_final`.
The inverse mismatch must also be rejected: non-terminal handler kinds such as
`processing_status`, `status_update`, `interactive`, `task_submitted`, and
`artifact_update` must not arrive with `HubAgentResponseInternal.is_terminal is
True`.
Do not trust Hub payload `lifecycle_message_id` blindly for
`processing_status`. Current relay handling validates the candidate
`user_message_id` against the agent message turn/root before lifecycle writes;
otherwise it drops the frame. Phase 7b cannot reconstruct that validation from a
raw Hub payload inside `ExecutionFacade`, so the adapter must reject
`processing_status` payloads that include `lifecycle_message_id` unless an
upstream Phase 8 Hub normalizer has already performed equivalent turn/root
validation and sets `lifecycle_message_id_verified=True`. Until that verified
field exists, Hub `processing_status` internal events must omit
`lifecycle_message_id` so `AgentResponseHandler` does not write lifecycle state
against an unvetted id.
If Hub provides only `continuation_message_id`, map it to
`AgentEvent.message_id` deliberately and cover that mapping in Task 11 tests.
Task 11 tests must also cover rejection of an unknown `kind`, conflicting
payload/top-level `task_id`, missing kind-specific fields, malformed scalar
fields such as non-string `message_id` / `continuation_message_id` / `text` /
`error_text`, invalid boolean flags, terminal `response` / `error` / `canceled`
events with `is_terminal=False` or payload `is_final=False`, non-terminal kinds
with `is_terminal=True`, unverified `processing_status.lifecycle_message_id`,
invalid integer step counters, invalid `parts` / `artifacts` shapes, unsupported
`state` values per event kind, valid processing-status states that are not A2A
task states, interactive underscore-state normalization, explicit rejection of
legacy `partial`, and the legacy `event_type` -> `kind` mapping so unsupported
Hub payloads cannot silently fall through
`AgentResponseHandler.handle()`.
Because `HubAgentResponseInternal` is a `FrozenDTO`, its `payload` and nested
containers are immutable. The adapter must deep-copy/thaw mutable fields before
constructing the execution-local `AgentEvent`, especially `parts`, `artifacts`,
and nested file metadata. `AgentResponseHandler` may mutate those structures
during inline-file/S3 conversion, so Task 11 must include a Hub inline-file
response test proving the converted handler path can update nested `bytes`,
`uri`, and `metadata` without hitting `FrozenDict` / `FrozenList`.

- [ ] **Step 2: Add registration in app shell**

In this task, extend the container deps to expose the Hub sink:

```python
@dataclass(frozen=True)
class ExecutionDeps:
    execution_engine: ExecutionEngine
    hitl_manager: HITLManager
    hub_agent_response_sink: HubAgentResponseSink
```

Update `create_execution_deps(facade)` and add the runtime conformance assertion
that was intentionally deferred in Task 6:

```python
assert isinstance(facade, HubAgentResponseSink)
```

After both Delivery and Execution are constructed:

```python
_delivery_deps.event_publisher.register_internal_handler(
    "hub_agent_response_internal",
    _execution_deps.hub_agent_response_sink.handle_hub_agent_response,
)
```

If Phase 8 HubRuntimeBridge is not present, this registration is still harmless and prepares the seam.

- [ ] **Step 3: Keep current relay paths intact**

Do not remove current `services.relay_service` or webhook paths in Phase 7b. Phase 8 owns Hub runtime rewire.

- [ ] **Step 4: Run tests**

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_execution_facade.py tests/test_api_relay.py tests/test_api_webhooks.py tests/test_agent_response_handler.py tests/test_delivery_event_publisher.py
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add execution/facade.py container.py main.py tests/test_execution_facade.py tests/test_execution_protocols.py tests/test_api_relay.py tests/test_api_webhooks.py tests/test_delivery_event_publisher.py
git commit -m "feat: register execution hub response sink"
```

### Task 12: Import Boundary And Legacy Surface Gates

**Files:**
- Modify: `tests/test_execution_protocols.py`
- Modify: `tests/test_phase7_execution_event_gate.py`
- Modify: `docs/MODULAR_DECOUPLING_DESIGN.md`

- [ ] **Step 1: Add import-boundary tests**

`tests/test_execution_protocols.py` should AST-parse `execution/**/*.py` and fail on:
- concrete `delivery.*` imports;
- `modules.*` imports;
- `database.*` imports;
- broad `services.*` imports, except exact path-level allowlist entries for named compatibility adapters with expiry notes;
- `services.sse_services`;
- `main`, `api`, or `container`;
- external SDKs `a2a`, `openai`, `google.genai`, `aioboto3` outside exact
  allowed adapter files. The initial A2A allowlist should include only files
  that currently own A2A protocol translation:
  `execution/dispatch/dispatch_middleware.py`,
  `execution/dispatch/transports/{direct,relay,webhook,base}.py`,
  `execution/orchestration/{queue_executor,room_message_center}.py`,
  `execution/state/task_state_manager.py`, and
  `execution/dispatch/response_handler.py`. If implementation removes A2A
  types from one of these files, remove it from the allowlist in the same
  commit. Do not add `execution/cancellation.py` or `execution/hitl/service.py`
  to this allowlist; those paths must communicate state through string/domain
  ports and leave any `TaskState` conversion to startup-bound adapter callables.
  Shared SDK-free status constants live in `common/a2a_constants.py`, not under
  `execution/**`, so non-Execution services do not import Execution.
  `common/a2a_constants.py` must not import `a2a.types`; enum conversion remains
  in adapter callables. The existing `common/utils/a2a_helpers.py` SDK import is
  a legacy utility exception used by current response handling, and the gate
  must either allowlist that exact helper with an expiry task or move the helper
  behind the A2A adapter in the same task.
- `fastapi` imports anywhere under `execution/**`; moved code must raise
  Execution-owned domain exceptions and API routes must translate them to
  HTTP responses.
- direct `config.settings` imports or direct environment reads
  (`os.environ`, `os.getenv`, `os.environ.get`) outside exact moved-file
  allowlist entries with expiry notes. The initial allowed paths, if scalar
  settings injection is not completed in Task 4, are
  `execution/orchestration/room_message_center.py` and
  `execution/orchestration/supervisor_executor.py`; the latter may temporarily
  carry the existing `SUPERVISOR_MAX_STEPS` `os.environ` read only as an
  explicit deferred config cleanup. If
  `CloudHealthMiddleware` still reads settings after the move, either inject
  `cloud_health_cache_ttl` / `cloud_health_check_timeout` through
  `cloud_health_middleware_factory` or add the exact temporary allowlist path
  `execution/dispatch/middleware/cloud_health.py` with an expiry note.

Allow temporary legacy imports only in explicitly named compatibility files, and document each one with an expiry task. The initial allowlist should include `execution/legacy_processing_status.py` for the C3 `send_processing_status` adapter and `execution/run_lifecycle.py` for the existing run command handler adapter; add no broad package allowlists.

- [ ] **Step 2: Assert old modules are shims**

AST-parse exactly the legacy paths moved in Task 4 and fail if any of those
paths contain class/function implementation bodies instead of imports,
compatibility proxy binding, and `__all__`. Enumerate the moved set explicitly:
`modules/RoomMessageCenter.py`, `modules/QueueExecutor.py`,
`modules/SupervisorExecutor.py`, `modules/debate_dispatcher.py`,
`modules/AgentDispatcher.py`, `modules/AgentMessageProcessor.py`,
`modules/TaskStateManager.py`, `modules/agent_event.py`,
`modules/dispatch_middleware.py`, `modules/agent_response_handler.py`, moved
files under `modules/transports/`, and moved files under `modules/middleware/`.
Do not glob all `modules/*.py`, because unrelated legacy modules may remain
real implementations in Phase 7b. Also parse `services/hitl_service.py`: after
Task 5 it must contain only the HITL compatibility imports, the bound proxy
instance, `bind_hitl_service(...)`, and `__all__`; it must not recreate a
no-argument `HITLService()` singleton.

- [ ] **Step 3: Assert no Delivery callback regression**

Add a strict scan:

```bash
rg -n "run_command_handler|record_processing_status|services\\.hitl_service|modules\\." delivery services/sse_services.py
```

Expected: no matches, except documentation/test strings that are explicitly ignored in the AST test.

Also assert Room does not route back through the HITL compatibility shim after
Task 5:

```bash
rg -n "services\\.hitl_service|from services import hitl_service" services/room_services.py
```

Expected: no matches. `RoomServices` must use the bound HITL pending checker,
not the Execution-backed legacy shim.

Also assert API routes no longer import the HITL compatibility shim after Task 7:

```bash
rg -n "services\\.hitl_service|from services import hitl_service" api/hitl.py api/sse.py
```

Expected: no matches. `api/hitl.py` must call the bound `HITLManager`, and
`api/sse.py` message cancellation must call `ExecutionEngine.cancel(...)`
rather than importing the HITL singleton for cleanup.

- [ ] **Step 4: Update design doc status**

Update `docs/MODULAR_DECOUPLING_DESIGN.md` Phase 6+7 section to say Phase 7a is complete and Phase 7b is governed by this plan.

- [ ] **Step 5: Run gates**

Run:

```bash
PYTHONPATH=. uv run pytest -q tests/test_execution_protocols.py tests/test_phase7_execution_event_gate.py tests/test_delivery_protocols.py
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add tests/test_execution_protocols.py tests/test_phase7_execution_event_gate.py docs/MODULAR_DECOUPLING_DESIGN.md
git commit -m "test: enforce execution module boundaries"
```

### Task 13: Full Flow Verification

**Files:**
- Modify tests only for legitimate API binding changes discovered during verification.

- [ ] **Step 1: Run focused Phase 7 suite**

Before running the focused suite, migrate old Phase 7a gates that inspect
production source locations directly. `tests/test_phase7a_processing_status_gate.py`
must read the Phase 7b execution manifest and new `execution/**` call sites
instead of requiring every old fixture entry to match a live
`modules/*` `send_processing_status` call. Keep the Phase 7a golden wire-order
assertions, but point static source discovery at the moved execution modules and
allow legacy shim files. Likewise, update
`tests/test_module_room_message_center.py` AST helpers that currently read
`modules/RoomMessageCenter.py` directly so implementation assertions inspect
`execution/orchestration/room_message_center.py` while shim-specific assertions
remain on the legacy module.

Run:

```bash
PYTHONPATH=. uv run pytest -q \
  tests/test_execution_protocols.py \
  tests/test_execution_facade.py \
  tests/test_phase7_execution_event_gate.py \
  tests/test_phase7a_processing_status_gate.py \
  tests/test_phase7a_processing_status_golden.py \
  tests/test_api_room_center.py \
  tests/test_api_hitl.py \
  tests/test_api_sse.py \
  tests/test_module_room_message_center.py \
  tests/test_module_queue_executor.py \
  tests/test_module_supervisor_executor.py \
  tests/test_sdr_wave1.py \
  tests/test_unified_summary.py \
  tests/test_direct_transport.py \
  tests/test_relay_streams.py \
  tests/test_module_task_state.py \
  tests/test_transport_parity.py \
  tests/test_turn_id_passthrough.py \
  tests/test_flow_contracts.py \
  tests/test_agent_response_handler.py \
  tests/test_stale_task_checker_run_lifecycle.py \
  tests/test_stale_task_checker_recovery.py
```

Expected: pass.

- [ ] **Step 2: Run Delivery regression suite**

Run:

```bash
PYTHONPATH=. uv run pytest -q \
  tests/test_delivery_protocols.py \
  tests/test_delivery_translator.py \
  tests/test_delivery_event_publisher.py \
  tests/test_sse_adapter_delivery.py \
  tests/test_service_sse.py \
  tests/test_sse_event_broker.py
```

Expected: pass.

- [ ] **Step 3: Run high-risk integration suites**

Run:

```bash
PYTHONPATH=. uv run pytest -q \
  tests/test_service_room.py \
  tests/test_phase5_supervisor_integration.py \
  tests/test_sdr_wave1.py \
  tests/test_unified_summary.py \
  tests/test_supervisor_v2_improvements.py \
  tests/test_supervisor_debate_sequential.py \
  tests/test_api_relay.py \
  tests/test_api_webhooks.py \
  tests/test_a2a_service_webhook_fallback.py \
  tests/test_multimodal_integration.py
```

Expected: pass.

- [ ] **Step 4: Run full test suite**

Run:

```bash
LOG_PATH=logs/app.log OPENAI_API_KEY=test-key uv run python -m pytest -q
```

Expected: pass.

- [ ] **Step 5: Run static scans**

Run:

```bash
rg -n "record_and_maybe_broadcast_run_event|broadcast_run_event_payload|services\\.run_lifecycle_service|services\\.run_command_handler|run_command_handler|feature_run_dual_write_enabled|send_processing_status\\(" execution api/room_center.py api/hitl.py api/sse.py services/room_services.py jobs/stale_task_checker.py
rg -n "from modules\\.RoomMessageCenter|room_message_center\\.process_room_user_message" jobs/stale_task_checker.py
rg -n "from execution\\.events|import execution\\.events" services/room_services.py
rg -n "services\\.hitl_service|from services import hitl_service" api/hitl.py api/sse.py services/room_services.py
rg -n "from services\\.sse_services|import services\\.sse_services" execution
rg -n "from modules\\.|import modules\\." execution
rg -n "from database\\.|import database\\.|from services\\.|import services\\." execution
```

Expected: no output except path-level allowlist entries for
`execution/legacy_processing_status.py` and `execution/run_lifecycle.py`, plus
any manifest-approved compatibility comments/tests. The first scan may report
`execution/legacy_processing_status.py` as the only production
`send_processing_status(` caller and `execution/run_lifecycle.py` as the only
production `run_command_handler` adapter. It must not report
`api/hitl.py`, `api/sse.py`, or `services/room_services.py` importing
`services.hitl_service` after those paths bind through Execution protocols. It
must not report
`jobs/stale_task_checker.py` importing `services.run_command_handler` or calling
`feature_run_dual_write_enabled()` directly after Task 8; those come through the
bound watchdog deps. It also must not report stale-checker recovery imports from
`modules.RoomMessageCenter` or direct
`room_message_center.process_room_user_message` calls after recovery scheduling
is routed through the Execution facade's tracked task helper. A focused
stale-recovery AST test must additionally prove the orphan/supervisor recovery
methods do not use bare `asyncio.create_task(`; the job's own `_run_loop()`
task is not an orchestration task and may remain outside that recovery-specific
assertion.

- [ ] **Step 6: Commit final test repairs**

Commit only if verification required fixes:

```bash
git add tests/test_execution_protocols.py tests/test_execution_facade.py tests/test_phase7_execution_event_gate.py tests/test_phase7a_processing_status_gate.py tests/test_phase7a_processing_status_golden.py tests/test_api_room_center.py tests/test_api_hitl.py tests/test_api_sse.py tests/test_module_room_message_center.py tests/test_module_queue_executor.py tests/test_module_supervisor_executor.py tests/test_sdr_wave1.py tests/test_unified_summary.py tests/test_direct_transport.py tests/test_relay_streams.py tests/test_module_task_state.py tests/test_transport_parity.py tests/test_turn_id_passthrough.py tests/test_flow_contracts.py tests/test_agent_response_handler.py tests/test_stale_task_checker_run_lifecycle.py tests/test_stale_task_checker_recovery.py tests/fixtures/phase7_execution_event_callers.json docs/superpowers/plans/2026-05-17-phase-7-execution-module.md docs/MODULAR_DECOUPLING_DESIGN.md
git commit -m "test: verify phase 7 execution extraction"
```

## Acceptance Checklist

- [ ] Phase 7a gates still pass.
- [ ] Phase 6 Delivery gates pass before Phase 7b starts.
- [ ] `ExecutionFacade` satisfies `ExecutionEngine`, `HITLManager`, and `HubAgentResponseSink`.
- [ ] API routes use Execution protocols for sendMessage, active runs, HITL, and cancel; `api/a2a_tasks.py` remains a documented Phase 7b deferral until the follow-up Execution A2A task API migration.
- [ ] `ExecutionEngine.cancel()` receives `requested_by_user_id` and preserves the Mongo cancellation audit field.
- [ ] `ExecutionFacade` background orchestration tasks use `traced_create_task()` by default.
- [ ] Phase 7b Execution-owned code no longer calls `sse_manager.send_processing_status()` directly outside `LegacyProcessingStatusC3Adapter` and old-code examples in tests/docs. This acceptance item is not a global backend SSE ban: legacy `modules/WorkflowCenter.py` and active `api/orchestration_center.py` workflow routes remain out of scope and must be manifest-classified as Workflow decommission/migration follow-up work.
- [ ] Execution-owned processing-status emits record lifecycle before typed Delivery emits or manifest-covered compatibility frames.
- [ ] `execution/**` has no direct `modules.*`, `database.*`, broad `services.*`, concrete Delivery, `api`, `main`, or `container` imports outside exact path-level compatibility allowlists.
- [ ] Delivery remains a pure transport and has no imports/callbacks into Execution or run lifecycle writers.
- [ ] Room-level locking behavior is preserved.
- [ ] Startup run-head healing is preserved through `ExecutionEngine.heal_diverged_runs()`.
- [ ] Shutdown cancels tracked in-flight Execution tasks, including sendMessage orchestration and stale-task orphan/supervisor recovery work scheduled through the Execution facade helper.
- [ ] Legacy `modules/*` imports still work through shims until Phase 9.
- [ ] Full message flow, HITL pause/resume, cancellation, direct queue, supervisor, debate, and relay/webhook paths pass focused tests.

## Handoff

Plan complete when this file is committed and `docs/MODULAR_DECOUPLING_DESIGN.md` points to it. Ready implementation branch must begin from a post-Phase-6 branch; current `main` is not sufficient because Delivery source files are absent.
