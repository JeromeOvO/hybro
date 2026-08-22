# Orchestrator Plan 5 — Legacy Retirement

This document is the implementation plan for deleting the legacy execution
system after Plan 4's production cutover has stabilized. Plan 5 does not add
capabilities; it removes the old Fast/Ultimate executors, the dual-routing
seam, and the compatibility layers so the repository ends with exactly one
execution path.

## Execution Record (traceability)

Changes land as one commit per phase, and each commit message carries the
phase it closes. This section is updated as each phase lands so the plan and
the history stay in lockstep.

- **Phase 1 — Stop dual routing** — landed (`refactor: retire dual-routing
  seam and route every ingress to the orchestrator`). Owner constants, routing
  flags, `assign_runtime`, and every legacy fallback removed; every new Run is
  orchestrator-owned.
- **Phase 2 — Delete the legacy Fast path** — landed (`refactor: delete legacy
  Fast/Ultimate executors and converge to the orchestrator`). `QueueExecutor`,
  `transports/direct.py`, `agent_dispatcher.py`, `agent_message_processor.py`,
  and the queue continuation path removed.
- **Phase 3 — Delete the legacy Ultimate path** — landed (same commit as Phase 2).
  `SupervisorExecutor`, `room_supervisor_service.py`, `planner*.py`,
  `synthesis_coordinator.py`, and `room_message_center.py` removed; the facade,
  stale-task checker, HITL continuation adapter, response handler, and relay
  service were converged to drop the `room_message_center` dependency.
- **Phase 4 — Delete the deterministic-semantics machinery** — landed
  (`refactor: delete legacy deterministic-semantics orchestration modules`).
  The planner-side rule engine (`action_validator`, `completion_policy`,
  `context_builder`, `context_ref_resolution`, `continuation_policy`,
  `dispatch_payload`, `file_turn`, `goal_fingerprinting`, `goal_progress`,
  `recovery_policy`, `terminal_summary`) is deleted. The
  `obligation`/`goal_fingerprint`/`completion_evidence` fields that still back
  the legacy `run_store`/`run_reducer`/HITL adapters are removed together with
  Phase 5.
- **Phase 5 — Converge HITL, callbacks, and data compatibility** — partially
  landed (forced convergence). The queue continuation, legacy recovery
  scheduler, and `room_message_center` reach-through are gone. Still remaining:
  deleting the legacy `HITLService`/`response_handler`/`transports`/
  `run_store`/`run_reducer` and building the hub-relay → orchestrator
  observation bridge.
- **Phase 6 — Clean up configuration, tests, and docs** — partially landed.
  Legacy-seam tests deleted/rewritten; the `use_supervisor` switch and the
  `obligation`/`goal_fingerprint`/`completion_evidence` model fields are the
  remaining `rg` invariants.

> **Convergence decision (accepted).** In the actual code the legacy
> executors are not leaf modules: deleting `room_message_center.py` forces the
> HITL continuation adapter, the cancellation finalizer, and the hub relay
> service to converge at the same time (they read
> `room_message_center.agent_response_handler` /
> `.agent_message_processor` / `.resume_queue_from_continuation`). Phases 2→3
> therefore land together with the forced Phase 5 HITL/relay/cancellation
> convergence as a single coordinated commit; the phase boundaries below are
> kept for traceability, not as independent merge windows.

The target architecture is:

```text
Fast profile ─────┐
                  ├→ RoomAgentSession
Ultimate profile ─┘   → OrchestratorKernel
                         → A2A runtime
                         → external Agent / HITL / Recovery / SSE
```

Fast and Ultimate remain product experiences and parameter profiles; they are
no longer separate executors.

## Architectural North Star

The single execution path is modeled on the mechanism of one orchestrating
agent harness (cf. the `pi` coding-agent harness): a single kernel holds the
model and the tool surface, delegates work to sub-agents, and keeps durable
state so every step is re-entrant and recoverable.

The mapping onto Hybro, and the one intentional difference:

- **`OrchestratorKernel` = the main agent.** One provider-neutral model/tool
  loop with CAS checkpoints, bounded compaction, and two-phase tool
  acceptance.
- **External A2A agents = the sub-agents.** Unlike pi's in-process coding
  sub-agents, Hybro's sub-agents are heterogeneous external services (cloud,
  hub, local) reached over A2A. Hybro never performs an agent's work itself:
  it is the middleman between the user and the agents — it decomposes intent,
  dispatches A2A tasks, observes results, resolves HITL, and synthesizes the
  user-facing answer.
- **The A2A runtime = the delegation/observation tool surface.** Dispatch
  (direct/relay), the webhook/SSE observation ingress, and the call ledger are
  the "tools" the kernel uses to reach sub-agents and to accept their results
  back.
- **HITL = the coordination channel.** When an agent needs the user, the
  kernel surfaces an interaction and resumes the run once the answer is durably
  applied (answer-idempotent).
- **Durable state = the recovery backbone.** `OrchestratorRunState` (schema 5),
  the call ledger, the observation inbox, the HITL store, and the Room Epoch
  play the role pi gives to sessions/transcripts/async runs: any interrupted
  step is re-entrant and recovered by the recovery cycle.

The consequence for this plan: there is no second engine to fall back to. An
unsupported envelope, an unknown webhook, or an unowned interaction is a hard
error or a synthesized answer — never a hand-off to a deleted executor.

## Prerequisites (hard gates)

Plan 5 starts only after **all** of the following hold:

1. **Plan 4 steps 8–9 are closed.** Feature flags/canary rollout and the
   rollback manual exercise are complete, and the cutover DRI has signed off.
2. **Legacy-owned Runs have drained.** No in-flight Run is owned by the legacy
   executor: `room_agent_messages`/legacy run rows with no
   `extend_info.orchestrator_run_id` and no pending task state reach zero, and
   the legacy stale-task checker has nothing left to recover.
3. **Legacy HITL interactions have drained.** No open legacy
   `HITLManager`-owned interactions remain; every answer is applied or
   superseded.
4. **Legacy callbacks have drained.** No inbound webhook/relay message still
   correlates to a legacy run.
5. **Routing is 100% orchestrator.** `orchestrator_routing_enabled=true`,
   ratios are 100/100, kill switch off, and the canary window (§8.2 of the
   Plan 4 doc) shows no regression for a full stability window.

The order below is a dependency chain: dual routing goes first (it is the only
thing that still references both engines), then the two executors, then the
deterministic-semantics machinery they alone used, then the compatibility
surface.

---

## Phase 1 — Stop dual routing

Delete the ownership decision and every legacy fallback. After this phase a
new Run is always created and owned by the orchestrator; there is no second
owner value.

### 1.1 Remove the ownership seam

- `execution/orchestrator_routing.py`
  - delete `OWNER_LEGACY` / `OWNER_ORCHESTRATOR` constants,
    `DualRuntimeRouter.assign_runtime` (the whole allowlist/ratio/hash
    decision), `_SERVABLE_SCOPE_SOURCES`, and `_allowlist`; also update the
    module `__all__` (`:1046-1053`) which currently exports `OWNER_LEGACY`,
    `OWNER_ORCHESTRATOR`, and `_SERVABLE_SCOPE_SOURCES`.
  - the router becomes a thin `process_room_user_message` adapter (keep the
    `RoomMessageEnvelopeResolver`, profile resolution, and preflight).
- `execution/facade.py` — every owner-resolution site, not just the message
  path:
  - `_route_orchestration` (`:812`) and its `owner = OWNER_LEGACY` default +
    `UnsupportedEnvelopeError` legacy fallback;
  - `schedule_recovery_orchestration` (`:932-944`) — recovery ingress;
  - `cancel` (`:1078-1092`) — cancellation ingress;
  - `resolve_hitl_batch` (`:1291-1303`) — HITL ingress.
  After this phase the facade calls the orchestrator router directly; an
  unsupported envelope is a hard error or a synthesized answer, never a
  hand-off to a deleted executor. Remove `OWNER_LEGACY` / `OWNER_ORCHESTRATOR`
  imports.
- `container.py` — remove the dual-routing wiring surface:
  `app.state.orchestrator_routing` assignment and the
  `bind_orchestrator_router` seam (`facade.py:540`); the facade binds the
  orchestrator router unconditionally.

### 1.2 Remove the routing flags

- `common/config/settings.py`
  - delete `orchestrator_routing_enabled`, `orchestrator_fast_ratio`,
    `orchestrator_ultimate_ratio`, `orchestrator_user_allowlist`,
    `orchestrator_room_allowlist`, `orchestrator_kill_switch`, and their
    validators/parsers (`:112-117`, `:422-449`).
  - keep the typed profile/model/budget settings the kernel still reads; only
    the *routing* flags go away.

### 1.3 Collapse ingress to a single owner

- `api_gateway/routes/webhook_routes.py`: remove the `OWNER_LEGACY` default
  (`:79`) and the `OWNER_ORCHESTRATOR` branch (`:95`); correlation always
  resolves to an orchestrator call.
- Hub relay and HITL/cancel ingress: remove the owner discriminator; all
  entries correlate by durable call/invocation lineage
  (`runtime_generation` is now always `"orchestrator"`).
- `jobs/stale_task_checker.py`: remove the legacy-orphan branch
  (`_recover_orphaned_messages`); keep the orchestrator recovery bindings
  (`_recover_stuck_orchestration_runs`,
  `_recover_claimed_orchestration_envelopes`).

---

## Phase 2 — Delete the legacy Fast path

Delete only the modules that exist for direct/Fast execution. Shared ingress
modules (`response_handler.py`, `agent_message_processor.py`, and the
`transports/relay|webhook|base.py` family) also serve webhook/relay ingress,
which is converged in Phase 5 — they must be *trimmed*, not deleted, here.

- `execution/orchestration/queue_executor.py` (`QueueExecutor`) — delete.
- `execution/dispatch/transports/direct.py` and the direct-chat transport
  branch — delete.
- `execution/dispatch/agent_dispatcher.py` — delete if it is direct-only after
  trimming; verify with `rg` before deleting.
- Legacy queue recovery (the fast-path recovery inside the stale-task checker
  and any `queue` recovery module) — delete.
- `execution/orchestration/room_message_center.py`
  (`RoomMessageCenter.process_room_user_message`): remove the Fast/direct
  branch. `room_message_center.py` is the shared node that imports both
  `QueueExecutor` (`:35`) and `SupervisorExecutor` (`:37`); it must be edited
  to drop each executor as it is deleted.

---

## Phase 3 — Delete the legacy Ultimate path

- `execution/orchestration/supervisor_executor.py` (`SupervisorExecutor`) —
  delete.
- `execution/orchestration/room_supervisor_service.py`
  (`RoomSupervisorService`) — delete.
- `execution/orchestration/planner.py` (the planner loop),
  `planner_recovery.py`, `planner_prompt.py`, and the planner rejection
  recovery — delete.
- `execution/orchestration/synthesis_coordinator.py`
  (`SynthesisCoordinator`, a `CoordinatorSynthesisPort` invoked from
  `room_message_center.py:1896`) — delete only once `room_message_center.py`
  no longer calls it; it is shared by the legacy Fast+Ultimate entry, not
  supervisor-only.

After Phases 2–3, `room_message_center.py` no longer references either legacy
executor and can itself be deleted (or reduced to the parts the orchestrator
adapter still uses).

---

## Phase 4 — Delete the legacy deterministic-semantics machinery

The new architecture lets the LLM decide semantic completion; the runtime only
enforces verifiable engineering constraints (idempotency, CAS, cancellation,
recovery, HITL, timeout, Room Epoch). Delete the rule-engine artifacts that
tried to decide "is the task semantically done":

- `execution/orchestration/goal_fingerprinting.py` (goal fingerprints)
- `execution/orchestration/goal_progress.py`
- `execution/orchestration/completion_policy.py` (completion gate)
- `execution/orchestration/outcome_policy.py` / `outcome_evaluator.py`
  (completion evidence)
- `execution/orchestration/blocker_matching.py` / `blocker_resolver.py`
  (obligation/blocker resolution)
- `models/orchestration.py` and `models/supervisor.py`: remove the
  `obligations`, goal-fingerprint, completion-evidence, and completion-gate
  fields and their serializers; keep the DTOs still used by the new path.

### 4.1 Legacy module disposition (complete list)

Enumerate (rather than rely on catch-alls) the remaining legacy-only
`execution/orchestration/` and `execution/dispatch/` modules. Each is deleted
once its only importers are gone; `rg` in the same commit proves zero
references:

- `execution/orchestration/`: `action_validator.py`, `recovery_policy.py`,
  `continuation_policy.py`, `dispatch_payload.py`, `file_turn.py`,
  `result_ingestor.py`, `failure_classifier.py`, `terminal_summary.py`,
  `agent_observation.py`, `context_builder.py`, `candidate_scope.py`,
  `run_store.py`, `run_reducer.py`.
  - `run_store.py`/`run_reducer.py` back the legacy `OrchestrationRunState`
    (schema v2) and are still imported by `execution/facade.py:44-48`,
    `execution/cancellation/finalizer.py:9-10`,
    `execution/hitl/adapters.py:5-6`, and
    `execution/dispatch/agent_ingress_router.py:17`; they are deleted only
    after the HITL/cancellation adapters stop referencing them (Phase 5).
- `execution/dispatch/`: `transports/relay.py`, `transports/webhook.py`,
  `transports/base.py`, `dispatch_middleware.py`,
  `middleware/cloud_health.py`, `middleware/hub_transport.py`,
  `agent_ingress_router.py`, `task_notifications.py`.
  (`agent_event.py` and `a2a_interaction.py` are shared via `facade.py:32-33`
  and are left alone.)

---

## Phase 5 — Converge HITL, callbacks, and data compatibility

- Remove the legacy HITL route/alias (`HITLManager`-owned interactions), the
  legacy cancellation finalizer, and the dual callback routing; the durable
  orchestrator HITL store and continuation recovery are the only remaining
  path.
- Delete the shared ingress modules trimmed in Phase 2 only now
  (`response_handler.py`, `agent_message_processor.py`, and the
  `transports/relay|webhook|base.py` family).
- **Code-order rule (not just drain):** `execution/hitl/adapters.py` and
  `execution/cancellation/finalizer.py` still import the legacy
  `run_store`/`run_reducer`; delete those two adapters *before* removing
  `run_store.py`/`run_reducer.py` in Phase 4.1, or in the same commit.
  Do not parallelize Phase 5 with Phase 2–3 blindly: the shared
  `response_handler`/`agent_message_processor` modules are deleted here and
  must not leave dangling imports in earlier phases.
- **Do not blind-delete durable identity.** Preserve:
  - persisted `schema_version` discriminators (including
    `OrchestratorRunState.schema_version = 5` and the legacy
    `ORCHESTRATION_RUN_SCHEMA_VERSION = 2`);
  - `HITLRouteSnapshotV2` and its `schema_version: Literal[2]`;
  - the `orchestrator-v3-a2a` artifact owner/origin-key namespace (SHA-256
    idempotency preimage — renaming would duplicate owned artifacts);
  - A2A/API/event protocol versions and migration versions.
  Historical data must remain readable, migratable, or archivable.

---

## Phase 6 — Clean up configuration, tests, and docs

- Delete the legacy execution-mode switch: the `use_supervisor` boolean
  (`room/compat/runtime.py:121` and its ~40 usages) and the
  `execution_mode == "supervisor"` string fork (`:2042`). There is no
  `use_fast`/`use_ultimate` symbol; the Fast/Ultimate distinction is the
  profile parameter table, not a runtime switch.
- Delete/rewrite tests and fixtures that encode the seam:
  - `tests/test_orchestrator_dual_routing.py`
  - `tests/fixtures/phase9_import_allowlist.json` (imports
    `OWNER_ORCHESTRATOR`/`OWNER_LEGACY`)
  - `tests/test_api_thin_adapters.py`
  - `tests/test_orchestration_single_path_static.py`
  - `tests/test_execution_runtime_boundaries.py`
  - `tests/test_phase9_cleanup_gate.py`
  Keep tests for the surviving orchestrator runtime and the durable-identity
  pins.
- Update `System-Architecture.md` and `Orchestrator-Production-Cutover.md`
  (retire its "Plan 5 boundaries" section) so the system describes exactly one
  orchestrator.
- Remove `room_owned_collections` registrations that only protected the
  legacy cleanup path, keeping the orchestrator-owned collection set.

---

## Execution order and dependency notes

1. **Phase 1 first** — it is the only component that references both engines;
   deleting it breaks the compile-time coupling to `QueueExecutor` /
   `SupervisorExecutor`.
2. **Phase 2 → Phase 3** — the binding constraint is
   `room_message_center.py`, which imports both `QueueExecutor` (`:35`) and
   `SupervisorExecutor` (`:37`). Either executor can go first as long as
   `room_message_center.py` is edited in the same commit to drop that import.
   No Fast-only module is imported by the Ultimate path.
3. **Phase 4** after both executors are gone (the rule machinery is referenced
   only by the supervisor path; verify with `rg` before deleting).
4. **Phase 5** is gated on both data drain *and* the code-order rule above
   (legacy HITL/cancellation adapters reference `run_store`/`run_reducer`).
   Per the accepted convergence decision, the HITL/relay/cancellation
   convergence is pulled forward into the Phase 2→3 commit because
   `room_message_center.py` is the shared node the legacy HITL continuation
   adapter, the cancellation finalizer, and the hub relay service all reach
   through.
5. **Phase 6** last; it includes the doc/test sweep that declares the
   retirement complete.

Each phase lands as its own commit and runs the full backend suite plus Ruff
format/check; deleting an executor is atomic with deleting its tests and
imports so the tree never has a dangling reference.

## Validation and acceptance

- Backend full test suite green after every phase; Ruff format/check green.
- Frontend lint/tests/build green (the frontend keeps its
  Fast→direct / Ultimate→supervisor mapping until a separate product rename,
  which is out of scope here).
- `docker compose up -d --build` and `/health`.
- Real Fast and Ultimate A2A flows still work end-to-end through the product
  UI on the single orchestrator path.
- `rg` invariants (CI-enforced):
  - `QueueExecutor`, `SupervisorExecutor`, `RoomSupervisorService`,
    `OWNER_LEGACY`, `OWNER_ORCHESTRATOR`, `assign_runtime`, `use_supervisor`
    return no hits in non-test source;
  - `orchestrator_routing_enabled` / `orchestrator_fast_ratio` /
    `orchestrator_ultimate_ratio` / `orchestrator_user_allowlist` /
    `orchestrator_room_allowlist` / `orchestrator_kill_switch` return no hits;
  - `obligation`, `goal_fingerprint`, `completion_evidence` return no hits in
    execution/model code.
- Historical-data readability: a pre-retirement dump still loads (schema
  versions and protocol versions are preserved).

## Out of scope

- Renaming `direct|supervisor` in the API/product surface (frontend mapping
  stays until a dedicated product rename).
- Removing `orchestrator-v3-a2a` durable artifact identity.
- Data migration of old collections beyond "readable/archivable".
