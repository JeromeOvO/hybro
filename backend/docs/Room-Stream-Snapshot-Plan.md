# Room Stream: Snapshot-Driven Delivery (Plan)

> Status: Implemented. This document is the acceptance contract for the
> snapshot-driven room stream and its frontend activity projection.
>
> Scope: `backend/delivery` + `backend/execution` public projection +
> frontend room sync (`frontend/src/lib/room-sync`,
> `frontend/src/hooks/room/sse-handlers`, message/streaming stores).
> The LLM gateway, orchestrator kernel, and A2A transport are unchanged
> except for added public event emission.

This plan evolves the existing per-room SSE stream in place — no protocol
fork, no new SSE delivery endpoint, and no "V2" naming anywhere (see §10
Naming guards). The current notification-driven stream grows into a
snapshot-driven stream with ordered deltas, boundary checkpoints, and a
single event fold path shared by live delivery and historical replay. The
single new HTTP surface introduced by this plan is the replay endpoint
(§5), which is a fallback read path, not a delivery channel.

## 1. Motivation

Current room sync is notification-driven SSE plus heuristic reconciliation:

- Frames carry no room-level sequence. The frontend correlates events through
  `client_request_id` matching (`sse-handlers/correlation.ts`,
  `sse-handlers/pending-turn-buffer.ts`, and the id-matching branches in
  `sse-handlers/handlers/processing-status.ts`).
- Missed non-terminal frames (`artifact_update` today;
  `agent_response_partial` once Phase 2 wires it as the streaming channel —
  it currently has no production producer, only translator/tests) are
  never recovered until a terminal event triggers `reconcileWithDb` after
  fixed 150/1500 ms delays; a 5 s safety-net poll in
  `useRoomSSEConnection.ts` is the backstop.
- Slow clients are disconnected outright (`SSEConnection.send_frame` calls
  `close()` on `QueueFull`), then pay full-DB hydration on reconnect.
- LLM decisions, tool calls, retries, and usage are persisted internally
  (orchestrator events, `llm_call_completed` logs) but are not projected to
  the frontend — `handleRunEvent` only consumes the three terminal run
  sub-types.
- Terminal SSE frames can be broadcast before all durable projection steps
  complete, which is why the frontend needs fixed-delay reconciliation.

## 2. Design principles

- **P1 — Event sourcing for the live view.** An append-only room event log is
  the source of truth for the realtime UI; Mongo projections remain the
  durable record. The UI never invents state; it folds events.
- **P2 — Snapshot + delta.** Client state = the latest full snapshot plus the
  ordered deltas after it. Snapshots reconcile; deltas carry liveness.
- **P3 — Boundary checkpoints.** Snapshots are forced only at
  durable-confirmed boundaries: tool execution completed
  (`tool_execution_completed`, `execution/orchestrator/lifecycle.py`
  `SessionEventType`), agent dispatch completed
  (`agent_dispatch_completed`, `models/orchestration.py`
  `OrchestrationEventType` — a different subsystem, not a SessionEvent),
  message completed (`message_completed`), and terminal projection steps
  settled. These are the Hybro equivalents of pi-web-ui's
  `agent_end`/`tool_execution_end` checkpoints. No fixed-delay
  reconciliation.
- **P4 — One fold path.** The same fold/reducer consumes live events and
  historical replay. Hydration and SSE cease to be separate code paths.
- **P5 — Private/public split.** Raw prompts, full tool arguments, and
  reasoning never enter public payloads. Public events carry redacted
  summaries and metadata only (extends the existing `AgentEvent` public_text
  vs. private-evidence convention).

## 3. Target architecture

```text
┌────────────────────────────── Browser (React) ──────────────────────────────┐
│ Conversation timeline │ Turn Trace panel │ Task bubbles │ HITL overlay      │
│        ▲               ▲              ▲          ▲                          │
│        └───────┬───────┴──────────────┴──────────┘                          │
│                ▼                                                            │
│   RoomReducer — single state entry (snapshot replace + ordered delta patch)│
│   ├─ message-store   (message entity projection)                            │
│   ├─ streaming-store (in-flight units, entityId → partial)                  │
│   └─ trace-store     (run → decision/llm_call/tool_call tree)               │
│                                                                             │
│   SSEConnection: connect → connected{room_seq} → snapshot → deltas;         │
│   heartbeat carries latest room_seq; gap ⇒ re-request snapshot              │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │ GET /sse/room/{room_id}/stream (unchanged URL)
┌──────────────────────────────▼──────────────────────────────────────────────┐
│ Delivery Edge (FastAPI, per instance)                                       │
│                                                                             │
│  RoomSyncService   — connection registry, ordered delta fanout,             │
│                      backpressure policy (snapshots droppable, deltas       │
│                      never policy-dropped, slow clients resync not drop)    │
│  EventPublisher /  — single writer of the public event log (the choke     │
│  RoomEventWriter     point ALL emit paths already pass through): keeps     │
│                      terminal lease dedup; every delivered emit persists   │
│                      an idempotent room_events doc and broadcasts the      │
│                      frame with room_seq/room_event_id                      │
│  SnapshotService   — folds room_events[0..N] into the snapshot              │
│                      (incremental materialized projection; N = room_seq)    │
│  PublicProjectionTranslator — private → public event projection (redaction) │
└──────────────┬───────────────────────────────┬──────────────────────────────┘
               │ append event (persist first)  │ Redis pub/sub (cross-instance)
┌──────────────▼───────────────────────────────▼──────────────────────────────┐
│ Room event log (append-only)                                                │
│  Mongo room_events: { room_id, room_seq↑, event_id, parent_event_id,        │
│     run_id, kind, ts, payload_public, persist_state }                       │
│  Redis: latest room_seq per room │ terminal dedup keys │ fanout channels    │
│  (existing run_events stays as the private, authoritative run fact log)     │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │ execution produces events
┌──────────────────────────────▼──────────────────────────────────────────────┐
│ Execution & Orchestration                                                   │
│  Orchestrator kernel: LLM decision loop (GatewayModelRuntime → assembler →  │
│     tool executor) → emits public events (llm_call / decision / tool_call)  │
│  RunCommandHandler + run_events (unchanged)                                 │
│  A2A dispatch / task_notifications (unchanged; deltas gain parent_event_id) │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────────────┐
│ LLM Gateway (unchanged; adds public event emission)                         │
│  OpenAI/DeepSeek providers → GatewayTurnEvent stream (unchanged)            │
│  + project GatewayTurnEvent usage/finish kinds plus completion-log fields   │
│    into llm_call_completed events (see §6 for the exact source mapping)     │
└──────────────────────────────────────────────────────────────────────────────┘
```

## 4. In-place protocol evolution (no V2)

Rules:

- Same delivery endpoint (`/sse/room/{room_id}/stream`), same connection
  lifecycle, same heartbeat mechanism. No new delivery URL, no version
  prefix, no "V2" in any name, docstring, or error message. (The replay
  endpoint in §5 is a new fallback read path, not a delivery channel.)
- All changes are additive to the existing JSON frames. Old frontends ignore
  unknown fields inside `data`; unknown frame types (the new `snapshot`
  frame) are dropped by the `isRoomSSEType` guard in
  `sse-handlers/dispatch.ts` — the functional outcome is the same: old
  clients ignore them.
- Capability negotiation happens through the existing `connected` handshake
  frame, whose `data` gains `room_seq` (the room's latest contiguous seq).
  `room_seq` is the single room-level version — no separate `stateVersion`
  exists (the snapshot watermark below is the same number). A new frontend
  enables the new semantics only when `room_seq` is present; otherwise it
  falls back to current behavior. Old frontends keep working unchanged
  during the rollout.
- All new fields live inside `data`, not at the frame top level.
  `hasSSEFrameEnvelope` (`frontend/src/lib/types/sse.ts`) requires exactly
  the four top-level keys `{type, timestamp, room_id, data}` and is the
  parse gate in `lib/api/sse.ts`; keeping additions inside `data` means the
  envelope gate and `sse.ts` need no changes and old clients cannot drop
  frames. The four-key envelope shape becomes a pinned contract.
  `types/sse.ts` itself does change additively: `RoomSSEType`,
  `ROOM_SSE_TYPES`, and `RoomSSEFrameMap` gain the `snapshot` entry.
  `dispatch.ts` gains the matching `snapshot` case and
  `HANDLED_ROOM_SSE_TYPES` entry (the `satisfies Record<RoomSSEType, true>`
  and `satisfies never` checks make this a compile-time-forced change).
- Existing frame types (`processing_status`, `run_event`,
  `agent_response_partial`, `agent_response`, `task_submitted`,
  `task_update`, `artifact_update`, `error`, `cancellation`,
  `hitl_request`, `hitl_response`, `hub_agent_event`) remain as the
  delta channel; their `data` gains:
  - `room_seq` (per-room monotonic, assigned at persist time)
  - `room_event_id` (the persisted `room_events` doc id; a NEW key — it
    does NOT collide with `run_event.data.event_id`, which keeps carrying
    the run fact's own event id)
  - `parent_event_id` (optional, references another `room_event_id`; links
    agent/task deltas to the decision event that caused them; sourced from
    the emitting execution path, not the sequencer)
- One new frame type:

```text
snapshot { type:"snapshot", timestamp, room_id, data: {
             room_seq,
             messages[], tasks[], runs[], hitl[],
             streaming: { entityId → partial },
             trace: { runId → { tree, usage, duration } } } }
```

Client rules (replace all current correlation heuristics):

1. `last_room_seq` is seeded from `connected.data.room_seq` on connect.
   No gap check runs until the first snapshot of the session is applied.
2. Deltas arriving before the first snapshot are buffered; they are
   replayed in order after the snapshot applies, then the gap check runs.
3. After a snapshot applies: `delta.data.room_seq != last_room_seq + 1`
   ⇒ the out-of-order delta is buffered in a bounded reorder window
   (~500 ms; tuned to outlast cross-instance fanout latency, NOT tied to
   the 30 s heartbeat). If the missing seq arrives within the window,
   buffered deltas replay in order. If the gap persists past the debounce,
   the client re-requests a snapshot; buffered higher-seq deltas are
   discarded only after the replacement snapshot applies. Deltas with
   `data.room_seq <= snapshot.data.room_seq` are discarded.
4. Terminal frames (`processing_status` and terminal `run_event`
   notifications alike) are emitted only after their terminal fact settles
   durably: no durable side-effect step — every step EXCEPT the
   `run_event_sse`/`processing_sse` steps themselves, which emit the frame
   and are therefore `running`/`pending` at emit time — remains in
   `{pending, running}` (the `TerminalProjection.steps` state machine uses
   `pending → running → completed`, with `blocked` for failed side
   effects). `blocked` steps do NOT gate emission — the existing machinery
   settles the fact on them (`refresh_terminal_projection_schedule` derives
   `pending` only from steps in `{pending, running}`), so gating on
   `blocked` would withhold the terminal frame forever with no recovery
   path. The emitted frame carries the fact's canonical status.
5. Streaming partials accumulate under a stable `entityId`; the terminal
   delta commits and replaces them.
6. Each browser tab is an independent consumer (own `last_room_seq`, own
   connection). The droppable-snapshot policy is per connection; tabs
   self-heal independently via rule 3. No cross-tab coordination.
7. Snapshot delivery: every connect emits a snapshot as the first frame
   after `connected`, served from the incremental materialized checkpoint
   (cheap). Like all snapshots it is droppable under backpressure — a
   bootstrap trigger guarantees recovery: if no snapshot has been applied
   within a short bootstrap window after connect (500 ms, or immediately
   upon the first delta arriving without a prior snapshot), the client
   re-requests a snapshot. `?snapshot=1` forces a fresh fold from the
   authoritative `room_events` log (bypassing the checkpoint), used by
   rule 3's gap recovery to rule out any possibility of checkpoint
   staleness: the client closes its current stream and reconnects to the
   same endpoint with `?snapshot=1`. No new URL. Frontend plumbing: the
   URL is constructed inline in `SSEConnection.connect()`
   (`lib/api/sse.ts:77`) — that construction gains an optional
   query-param path.

### 4.1 File touchpoints (backend)

| File | Change |
|---|---|
| `delivery/translator.py` | `to_sse_frame`/`_frame()` gain OPTIONAL `room_seq` + `room_event_id` + `parent_event_id` kwargs (omit when `None` — existing callers and `tests/fakes/delivery.py::FakeEventPublisher` keep working); add `snapshot` frame builder. (Phase 1 decision-visibility kinds are `run_event` payload types, not new frame branches — translator work there is nil; Phase 2 adds the `snapshot` branch.) |
| `delivery/event_publisher.py` | becomes the persist-before-broadcast writer: on every emit that passes dedup (dedup short-circuits `IN_FLIGHT`/`ALREADY_DELIVERED`/`DEDUPLICATED` skip persistence — those events already have their doc), the persist happens BEFORE the broadcast; a broadcast failure leaves the persisted doc for the retry to reuse (no post-delivery persistence). Persists with a deterministic idempotency `_id` (terminal: `delivery_id`/dedup key; non-terminal: message_id + kind + content digest + per-stream monotonic component) and threads `room_seq`/`room_event_id` into `to_sse_frame` — this covers the finalizer's `_checked_emit`, `events.py`, the HITL adapter, and watchdog/orchestrator paths with no per-site wiring (DTOs stay unchanged); delivery retries reuse the same persisted doc and re-broadcast the same seq; `parent_event_id` is an optional caller kwarg; defense-in-depth check via an injected `ProjectionSettlementReader` port (reads terminal-fact settlement from `run_events`): never emit a terminal `run_event`/`processing_status` frame whose projection still has durable side-effect steps (all steps except `run_event_sse`/`processing_sse` themselves) in `{pending, running}` — terminal `task_update` frames emitted by `descendant_cleanup`/`system_task_delivery` during phase 1 are gated by the existing per-step dependencies instead, not by this check; snapshot backpressure policy |
| `delivery/sse/manager.py` | admission/broadcast unchanged; backpressure policy switches from disconnect to resync. NOTE: `broadcast_frame_to_room` today removes any connection whose `send_frame` returns `False` — for mark-for-resync the `send_frame` return contract is redefined (`False` = actually closed; a resync-drop is not a close) or the broadcast loop is adjusted accordingly |
| `delivery/sse/connection.py` | the `QueueFull → close()` code lives here (`send_frame`); replace with mark-for-resync; heartbeat `next_frame` gains access to the room's latest `room_seq` (injected sequencer reader) |
| `delivery/event_bus/cross_instance.py` | unchanged (room_seq travels inside `data`; Redis fanout already frame-opaque) |
| `api_gateway/routes/sse_routes.py` | unchanged URL; the `connected` frame is built here (currently lines 64–69): its `data` gains `room_seq`; every connect emits a snapshot as first frame after `connected`, and `?snapshot=1` forces a fresh one on reconnect |
| `execution/terminal_projection.py` | single owner of the terminal gate: two-phase finalize — phase 1 runs the durable side-effect steps; `run_event_sse`/`processing_sse` execute only in a phase-2 pass once every other step reports `completed` or `blocked`. Transiently failed steps are released to `pending` and scheduled for retry by the existing machinery, which defers phase 2. (A plain `_STEP_ORDER` reorder is insufficient: `finalize()` is a single pass, so moving the SSE steps to the end still emits them in the same pass while an earlier step remains `pending`.) |
| `execution/events.py` | fallback path (no bound finalizer, or `record_lifecycle=False` watchdog emission) must apply the same terminal gating; in production the finalizer is always bound (`RunLifecycleAdapter`), and the `record_lifecycle=False` watchdog emit deps are currently dead code (`stale_task_checker` only awaits `append_run_timeout_failure`) — PREFERRED: eliminate the fallback emit entirely; otherwise it reads the already-persisted projection settled by the calling path instead of gating on steps it cannot settle |
| `execution/orchestrator/*` (new `public_projection.py`) | `PublicProjectionTranslator`: orchestrator/model events → public payloads (§6) |
| new `dal/` or `execution/repository` module | `room_events` collection + `RoomSequencer` (per-room monotonic `room_seq`) |

## 5. Room event log & sequencing

- `room_events` is append-only. Writes follow persist-before-broadcast: the
  event is inserted (with its `room_seq`) before the delivery layer fans out
  the corresponding frame. Delivery never precedes durability. The seq is
  assigned exactly ONCE at persist time; the frame is then built from the
  values already in hand from that same persist (no second sequencer call,
  no DB re-read), so the delta's `room_seq` always matches the snapshot
  fold order.
- **Single writer = the event publisher.** `EventPublisherImpl` is the
  choke point every emit path already passes through — the finalizer
  (`_checked_emit` → `emit_checked`), `events.py` →
  `event_publisher.emit`, the HITL adapter → `event_publisher.emit`, and
  the watchdog/orchestrator paths alike. On every delivered emit the
  publisher persists a `room_events` doc and threads `room_seq` and
  `room_event_id` into the translated frame, so the persist-before-
  broadcast invariant holds for ALL paths including the terminal frames —
  no call-site wiring and no facade-only routing are needed.
  `parent_event_id` is the only caller-supplied value (optional kwarg,
  where the emitting path knows the causal decision event).
- **Idempotent persistence.** The `room_events` doc `_id` is a
  deterministic idempotency key, not a fresh `id_factory` value: terminal
  events use their `delivery_id`/dedup key (they already re-emit after a
  failed delivery releases the reservation —
  `emit_checked` returns `FAILED` and the finalizer sends the step back to
  `pending`), non-terminal events derive it from their stable identity
  fields (message_id + kind + content digest) PLUS a per-stream monotonic
  component (chunk index / delta counter), so distinct streaming deltas
  with identical content do not collapse into one doc. The monotonic
  component is a publisher-maintained per-stream counter (keyed by
  `entityId`/stream), NOT a DTO field and NOT a caller kwarg. A retry
  therefore inserts
  the same `_id` (duplicate insert is a no-op) and re-broadcasts the frame
  with the SAME already-persisted `room_seq`/`room_event_id`, so the fold
  never double-counts and the "seq assigned exactly ONCE per logical
  event" invariant holds across delivery retries. After a
  persist-succeeded/broadcast-failed split, the seq is not in hand — the
  retry catches `DuplicateKeyError` from `insert_one` and does a
  `find_one` on the deterministic `_id` to read back the already-assigned
  `room_seq` (the "no DB re-read" claim applies only to the first-attempt
  happy path).
- `room_seq` is per-room monotonic. Assignment strategy (decision needed,
  see §11): Redis `INCR` per room (fast, consistent with existing Redis
  usage, but a NON-atomic option — the INCR is a separate operation from
  the insert, so it is only viable paired with risk 10's confirmed-skip
  fallback) vs. Mongo counter document per room advanced in the same
  transaction as the insert (atomic; the strongly preferred option per
  risk 10). Whichever is chosen, the sequencer must be a single injectable
  dependency so the choice does not leak into delivery code.
- `room_seq` and `parent_event_id` are frame-level (translator) fields only;
  they are NOT added to the delivery DTOs, so
  `test_common_foundation.py::test_delivery_event_schemas_match_design_doc`
  (which pins exact DTO field sets) remains unchanged.
  `to_sse_frame(event, *, timestamp)` gains `room_seq`, `room_event_id`,
  and `parent_event_id` parameters — all OPTIONAL keyword params with
  omit-when-`None` semantics, so existing callers (including
  `tests/fakes/delivery.py::FakeEventPublisher`) keep working until Phase 2
  wires them. `room_seq` and `room_event_id` come from the `room_events`
  doc the event publisher persisted immediately before the emit (values in
  hand, not a DB re-read); `parent_event_id` is an optional emit-time
  argument supplied by the emitting execution path (references another
  `room_event_id` — the decision event that caused this one). DTOs are
  unchanged, so all three flow through the publisher call, not through the
  DTO.
- **`parent_event_id` return-value contract.** `emit`/`emit_checked` keep
  their current signatures and return types (`bool` /
  `DeliveryEmitStatus`) — no existing consumer changes. Callers that need
  to reference the persisted event later call a dedicated
  `emit_checked_identified(event, *, parent_event_id=None) ->
  tuple[DeliveryEmitStatus, str | None]` instead: it persists exactly like
  `emit_checked` and returns the persisted doc's `room_event_id`. The
  caller captures that id and passes it as the child's `parent_event_id`
  kwarg on a later emit. Cross-process parents (decision on instance A,
  dispatch on instance B) pass the id through the existing
  A2A/execution metadata.
- `persist_state` maps to the existing `TerminalProjection.steps` state
  machine (`pending → running → completed`; `blocked` for failed side
  effects). The per-event value is aggregate: `settled` when no durable
  side-effect step (every step EXCEPT `run_event_sse`/`processing_sse`,
  which are the emitters and hence `running`/`pending` at emit time)
  remains in `{pending, running}` (all remaining steps `completed` or
  `blocked`). Terminal frames are emitted on `settled`, carrying the fact's
  canonical status. `blocked` steps do not gate emission — the existing
  recovery semantics settle the fact on them (`_release_failed_step` sets
  `retryable=not blocked`; `refresh_terminal_projection_schedule` derives
  `pending` only from `{pending, running}`), so gating on `blocked` would
  withhold the frame forever.
- Snapshots are produced by folding `room_events[0..N]` through the same
  fold logic the client uses (P1/P4); `snapshot.data.room_seq = N` is the
  last folded event, so snapshot content is trivially consistent with its
  watermark. The fold is incrementally materialized (checkpoint at seq M,
  fold M+1..N on demand) to keep long rooms cheap.
- Replay endpoint: `GET /sse/room/{room_id}/events?after=<room_seq>&limit=N`,
  hosted in `api_gateway/routes/sse_routes.py` alongside the stream route
  (auth identical to the SSE route). Cold hydration (no `after` yet) is
  `after=0`; the primary live-recovery path remains re-request-snapshot.
  Used by Phase 2 tests and as the fallback when a snapshot request alone
  cannot satisfy a client (e.g. a gap during heavy fanout).
- Test churn (Phase 2): `backend/tests/test_delivery_translator.py`
  currently asserts whole-frame dict equality for `processing_status`,
  `run_event`, `agent_message_partial`/`agent_message_final`,
  `cancellation`, `hitl_request`/`hitl_response`, and `task_submitted`,
  and whole-`data` dict equality for `artifact_update` (while
  `task_update` and `error` assert individual `data` sub-fields); those
  assertions must be regenerated to include the new `data` fields. This is expected churn, not a contract break — the
  durable identities in §10 are the only pinned surfaces. Also affected:
  `backend/tests/test_delivery_event_publisher.py` (whole-frame dict
  assertions for `processing_status`), `backend/tests/test_terminal_projection_recovery.py`
  (does NOT reference `_STEP_ORDER` directly, but DOES encode the current
  single-pass ordering — e.g. asserts `run_event_sse == "completed"` while
  `system_task == "pending"` after one `finalize()` call — so the
  two-phase finalize WILL break and require rewriting these per-step final
  state assertions), and
  `frontend/tests/unit/lib/sse-types.test.ts` (pins the recognized
  `RoomSSEType` set; gains `snapshot` and heartbeat-`room_seq`
  assertions). In addition, every delta-channel `*Data` type (~12 of the
  ~15) gains `room_seq` and `room_event_id` (and optionally
  `parent_event_id`) per §4; `ConnectedData` and `HeartbeatData` gain only
  `room_seq` (handshake/heartbeat — they are not persisted `room_events`
  docs), and the new `snapshot` data carries only the `room_seq` watermark —
  a broad but mechanical additive type change across `types/sse.ts`.
  Three further callers of `to_sse_frame` exist and survive unchanged if
  the new params are optional/omit-when-None: `tests/fakes/delivery.py`
  (`FakeEventPublisher.emit`), `tests/test_common_dto_hitl.py:266`, and
  `tests/test_orchestration_hitl_contract.py:86` (the latter two assert
  only `data` sub-fields).
- If `RoomSequencer` (or any other new port introduced in §4.1 —
  `RoomEventReader`, `ProjectionSettlementReader`) becomes a
  `common.protocols` Protocol, update
  `test_common_foundation.py::test_protocol_methods_match_design_doc`
  expected method set for ALL of them in the same change (Phase 2), not
  deferred to Phase 3.

## 6. Decision-visibility vocabulary (public projection)

Public payloads are produced exclusively by `PublicProjectionTranslator`.
Redaction rules: no raw system/user prompts, no full tool arguments, no
reasoning text, no private transport metadata. Fields marked
`summary` are backend-generated short text.

| Public kind | Emitted from | Payload |
|---|---|---|
| `turn_started` / `turn_completed` | run lifecycle (existing) | `run_id`, timing |
| `llm_call_completed` | gateway completion logs (`_log_call_completed` for non-streaming, `_log_stream_completed` for the streaming turn path — both emit the same `llm_call_completed` structured log) + `GatewayTurnEvent` `usage`/`finish` kinds | `model`, `provider`, `attempt`, `outcome`, `duration_ms`, `usage {input,output}`, `finish_reason` |
| `llm_retry_scheduled` | `GatewayModelRuntime` stream: `ModelStreamEvent(kind="retry_scheduled")` | `attempt`, `error_class`, `retry_delay_ms` (`retryable` is intentionally redacted from the public payload) |
| `orchestrator_decision` | kernel model turn with tool calls | `chosen_agents[]`, `plan_steps[] (summary)`, `reason (summary)` |
| `tool_call_accepted` / `tool_call_completed` | tool executor (existing event types) | `tool_name`, `arg_summary`, `result_summary`, `exit_code`, `duration_ms` |
| `agent_dispatch` | `task_submitted` (existing frame, gains `parent_event_id`) | agent id/name, task id |
| `agent_response_partial` / `agent_response` | existing | unchanged semantics + `room_seq` |
| `processing_status` | existing | unchanged + `room_seq`; terminal gated per §4 |

Frontend renders kinds above in a per-turn **Turn Trace** panel
(`trace-store`): a tree of decision → llm_call → tool_call → agent_task →
response, each node with duration/usage badges. This is the visible payoff
of the event log and is deliverable independently of the protocol work
(Phase 1).

Phase split for this table: the KIND names are delivered in **Phase 1** as
`run_event` payload types (no frame-shape changes); the field annotations
(`room_seq` / `room_event_id` / `parent_event_id`, terminal gating) apply
from **Phase 2**. Phase 1's trace-store correlates nodes via the existing
`client_request_id` until Phase 2 lands.

## 7. Backpressure & checkpoint policy

- **Snapshots are droppable.** If the per-connection budget is exhausted, a
  pending snapshot is skipped; the next checkpoint supersedes it. Deltas
  are never policy-dropped; only a physically full queue drops the
  overflowing delta, which the client's gap detection (rule 3) recovers via
  a snapshot re-request.
- **Slow consumers resync instead of disconnecting.** Replace the current
  `QueueFull → close()` with: drop the pending snapshot (if any), mark the
  connection for resync, and let the client's gap detection re-request a
  snapshot. The connection itself stays alive.
- **Checkpoint cadence** (adapted from verified pi-web-ui behavior):
  - idle: coalesced snapshot every 60 ms;
  - streaming: reconciliation snapshot every 2 s;
  - `tool_execution_completed` / `agent_dispatch_completed` /
    `message_completed` / terminal projection completed: immediate forced
    snapshot.
- Heartbeat frames carry the latest `room_seq` so clients detect gaps even
  when no delta flows. Heartbeats are synthesized in
  `connection.py:next_frame` with `data: {}`; the connection gains an
  injected sequencer reader to populate `data.room_seq`. Frontend side,
  `HeartbeatData` (`types/sse.ts`, currently `Record<string, never>`) gains
  `room_seq`, and the heartbeat handler becomes the gap-detection consumer
  (enumerated in §5 test churn).

## 8. Frontend reducer & removal list

- Introduce `RoomReducer` as the single write path. It consumes:
  - live SSE frames (snapshot + deltas), and
  - historical replay from the `room_events` endpoint during hydration.
  Both flows call the same fold functions; `hydrate-room`/`apply-db-messages`
  become replay feeders, not a parallel state path.
- Gap detection lives in the dispatcher: compare `room_seq` continuity;
  on gap, request snapshot (single self-heal path).
- **Removed after Phase 3** (replaced by the reducer):
  - `sse-handlers/correlation.ts` and `pending-turn-buffer.ts`;
  - the 5 s safety-net polling loop in `useRoomSSEConnection.ts` and the
    150/1500 ms fixed-delay reconcile scheduling in
    `sse-handlers/handlers/processing-status.ts`
    (`scheduleTerminalReconcile`, calls at ~lines 349/352);
  - `processing-status.ts` id-matching heuristics
    (`isTurnLevelTerminalProcessingStatus`, `isCurrentProcessingUser`, …);
  - `turnTerminalStatus` inference guesses in `infer-turn-terminal-status.ts`
    (terminal state now arrives as a durable-confirmed frame).
- Kept and upgraded: streaming-store merge logic (entityId semantics),
  message-store upsert priorities simplify to reducer order (no more
  'sse' vs 'hydration' source arbitration).

## 9. Migration plan

### Phase 1 — Decision visibility (no protocol change)

- Backend: `PublicProjectionTranslator`; emit `llm_call_*`,
  `orchestrator_decision`, `tool_call_*` as `run_event` payloads over the
  existing channel; `tool_status`-style fields (duration/exit code).
- Frontend: `trace-store` + Turn Trace panel consuming existing `run_event`
  frames.
- Acceptance: LLM decisions, retries, tool calls, and per-turn usage are
  visible in the UI; no protocol or frame-shape changes.

### Phase 2 — Snapshot + delta semantics (in-place evolution)

- Backend: `room_events` collection + `RoomSequencer`; persist-before-
  broadcast; `connected` handshake with `room_seq`; `snapshot`
  frame + `SnapshotService`; backpressure/resync policy; terminal frames
  gated on projection completion.
- Frontend: `RoomReducer`; snapshot apply + delta patch + gap self-heal;
  capability detection off the `connected` frame (old behavior kept as
  fallback for old clients during rollout).
- Acceptance: kill the SSE connection mid-stream and verify the UI converges
  from snapshot alone; verify terminal frames only arrive after durable
  projection (check the persisted `room_events` doc's `persist_state` via
  the replay endpoint — `persist_state` is NOT on the wire); old clients
  still converge via their existing 5 s poll / 150 ms reconcile paths
  (note: the server-side disconnect-on-QueueFull path disappears for them,
  so their disconnect-triggered full hydration no longer fires — this is a
  behavior change to verify, not a no-op).

### Phase 3 — Cleanup & guards

- Delete the heuristic code listed in §8; unify hydration behind the replay
  endpoint; add naming guards (§10) and contract tests
  (`test_common_foundation.py` protocol inventories plus focused delivery
  tests).
- Acceptance: zero fixed-delay reconciliation, zero polling, zero
  id-matching heuristics in `frontend/src/hooks/room/` AND
  `frontend/src/stores/message-store/` (e.g. `infer-turn-terminal-status.ts`
  lives under the latter — the acceptance grep must cover both trees); gap
  self-heal is the only recovery path; `git grep` finds no `_v2`/`V2`/`sse-v2`
  naming in owned surfaces.

## 10. Naming guards

Version-neutral naming, mirroring the orchestrator cutover precedent:

- Owned surfaces must not contain `sse_v2`, `stream_v2`, `protocol_v2`,
  `SSE V2`, or similar versioned branding. Enforce with pinned-string tests
  once Phase 3 lands.
- Durable identities that must remain unchanged (enforced by existing
  pinned-string tests):
  - the SSE endpoint path `/sse/room/{room_id}/stream` and
    `/sse/room/{room_id}/status`;
  - all existing SSE frame `type` strings;
  - `RunEventNotification` and `ProcessingStatusEvent` DTO field names;
  - `TerminalProjection.steps` keys (`run_event_sse`, `processing_sse`, …).

## 11. Risks & open questions

1. **room_seq assignment under multi-instance writes.** Redis INCR is fast
   but loses sequence authority if Redis is down AND can burn a seq on a
   persist failure (see risk 10); a Mongo counter per room is durable but
   adds a write to the hot path. Decision required before Phase 2; the
   sequencer is injected so the choice is swappable — but risk 10 makes
   atomic seq+insert allocation the strongly preferred option.
2. **Snapshot watermark consistency.** The snapshot is a fold of
   `room_events[0..N]`, so `snapshot.data.room_seq = N` is the last folded
   event and content/watermark cannot diverge. N must be the contiguous
   prefix of persisted seqs: with multi-instance writers, seq 6 can be
   inserted before seq 5, so the fold may not skip a missing seq (either
   serialize per-room appends, or fold up to the first gap minus one).
   Relationship to risk 10: the confirmed-skip window is the mechanism
   that upgrades a transient gap ("keep waiting for seq 5") into a
   permanent-hole skip ("backfill marks it skipped, fold advances"). If
   atomic seq+insert allocation is chosen, the window is moot — a seq
   exists iff its doc exists, so gaps can only be transient reordering.
   In-flight deltas with `room_seq > N` are buffered client-side (rules
   2–3) until they or the next snapshot arrive. The incremental
   materialized fold (checkpoint at M, fold M+1..N) must guarantee no
   event beyond N is folded, which the append-only `room_events` ordering
   provides.
3. **Replay endpoint cost.** Long rooms make `?after=` replay heavy.
   Mitigation: replay is the fallback; primary recovery is snapshot. Cap
   `limit` and page.
4. **Old-client behavior during rollout.** Because all additions live
   inside `data`, old clients pass every frame through
   `hasSSEFrameEnvelope` unchanged; they ignore the `snapshot` frame (it is
   dropped by the `isRoomSSEType` guard in `dispatch.ts`, not by a switch
   branch) and keep using the current heuristics. Contract test in Phase 2:
   the four-key envelope shape and all existing frame `type` strings are
   unchanged.
5. **HITL overlay.** `hitl_request`/`hitl_response` join the reducer as
   ordinary events with `room_seq`, and the snapshot `data` carries a
   `hitl[]` section (pending requests + interactions) so the reconnect
   restore path in `useRoomSSEConnection.ts` (currently
   `phase: 'hitl_overlay'`) is replaced by snapshot content.
6. **Cancel flow.** Cancellation remains lifecycle events folded by the
   reducer; terminal gating covers the `canceled` status too. Local UI-only
   state (cancel-confirm ephemeral message, `cancelAllNonTerminal`) stays a
   client concern and is not part of the protocol.
7. **Multi-tab.** Each tab is an independent consumer (rule 6 in §4). The
   droppable-snapshot policy is per connection; no cross-tab coordination
   is introduced. Accepted behavior.
8. **Terminal dedup interplay.** The existing Redis lease deduplicator
   stays. The new gate is *when* a terminal frame is emitted (after
   projection), not *whether* (dedup) — the two mechanisms are orthogonal
   and both must hold.
9. **Cross-publisher fanout ordering.** Redis pub/sub preserves per-publisher
   order only. Two instances assigning `room_seq` 5 and 6 could deliver 6
   before 5 to a subscriber. Rule 3 buffers the out-of-order delta for the
   ~500 ms reorder window; only a gap persisting past the debounce triggers
   a snapshot re-request (avoids thrash under concurrent fanout).
10. **Permanent seq holes (persist-failure split).** If seq allocation and
    the `room_events` insert are separate operations, a crash/failure
    between them burns a seq with no doc written, leaving a PERMANENT hole
    (persisted seqs …,4,6). The contiguous-prefix fold then stalls at the
    hole: snapshots never advance past it and rule 3 keeps re-requesting
    them (liveness stall). Required: allocate the seq ATOMICALLY with the
    insert (Mongo counter advanced in the same transaction as the doc
    write — or seq derived from the insert itself), so a seq exists iff
    its doc exists. Fallback if atomic allocation is infeasible: the fold
    tolerates holes after a confirmed-skip window (backfill marks the
    missing seq as skipped).

## 12. Evidence base (verified vs. adapted)

- **Verified against source:** pi-web-ui (`agent-service.js`: snapshot-driven
  frontend, `message_delta`/`tool_delta` with per-conversation `seq`,
  60 ms/2 s/boundary checkpoint cadence, reconnect = re-request snapshot,
  snapshots droppable under backpressure while deltas must deliver);
  ds-web-ui (`agent-service.js` + `dsh-client.js`: same UI protocol bridged
  to the DSH runtime over stdio JSON-RPC, live/replay unified through one
  `onSessionEvent` fold, minimal protocol surface with durable enqueue
  receipt); deepseek-harness SDK protocol docs (`session.event` unfiltered
  event stream, `session.status`, `session/prompt` receipt). These sources
  live outside this repository; the quoted cadence numbers (60 ms/2 s)
  must be re-confirmed against the upstream packages at implementation
  time.
- **Not verified:** Cursor (closed source). This plan does not cite Cursor
  as precedent.
- **Original adaptation work (no precedent):** room-level sequencing and
  snapshot versioning under multi-instance Redis fanout, terminal
  projection gating, and the Phase 1–3 in-place rollout. These are Hybro-
  specific and are the risk surface this plan owns.
