# Minimal Durable Cancellation Plan

## Status

Implementation plan only. This replaces the previous lifecycle-v2 redesign with a focused fix for the observed Stop failure.

The plan deliberately changes only cancellation ownership, cancellation recovery, and the frontend handling needed to represent an accepted-but-not-yet-settled cancellation.

## Problem

Today Stop performs several best-effort operations without first making the owning Run durably non-runnable:

```text
Stop
  -> cancel descendant A2A/HITL work
  -> signal the process-local Session
  -> await the Kernel task
```

In the investigated incident:

1. Stop returned HTTP 200.
2. Descendant A2A streams received `CancelledError`.
3. The A2A runtime translated client cancellation into a recoverable dispatch error.
4. The Tool and Run became suspended as `waiting_external`.
5. `RoomAgentSession.abort()` accepted that nonterminal result without checking durable cancellation.
6. A late observation/recovery resumed the Run with a fresh in-memory signal.
7. The Run eventually committed `completed`.

The durable Run ended as:

```text
status: completed
cancellation_cause: null
```

The root problem is simple: descendant interruption and an in-memory signal are treated as cancellation authority, while the owning Run remains runnable.

## Required Behavior

After cancellation wins for an admitted Run:

1. The Run durably enters `canceling` before descendant cleanup or Session interruption.
2. `canceling` never returns to `running`, `waiting_external`, `awaiting_user`, or `finalizing`.
3. Normal Kernel recovery and Tool-observation application cannot resume a `canceling` Run.
4. Existing A2A cancellation and HITL abandonment finish first; only then does the reconciler terminal-close the root as `canceled`.
5. A crash while `canceling` leaves recovery work due; another worker continues cancellation.
6. Independent A2A/HITL recovery and answer projection stop after observing `canceling`.
7. The frontend keeps message/Run correlation while cancellation is pending.
8. Only durable terminal cancellation performs terminal UI cleanup.

## Scope

### Included

- one new persisted Run status: `canceling`;
- minimal immutable cancellation metadata on the Run;
- one purpose-specific Run-store CAS for claiming cancellation;
- one CAS-first helper shared by message Stop and Run-owned HITL Stop;
- recovery routing for `canceling` Runs;
- settlement/store guards that make `canceling -> canceled` the only terminal exit;
- send-boundary guards for fresh/recovered A2A work and HITL continuation;
- a durable postcondition check in Session/host cancellation;
- correct handling of `pending_reconciliation` in the frontend;
- focused unit, race, restart, and E2E tests;
- the two focused indexes needed for active-room uniqueness and crash-gap cancellation recovery.

### Explicitly Deferred

These are not required to fix the incident:

- a new admission arbiter for cancellation before Run creation;
- redesigning completion or failure ownership;
- replacing the existing projection outbox;
- new intent, receipt, certificate, or sequence systems;
- cross-collection Mongo transactions;
- a new lifecycle schema or exhaustive historical event rewrite;
- cancellation request aliases or a global request registry;
- a client protocol-version/reload gate;
- HITL answer-state redesign;
- guaranteeing rollback of remote side effects;
- guaranteeing that no already-dispatched network packet crosses the boundary after Stop.

If a deferred problem causes a separate production incident, address it independently with evidence from that incident.

## Minimal State Machine

Add `canceling` to `RunStatus`:

```text
queued ───────────────┐
running ──────────────┤
waiting_external ─────┼── cancellation CAS ──> canceling ──> canceled
awaiting_user ────────┘
```

Existing terminal and completion behavior stays unchanged:

```text
finalizing -> completed
running/waiting_external/... -> failed | budget_exhausted  # existing behavior
completed | failed | canceled | budget_exhausted           # absorbing
```

Cancellation does not take ownership from `finalizing` or a terminal Run. If completion or another terminal transition wins first, the cancellation API returns the durable winner instead of rewriting it.

### Transition Rules

1. `queued`, `running`, `waiting_external`, and `awaiting_user` may transition to `canceling`.
2. `canceling` may transition only to `canceled` or remain `canceling` during retry.
3. `finalizing`, `completed`, `failed`, `canceled`, and `budget_exhausted` reject a new cancellation transition and return their current state.
4. Repeating the same cancellation command against `canceling` or `canceled` is idempotent.
5. Generic Run mutation must reject any candidate that changes `canceling` back to a normal state.
6. Existing optimistic-CAS version checks remain the race boundary. A stale worker holding a pre-cancel Run version cannot checkpoint after the cancellation CAS increments `state_version`.

### Enforce the exit in existing transition functions

Do not rely only on callers to respect the state machine:

1. `evaluate_terminal_decision()` and `commit_terminal_decision()` treat `canceling` as `terminal_conflict`; completion cannot construct `completed` from it.
2. `commit_terminal_status()` rejects `failed` and `budget_exhausted` when the current Run is `canceling`. It accepts only a `canceled` request whose cause matches the persisted cancellation metadata.
3. `transition_after_terminal_evaluation()` rejects `canceling`; it cannot write `waiting_external` or `awaiting_user` from that state.
4. Both Run-store `cas_mutate()` implementations provide a final guard: when persisted state is `canceling`, a candidate may remain `canceling` or become matching `canceled`, and no other status is accepted.
5. Add focused tests at both the pure transition layer and store layer. No new terminal-owner abstraction is introduced.

## Persisted Run Fields

Add only the metadata required to identify and recover the winning cancellation:

```python
status: RunStatus  # includes "canceling"
cancellation_command_id: str | None
cancellation_requested_at: datetime | None
cancellation_cause: CancellationCause | None
recovery_claim.kind: Literal["execution", "cancellation"] = "execution"
```

Rules:

1. All three fields are required for `canceling`.
2. The `canceling -> canceled` transition preserves them unchanged.
3. Historical schema-v6 `canceled` Runs may lack command ID and request timestamp; they remain readable and terminal but cannot be mistaken for a pending cancellation.
4. Canonical `canceled` Runs continue to require `cancellation_cause` under the existing contract.
5. The new command/time fields are null for non-cancellation states and immutable once present.
6. `cancellation_command_id` is deterministic for the Run and cause, for example:

   ```text
   cancel:<run_id>:<cause>
   ```

7. No Run backfill or schema-v7 migration is required.

## Run Store Contract

Add one purpose-specific repository method rather than teaching every caller to construct the transition:

```python
async def request_cancellation(
    run_id: str,
    *,
    expected_state_version: int,
    command_id: str,
    cause: CancellationCause,
    requested_at: datetime,
) -> RunStoreResult: ...
```

The repository operation:

1. matches the exact `run_id` and `state_version`;
2. accepts only the four cancelable states;
3. writes `status="canceling"` and immutable cancellation metadata;
4. sets the embedded recovery mirror to `kind="cancellation"`, due now, with no owner;
5. increments `state_version` and `updated_at` atomically;
6. records/replays the deterministic command using the existing command-id mechanism.

After that Run CAS, the store idempotently updates the existing dedicated recovery row to the same cancellation kind, due now, and clears the displaced execution owner. This second write is intentionally not claimed as atomic with the Run CAS.

On CAS conflict, the service reloads once and classifies the durable result:

| Stored state | Cancellation result |
|---|---|
| `canceling` with same command | accepted/replayed, reconciliation pending |
| `canceled` with same command | canceled |
| `finalizing` / `completed` | completion already owns the Run |
| `failed` / `budget_exhausted` | terminal failure already owns the Run |
| different cancellation command | conflict; preserve first winner |
| cancelable state with newer version | retry the transition with the same command |

Do not add a generic terminal-owner framework in this change.

## Cancellation Routing

Replace the current order in `route_cancellation_by_user_message`.

### Current order

```text
cancel HITL
-> cancel A2A descendants
-> abort owning Run
```

### Required order

```text
resolve owning Run
-> request_cancellation CAS
-> if canceling won:
     signal live Session
     cancel A2A descendants
     abandon owning HITL
     reconcile Run terminal closure
-> return canceled or pending_reconciliation
```

Rules:

1. No Run-owned A2A or HITL cancellation occurs before the owning Run reaches `canceling`.
2. Session signaling is an optimization after the durable winner, never the winner itself.
3. Descendant cleanup remains idempotent and continues to use:
   - `A2ACancellationCoordinator.cancel_run()`;
   - the A2A HITL `abandon` path;
   - the existing unified-manager `_hitl_message_cancellation.cancel_requests_for_message()` path, moved after the Run CAS for canonical supervisor interactions;
   - the existing terminal closure plan.
4. Failure to finish descendant cleanup does not revert `canceling`.
5. `route_cancellation_by_user_message()` returns the existing `CancellationAck`, derived first from durable Run state; descendant results only decide whether reconciliation is still pending.
6. Remove `_orchestrator_cancellation_ack(dict)` as the source of truth because an empty/finished descendant set does not prove the Run is canceled.
7. If synchronous reconciliation cannot finish, return `pending_reconciliation`; recovery remains due.
8. Only an interaction with `orchestration_run_id is None` is runless and exempt. `source=supervisor` is not an exemption.
9. Do not introduce new descendant lease or certificate types in this fix.

### Apply the same order to every Run-owned Stop entry

Create one small CAS-first cancellation service entry. The message router uses it and then runs `interrupt/cleanup -> reconcile`; Run-owned HITL paths use the same entry before mutating the interaction:

1. `ExecutionFacade.cancel()` removes its pre-router `_hitl_message_cancellation.cancel_requests_for_message()` call for orchestrator-owned messages. The CAS-first service calls that existing cleanup only after cancellation wins, so canonical supervisor HITL is still closed.
2. `route_cancellation_by_user_message()` resolves the Run and calls the helper.
3. `cancel_hitl_interaction()` still validates room, interaction identity, and expected revision first, then calls the same helper for `route.orchestration_run_id` before `hitl_port.abandon()`, A2A cancellation, or public HITL closure.
4. If `finalizing`, `completed`, `failed`, or `budget_exhausted` already won, both paths return that result and perform no descendant cleanup.
5. A Run-owned HITL cancellation does not call `_wake_after_hitl_resume()` after cancellation wins; cancellation reconciliation owns the next action.
6. If router lookup falls back to the unified HITL manager, the manager inspects the persisted interaction before mutation. A non-null `orchestration_run_id` identifies canonical supervisor HITL and must invoke the same Run cancellation CAS first.
7. Replace the canceled branch of `terminalize_canonical_hitl_run`/its manager callback with this CAS-first request. The manager may mark the interaction canceled only after the Run reaches `canceling`; then the shared cancellation reconciler performs remaining cleanup and root settlement.
8. Only interactions with null `orchestration_run_id` retain direct manager cancellation. Public source labels such as `supervisor` or `agent` do not determine ownership.

## Cancellation Reconciliation

Use a small cancellation branch in the existing generic Run recovery worker.

```python
if run.status == "canceling":
    await reconcile_cancellation(run)
else:
    await kernel.run(...)
```

Because the current Mongo store uses a dedicated recovery document as scheduling authority, add one narrow repair step for the Run-CAS/dedicated-row crash gap:

1. `recover_generic_runs()` calls `repair_canceling_recovery(limit)` before `list_due_runs()`.
2. The repair query returns only `canceling` Runs whose dedicated row is missing or has `kind!="cancellation"`; use a bounded aggregation/read join or equivalent repository query. Correct cancellation rows—including leased or backed-off rows—do not consume the repair limit.
3. For each returned defect, idempotently replace dedicated scheduling metadata with cancellation kind, due now, and no owner.
4. A stale execution worker cannot release the replaced row because its owner no longer matches, and its Run checkpoint loses against `canceling`.
5. The optional `RecoveryClaim.kind` defaults to `execution`, so existing rows remain readable.
6. After repair completes for this cycle, call the existing `list_due_runs()` and claim path. A crash during repair is retried next cycle.

No cross-collection transaction is required: the Run state is authoritative, repair precedes due selection, and the query budget is spent only on actual scheduling defects.

`reconcile_cancellation(run)` performs existing operations in this order:

1. Reload the Run and verify `status == "canceling"` plus the same cancellation command.
2. Signal a matching live Session through the signal-only operation; this cannot settle root.
3. Call the existing A2A cancellation coordinator for unresolved descendants.
4. Close unresolved Run-owned HITL through the existing A2A HITL port and unified-manager cancellation path; ownership is selected by persisted `orchestration_run_id`, not source label.
5. If any A2A call remains `cancel_pending`, any Run-owned HITL remains open, or the active Session has not stopped normal work, leave the Run `canceling` and retry; do not terminalize root.
6. Only after those existing cleanup checks pass, call the Kernel terminalization/terminal-closure path with:

   ```python
   status="canceled"
   cancellation_cause=run.cancellation_cause
   reason="cancellation requested"
   ```

7. Reload the Run.
8. If it is `canceled`, release recovery with no next attempt.
9. If it remains `canceling`, release with bounded retry/backoff.
10. If it became any other state, treat that as an invariant error; do not run the normal Kernel.

This is intentionally not a new worker, lease model, or outbox. It is one status-based branch in the recovery infrastructure already used by Runs.

### Retry Policy

- Reuse the existing recovery lease and retry policy after the cancellation scheduling row is repaired.
- Cancellation retries should happen promptly but remain bounded by the existing worker cadence.
- Do not quarantine on the first remote cancellation failure.
- Quarantine only under the existing repeated invariant-failure policy.
- A remote endpoint that never acknowledges cancellation must not keep the root open forever if existing local terminal closure already permits an unresolved child to be marked canceled.

## Kernel and Observation Guards

The durable state must be checked at mutation boundaries, not only at HTTP ingress.

### Kernel `run()`

Immediately after loading the Run and before deadline handling, active-attempt repair, Tool terminal publication, interaction presentation, or model work, stop normal execution when status is `canceling` and return an internal `cancellation_pending` result without terminalizing the Run.

A cancellation signal follows the same path only after reloading and confirming `canceling`. It stops the active task but does not construct root settlement. Only the cancellation reconciler may call `_terminate(..., status="canceled")` after descendant cleanup succeeds.

### Kernel `observe_tool()`

Add `canceling` to the rejected states before applying an observation. A late observation may still be stored by the A2A inbox/ledger for audit or child reconciliation, but it cannot be applied to the owning Run and cannot trigger model execution.

### Checkpoints

For ordinary Kernel checkpoints:

1. CAS continues to require the previously loaded `state_version`.
2. On conflict, reload.
3. If the winner is `canceling`, stop normal work, return internal `cancellation_pending`, and leave cancellation recovery due.
4. Never rebuild a normal candidate or terminalize root from the reloaded `canceling` Run; the reconciler owns settlement after cleanup.

This uses the existing Run CAS. No new transaction or fencing-token system is required for the incident.

### A2A client cancellation classification

Do not translate a client cancellation signal into `RecoverableEpochError` and then `ToolSuspension(status="waiting_external")`.

Introduce a narrow internal control outcome/exception such as `ClientCancellationRequested`:

1. `_dispatch_with_claim_heartbeat()` raises it when the cancellation signal wins.
2. The generic recoverable-error handler does not convert it to suspension.
3. The Kernel catches it, reloads the Run, and:
   - returns internal `cancellation_pending` without terminalizing when the Run is `canceling`;
   - otherwise treats it as an unexpected control-path error rather than silently suspending.

Lease loss, network failure, and provider interruption keep their existing recoverable semantics.

### Existing remote dispatch boundaries

Kernel guards do not cover independent A2A recovery or HITL continuation workers. Add a read-only Run-state check immediately before their existing transport send calls:

1. `RunPreparedInvocationSnapshotReader` returns no dispatchable snapshot/invocation for `canceling` or terminal Runs.
2. `RunBackedDispatchRecovery.__call__()` reloads the Run immediately before `recover_dispatch`; `canceling` suppresses dispatch and makes existing cancellation coordination due.
3. Fresh A2A dispatch performs the same reload immediately before the transport adapter call, so accepted work waiting locally is not newly sent after the worker observes `canceling`.
4. HITL answer handling reloads the owning Run before `_ensure_resumed_projection()` or the equivalent supervisor answer projector. `canceling` or terminal state suppresses new `hitl_response`, `run_resumed`, continuation command creation, and wake; return the existing canceled/stale result.
5. `emit_hitl_resolved_events(status="responded")`/the canonical control publisher rechecks immediately before publishing `run_resumed`. If cancellation won after response projection, suppress resumed and let cancellation reconciliation close the interaction.
6. HITL continuation delivery rechecks `route.orchestration_run_id` again immediately before transport dispatch. `canceling` suppresses delivery and does not call `_wake_after_hitl_resume()`.
7. Apply the same pre-projection and pre-send checks to canonical supervisor HITL because it also has `orchestration_run_id`.
8. These checks do not reject inbox persistence. Late observations may still be recorded in the existing ledger/audit path, but `observe_tool()` cannot apply them to a `canceling` Run.
9. These are checks at existing projection/control/send boundaries, not a new lease/fencing system. They do not claim to recall an event or packet already accepted before the cancellation CAS.

## Session and Host Postcondition

`RoomAgentSession.abort()` currently signals and awaits work but accepts `waiting_external` as a successful result. Change its contract:

Replace cancellation use of `abort()`/`abort_run()` with a signal-only operation such as `interrupt_run(run_id, cancellation_command_id)`:

1. It is called only after the Run cancellation CAS wins.
2. A matching hosted Session sends the in-memory signal to active work and may wait for that task to become idle within the existing bound.
3. It never starts `kernel.run()` or `kernel.terminalize()`.
4. A non-hosted or already-idle Run is a successful no-op; durable recovery owns subsequent work.
5. After an active task returns, reload and require durable `canceling` or `canceled` with the expected command. In normal ordering it remains `canceling` because only the reconciler may settle root.
6. `running`, `waiting_external`, `awaiting_user`, or `finalizing` is an invalid postcondition.
7. Timeout leaves the Run `canceling` and due; it does not clear recovery work.

Keep shutdown interruption separate: it may retain its current nonterminal rescheduling semantics. Remove cancellation call sites that use the terminalizing `abort_run()` behavior.

## API Behavior

Keep the existing `CancellationAck`; do not introduce a new public response union in this fix.

Use these meanings consistently:

| Durable result | `CancellationAck` |
|---|---|
| Run is `canceling` | `status="cancellation_pending"`, `cancellation_applied=true`, `reconciled=false` |
| Run is `canceled` | `status="canceled"`, `cancellation_applied=true`, `reconciled=true` |
| Run already completed/finalizing | current terminal/completion status, `cancellation_applied=false`, `reconciled=true` |
| Run already failed/budget exhausted | current terminal status, `cancellation_applied=false`, `reconciled=true` |

The route continues to expose `outcome="pending_reconciliation"` when reconciliation is incomplete.

Update the existing frontend `cancelMessage()` return type so `status` also accepts `cancellation_pending` and `outcome` accepts `pending_reconciliation`. No new request body, global request ID, OpenAPI union, or client protocol version is required.

## Frontend Behavior

### Stop response

In `useRoomActions.ts`, branch on `outcome`, not on truthiness of `status`. Handle `pending_reconciliation` before `cancelAllNonTerminal()`, terminal message stamping, or any lifecycle cleanup.

For `pending_reconciliation`:

1. Keep `messageId`, `clientRequestId`, active Run correlation, placeholder, and processing state.
2. Keep the room `cancelling` flag true.
3. Show `Stopping...`.
4. Do not call `cancelAllNonTerminal()`.
5. Do not mark the user message terminal.
6. Do not call `markProcessingResolved()` or `stopProcessing()`.
7. Arm the existing timeout only as a warning; timeout must not erase correlation or claim the Run stopped.
8. Request reconciliation/snapshot while continuing to listen for the terminal event.

For `canceled`, preserve the current terminal cleanup path, but prefer durable SSE/snapshot settlement when available.

For `already_terminal`, reconcile from the database and apply that terminal state; do not synthesize canceled.

### Processing status

A nonterminal processing update received while local cancellation is pending must not clear the pending Stop state or display normal forward progress as if Stop failed. It may be ignored until reconciliation.

Terminal canceled processing status remains responsible for final cleanup:

- resolve processing;
- disarm timeout;
- clear correlation;
- close remaining UI cards according to durable terminal data;
- remove placeholder/stream state;
- show `Request stopped`/the existing canonical cancellation copy.

### Refresh/reconnect through existing `active_runs`

Do not extend the canonical room-event snapshot: its `runs` section currently stores terminal projections only. Reuse the existing room/inquiry `active_runs[]` response instead.

1. Backend active-Run reads include `canceling` and continue returning its existing `state` plus `trigger_message_id`.
2. `ActiveRunRefWire` already contains `state`; update the narrower `ProcessingSnapshotRoom.active_runs` type in `useProcessingRestore.ts` so it does not discard that field.
3. When the matching active Run has `state="canceling"`, restoration sets:

   ```text
   processing = true
   cancelling = true
   label = "Stopping..."
   ```

4. Restore `messageId` from `trigger_message_id` and pass the hydrated user entity's `clientRequestId` to existing `lifecycle.startProcessing(messageId, clientRequestId)`.
5. Continue normal active-Run restoration for other states.
6. Do not add a new snapshot or lifecycle schema.

## Index Changes

Only two focused index changes are required:

1. Replace the active-room unique partial index with `orchestrator_active_room_unique_canceling`, using the same `(room_id)` key and the current nonterminal status set plus `canceling`.
2. Add `orchestrator_canceling_recovery` on `(updated_at, run_id)` with partial filter `status="canceling"` for the bounded crash-gap repair scan.

Deployment order:

1. Create and verify both indexes before code can write `canceling`.
2. Switch active-room readiness metadata to the replacement name.
3. Drop the old active-room index only after the replacement is verified.
4. Do not change client-request, projection, event, HITL, or receipt indexes.

## Expected Code Areas

Backend:

- `backend/execution/orchestrator/models.py`
- `backend/execution/orchestrator/persistence.py`
- `backend/execution/orchestrator/ports.py`
- `backend/execution/orchestrator/in_memory.py`
- `backend/dal/orchestrator/run_store.py`
- `backend/execution/orchestrator/kernel.py`
- `backend/execution/orchestrator/settlement.py`
- `backend/execution/orchestrator/session.py`
- `backend/execution/adapters/session_host.py`
- `backend/execution/orchestrator_routing.py`
- `backend/execution/facade.py`
- `backend/execution/orchestrator/a2a_runtime/runtime.py`
- `backend/execution/orchestrator/a2a_runtime/preparation.py`
- `backend/execution/orchestrator/a2a_runtime/recovery.py`
- `backend/execution/orchestrator/a2a_runtime/hitl.py`
- `backend/execution/orchestrator/a2a_runtime/interaction_outcome.py`
- `backend/execution/hitl/service.py`
- canonical supervisor callbacks in `backend/container.py`
- `backend/orchestrator_composition.py`
- focused backend tests and protocol inventories

Frontend:

- `frontend/src/hooks/room/useRoomActions.ts`
- `frontend/src/hooks/room/useRoomActions.test.tsx`
- `frontend/src/hooks/room/processing-lifecycle.ts`
- `frontend/src/hooks/room/sse-handlers/handlers/processing-status.ts`
- `frontend/src/hooks/room/useProcessingRestore.ts`
- `frontend/src/lib/api/sse.ts`
- focused Stop UI tests

Documentation after implementation:

- `backend/docs/System-Architecture.md`
- `frontend/docs/System-Architecture.md` if snapshot/UI lifecycle behavior changes

## Implementation Phases

### Phase 1: Durable Run winner

1. Add `canceling` and the three optional cancellation fields.
2. Add optional recovery kind and update model validation plus active/recovery status sets.
3. Add `request_cancellation()` and pre-due-query cancellation scheduling repair to the Run-store protocol and both implementations.
4. Add the replacement active-room index and focused canceling-recovery index.
5. Add repository tests for accepted, replayed, CAS-loser, terminal-winner, and Run-CAS/dedicated-row crash-gap outcomes.

### Phase 2: Route and recover cancellation

1. Route both message Stop and Run-owned HITL Stop through the shared CAS-first helper; remove facade pre-cancel and post-cancel continuation wake.
2. Add the `canceling` branch to generic recovery.
3. Enforce canceling exits in settlement functions and Run-store CAS.
4. Add Kernel run/observation/checkpoint guards; active Kernel returns cancellation pending instead of settling root.
5. Separate client cancellation from recoverable transport/epoch failure.
6. Add canceling checks before HITL answer projection/run-resumed and at fresh A2A, A2A recovery, and HITL continuation send boundaries.
7. Replace cancellation use of Session/host abort with signal-only interruption.
8. Route canonical supervisor HITL by non-null orchestration_run_id through CAS-first cancellation.
9. Reuse existing descendant cleanup and terminal closure; only reconciler settles root after cleanup.
10. Add restart, late-observation, independent-dispatch, supervisor-HITL, and answer-projection tests.

### Phase 3: Frontend pending behavior

1. Branch Stop handling on `outcome`.
2. Preserve correlation for `pending_reconciliation`.
3. Stop optimistic terminalization and `cancelAllNonTerminal()` in the pending path.
4. Restore `canceling` as `Stopping...` from existing `active_runs[].state` and hydrated user-message correlation.
5. Update focused unit and E2E tests.

### Phase 4: Documentation and deployment

1. Update system architecture documentation to describe `canceling` and recovery.
2. Create/verify the replacement active-room index.
3. Deploy backend and frontend together.
4. Monitor Runs remaining in `canceling`, cancellation retry count, and cancellation latency.
5. Keep rollback simple: old readers must tolerate the optional fields, but do not roll back to a binary whose `RunStatus` rejects persisted `canceling` records.

## Test Plan

### Run-store tests

1. `running -> canceling` persists all metadata and advances version.
2. `waiting_external -> canceling` is accepted.
3. `awaiting_user -> canceling` is accepted.
4. Same command against `canceling` replays.
5. Different command cannot replace cancellation metadata.
6. `finalizing`/terminal states are not rewritten.
7. A stale normal checkpoint loses after cancellation CAS.
8. Validation rejects missing or mutable metadata on `canceling` but accepts historical canceled schema-v6 documents without new command/time fields.
9. A crash after the Run CAS but before dedicated-row update is repaired before due selection.
10. Repair query returns only missing/wrong-kind rows; correct leased/backoff cancellation rows do not consume its limit or starve later defects.
11. Recovery cycle invokes repair before `list_due_runs()`.
12. The repair scan does not steal a live cancellation lease.
13. Due-run queries include `canceling` with recovery kind cancellation.
14. Completion construction, failed/budget terminalization, and terminal evaluation all reject `canceling`; only matching canceled terminalization succeeds.
15. Run-store fallback guard rejects a candidate that changes `canceling` to any state except matching `canceled`.
16. Active-room uniqueness remains enforced while one Run is `canceling`.

### Routing and Session tests

1. Assert call order: Run cancellation CAS before Session signal, A2A cancellation, and HITL abandonment.
2. If the CAS loses to completion, no descendant cancellation is started.
3. Descendant cleanup failure leaves the Run `canceling` and due.
4. Signal-only Session/host interruption never starts Kernel or terminalizer, including for idle/non-hosted Runs.
5. Active Kernel returns cancellation pending; root remains canceling while any A2A/HITL cleanup is pending.
6. Crash after interruption or partial child cleanup resumes reconciliation and does not observe a prematurely canceled root.
7. Cancellation with no active descendants reaches `canceled` through the reconciler.
8. Pending remote cancellation returns `pending_reconciliation`.
9. Facade message Stop performs no HITL mutation before router CAS.
10. Run-owned A2A HITL Stop uses the CAS-first service; a completion winner causes no abandon/A2A cleanup.
11. Unified-manager canonical supervisor HITL with non-null orchestration_run_id uses the CAS-first service before interaction mutation.
12. Only null-orchestration-run HITL uses direct manager cancellation.
13. Run-owned HITL cancellation never wakes continuation after cancellation wins.

### Kernel and A2A tests

1. Kernel loaded with `canceling` performs no model, Tool, retry, or interaction work.
2. Tool observation against `canceling` cannot resume the Run.
3. Cancellation signal is not converted to `waiting_external`.
4. Network/lease failures still use existing `waiting_external` recovery.
5. A stale worker checkpoint after cancellation loses CAS and does not retry as normal progress.
6. Late terminal A2A observation may settle/audit the child but cannot change the root winner.
7. Recovery restart of `canceling` continues terminal closure and reaches `canceled`.
8. Accepted A2A recovery paused before transport does not dispatch after Run becomes `canceling`.
9. Fresh A2A dispatch paused at the transport boundary does not send after `canceling` is observed.
10. A2A HITL answer paused before response projection emits no new hitl_response or run_resumed after observing canceling.
11. Cancellation winning between responded projection and run_resumed suppresses run_resumed.
12. Canonical supervisor answer follows the same projection guards.
13. HITL continuation paused before transport does not dispatch or wake the Run after canceling.

### Frontend tests

1. `pending_reconciliation` keeps message/client/Run correlation and processing state.
2. Pending response does not call `cancelAllNonTerminal()`.
3. Pending response does not mark the user message canceled.
4. Terminal canceled event performs cleanup once.
5. `already_terminal=completed|failed` does not render canceled.
6. Existing `active_runs[].state=canceling` restores `Stopping...` after refresh.
7. Cancellation timeout warns without clearing correlation.
8. `cancelMessage()` accepts the existing pending status/outcome returned by the backend.
9. `useProcessingRestore` retains `active_runs[].state`, restores `canceling` as `Stopping...`, and restores client request correlation from the hydrated user message.
10. Canonical snapshot DTO remains unchanged.


### E2E regression

For an in-flight Agent call:

1. Start a Run and capture its user message/client request correlation.
2. Click the existing Stop control.
3. Assert the request targets the correct message ID.
4. If the response is pending, assert the UI remains `Stopping...`.
5. Pause an A2A recovery or HITL continuation at its send boundary, then release it after durable `canceling`; assert no new remote call is sent.
6. Deliver a late working or terminal observation.
7. Assert no new normal progress, Agent call, model response, or final answer appears.
8. Assert the Run reaches durable `canceled`.
9. Assert the UI shows `Request stopped`.
10. Refresh and assert the canceled state remains stable.

Add a restart variant that stops the backend after the `canceling` CAS and before terminal closure, then confirms recovery reaches `canceled`.

## Acceptance Criteria

1. The owning Run enters durable `canceling` before descendant cleanup or Session interruption.
2. Settlement functions and Run-store CAS enforce that `canceling` can only remain canceling or become matching `canceled`.
3. Message Stop, A2A HITL Stop, and canonical supervisor HITL Stop all claim the Run first; only null-orchestration-run HITL is exempt.
4. Session interruption cannot settle root; cancellation reconciler settles only after A2A/HITL cleanup is complete.
5. Repair runs before due selection and correct cancellation lease/backoff rows do not consume its defect limit.
6. Fresh A2A dispatch, A2A recovery, HITL answer projection/run-resumed, and HITL continuation do not progress after observing `canceling`.
7. Client cancellation is never represented as `waiting_external`.
8. Late observations cannot resume the owning Run after cancellation wins.
9. Crash/restart after either the Run CAS or dedicated scheduling write resumes cancellation through the bounded repair scan and existing recovery lease.
10. Existing A2A, HITL, and terminal closure components are reused.
11. `pending_reconciliation` preserves frontend correlation and shows `Stopping...`.
12. Refresh restores `canceling` from existing `active_runs[].state` plus persisted user-message correlation; canonical snapshot schema is unchanged.
13. Only durable terminal cancellation performs terminal UI cleanup.
14. The implementation adds no lifecycle-v2 intent/receipt/certificate system, admission redesign, or new transaction infrastructure.

## Validation Commands

Backend:

```bash
cd backend
uv run ruff check .
uv run ruff format --check .
uv run pytest \
  tests/test_orchestration_run_store.py \
  tests/test_orchestrator_session.py \
  tests/test_orchestrator_session_host.py \
  tests/test_orchestrator_a2a_cancellation.py \
  tests/test_execution_facade.py
```

Frontend:

```bash
cd frontend
npm run lint
npm run test -- \
  src/hooks/room/useRoomActions.test.tsx \
  src/hooks/room/sse-handlers/handlers/processing-status.test.ts
npm run build
npx playwright test tests/e2e/authenticated-flows.spec.ts
```

Documentation-only validation while editing this plan:

```bash
git diff --check
```

Do not call real providers during automated tests. Use controlled fake A2A dispatch, cancellation, observation, and restart boundaries.
