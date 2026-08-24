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
   orchestration call and retains identity `run_id + call_id`. Cards do not
   become Trace nodes and repeated calls to one Agent are not merged.
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
| `agent_start/end/settled` | `run_started` / `run_settled` | Renamed because “Agent” already means an A2A specialist in Hybro. |
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
- `awaiting_input` is not settled. The Trace remains available and the final
  slot is replaced by the HITL surface.
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
contract. Validators enforce bounded text, required correlation, opaque public
call IDs, valid indices, and the exclusion of private/reasoning fields.

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

Public `assistant_message_event.type` initially permits:

```text
text_start
text_delta
text_end
```

Thinking/reasoning events are explicitly excluded. Tool-call argument deltas are
also excluded because arguments are not public until they have been completely
assembled, validated, authorized, and redacted.

Each published chunk has a deterministic event identity derived from:

```text
run_id + internal_turn_id + message_id + content_index
+ start_offset + end_offset + content_digest
```

`start_offset` and `end_offset` are Unicode code-point offsets in the assembled
public text and are included in the event. `delta_index` remains a monotonic
convenience cursor, not the sole identity. The event publisher persists the
logical event before fanout, so delivery retries reuse the same
`room_seq`/`room_event_id` and do not duplicate text.

To avoid one Mongo document per provider token, the public projection coalesces
provider text deltas. It flushes when any of these conditions occurs:

- 50–80 ms have elapsed since the previous public chunk;
- the accumulated chunk reaches 256–512 UTF-8 characters;
- a tool call begins;
- the message, attempt, run, or connection terminates.

The preallocated message identity and active attempt record are checkpointed in
`OrchestratorRunState` before the provider request. Published chunks are already
durable in `room_events`; the run state stores the greatest published end
offset. A crashed provider stream is **not resumed** with reconstructed timing
boundaries. Recovery emits one `message_end(aborted)` for the interrupted
message using its durable checkpoint, then starts a new attempt with a new
internal turn/message identity. This makes restart behavior explicit and avoids
pretending an unresumable provider stream can reproduce identical chunks.

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
    "stop_reason": "toolUse",
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

`text` is a public, user-visible Assistant text checkpoint. It lets a snapshot
or replay reconstruct the message without depending on every partial chunk.

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
- `input` contains only validated, bounded public summaries.
- Nested private objects, auth references, routing metadata, and secrets are
  omitted.

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

Allowed outcomes are `completed`, `failed`, and `canceled`. The public result is
bounded and redacted. Full Agent output remains available through the
corresponding Agent Card/detail surface. Suspension is non-terminal and is
represented by `tool_execution_update(status="suspended")`, never by a fake end.

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

Allowed statuses are `completed`, `error`, and `aborted`. This is a
fold/checkpoint boundary and is hidden in the normal UI.

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

HITL uses the existing durable `hitl_request`/`hitl_response` content events and
adds control boundaries:

```text
run_waiting_input { interaction_id, requested_at }
run_resumed       { interaction_id, resumed_at }
```

Before `run_waiting_input`, each affected running Tool receives an indexed
`tool_execution_update(status="suspended")`. `run_waiting_input` then sets the
Hybro Turn to `awaiting_input` and keeps Trace available. On resume,
`run_resumed` restores `active` on the same `hybro_turn_id`; each continuing call
receives a higher-index `tool_execution_update(status="running")`. It never
creates a new User Turn.

Before failed/canceled `run_settled`, every open Tool receives an authoritative
`tool_execution_end(outcome="failed"|"canceled")`. Settlement does not invent
Tool terminal state on the frontend.

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

The final answer is considered committed when this durable checkpoint is folded.
The causal terminal order is:

```text
message_end(final)
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
final-commit requirement and collapse on their authoritative `run_settled` after
all open Assistant/Tool states have been terminalized.

### 5.15 Agent Card events

Existing `task_submitted` and `task_update` events remain the Agent Card
projection contract. They are not Turn Trace entries.

One call remains one card, but its public/durable message ID uses the same
opaque derived invocation identity as Trace:

```text
orchestrator:{run_id}:{opaque_public_call_id}
```

The UI card key is this durable public message ID. The private provider/A2A
`call_id` is stored only in backend-owned call-ledger/binding fields and never
enters `room_events`, snapshots, DOM attributes, or frontend state. Public Trace
and Agent Card correlation use `opaque_public_call_id`; the backend maintains
the private mapping.

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
failed:    runSettled(failed) && no open Assistant/Tool item
canceled:  runSettled(canceled) && no open Assistant/Tool item
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
6. build and validate the final `AssistantMessage` with the preallocated ID;
7. emit `message_end` with the authoritative public text and disposition;
8. execute accepted tools with `tool_execution_*` projection;
9. emit `turn_end` after all results for that internal turn are recorded.

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
- `agent_response` commits the final room message;
- `run_settled` sets terminal status and duration;
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
  internalTurns: InternalTurnProjection[]
  activity: TurnActivityItem[]
  currentAssistant?: AssistantProjection
  finalAnswer?: AssistantProjection
  finalCommitted: boolean
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
| `tool_execution_end(completed/failed/canceled)` | terminalize Tool activity in place |
| `turn_end` | close the matching internal turn |
| `retry_scheduled` | append Retry activity and await next internal turn |
| `run_waiting_input` | set `awaiting_input`; preserve same Hybro Turn |
| `run_resumed` | return the same Hybro Turn to `active` |
| `agent_response` | commit Final Answer entity/content |
| `run_settled(completed)` | require final commit; set terminal state/time/duration |
| `run_settled(failed/canceled)` | require no open Assistant/Tool activity, then set terminal state/time/duration without final requirement |
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
activity. A `run_settled` frame that contradicts open Assistant/Tool state is a
protocol violation: the live reducer requests one fresh snapshot and does not
fabricate terminal child states. Backend contract tests prevent production of
that ordering.

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
  - failure/cancel;
  - HITL suspend/resume;
  - final streaming;
  - reconnect during commentary, tool output, and final text.
- Record the current dependencies on `processing_status` before removing it.

Acceptance:

- schemas reject reasoning fields, raw call IDs, private arguments, and missing
  correlation;
- fixture room sequences are contiguous and replayable;
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

- persist the active internal-turn/message/attempt/public-offset checkpoint;
- preallocate Assistant IDs;
- add discriminated validated public payload DTOs;
- emit/coalesce `message_*` events;
- classify commentary/final at `message_end`;
- emit Pi-aligned tool lifecycle;
- emit authoritative `run_settled`;
- keep final outbox idempotency;
- extend snapshot fold.

Acceptance:

- replay shows real Assistant text deltas and tool lifecycle in order;
- final text is visible before the final DB hydration path;
- crash tests at message start, published chunk, final DB write, room-event
  append, and fanout recover without lost/duplicated committed text;
- one durable final `agent_response` and one `run_settled` exist;
- raw reasoning/private IDs never appear in `room_events`.

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
- Process restart terminalizes the interrupted persisted message once and starts
  a new attempt/message identity without duplicating committed text.
- Delta coalescing flush on time, size, tool start, message end, error, and
  cancellation.
- Persist-before-broadcast for every new public subtype.
- Snapshot reconstruction during partial Assistant text.
- Snapshot reconstruction during tool update.
- Final message and `run_settled` idempotency.
- Multiple calls to the same Agent retain separate opaque call identities.
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
- Failed/canceled headers show correct duration.
- User manual collapse/reopen is respected.
- Tool start/update/end updates one row per call.
- Same Agent called twice produces two Tool rows and two Agent Cards.
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
- cancellation;
- HITL pause/resume;
- Fast/direct mode.

## 13. Performance and retention

- Coalesce text deltas before persistence as described in §5.6.
- Bound public commentary, tool input, partial progress, and result checkpoint
  sizes at the backend.
- Snapshot materialization remains incremental.
- Frontend subscribes per Turn and per current message; historical Turns do not
  rerender for active deltas.
- Collapsed Trace content is not mounted until opened.
- Long activity histories use CSS `content-visibility` first; virtualization is
  added only if measured traces justify it.
- No room-event retention or compaction is implemented in this cutover. A
  separate future design must satisfy the continuity constraints in §5.1 before
  deleting any delta.

## 14. Security and privacy

Public projection tests must prove that room events never contain:

- system prompts or full user prompt copies;
- model thinking/reasoning deltas;
- provider API request/response bodies;
- credentials, auth references, relay tokens, or webhook secrets;
- private A2A/provider call IDs;
- unbounded tool arguments or Agent results;
- internal exception tracebacks.

Assistant commentary is displayed only when it is explicitly user-visible text
from the validated Assistant message. Empty commentary stays empty.

## 15. Rollout and compatibility

This is an in-place contract evolution:

1. Backend may dual-emit old and new public lifecycle facts briefly, but the new
   frontend consumes only one family.
2. Deploy backend support before switching the frontend fold.
3. Snapshot advertises support through presence of canonical `turns`; no V2
   naming or new endpoint is introduced.
4. The frontend may fall back to the existing renderer only for rooms generated
   before canonical Turn events existed. It must not merge old and new activity
   for the same Turn.
5. Once all active rooms and execution modes emit canonical events, remove dual
   emission and the fallback.

Historical limitations:

- Old rooms without durable activity events cannot recover Pi-like commentary.
- Existing final messages and Agent Cards still hydrate from Mongo.
- The UI must not fabricate missing historical Trace lines to make old rooms
  look complete.

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
8. **Agent Card/Tool row duplication.** They intentionally represent different
   surfaces and correlate through backend mapping, but neither is derived from
   the other on the frontend.
9. **Removing `processing_status` too early.** Phase 4 cannot begin until the
   responsibility table in §6.2 has passing replacement tests for every mode.
10. **Raw Assistant text may contain private model reasoning.** Only provider
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
