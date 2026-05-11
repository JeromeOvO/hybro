# DELTA_CHECKPOINTS_STREAMING

**Status:** Implemented  
**Author:** Engineering  
**Last updated:** 2026-05-10

---

## 1. Overview

The Hybro chat UI renders agent responses that arrive via Server-Sent Events
(SSE). Two event types carry agent content to the frontend:

| Event              | When emitted                                          | Payload highlights                                                                    |
|--------------------|-------------------------------------------------------|---------------------------------------------------------------------------------------|
| `artifact_update`  | Incrementally during streaming, once at stream end    | `message_id`, `artifact` (with parts), `append`, `last_chunk`                         |
| `task_update`      | Once when the agent's task reaches a terminal state   | `message_id`, `status`, `content`, `parts`, `error`                                   |

This document describes the **implemented architecture**: DB as single source of
truth with streaming as a pure ephemeral display buffer. It also records the
historical root causes of previous content-duplication bugs and the prior fix
series, for context.

---

## 2. Architecture: DB as Single Source of Truth

### 2.1 Core Principle

Every content-level bug in the previous implementation traced to a single
structural problem: one field (`entity.content`) served two incompatible roles:

1. **Live display** — updated on every streaming chunk so the user sees text
   appearing as the agent types.
2. **Permanent state** — persisted, compared by `isNoOpUpdate`, read by the
   render layer after streaming ends.

Because one field had to do both jobs, the content written during streaming
(lossy, filtered, heuristic-driven) inevitably diverged from the DB checkpoint
(authoritative). Every guard, equality check, length heuristic, and token filter
that existed before was patching that divergence.

The fix is **complete separation by store**:

- **`streamingStore`** — a separate Zustand slice holding live display buffers
  indexed by `message_id`. Written exclusively by `artifact_update` chunks.
  Never persisted. Never touched by `messageStore`.
- **`messageStore`** — holds entities indexed by `message_id`. Written
  exclusively by `task_update` (SSE) and DB reconcile. **Never written by
  `artifact_update` chunks.**

```
┌─────────────────────────────┐    ┌───────────────────────────────────┐
│  messageStore               │    │  streamingStore                    │
│  (persistent entity state)  │    │  (ephemeral display state)         │
│                             │    │                                    │
│  entities[id].content  ──── │    │  buffers[id].text   ← SSE chunks  │
│  entities[id].artifacts      │    │  buffers[id].artifacts              │
│  entities[id].taskStatus     │    │  buffers[id].isComplete             │
│                             │    │                                    │
│  Written ONLY by:           │    │  Written ONLY by:                  │
│  • task_update (SSE)        │    │  • artifact_update chunks           │
│  • DB reconcile             │    │  Cleared by:                       │
│                             │    │  • task_update (after entity write)│
│  isNoOpUpdate compares      │    │  • agent_response                  │
│  these fields               │    │  • DB reconcile (on reconnect)     │
└─────────────────────────────┘    └───────────────────────────────────┘
```

Source of truth hierarchy:

```
┌──────────────────────────────────────────────────────────────────┐
│  1. MongoDB artifacts[].parts  ← atomic $push per chunk (backend)│
│  2. task_update SSE payload    ← read from MongoDB at completion  │
│  3. DB reconcile response      ← read from MongoDB on reconnect  │
│                                                                   │
│  NOT a source of truth:                                           │
│  ✗  streamingStore buffers     ← ephemeral display only           │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 Data Flow

```
artifact_update(chunk) ──► streamingStore.append(id, chunk)
                                └─► render reads buffers[id].text
                                └─► messageStore is NOT touched

artifact_update(last)  ──► streamingStore.markComplete(id)
                                └─► render still reads buffers[id].text

task_update(completed) ──► messageStore.upsertMessage(id, DB content)
                       ──► streamingStore.clear(id)
                                └─► render: buffers[id] gone →
                                    reads messageStore.entities[id].content
```

During streaming the render reads `streamingStore.buffers[id]?.text`. Once
`task_update` fires and the buffer is cleared, the fallback
`messageStore.entities[id].content` takes over — already holding the
DB-canonical value.

### 2.3 Store Shapes

```typescript
// ── streamingStore (src/stores/streaming-store/index.ts) ──────────
interface StreamBuffer {
  text: string              // accumulated SSE text for live render
  artifacts: ArtifactData[] // accumulated SSE artifacts (display only)
  isComplete: boolean       // true after last_chunk=true received
}

interface StreamingState {
  buffers: Record<string, StreamBuffer>  // keyed by message_id

  append: (id: string, chunk: ArtifactData, isAppend: boolean) => void
  markComplete: (id: string) => void
  clear: (id: string) => void
  clearRoom: (messageIds: ReadonlySet<string>) => void
}

// ── messageStore.MessageEntity ────────────────────────────────────
// No new fields. entity.content and entity.artifacts are NEVER written
// by artifact_update chunks — only by task_update or DB reconcile.
```

### 2.4 Render Layer — Dual-Subscription Pattern

The merge of streaming and entity content happens at the selector level
(`selectConversationTurns`, `selectAgentResponseDetail`), not inside individual
components. `AgentContentBlock` and `AgentResponseDetailPane` remain pure
components receiving `content: string` and `isStreaming: boolean` as props.

`useConversationTurnViews` uses separate subscriptions + `useMemo`:

```typescript
// src/hooks/useConversationTurnViews.ts
const buffers    = useStreamingStore(s => s.buffers)
const entities   = useMessageStore(s => s.entities)
const orderedIds = useMessageStore(s => s.orderedIds)

const next = React.useMemo(
  () => selectConversationTurns(roomId, entities, orderedIds, buffers),
  [roomId, entities, orderedIds, buffers],
)
```

Why separate subscriptions: composing `buffers` inside a `useMessageStore`
selector is incorrect — when `buffers` changes (new chunk), `messageStore`'s
selector never re-runs because `messageStore` state is unchanged during
streaming.

`selectConversationTurns` and `selectAgentResponseDetail` both apply the same
merge pattern:

```typescript
const buffer = buffers[agent.id]
const content = buffer ? buffer.text : (agent.content ?? '').trim()
const isStreaming = buffer
  ? !buffer.isComplete
  : (agent.taskStatus == null || agent.taskStatus === 'working' || agent.taskStatus === 'submitted')
// Suppress raw artifacts while buffer is active: buffer.text already
// represents their text — showing both would duplicate content.
const artifacts = buffer ? undefined : agent.artifacts
```

The detail pane call site (`room-page-shell.tsx`) uses the same dual-subscribe:

```typescript
const buffers = useStreamingStore(s => s.buffers)
const detail = useMemo(
  () => selectAgentResponseDetail(roomId, selectedMessageId, entities, orderedIds, buffers),
  [roomId, selectedMessageId, entities, orderedIds, buffers],
)
```

### 2.5 SSE Handler

```typescript
// artifact_update — writes ONLY to streamingStore, never messageStore
case 'artifact_update': {
  const { message_id, artifact, append, last_chunk } = sseMessage.data
  streaming.append(message_id, toArtifactData(artifact), append ?? false)
  if (last_chunk) streaming.markComplete(message_id)
  // messageStore.upsertMessage is NOT called
  break
}

// task_update — unconditional write to messageStore, then clear buffer
// ORDER IS CRITICAL: entity must be written before buffer is cleared.
// Reversing the order would produce a blank-flash frame where the render
// sees no buffer AND no entity content, showing EmptyResponse for one cycle.
// React 18 automatic batching ensures both setState calls collapse into a
// single render cycle, so no intermediate state is ever visible.
case 'task_update': {
  // resolvedContent: prefer DB content; fall back to buffer text if DB
  // content is empty (status-only update), then existing entity content.
  const bufferText = useStreamingStore.getState().buffers[messageId]?.text
  const resolvedContent =
    (content ?? '').trim().length > 0 ? content
    : bufferText?.length ? bufferText
    : (existing?.content ?? '')

  store.upsertMessage({ id: messageId, content: resolvedContent, ...taskFields }, 'sse')
  streaming.clear(messageId)
  break
}

// agent_response — also clears buffer
case 'agent_response': {
  store.upsertMessage({ ...agentResponseFields }, 'sse')
  streaming.clear(messageId)
  break
}
```

`task_update` is an unconditional `upsertMessage` call. No Fix 2 equality guard,
no `resolveSingleWriteContent`. `isNoOpUpdate` inside `applyUpsert` handles the
no-op case (identical fields → no React re-render).

### 2.6 SSE Connection Loss and DB Reconcile

```
Where streamingStore is cleared:

  task_update SSE handler        → streaming.clear(messageId)
  agent_response SSE handler     → streaming.clear(messageId)
  reconcileWithDb (room hydration) → streaming.clearRoom(writtenIds)
  useRoomReset (room switch)     → streaming.clearRoom(allIds)
```

SSE connection loss scenario:

```
SSE disconnect
  → buffers[id] has text, entity[id].content is empty (never written during stream)
  ↓
Reconnect → reconcileWithDb fires
  → messageStore.upsertMany(filtered, 'db')   ← entity gets DB canonical content
  → streaming.clearRoom(writtenIds)            ← buffer discarded
  ↓
Render: entity.content drives display (canonical, correct)
```

### 2.7 Agent Card and Detail Pane — Live Status

Both the main chat agent card and the popup detail pane show:

- **"Streaming"** label when `buffer !== undefined && !buffer.isComplete && taskStatus === 'working'`
- **Spinner** on the agent avatar when `isStreaming` is true (`conversation-avatar-working` CSS class)
- **"Working on your request…"** task description fallback when `taskContent` and `taskStatusMessage` are both empty and the agent is in an active working state

These are derived identically in `selectConversationTurns` and
`selectAgentResponseDetail` to keep both surfaces in sync.

### 2.8 Properties Eliminated by This Design

| Old complexity | Why it existed | Status |
|----------------|----------------|--------|
| Fix 2 equality guard | Prevent re-render when DB matches accumulated | **Deleted** — `isNoOpUpdate` handles no-ops |
| `resolveSingleWriteContent` / Fix 4 | Let DB content overwrite accumulated | **Deleted** — entity never holds accumulated content |
| `isNonInformativeTextChunk` token filtering | Prevent placeholder chunks from corrupting entity | **Scope reduced** — buffer corruption is display-only |
| Content selection `>=` vs `>` | Equal-length corrections must win | **Deleted** — no length comparison in entity write path |
| `resolveDbReconcileAgentContent` / Rule 2b | Protect terminal SSE entity from DB reconcile rewrite | **Deleted** — entity is always DB-canonical |
| `DB_RECONCILE_MATERIAL_LENGTH_DELTA` | Length threshold for reconcile gate | **Deleted** |
| Fix 1 `taskStatus` on `last_chunk` | Pre-align entity for `isNoOpUpdate` | **Deleted** — entity not written by `artifact_update` |

---

## 3. Backend Event Flow

```
Agent SDK callback
       │
       ▼
AgentResponseHandler
  ├─ _on_artifact(e)
  │     ├─ db.accumulate_artifact_on_message()   ← persists incremental chunks atomically
  │     └─ sse.send_artifact_update()            ← SSE: artifact_update
  │
  └─ _on_response(e)  [terminal]
        ├─ db.update_task_state_on_message()     ← persists final state
        ├─ notify_task_update()
        │     ├─ reads full message + artifacts from DB  ← canonical content
        │     └─ sse.send_task_update()          ← SSE: task_update (checkpoint)
        └─ NOTE: send_agent_response removed (commit 0846520c)
```

Key points:

- **Delta model**: `artifact_update` events stream incremental content.
- **Checkpoint model**: `task_update` is the authoritative terminal event. The
  backend reads the **fully-accumulated content from MongoDB** before sending —
  this content is canonical.
- **Non-streaming agents**: Only `task_update` fires. The frontend handles it
  as the sole content source.
- **Direct dispatch**: A `task_update` with `status="working"` is sent
  immediately after `task_submitted` to populate the card description row from
  the start.

---

## 4. Historical Root Causes (Previous Implementation)

The previous implementation accumulated streaming content directly into the
store entity and used guards and heuristics to prevent it from conflicting with
the authoritative `task_update` checkpoint. This section is kept for historical
context.

### 4.1 Duplicated Agent Responses

**Mode A — re-render duplicate**: Without a terminal state flag on
`artifact_update(last_chunk=true)`, `task_update` changed `taskStatus`
(`undefined → "completed"`) and triggered a full re-render even when visible
content was identical.

**Mode B — second entity duplicate**: `artifact_update` resolved to an
optimistic placeholder ID; `task_update` used the real DB `message_id`. Two
entities were created.

### 4.2 Content Corruption (Garbled Markdown)

Streaming accumulation is lossy: token-boundary whitespace and punctuation
could be filtered or merged. Two layers previously blocked the DB checkpoint
from correcting this: `resolveSingleWriteContent` (discarded differing DB
content) and Rule 2b (`resolveDbReconcileAgentContent`, blocked reconcile
unless DB content was ≥24 chars longer).

### 4.3 Content Flash During Streaming

A non-renderable `artifact_update` caused redundant `upsertMessage` calls
(triggering React re-renders). Separately, `<Streamdown>` was remounted when
`isStreaming` flipped (`key={isStreaming ? 'streaming' : 'static'}`), causing
raw markdown to flash for one render cycle. The `key` prop was removed in a
prior commit (Fix 5) — **do not re-add it**.

### 4.4 Wrong Artifact Selected (Multi-Artifact Agents)

`extractTextFromArtifacts()` previously selected the **longest** text-only
artifact. When a "thinking" artifact grew longer than the answer artifact, the
displayed content jumped. Fixed by preferring the **last** text-only artifact
(emission order = answer is last).

### 4.5 Streaming Accumulation Divergence

| Source | Description |
|--------|-------------|
| `isNonInformativeTextChunk` filtering | Dropped `.`, `...`, `…`, `""` mid-stream |
| `.trim()` on `joinedText` | Dropped space-only tokens |
| Content selection `>` instead of `>=` | Equal-length corrections not applied |
| `resolveSingleWriteContent` | Discarded DB content when it differed |
| `extractTextFromArtifacts` longest-pick | Multi-artifact display jump |

All five sources are now moot because `artifact_update` no longer writes to
`messageStore`. Buffer corruption is display-only and self-corrects when
`task_update` fires.

---

## 5. Files Modified (Current Implementation)

| File | Change |
|------|--------|
| `src/stores/streaming-store/index.ts` | New file — ephemeral streaming store |
| `src/hooks/room/sse-handlers/index.ts` | `artifact_update` writes only to `streamingStore`; `task_update` clears buffer; `agent_response` clears buffer |
| `src/stores/message-store/upsert.ts` | Deleted Rule 2b, `resolveDbReconcileAgentContent`, `DB_RECONCILE_MATERIAL_LENGTH_DELTA` |
| `src/lib/selectors/select-conversation-turns.ts` | Dual-subscription merge of `buffers` into agent blocks; "Streaming" label; fallback description |
| `src/lib/selectors/select-agent-response-detail.ts` | Same `buffers` merge for detail pane; "Streaming" label; fallback description; artifact suppression during streaming |
| `src/hooks/useConversationTurnViews.ts` | Separate `useStreamingStore` + `useMessageStore` subscriptions |
| `src/components/room-page-shell.tsx` | Dual-subscribe `buffers`; pass to `selectAgentResponseDetail` |
| `src/hooks/room/useRoomHydration.ts` | `clearRoom` after `upsertMany` on reconcile |
| `src/hooks/room/useRoomReset.ts` | `clearRoom` on room switch |
| `src/components/conversation/AgentResponseDetailPane.tsx` | Spinner on avatar when `isStreaming`; correct status tone colors |
| `src/components/conversation/AgentCard.tsx` | Always truncate task text (no expand-on-select) |
| `src/components/conversation/conversation-tokens.css` | Detail pane task text fully expanded (no line-clamp); avatar outer/inner wrapper split for spinner |
| `src/components/markdown-content.tsx` | Fix 5 — `key` prop removed (**already applied**, do not re-add) |
| `modules/transports/direct.py` | Send `task_update(working)` immediately after `task_submitted` to populate card description |

---

## 6. Scenario Matrix

| # | Scenario | `artifact_update` | `task_update` | Expected behavior |
|---|----------|-----------------|-------------|-------------------|
| 1 | **Normal streaming** | Streams chunks to `streamingStore` | Writes entity, clears buffer | Single render; buffer → entity transition invisible |
| 2 | **Garbled tokens in stream** | Buffer may show `###🏆Top3` | DB content `### 🏆 Top 3` written to entity; buffer cleared | Correct content after stream ends |
| 3 | **Network reorder** (`task_update` first) | Arrives after entity exists | `isNoOpUpdate` drops if identical | Single message |
| 4 | **Streaming interrupted** | Partial chunks, no `last_chunk` | Guard passes (non-terminal), writes full DB content | Single message with complete content |
| 5 | **Non-streaming agent** | Never fires | Sole content source | Single message |
| 6 | **Multi-agent room** | Each agent has unique `message_id` | Independent per-message | One message per agent |
| 7 | **Page refresh during streaming** | Lost (SSE disconnects) | Reconnect delivers `task_update`; reconcile writes entity | Single message, canonical content |
| 8 | **Failed/rejected/canceled** | May have partial streaming | `task_update` with terminal status | Single message with error state |
| 9 | **Multi-artifact agent** (thinking + answer) | Two text-only artifacts | `extractTextFromArtifacts` picks last → answer artifact | No content jump mid-stream |
| 10 | **Late failure** (stream complete, error after) | Completed normally | `task_update(failed)`: status changed → not no-op → entity transitions | Failure state correctly applied |
| 11 | **Artifact-only agent** (`content=""`) | Streams entirely via artifacts | `task_update` with empty content — `resolvedContent` falls back to `bufferText` | Single message, no blank flash |
| 12 | **SSE disconnect mid-stream** | Buffer has partial text, entity empty | Reconnect → reconcile → `clearRoom` | Entity gets canonical DB content; buffer discarded |

---

## 7. Testing Checklist

- [ ] Agent response appears only once after streaming completes (no duplicate re-render flash)
- [ ] Agent response appears as a single bubble, not two (no second-entity duplicate)
- [ ] Non-streaming agents (only `task_update`) render correctly
- [ ] Garbled markdown is corrected to canonical DB version after stream ends
- [ ] No flash/blink during streaming or at stream end
- [ ] No raw markdown text visible during streaming-to-static transition (Fix 5 regression — no `key` prop on `<Streamdown>`)
- [ ] Multi-agent rooms show one response per agent, no cross-talk
- [ ] Page refresh mid-stream recovers cleanly
- [ ] Failed/rejected/canceled terminal states render correctly
- [ ] Timeline events (`agent_completed`, `agent_failed`) appear correctly
- [ ] `isNoOpUpdate` drops redundant upserts when all fields match
- [ ] Multi-artifact agents display the answer artifact from the first token
- [ ] Late failure: `task_update(failed)` after streaming `completed` correctly transitions entity
- [ ] Artifact-only agents (`content=""`, data in `artifacts`): no blank flash on `task_update`
- [ ] SSE disconnect mid-stream: reconnect shows canonical DB content with no duplicate
- [ ] Main chat card shows "Streaming" label and spinner during streaming
- [ ] Detail pane shows "Streaming" label and spinner during streaming
- [ ] Detail pane task description shows "Working on your request…" fallback when no static description available
- [ ] Detail pane task description is fully expanded (no line-clamp)
- [ ] Main chat card task description is truncated (single line)
- [ ] Status label colors correct in both light and dark mode

---

## 8. Related Historical Fixes

| Commit | Repo | Description |
|--------|------|-------------|
| `0846520c` | multi-agents-backend | Removed redundant `send_agent_response` call that caused duplicate messages |
| `ca9ae651` | multi-agents-backend | Lowered supervisor re-delegation threshold to prevent duplicate responses on resume |
| `724787b` | hybro-frontend | Eliminated dual-path race where `useTurnHydration` and `useMessageStoreSync` both fetched messages |
| `3ea47ea` | hybro-frontend | Resolved real-time SSE duplicate dedup and stale phases |
| `bac4e6b` | hybro-frontend | Stabilized artifact rendering pipeline |

---

## 9. Future Work

### 9.1 Backend Deduplication

The backend could optionally skip `task_update` when `artifact_update(last_chunk=true)`
was already sent and accumulation was clean. With the current architecture this
is optional — the frontend applies every `task_update` unconditionally and
`isNoOpUpdate` handles the no-op case at zero extra cost.

### 9.2 Sequence Numbers

Adding monotonic sequence numbers to SSE events would allow the frontend to
definitively order events regardless of network conditions, eliminating the
need for the `pending-turn-buffer.ts` correlation heuristics. This is
complementary to the current architecture.
