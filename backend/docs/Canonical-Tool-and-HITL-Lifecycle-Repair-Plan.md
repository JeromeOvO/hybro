# Canonical Tool and HITL Lifecycle Repair Plan

## Status

Proposed.

## Summary

The orchestrator must use one unambiguous Assistant-message rule:

- an Assistant message with tool calls is not a final answer;
- an Assistant message without tool calls is a final answer.

The current model-first HITL implementation violates this rule when an A2A Agent
interaction is parked. A no-tool Assistant answer is first published as final,
then a degradation fallback exposes the parked Agent question and moves the Run
to `awaiting_user`. This can produce an invalid canonical sequence:

```text
message_end(final)
hitl_request
run_waiting_input
```

The invalid sequence prevents canonical snapshot folding. A later successful
HITL cancellation can therefore leave the browser showing a terminal Run as
Running.

This plan removes the degradation fallback, closes unused parked interactions
before accepting a final answer, preserves explicit HITL forwarding, and adds
the missing terminal-state convergence to the HITL Cancel action without
refactoring the normal Stop action.

## Goals

1. Make the presence or absence of tool calls the only classifier of a valid
   Assistant response.
2. Remove all runtime production of the legacy degradation fallback.
3. Close parked Agent interactions safely when the Supervisor chooses a final
   answer.
4. Prevent canonical event histories from returning to a waiting state after a
   final answer.
5. Treat cancellation of a HITL interaction as cancellation of its entire
   owning Run.
6. Make both cancellation entry points converge to the same frontend terminal
   state.

## Non-goals

- Defining a new HITL contract for third-party `input-required` responses that
  do not contain the currently required typed interaction metadata.
- Parsing arbitrary Agent prose to separate an answer from a question.
- Retrying cancellation by sending both cancellation APIs.
- Relaxing canonical snapshot validation to accept contradictory histories.
- Adding a replacement automatic fallback from model failure to user input.
- Changing how `input-required` responses without typed interaction metadata
  are normalized. That requires a separate design for interaction identity,
  question identity, fingerprints, versions, and continuation ownership.
- Changing bundled Agent prompt construction as a prerequisite for lifecycle
  correctness.

## Current Behavior

### No-tool decision with a parked interaction

The Kernel currently performs these operations in this order:

1. receive a complete Assistant response;
2. append and publicly complete the Assistant message;
3. classify a message without tool calls as `final`;
4. detect a remaining presented interaction;
5. invoke the fallback identified in code as `F5 degrade`;
6. publish the parked interaction to the user;
7. checkpoint the Run as `awaiting_user`.

The fallback uses:

- `decision_turn_inconclusive` as its reason;
- `degraded_to_user` as its model decision;
- `_degrade_presented_interactions()` to publish parked interactions.

A second branch calls the same degradation helper after a decision-turn
provider error retry is exhausted. Both branches automatically change the Run
to `awaiting_user`.

The no-tool branch treats a valid Assistant response as inconclusive even
though the message has already been classified as final. The provider-error
branch changes a failed model decision into user-facing HITL instead of closing
the failed attempt through the normal terminal path.

### HITL cancellation

The canonical HITL cancellation endpoint already attempts to:

1. claim cancellation of the owning Run;
2. cancel descendant Agent calls;
3. abandon the exact HITL interaction;
4. emit canceled HITL events;
5. settle the owning Run as canceled.

The frontend does not perform the same convergence work after both cancellation
entry points:

- normal Stop enters the cancelling state, reconciles persistence, requests a
  canonical snapshot, and waits for terminal authority;
- HITL Cancel optimistically closes the interaction component but does not run
  the complete cancellation convergence path.

If snapshot folding fails, the HITL component disappears while the room remains
Running.

## Required Invariants

### Assistant classification

For every valid Assistant response:

| Assistant response | Required result |
| --- | --- |
| Contains one or more tool calls | Execute the declared tool batch; do not finalize the answer |
| Contains no tool calls | Accept the response as final |
| Provider or protocol failure | Use the existing bounded retry or failure path |

A parked interaction must not change this classification.

### Parked interaction ownership

Before a Run can complete successfully, every accepted tool entry and every
owned Agent-call ledger record must be terminal. If a final Assistant answer
makes a parked interaction unnecessary, the closeout must:

1. terminalize the exact child Agent call without canceling the root Run;
2. abandon the exact interaction idempotently;
3. terminalize its owning Kernel tool entry;
4. record a terminal ToolResult such as `interaction_abandoned`;
5. prove that no actionable HITL interaction or recoverable child call remains;
6. publish the final message, close the shared Turn, and complete the Run.

Abandoning a child interaction does not cancel the root Run. The root Run still
completes successfully with the Supervisor's final answer.

### Canonical ordering

The event producer must enforce all of the following:

- a final `message_end` cannot be followed by `run_waiting_input` for the same
  active Run;
- a terminal Run cannot retain an open HITL interaction;
- a terminal Run cannot retain an incomplete accepted tool entry;
- every public tool start has one terminal tool event;
- every public Turn has one valid terminal Turn event;
- cancellation resolves public HITL state before `run_settled(canceled)`.

Snapshot folding remains strict and fail-closed.

### Cancellation

Canceling an actionable HITL interaction means canceling its owning Run.
Success must eventually produce one terminal authority:

```text
hitl_response(canceled)
tool and Turn cancellation closure
run_settled(canceled)
```

The normal Stop action and HITL Cancel use different request identities, but
they must converge to the same authoritative terminal client state. This does
not require them to share an action helper.

## Design

### 1. Classify before public completion

Refactor the Kernel response path so it classifies the complete Assistant
response before publishing final lifecycle events.

The existing `message_start` and streaming `message_update` behavior remains in
place. This repair does not add full-response buffering or a new recovery
protocol.

For a decision continuation associated with parked interactions, delay only
`message_completed(disposition="final")` until the complete response has been
classified and parked ownership has been reconciled for the no-tool path.

### 2. Remove the degradation fallback

Delete runtime behavior that automatically exposes parked interactions when a
valid Assistant message has no tool calls.

Removal includes:

- the no-tool fallback branch in `OrchestratorKernel.run()`;
- the provider-error exhaustion fallback in `OrchestratorKernel.run()`;
- `_degrade_presented_interactions()`;
- production of `decision_turn_inconclusive`;
- production of `model_decision.degraded_to_user`;
- tests whose expected behavior is automatic degradation;
- documentation that describes automatic degradation as supported behavior.

Remove the decision-continuation-specific provider-error branch, including its
separate retry counter. Route provider errors through the existing generic
provider-error path: close the failed canonical Turn and its parked child state,
record the notice, apply the normal bounded retry budget, and fail the Run when
that budget is exhausted. It must not publish HITL.

Provider-failure closeout must reuse the same exact-child terminalization
primitive introduced for successful closeout. A failed Run must not leave an
`input_required` child-call ledger record eligible for recovery.

`publish_parked_interaction()` must remain because the explicit
`surface_agent_questions` tool path also uses it.

Persisted historical events may continue to be readable during their supported
retention period. Read compatibility must not preserve or reintroduce the
runtime fallback. Removal of historical DTO or frontend enum values should be a
separate cleanup after stored-event retention and replay requirements are
verified.

### 3. Add successful parked-interaction closeout

Introduce a dedicated idempotent closeout operation for a successful no-tool
final decision. It should reuse the existing exact-ownership and terminal
closure machinery rather than duplicating interaction or event mutations.

The parked tool batch and the decision Assistant share the active internal
Turn. The operation must therefore preserve this order:

```text
terminalize exact child Agent call and abandon its interaction
-> checkpoint the Kernel tool entry as terminal
-> publish tool_execution_end
-> flush ToolResults into the transcript
-> publish message_end(final)
-> publish turn_end with the Turn's complete public Tool id inventory
-> settle the root Run as completed
```

The child-call transition must reuse the existing A2A runtime cancellation
mechanism for the exact `call_record_id`, or an equivalent extracted primitive
with the same durable CAS and terminal-interaction finalization. Calling
`abandon_parked_interaction()` alone is insufficient because it closes only the
HITL aggregate and does not terminalize the Agent-call ledger record.

This is a successful root closeout, not the existing failed/aborted root
termination path. It may record the unused child call as canceled or abandoned,
but it must not mark the root Run canceled or failed. It also must not emit
`turn_end` before the decision Assistant's final `message_end`.

Restart and replay behavior must be deterministic:

- repeated child cancellation and interaction abandonment accept terminal,
  replay, or already-absent outcomes;
- checkpoint command ids are stable;
- exactly one durable child terminal state and one public terminal event exist;
- a crash between child terminalization and Run completion resumes without
  duplicate public events.

### 4. Preserve explicit HITL and continuation paths

Keep `surface_agent_questions` as the only Supervisor decision that exposes a
parked Agent question to the user.

```text
surface_agent_questions tool call
-> publish exact parked interaction
-> hitl_request
-> run_waiting_input
```

Keep explicit Agent continuation as the path that answers the Agent from known
context:

```text
Agent continuation tool call
-> dispatch reply to exact parked call
-> ToolResult or next ToolSuspension
-> next model turn
```

Invalid tool calls remain tool errors. They must be returned as ToolResults and
must not trigger implicit HITL publication.

### 5. Keep protocol expansion out of this repair

The current runtime intentionally converts an `input-required` response without
typed interaction metadata into a completed ToolResult. Changing that policy
is a separate capability because it requires new durable ownership rules for
interaction ids, question ids, fingerprints, versions, and continuation.

This repair applies to interactions the current runtime already parks. Its
correctness must not depend on the length or wording of those typed questions,
and it does not require a bundled Agent change.

A separate plan is required before claiming complete third-party A2A
`input-required` compatibility without typed metadata.

### 6. Fix HITL cancellation convergence locally

Retain separate cancellation commands:

- normal Stop targets the root user message;
- HITL Cancel targets the exact interaction and expected version.

Do not call normal Stop after a successful HITL cancellation. The Run has
already been canceled by the HITL endpoint.

Keep the normal Stop implementation unchanged. Modify `cancelHitlRequest()`
locally:

1. before the request, set the room cancelling state and call
   `lifecycle.setCancelTimedOut(false)`;
2. send the version-fenced HITL cancellation command;
3. after success, retain the existing optimistic HITL update, reconcile
   authoritative room persistence, and request a forced canonical snapshot;
4. after request failure, perform the existing conflict reconciliation where
   applicable and always clear the room cancelling state before rethrowing the
   error.

Keep the request error boundary separate from post-success recovery. A
reconciliation or snapshot-request failure after the server accepted
cancellation is logged as recovery failure; it must not be reclassified as a
failed cancellation or clear `cancelling` before terminal authority arrives.

Do not extract a shared helper and do not refactor the normal Stop
`already_terminal` branch.

The existing authoritative terminal event and reconciliation handlers remain
the sole owners of:

- `markProcessingResolved()`;
- `stopProcessing()`;
- placeholder removal;
- cancellation-timeout disarming;
- clearing the room cancelling state.

The HITL interaction may be updated optimistically after the server accepts the
command, but hiding the component must not be treated as proof that the Run has
settled. On success, `cancelling` remains set until authoritative terminal
handling clears it. No API response expansion, shared cancellation abstraction,
normal Stop change, or button-label change is required for this repair.

## Implementation Areas

### Backend

Default implementation files:

- `backend/execution/orchestrator/kernel.py`
- the minimum existing A2A runtime/cancellation implementation needed to
  terminalize one exact child call without canceling the root Run

Default tests:

- `backend/tests/test_orchestrator_model_first_kernel.py`
- the existing focused A2A cancellation test file if the child terminalization
  primitive changes
- `backend/tests/test_delivery_snapshot.py` for regression coverage only

Do not change the snapshot validator unless a failing valid-history test proves
the production fold is incorrect. Do not change public Protocol or DTO
inventories as part of fallback removal unless compilation or a focused
contract test proves a runtime dependency remains.

### Frontend

Default implementation file:

- `frontend/src/hooks/room/useRoomActions.ts`

Default test:

- `frontend/tests/unit/hooks/room/useRoomActions-hitl.test.ts`

The authoritative terminal handlers are expected to remain unchanged. Expand
the frontend file list only when a focused failing test proves another owner
must change.

New symbols, filenames, tests, and documentation must use product-neutral
lifecycle terminology.

## Delivery Phases

### Phase 1: Kernel semantics

1. Add failing tests for no-tool final decisions with one and multiple parked
   interactions.
2. Add exact child-call terminalization and successful parked-interaction
   closeout.
3. Delay final `message_completed` until after child/tool closeout while
   preserving existing start and streaming update behavior.
4. Remove the no-tool degradation branch and the entire decision-specific
   provider-error branch; use the generic bounded provider-error path instead.
5. Verify explicit surface and continuation paths remain unchanged.

### Phase 2: Canonical event and recovery coverage

1. Fold the complete no-tool final event sequence through `RoomEventFold`.
2. Add crash/replay coverage around child terminalization, interaction
   abandonment, and terminal event publication.
3. Assert that no final event can precede `run_waiting_input`.
4. Fold provider retry and exhausted-failure sequences and prove neither emits
   automatic HITL.
5. Add a regression for the formerly degrading flow followed by cancel and
   snapshot folding, using newly produced valid events rather than accepting an
   invalid sequence.

### Phase 3: HITL cancellation convergence

1. Set and reset cancellation UI state locally in `cancelHitlRequest()`.
2. Reconcile and request a canonical snapshot after HITL cancellation succeeds,
   treating both as best-effort recovery after server acceptance.
3. Clear cancelling on every failed cancellation request after any applicable
   conflict reconciliation.
4. Preserve interaction version fencing and existing normal Stop behavior.
5. Keep final processing cleanup exclusively in the authoritative terminal
   event and reconciliation handlers.

### Phase 4: Contracts and documentation

1. Stop producing legacy degradation decisions.
2. Retain read-only historical parsing only where persistence requires it.
3. Update backend and frontend system architecture documents.
4. Verify no new branded lifecycle identifiers or filenames were introduced.

## Required Test Scenarios

### Kernel

1. One parked interaction plus no-tool Assistant response:
   - the exact child-call ledger record is terminal;
   - the interaction is abandoned;
   - the Kernel tool entry is terminal;
   - no `hitl_request` is emitted;
   - no `run_waiting_input` is emitted;
   - the final answer is published;
   - the Run completes.

2. Multiple parked interactions plus no-tool Assistant response:
   - every exact child-call ledger record reaches one durable terminal state;
   - every interaction reaches one durable terminal state;
   - all accepted Kernel tool entries close;
   - replay may repeat idempotent calls but does not duplicate public terminal
     events;
   - the Run completes.

3. Parked interaction plus `surface_agent_questions`:
   - only the selected interaction is published;
   - the Run enters `awaiting_user`;
   - no final answer is committed.

4. Parked interaction plus Agent continuation:
   - the reply targets the exact parked call;
   - a ToolResult or next suspension is observed;
   - the model loop continues.

5. Provider failure:
   - bounded retries still apply;
   - retry and exhausted-failure event sequences both fold successfully;
   - the exact parked child-call ledger record is terminal after final failure;
   - exhausted retries fail the Run;
   - no automatic HITL publication or degradation decision occurs.

### Cancellation

1. HITL Cancel sets cancelling before sending the request.
2. Successful HITL Cancel reconciles persistence and requests a canonical
   snapshot while terminal cleanup remains owned by authoritative handlers.
3. Failed HITL Cancel clears cancelling after any applicable 404/409/410
   reconciliation.
4. HITL Cancel closes the interaction, descendant Agent call, public Turn, and
   owning Run.
5. HITL Cancel followed by refresh produces a foldable canceled snapshot.
6. A second local Stop attempt is unnecessary after HITL cancellation.

Duplicate HITL Cancel success is not required. Existing 404/409/410 responses
and frontend authoritative refresh behavior remain the conflict contract.

## Validation Commands

Run focused backend checks from `backend/`:

```bash
uv run pytest tests/test_orchestrator_model_first_kernel.py
uv run pytest tests/test_orchestrator_a2a_hitl.py
uv run pytest tests/test_delivery_snapshot.py
uv run ruff format --check .
uv run ruff check .
```

Run focused frontend tests from `frontend/` using the repository's configured
test command for the affected files, followed by lint and production build.

After implementation, rebuild the affected containers and verify this complete
browser flow:

```text
send request
-> Agent returns input-required
-> Supervisor explicitly surfaces the question
-> user selects Cancel request
-> Run becomes canceled
-> refresh room
-> room remains canceled and composer is unlocked
```

Also verify the no-tool final path:

```text
send request
-> Agent returns input-required
-> Supervisor returns a final answer without tools
-> parked interaction closes
-> final answer remains visible
-> Run is completed
-> refresh preserves the completed state
```

## Acceptance Criteria

The repair is complete when:

- no valid no-tool Assistant response can trigger automatic HITL publication;
- the runtime no longer produces `decision_turn_inconclusive` or
  `degraded_to_user`;
- unused parked interactions close before successful Run completion;
- explicit `surface_agent_questions` remains the only model decision that
  exposes parked Agent questions;
- no newly produced canonical history contains final followed by waiting input;
- HITL Cancel terminates the entire owning Run;
- both cancellation entry points converge to an unlocked terminal UI;
- refresh and snapshot folding preserve completed and canceled outcomes;
- lifecycle correctness for currently parked typed interactions does not depend
  on bundled Agent prompt wording;
- handling untyped `input-required` responses remains explicitly out of scope;
- all focused tests, formatting, lint, and build checks pass.
