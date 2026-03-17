# Remove `agent_token` — Unify Streaming on A2A Protocol

> **Status: All Phases Complete (including Phase 6 refresh fidelity fixes)** | Eliminates the custom `agent_token` SSE event and consolidates all real-time content streaming onto A2A's native streaming events (`TaskArtifactUpdateEvent` for content, `TaskStatusUpdateEvent` for status).

**Target A2A version**: v0.3 (current Hybro SDK: `@a2a-js/sdk ^0.3.10`)
**v1.0 readiness**: Design is forward-compatible with A2A v1.0; v1.0-only features are flagged as future enhancements.
**Supersedes**: `TOKEN_STREAMING_DESIGN.md` (entire document)
**Related**: `STREAMING_RENDERING_REDESIGN.md`, `A2A_UPGRADE_ROADMAP.md`
**A2A Reference**: [Streaming & Asynchronous Operations](https://a2a-protocol.org/latest/topics/streaming-and-async/)

---

## 1. Problem Statement

The codebase maintains **two parallel streaming paths** for delivering real-time agent content to the browser:

| Path | SSE Event | Origin | Persistence | Frontend Rendering |
|------|-----------|--------|-------------|-------------------|
| **Custom** | `agent_token` | Hybro-specific; `DirectTransport` extracts text from A2A `message` events and re-emits as individual tokens | None (ephemeral) | `streamingBuffer` → `useStreamingContent` → `EntityAgentBubble` → Streamdown |
| **A2A Standard** | `artifact_update` | Native A2A `TaskArtifactUpdateEvent` with `append`/`last_chunk` semantics | Atomic MongoDB accumulation via `accumulate_artifact_on_message` | `mergeArtifacts` → message store → `ArtifactList` → `PartRenderer` |

This duality causes:

1. **Duplicated complexity**: ~600 lines of frontend production code (`streaming-buffer.ts`, `useStreamingContent.ts`, `typewriter.ts`) plus ~700 lines of tests/docs exist solely for the custom path.
2. **Inconsistent behavior**: Cloud agents (GPT) stream via `agent_token`; local agents (Ollama) stream via `artifact_update`. The same UI produces different visual experiences.
3. **No persistence for token streams**: If the user refreshes mid-stream, `agent_token` content is lost. `artifact_update` content survives because it's persisted incrementally.
4. **Backend branching**: `DirectTransport._handle_stream_message_chunk` manually extracts text from A2A `Part`s and re-emits via `sse_manager.send_agent_token`, bypassing `AgentResponseHandler`. This "accepted asymmetry" adds maintenance burden.
5. **Rendering bugs**: The "one-token-per-line" flash required separate fixes for each path (Phase 2 for `agent_token`, Phase 2.5 for `artifact_update` in `STREAMING_RENDERING_REDESIGN.md`).

### Goal

Eliminate `agent_token` entirely. All agents — cloud, local, hub-relayed — stream content through A2A `artifact_update` events. One SSE event type, one frontend rendering path, one persistence model.

---

## 2. A2A Protocol Streaming Model

This section documents the A2A streaming primitives relevant to this design. Hybro currently runs **A2A v0.3** (`@a2a-js/sdk ^0.3.10`). All primitives in §2.1–§2.3 are available in v0.3. V1.0-only features are called out in §2.5.

### 2.1 Task lifecycle streaming (v0.3)

In v0.3, the client calls `message/stream` (`SendStreamingMessageRequest`). The agent returns a `Task` object followed by zero or more `TaskStatusUpdateEvent` and `TaskArtifactUpdateEvent` objects. The stream closes when the task reaches a terminal state (`completed`, `failed`, `canceled`, `rejected`).

### 2.2 A2A streaming events (spec §4.2)

A2A defines exactly **two** streaming event types. Both are available in v0.3:

| Event | Purpose | Chunk reassembly | Used for |
|-------|---------|-----------------|----------|
| **`TaskArtifactUpdateEvent`** | Delivers new or updated artifacts — the agent's primary output content | Yes (`append` + `last_chunk`) | **Streaming response text**, files, data. This is what replaces `agent_token`. |
| **`TaskStatusUpdateEvent`** | Communicates task lifecycle state changes | No | Status transitions (`working` → `completed`), plus an optional `status.message` carrying intermediate text (e.g., "Searching the web..."). |

**Why `TaskArtifactUpdateEvent` is the right replacement for `agent_token`:**

- It's the **only** event with `append`/`last_chunk` chunk reassembly semantics — designed for incremental content delivery.
- `TaskStatusUpdateEvent` can carry text via `status.message` (a `Message` with `Part[]`), but this is for **progress commentary**, not the primary response. It has no append semantics — each status update replaces the previous status.
- The backend already uses `TaskStatusUpdateEvent` for status transitions (→ `task_update` SSE). That path is unrelated to `agent_token` and remains unchanged.

### 2.3 `TaskArtifactUpdateEvent` fields (v0.3)

The protocol's mechanism for incremental content delivery. Available in v0.3 SDK as `TaskArtifactUpdateEvent`:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `artifact` | `Artifact` | Yes | The artifact generated or updated (contains `artifact_id`, `parts: Part[]`, `name`, `description`) |
| `context_id` | `string` | Yes | The context ID associated with the task |
| `kind` | `"artifact-update"` | Yes | Event discriminator |
| `append` | `boolean` | No | If true, content should be appended to a previously sent artifact with the same ID |
| `last_chunk` | `boolean` | No | If true, this is the final chunk of the artifact |
| `metadata` | `object` | No | Metadata associated with the artifact update |

The `append` + `last_chunk` pair provides **chunk reassembly semantics**: the client accumulates parts from events sharing the same `artifact_id`, and knows the artifact is complete when `last_chunk=true`.

### 2.4 `TaskStatusUpdateEvent` (v0.3)

Communicates task lifecycle transitions (`working` → `input-required` → `completed`, etc.) and carries an optional `status.message: Message` field with intermediate text from the agent (e.g., "Searching...", "Analyzing results...").

**Key distinction from `TaskArtifactUpdateEvent`:**
- Each `TaskStatusUpdateEvent` **replaces** the previous status (no append semantics).
- `status.message` is transient progress info, not the primary response content.
- Hybro already handles `TaskStatusUpdateEvent` via the `task_update` SSE path. **This path is unaffected by the `agent_token` removal and stays as-is.**

### 2.5 Reconnection via `tasks/resubscribe` (v0.3)

V0.3 provides `TaskResubscriptionRequest` (`tasks/resubscribe` JSON-RPC method) to resume a streaming connection after disconnect. The server returns the current task state and resumes streaming. This is the A2A-standard approach to handling disconnections — the custom `streamingBuffer` partial-content promotion in our codebase is a non-standard workaround.

> **v1.0 note**: In v1.0, this is renamed to `SubscribeToTask` (`tasks/subscribe`). The semantics are the same. When migrating to v1.0, update the RPC method name.

### 2.6 v1.0 enhancements (future — do NOT implement now)

The following features are new in A2A v1.0 and are **not available in v0.3**. This design is structured so they can be adopted later without rework.

| v1.0 Feature | Impact on This Design | Migration Path |
|---|---|---|
| **Message-only stream** (agent returns `Message` instead of `Task`) | Our backend always creates tasks. When v1.0 is adopted, simple agents could skip task creation and return a `Message` directly. | Add a new `case 'message'` handler in `useRoomWebhook.ts`. The artifact pipeline remains unchanged. |
| **`SubscribeToTask`** (renamed from `tasks/resubscribe`) | Same semantics as v0.3 `TaskResubscriptionRequest`. | Update RPC method name in backend. |
| **`contextId` on events** | V0.3 `TaskArtifactUpdateEvent` already has `context_id`. V1.0 adds it to more places. | Frontend already ignores `context_id` on artifact events. No change needed. |
| **`ListTasks` with pagination** | Not related to streaming. | Separate migration item (see `A2A_UPGRADE_ROADMAP.md`). |

### 2.7 Why `TaskArtifactUpdateEvent` is the right primitive for text streaming

The `agent_token` design assumed that streaming text requires a separate, ephemeral side-channel. A2A takes the opposite approach: streaming text *is* an artifact being progressively built. Each `TaskArtifactUpdateEvent` with `append=true` adds content to a durable artifact. This means:

- **Persistence is built-in**: The artifact accumulates in the task's artifact list, available via `tasks/get` at any time.
- **Reconnection is built-in**: `tasks/resubscribe` (v0.3) resumes from the current artifact state.
- **Multimodal by default**: The same event can stream text, file, or data parts.
- **Stream termination is explicit**: `last_chunk=true` signals completion, and the `TaskStatusUpdateEvent` with a terminal state signals the overall task is done.

---

## 3. Current Architecture

### 3.1 Backend: Two emission paths in DirectTransport

```
A2A agent streams response
  │
  ├─ kind="message" (text chunks)
  │   └─ DirectTransport._handle_stream_message_chunk()
  │       ├─ extract_parts() → text content
  │       ├─ persist to task history
  │       └─ sse_manager.send_agent_token(token=text)  ← CUSTOM PATH
  │
  └─ kind="artifact-update" (artifact chunks)
      └─ DirectTransport._handle_stream_artifact_update()
          ├─ persist to task artifacts + S3 conversion
          └─ sse_manager.send_artifact_update(artifact, append, last_chunk)  ← A2A PATH
```

### 3.2 Backend: RelayTransport normalizes both

```
Hub daemon publishes events (via relay_client.publish())
  │
  ├─ type="agent_token" → AgentEvent(kind="token") → handler._on_token() → send_agent_token
  └─ type="artifact_update" → AgentEvent(kind="artifact_update") → handler._on_artifact() → send_artifact_update
```

### 3.2.1 Hub daemon: dispatcher.py translates A2A → hub events

```
Local agent streams A2A SSE response to hub daemon
  │
  ├─ A2A kind="message"     → DispatchEvent(type="artifact_update", data={raw, text, parts, append, last_chunk, artifact})  [Phase 5]
  ├─ A2A kind="artifact-update" → DispatchEvent(type="artifact_update", data={raw, text, parts, append, last_chunk})
  ├─ A2A kind="status-update"   → DispatchEvent(type="task_status", data={state, status_text, final, ...})
  └─ A2A kind="task"            → DispatchEvent(type="task_submitted", data={task_id, ...})
```

~~The hub daemon is the **origin** of `agent_token` events in the relay path: it translates A2A `message` streaming events (text chunks) into `agent_token` hub publish events, then publishes them to the cloud backend via HTTP POST. The publish queue (`publish_queue.py`) treats `agent_token` as a `STREAMING_EVENTS` type with low retry budget (`max_retries_streaming = 3`).~~ **Updated (Phase 5)**: The hub daemon now emits `artifact_update` for both `kind="message"` and `kind="artifact-update"` A2A events. The streaming retry tier (`STREAMING_EVENTS`) has been removed; all events use the normal retry budget.

### 3.3 Frontend: Two rendering pipelines (historical — agent_token path removed in Phase 2)

```
SSE events arrive
  │
  ├─ case 'agent_token':
  │   ├─ streamingBuffer.append(message_id, token)
  │   ├─ useStreamingContent() → { isStreaming, streamingText }
  │   ├─ EntityAgentBubble renders streamingText via Streamdown
  │   ├─ On finalize: TypewriterManager reveals final content
  │   └─ On disconnect: partial content promoted to entity
  │
  └─ case 'artifact_update':
      ├─ mergeArtifacts(existing, incoming, append) with mergeTextParts()
      ├─ store.upsertMessage({ artifacts: merged })
      ├─ ArtifactList → ArtifactRenderer → PartRenderer → <p>
      └─ isStreaming flag on ArtifactData controls spinner
```

### 3.4 Files to remove or modify

#### Frontend — Files DELETED (Phase 3)

| File | Lines | Purpose |
|------|-------|---------|
| `src/stores/streaming-buffer.ts` | 129 | Ephemeral token buffer with rAF batching |
| `src/hooks/useStreamingContent.ts` | 47 | React hook for per-message streaming state |
| `src/stores/typewriter.ts` | 127 | TypewriterManager for non-streaming reveal |
| `src/stores/__tests__/streaming-lifecycle.test.ts` | ~200 | Integration tests for buffer + typewriter |
| `tests/unit/stores/streaming-buffer.test.ts` | ~100 | Unit tests for StreamingBuffer |
| `tests/unit/hooks/useStreamingContent.test.ts` | ~50 | Unit tests for the hook |

#### Frontend — Files to MODIFY

| File | Changes |
|------|---------|
| `src/hooks/useRoomWebhook.ts` | Remove `case 'agent_token'`, remove `streamingBuffer` imports, remove `TypewriterManager` usage, remove disconnect partial-promotion logic, remove streaming guards in `task_update` |
| `src/components/message-bubble.tsx` | Remove `useStreamingContent` call, remove `isStreaming`/`streamingText`/`prevIsStreaming`/`isRevealing`/`revealedChars` state machine, simplify `displayContent` derivation |
| `src/components/markdown-content.tsx` | Remove `isStreaming` prop (Streamdown mode derived from artifact `isStreaming` flag instead) |
| `src/lib/types/sse.ts` | Remove `'agent_token'` from type union, remove `token?: string` field |
| `tests/unit/hooks/useRoomWebhook.test.ts` | Remove `agent_token` test cases, remove `streamingBuffer`/`TypewriterManager` mocks |
| `tests/unit/components/message-bubble.test.tsx` | Remove `useStreamingContent` mocks, update streaming assertions |
| `src/stores/message-store/upsert.ts` | Remove the `isEphemeral` + `agent_token` streaming-transition comment and the special `taskStatus = undefined` branch for ephemeral entities (line 67-75). The `isEphemeral` mechanism itself may become dead code — audit whether any other path still creates ephemeral entities. |
| `src/stores/message-store/resolve-display-type.ts` | Remove the `isEphemeral` → `agent-bubble` fast-path (line 23-24) if no other code creates ephemeral entities after `agent_token` removal. |
| `tests/unit/stores/upsert.test.ts` | Remove `isEphemeral` test cases if the field is removed. |
| 6 other test files | Remove `streamingBuffer`/`TypewriterManager` mock imports |

#### Backend — Files to MODIFY

| File | Changes | Phase 1 |
|------|---------|---------|
| `config/settings.py` | Add `stream_via_artifact: bool = True` feature flag | ✅ Done (removed in Phase 4) |
| `modules/transports/direct.py` | `_handle_stream_message_chunk`: emit `send_artifact_update` instead of `send_agent_token` | ✅ Done (flag removed in Phase 4) |
| `modules/transports/relay.py` | Remove `"agent_token" → "token"` normalization; convert inbound `agent_token` hub events to `artifact_update` AgentEvents | ✅ Done (flag removed in Phase 4) |
| `modules/agent_response_handler.py` | Remove `_on_token` method and `case "token"` branch; remove fallback `send_agent_token` in `_on_artifact`; **remove `send_agent_token` call in `_on_status` — replace with `send_artifact_update` or `send_task_update` depending on semantics** | ✅ Done (dead code removed in Phase 4) |
| `services/sse_services.py` | Remove `send_agent_token` method; remove `agent_token` special-casing in `broadcast_to_room` | ✅ Done (Phase 4) |
| `modules/agent_event.py` | Remove `"token"` from `kind` Literal union | ✅ Done (Phase 4) |
| `models/hub.py` | Remove `"agent_token"` from `HubPublishEventType` | Deferred to Phase 5b (kept with deprecation comment for backward compat until all hubs updated) |

#### Hybro-Hub — Files to MODIFY

~~The hub daemon (`hybro-hub`) is the **source** of `agent_token` events for hub-relayed agents. It translates A2A `message` streaming events into `agent_token` hub publish events. These changes can be deferred (the backend's `RelayTransport` normalization handles them transparently), but should be made to complete the migration.~~ **Updated (Phase 5)**: All hub files below have been updated.

| File | Changes | Status |
|------|---------|--------|
| `hub/dispatcher.py` | Changed A2A `kind="message"` translation to `DispatchEvent(type="artifact_update")` with synthetic artifact dict. Collapsed accumulation branches. | ✅ Phase 5 |
| `hub/publish_queue.py` | Removed `STREAMING_EVENTS` and streaming retry tier from `_max_retries_for()`. | ✅ Phase 5 |
| `hub/config.py` | Updated `max_retries_streaming` comment to note it's unused. | ✅ Phase 5 |
| `tests/test_publish_queue.py` | Updated all `agent_token` references; replaced streaming-tier test with normal-tier assertion. | ✅ Phase 5 |
| `tests/test_dispatcher.py` | Added 3 streaming dispatch tests for `kind="message"` → `artifact_update` translation. | ✅ Phase 5 |

#### a2a-adapter — No changes needed

The `a2a-adapter` already emits **only** A2A-standard `TaskArtifactUpdateEvent` via the SDK's `TaskUpdater.add_artifact()`. It has zero references to `agent_token`. Each chunk from `adapter.stream()` becomes one `TaskArtifactUpdateEvent` with `append=True`. No changes required.

---

## 4. Proposed Design

### 4.1 Core principle

> Every streaming text chunk becomes an A2A `artifact_update` event with `append=true`. The artifact accumulates in both the database and the frontend message store. No ephemeral side-channel.

### 4.2 Backend: Convert message chunks to artifact_update

Replace the `send_agent_token` call in `DirectTransport._handle_stream_message_chunk` with `send_artifact_update`:

```python
# BEFORE (direct.py, line 785-791):
if ctx.send_sse:
    await self.sse_manager.send_agent_token(
        ctx.room_id, ctx.current_message.message_id,
        ctx.current_message.agent_id, content,
    )

# AFTER:
if ctx.send_sse and content:
    artifact_dict = {
        "artifact_id": f"{ctx.current_message.message_id}-stream",
        "parts": [{"kind": "text", "text": content}],
    }
    await self.sse_manager.send_artifact_update(
        ctx.room_id, ctx.current_message.message_id,
        ctx.current_message.agent_id, artifact_dict,
        append=True, last_chunk=False,
    )
```

On stream finalization, emit a final `artifact_update` with `last_chunk=true`:

```python
# In _finalize_streaming, after persisting final state:
if ctx.send_sse:
    await self.sse_manager.send_artifact_update(
        ctx.room_id, ctx.current_message.message_id,
        ctx.current_message.agent_id,
        {"artifact_id": f"{ctx.current_message.message_id}-stream", "parts": []},
        append=True, last_chunk=True,
    )
```

### 4.3 Backend: Unify RelayTransport

Currently `relay.py` normalizes hub `"agent_token"` events to `AgentEvent(kind="token")`. Change the normalization map:

```python
# BEFORE (relay.py, line 37-44):
_EVENT_TYPE_MAP = {
    "agent_token": "token",        # ← custom path
    "artifact_update": "artifact_update",
    ...
}

# AFTER:
_EVENT_TYPE_MAP = {
    "agent_token": "artifact_update",  # ← normalize to A2A path
    "artifact_update": "artifact_update",
    ...
}
```

The normalization function wraps the token text into an artifact structure:

```python
def _normalize_token_as_artifact(hub_event) -> AgentEvent:
    return AgentEvent(
        kind="artifact_update",
        message_id=hub_event.message_id,
        room_id=hub_event.room_id,
        agent_id=hub_event.agent_id,
        artifacts=[{
            "artifact_id": f"{hub_event.message_id}-stream",
            "parts": [{"kind": "text", "text": hub_event.token}],
        }],
        append=True,
        last_chunk=False,
        skip_persist=True,  # Hub agents persist on their own; avoid double-write
    )
```

### 4.4 Backend: Remove dead code

1. Delete `AgentResponseHandler._on_token()` and the `case "token"` branch.
2. Delete `SSEManager.send_agent_token()`.
3. Remove the `elif e.text: await self._sse.send_agent_token(...)` fallback in `_on_artifact`.
4. **Remove `send_agent_token` call in `_on_status` (line 234-241)**. Currently, when a `status_update` event contains text, `_on_status()` sends it as `agent_token`. This should be changed to emit a `task_update` SSE with the status message, or a lightweight `artifact_update` — depending on whether the text is progress commentary (use `task_update`) or actual content (use `artifact_update`). The current behavior conflates A2A `TaskStatusUpdateEvent.status.message` with the `agent_token` streaming path.
5. Remove `"token"` from `AgentEvent.kind` Literal.
6. Remove `"agent_token"` from `HubPublishEventType`.
7. Remove `agent_token` debug-level log suppression in `broadcast_to_room`.

### 4.5 Frontend: Upgrade artifact rendering for streaming text

**Status**: Implemented (Phase 2).

`TextPartView` uses `MarkdownContent` with an `isStreaming` prop for streaming-aware markdown rendering (Streamdown caret). The `isStreaming` flag flows from `ArtifactData.isStreaming` (set by the `artifact_update` handler based on `append && !last_chunk`) through `ArtifactRenderer` → `PartRenderer` → `TextPartView`.

`ArtifactRenderer` suppresses card chrome (border, spinner icon) for `-stream` text-only artifacts so streaming text renders inline within the bubble, not inside a bordered card.

### 4.6 Frontend: Simplify EntityAgentBubble

**Status**: Implemented (Phase 2) and refined (Phase 6).

The streaming state machine (`useStreamingContent`, `revealedChars`, `isRevealing`, `prevIsStreaming`, `TypewriterManager`) has been completely removed. The bubble's visual state is now derived by a pure `derivePhase(entity)` function:

```typescript
export function derivePhase(entity: MessageEntity): AgentPhase {
  const hasContent = !!entity.content?.trim()
  const hasArtifacts = (entity.artifacts?.length ?? 0) > 0
  const isStreaming = entity.artifacts?.some(a => a.isStreaming) ?? false
  const hasVisibleBody = hasContent || hasArtifacts

  // Tier 1: taskStatus is authoritative when present
  if (isFailureState(entity.taskStatus))  return 'failed'
  if (isInteractiveState(entity.taskStatus)) return 'interactive'
  if (entity.taskStatus === TASK_STATE.COMPLETED)
    return hasVisibleBody ? 'complete' : 'complete-empty'
  if (entity.taskStatus && !isTerminalState(entity.taskStatus))
    return hasVisibleBody ? 'streaming' : 'waiting'

  // Tier 2: no taskStatus — infer from content signals
  if (isStreaming) return 'streaming'
  if (hasVisibleBody) return 'complete'
  return 'waiting'
}
```

**Content source-of-truth strategy**: The bubble uses a dual-source rendering model:

1. **`entity.content`** is the primary text source, rendered via `MarkdownContent` in the main bubble body. It is populated from `message_text` (for terminal tasks) or `extractTaskContent` from artifacts (for non-terminal tasks recovered from DB).
2. **`entity.artifacts`** render separately below the bubble via `ArtifactList`, but with a **deduplication filter** that suppresses text-only artifacts when `entity.content` already covers the same text. A streaming guard (`if (a.isStreaming) return true`) ensures in-flight artifacts are never suppressed.

This means there is no empty or duplicated output — the dedup filter handles the overlap, and `derivePhase` ensures the correct visual phase regardless of which source has content.

**"Still working…" indicator**: For the `streaming` phase, a secondary indicator appears when `entity.taskStatus` is non-terminal, non-interactive, and no artifact is actively streaming (i.e. content has arrived but the task isn't done yet):

```typescript
{entity.taskStatus && !isTerminalState(entity.taskStatus) &&
 !isInteractiveState(entity.taskStatus) && !isArtifactStreaming && (
  <div>
    <Loader2 className="animate-spin" />
    <span>{entity.taskStatusMessage || 'Still working…'}</span>
  </div>
)}
```

### 4.7 Frontend: Remove agent_token SSE handler

Delete the entire `case 'agent_token'` block from `useRoomWebhook.ts` (~45 lines). Also remove:

- `streamingBuffer.clear()` and `typewriterManager.finishAll()` from room-switch cleanup
- `streamingBuffer.finalize()` and `typewriterManager.finish()` from terminal `task_update`
- `streamingBuffer.isStreaming()` guard in non-terminal `task_update`
- Disconnect handler partial-content promotion loop (`for (const [messageId, partial] of streamingBuffer.entries())`)
- All imports of `streamingBuffer` and `TypewriterManager`

### 4.8 Frontend: Handle the typewriter effect (optional)

The `TypewriterManager` exists to provide a progressive reveal animation when content arrives all at once (non-streaming agents). With this migration, two options:

**Option A (Recommended): Drop the typewriter entirely.** Non-streaming agents already show a working indicator, then content appears. This is the standard pattern in ChatGPT, Claude, and Gemini UIs. The typewriter was a polish feature; removing it simplifies the codebase significantly.

**Option B: Implement typewriter via artifact streaming.** The backend emits a single `artifact_update` with `append=false` and the full content. If a client-side typewriter is desired, the `TextPartView` component can progressively reveal characters using a local `useEffect` timer, entirely contained within the component with no global state.

### 4.9 Frontend: Processing placeholder lifecycle

**Status**: Implemented (Phase 2).

The processing placeholder (`processing-placeholder-{roomId}`) is removed by the `artifact_update` handler when the first artifact content arrives:

```typescript
case 'artifact_update': {
  // Remove processing placeholder on first artifact content
  if (!placeholderDismissedRef.current) {
    store.removeMessage(getProcessingPlaceholderId())
    placeholderDismissedRef.current = true
  }
  // ... existing merge logic ...
}
```

### 4.10 Frontend: SSE disconnect handling via A2A reconnection

**Status**: Implemented (Phase 2 + Phase 6).

The custom `agent_token` disconnect handler (iterating `streamingBuffer.entries()` to promote partial content) has been removed. With the migration to `artifact_update`, disconnect resilience is handled at two levels:

**1. Persisted artifacts (implemented):** Each `artifact_update` chunk is persisted to MongoDB via `accumulate_artifact_on_message`. On page refresh, the DB-hydrated message carries the accumulated artifact content. `convertApiMessageToIncoming` extracts text from task artifacts (via `extractTaskContent`) and populates `entity.content`, so the bubble displays everything received before the disconnect.

**2. `derivePhase` handles working tasks with content (implemented):** The `derivePhase` function (§4.6) maps a non-terminal task with visible body (`hasContent || hasArtifacts`) to `'streaming'` phase, ensuring the bubble renders content with an activity indicator instead of showing an empty waiting state. If the task has no visible body, it renders as `'waiting'` with the original ticking-clock spinner.

**3. `tasks/resubscribe` (future):** A2A v0.3 provides `TaskResubscriptionRequest` to resume a streaming connection after disconnect. The server returns the current task state and resumes streaming. In v1.0, this becomes `SubscribeToTask`. This is available for future implementation but not required — persisted artifact content ensures no visible data loss.

---

## 5. Data Flow (After Migration)

```
A2A Agent streams response
  │
  ├─ kind="message" (text chunks)
  │   └─ DirectTransport._handle_stream_message_chunk()
  │       ├─ extract_parts() → text
  │       ├─ Wrap as artifact: {artifact_id, parts: [{kind:"text", text}]}
  │       ├─ accumulate_artifact_on_message() (atomic MongoDB)
  │       └─ sse_manager.send_artifact_update(append=true, last_chunk=false)
  │
  ├─ kind="artifact-update" (native artifacts)
  │   └─ DirectTransport._handle_stream_artifact_update()
  │       └─ (unchanged — already uses send_artifact_update)
  │
  └─ Stream ends
      └─ _finalize_streaming()
          └─ send_artifact_update(append=true, last_chunk=true)

        │
        ▼ SSE

useRoomWebhook.ts → case 'artifact_update'
  ├─ Remove processing placeholder (first event)
  ├─ Convert wire format → ArtifactData
  ├─ mergeArtifacts() with mergeTextParts() (concatenate consecutive text)
  └─ store.upsertMessage({ artifacts })
        │
        ▼ React

EntityAgentBubble
  ├─ phase = derivePhase(entity)  ← single source of truth for visual state
  ├─ displayContent = entity.content  (text from message_text or extractTaskContent)
  ├─ phase == 'waiting'  → spinner + taskStatusMessage/taskContent
  ├─ phase == 'streaming' or 'complete':
  │   ├─ <MarkdownContent content={displayContent} />  (main bubble body)
  │   └─ "Still working…" indicator (if non-terminal + not actively streaming)
  ├─ entity.artifacts → dedup filter:
  │   ├─ text-only artifacts matching entity.content → suppressed
  │   ├─ streaming artifacts → always shown
  │   └─ remaining → ArtifactList → ArtifactRenderer → PartRenderer
  └─ showIndicator = !entity.content && !hasArtifactContent
```

---

## 6. Migration Plan

### Phase 1: Backend — Emit artifact_update instead of agent_token ✅

**Status**: Implemented. All changes gated behind `STREAM_VIA_ARTIFACT=true` (default: on).

**Scope**: `multi-agents-backend` only. Frontend continues to handle both event types.

**What was done:**

1. Added `stream_via_artifact: bool = True` feature flag in `config/settings.py` (env var: `STREAM_VIA_ARTIFACT`).
2. In `DirectTransport._handle_stream_message_chunk`, replaced `send_agent_token` with flag-gated `send_artifact_update` wrapping text as `{artifact_id: "{msg_id}-stream", parts: [{kind: "text", text}]}` with `append=True, last_chunk=False`. Added content truthiness check to skip empty events.
3. In `DirectTransport._finalize_streaming`, added `send_artifact_update` with `last_chunk=True` and empty parts at the start of finalization.
4. In `RelayTransport._normalize`, hub `agent_token` events are converted to `AgentEvent(kind="artifact_update")` with text wrapped as artifact parts, `append=True`, `skip_persist=True`.
5. In `AgentResponseHandler._on_artifact`, text-only fallback (no artifact object) wraps text as artifact and calls `send_artifact_update` instead of `send_agent_token`.
6. In `AgentResponseHandler._on_status`, replaced `send_agent_token` with `send_task_update(status="working", status_message=text)` — status text is progress commentary per A2A spec §2.4, not primary content.

All changes retain the legacy `agent_token` path behind the flag for rollback (`STREAM_VIA_ARTIFACT=false`).

**Tests added/updated:**
- `tests/test_direct_transport.py`: 5 new tests (artifact_update emission, flag-off fallback, empty content skip, last_chunk finalization, no-last-chunk when flag off).
- `tests/test_agent_response_handler.py`: 3 new tests (artifact text fallback, status task_update, status fallback) + 2 updated (set flag=False for legacy assertions).
- `tests/test_api_relay.py`: 3 new tests replacing 1 (flag-off fallback, flag-on normalization, empty-text edge case).

**Known visual considerations (deferred to Phase 2):**
- Streaming text renders via `TextPartView` (`<p>` tag) instead of `MarkdownContent` (Streamdown). This means no markdown formatting during streaming. The design doc §4.5 addresses this for Phase 2.
- `ArtifactRenderer` wraps streaming text in a bordered card with a spinner icon. Phase 2 should suppress this chrome for `*-stream` text-only artifacts.
- Processing placeholder is already dismissed by `task_submitted` (sent before streaming starts in the direct path). Relay path should be monitored.

**Validation**: Existing frontend handles `artifact_update` already. Cloud agents now behave like Ollama — content appears as artifacts.

### Phase 2: Frontend — Remove agent_token infrastructure

**Status**: Implemented.

**Scope**: `hybro-frontend` only. Backend no longer emits `agent_token`.

**What was done:**

1. Removed `case 'agent_token'` from `sse-handlers/index.ts`.
2. Removed `streamingBuffer` and `TypewriterManager` imports and usage from SSE handlers, `useRoomSSEConnection.ts`, and `useRoomReset.ts`.
3. Simplified `EntityAgentBubble`: removed streaming state machine, derived display from entity props only. Removed dead `isStreaming` prop from `AgentMessageBubbleInner`.
4. Added placeholder dismissal to `case 'artifact_update'`.
5. Upgraded `TextPartView` to use `MarkdownContent` with `isStreaming` prop for streaming artifacts.
6. `ArtifactRenderer` suppresses card chrome for `-stream` text-only artifacts; threads `isStreaming` to `PartRenderer`.
7. Removed `'agent_token'` from `SSEMessage.type` union and `token` field from SSE types.
8. Removed `agent_token` ephemeral branch from `upsert.ts`. Audited `isEphemeral` — still needed for processing placeholders and cancel confirmations.
9. Added `agentId`/`agentSource` to `artifact_update` handler to prevent missing avatar when artifact arrives before `task_submitted`.
10. Added streaming guard to artifact deduplication filter to prevent visual flash when `entity.content` arrives.
11. Updated tests: removed `agent_token` test cases and `createAgentTokenSSE` helper. Removed `streamingBuffer`/`TypewriterManager` mocks from SSE handler tests and `useStreamingContent` mocks from component tests.

**Dead code deleted (Phase 3, executed alongside Phase 2):** `streaming-buffer.ts`, `useStreamingContent.ts`, `typewriter.ts`, `streaming-cursor.tsx`, `streaming-lifecycle.test.ts`, `typewriter.test.ts`, `streaming-buffer.test.ts`, `useStreamingContent.test.ts`, and `streaming-cursor.test.tsx`.

### Phase 3: Delete dead code

**Status**: Implemented (executed alongside Phase 2).

**What was done:**

1. Deleted `src/stores/streaming-buffer.ts`.
2. Deleted `src/hooks/useStreamingContent.ts`.
3. Deleted `src/stores/typewriter.ts`.
4. Deleted `src/components/streaming-cursor.tsx`.
5. Deleted test files: `src/stores/__tests__/streaming-lifecycle.test.ts`, `src/stores/__tests__/typewriter.test.ts`, `tests/unit/stores/streaming-buffer.test.ts`, `tests/unit/hooks/useStreamingContent.test.ts`, `tests/unit/components/streaming-cursor.test.tsx`.

**Remaining (deferred):**

6. ~~Delete `docs/TOKEN_STREAMING_DESIGN.md` (or archive with a deprecation header).~~ Done (added deprecation header).
7. ~~Update `docs/STREAMING_RENDERING_REDESIGN.md` to reflect the unified path.~~ Done.

### Phase 4: Backend cleanup

**Status**: Implemented.

**What was done:**

1. Deleted `SSEManager.send_agent_token()` method from `services/sse_services.py`.
2. Removed `agent_token` log suppression in `broadcast_to_room` (both the silent-drop and debug-level logging).
3. Deleted `AgentResponseHandler._on_token()` and `case "token"` from the match dispatch block.
4. Removed all `stream_via_artifact` feature flag branches in `agent_response_handler.py`, `direct.py`, and `relay.py` — hardcoded the `artifact_update` path.
5. Removed `"token"` from `AgentEvent.kind` Literal union.
6. Kept `"agent_token"` in `HubPublishEventType` with deprecation comment (hub daemon still emits it; relay normalization always maps to `artifact_update`).
7. Deleted `stream_via_artifact: bool = True` setting from `config/settings.py`.
8. Deleted unused `_EVENT_TYPE_MAP` from `relay.py`.
9. Removed `settings` imports from `agent_response_handler.py`, `direct.py`, and `relay.py` (no longer needed).
10. Updated all 4 test files: removed `send_agent_token` mocks, `kind="token"` events, flag-off fallback tests, and monkeypatch calls.

**Note on `HubPublishEventType`**: `"agent_token"` is intentionally retained for backward compatibility with pre-Phase-5 hub daemons. The relay normalization handles it transparently. Remove after all hub daemons are confirmed updated to Phase 5 (Phase 5b).

### Phase 5: Hub daemon — Emit artifact_update instead of agent_token

**Status**: Implemented.

**Scope**: `hybro-hub` only. Backend's `RelayTransport` normalization already handles `agent_token` → `artifact_update` transparently, so this phase aligns the hub's wire protocol.

**What was done:**

1. In `hub/dispatcher.py`, changed A2A `kind="message"` translation from `DispatchEvent(type="agent_token")` to `DispatchEvent(type="artifact_update")` with message text wrapped as a synthetic artifact dict (`artifactId: "{agent_message_id}-stream"`, `parts: [{kind: "text", text}]`, `append: true`, `last_chunk: false`). This matches the backend's `RelayTransport._normalize` extraction path for `artifact_update` events.
2. Updated `dispatch()` accumulation: removed `"agent_token"` from the streaming yield guard and collapsed the `agent_token`/`artifact_update` branches — both message-derived and native artifact events now accumulate into `result.artifact_text`.
3. Removed `STREAMING_EVENTS = frozenset({"agent_token"})` from `hub/publish_queue.py` and simplified `_max_retries_for()` — all events now use either `max_retries_critical` or `max_retries_normal`.
4. Updated `hub/config.py` comment on `max_retries_streaming` to note it's unused (field retained for config-file backward compat).
5. Updated hub tests: added 3 streaming dispatch tests (`test_message_kind_emits_artifact_update`, `test_native_artifact_update_unchanged`, `test_empty_message_skipped`); updated all `agent_token` references in `test_publish_queue.py`.

**Deferred to Phase 5b**: Remove `"agent_token"` from backend `HubPublishEventType` and the `RelayTransport._normalize` backward-compat branch. This requires confirming all hub daemons are updated, since Pydantic validation on the `/publish` endpoint would reject `agent_token` events if the Literal type no longer includes it.

**Coordination note**: The hub can be deployed independently. The backend already accepts `artifact_update` from the hub (Phase 4). Old hub daemons continue to work because the backend's relay normalization still handles `agent_token`.

### Phase 5b: Backend cleanup — Remove agent_token backward compat

**Status**: Implemented.

**Scope**: `multi-agents-backend` only.

**What was done:**

1. Removed `"agent_token"` from `HubPublishEventType` Literal in `models/hub.py`.
2. Removed the `if event_type == "agent_token"` normalization branch in `relay.py` `_normalize()`.
3. Removed `agent_token` from the `models/hub.py` module docstring.
4. Updated `test_api_relay.py`: replaced `agent_token` normalization test cases with a single test asserting `agent_token` is rejected as unknown.
5. Updated `openapi.json`: removed `agent_token` from the `HubPublishEventType` enum.

### Phase 6: Refresh fidelity fixes — Visual consistency after page reload

**Status**: Implemented.

**Scope**: `multi-agents-backend` (1 file) + `hybro-frontend` (2 files + tests).

**Problem**: After page refresh mid-task, the UI displayed visual changes that shouldn't occur:
- The ticking-clock waiting indicator was replaced by "Still working…" with different text.
- The status message changed from "Processing your request…" to the echoed user message.
- `entity.content` was populated with the user's original prompt (from `message_text`), causing a non-terminal task to skip the waiting phase and render in streaming phase.

**Root causes identified:**

1. **Backend**: `_generate_agent_message_content()` in `room_services.py` initialized `message_text` with the user's original prompt at task creation time. This is semantically incorrect — `message_text` should only contain agent-produced output.
2. **Frontend**: `convertApiMessageToIncoming` in `convert-api-message.ts` unconditionally used `message_text` as `content`, so the echoed user prompt appeared as the agent's response body.
3. **Frontend**: `extractTaskError()` reads `task.status.message.parts[0].text`, which for working tasks often contains the echoed user message (per A2A spec, `status.message` is transient progress info). This value was incorrectly mapped to `taskStatusMessage` and used as a content fallback.

**What was done:**

1. **Backend fix** (`multi-agents-backend/services/room_services.py`): Changed `_generate_agent_message_content()` to return `MessageContent(message_task=task)` without setting `message_text`. The field is left `None` at creation time and only populated when the agent produces output (streaming artifacts or terminal response).

2. **Frontend `message_text` gate** (`hybro-frontend/src/stores/message-store/convert-api-message.ts`): Added a condition to skip `message_text` as display `content` for non-terminal agent tasks:
   ```typescript
   const isNonTerminalAgentTask = apiMessage.message_type === 'agent'
     && taskStatus && !isTerminalState(taskStatus)
   if (!isNonTerminalAgentTask) {
     content = apiMessage.message_content.message_text
   }
   ```
   This ensures `message_text` is only used as content for terminal tasks, non-task messages, or user messages.

3. **Frontend `extractedError` gate** (`convert-api-message.ts`): The fallback that promoted `extractTaskError()` to `content` was already gated to terminal states only (`!taskStatus || isTerminalState(taskStatus)`). This was confirmed correct and retained.

4. **Frontend `taskStatusMessage` cleanup** (`convert-api-message.ts`): Removed incorrect mapping of `taskStatusMessage` from `taskError` (which contained the echoed user message from `task.status.message`). `taskStatusMessage` is now left undefined for DB-hydrated messages, allowing the bubble to fall through to `taskContent` or defaults.

5. **Frontend `derivePhase` rewrite** (`message-bubble.tsx`): Replaced the original `derivePhase` with a two-tier function (§4.6) that treats `taskStatus` as authoritative. A non-terminal task with no visible body correctly resolves to `'waiting'` (ticking clock), while one with content resolves to `'streaming'` (content visible + "Still working…" indicator).

**Result**: After refresh, a working task with no agent output stays in the `'waiting'` phase with the original "Processing your request…" text and ticking clock — identical to the live SSE experience. No visual shift occurs.

**Tests added/updated:**
- `convert-api-message.test.ts`: Added `it('ignores message_text for non-terminal agent tasks')`, `it('uses message_text for completed agent tasks')`. Removed incorrect `taskStatusMessage` assertions from non-terminal test cases.
- `message-bubble.test.tsx`: Updated `derivePhase` test coverage for the two-tier logic.

---

## 7. Performance Considerations

### 7.1 Will artifact_update be as fast as agent_token?

The `agent_token` design doc (§5) argued that writing to the Zustand message store at 50-200 tokens/sec would be "catastrophic for React rendering." This justified the ephemeral `streamingBuffer` with rAF batching.

**Why this concern no longer applies:**

1. **mergeTextParts reduces store writes**: Consecutive text parts are concatenated in-memory before the store update. Each `artifact_update` triggers one `upsertMessage` call, but the store diff is minimal (one string concatenation within the existing artifacts array).

2. **Zustand subscribeWithSelector**: Components only re-render when their selected slice changes. `EntityAgentBubble` selects `entity.artifacts`, which changes on each update — but the component is already rendering during streaming, so the marginal cost is one React reconciliation per event.

3. **Event frequency is lower than feared**: Real-world measurements show A2A agents emit 10-50 streaming events per second (not 200). Each event typically contains a word or phrase, not a single character. At 50 events/sec, React easily keeps up.

4. **If performance is still a concern**: Add a simple debounce to `mergeArtifacts` — batch incoming artifact updates over a 16ms window (one frame) before writing to the store. This is ~20 lines of code vs. ~300 lines for the current `StreamingBuffer` + `useStreamingContent` + rAF infrastructure.

### 7.2 Database write overhead

The `agent_token` path does zero database writes during streaming. The `artifact_update` path calls `accumulate_artifact_on_message` for each chunk, which is an atomic MongoDB operation.

**Mitigation options (choose one):**

- **Option A**: Set `skip_persist=true` on streaming text chunks; only persist on `last_chunk=true` or during finalization. The frontend reconstructs from SSE events; page refresh falls back to the last persisted state.
- **Option B**: Batch persistence — accumulate chunks in memory and flush to MongoDB every N chunks or every M seconds. The `_handle_stream_message_chunk` already accumulates `full_response_text` in `streaming_state`, so the final persist captures everything.
- **Option C (Recommended)**: Keep per-chunk persistence. MongoDB `$concatArrays` is an atomic pipeline update, not a read-modify-write. At 10-50 ops/sec per active stream, this is well within MongoDB's capacity. The benefit — full persistence on every chunk — means page refresh never loses streaming content. This aligns with A2A's design intent where artifacts are durable objects, retrievable via `tasks/get` at any time.

### 7.3 SSE payload size

`agent_token` payload: `{ message_id, agent_id, token, timestamp }` (~150 bytes)
`artifact_update` payload: `{ message_id, agent_id, artifact: { artifact_id, parts: [{ kind, text }] }, append, last_chunk, timestamp }` (~250 bytes)

The ~100 byte overhead per event is negligible on modern connections. At 50 events/sec, the difference is 5KB/sec — imperceptible.

---

## 8. Backward Compatibility

### 8.1 Hub agents and the hub daemon

Hub agents stream A2A `message` events to the hub daemon, which translates them into `agent_token` hub publish events (`hub/dispatcher.py`, line 325-333). The hub daemon publishes these to the cloud backend via `relay_client.publish()`.

During Phases 1-4, the backend's `RelayTransport` normalization (§4.3) converts these inbound `agent_token` events to `artifact_update` AgentEvents **transparently**. Hub agents and the hub daemon do not need to change for the migration to work.

In Phase 5 (optional), the hub daemon itself can be updated to emit `artifact_update` instead of `agent_token`, eliminating the normalization step. The `publish_queue.py` streaming retry logic (`STREAMING_EVENTS = {"agent_token"}`, `max_retries_streaming = 3`) would also be updated at that point.

### 8.2 Frontend fallback during rollout

During Phase 1 (backend migrated, frontend not yet), the frontend already handles `artifact_update` events (Phase 2.5 of `STREAMING_RENDERING_REDESIGN.md`). Cloud agents will seamlessly switch from the `agent_token` rendering path to the `artifact_update` rendering path with no frontend changes.

During Phase 2 (frontend cleanup), the `agent_token` handler is removed. If a rollback is needed, the feature flag on the backend reverts to emitting `agent_token`, and the frontend can be reverted independently.

### 8.3 Existing messages

Messages already persisted in the database are unaffected. Historical messages that were streamed via `agent_token` have their final content stored in `message_content.message_text` (written by the terminal `task_update`/`agent_response` event). They will render normally.

---

## 9. Testing Strategy

### Unit tests to ADD

| Test | Description | Status |
|------|-------------|--------|
| `DirectTransport: message chunk emits artifact_update` | Verify `_handle_stream_message_chunk` calls `send_artifact_update` with correct artifact structure | ✅ `test_direct_transport.py` |
| `DirectTransport: finalize emits last_chunk=true` | Verify final artifact_update has `last_chunk=true` | ✅ `test_direct_transport.py` |
| `DirectTransport: flag-off falls back to agent_token` | Verify legacy path when `stream_via_artifact=False` | ✅ `test_direct_transport.py` |
| `DirectTransport: empty content skips SSE` | Verify no event emitted for empty content | ✅ `test_direct_transport.py` |
| `RelayTransport: agent_token normalized to artifact_update` | Verify hub `agent_token` events are wrapped as artifact AgentEvents | ✅ `test_api_relay.py` |
| `RelayTransport: empty token produces no artifacts` | Verify `artifacts=None` when token text is empty | ✅ `test_api_relay.py` |
| `AgentResponseHandler._on_artifact: text-only uses artifact_update` | Verify text fallback wraps as artifact when flag on | ✅ `test_agent_response_handler.py` |
| `AgentResponseHandler._on_status: emits task_update` | Verify status_update with text emits `send_task_update`, not `agent_token` | ✅ `test_agent_response_handler.py` |
| `AgentResponseHandler._on_status: fallback to token` | Verify legacy `send_agent_token` path when flag off | ✅ `test_agent_response_handler.py` |
| `TextPartView: renders markdown with streaming caret` | Verify Streamdown receives `isStreaming` prop from artifact | ✅ Phase 2 |
| `EntityAgentBubble: shows indicator until artifact arrives` | Verify `showIndicator` hides when `entity.artifacts.length > 0` | ✅ Phase 2 |
| `artifact_update handler: dismisses processing placeholder` | Verify placeholder removed on first artifact event | ✅ Phase 2 |
| `Hub dispatcher: message kind emits artifact_update` | (Phase 5) Verify A2A `message` events are translated to `DispatchEvent(type="artifact_update")` | ✅ `test_dispatcher.py` |
| `convertApiMessage: ignores message_text for non-terminal agent tasks` | (Phase 6) Verify user prompt seed in `message_text` is not used as `content` for working tasks | ✅ `convert-api-message.test.ts` |
| `convertApiMessage: uses message_text for completed agent tasks` | (Phase 6) Verify `message_text` populates `content` for terminal tasks | ✅ `convert-api-message.test.ts` |
| `derivePhase: non-terminal task without body → waiting` | (Phase 6) Verify correct waiting phase after refresh for empty working tasks | ✅ `message-bubble.test.tsx` |
| `derivePhase: non-terminal task with content → streaming` | (Phase 6) Verify streaming phase for working tasks with partial content | ✅ `message-bubble.test.tsx` |

### Unit tests REMOVED (Phase 2/3)

| Test | Reason | Status |
|------|--------|--------|
| `streaming-lifecycle.test.ts` (entire file) | Tests `StreamingBuffer` + `TypewriterManager` lifecycle | ✅ Deleted |
| `typewriter.test.ts` (entire file) | Tests deleted module | ✅ Deleted |
| `streaming-buffer.test.ts` (entire file) | Tests deleted module | ✅ Deleted |
| `useStreamingContent.test.ts` (entire file) | Tests deleted hook | ✅ Deleted |
| `agent_token` cases in `useRoomWebhook.test.ts` | Handler deleted | ✅ Removed |
| `streamingBuffer` mocks in 6 test files | Dead imports | ✅ Removed |
| `_on_status → send_agent_token` cases in `test_agent_response_handler.py` | `_on_status` no longer emits `agent_token` | ✅ Phase 1 |

### Manual testing matrix

| Scenario | What to verify |
|----------|---------------|
| Cloud agent (GPT) streaming | Text appears progressively as artifact, markdown renders correctly, caret visible during stream |
| Local agent (Ollama) streaming | Behavior unchanged from Phase 2.5 fix (already uses artifact_update) |
| Hub-relayed agent streaming | Hub `agent_token` events render as artifact content |
| Non-streaming agent | Content appears after task completes (no typewriter unless Option B chosen) |
| Page refresh mid-stream | Content up to last persisted chunk is visible on reload |
| Page refresh mid-stream (waiting phase) | Working task with no agent output: ticking clock + "Processing your request…" text persists identically after refresh — no visual shift |
| Page refresh mid-stream (streaming phase) | Working task with partial content: content visible + "Still working…" indicator — no echoed user message |
| SSE disconnect mid-stream | Partial artifact content is already in the store (persisted); no data loss. If `tasks/resubscribe` is implemented, stream resumes. |
| Multiple agents streaming simultaneously | Each agent's artifact accumulates independently |
| SSE reconnection via tasks/resubscribe | (Future) Client resubscribes; receives current Task state + remaining events |

---

## 10. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Performance regression from per-chunk store updates | Low | Medium | Benchmark with GPT-4o streaming; add 16ms debounce if needed (§7.1) |
| MongoDB write amplification | Low | Low | `$concatArrays` is atomic; monitor `oplog` size during load test (§7.2) |
| Hub agents still emit `agent_token` after backend migration | ~~Certain~~ Resolved | None | ~~RelayTransport normalization handles this transparently (§8.1). Phase 5 updates the hub daemon itself.~~ Phase 5 complete — hub now emits `artifact_update`. Backend relay normalization retained for old hubs until Phase 5b. |
| Hub daemon deployment coordination | Low | Medium | Phase 5 is independent of Phases 1-4; RelayTransport normalization ensures backward compat. Hub can be deployed independently; old hubs continue to work. |
| Typewriter removal feels like UX regression | Medium | Low | If user feedback is negative, implement Option B (§4.8) — component-local typewriter with no global state |
| SSE payload size increase | Low | Negligible | 100 bytes/event overhead, 5KB/sec at peak (§7.3) |
| Feature flag rollback needed | Low | Low | Backend flag reverts to `agent_token`; frontend handles both during transition |

---

## 11. Success Metrics

| Metric | Before | Target |
|--------|--------|--------|
| Streaming-related frontend source files | 9 (buffer, hook, typewriter, tests, types, handler) | 0 custom; all via artifact path |
| SSE event types for streaming | 2 (`agent_token` + `artifact_update`) | 1 (`artifact_update`) |
| Backend emission paths | 2 (`send_agent_token` + `send_artifact_update`) | 1 (`send_artifact_update`) |
| Content survival on page refresh mid-stream | No (ephemeral buffer lost) | Yes (persisted per chunk) |
| Visual fidelity on page refresh | Waiting indicator lost; echoed user message shown as content | Identical to live SSE experience (Phase 6) |
| Reconnection strategy | Custom partial-content promotion | A2A `tasks/resubscribe` (v0.3) or `tasks/get` (spec-compliant) |
| Lines of frontend code removed | — | ~600 production + ~700 test/doc |
| Visual streaming behavior | Different per agent type | Identical for all agents |

---

## 12. Open Questions

1. **Typewriter animation**: Do we want to keep a progressive reveal for non-streaming agents? If yes, implement as a component-local effect in `TextPartView` (Option B in §4.8). Needs product input.

2. **Persistence frequency**: Should every streaming chunk be persisted to MongoDB, or should we batch/skip for performance? Current recommendation is per-chunk persistence (§7.2 Option C), but this can be revisited under load.

3. **Hub daemon migration**: ~~After the backend stops emitting `agent_token`, should the hub daemon protocol vocabulary (`HubPublishEventType`) also drop `"agent_token"`? This requires coordinating with hub agent implementations.~~ **Resolved (Phase 5)**: Hub daemon now emits `artifact_update` instead of `agent_token`. `HubPublishEventType` retains `"agent_token"` for backward compat with old hubs until Phase 5b confirms all hubs are updated.

4. **Artifact deduplication**: ~~The frontend currently deduplicates text-only artifacts whose text matches `entity.content`. With all streaming going through artifacts, this dedup logic may need to be revisited — the artifact text *is* the primary content, not a duplicate.~~ **Resolved (Phase 2)**: Added `if (a.isStreaming) return true` guard to the dedup filter so streaming artifacts are never suppressed. Once streaming completes and `entity.content` arrives, finished text-only artifacts matching `entity.content` are correctly filtered to avoid duplicate display.

5. **`entity.content` vs `entity.artifacts` as source of truth**: ~~Should the final artifact text be promoted to `entity.content` on `last_chunk=true`? This would maintain backward compatibility with components that read `entity.content` directly. Alternatively, components can be updated to read from artifacts.~~ **Resolved (Phase 6)**: The settled design uses a dual-source model. `entity.content` is the primary text source for the bubble body (populated from `message_text` for terminal tasks, or `extractTaskContent` from artifacts for non-terminal DB-hydrated messages). Non-text artifacts render separately via `ArtifactList`. A deduplication filter in the render layer suppresses text-only artifacts that duplicate `entity.content`, with a streaming guard to never suppress in-flight artifacts. See §4.6 for details.

6. **`tasks/resubscribe` for reconnection**: The v0.3 SDK provides `TaskResubscriptionRequest` (`tasks/resubscribe`) for resuming a stream after disconnect. Should we implement this as a reconnection strategy, or is relying on persisted artifact state via `tasks/get` sufficient? The former provides real-time resume; the latter is simpler but loses remaining streaming events. (In v1.0, this becomes `SubscribeToTask`.)

7. **Message-only stream support (v1.0 only)**: A2A v1.0 introduces a "Message-only stream" pattern where agents return a single `Message` instead of a `Task`. This is NOT available in v0.3. Our backend always creates tasks, which is correct for v0.3. When migrating to v1.0, consider supporting the `Message`-only path for simple agents. This is orthogonal to the `agent_token` removal.

---

## 13. Timeline Estimate

| Phase | Effort | Dependencies | Status |
|-------|--------|-------------|--------|
| Phase 1: Backend emission change | 1-2 days | None | ✅ Complete |
| Phase 2: Frontend cleanup | 2-3 days | Phase 1 deployed | ✅ Complete |
| Phase 3: Delete dead code | 0.5 day | Phase 2 merged | ✅ Complete |
| Phase 4: Backend cleanup | 0.5 day | Phase 1 stable for ≥1 week | ✅ Complete |
| Phase 5: Hub daemon migration | 0.5-1 day | Phase 1 deployed (backend handles both) | ✅ Complete |
| Phase 5b: Backend backward-compat removal | 0.5 day | All hub daemons confirmed running Phase 5+ | ✅ Complete |
| Phase 6: Refresh fidelity fixes | 0.5 day | Phase 2 complete | ✅ Complete |
| **Total** | **5.5-7.5 days** | | |

---

## Appendix A: Deleted Module Inventory (all deleted in Phase 3)

| Module | Lines | Dependents (removed) | Replacement |
|--------|-------|-----------|-------------|
| `streaming-buffer.ts` | 129 | `useStreamingContent.ts`, `typewriter.ts`, `useRoomWebhook.ts`, 6 test files | `mergeArtifacts` in message store |
| `useStreamingContent.ts` | 47 | `message-bubble.tsx`, 1 test file | `entity.artifacts[].isStreaming` |
| `typewriter.ts` | 127 | `useRoomWebhook.ts`, 1 test file | Dropped (Option A) or component-local `useEffect` (Option B) |
| `streaming-cursor.tsx` | 14 | `message-bubble.tsx` | Streamdown built-in caret (`caret="block"`) |

## Appendix B: Comparison with agent_token Design

| Dimension | `agent_token` (current) | `artifact_update` (proposed) |
|-----------|------------------------|------------------------------|
| SSE event | `agent_token { token }` | `artifact_update { artifact, append, last_chunk }` |
| Granularity | Single token (word/char) | Single token wrapped as artifact part |
| Persistence | None (ephemeral buffer) | Atomic MongoDB per chunk |
| Frontend store | External `StreamingBuffer` (raw Map + rAF) | Zustand message store (`mergeArtifacts`) |
| React binding | `useSyncExternalStore` | Zustand `subscribeWithSelector` |
| Renderer | Streamdown via `MarkdownContent` | Streamdown via `TextPartView` → `MarkdownContent` |
| Reconnection | Partial content lost | `tasks/resubscribe` (v0.3) or `tasks/get` for persisted state |
| Finalization | `streamingBuffer.finalize()` + typewriter | `last_chunk=true` → `isStreaming=false` (A2A `TaskArtifactUpdateEvent`) |
| Code footprint | ~600 lines production + ~700 lines tests | Reuses existing artifact infrastructure |
| A2A compliance | Non-compliant (custom event) | Fully compliant |
