# Plan: Stable Turn IDs + Single Writer to `useTurnEventStore`

> **Line-number disclaimer.** File:line references in this document
> (e.g. `useSendMessage.ts:174`, `sse-handlers/index.ts:661-670`)
> were captured during the audits on 2026-04-17. They are indicative,
> not authoritative — re-run the Audit Checklist (`rg` commands) at
> the start of each commit to rebind symbols to their current
> positions. Do not block on a line-number mismatch; trust the
> symbol name.

## Problem Statement

The chat UI occasionally renders the agent's response twice; a refresh fixes it.
Root-cause analysis in the previous session found the bug is a symptom of a
larger design issue, not a one-off. The current architecture has:

1. **Two stores of the same truth**
   - `useMessageStore` — normalized message entities (keyed by `messageId`).
   - `useTurnEventStore` — turn-event log (keyed by `turnId`).
2. **A mutable turn id**
   - `turnId` is transiently `clientRequestId`, then `tempMessageId`, then
     `realMessageId`. Every rename is a potential inconsistency point.
3. **Two concurrent writers into `useTurnEventStore`**
   - `useMessageStoreSync` (bridge) projects message-store diffs into turn events.
   - `sse-handlers/index.ts` writes turn events directly from `turn_event`,
     `processing_status`, and other SSE messages.
4. **Reconciliation-by-sweep**
   - `useTurnEventStore.append` has an "optimistic merge" block.
   - `useMessageStoreSync.cleanupOrphanOptimisticTurns` sweeps stragglers.
   - `useTurnEventStore.removeTurn` recomputes composer state from scratch.

The duplication bug happens when the bridge and the direct SSE writer race
during the temp→real ID swap, producing two turn logs keyed by different ids
for the same conversation turn.

Key enabling observation
: The backend already returns `message_id` synchronously in the `POST /send`
  response (see `useSendMessage.ts` around line 139:
  `createResponse.message_id || createResponse.message?.message_id`), and the
  backend uses that `message_id` as the `turn_id` for all subsequent
  `turn_event` SSE messages (confirmed in `RoomMessageCenter.py`:
  `summary_turn_id = user_message_id`, and `turn_id=user_message_id` in the
  journal appends).
: **Therefore the real turn id is knowable before any SSE event arrives.** We
  don't need a rolling-alias scheme; we just need to stop using one.

## Goals

1. One stable `turnId` per turn, set once and never renamed.
2. **Exactly one** authoritative writer to `useTurnEventStore` — not two
   writers with non-overlapping event kinds.
3. Delete the reconciliation code that exists only because of goals 1 and 2
   being violated.
4. No behavior change visible to the user: optimistic render still shows up
   quickly on send (skeleton covers the POST latency); hydration still
   repopulates on refresh.

## Non-Goals

- Removing `useMessageStore`. Messages remain the authoritative store for
  arbitrary message operations (edit, delete, quote, attachments, etc.). We
  only collapse `useTurnEventStore`'s ingress.
- Backend protocol changes. Everything here is frontend-only.
- Changing hydration semantics or the `/turns` endpoint shape.

## Feature Flag

All changes land behind the existing `turnBasedTimeline` flag (see
`useRoomWebhook.ts:168` — `handleOptimisticTurn` is already gated on this
flag). Flag stays on default-off in staging, enabled progressively, and
removed only after success criteria are met in production.

### Writer-mode sub-flag — `turnEventStoreWriter`

To prevent the "two writers concurrently active" regression between
Commit 0 and Commit 4, we gate the writer wiring on a **ternary**
sub-flag with explicit states (not a boolean):

| Value (default) | Direct SSE writer | Bridge writer | When it applies |
| --- | --- | --- | --- |
| `"bridge"` **(default)** | ignores `turn_event` SSE (buffers only — see D1) | active (projects message-store → turn events) | Today's behavior. Lives while C1–C3 land (they mutate shared store shape and the bridge). |
| `"direct"` | active (single writer) | hydration-fallback only (one-shot on reset; no incremental slot emission) | Target steady state. Only flip after Commit 0 has soaked in prod and Commits 1–4 have shipped. |
| `"both-shadow"` (staging-only) | active but writes to a **shadow** store (`useTurnEventStoreShadow`); no UI impact | active (drives UI) | Optional 24-48 h staging-only validation. Projection-diff assertions (§5.B) compare shadow vs. primary per turn. Must never ship enabled to prod. |

**Ordering invariant:** `turnEventStoreWriter="direct"` is **forbidden**
until the following are all true:

1. Commit 0 is live in the same environment (i.e. backend `start_turn()`
   is being called for every persisted `RoomUserMessage`, including
   recovery and mentions paths).
2. Commits 1–3 are shipped (stable `turnId`, real `messageId` from POST,
   optimistic skeleton row).
3. Commit 4 has landed the bridge into "hydration-fallback mode" (no
   incremental `slot_*`/`hitl_*`/`turn_*` emission on this code path).

Staging gate must enforce this via a startup assertion: if
`turnEventStoreWriter === "direct"` while the bridge is still producing
incremental events (detected by a dev-only counter in
`useMessageStoreSync`), the app logs a fatal-severity warning and falls
back to `"bridge"`.

**Rollback:** flipping `turnEventStoreWriter` back to `"bridge"` must be
safe at any time — no DB migration, no one-way door. This is the
primary kill-switch if Option A regresses in production.

## Design

### D0. Pick the single writer (top-level decision — required before Commit 2)

Today there are two production writers into `useTurnEventStore`:

- **Direct SSE writer** — `src/hooks/room/sse-handlers/index.ts` `case 'turn_event':`
  (lines 661-670) plus the `processing_status` `phase_changed` + terminal
  emissions (lines 186-203, 222-264).
- **Bridge writer** — `useMessageStoreSync` projects `MessageEntity`
  deltas into `slot_*` / `hitl_*` / `turn_completed` events.

The bridge's own docstring (`useMessageStoreSync.ts:12-22`) describes
itself as a **Redis-down fallback**: *"When Redis is down the backend
cannot emit turn_event SSE; only legacy SSE events arrive — these update
the message store but leave the turn event store empty."* In reality both
writers run unconditionally whenever Redis is up, which is the root of the
duplication bug family.

We must pick one. The two real options:

#### Option A — Direct SSE is primary (RECOMMENDED)

Matches original design intent. Backend's `turn_event` journal is the
source of truth; `useTurnEventStore` is just a projection of that stream
plus local synthetic events from `processing_status`.

- **Delete** the bridge's slot/HITL event production entirely
  (`pushIncrementalUpdates`, `buildTurnEvents`'s slot loop, orphan sweep).
- Keep the bridge only for its *hydration-after-reset* role (triggered
  from `useTurnHydration.ts:71-76` when journal fetch returns zero turns
  but message store has data — the "Redis-was-down-during-original-send"
  recovery path). Gate this behind an explicit `reason: 'hydration-fallback'`
  call; no subscription-based continuous projection.
- Direct SSE handler covers `turn_event: *` (including slot events),
  `processing_status` → `phase_changed` / terminal events, and `turn_started`
  synthesized by `useSendMessage` on POST response.
- **Risk**: if backend's `turn_event` coverage is incomplete (e.g. artifact
  streaming emits `artifact_update` SSE but no corresponding
  `artifact_appended` turn event), the turn store loses those updates.
  **Mitigation**: audit backend `TurnEventAppender` call sites to confirm
  full coverage before Commit 4. Block Commit 4 on this audit.

#### Option B — Bridge is primary

- **Delete** the `case 'turn_event':` block at `sse-handlers/index.ts:661-670`.
- Backend's journal becomes a DB-only concern (still used by `/turns/recent`
  for hydration).
- Direct SSE handler keeps `phase_changed` + terminal emission (these are
  not derivable from `MessageEntity` state).
- **Risk**: drops any event kind that has no `MessageEntity` representation
  (e.g. token-level `slot_delta`). Forces all UI richness to route through
  message entities, which is a step backwards architecturally.

**Recommendation**: Option A. Rationale:

1. Aligns with the stated design intent (journal as source of truth).
2. Lets future token-streaming (`slot_delta`) land without revisiting this
   decision.
3. Bridge-as-hydration-fallback is a narrower, testable role than
   bridge-as-continuous-projection.

**Decision gate**: Operator must sign off on A vs B in this document
(add `> Decision: Option A (YYYY-MM-DD, @username)`) before Commit 2.
The rest of the plan assumes A; if B is chosen, flip Commit 2-4 file
targets accordingly (the invariants stay the same).

> Decision: **Option A-prime** (2026-04-17) — Direct SSE is the single
> writer; bridge demoted to hydration-only fallback. Backend journal
> pipeline must be fixed first (Commit 0, see *Backend coverage audit*
> section), because the current backend never emits `turn_event` SSEs
> in production.

### D1. Stable turn id from the start

Current flow (from `useSendMessage.ts`):

1. Generate `tempMessageId = temp-<rand>` and `clientRequestId = uuid()`.
2. `upsertMessage` with id `tempMessageId`.
3. `onOptimisticTurn(clientRequestId, ...)` creates a turn keyed on
   `clientRequestId`.
4. POST returns `messageId`; call `replaceMessageId(tempMessageId, messageId)`.
5. Bridge reacts; `useTurnEventStore.append`'s optimistic-merge block swaps
   the `clientRequestId`-keyed turn into a `messageId`-keyed turn.

New flow:

1. Generate `clientRequestId = uuid()` (still needed server-side for idempotency).
2. Render a **skeleton row** immediately (see *Skeleton row (required)*
   below). Skeleton is purely cosmetic and does not touch either store.
3. Wait for `SendMessage` POST to return the real `messageId`.
4. `upsertMessage` with id `messageId` (no temp id ever).
5. `onRealTurn(messageId, ...)` creates the turn keyed on `messageId`.
6. No rename ever happens.

Skeleton row (required):

The "optimistic render is <100 ms" claim is unverified and depends on
backend load, attachment size, and network RTT. Rather than leaving the
UX to chance, ship the skeleton row as part of Commit 1. It is:

- Rendered by `RoomChatShell` when `sending === true && !currentTurnId`.
- A single row with the user's plaintext input (already in hand) styled
  like a user bubble, plus a pending-spinner on the assistant side.
- Not in `useMessageStore` or `useTurnEventStore`; just derived UI state
  in the component.
- Replaced (not merged) by the real row once `onRealTurn` fires.

Handling `processing_status` SSE arriving before POST response:

The backend emits `processing_status(PROCESSING)` during the HTTP request
handler, and SSE is a separate connection. It is possible for *any*
early `processing_status` event (not only `PROCESSING` /
`phase_changed`) to arrive before the POST response returns the real
`messageId`. Concrete failure modes we've seen or must handle:

- Early `phase_changed(PROCESSING)` (the common case).
- Rapid cancellation: client cancels before POST returns, backend emits
  `turn_canceled` immediately; in recovery paths the SSE can precede
  the client's `setMessageId` mapping.
- Validation-error terminal events (`turn_failed` for a rejected
  message) if the backend emits the status before the 4xx body reaches
  the client.
- Out-of-order `hitl_requested` on auto-assign + HITL mentions when the
  LLM-parse branch queues an interrupt very early.

All of these must be buffered identically; it is a correctness mistake
to special-case only `phase_changed`.

**Buffer spec (D1.buf):**

1. *Key:* `clientRequestId` (string). This is the only identifier
   both sides share before the POST resolves.
2. *Value:* an append-only array of `TurnEvent | ProcessingStatusEvent`
   objects, preserving arrival order.
3. *Scope:* module-scoped Map inside `sse-handlers/index.ts`. One map
   per page load; not persisted.
4. *Admission:* every `processing_status` and `turn_event` SSE whose
   `clientRequestId` has not yet been mapped to a `messageId` goes
   into the buffer **instead of** being dispatched to the store.
5. *Flush:* on POST success, caller invokes
   `flushPending(clientRequestId, messageId)`. The buffer entries are
   replayed in-order with `turnId = messageId`; duplicates are
   dropped by `TurnEventLog.append`'s existing `eventId` dedupe
   (the event ids are already assigned by the backend, so re-append
   is idempotent).
6. *Flush on POST failure:* caller invokes
   `dropPending(clientRequestId)`. The buffer entries are discarded
   silently — the skeleton row already surfaces the error, and the
   backend will never produce matching completion events for a
   failed POST.
7. *TTL / eviction:* any buffered `clientRequestId` older than
   **30 s** (configurable constant `PENDING_TURN_BUFFER_TTL_MS`) is
   dropped and emits a single dev-level warning
   (`pending turn buffer evicted for clientRequestId=... — possible
   orphan SSE stream`). Prevents unbounded memory growth if POST
   never returns and `dropPending` was never called.
8. *Cap:* the buffer holds at most 64 distinct `clientRequestId`s and
   at most 256 events per `clientRequestId`. Over-cap events are
   dropped with a single warning — these thresholds are well above
   the real per-turn event count and exist only as a safety net.

`useTurnEventStore.append` must also tolerate a first event of
`phase_changed`, `turn_canceled`, `turn_failed`, or `hitl_*` (not
only `turn_started`) for a turn — see D3 below. The same idempotency
rule applies to every kind, not just `phase_changed`.

This deletes:

- `replaceMessageId` call at `useSendMessage.ts:174`.
- `findByClientRequestId` correlation at `sse-handlers/index.ts:172-175`.
- `tempMessageId` variable and all its rollback branches in `useSendMessage.ts`.
- The `replaceMessageId` and `findByClientRequestId` methods themselves
  from `useMessageStore` (no other callers; see Commit 6).

### D2. Single writer into `useTurnEventStore` (assumes Option A)

All event kinds flow through one path: the SSE dispatcher in
`src/hooks/room/sse-handlers/index.ts`. The bridge
(`useMessageStoreSync`) is demoted to a **hydration-only fallback**
invoked explicitly on first-load when the journal has no entries.

| Event kind                                  | Writer                                         |
| ------------------------------------------- | ---------------------------------------------- |
| `turn_started`                              | `useSendMessage` (synthesized on POST return)  |
| `slot_opened` / `_snapshot` / `_terminated` | SSE handler — `case 'turn_event':`             |
| `slot_delta` (future)                       | SSE handler — `case 'turn_event':`             |
| `hitl_requested` / `_answered` / `_canceled` | SSE handler — `case 'turn_event':`             |
| `hitl_expired` (forward-compat; **no backend emitter today** — see Q7)  | SSE handler — `case 'turn_event':` (kept as dead-code-ready projection) |
| `phase_changed`                             | SSE handler — `processing_status` branch       |
| `turn_completed` / `_failed` / `_canceled`  | SSE handler — `processing_status` branch       |

Backend audit required before this design is safe — see D0 risk note.
The audit artifact goes in this document under *Backend coverage audit*
(section stub, fill before Commit 4).

Bridge becomes a single function invoked once per hydration:

```ts
// useMessageStoreSync.ts, post-refactor
export function hydrateTurnStoreFromMessages(
  messages: MessageEntity[],
): TurnEvent[]  // pure function, no subscription
```

Called from `useTurnHydration` when `/turns/recent` returns zero turns
but the message store has entities (indicating journal was empty at
original send time — i.e. Redis-down during the original turn). No
subscription, no orphan sweep, no merge logic.

**Sequencing rules (required to avoid a fallback-vs-live-SSE race):**

1. The fallback runs **only** during the initial hydration pass for
   a room, gated by `useTurnHydration`'s `hasHydratedOnce` guard. It
   must not re-fire when messages are later mutated by live SSE.
2. The fallback runs **after** `/turns/recent` has resolved — not
   in parallel. If `/turns/recent` returns ≥1 turn, the fallback
   is skipped unconditionally, even if the message store has
   additional entities that aren't represented there (those are
   `MessageEntity`s for turns older than the window; hydration
   coverage extends as the user scrolls).
3. The fallback checks live SSE state: if `useRoomSSEConnection` is
   currently `OPEN` and any `turn_event` has been observed for this
   room in the last 500 ms, the fallback is skipped. This closes
   the race where the backend starts broadcasting mid-hydration.
4. The fallback writes via `useTurnEventStore.replaceFromHydration`
   (new API added in C4), which is a batch replace rather than
   per-event `append`. This prevents a subsequent live `turn_event`
   from a different turn being reordered relative to the hydrated
   set.
5. The fallback is one-shot: the store tracks a
   `hydrationFallbackDispatched` boolean per `roomId`; subsequent
   resets clear it so a user navigating away and back re-evaluates.

### D3. Shrink `useTurnEventStore.append` to plain append

After D1 + D2, `append` no longer has to merge optimistic turns. It becomes:

```ts
append(turnId, event) {
  const state = get()
  const existing = state.turnLogs.get(turnId)
  const log = existing ?? new TurnEventLog(turnId)
  const isNew = !existing
  log.append(event) // existing eventId dedup in TurnEventLog stays
  const newLogs = new Map(state.turnLogs).set(turnId, log)
  const newOrder = isNew ? [...state.orderedTurnIds, turnId] : state.orderedTurnIds
  const newComposer = shouldUpdateComposer(event.type)
    ? composerReducer.reduce(state.composerState, event)
    : state.composerState
  set({ turnLogs: newLogs, orderedTurnIds: newOrder, composerState: newComposer })
}
```

Note: this path explicitly tolerates a first-event other than
`turn_started` (e.g. a buffered `phase_changed` replayed after POST
resolves with the `messageId` mapping — see D1). Downstream projection
reducers (`contentSlotsReducer`, `composerReducer`) must also tolerate
any single event kind as the first event for a turn. Audit during
Commit 2; fix by defaulting missing fields rather than throwing.

Deletions:

- `turnIdByClientRequestId` map (no longer used).
- The entire optimistic-merge block (`index.ts:43-93`).
- The `alreadyTracked` guard (`index.ts:126-129`) — no duplicate path remains.
- `createOptimisticTurn(clientRequestId, ...)` — delete outright.
  Only caller is `useSendMessage`, which now calls `append(messageId, turnStartedEvent)`
  directly.

### D4. Delete the orphan sweep

With stable ids, `cleanupOrphanOptimisticTurns` in `useMessageStoreSync.ts`
has nothing to clean. Delete it and its call site.

### D5. Simplify `TurnEventLog` dedup contract

Document that `TurnEventLog.append` is idempotent on `eventId` (it already
is), so any retry-safe writer can emit the same event twice without
duplication. Nothing code-wise changes; this just makes the invariant
explicit.

**Documentation location (required as part of Commit 6):**

- Add a `/**` JSDoc block above the `append` method in
  `src/stores/turn-event-store/index.ts` stating:
  *"Idempotent on `eventId`. Callers (SSE dispatcher, hydration
  fallback, tests) MAY append the same event multiple times; only
  the first insertion mutates state. This invariant is load-bearing
  for the pre-POST SSE buffer flush in `sse-handlers/index.ts`
  (see D1.buf) — removing it will reintroduce the duplication bug."*
- Add a one-paragraph reference to `docs/ROOM_TIMELINE_DESIGN.md`
  pointing readers at the JSDoc for the authoritative definition.
- No separate standalone markdown doc is created; the JSDoc +
  existing timeline design doc are the two touchpoints.

## File-by-file changes

### 1. `src/hooks/room/useSendMessage.ts`

- Remove `tempMessageId` variable.
- Move `upsertMessage({ id: tempMessageId, ... })` → after the POST, using
  `messageId`.
- Rename `onOptimisticTurn` to `onRealTurn(messageId, userMessage)` (new
  signature; single stable id). Call after POST.
- Remove `replaceMessageId(tempMessageId, messageId, ...)` call.
- Rollback: on POST failure, just clear the skeleton row state
  (`setSending(false)` + error toast). No store entries to remove because
  nothing was written pre-POST.
- Render the skeleton row (see D1) while the sent message has not yet
  been reconciled with a real turn. The predicate is:
  `sending && !hasRealTurnForClientRequestId(clientRequestId)`, where
  `hasRealTurnForClientRequestId` checks whether `onRealTurn` has
  fired for this `clientRequestId` (the component tracks this via
  local state set inside the callback, not via the turn-store map —
  that map is removed in Commit 2). The skeleton is **required**,
  not optional.

### 2. `src/stores/turn-event-store/index.ts`

- Delete `turnIdByClientRequestId` from state + `reset()`.
- Replace the current `append` body with the simplified version in D3.
- Delete `createOptimisticTurn` entirely. Callers switch to
  `append(turnId, turn_started_event)`.
- `removeTurn` can skip the `newLookup` block.
- Update `TurnEventStoreState` interface to drop `turnIdByClientRequestId`
  and `createOptimisticTurn`.

### 3. `src/hooks/turn/useMessageStoreSync.ts`

Refactor from continuous subscription to one-shot hydration fallback:

- Delete `cleanupOrphanOptimisticTurns` and its call site.
- Delete the subscription-based projection. Replace with exported pure
  function `hydrateTurnStoreFromMessages(messages): TurnEvent[]`.
- Delete the "unlinked agents → assign to last user" fallback
  (`index.ts:87-105`). It was compensating for temp-id drift that no
  longer exists.
- `buildTurnEvents`' `clientRequestId` field on `turn_started` is no
  longer needed. Drop it — the event schema change needs a test update
  sweep but the field is not persisted on the backend.

### 4. `src/hooks/room/sse-handlers/index.ts`

- In the `processing_status(PROCESSING)` handler (lines 164-176), delete
  the `findByClientRequestId` + `replaceMessageId` block entirely.
- Keep the `phase_changed` emission (lines 186-203) and terminal
  emission (lines 222-264).
- The `case 'turn_event':` branch at **lines 661-670** becomes the
  authoritative writer for slot/HITL events. Ensure it uses
  `camelCaseEvent` from `useSSEToEventLog.ts` before calling
  `append(turnId, event)`.
- Add a small in-module buffer for **every** SSE event that arrives
  before the POST resolves (all `processing_status` kinds *and* all
  `turn_event` kinds; see D1.buf for the full spec, TTL, and caps),
  keyed by `clientRequestId`, flushed by
  `useSendMessage` on POST success via a new dispatcher method
  `flushPending(clientRequestId, messageId)`). See D1.

### 5. `src/hooks/turn/useSSEToEventLog.ts`

- Confirmed: this file exports `useSSEToEventLog` (unused elsewhere) and
  `camelCaseEvent` (used by `sse-handlers/index.ts`).
- Delete the unused hook; keep `camelCaseEvent` as a pure utility.

### 6. `src/stores/message-store/index.ts`

- Remove `replaceMessageId` and `findByClientRequestId` (no remaining
  callers after Commit 2). Drop their tests.
- Keep `version` bump semantics — hydration still uses it.

### 7. `src/hooks/room/useRoomWebhook.ts`

- Rename `handleOptimisticTurn` → `handleRealTurn`; signature changes
  from `(clientRequestId, ...)` to `(messageId, ...)`. Still gated on
  `turnBasedTimeline`.

### 8. `src/hooks/room/useTurnHydration.ts`

- On first load, if `/turns/recent` returns `{ turns: [] }` but
  `useMessageStore.messages.size > 0`, call
  `hydrateTurnStoreFromMessages(messages)` and append all produced
  events. This replaces the bridge's subscription role.

### 9. Audit (no code changes expected, but verify)

- `src/hooks/room/useProcessingRestore.ts` — already verified (Q4,
  2026-04-17): uses `upsertMessage` + `setMessageId` with real
  `processing_message_id`s; no `turnIdByClientRequestId` or
  `createOptimisticTurn` dependency. Re-run `rg` during Commit 5
  sanity check.
- `src/hooks/room/useRoomHydration.ts` — `reconcileWithDb` is defined
  here (not in `useRoomData.ts`; `useRoomData.ts` just re-exports).
  Already verified (Q4): uses `upsertMany` with DB-shaped messages,
  no temp-id → real-id transition assumptions. Re-run `rg` during
  Commit 5 sanity check.

### 10. Tests

Update / add:

- `__tests__/turn-event-store.test.ts` — remove optimistic-merge tests;
  assert that `append` on a non-existent `turnId` just creates the log.
- `__tests__/useMessageStoreSync.test.ts` — remove orphan-sweep tests.
- `__tests__/useSendMessage.test.ts` — assert that the first time the
  turn appears in `orderedTurnIds` it's already the real `messageId`,
  never `clientRequestId` or `temp-*`.
- New integration test: simulate POST returning `messageId=M1` followed
  immediately (synchronously in the test) by SSE `turn_event`s for `M1`;
  assert `orderedTurnIds === ['M1']` (no `['crid_*', 'M1']` transient).
- Regression test for the original duplication bug: simulate the bridge
  running a projection after a `turn_event` `slot_opened` for `M1`
  arrives; assert a single turn in `orderedTurnIds` and a single slot in
  the projection.

## Migration Strategy

### Order of operations

Do not do this as one mega-PR. Stage it so each commit is independently
testable and shippable:

All frontend commits land behind the `turnBasedTimeline` flag. Each is
independently revertable. **Commit 0 ships to the backend first** and
must soak in staging for ≥24h before Commit 1 begins. See the
*Commit 0 (backend)* spec below for details.

0. **Commit 0 — Backend `start_turn()` wiring (backend repo)**
   - Land the `start_turn()` call inside
     `RoomMessageCenter.process_room_user_message` (see C0.1).
   - Add `tests/test_turn_event_end_to_end.py` (see C0.2).
   - Optional: plumb `client_request_id` through
     `OrchestrationRequest` (C0.3).
   - Staging soak: 20 messages of varied kinds, verify
     `turn_events` Mongo docs and zero journal-disabled errors.
   - Only after this soaks ≥24h do the frontend commits below begin.

1. **Commit 1 — Stable id + skeleton row (ships together)**
   - `useSendMessage`: add the skeleton row component and its
     `sending && !didRender` gate. Render user bubble from plain input
     immediately; render the real entry after POST resolves.
   - Switch optimistic render to run after POST returns, using `messageId`.
   - Keep `createOptimisticTurn(clientRequestId, ...)` call site but pass
     `messageId` (real id from POST response) as the `clientRequestId`
     argument so the indexer `turnIdByClientRequestId[messageId] =
     messageId` is a self-map. This is an intentional no-op in the
     merge path: any later `append(messageId, ...)` looks up `messageId
     → messageId` and behaves as a plain append. This keeps existing
     tests green without requiring the Commit 2 merge-block deletion.
     Commit 2 then removes the map and `createOptimisticTurn` entirely.
   - The skeleton row is **required** in Commit 1 so the UX stays
     acceptable regardless of POST latency.
   - Ship. Verify skeleton → real transition has no layout shift.

2. **Commit 2 — Remove `clientRequestId` indirection + buffer SSE**
   - Delete `turnIdByClientRequestId`, the merge block, and
     `createOptimisticTurn`.
   - Replace `append` with the D3 form.
   - Add the pre-POST SSE buffer (all `processing_status` + all
     `turn_event` kinds, per D1.buf) + `flushPending` on POST success
     + `dropPending` on POST failure + 30 s TTL eviction in
     `sse-handlers/index.ts`.
   - Update/delete tests.
   - Ship.

3. **Commit 3 — Remove orphan sweep**
   - Delete `cleanupOrphanOptimisticTurns` and its call site.
   - Note: the bridge still owns a separate "stale-slot sweep" for its
     own incremental projection (not the orphan-turn sweep). Leave
     that intact until Commit 4 converts the bridge to a one-shot
     hydration function, at which point the stale-slot sweep becomes
     unreachable and is deleted as part of the same diff.
   - Ship.

4. **Commit 4 — Collapse bridge to hydration fallback (Option A)**
   *(requires Commit 0 to have soaked in production for ≥24h first)*
   - Verify the Backend coverage audit results (see section below)
     remain accurate post-C0 — specifically that the events listed
     under "Has emission site in code? Yes" now all broadcast.
   - Replace `useMessageStoreSync`'s subscription with the pure
     `hydrateTurnStoreFromMessages` function.
   - Wire it into `useTurnHydration` for the zero-turns-but-messages
     case.
   - Verify `case 'turn_event':` in `sse-handlers/index.ts:661-670`
     handles every slot/HITL event kind that the bridge used to produce.
   - Ship behind `turnBasedTimeline` + the ternary sub-flag
     `turnEventStoreWriter` (see Feature Flag section). Commit 4 flips
     the default from `"bridge"` to `"direct"` only in staging first,
     and only after Commit 0 has soaked ≥24 h in the same environment.
     Production flip happens in a subsequent deploy.

5. **Commit 5 — Remove `replaceMessageId` / `findByClientRequestId`**
   - Delete both methods from `useMessageStore`.
   - Delete `useSendMessage.ts:174` call site.
   - Delete `sse-handlers/index.ts:172-175` call site.
   - Delete associated tests (don't `.skip`).
   - Ship.

6. **Commit 6 — Dead code + docs**
   - Remove the unused `useSSEToEventLog` hook (keep `camelCaseEvent`).
   - Remove `clientRequestId` **from the `turn_started` event shape
     only**, across tests, fixtures, and the hydration mapper. Note:
     the pre-POST buffer introduced in Commit 2 (D1.buf) keeps using
     `clientRequestId` as its key — that is an in-memory SSE-dispatcher
     concern, not a store shape, and it stays. This commit's deletion
     is scoped to the `turn_started` event payload field, nothing else.
   - Update architecture notes in `docs/ROOM_TIMELINE_DESIGN.md`.
   - Remove the `turnEventStoreWriter` sub-flag once
     `"direct"` has been stable for 1 week in production.

### Rollback plan

- **Commit 0 rollback**: revert the backend diff. Production returns
  to the current behaviour (empty journal, bridge is the only writer)
  with zero user-visible impact — the frontend is still on the
  bridge path at this point.
- Commits 2-6 are cleanly revertable because Commit 1 establishes the
  invariant "turn store is keyed on real `messageId`" independently. If
  Commit 2+ regresses, revert and the temp-id merge path comes back.
- If Commit 4 (Option A) regresses and the backend coverage audit
  missed an event kind, set `turnEventStoreWriter="bridge"` to
  re-enable the bridge's incremental-event path without shipping a
  code revert. The direct SSE path then reverts to buffer-only.
- If Commit 4 regresses and the issue is on the backend side (e.g.
  `turn_journal_disabled` spam reappears under real load), flip the
  sub-flag to `"bridge"` and open a follow-up ticket against Commit 0's
  implementation — do not revert Commit 4's frontend diff alone, as
  that leaves two writers racing.

### Backend coverage audit (completed 2026-04-17)

**Critical finding: the backend `turn_event` SSE pipeline is dead in production.**

`TurnEventAppender.start_turn()` is defined at
`services/turn_event_service.py:80` but has **zero production call sites**
(only tests call it — `tests/test_turn_lifecycle.py`,
`tests/test_turn_event_appender.py`). Without `start_turn()` being
invoked, the Mongo `turn_events` document for a turn never exists. Every
production `appender.append(...)` then fails the `turn_exists` guard at
`_append_internal` and raises `TurnNotStartedError`, which in
`dual_write_mode=True` is caught, marks the journal disabled, and
**never broadcasts**. `/rooms/{room_id}/turns/recent` returns empty.

Concretely this means the frontend's `case 'turn_event':` dispatch at
`src/hooks/room/sse-handlers/index.ts:661-670` is effectively dead code
in production today, and the "Redis-down fallback" framing of
`useMessageStoreSync` is misleading — the bridge is the **only** source
of slot/HITL events in all modes regardless of Redis state.

Per-event table:

| Event kind          | Has emission site in code? | Broadcasts in production today?              | Backend file/line |
| ------------------- | :------------------------: | :------------------------------------------: | ----------------- |
| `turn_started`      | No (never called)          | No                                           | `services/turn_event_service.py:80` defined; no callers |
| `turn_completed`    | Yes                        | **No** (journal disabled before this fires)  | `modules/RoomMessageCenter.py:676,1686,1732` |
| `turn_failed`       | Yes                        | **No** (same)                                | `modules/RoomMessageCenter.py:622,1791`      |
| `turn_canceled`     | Yes                        | **No** (same)                                | `modules/RoomMessageCenter.py:646,1758`      |
| `phase_changed`     | Yes                        | **No** (same)                                | `modules/SupervisorExecutor.py:108`, `modules/WorkflowCenter.py:63` |
| `slot_opened`       | Yes                        | **No** (same)                                | `services/slot_lifecycle.py:30`              |
| `slot_delta`        | Defined in enum only       | No (no emitter in production code)           | —                                            |
| `artifact_appended` | Defined in enum only       | No                                           | —                                            |
| `slot_snapshot`     | Yes                        | **No** (same)                                | `services/slot_lifecycle.py:52`              |
| `slot_terminated`   | Yes                        | **No** (same)                                | `services/slot_lifecycle.py:61`              |
| `hitl_requested`    | Yes                        | **No** (same)                                | `services/hitl_service.py:248`               |
| `hitl_answered`     | Yes                        | **No** (same)                                | `services/hitl_service.py:503`               |
| `hitl_canceled`     | Yes                        | **No** (same)                                | `services/hitl_service.py:766`               |
| `hitl_error`        | Yes                        | **No** (same)                                | `services/hitl_service.py:445`               |
| `hitl_expired`      | **No emitter at all**      | No                                           | Only defined in enum + `_status_map`         |

### Implications for Option A

Option A (direct SSE as single writer) as originally scoped **cannot
ship without a backend precursor**. Emitting nothing through
`useTurnEventStore` would leave the timeline empty on every turn.

Two viable paths:

**Option A-prime (preferred, keeps Option A's long-term design)**

Add a **Commit 0 (backend)** ahead of the frontend work:

1. Call `appender.start_turn(room_id, user_message_id, user_input, client_request_id)`
   from `services/room_services.py:_persist_user_message` (or immediately
   after, inside the same async task) using the newly persisted
   `user_message.message_id` as `turn_id`.
2. Add an integration test that asserts `/turns/recent` returns the turn
   after a normal send, and that `turn_event` SSEs are observable over
   the SSE connection.
3. Add an emitter for `hitl_expired` (or accept it as a permanent gap
   and remove it from the frontend projections). Decision deferred; not
   blocking for Commit 4.
4. Only after Commit 0 ships and bakes in staging does the frontend
   sequence (Commits 1-6) become safe.

**Option B (fast pragmatic path)**

If fixing the backend journal is out of scope, **flip to Option B**:
delete the direct `case 'turn_event':` dispatch on the frontend, keep
the bridge as the single writer, and still land the stable-id work
(Commits 1-3 + 5-6 minus the bridge-deletion parts). This yields most
of the duplication-bug fix without touching the backend.

**Recommendation: Option A-prime.** The backend work is a small,
bounded change (one `start_turn` call + one integration test) and
unblocks every future feature that depends on the event journal
(token streaming via `slot_delta`, replay-from-journal, cross-session
HITL state, etc.).

> Backend Commit 0 decision: **Option A-prime** (2026-04-17) — add a
> backend commit that wires `start_turn()` into the live request path
> before any frontend work lands.

### Commit 0 (backend): wire up `start_turn()` so the journal pipeline is actually live

Repo: `multi-agents-backend`. No frontend changes in this commit.

#### C0.1 — Emit `turn_started` inside `process_room_user_message`

File: `modules/RoomMessageCenter.py`

The cleanest hook is inside `process_room_user_message`, right after
the room lock is acquired and the processing claim is refreshed
(around line 453, before any dispatch into executors). That point:

- is on the common path for every dispatch (mentions, supervisor,
  debate, all-agents);
- already has `self._turn_event_appender` wired via `set_redis_service`;
- runs after the frontend has already received `processing_status:
  PROCESSING`, so ordering with the frontend's "skeleton row" is safe
  (the frontend doesn't depend on seeing `turn_started` before
  `phase_changed`, because Commit 1 synthesizes `turn_started` locally
  after POST).

Pseudocode:

```python
# After refresh_processing_claim(...) and before any dispatch
if self._turn_event_appender:
    # Explicit idempotency: the recovery path
    # (process_room_user_message with is_recovery=True) legitimately
    # re-enters this code for a turn whose turn_events doc already
    # exists. We must NOT let start_turn() raise on the unique
    # (room_id, turn_id) index, because under dual_write_mode=True
    # that disables journaling for the rest of the turn.
    already_started = await self.database_service.turn_exists(
        room_id, room_user_message_id
    )
    if not already_started:
        user_msg = await self.database_service.get_room_user_message_by_message_id(
            room_user_message_id
        )
        if user_msg is not None:
            user_input = {
                "text": user_msg.message_content.message_text or "",
                "attachment_count": len(user_msg.message_content.attachments or []),
            }
            await self._turn_event_appender.start_turn(
                room_id=room_id,
                turn_id=room_user_message_id,
                user_input=user_input,
                client_request_id=None,  # see C0.3 for plumbing
            )
```

**Idempotency contract.** Serialization of the happy path is already
provided by the per-room async lock acquired above. The explicit
`turn_exists(...)` check covers the only legitimate re-entry cases:
- `process_room_user_message(is_recovery=True)` from
  `jobs/stale_task_checker.py` (orphan recovery).
- `_recover_stuck_supervisor_trajectories` (V2 trajectory recovery),
  which also routes through `process_room_user_message` with
  `is_recovery=True`.

Both recovery paths find the `turn_events` doc already present and
skip `start_turn()` entirely, so subsequent `append(...)` calls
continue to succeed and the journal stays enabled.

**HITL resume** (`resume_queue_from_continuation` at
`modules/RoomMessageCenter.py:1886`) is a separate entry point that
does NOT call `process_room_user_message`, so it never hits this
code. That is intentional: the original `process_room_user_message`
call that preceded the HITL pause already emitted `turn_started`,
so the `turn_events` doc exists and the resume path's subsequent
`hitl_answered` / `slot_*` / `turn_completed` appends target the
existing doc. No `start_turn()` call is needed on the resume
entry.

#### C0.2 — Integration test: `turn_event` SSE + `/turns/recent`

New file: `tests/test_turn_event_end_to_end.py`.

- **Happy path.** Spin up a test room (existing fixture), send a
  message through the normal `POST /rooms/{id}/messages` path with
  a mocked executor that emits `slot_opened` + `slot_terminated`.
  - Assert the SSE stream contains a `turn_event` envelope with
    `type == "turn_started"` for that turn, followed by the slot
    events and the terminal `turn_completed`.
  - Assert `GET /rooms/{id}/turns/recent` returns exactly one turn
    with `turn_id == user_message_id` and the expected event
    sequence.
- **Recovery-path idempotency.** Persist a user message, manually
  insert a `turn_events` doc (simulating a prior, crashed run that
  already emitted `turn_started`), then call
  `process_room_user_message(request, is_recovery=True)` directly.
  - Assert `start_turn()` is NOT called a second time (monkeypatch
    `self._turn_event_appender.start_turn` to a spy).
  - Assert the journal is still ENABLED for the turn (no
    `dual_write_mode` disablement), i.e. a follow-up
    `slot_opened` append writes successfully to the existing doc
    and broadcasts a `turn_event` SSE.
  - Assert `GET /rooms/{id}/turns/recent` returns the turn with
    both the pre-existing `turn_started` and the new recovery-path
    events.
- **HITL-resume isolation.** Persist a user message, run
  `process_room_user_message` up through a `hitl_requested` emission
  (via mocked executor), then call
  `resume_queue_from_continuation(message_id, task_result_text="ok")`
  with a pre-seeded continuation doc.
  - Assert `start_turn()` is NOT called during resume.
  - Assert the resume path's `hitl_answered` event appends to the
    existing `turn_events` doc and broadcasts a `turn_event` SSE.
- Mark the existing `tests/test_turn_event_appender.py` dual-write
  negative test as still-valid (we are not changing `dual_write_mode`
  semantics; we are only ensuring the positive path actually runs).

#### C0.3 — Optional: plumb `client_request_id` end-to-end

Not strictly required for the frontend plan to work (`start_turn`
accepts `client_request_id=None`), but useful for log correlation
and for surviving reconnects before Commit 1 lands:

1. Add `client_request_id: str | None = None` to `OrchestrationRequest`
   in `models/request.py`.
2. Pass it from `services/room_services.py` where
   `OrchestrationRequest(...)` is constructed.
3. Use it in the `start_turn(...)` call above instead of `None`.

This can be split into its own sub-commit or included with C0.1.

#### C0.4 — Decide `hitl_expired` policy (separable)

Options:

- **Drop it from the projections.** Remove `hitl_expired` handling
  from the frontend reducers (or keep them as unreachable fallbacks
  that just log). Accept as a permanent gap until a background
  expiration job is introduced.
- **Add a minimal backend emitter.** Inside a new
  `jobs/hitl_expiry_checker.py` (or an existing stale-task job),
  mark HITL requests past `expires_at` and call
  `_emit_hitl_turn_event(..., "hitl_expired", ...)` the same way
  `hitl_canceled` is emitted.

Not a blocker for Commit 4 either way — the bridge never emitted
`hitl_expired` either (there's nothing in `MessageEntity` that
distinguishes "expired" from "canceled"). Defer to a follow-up
ticket unless the decision is "drop".

#### Ship criteria for Commit 0

- Unit + integration tests green, including the three C0.2 scenarios
  (happy path, recovery-path idempotency, HITL-resume isolation).
- `database_service.turn_exists(room_id, turn_id)` is already
  implemented (`services/database_service.py:2431`) and already
  used by `services/turn_event_service.py:139` in `_append_internal`.
  Verify the C0.1 call-site wires to the same helper (no duplicate
  implementation) and extend coverage only if the existing tests
  don't already exercise the "returns True when a turn_started
  doc exists" branch.
- Staging soak: send 20 messages of various kinds (single-agent,
  multi-agent, supervisor, HITL request+answer, cancel). For each,
  verify in MongoDB that `turn_events` has a document with the
  expected event sequence, and that no
  `turn_journal_disabled:<turn_id>` key appears in Redis.
- Also exercise orphan recovery once during soak (e.g. kill a worker
  mid-turn, then let the stale-task checker re-trigger): verify the
  recovered turn's journal remains enabled and the recovery events
  are appended to the same `turn_events` doc.
- Logs contain zero `"Turn journal disabled for %s"` errors in that
  soak window.
- Rollback: revert the Commit 0 diff. Production returns to the
  current behaviour (empty journal, bridge is the only writer) with
  no user-visible impact.

Only after C0 is merged **and soaked in staging for ≥24h** does
the frontend commit sequence (Commits 1-6) begin.

## Risk Assessment

| Risk                                      | Likelihood | Mitigation                                                                 |
| ----------------------------------------- | ---------- | -------------------------------------------------------------------------- |
| POST is slow → user sees blank chat       | High on LLM-parse branch (~500-3000 ms); low elsewhere (~≤300 ms). See Q6. | Skeleton row mounts on send (not on POST return); user text renders optimistically via `useMessageStore` scratch entity; turn-store append waits for real `turnId`. |
| POST fails after upload → need rollback   | Already exists | Clear the skeleton + toast. No store writes to unwind.              |
| Any SSE event (`phase_changed`, terminal, `hitl_*`, `slot_*`) arrives before POST response | Medium | Buffer **all** kinds by `clientRequestId` per D1.buf, flush on POST success, drop on POST failure, evict at 30 s TTL; `append` tolerates first-event != `turn_started`. |
| Redis-down mode: backend does not emit `turn_event` | Medium | Option A only: backend coverage audit + `turnEventStoreWriter="bridge"` kill-switch; hydration fallback still covers refresh. |
| Two writers active simultaneously (C0 shipped, C4 shipped, but sub-flag left on `"direct"` before C1-C3 finish — or `"both-shadow"` accidentally enabled in prod) | Medium pre-mitigation | Ternary `turnEventStoreWriter` flag with ordering invariant (see Feature Flag); startup assertion falls back to `"bridge"` if bridge is still producing incremental events; `"both-shadow"` is staging-only and writes to a separate store. |
| Projection reducers assume `turn_started` is first event | Medium | Audit `contentSlotsReducer` + `composerReducer` in Commit 2; default missing fields instead of throwing. |
| Hydration path regresses                  | Low        | `useTurnHydration` already writes real ids; new fallback is additive.       |
| HITL overlay race                         | Low        | HITL injection already waits for the turn to exist; no id correlation needed. |
| `useProcessingRestore` or `reconcileWithDb` relies on removed methods | Low (audited Q4, 2026-04-17) | File §9 audit complete; re-run the audit checklist before Commit 5 as a final sanity check. |
| Third-party consumers of `clientRequestId → turnId` map | Low (audited Q5, 2026-04-17) | Usage limited to `src/stores/turn-event-store/index.ts`, `src/hooks/room/useRoomWebhook.ts`, `src/hooks/room/useSendMessage.ts`; all are rewritten in Commits 1-2. Re-run `rg "turnIdByClientRequestId\|createOptimisticTurn\|onOptimisticTurn" src/` before Commit 2. |

## Audit Checklist (run before Commit 2)

```bash
rg -n "turnIdByClientRequestId"     src/
rg -n "createOptimisticTurn"        src/
rg -n "replaceMessageId"            src/
rg -n "findByClientRequestId"       src/
rg -n "cleanupOrphanOptimistic"     src/
rg -n "useSSEToEventLog"            src/
rg -n "tempMessageId"               src/
rg -n "onOptimisticTurn"            src/
rg -n "handleOptimisticTurn"        src/
rg -n "clientRequestId"             src/stores/turn-event-store src/hooks/turn src/hooks/room
rg -n "useProcessingRestore"        src/
rg -n "reconcileWithDb"             src/
```

Every hit needs a decision: delete, keep (with justification), or adapt.

## Success Criteria

1. `useTurnEventStore.orderedTurnIds` never contains a `clientRequestId`
   or `temp-*` value after a normal send, in any tracked test scenario.
2. The "Hermes agent response appears twice" bug and its variants cannot
   be reproduced in 50 consecutive sends, with or without SSE
   disconnection during processing.
3. `useTurnEventStore.append` file diff is ≥40 lines shorter.
4. `useMessageStoreSync.ts` file diff is ≥60 lines shorter (subscription
   → pure function).
5. `replaceMessageId`, `findByClientRequestId`, `turnIdByClientRequestId`,
   `createOptimisticTurn`, `cleanupOrphanOptimisticTurns`, and the unused
   `useSSEToEventLog` hook are all fully removed from the codebase
   (`rg` returns zero hits) after Commit 6.
6. Manual smoke: single-agent send, multi-agent send, cancel mid-processing,
   HITL round-trip, SSE reconnect mid-turn, Redis-down mode (toggled via
   `turnEventStoreWriter="bridge"` in a local dev config), refresh
   after send — all render the same turn count before and after refresh.
7. No test is `.skip`'d or `.todo`'d to pass the suite; removed tests
   are actually deleted.

## Estimated Effort

- **Commit 0 (backend):** ~0.5 day implementation + ≥24h staging
  soak. One-line-ish `start_turn()` call + one integration test +
  optional `client_request_id` plumbing.
- **Commits 1-6 (frontend):** ~2-3 days of focused work + 1 day of
  staged rollout and monitoring.
- Commit 1 is the highest-risk user-visible change and needs a round of
  design review on the skeleton row before merging.
- Commit 4 waits on Commit 0 soak; the audit is already done.
- Commits 2, 3, 5, 6 are internal cleanup (<0.5 day each).
- Total elapsed time, including soak periods and monitoring:
  roughly 1 week.

## Open Questions

1. ~~Option A vs Option B (D0)~~ — **resolved: Option A-prime**.
2. ~~Backend coverage audit~~ — **resolved**; see the audit section.
   Every production emission site requires Commit 0 to become live.
3. ~~Is there any hydration path that produces a turn with `clientRequestId`
   as its `turnId`?~~ — **resolved (2026-04-17): NO.** `fetchRecentTurns`
   (`src/lib/api/turns.ts:21`) returns `WireTurnJournal[]` where
   `turn_id` comes directly from the backend `turn_events` MongoDB
   document. Backend uses `user_message.message_id` as the `turn_id`
   (`modules/RoomMessageCenter.py`). Hydration always produces
   real-`messageId`-keyed turns.
4. ~~Does `useProcessingRestore.ts` or `useRoomData.ts:reconcileWithDb`
   depend on `replaceMessageId` or `turnIdByClientRequestId`?~~ —
   **resolved (2026-04-17): NO.**
   - `src/hooks/room/useProcessingRestore.ts` only calls `upsertMessage`
     and `lifecycle.setMessageId` with real `processing_message_id`
     values from the room query. No dependency on
     `replaceMessageId` / `findByClientRequestId` / `turnIdByClientRequestId`.
   - `reconcileWithDb` (`src/hooks/room/useRoomHydration.ts:107`) only
     calls `upsertMany(filtered, 'db')` with DB-shaped messages keyed by
     real `message_id`. No dependency on the APIs slated for deletion
     in Commit 5.
5. ~~Does the `/chat` route or any non-room surface use
   `createOptimisticTurn` or `onOptimisticTurn`?~~ — **resolved
   (2026-04-17): NO.** Grep for `createOptimisticTurn|onOptimisticTurn|
   handleOptimisticTurn` under `src/` matches only 3 files, all
   room-scoped:
   - `src/stores/turn-event-store/index.ts` (declaration + impl)
   - `src/hooks/room/useRoomWebhook.ts` (wiring)
   - `src/hooks/room/useSendMessage.ts` (consumer)
   `/chat` (`src/app/c/chat/page.tsx`) only creates rooms and navigates
   via `useChatRoomCreation`; it never touches `useTurnEventStore` or
   the optimistic-turn APIs. The optimistic-turn surface is fully
   room-scoped — safe to delete in Commit 2.
6. ~~What is the measured p50/p95 POST latency for `SendMessage` on
   production traffic?~~ — **resolved (2026-04-17) by static analysis;
   direct instrumentation is a nice-to-have.**

   **Synchronous backend work before POST returns** (read code path at
   `api/room_center.py:259-343` → `services/room_services.py:1832`):
   1. `hitl_service.get_pending_requests(room_id)` — 1 Mongo query.
   2. Attachment resolution — 0-N S3/Mongo round-trips, usually cached.
   3. `get_room_by_room_id` — 1 Mongo query.
   4. Pre-persist scope validation (`_validate_canonical_mentions` or
      `_resolve_explicit_target_scope`) — 0-1 Mongo queries depending
      on route (mention vs explicit target vs `all_agents`).
   5. `_persist_user_message` — 1 Mongo insert + SSE
      `processing_status` broadcast.
   6. `_initialize_room_memory` — in-memory update, very cheap.
   7. **Parser branch** (this is the bimodal step):
      - **Direct chat** (`len(selected_agent_set) == 1` and not debate):
        explicit shortcut at `services/room_services.py:1780-1798` —
        **no LLM call.**
      - **`_prepare_for_supervisor_v2`** (`:1474`): explicitly
        comments "Does NOT call the supervisor LLM"; the synchronous
        cost is `ContextAssemblyService` bookkeeping plus a
        `rooms` extend_info write. Cheap (~tens of ms).
      - **`parse_user_message` LLM branch**
        (`services/room_services.py:1801-1808`): calls
        `openai_service.parse_user_message_by_llm(...)`. This is the
        **one path that issues a real LLM completion** inside the
        POST handler. Typical latency 500 ms – 3 s depending on
        provider and token budget.
   8. `_generate_agent_messages_based_on_parsed_result` — N Mongo
      inserts.

   **Latency estimates (unmeasured; should be validated with
   instrumentation before wide rollout):**
   - **Direct-chat / supervisor-v2 / mention with validated scope:**
     ≲ 300 ms p50, dominated by 2-4 Mongo round-trips + attachment
     resolution.
   - **LLM-parser branch (auto-assign or `all_agents` multi-agent):**
     500-3000 ms p95. This is the window the skeleton row must cover.

   **Implications for Commit 1 UX (confirm existing design, not new
   requirements):**
   - The already-specified skeleton row (lines 160-171) is
     **mandatory, not optional**. A deferred skeleton would leave
     the viewport blank for up to ~3 s on the LLM-parse branch.
   - The skeleton row already embeds the user's plaintext input
     (which the component has in hand before POST dispatch) styled
     like a user bubble, so user text is visible within one frame
     of send. No scratch entity in `useMessageStore` is needed.
   - The skeleton is **replaced** (not merged) by the real row when
     `onRealTurn(messageId, …)` fires — the existing "no temp id
     ever" invariant holds.
   - The single-writer invariant is preserved: no turn-store keys
     are ever mutated, and no `useMessageStore` entity exists until
     the real `messageId` is known.

   **Instrumentation recommendation (nice-to-have, can land alongside
   Commit 0):** add `logger.info("send_message_to_room latency: %dms
   path=%s", elapsed_ms, path_tag)` where `path_tag ∈ {direct_chat,
   supervisor_v2, llm_parse, mention, cancel}` so staging p50/p95 can
   be read off logs. Not a blocker for frontend work.

7. ~~`hitl_expired` policy (C0.4): drop from frontend projections, or
   add a backend expiry job?~~ — **resolved (2026-04-17): keep
   projections, drop synthetic emitter, do NOT add backend emitter
   in this refactor.**

   **State of the world today:**
   - **Backend schema** declares `TurnEventType.HITL_EXPIRED` in
     `models/turn_event.py:162` and `HITLEventType.INPUT_EXPIRED` in
     `models/hitl.py:49`, with a status mapping in
     `services/hitl_service.py:907`. These are defined but **nothing
     emits them.** `docs/HITL_DESIGN.md` §ToDo item 5 explicitly
     lists "Add HITL expiry job … emit `hitl_input_expired` SSE" as
     outstanding work.
   - **Backend HITL request creation** (`services/hitl_service.py:203`)
     stores `expires_at = utcnow() + 24h` on the request, but no job
     ever checks this field. Requests linger PENDING indefinitely
     until answered or explicitly canceled.
   - **Backend does emit `hitl_canceled`** turn events natively via
     `_emit_hitl_turn_event` at `services/hitl_service.py:766-770`,
     so the cancel path is already covered once Commit 0 lands.
   - **Frontend bridge (`useMessageStoreSync`)** synthesizes
     `hitl_expired` at lines 234-242 and 451-459 whenever
     `agent.hitlResolved === true && agent.hitlUserAnswer == null`.
     This is actually a **mis-classification** of the "resolved
     without answer" state — such states today come from
     `hitl_canceled` or DB hydration of historical HITLs, not from
     expiry (because expiry doesn't exist). The bridge's `hitl_expired`
     emission conflates "canceled externally" with "expired".
   - **Frontend projections** (`rail.ts:156,169`,
     `composer.ts:77`, `content-slots.ts:224`) and the SSE translator
     (`useSSEToEventLog.ts:97`) handle `hitl_expired` cleanly with
     distinct visuals (`x` icon, "— expired" suffix).

   **Decision:**
   a. **Drop the synthetic `hitl_expired` emitter** from the bridge.
      This falls out naturally when the bridge is deleted in
      Commit 5; nothing else in the frontend synthesizes it.
   b. **Keep `hitl_expired` handling in projections and the SSE
      translator** as forward-compatible dead code. Cost is ~10 lines
      across 4 files; benefit is zero churn when the backend expiry
      job eventually lands.
   c. **Do not add a backend expiry emitter as part of this refactor.**
      It is orthogonal to the turn-store single-writer work, it
      needs a product decision (what does "expire a 24-hour-old
      HITL" actually do to the paused supervisor trajectory?), and
      it already has a home in `docs/HITL_DESIGN.md` §ToDo item 5 /
      `jobs/stale_task_checker.py` which has a similar auto-fail
      pattern to extend.

   **One consequence worth noting for Commit 5 review:** after the
   bridge is deleted, any HITL that is resolved-without-answer today
   (e.g. user-canceled from another client, or DB-hydrated as
   resolved without a cached answer) will no longer produce any
   turn event at all unless the backend `hitl_canceled` path was
   hit. This is strictly more correct than the current behaviour
   (which mis-reports such states as "expired"). The cancel-from-UI
   path is fully covered by the real backend `hitl_canceled`
   emission at `services/hitl_service.py:766-770`. The only remaining
   gap is "HITL marked resolved in DB without any matching cancel
   event", which already indicated a backend bug and will now be
   visibly absent from the rail/composer instead of silently
   mislabeled — arguably a feature.
8. ~~Does `_persist_user_message` always run before
   `process_room_user_message` in all flow variants (mentions, recovery,
   HITL resume)?~~ — **resolved (2026-04-17): YES**, but with one
   nuance about the HITL-resume entry point (see next paragraph).

   **Call-graph evidence:**
   - **Normal send & @mentions** (`api/room_center.py:322-341`):
     `room_center.send_message_to_room(...)` calls
     `_persist_user_message` at `services/room_services.py:1915`
     BEFORE returning the `message_id`. Only on
     `response.success and response.message_id` does the API layer
     schedule `background_tasks.add_task(process_room_user_message, ...)`
     with that real `message_id`. `_handle_mentions_flow`
     (`services/room_services.py:2207`) explicitly notes "the actual
     agent execution happens in a background task
     (`process_room_user_message`)".
   - **Stale task / orphan recovery** (`jobs/stale_task_checker.py:674`
     and `:783`): Both recovery paths scan the DB for already-persisted
     user messages and re-trigger `process_room_user_message` with
     `is_recovery=True`. The message is persisted by definition of
     being found in the scan.
   - **`process_room_user_message` internal** (line 482): Calls
     `get_room_user_message_by_message_id(room_user_message_id)` — the
     code already assumes the user message exists in the DB, which
     aligns with the invariant.

   **HITL resume nuance (new — must be recorded in Commit 0):**
   `resume_queue_from_continuation` (`modules/RoomMessageCenter.py:1886`)
   is a *separate entry point* from `process_room_user_message`. It is
   called from push-notification webhooks and HITL-answer callbacks
   to resume a paused supervisor trajectory. It does NOT go through
   `process_room_user_message` and therefore does NOT hit the
   C0.1 `start_turn(...)` call site. This is benign *in the happy
   path* because:
   - The original `process_room_user_message` call (which paused
     via HITL) already emitted `turn_started`, so the `turn_events`
     Mongo doc exists.
   - Resume-path events (`hitl_answered`, subsequent `slot_*` events,
     `turn_completed`) are appended to that existing doc.

   However, if the server crashes *between* user-message persistence
   and the `start_turn()` call (e.g. OOM during lock acquisition),
   recovery will re-enter via `process_room_user_message(is_recovery=True)`
   and the `turn_events` doc gets created on that recovery run —
   still fine, but only because `start_turn()` is reached on every
   `process_room_user_message` entry, **not** on resume.

   **Implication for C0.1 — idempotency must be explicit, not
   exception-driven.** The current plan text at lines 604-610 says
   `start_turn()` relies on the unique `(room_id, turn_id)` index to
   reject a second call, which then *disables the journal for the
   rest of the turn* under `dual_write_mode=True`. That is
   unacceptable for the recovery path (which legitimately re-enters
   `process_room_user_message` for the same `turn_id`): we would
   lose journaling on every recovered turn. C0.1 MUST therefore:
   1. Call `await self.database_service.turn_exists(room_id, turn_id)`
      first, and skip `start_turn()` entirely if it returns `True`
      (so the journal stays enabled for subsequent appends).
   2. Add a regression test that exercises the recovery path and
      asserts the journal remains enabled and subsequent events are
      appended.

All open questions (#1-#8) are now resolved. Commit 0's implementation
text (lines 604-610) must be updated to make idempotency explicit
(`turn_exists` guard) before the C0.1 code lands — that refinement,
plus the trivial `database_service.turn_exists(room_id, turn_id)`
helper, are the only remaining prerequisites for Commit 0 merge.

The plan is implementation-ready end-to-end. Optional future work
tracked outside this refactor:
- Direct-measurement of POST latency via structured logs
  (Q6 instrumentation recommendation) — can land alongside Commit 0.
- Backend HITL expiry job (Q7 item c) — product decision required;
  see `docs/HITL_DESIGN.md` §ToDo item 5.
