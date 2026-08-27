# Pi-Aligned Turn Lifecycle and Rendering Plan

> Status: Proposed
>
> Replaces: `Room-Stream-Snapshot-Plan.md`
>
> Scope: the existing room SSE stream, public orchestration projection,
> snapshot fold, frontend room reducer, Turn lifecycle projection, Turn Trace,
> final-answer streaming, and Agent Card placement.
>
> Product target: preserve Hybro's user-request-level Turn while making the
> observable execution model and rendering behavior follow Pi as closely as
> Hybro's A2A and room architecture allow.

## 1. Purpose

The existing snapshot-driven room stream solved delivery correctness:

- public events are persisted before broadcast;
- each room has a monotonic `room_seq`;
- clients hydrate from a snapshot and apply ordered deltas;
- gaps trigger snapshot recovery rather than heuristic reconciliation;
- terminal projections and final messages are durable and replayable.

Those mechanisms stay. This plan replaces the content and UI model built on top
of them.

The current activity surface is still control-plane-oriented. It combines
prewritten `processing_status` messages with selected LLM and tool metadata,
then renders them as a diagnostic panel. That does not resemble Pi or Codex:
users see generic status prose instead of the actual observable sequence of
Assistant messages, tool calls, tool results, and the final Assistant message.

This plan makes the room stream carry a Pi-aligned public Agent event model and
makes the frontend fold that model into one authoritative Hybro Turn.

## 2. Non-negotiable decisions

1. **Hybro Turn remains user-request scoped.** A Hybro Turn begins with one
   accepted User Message and ends when that request settles. It may contain
   multiple Pi-style internal turns.
2. **Pi semantics, Hybro transport.** The public content vocabulary follows
   Pi's `message_*`, `tool_execution_*`, and turn lifecycle semantics. Delivery
   continues through Hybro's existing room SSE envelope, `room_events`,
   snapshots, and replay endpoint.
3. **No protocol fork.** Keep the existing stream endpoint and do not add V2
   naming or a parallel delivery channel.
4. **No chain-of-thought exposure.** Public Assistant text means model-produced
   user-visible text only. Reasoning/thinking deltas, raw prompts, credentials,
   and private transport data never enter the public stream.
5. **No fabricated activity history.** Prewritten lines such as “Planning next
   step”, “Evaluating results”, and “Preparing final answer” are not persisted
   or rendered as Turn Trace entries.
6. **Agent Cards remain independent projections.** One card represents one
   orchestration call. Canonical public identity is
   `run_id + opaque_public_call_id`; the private provider/A2A `call_id` remains
   only in backend-owned bindings. Cards do not become Trace nodes and repeated
   calls to one Agent are not merged.
7. **Final Answer is a real streamed Assistant message.** It is not synthesized
   from terminal statuses or discovered only by later DB hydration.
8. **One frontend fold owns Turn state.** Snapshot hydration, live deltas, and
   replay update the same Turn projection. Components do not infer lifecycle
   from unrelated stores or message names.
9. **The final visual order is fixed:**

   ```text
   User Message
   Turn Trace
   Final Answer
   Agent Cards
   ```

10. **Completion collapses activity.** While work is active the Trace is open and
    follows new activity. Once the authoritative Final Answer and run settlement
    are present, it collapses and displays total Turn duration.

## 3. Pi semantics and the Hybro difference

### 3.1 Pinned Pi reference

The semantic reference for this plan is the installed
`@earendil-works/pi-coding-agent` **0.84.2** SDK/RPC/JSON contract documented in
`README.md`, `docs/sdk.md`, `docs/rpc.md`, `docs/json.md`, and
`docs/session-format.md`. Implementation must re-check the installed version
before cutover; a later Pi contract does not silently redefine this plan.

Pi emits message lifecycle events for User, Assistant, and ToolResult messages,
as well as Agent, low-level turn, and tool-execution lifecycle events. One Pi
low-level turn consists of one Assistant response plus any tool calls and tool
results caused by that response:

```text
agent_start
turn_start
message_start / message_update* / message_end
  (Assistant message)
tool_execution_start / tool_execution_update* / tool_execution_end
message_start / message_end
  (ToolResult message)
turn_end
...
agent_end
agent_settled
```

A single prompt may cause several such turns before the Agent settles.

Hybro does **not** copy Pi's wire objects byte-for-byte. It projects a safe
subset through the existing room stream. This distinction is normative:

| Pi 0.84.2 event/field | Hybro public projection | Decision |
|---|---|---|
| User `message_start/end` | accepted durable User Message + `run_started` binding | Existing room message is authoritative; do not duplicate it as Trace content. |
| Assistant `message_start` | `run_event: message_start` | Preserved with stable public `message_id`. |
| Assistant `message_update.assistantMessageEvent` | `run_event: message_update.payload.assistant_message_event` | Preserve public text deltas; omit thinking and tool-argument deltas. |
| Assistant `message_end.message` | `run_event: message_end` | Project public text, stop reason, and Hybro `disposition`. |
| Pi `tool_execution_start/update/end` | same subtype names | Preserve semantics; rename fields to repository-standard snake_case. |
| ToolResult `message_start/end` | `tool_execution_end` checkpoint + private transcript message | Do not duplicate result text in the normal public fold. |
| `turn_start/end` | same subtype names | Preserve internal boundaries under one Hybro Turn. |
| Pi `agent_start` | `run_started` | Renamed because “Agent” already means an A2A specialist in Hybro. |
| Pi `agent_end` | no settlement projection | It may precede retry, compaction, or queued continuation and is not terminal. |
| Pi `agent_settled` | `run_settled` | The only Pi Agent-level terminal semantic projected as Hybro settlement. |
| Pi full `args`/`result` | bounded public summaries | Redacted by design. |
| Pi provider/model/usage | private diagnostics | Not part of normal Turn Trace. |
| Pi `thinking_*` | no public equivalent | Explicitly prohibited. |
| Pi cumulative tool `partialResult` | indexed accumulated `partial_result` | Replace-by-index checkpoint semantics. |

Fields added by Hybro (`room_seq`, `room_event_id`, correlation, disposition,
public opaque call identity) support durable room synchronization and safe
classification. The target is therefore a **Pi-aligned safe projection**, not a
claim of Pi wire compatibility.

Hybro groups that sequence under one user-facing Turn:

```text
Hybro Turn
├─ User Message
├─ Internal turn 1
│  ├─ Assistant commentary
│  ├─ Agent/tool call
│  └─ Agent/tool result
├─ Internal turn 2
│  ├─ Assistant commentary
│  ├─ Agent/tool call
│  └─ Agent/tool result
├─ Internal turn N
│  └─ Final Assistant message
└─ Run settlement
```

The frontend may hide internal `turn_start`/`turn_end` boundaries by default,
but it must preserve them in `internalTurns` so ordering, message/tool
ownership, replay, retries, and future debugging remain correct. The identifiers
are distinct:

```text
hybro_turn_id   = stable user-request/run projection identity
internal_turn_id = one Pi-style model turn within that Hybro Turn
```

## 4. Target user experience

### 4.1 Active Turn

```text
User
What is the weather in Shanghai today?

▼ Turn Trace
  ◌ Thinking…
  ● I’ll ask Weather Agent for the current conditions in Shanghai.
  ◌ Calling Weather Agent
      Input  Current weather in Shanghai
  ✓ Weather Agent returned a result · 2.9s
      Output 28.5°C, clear sky, humidity 80%

Shanghai is currently 28.5°C with clear skies▍

Sources · 1 Agent
  Weather Agent — Completed
```

Rules:

- `Thinking…` is an ephemeral frontend placeholder only. It is never written to
  `room_events`, Mongo message history, or a snapshot.
- The first real Assistant or tool event removes the placeholder.
- Real events append in authoritative room order.
- The current Assistant message streams below the Trace in the Final Answer
  slot. If `message_end` classifies it as tool-using commentary, the frontend
  atomically moves it into the Trace before tool execution begins.
- Agent Cards occupy the source area after the Final Answer slot. Before a final
  exists, the empty slot takes no space and live cards naturally follow Trace.

### 4.2 Settled Turn

```text
User
What is the weather in Shanghai today?

▶ Worked for 18.6s · 1 tool call

Shanghai is currently 28.5°C with clear skies...

Sources · 1 Agent
  Weather Agent — Completed
```

Rules:

- The Trace auto-collapses once both the authoritative final message and run
  settlement have arrived.
- Users can reopen it; a late duplicate or replayed terminal frame must not
  collapse it again after manual reopening.
- Failed and canceled Turns collapse to `Failed after …` or `Stopped after …`.
- `awaiting_input` is not settled. The Trace stays available and records the
  HITL event (e.g. the ask_user marker with its `Waiting for input` state).
  The body/final slot stays empty: question content renders exclusively in
  the composer interaction UI (shadcn Questionnaire), never in the answer
  area.
- Historical completed Turns initialize collapsed without a mount-time flash.

### 4.3 Expanded historical Trace

```text
▼ Worked for 18.6s · 1 tool call
  ● I’ll ask Weather Agent for the current conditions in Shanghai.
  ✓ Weather Agent returned a result · 2.9s
      Input  Current weather in Shanghai
      Output 28.5°C, clear sky, humidity 80%
```

Model/provider/token metadata is not shown in the default Trace. It may remain
available to a separate developer-only diagnostic view.

## 5. Public event contract

### 5.1 Existing room envelope stays

The four-key SSE envelope remains unchanged:

```json
{
  "type": "run_event",
  "timestamp": "2026-08-24T07:30:00Z",
  "room_id": "room-id",
  "data": {}
}
```

Persisted deltas continue to carry delivery metadata inside `data`:

```text
room_seq
room_event_id
parent_event_id (optional)
```

These fields are transport metadata. They are required for ordering, dedup,
gap recovery, and causal inspection, but no normal UI component displays them.

Turn correlation remains outside the public content payload:

```text
run_id
correlation_id (= client_request_id)
```

#### Retained delivery invariants

This document replaces the previous plan as the normative contract and therefore
restates the implemented delivery guarantees:

1. `room_events` is append-only and is the public realtime source of truth.
2. Every public delta is inserted before local or Redis fanout.
3. `room_seq` is allocated per room atomically with event insertion in a Mongo
   transaction. The non-transactional development fallback backfills confirmed
   holes with idempotent `skipped` tombstones.
4. A logical event has one deterministic idempotency key. Delivery retries read
   back and reuse the original `room_seq`/`room_event_id`; they never append a
   second logical delta.
5. Snapshot watermark `N` represents a contiguous folded prefix `0..N`. No event
   above a gap enters the snapshot.
6. `connected` and heartbeat frames advertise the latest room watermark. The
   browser applies snapshot replace plus ordered deltas, buffers a bounded
   reorder window, and requests `?snapshot=1` when a gap persists.
7. Every connection receives `connected` then snapshot before normal fold
   continuity is assumed. Pre-snapshot deltas are buffered. If no snapshot has
   applied within 500 ms, or the first pre-snapshot delta arrives, a one-shot
   bootstrap trigger reconnects to request one; a backpressure-dropped initial
   snapshot therefore cannot leave the client buffering forever.
8. Every recovery request using `?snapshot=1` bypasses the incremental
   checkpoint and fresh-folds the authoritative contiguous `room_events`
   prefix. It cannot repeatedly serve the checkpoint that caused recovery.
9. Slow consumers resync instead of treating a full queue as successful
   delivery. Snapshot frames may be superseded; durable deltas are recovered by
   sequence gap detection.
10. Terminal public facts, including `run_settled`, are emitted only after their
   terminal CAS winner and required durable projection steps have settled.
   Blocked irrecoverable side effects follow the existing terminal projection
   policy; pending/running side effects gate settlement.
11. Final-message outbox completion requires the Mongo message checkpoint and
    an idempotent persisted `agent_response` room event. A transient fanout miss
    does not create another event.
12. Replay remains
    `GET /sse/room/{room_id}/events?after=<room_seq>&limit=N` with the same auth
    as the stream.
13. Event retention/compaction is **out of scope for this cutover**. No existing
    `room_events` delta may be deleted. A future retention design must first add
    a durable base snapshot/checkpoint plus continuity-preserving tombstone or
    equivalent cursor semantics; deleting deltas from a zero-based contiguous
    fold is prohibited.

### 5.2 Pi-aligned `run_event` subtypes

The existing top-level `run_event` frame evolves in place. Its `data.type`
accepts these public subtypes:

```text
run_started
turn_start
message_start
message_update
message_end
tool_execution_start
tool_execution_update
tool_execution_end
turn_end
retry_scheduled
run_waiting_input
run_resumed
run_settled
```

Existing private orchestrator facts and public lifecycle projections remain
free to use other internal kinds. Only the kinds above feed the normal Turn
Trace and final-answer fold.

Payloads are validated as a discriminated public union before they reach
`RunEventNotification`; a bare unconstrained `dict` is not sufficient for this
contract. Every subtype and nested variant has an explicit DTO with required,
optional, and forbidden fields. Validators enforce bounded text, required
correlation, opaque public call IDs, valid indices, terminal-status-dependent
fields, and the exclusion of private/reasoning fields. Contract fixtures cover
every union member and reject unknown fields at the public translator boundary.

Phase 0 also creates a checked inventory of **every** top-level `room_events`
producer and classifies each field as intentional user content, allowlisted
public projection, private/detail-only, or prohibited. Canonical runs may emit
content only through typed sanitized DTOs:

- `message_*`, `agent_response`, and any nested final-message parts use the
  stateful public-text sanitizer;
- `tool_execution_*` and `task_*` use registered safe-summary builders;
- `hitl_request` sanitizes prompts/choices and omits private source/routing IDs;
- `hitl_response`, cancellation, and error/control events use allowlisted status
  and reason enums, never raw exception/reason dictionaries;
- artifact payloads remain private/detail-only unless a registered artifact-type
  projector produces a bounded safe summary;
- content-bearing `processing_status`, `AgentMessageFinal`, raw
  `ArtifactUpdateEvent`, and other legacy DTOs are prohibited for a canonical
  run. A minimal compatibility event is allowed only after its exact DTO has
  passed the same field inventory and contains no free-form content. The
  stale-browser `processing_status` adapter is the sole initial exception shape
  and is defined in §15.

Accepted User Messages are intentional room content governed by room auth; this
inventory prevents duplicating their full prompt into lifecycle, error,
processing, or artifact payloads.

### 5.3 `run_started` and durable root binding

The first public lifecycle fact binds the run to its User Message before any
Assistant or tool activity is emitted:

```json
{
  "type": "run_started",
  "run_id": "run-id",
  "correlation_id": "client-request-id",
  "payload": {
    "hybro_turn_id": "run-id",
    "user_message_id": "user-message-id",
    "started_at": "2026-08-24T07:29:42.100Z",
    "mode": "supervisor"
  }
}
```

`user_message_id` and `correlation_id` are required for all newly produced
Turns. The snapshot fold stores this binding. No frontend fallback may attach a
run to the latest active User Message, a nearby timestamp, or matching content.
Legacy rooms without the binding use the legacy renderer exclusively and never
mix old and canonical events in one Turn.

### 5.4 `turn_start`

```json
{
  "event_id": "public:run:turn:1:start",
  "run_id": "run-id",
  "seq": 1,
  "type": "turn_start",
  "correlation_id": "client-request-id",
  "payload": {
    "internal_turn_id": "model-turn-id",
    "attempt": 1
  }
}
```

This is a fold boundary, not a visible Marker.

### 5.5 `message_start`

The Kernel allocates a stable Assistant `message_id` before provider streaming
begins.

```json
{
  "type": "message_start",
  "payload": {
    "internal_turn_id": "model-turn-id",
    "message_id": "assistant-message-id",
    "role": "assistant"
  }
}
```

The frontend creates `currentAssistant` with empty content. No durable fake
message is created in `message-store` merely to display a spinner.

### 5.6 `message_update`

The public shape follows Pi's delta model:

```json
{
  "type": "message_update",
  "payload": {
    "internal_turn_id": "model-turn-id",
    "message_id": "assistant-message-id",
    "assistant_message_event": {
      "type": "text_delta",
      "content_index": 0,
      "delta_index": 4,
      "start_offset": 0,
      "end_offset": 22,
      "delta": "I’ll ask Weather Agent"
    }
  }
}
```

Public `assistant_message_event.type` initially permits only `text_delta`.
`message_start` and `message_end` already provide the public start and
checkpoint/end boundaries, so separate nested `text_start`/`text_end` variants
would be redundant. A `text_delta` requires exactly `content_index`,
`delta_index`, `start_offset`, `end_offset`, and `delta`; all reasoning,
thinking, tool-argument, and provider-native fields are forbidden. Tool-call
arguments are not public until they have been completely assembled, validated,
authorized, and redacted.

Each published chunk has a deterministic event identity derived from:

```text
run_id + internal_turn_id + message_id + content_index
+ start_offset + end_offset + content_digest
```

`start_offset` and `end_offset` are Unicode code-point offsets in the assembled
**sanitized public text** and are included in the event. Before chunk identity or
offsets are assigned, a stateful public-text sanitizer removes configured secret
values and credential/token patterns, including matches split across provider
chunks. It holds the bounded detector look-behind needed to avoid publishing a
prefix of a secret; semantic-boundary flushes finalize that buffer. The same
sanitizer and replacement rules produce `message_end.text` and the durable final
message, so checkpoints cannot diverge from streamed public text. `delta_index`
remains a monotonic convenience cursor, not the sole identity. The event
publisher persists the logical event before fanout, so delivery retries reuse
the same `room_seq`/`room_event_id` and do not duplicate text.

To avoid one Mongo document per provider token, the public projection coalesces
provider text deltas. It flushes when any of these conditions occurs:

- 50–80 ms have elapsed since the previous public chunk;
- the accumulated chunk reaches 256–512 UTF-8 characters;
- a tool call begins;
- the message, attempt, run, or connection terminates.

The preallocated message identity and active attempt record are checkpointed in
`OrchestratorRunState` before the provider request. Chunk append and Run-state
CAS are separate writes, so `room_events`—not the Run-state offset—is the
recovery authority. After a chunk append succeeds, the publisher may advance an
advisory greatest-published-offset checkpoint; it must never advance that
checkpoint before append acknowledgement.

On recovery, the backend first reads back the one deterministic
`message_end` idempotency key derived from
`run_id + internal_turn_id + message_id + message_end`, then fresh-folds the
authoritative contiguous `room_events` prefix for the active message. If direct
readback finds the terminal above a sequence gap, recovery heals/tombstones the
lower confirmed hole under §5.1 and advances the contiguous fold through that
terminal before deciding what is missing. It never emits a competing terminal
merely because an already-persisted one is temporarily above the snapshot
watermark. The fold validates offsets and derives exact public text, greatest
end offset, and the existing terminal. A stale lower Run-state offset is
repaired from that fold and can never cause `message_end` to replace longer text
with a shorter checkpoint. If Run state claims an offset absent from both the
durable prefix and deterministic event-key readback, recovery treats the
unacknowledged claim as unpublished and records a repair diagnostic.

An existing valid `message_end` is adopted as authoritative and never followed
by a second, contradictory terminal. Recovery resumes from the next missing
semantic boundary: commentary verifies the already durable private Assistant
and source-ordered declared Tool batch before idempotently resuming per-entry
validation/authorization/acceptance; final emits or
adopts `turn_end(completed)` before durable final projection; and error/aborted
continues at `turn_end` plus retry/settlement. Missing commentary prerequisites
are a protocol failure and are never reconstructed from public text.

Only when neither deterministic readback nor the healed contiguous fold contains
a terminal does recovery emit one deterministic `message_end(aborted)` with the
fresh-folded public text. If the private Assistant checkpoint contains a
declared Tool batch, recovery then persists exactly one source-ordered private
`process_restart_before_public_terminal` error ToolResult for every declaration;
none receives a public Tool row because per-entry acceptance begins only after a
public commentary terminal. It then emits `turn_end(aborted)` and deterministic
`retry_scheduled(error_class="process_restart")` before starting the new attempt.
That retry fact is the public causal successor used by settlement validation. The provider stream itself is never resumed with reconstructed
timing boundaries. Crash tests cover both sides of terminal append and its
Run-state CAS for every disposition.

Exact thresholds are configuration constants with focused tests, not public
protocol values.

### 5.7 `message_end`

`message_end` is authoritative for one Assistant message and classifies it only
after the assembler knows whether it contains tool calls.

Tool-using Assistant message:

```json
{
  "type": "message_end",
  "payload": {
    "internal_turn_id": "model-turn-id",
    "message_id": "assistant-message-id",
    "stop_reason": "tool_use",
    "disposition": "commentary",
    "text": "I’ll ask Weather Agent for the current conditions in Shanghai."
  }
}
```

Final Assistant message:

```json
{
  "type": "message_end",
  "payload": {
    "internal_turn_id": "model-turn-id",
    "message_id": "assistant-message-id",
    "stop_reason": "stop",
    "disposition": "final",
    "text": "Shanghai is currently 28.5°C with clear skies."
  }
}
```

Allowed dispositions:

```text
commentary
final
error
aborted
```

Every `message_end` requires the common identity fields, normalized snake-case
`stop_reason`, `disposition`, and bounded `text` checkpoint (which may be
empty). The valid combinations are closed:

| Disposition | Allowed `stop_reason` | Conditional fields |
|---|---|---|
| `commentary` | `tool_use` | `error_summary` forbidden |
| `final` | `stop` | `error_summary` forbidden |
| `error` | `length`, `content_filter`, `error`, `deferred` | sanitized `error_summary` required |
| `aborted` | `aborted` | `error_summary` forbidden |

Provider-native spellings such as `toolUse` normalize at the private-to-public
translator. A no-tool response stopped for length/content filtering is not a
successful final. `text` contains only public, user-visible Assistant text and
lets a snapshot or replay reconstruct the message without depending on every
partial chunk. Raw exception text is never a valid `error_summary`.

If a tool-using Assistant message contains no text, no commentary Marker is
fabricated. The Trace proceeds directly to the tool event.

### 5.8 `tool_execution_start`

This event is emitted only after the tool call has been assembled, schema
validated, authorized, accepted in the durable call ledger, and publicly
redacted.

```json
{
  "type": "tool_execution_start",
  "payload": {
    "internal_turn_id": "model-turn-id",
    "tool_call_id": "inv_opaque_public_id",
    "tool_name": "Weather Agent",
    "input": {
      "task": "Current weather in Shanghai"
    }
  }
}
```

Requirements:

- `tool_call_id` is a stable opaque public identity, never the private provider
  or A2A call ID.
- `tool_name` is the user-facing Agent/tool label, never an internal registry
  symbol when a public label exists.
- `input` is produced by a deny-by-default, tool-specific public-summary
  builder with an allowlist of fields and transformations. A tool without a
  registered safe builder emits an empty/generic input summary rather than
  traversing arbitrary arguments.
- Length bounds and schema validation are necessary but are not treated as
  privacy controls. Secret-bearing scalar values, URLs, user data, nested
  private objects, auth references, routing metadata, and credentials are
  omitted.
- `partial_result` and terminal `result` use the same tool-specific allowlisted
  projection policy and safe fallback; the canonical path must not reuse a
  generic recursive scalar-copy translator.

### 5.9 `tool_execution_update`

This optional event carries actual tool/Agent progress, not server-authored
stage filler.

```json
{
  "type": "tool_execution_update",
  "payload": {
    "internal_turn_id": "model-turn-id",
    "tool_call_id": "inv_opaque_public_id",
    "tool_name": "Weather Agent",
    "update_index": 2,
    "status": "running",
    "partial_result": "Retrieved current conditions; formatting response."
  }
}
```

Rules:

- Emit only when the underlying tool or Agent produced meaningful public
  progress.
- `status` is `running` or `suspended`. A later higher-index update may move a
  suspended call back to `running` after HITL resume.
- `partial_result` is an accumulated bounded checkpoint, matching Pi's tool
  update semantics; it replaces the previous checkpoint for that call.
- `update_index` is monotonic per public `tool_call_id`. Duplicate or older
  indices are ignored; a gap is recovered through room-level sequence/snapshot
  recovery, not by concatenating text.
- Absence of updates is normal and must not be filled with canned prose.

### 5.10 `tool_execution_end`

```json
{
  "type": "tool_execution_end",
  "payload": {
    "internal_turn_id": "model-turn-id",
    "tool_call_id": "inv_opaque_public_id",
    "tool_name": "Weather Agent",
    "outcome": "completed",
    "result": "28.5°C, clear sky, humidity 80%.",
    "is_error": false,
    "duration_ms": 2900
  }
}
```

Allowed wire outcomes are `completed`, `failed`, and `canceled`, with closed
field relationships:

| Outcome | `is_error` | `failure_reason` | `result` |
|---|---:|---|---|
| `completed` | `false` | forbidden | required bounded safe summary, possibly empty |
| `failed` | `true` | optional allowlisted enum | required bounded safe summary, possibly empty |
| `canceled` | `false` | forbidden | required empty string |

Durable Hybro/A2A terminal states `rejected` and `expired` normalize to
`outcome="failed"` with `failure_reason="rejected"|"expired"`. Raw exception
text is never a valid reason or result. This guarantees every accepted ledger
call has one valid public end and cannot remain open at settlement. The result
is projected through the tool-specific safe-summary policy. Full private Agent
output is available only through an authenticated detail fetch backed by private
storage; it is not copied into Agent Card room events or snapshots. Suspension
is non-terminal and is represented by
`tool_execution_update(status="suspended")`, never by a fake end.

### 5.11 `turn_end`

```json
{
  "type": "turn_end",
  "payload": {
    "internal_turn_id": "model-turn-id",
    "message_id": "assistant-message-id",
    "tool_call_ids": ["inv_opaque_public_id"],
    "status": "completed"
  }
}
```

Allowed statuses are `completed`, `error`, and `aborted`. The DTO is closed:

- `internal_turn_id`, `status`, and `tool_call_ids` are always required.
  `tool_call_ids` is the source-ordered inventory of public IDs durably accepted
  into the call ledger—exactly the calls that emitted `tool_execution_start`—or
  `[]`. It is not the raw declared model batch.
- The private Assistant checkpoint separately preserves the complete
  source-ordered declared batch and each entry's validation/authorization/
  acceptance/processing state. Processing is fail-fast in source order. The
  first invalid or pre-acceptance-rejected entry receives a deterministic
  private error ToolResult but no public call ID/Tool row; every later unstarted
  declaration receives a deterministic private
  `skipped_due_to_prior_rejection` error ToolResult and is never accepted or
  executed. A call accepted into the ledger but later rejected/expired remains
  in `tool_call_ids` and receives both its private error ToolResult and the
  normalized failed public `tool_execution_end` from §5.10.
- Every declared ToolCall has exactly one source-ordered private ToolResult before
  `turn_end`, including invalid and skipped entries. Every accepted call also has
  exactly one durable public `tool_execution_end`. If an accepted sibling is
  suspended on HITL when another entry fails or cancellation wins, all unresolved
  HITL requests terminalize and clear ownership before that Tool end. Only after
  the complete private result sequence and all accepted public ends are durable
  may the turn close `error`/`aborted` and retry/settle.
- `completed` requires `message_id` matching the terminal Assistant message. A
  commentary turn may close completed only after every declared call has its
  private ToolResult and every accepted call has its durable public end; a final
  turn closes completed immediately after its final `message_end` and before
  final projection.
- `error`/`aborted` require matching `message_id` when `message_start` occurred.
  If `turn_start` occurred but failure/cancellation won before any Assistant
  started, `message_id` is omitted and `tool_call_ids` is `[]`.
- Recovery/adoption replays the fail-fast state machine idempotently from the
  durable declared batch, reuses existing ledger/public-ID bindings and private
  result checkpoints, and never guesses or re-executes an accepted side effect.
  Persisted per-entry state decides whether later declarations remain processable
  or are deterministically skipped; the public terminal inventory is always
  derived from durable accepted bindings.

This is a fold/checkpoint boundary and is hidden in the normal UI. It is a
Hybro-safe projection rather than a byte-for-byte Pi `turn_end`, because Pi's
native event always carries a message while Hybro must close a durably started
turn that can fail before Assistant construction.

### 5.12 Retry and HITL lifecycle

A real model retry is public only as a fact:

```json
{
  "type": "retry_scheduled",
  "payload": {
    "internal_turn_id": "model-turn-id",
    "attempt": 2,
    "delay_ms": 1000,
    "error_class": "provider_timeout"
  }
}
```

The interrupted Assistant message is checkpointed as `error` or `aborted`, then
its matching `turn_end(status="error"|"aborted")` closes the internal turn
before `retry_scheduled` and the next `turn_start`. Retry belongs to the closed
`internal_turn_id`. Crash recovery follows the same sequence; it never leaves an
old internal turn active in the snapshot. Retry text is rendered only from this
event; the server does not manufacture a generic progress history.

HITL uses the existing durable per-question `hitl_request`/`hitl_response`
content events and adds interaction-level control boundaries:

```text
run_waiting_input {
  interaction_id, request_ids[], requested_at
}
run_resumed {
  interaction_id, resolved_request_ids[], resumed_at
}
```

Each canonical `hitl_request` and `hitl_response` carries explicit Turn-root
correlation: `run_id`, `client_request_id`, and `related_user_message_id`, in
addition to `interaction_id`, `request_id`, and reused public `message_id`.
Requests also carry `question_index`, `question_count`, bounded sanitized
`prompt`, allowlisted `prompt_type`, bounded sanitized `choices`, allowlisted
`source`, and an optional public `agent_label`. Private `agent_id`, step/routing
IDs, and raw metadata are forbidden. A response sets one status: `responded`,
`expired`, `canceled`, or `error`; it does not invent a separate response message
or expose answer content. The fold rejects any request/response whose three root
fields do not exactly match the bound canonical Turn. When the durable aggregate has a safe
opaque answer reference, the response may carry `answer_ref`; otherwise status
is sufficient for the normal HITL surface. Legacy `resolved` normalizes to
`responded` at the canonical translator.

The causal order is normative for a resumable interaction:

```text
tool_execution_update(suspended)*
→ persisted hitl_request* for every required request
→ run_waiting_input referencing the complete ordered request_ids[]
→ persisted hitl_response(responded)* for every required request
→ durable answer application succeeds
→ run_resumed referencing the complete resolved_request_ids[]
→ tool_execution_update(running)*
```

The Turn fold owns an ordered `hitl_interactions` history plus
`active_interaction_id`. This supports questionnaires and multiple HITL rounds
under one Hybro Turn. `run_waiting_input` is invalid unless every referenced
request has already folded for the same run/correlation and question indices are
complete. `run_resumed` is invalid unless every required request has a responded
resolution and durable application succeeded. It clears
`active_interaction_id`, restores `active`, and permits continuing calls to
receive a higher-index running update. For expiry, cancellation, or error, the producer first emits a terminal
`hitl_response` for **every** unresolved request in the required set. On folding
the last required terminal response, the interaction state becomes `error`,
`canceled`, or `expired` (precedence: error, then canceled, then expired) and
`active_interaction_id` is cleared. No `run_resumed` is emitted; only after that
clear may the owning run continue its failed/canceled terminal sequence.
Snapshot and replay preserve all root correlation, request IDs, reused message
IDs, sanitized question display fields, per-request statuses, optional opaque
answer references, and round order, so the dedicated HITL surface does not
depend on a REST/message-store overlay to infer ownership or lifecycle.

Before failed/canceled `run_settled`, the backend emits, in order, an
authoritative `message_end(error|aborted)` for any open Assistant, terminal HITL
responses for every unresolved request and clears interaction ownership,
terminal `tool_execution_end` for every open Tool, and
`turn_end(error|aborted)` for every active internal turn. HITL ownership always
closes before terminal Tool/call authority is exposed. Only then may it persist
`run_settled`. Settlement never invents child terminal state on the frontend.

### 5.13 `run_settled`

```json
{
  "type": "run_settled",
  "payload": {
    "status": "completed",
    "started_at": "2026-08-24T07:29:42.100Z",
    "settled_at": "2026-08-24T07:30:00.700Z",
    "duration_ms": 18600,
    "final_message_id": "assistant-message-id"
  }
}
```

Allowed statuses:

```text
completed
failed
canceled
```

Terminal fields form a closed conditional DTO:

| Status | Required fields | Forbidden fields |
|---|---|---|
| `completed` | `final_message_id` matching the committed final | `failure_code`, `error_summary`, `cancellation_code` |
| `failed` | allowlisted `failure_code` and bounded sanitized `error_summary` | `final_message_id`, `cancellation_code` |
| `canceled` | allowlisted `cancellation_code` | `final_message_id`, `failure_code`, `error_summary` |

Initial `failure_code` values are `budget_exhausted`, `provider_error`,
`assembly_error`, `tool_failure`, `hitl_error`, `rejected`, and
`internal_error`; unknown/private failures normalize to `internal_error` with a
generic sanitized summary. Initial cancellation codes are `user_requested`,
`room_closed`, `shutdown`, and `policy`; unknown values normalize to `policy`.
Raw `terminal_reason`, cancellation prose, and exception text are prohibited.
This makes failures before any Assistant message visible without fabricating an
Assistant event.

Every settlement status requires no open Assistant or Tool, no active HITL
interaction, and no active internal turn. Completed requires at least one
internal turn and the **final** turn completed; earlier turns may be
error/aborted only when each has a causally ordered `retry_scheduled` (including
`process_restart`) that ultimately leads to the final completed turn.
Failed/canceled may contain zero internal turns when settlement won before any
`turn_start`; otherwise the final started turn must have closed as
error/aborted, including the no-Assistant `turn_end` form from §5.11. DTO,
producer, snapshot, and frontend fold validation enforce these conditions.

This replaces `processing_status` as the authoritative user-Turn terminal
signal. `duration_ms` is server-authored and authoritative. It covers the
Hybro user request from accepted User Message to settled Run. HITL waiting time
is included in wall-clock duration for the initial implementation; if active
compute time is later required, it must be a separate explicitly named field.

### 5.14 Final durable message

The existing `agent_response` remains the durable room-message checkpoint:

```json
{
  "type": "agent_response",
  "data": {
    "message_id": "assistant-message-id",
    "agent_id": "system:hybro",
    "client_request_id": "client-request-id",
    "related_message_id": "user-message-id",
    "content": "Shanghai is currently 28.5°C with clear skies."
  }
}
```

Its transport and correlation fields are not Pi content; they are Hybro's
reliable room projection envelope. The UI does not display them.

The final answer is considered committed only when the folded `agent_response`
matches one canonical Turn by all of these fields:

```text
data.client_request_id == turn.client_request_id
data.related_message_id == turn.user_message_id
data.message_id == turn.final_answer.message_id
```

The matching `final_answer` must already come from `message_end(final)` for that
run. Its checkpoint content is replaced by the durable response content; content
equality is not a correlation mechanism. An identity-matching direct-Agent
response may commit because the direct path first creates that stable
Assistant/final projection. Unrelated or card-only specialist `agent_response`
events remain detail entities and cannot commit a Turn final. The causal
terminal order is:

```text
message_end(final)
→ turn_end(completed)
→ Mongo final message checkpoint
→ persisted agent_response room event
→ all required terminal projection steps settled
→ persisted run_settled room event
```

`parent_event_id` links `agent_response` to the final `message_end` and
`run_settled` to the final durable checkpoint/terminal fact. A failure after any
boundary resumes from the same outbox intent and deterministic event identity.

The Trace auto-collapses when both conditions hold:

```text
final agent_response committed
AND
run_settled(completed) folded
```

`message_end(disposition="final")` may finish visible streaming first, but it
is not by itself the durable settlement boundary. Failed/canceled Turns have no
final-commit requirement and collapse on their authoritative `run_settled` only
after all Assistant/Tool/HITL states and the active internal turn have been
terminalized.

### 5.15 Agent Card events

Agent Cards are first-class folds of the canonical execution stream. New Runs
never emit `task_submitted`/`task_update`; those frames are historical
read-only compatibility events for Rooms created before the cutover.

One orchestration call remains one card, but its public/durable message ID uses
the same opaque derived invocation identity as Trace:

```text
orchestrator:{run_id}:{opaque_public_call_id}
```

`tool_execution_*` payloads carry the closed execution identity the card folds:

```json
{
  "type": "tool_execution_start",
  "payload": {
    "internal_turn_id": "turn-1",
    "tool_call_id": "inv_weather_0001",
    "tool_name": "Weather Agent - Get Current Weather",
    "input": { "task": "Current weather" },
    "execution_kind": "agent",
    "target": { "name": "Weather Agent", "source": null },
    "request_summary": "Check the weather in San Jose"
  }
}
```

```json
{
  "type": "tool_execution_end",
  "payload": {
    "tool_call_id": "inv_weather_0001",
    "execution_kind": "agent",
    "target": { "name": "Weather Agent", "source": null },
    "outcome": "completed",
    "result": "Clear, 22C",
    "detail_available": true,
    "duration_ms": 1530
  }
}
```

Rules:

- `execution_kind == "agent"` requires a `target`; `"tool"` must not carry one.
- `target.name` is the exact base Agent Card name. Registry ids, provider/A2A
  call ids, and endpoint scopes stay private; cards correlate through the
  opaque public call id only.
- `request_summary` is the safe model-authored `task` question (≤ 1000 chars,
  secret-sanitized). Raw ToolResult text/data never enters public events.
- `detail_available` is true only for completed Agent Executions. The full
  question/output is fetched through the authenticated detail API
  (§5.10) and never enters SSE or snapshots.
- Trace renders Agent Executions as orchestrator log lines (called / waiting
  / completed) without request/result details; the card owns those.
- Snapshot activity carries the same `execution_kind`/`target_name`/
  `request_summary`/`detail_available` fields, so live, refresh, and history
  fold one model.

### 5.16 Structured supervisor `ask_user`

The kernel exposes a synthetic structured action
`request_user_input(question, choices?)` to canonical Runs whose Supervisor
model supports structured actions:

1. The model declares the action as a normal tool call.
2. The kernel validates it against the closed schema (fail closed like any
   malformed Agent declaration) and creates one ordered interaction through
   the unified Execution HITL service with
   `application_route == SUPERVISOR_RUN` and `orchestration_run_id == run_id`.
3. The tool entry is suspended (`input_required`) and the Run transitions to
   `awaiting_user`; the durable HITL lifecycle publishes `hitl_request` and
   the canonical control `run_waiting_input`.
4. The recorded answer resumes the Run as the deterministic ToolObservation
   for the synthetic call (`run_resumed`, then the normal Tool-resumed/terminal
   flow). Replays collapse into the already-processed observation path.
5. The Supervisor answer is a durable ToolResult in the model transcript, so
   the continuing model turn sees the user answer exactly like any Tool
   output.

The ask_user declaration persists `source_step_id == call_id`; the HITL
application effect resolves it into the suspended call identity. Missing or
stale identities fail closed with `ContinuationLostError` and retry through
the journaled supervisor effect command.

Delivery notes:

- Answer routing is dual: the facade tries the orchestrator A2A ingress
  first; interactions it does not own (`OrchestratorHITLNotOwnedError`)
  fall back to the unified HITL manager, which is the authoritative path
  for supervisor-run interactions.
- The aggregate-owned run-answer projector publishes the canonical
  `run_resumed` control and marks the interaction's run projection applied,
  which unblocks the durable `APPLIED` transition (the HITL reconciler
  converges replays and restarts).
- Resuming a suspended Run that outlived its process-local session re-enters
  through the run-addressed observation sink (`session_host.observation_sink`),
  not through a live session lookup.

## 6. `processing_status` retirement

### 6.1 Immediate semantic change

`processing_status.details.message` stops feeding user-visible history. The
frontend does not turn it into Trace entries, work logs, or final-answer hints.

### 6.2 Responsibilities that must move first

Before emission can be deleted, these current responsibilities need explicit
owners:

| Current responsibility | New authority |
|---|---|
| Turn active/send guard | `run` lifecycle + unresolved `message_start`/tool execution |
| Turn terminal state | durable `run_settled` |
| Final answer readiness | `message_end(final)` + durable `agent_response` |
| Cancel completion | `run_settled(status="canceled")` |
| Failure banner/state | `message_end(error)` / tool error / `run_settled(failed)` |
| HITL pause | existing durable HITL request/response projection |
| Sidebar active room state | public `runs` projection, not browser-inferred logs |
| Snapshot restoration | snapshot `turns` projection described in §8 |
| Composer unlock | the single predicate below, never card completion guesses |

The one composer-release predicate is:

```text
completed: finalCommitted && runSettled(completed)
failed:    runSettled(failed) && no open Assistant/Tool/HITL/internal-turn state
canceled:  runSettled(canceled) && no open Assistant/Tool/HITL/internal-turn state
awaiting_input: release the send guard only into the dedicated HITL response UI,
                not the normal composer
```

No other event unlocks the normal composer.

### 6.3 Removal end state

After all production paths emit the new lifecycle:

- stop creating `ProcessingStatusEvent` for orchestrator lifecycle messages;
- remove persisted `status_logs` from the normal snapshot shape;
- remove `ProcessingStatusLogEntry`, `processingStatusLogs`, and their builders;
- remove `ProcessingStatusLog` from the conversation surface;
- remove `handleProcessingStatus` Turn correlation and terminal stamping;
- remove tests that pin canned log text;
- keep no hidden second terminal authority.

If a legacy Fast/Queue path cannot migrate in the same phase, its
`processing_status` handler may temporarily update control state behind a
feature-independent adapter, but it must not render and must have a documented
removal issue. The final acceptance gate requires zero production dependence.

## 7. Backend implementation

### 7.1 Model-stream projection

Current behavior consumes `ModelStreamEvent.text_delta` only inside
`ModelStreamAssembler` and creates the Assistant message ID after streaming.
The cutover therefore changes `execution/orchestrator/models.py`, Run-state
serialization/repository code, and recovery in addition to the Kernel: the
active internal turn, Assistant message identity, provider attempt, public
stream state, and greatest published text offset become durable recoverable Run
fields.

Change it to:

1. allocate and checkpoint stable `internal_turn_id` and Assistant `message_id`
   before the model stream;
2. emit public `turn_start` and `message_start`;
3. feed provider events to the assembler and public coalescer in parallel;
4. record private reasoning/usage as today, but never project reasoning publicly;
5. flush public text chunks as `message_update`;
6. build and validate the `AssistantMessage` with the preallocated ID;
7. durably persist the private Assistant transcript checkpoint and, for
   commentary, the complete source-ordered **declared** Tool-call batch before
   public `message_end`; each entry records later validation/authorization/
   acceptance state, and this checkpoint is the only recovery authority for
   continuing Tool preparation;
8. emit `message_end` with the authoritative public text and disposition;
9. in source order, idempotently validate, authorize, accept into the durable
   ledger, allocate/reuse the public ID, and execute each accepted call with
   `tool_execution_*` projection. On the first pre-acceptance failure, persist
   its private error ToolResult, mark all later unstarted declarations skipped
   with private error ToolResults, terminalize any accepted siblings (including
   HITL ownership before Tool ends), then close the turn error and enter
   retry/failed settlement; non-accepted entries never get a public Tool row;
10. emit `turn_end` after all results for commentary, or immediately after the
    no-tool final/error/aborted message, before any final projection or
    settlement.

The public stream is a projection of the same ordered Kernel events, not an
independent observer that guesses lifecycle afterward.

### 7.2 Assistant classification

Classification occurs only after the assembled message is valid:

| Outcome | Disposition |
|---|---|
| valid message with tool calls | `commentary` |
| valid message without tool calls and run completes | `final` |
| provider/assembly terminal error | `error` |
| cancellation | `aborted` |

Mixed Assistant text plus tool calls is valid. Its text becomes commentary.
A final message is the last valid no-tool Assistant message selected by the
Kernel's normal completion rule.

The state machine is closed for every disposition:

- `commentary`: checkpoint text into activity, clear `currentAssistant`, then
  wait for the declared Tool batch; after ToolResult persistence, start the next
  internal turn.
- `final`: keep/promote the text in the Final Answer slot and schedule the final
  durable message projection.
- `error`: checkpoint a failed Assistant activity item, clear the candidate
  Final Answer, and either emit `retry_scheduled` or settle failed.
- `aborted`: clear the candidate Final Answer, terminalize any open Tool rows,
  and either start a recovery attempt or settle canceled.

A successful `run_settled(completed)` is invalid unless a final Assistant
message has been durably committed. A tool-using Assistant can never directly
produce successful settlement, including budget/wrap-up branches; those
branches must run a final no-tool Assistant turn or settle failed/canceled with
an explicit user-facing terminal answer projection. `budget_exhausted`,
`rejected`, and irrecoverable provider errors map to wire status `failed` with a
bounded public error summary.

### 7.3 Tool lifecycle projection

Replace normal-UI use of:

```text
orchestrator_decision
tool_call_accepted
tool_call_completed
```

with Pi-aligned:

```text
tool_execution_start
tool_execution_update
tool_execution_end
```

Private facts and compatibility event types may remain during migration, but
only one public family feeds the canonical Turn fold. Do not render duplicate
old and new nodes.

### 7.4 Final delivery

`MongoFinalMessageProjector` remains the durable final-message writer. It must:

- persist the full final message under the same preallocated message ID;
- emit exactly one idempotent `agent_response` room event;
- retain `client_request_id` and `related_message_id` for correlation;
- treat persisted `room_event_id` as the outbox success boundary;
- converge after fanout failure through snapshot/replay without duplicating the
  final event.

The streaming public events and final checkpoint share the message ID. No
content matching is used.

### 7.5 Direct/Fast and A2A paths

The contract must work outside Supervisor mode:

- A direct Agent dispatch emits `tool_execution_start/end` around the A2A call.
- A direct single-Agent response may become the Final Answer without inventing a
  HYBRO synthesis message, but it still has a stable Assistant/final projection.
- Actual A2A streaming updates may emit bounded `tool_execution_update`.
- HITL suspends the Hybro Turn and resumes the same correlation after the answer.
- Failures and cancellation always end in one durable `run_settled` fact.

No mode may require `processing_status` for correctness after migration.

Production-path inventory for Phase 0/4:

| Mode/lifecycle | Current entrypoints that must migrate |
|---|---|
| Room admission and request persistence | `execution/facade.py`, `execution/run_command_handler.py` |
| Single production mode/profile routing | `execution/orchestrator_routing.py`, `execution/adapters/session_host.py` |
| Canonical Fast/Ultimate orchestrator | `execution/orchestrator/session.py`, `execution/orchestrator/kernel.py`, `execution/orchestrator/profiles.py`, `container.py` |
| A2A direct/relay observations | `execution/orchestrator/a2a_runtime/`, `execution/dispatch/agent_ingress_router.py`, `execution/dispatch/task_notifications.py` |
| HITL suspend/resume | `execution/hitl/`, `execution/orchestrator/a2a_runtime/hitl.py` |
| Cancellation | `execution/cancellation/`, `execution/orchestrator/a2a_runtime/cancellation.py` |
| Durable terminal/final projection | `execution/terminal_projection.py`, `dal/orchestrator/projection.py` |

Phase 0 must resolve any renamed concrete files and turn this table into a
checked producer matrix. Phase 4's zero-dependence gate scans these production
entrypoints, not only the orchestrator package.

## 8. Snapshot and replay projection

The delivery foundation remains:

```text
room_events append
→ incremental RoomEventFold
→ snapshot watermark
→ ordered frontend deltas
```

The snapshot gains a canonical `turns` section. Existing `messages`, `tasks`,
`runs`, `hitl`, and streaming sections may remain during migration, but normal
Turn rendering reads `turns` after cutover. Presence of
`turn_lifecycle_schema: 1` and `turns` is the capability signal; no V2 endpoint
or product naming is introduced. A checkpoint created without this schema is
invalidated/refolded before serving canonical Turns, so rolling deployments
never combine old trace materialization with new lifecycle deltas.

```ts
interface RoomSnapshotTurn {
  hybro_turn_id: string
  run_id: string
  user_message_id: string
  client_request_id: string
  state: 'active' | 'awaiting_input' | 'completed' | 'failed' | 'canceled'
  started_at: string
  settled_at: string | null
  duration_ms: number | null
  terminal_code: string | null
  terminal_summary: string | null
  internal_turns: Array<{
    internal_turn_id: string
    attempt: number
    message_ids: string[]
    tool_call_ids: string[]
    status: 'active' | 'completed' | 'error' | 'aborted'
  }>
  activity: RoomSnapshotActivityItem[]
  current_assistant: RoomSnapshotAssistant | null
  final_answer: RoomSnapshotAssistant | null
  final_committed: boolean
  hitl_interactions: Array<{
    interaction_id: string
    state: 'awaiting_input' | 'resumed' | 'expired' | 'canceled' | 'error'
    request_ids: string[]
    requests: Array<{
      request_id: string
      message_id: string
      question_index: number
      question_count: number
      prompt: string
      prompt_type: string
      choices: string[]
      source: string
      agent_label: string | null
      status: 'requested' | 'responded' | 'expired' | 'canceled' | 'error'
      answer_ref: string | null
    }>
    requested_at: string
    resumed_at: string | null
  }>
  active_interaction_id: string | null
  agent_call_message_ids: string[]
}
```

Snapshot materialization pages/folds the complete contiguous event range after
its checkpoint; a storage query limit must never silently truncate a Turn. Tests
must use histories larger than one read page and mixed old/new checkpoint
fixtures.

The backend snapshot fold and frontend live reducer consume equivalent event
semantics:

- `message_update` appends by `message_id/content_index/delta_index`;
- `message_end` replaces accumulated text with its authoritative checkpoint;
- commentary messages enter `activity`;
- final messages enter `final_answer`;
- tool start/update/end fold by opaque `tool_call_id`;
- `hitl_request`/`hitl_response` fold per-question identity/status into ordered
  interaction history, while waiting/resume boundaries set or clear
  `active_interaction_id`;
- only an `agent_response` matching the Turn root and provisional final message
  commits the final room message;
- `run_settled` sets terminal status, duration, and sanitized failure/cancellation
  code/summary fields;
- `task_*` updates Agent Card projections by message ID.

A snapshot arriving mid-stream must restore exactly the same visible text and
activity as an uninterrupted client. A final snapshot must initialize Trace
collapsed without waiting for client effects.

## 9. Frontend Turn projection

### 9.1 Single authoritative state

Introduce a Turn projection owned by the room reducer rather than reconstructing
lifecycle independently in multiple components.

```ts
type TurnLifecycleState =
  | 'active'
  | 'awaiting_input'
  | 'completed'
  | 'failed'
  | 'canceled'

interface HITLQuestionProjection {
  requestId: string
  messageId: string
  questionIndex: number
  questionCount: number
  prompt: string
  promptType: string
  choices: string[]
  source: string
  agentLabel?: string
  status: 'requested' | 'responded' | 'expired' | 'canceled' | 'error'
  answerRef?: string
}

interface HITLInteractionProjection {
  interactionId: string
  state: 'awaiting_input' | 'resumed' | 'expired' | 'canceled' | 'error'
  requestIds: string[]
  requests: HITLQuestionProjection[]
  requestedAt: string
  resumedAt?: string
}

interface TurnProjection {
  id: string // hybroTurnId
  runId: string
  roomId: string
  userMessageId: string
  clientRequestId: string
  state: TurnLifecycleState
  startedAt: string
  settledAt?: string
  durationMs?: number
  terminalCode?: string
  terminalSummary?: string
  internalTurns: InternalTurnProjection[]
  activity: TurnActivityItem[]
  currentAssistant?: AssistantProjection
  finalAnswer?: AssistantProjection
  finalCommitted: boolean
  hitlInteractions: HITLInteractionProjection[]
  activeInteractionId?: string
  agentCallMessageIds: string[]
}
```

Activity union:

```ts
type TurnActivityItem =
  | {
      kind: 'assistant'
      id: string
      internalTurnId: string
      text: string
      status: 'streaming' | 'completed' | 'error' | 'aborted'
      order: number
    }
  | {
      kind: 'tool'
      id: string
      internalTurnId: string
      toolCallId: string
      label: string
      input?: unknown
      partialResult?: string
      result?: string
      isError?: boolean
      durationMs?: number
      status: 'running' | 'suspended' | 'completed' | 'failed' | 'canceled'
      order: number
    }
  | {
      kind: 'retry'
      id: string
      internalTurnId: string
      attempt: number
      delayMs?: number
      errorClass?: string
      order: number
    }
```

`order` is derived from authoritative `room_seq`, not local receive time.
Snapshot items preserve the same order.

### 9.2 Fold behavior

| Event | Fold result |
|---|---|
| accepted User Message | create optimistic shell only; canonical identity waits for `run_started` |
| `run_started` | bind run/client request/User Message and create canonical active Hybro Turn |
| `turn_start` | append `InternalTurnProjection`; no visible item |
| `message_start` | create `currentAssistant` owned by the internal turn |
| `message_update(text_delta)` | append by validated offsets/idempotent event identity |
| `message_end(commentary)` | checkpoint text, append Assistant activity if non-empty, clear current slot |
| `message_end(final)` | checkpoint current slot as provisional Final Answer |
| `message_end(error)` | checkpoint failed Assistant activity, clear candidate, await retry or failure settlement |
| `message_end(aborted)` | terminalize/clear candidate and await recovery or cancellation settlement |
| `tool_execution_start` | append running Tool activity owned by the internal turn |
| `tool_execution_update(running/suspended)` | replace accumulated progress/state only when `update_index` advances |
| `tool_execution_end(completed/failed/canceled)` | terminalize Tool activity in place; rejected/expired have already normalized to failed |
| `turn_end` | close the matching internal turn; enforce message presence iff an Assistant started and publicly verify that `tool_call_ids` exactly equal started Tool rows with durable public ends; private ToolResult closure is backend-only |
| `retry_scheduled` | append Retry activity and await next internal turn |
| `hitl_request` | append/update one ordered question row for the matching Turn/interaction; do not infer by recency |
| `run_waiting_input` | require the complete referenced request set, set `activeInteractionId` and `awaiting_input`, preserve the same Turn |
| `hitl_response` | require exact Turn-root correlation; update the matching request row and optional opaque answer reference; when all required rows are terminal with any non-responded status, derive terminal interaction state and clear `activeInteractionId` |
| `run_resumed` | require all required rows responded and application committed, mark the interaction resumed, clear `activeInteractionId`, return the same Turn to `active` |
| matching final `agent_response` | commit Final Answer only when client request, related User Message, and provisional final message ID all match |
| `run_settled(completed)` | require final commit; set terminal state/time/duration |
| `run_settled(failed/canceled)` | require no open Assistant/Tool, active HITL interaction, or active internal turn; allow zero turns only when no `turn_start` folded, otherwise require the last turn error/aborted; set typed terminal code/summary without a final requirement |
| `task_submitted/update` | update Agent Card projection only |
| Snapshot | replace server Turn data at watermark while preserving local UI state |

No fold rule examines English substrings such as `synthesizing`, `planning`, or
`completed`. No fold rule correlates by nearest timestamp, Agent name, content
equality, or “latest active turn”.

Server projection state and local presentation state are separate. Expansion,
manual-collapse ownership, focus, pinned-bottom status, and scroll offset live in
a `TurnPresentationState` keyed by `hybro_turn_id`; snapshot replacement and
replayed terminal events cannot overwrite it. Buffered deltas above a snapshot
watermark are replayed through the same existing RoomReducer rule after the
server projection replacement.

Renderer selection is mutually exclusive per Turn:

```text
canonical `run_started`/snapshot turn present → canonical renderer only
legacy room with no canonical root binding   → legacy renderer only
```

A Turn never merges legacy `processing_status`/trace nodes with canonical
activity. A `run_settled` frame that contradicts open Assistant/Tool state, an
active HITL interaction, or an active internal turn is a protocol violation: the
live reducer requests one fresh snapshot and does not fabricate terminal child
states. Backend contract tests prevent production of that ordering.

### 9.3 Streaming candidate behavior

Before `message_end`, the frontend cannot know whether Assistant text will be
commentary or final. To preserve final-answer streaming:

- render `currentAssistant` in the Final Answer slot while it streams;
- keep Trace open above it;
- if `message_end(commentary)` arrives, atomically move the checkpointed text
  into Trace and clear the Final Answer slot;
- if `message_end(final)` arrives, keep it in the Final Answer slot;
- if the common tool-call case contains no Assistant text, no temporary final
  surface appears.

This mirrors Pi's “current Assistant message first, classification at message
end” semantics while preserving Hybro's required final layout.

### 9.4 Store cleanup

After cutover, remove or reduce these competing paths:

- `trace-store` as an independent lifecycle authority;
- `processingStatusLogs` and `ProcessingStatusLogEntry`;
- content/name-based summary inference in `derive-final-answer`;
- terminal inference from Agent Card combinations;
- partial/final matching by content equality;
- DB hydration logic that can overwrite newer Turn projection state;
- component-level assembly of trace order;
- live-only state that cannot be reconstructed from a snapshot.

`message-store` may remain the normalized durable message/card entity store, and
`streaming-store` may remain a low-level buffer implementation, but the Turn
projection owns which message is commentary, final, or an Agent Card.

## 10. Rendering implementation

### 10.1 Component order

`TurnRenderer` becomes structurally explicit:

```tsx
<UserMessageBlock />
<TurnTrace />
<FinalAnswer />
<AgentIndex />
```

`TurnBody` must not hide the order by nesting Final Answer and Agent Cards ahead
of Trace.

### 10.2 shadcn Marker

Add the shadcn Aria Marker component to:

```text
frontend/src/components/ui/marker.tsx
```

Use its owned source primitives:

```text
Marker
MarkerIcon
MarkerContent
```

Normal activity:

```tsx
<Marker>
  <MarkerIcon><Bot /></MarkerIcon>
  <MarkerContent>{assistantText}</MarkerContent>
</Marker>
```

Live activity:

```tsx
<Marker role="status">
  <MarkerIcon><LoaderCircle /></MarkerIcon>
  <MarkerContent className="shimmer">{liveText}</MarkerContent>
</Marker>
```

Tool activity uses one Marker row plus the existing shadcn `Collapsible` for
bounded Input/Output details. Do not wrap each entry in a generic Card.

Use `variant="separator"` only for meaningful internal boundaries when they are
shown in a developer view. It is not repeated between every normal activity row.

### 10.3 Accessibility

- The active Marker uses `role="status"`; completed historical rows do not.
- The Trace body uses `role="log"` and `aria-live="polite"` only while active.
- Decorative Marker icons remain `aria-hidden` through `MarkerIcon`.
- Tool details use real buttons and shadcn `Collapsible`, with accurate
  `aria-expanded`.
- Auto-collapse moves no keyboard focus. If focus is inside Trace when
  settlement arrives, defer collapse until focus leaves or move focus to the
  Trace trigger with an announcement.
- Collapsed content is unmounted or made fully inert; no hidden focus targets.
- Shimmer and collapse motion respect `prefers-reduced-motion`.

### 10.4 Auto-follow and manual control

- Active Trace initializes open.
- New items scroll to the bottom only while the viewport remains pinned near
  the bottom.
- Manual upward scrolling disables auto-follow until the user returns to the
  bottom.
- Manual collapse during execution remains respected; new events do not force
  reopening.
- Settlement performs one automatic collapse unless the user has already
  manually collapsed it.
- Manual reopening after settlement persists for that mounted Turn and is not
  undone by duplicate/replayed events.

### 10.5 Header and duration

Active:

```text
Turn Trace · Working
```

Completed:

```text
Worked for 18.6s · 1 tool call
```

Failed/canceled:

```text
Failed after 18.6s
Stopped after 18.6s
```

Duration is the server-authored `run_settled.duration_ms`, formatted with
locale-safe, tabular numerals. It is never computed from `Date.now()` on a
reconnecting client.

## 11. Implementation phases

### Phase 0 — Contract inventory and fixtures

- Pin the new subtype vocabulary and payload schemas in backend contract tests.
- Add sanitized fixture streams for:
  - commentary → tool → result → final;
  - no-commentary tool call;
  - repeated calls to the same Agent;
  - retry;
  - failure/cancel, including rejected and expired calls;
  - multi-question and multi-round HITL, including responded, expired, canceled,
    error, and resumed interactions with reused request message IDs;
  - final streaming plus unrelated and identity-matching direct-Agent
    `agent_response` cases;
  - reconnect during commentary, tool output, and final text;
  - deployment while legacy runs are streaming, executing tools, canceled, or
    waiting on HITL;
  - stale-browser compatibility with nonempty IDs, allowlisted status, and
    exactly `details: null`.
- Add DTO fixtures for every allowed union member and conditional terminal
  shape, plus negative fixtures for unknown/forbidden fields.
- Inventory every top-level room-event producer/field, including HITL,
  artifacts, errors, cancellation, final-message parts, and compatibility DTOs;
  assign an explicit canonical policy or prohibit it.
- Record the current dependencies on `processing_status` before removing it.

Acceptance:

- schemas reject reasoning fields, raw call IDs, private arguments, missing
  correlation, invalid terminal fields, unknown nested variants, and every
  unclassified canonical room-event producer/field;
- fixture room sequences are contiguous and replayable;
- a checked run-state migration/producer matrix selects exactly one lifecycle
  family per run;
- no production behavior changes yet.

### Phase 1 — Backend Pi-aligned message stream

Primary files:

```text
backend/execution/orchestrator/kernel.py
backend/execution/orchestrator/streaming.py
backend/execution/orchestrator/lifecycle.py
backend/execution/orchestrator/models.py
backend/execution/orchestrator/projection.py
backend/execution/orchestrator/public_projection.py
backend/execution/terminal_projection.py
backend/dal/orchestrator/projection.py
backend/common/dto/delivery.py
backend/delivery/event_publisher.py
backend/delivery/translator.py
backend/delivery/snapshot.py
backend/container.py
```

Work:

- bump and dual-read/migrate the Run-state schema, then persist the active
  internal-turn/message/attempt/public-offset checkpoint for canonical runs;
- preallocate Assistant IDs;
- add discriminated validated public payload DTOs, stateful Assistant secret
  sanitization, and tool/Agent-card public summary builders with deny-by-default
  fallback;
- emit/coalesce `message_*` events;
- classify commentary/final at `message_end`;
- emit Pi-aligned tool lifecycle;
- emit authoritative `run_settled`;
- keep final outbox idempotency;
- extend snapshot fold.

Acceptance:

- replay shows real Assistant text deltas and tool lifecycle in order;
- final text is visible before the final DB hydration path;
- crash tests at message start, immediately before/after chunk room-event
  append, immediately before/after Run-state offset CAS, final DB write,
  terminal room-event append, terminal Run-state CAS, and fanout recover without
  lost, duplicated, truncated, or contradictory committed text; existing
  `message_end` events are adopted rather than re-terminalized;
- one durable final `agent_response` and one `run_settled` exist;
- unrelated specialist responses cannot commit the final, while an
  identity-matching direct-Agent final does;
- rejected/expired calls terminalize as failed with safe reasons;
- canonical `room_events` contain no raw reasoning, private IDs, model-echoed
  configured credentials, secret-bearing tool/card/HITL/artifact/error content,
  or unaudited compatibility payloads.

### Phase 2 — Canonical frontend Turn fold

Primary files:

```text
frontend/src/lib/types/sse.ts
frontend/src/lib/room-sync/room-reducer.ts
frontend/src/lib/room-sync/hydrate-room.ts
frontend/src/lib/room-timeline/*
frontend/src/stores/message-store/*
frontend/src/stores/streaming-store/*
frontend/src/stores/trace-store/*
frontend/src/hooks/room/sse-handlers/*
```

Work:

- add typed Pi-aligned events;
- build one Turn projection;
- fold live/replay/snapshot through the same reducer;
- implement current-Assistant classification and final promotion;
- fold durable HITL message references and waiting/resume boundaries into the
  same Turn projection;
- commit finals only by exact root/message identity;
- use `room_seq` as activity order;
- move terminal/send-guard authority to `run_settled`.

Acceptance:

- no English-substring lifecycle inference;
- no local-time ordering;
- refresh at every fixture boundary produces identical Turn state;
- duplicate/reordered events do not duplicate text or calls.

### Phase 3 — Codex-like rendering

Primary files:

```text
frontend/src/components/ui/marker.tsx
frontend/src/components/conversation/TurnRenderer.tsx
frontend/src/components/conversation/TurnBody.tsx
frontend/src/components/conversation/TurnTracePanel.tsx
frontend/src/components/conversation/FinalAnswerSurface.tsx
frontend/src/components/conversation/AgentIndex.tsx
frontend/src/components/conversation/conversation-tokens.css
```

Work:

- install the shadcn Marker primitive;
- render continuous Assistant/tool activity;
- stream the current final candidate;
- enforce User → Trace → Final → Agent Cards;
- auto-collapse on final commit + settlement;
- display duration;
- implement pinned-bottom behavior and accessibility.

Acceptance:

- deterministic Playwright fixtures assert the §4 DOM order, visible Marker
  copy, expansion state, duration, and final/card placement;
- no generic activity Cards or persisted canned logs remain;
- fixed desktop/mobile screenshot baselines pass one bounded visual review;
- keyboard tests verify trigger/focus behavior and axe checks the rendered
  active and settled states; manual screen-reader smoke results are recorded.

### Phase 4 — Remove `processing_status` and legacy inference

Work:

- migrate Fast/Queue/HITL/cancel/sidebar dependencies;
- stop orchestrator lifecycle `ProcessingStatusEvent` emission;
- delete processing log stores, handlers, props, and tests;
- remove old public trace kinds from normal UI consumption;
- remove superseded final-answer inference and duplicate stores;
- update architecture docs and protocol inventories.

Acceptance:

```text
zero user-visible processing_status logs
zero production Turn terminal dependence on processing_status
zero content-equality final matching
zero Agent-name/call-name dedup
zero fixed-delay reconciliation or safety polling
```

### Phase 5 — Dark compatibility removal and final hardening

- Remove temporary adapters only after every production execution mode emits
  the new lifecycle.
- Keep unknown-event tolerance for rolling deploys, but remove feature flags and
  dual writes.
- Run full backend/frontend suites, Docker rebuild, and real live/reconnect E2E.
- Update exact public method inventories and route fixtures if interfaces move.

## 12. Required tests

### Backend

- Public redaction for all message/tool event payloads.
- Stable published-delta IDs across delivery retry.
- Process restart fresh-folds the durable chunk prefix, repairs stale lower
  Run-state offsets, ignores unacknowledged higher offsets, terminalizes the
  interrupted message once without truncation, and starts a new attempt/message
  identity. Tests crash on both sides of room-event append and offset CAS.
- Delta coalescing and stateful secret sanitization flush on time, size, tool
  start, message end, error, and
  cancellation.
- Persist-before-broadcast for every new public subtype.
- Pi `agent_end` alone never settles a run; retry/compaction/queued continuation
  may follow, and only the projected `agent_settled` boundary settles.
- Snapshot reconstruction during partial Assistant text.
- Snapshot reconstruction during tool update.
- Final message and `run_settled` idempotency.
- Failure/cancellation before `turn_start` settles with zero internal turns;
  after `turn_start` but before `message_start`, it emits
  `turn_end(error|aborted)` without `message_id`; failures after Assistant start
  require the matching ID. All paths emit typed sanitized settlement
  code/summary and reject raw terminal reasons/exceptions.
- `turn_end.tool_call_ids` exactly matches source-ordered durable accepted
  public-ID bindings, not the larger private declared batch. Invalid/
  authorization-rejected pre-acceptance entries create no Tool row and close the
  turn error; each such entry gets a private error ToolResult, all later
  declarations are durably skipped with private error ToolResults, and
  accepted-then-rejected/expired entries remain in the inventory and receive a
  failed Tool end. No retry/settlement occurs until every declared call has one
  source-ordered private result and every accepted call has a public end.
- Retry-to-success and crash-recovery-to-success allow earlier causally retried
  error/aborted turns while requiring the final turn completed.
- Recovery uses deterministic terminal-event-key readback plus a healed
  contiguous fold, adopts each already-persisted commentary/final/error/aborted
  `message_end` after a terminal-append/gap crash, and resumes only missing later
  boundaries; final always closes `turn_end(completed)` before projection.
- Commentary `message_end` cannot publish before the private Assistant and
  complete source-ordered declared Tool batch are durable. Recovery idempotently
  resumes per-entry acceptance, reuses accepted ledger bindings, never repeats
  side effects, and refuses an uncheckpointed batch. A crash after the declared
  batch checkpoint but before `message_end` writes one deterministic private
  restart-abort result per declaration before `turn_end`; no public Tool row is
  created. Mixed-batch crash tests cover that boundary, failure before the first
  acceptance, and failure between accepted calls, including accepted siblings
  suspended on HITL.
- Restart-aborted attempts emit deterministic
  `retry_scheduled(error_class="process_restart")` before a successor turn.
- Multiple calls to the same Agent retain separate opaque call identities.
- Rejected and expired durable calls each emit one failed Tool end with a safe
  `failure_reason` and do not block settlement.
- Multi-question and multiple-round HITL live/snapshot folds require exact
  run/client/User-Message root correlation and preserve sanitized
  prompts/types/choices/source labels, ordered request IDs, reused message IDs,
  per-request response status, active interaction, and optional opaque answer
  refs without REST-overlay inference; mismatched roots are rejected; expiry,
  cancellation, application failure, and resume are covered. The last terminal
  response for a non-resumable required set derives interaction state and clears
  the active interaction before settlement.
- Failure/cancellation at Assistant streaming, Tool execution, and HITL emits
  child terminal events plus `turn_end` before `run_settled`; HITL expiry,
  cancellation, and error terminalize every unresolved request and clear
  interaction ownership before Tool end. The fold rejects settlement with any
  active internal turn or interaction.
- An unrelated specialist `agent_response` cannot commit a Turn final, while an
  identity-matching direct-Agent response can.
- Canonical-only tests prove unconditional schema-v6 admission, mandatory
  projection/recovery workers, fail-fast startup, and canonical restarts at
  each stream/tool/HITL boundary.
- Unknown tools, secret-bearing scalar/nested ToolResults and Agent Card data,
  credentials echoed by Assistant text, secrets split across stream chunks,
  malicious HITL prompts/choices, artifacts, cancellation/error reasons,
  compatibility events, and nested final-message parts produce only sanitized
  canonical projections or are prohibited.
- Terminal outbox replay never duplicates Final Answer or Agent Cards.
- Full `pytest`, Ruff check, and Ruff format check.

### Frontend unit/integration

- Active Trace initializes expanded.
- Ephemeral Thinking disappears after first real event and is never hydrated.
- Assistant deltas assemble by validated offsets exactly once.
- Commentary moves into Trace at `message_end(commentary)`.
- Final candidate remains in Final Answer at `message_end(final)`.
- Trace collapses only after final commit plus `run_settled`.
- HITL does not collapse as completed.
- Failed/canceled headers show correct duration and typed terminal summary/code,
  including zero-turn failure and failure after `turn_start` but before
  `message_start`.
- User manual collapse/reopen is respected.
- Tool start/update/end updates one row per call, including normalized
  rejected/expired failures.
- Same Agent called twice produces two Tool rows and two Agent Cards.
- Multi-question, multi-round HITL request/wait/response/resume requires exact
  Turn-root correlation and reconstructs prompt/type/choices/source and status
  identically in live and snapshot folds; expired/canceled/error required sets
  clear the active interaction and do not resume or collapse as completed.
- An unrelated specialist response before the coordinator final does not commit
  or replace it; an identity-matching direct-Agent response does commit.
- Snapshot-before-DB and DB-before-snapshot converge identically.
- Gap recovery mid-final preserves text and scroll state.
- Final Answer renders streaming Markdown safely across partial syntax.
- TypeScript, focused Vitest, lint, and production build.

### Deterministic Playwright/Docker

A mock provider plus mock A2A weather fixture is the CI acceptance flow and must
prove without refresh:

1. User Message appears immediately.
2. Trace opens automatically.
3. Real Assistant commentary appears when the model produced it.
4. Tool Marker starts and completes.
5. Final Answer visibly streams.
6. Trace collapses after final commit/settlement and shows duration.
7. Agent Cards render after Final Answer.
8. Refresh restores the same content, duration, call count, and collapsed state.
9. Replay contains contiguous events and exactly one durable final message.

A real external Weather Agent run is a manual smoke test and does not replace
the deterministic gate.

Additional deterministic flows:

- disconnect/reconnect during final text;
- repeated same-Agent calls;
- Agent failure followed by successful alternative;
- cancellation/failure during Assistant, Tool, and HITL boundaries;
- multi-question and multi-round HITL pause/resume/expiry/cancel/error;
- Fast/direct mode with positive final commitment.

## 13. Performance and retention

- Coalesce text deltas before persistence as described in §5.6.
- Bound public commentary, tool input, partial progress, and result checkpoint
  sizes at the backend.
- Snapshot materialization remains incremental.
- Frontend subscribes per Turn and per current message; historical Turns do not
  rerender for active deltas.
- Collapsed Trace content is not mounted until opened.
- Long activity histories use the conversation as the single scroll owner;
  nested fixed-height Trace/Card scrollers and `content-visibility` painting
  gaps are prohibited.
- No room-event retention or compaction is implemented in this cutover. A
  separate future design must satisfy the continuity constraints in §5.1 before
  deleting any delta.

## 14. Security and privacy

Public projection tests must prove that room events never contain:

- system prompts or full user prompt copies;
- model thinking/reasoning deltas;
- provider API request/response bodies;
- credentials, auth references, relay tokens, or webhook secrets;
- private A2A/provider call IDs in newly generated canonical events;
- unbounded tool arguments or Agent results;
- internal exception tracebacks.

Every canonical room-event content surface uses a public projection boundary.
Tool input/progress/output and Agent Card content use registered per-tool or
per-Agent allowlisted summary builders with a deny-by-default empty/generic
fallback. Assistant deltas/checkpoints and final messages use the same stateful
secret sanitizer over sanitized public text before offsets are assigned. Tests
include secret-bearing scalar keys and values, credentials embedded in URLs,
secrets split across streaming chunks, model-echoed credentials, card results,
nested objects, and unknown tools; length bounds or a provider
“user-visible-text” label alone are never accepted as evidence of redaction.
Historical immutable events are handled by the rollout limitation in §15.

Assistant commentary is displayed only when it is explicitly user-visible text
from the validated Assistant message. Empty commentary stays empty.

## 15. Canonical-only operation

The lifecycle is a single mandatory contract, not a rollout option:

1. Every accepted orchestration request requires a nonempty
   `client_request_id` and creates a schema-v6 `canonical` Run.
2. Canonical admission, projection, and recovery have no runtime feature
   switches. Missing runtime composition, router binding, room-event
   persistence, or worker dependencies fails startup/readiness.
3. Every snapshot, including an empty room, contains
   `turn_lifecycle_schema: 1` and `turns: []` (or the folded Turns).
4. `tool_execution_start/update/end` is the sole Agent-call lifecycle source.
   Trace and Agent Cards are separate views of the same folded activity row and
   expose the same call identity and normalized status.
5. `task_*`, compatibility work logs, partial Agent frames, and legacy Trace
   snapshots cannot create or update Agent Cards, Trace, final content, or
   composer lifecycle authority.
6. A message-derived optimistic shell may render only the User bubble before
   `run_started`; it owns no execution state.
7. Privacy acceptance applies to every newly generated event. Historical data
   migration and old-room presentation are outside this contract.

## 16. Risks and mitigations

1. **Final/commentary classification occurs after streaming.** Render the current
   Assistant in the Final Answer slot; atomically move it only when
   `message_end(commentary)` proves a tool-using turn.
2. **Tiny provider deltas create event volume.** Coalesce with bounded time/size
   thresholds and flush at semantic boundaries.
3. **Mixed text and tool calls.** Treat the text as commentary; never duplicate
   it in Final Answer.
4. **Final stream completes before durable projection.** Keep visible text, but
   wait for final `agent_response` plus `run_settled` before auto-collapse and
   send-guard release.
5. **Out-of-order cross-instance fanout.** Existing `room_seq` reorder/gap
   recovery remains authoritative.
6. **Snapshot during an unclassified Assistant message.** Persist the start and
   coalesced deltas; snapshot restores `current_assistant` without guessing.
7. **Duplicate terminal signals.** One fold transition is absorbing by durable
   event identity; duplicates do not alter user-controlled expansion state.
8. **Agent Card/Tool row divergence.** Both surfaces must select the same
   `TurnProjection.activity` row by `(run_id, tool_call_id)`; neither maintains
   an independent status transition.
9. **Raw Assistant text may contain private model reasoning.** Only provider
    text content blocks designated as user-visible are projected; reasoning
    block kinds are rejected at the public translator boundary.

## 17. Definition of done

The plan is complete only when all statements are true:

- The room stream remains durable, ordered, snapshot-driven, and gap-healing.
- One Hybro User Message maps to one authoritative Turn projection.
- Multiple Pi-style internal turns are preserved under that projection.
- Default Trace content consists only of real Assistant commentary, Tool/Agent
  execution, real tool progress, results, retries, errors, and HITL facts.
- No prewritten processing log is visible or required for correctness.
- The Final Answer streams before DB refresh and survives reconnect.
- The visual order is User → Trace → Final → Agent Cards.
- Trace is active/open during work and auto-collapses exactly once on durable
  completion.
- Collapsed Trace shows server-authored total duration.
- Agent Cards remain one per call and appear after Final Answer.
- Snapshot and live streams produce byte-equivalent visible Assistant text and
  semantically equivalent Turn state.
- Raw reasoning and private execution data never enter public room events.
- Full backend, frontend, Docker, and real-browser acceptance gates pass.
