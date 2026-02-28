# Token Streaming Design — Real-time Agent Token Streaming

**Status**: Implemented
**Depends on**: None (backend already emits `agent_token` SSE events)
**Decoupled from**: All other frontend design docs

---

## 1. Problem Statement

Agent responses currently appear all at once when the `agent_response` SSE event arrives
with the complete text. This creates a poor user experience: for long agent responses
(which can take 10-30 seconds to generate), the user sees only a spinner and then a
sudden wall of text.

The backend already emits `agent_token` SSE events in real-time as tokens stream from
agents (`services/sse_services.py` lines 195-217). The event type is defined in the
frontend SSE type union but `handleSSEMessage` has no case for it — tokens are discarded.

Implementing token streaming gives users immediate, progressive feedback as the agent
generates its response, dramatically improving perceived latency.

---

## 2. Current State

### Backend SSE Emission

```python
async def send_agent_token(self, room_id, message_id, agent_id, token):
    data = {
        "message_id": message_id,
        "agent_id": agent_id,
        "token": token,            # Single token string
        "timestamp": utcnow().isoformat(),
    }
    await self.broadcast_to_room(room_id, "agent_token", data)
```

Tokens arrive individually, one per SSE event. A typical response generates
100-2000 token events over 5-30 seconds.

### Frontend SSE Type Union (`src/lib/types/sse.ts`)

`agent_token` is NOT in the `SSEMessage.type` union (only 8 types listed). However the
`SSEConnection` class forwards any non-heartbeat event, so the events reach
`handleSSEMessage` and fall through the switch with no matching case.

### Message Store (`src/stores/message-store/`)

`MessageEntity.content` is a string that holds the complete message text. The store uses
`zustand/subscribeWithSelector` for selective re-renders. Upserting on every token
(100-2000 times) would trigger store reconciliation, conflict resolution, sort recomputation,
and React re-renders for every single token — far too expensive.

### Message Bubble (`src/components/message-bubble.tsx`)

Agent message bubbles render `entity.content` through `MarkdownContent`. The component
re-renders when the entity changes.

---

## 3. Proposed Design

### 3.1 Two-Tier State Architecture

The core insight is that streaming tokens are **ephemeral** (they are replaced by the
final `agent_response` content) and **high-frequency** (hundreds per second). They
should not touch the normalized message store.

```
                         ┌─────────────────────────┐
  agent_token SSE ──────►│ Streaming Buffer         │ (ephemeral, high-frequency)
                         │ Map<messageId, string>   │
                         │ Updated via ref, not     │
                         │ Zustand state            │
                         └──────────┬──────────────┘
                                    │
                          useStreamingContent(id)
                          (subscribes to buffer via
                           useSyncExternalStore)
                                    │
                                    ▼
                         ┌─────────────────────────┐
                         │ message-bubble.tsx       │
                         │ Renders streaming text   │
                         │ with typing cursor       │
                         └─────────────────────────┘

  agent_response SSE ──► ┌─────────────────────────┐
                         │ Message Store            │ (normalized, conflict-resolved)
                         │ upsertMessage()          │
                         │ Final content only       │
                         └──────────┬──────────────┘
                                    │
                          Clear streaming buffer
                          for this messageId
                                    │
                                    ▼
                         ┌─────────────────────────┐
                         │ message-bubble.tsx       │
                         │ Renders final content    │
                         │ (no typing cursor)       │
                         └─────────────────────────┘
```

### 3.2 Lifecycle

1. First `agent_token` arrives for `message_id=X`. If no entity exists yet, create a
   placeholder entity in the message store (with empty content, `isEphemeral: true`).
   If the entity was created by `task_submitted` (displayType: task-status), re-upsert
   it as ephemeral so it renders as an agent-bubble for streaming.
   Initialize the streaming buffer entry for `X`.
2. Subsequent `agent_token` events append to the buffer string.
3. A `requestAnimationFrame`-based flush notifies subscribers (React components) at
   most once per frame (~60fps).
4. `task_update` (with terminal status) OR `agent_response` arrives with the final
   complete text. Finalize (delete) the streaming buffer entry for `X`. Upsert the
   entity with full content and `isEphemeral: false`. The component switches from
   streaming buffer to entity content seamlessly.
   **Note:** The backend sends `task_update` (not `agent_response`) when streaming
   completes for regular agent messages. `agent_response` is only used for
   coordinator-generated summaries (debate mode).
5. If the SSE disconnects mid-stream, the partial content in the buffer is promoted
   to the entity content as a fallback.

---

## 4. Files to Modify / Create

### 4.1 `src/lib/types/sse.ts` — Add `agent_token` to type union

The type `agent_token` should be added to the `SSEMessage.type` union:

```typescript
type: '...' | 'agent_token'
```

Data fields already sufficient (`message_id`, `agent_id`, content via new `token` field):

```typescript
// Token streaming field
token?: string
```

### 4.2 New file: `src/stores/streaming-buffer.ts` — Ephemeral token buffer

This is NOT a Zustand store. It uses raw mutable state with `useSyncExternalStore` for
React integration, optimized for high write frequency.

```typescript
type Listener = () => void

class StreamingBuffer {
  private buffers = new Map<string, string>()
  private listeners = new Set<Listener>()
  private pendingFlush = false
  private version = 0

  /** Append a token to the buffer for a message. */
  append(messageId: string, token: string): void {
    const existing = this.buffers.get(messageId) ?? ''
    this.buffers.set(messageId, existing + token)
    this.scheduleFlush()
  }

  /** Get accumulated text for a message (empty string if not streaming). */
  get(messageId: string): string {
    return this.buffers.get(messageId) ?? ''
  }

  /** Check if a message is currently streaming. */
  isStreaming(messageId: string): boolean {
    return this.buffers.has(messageId)
  }

  /** Remove buffer entry (called when agent_response arrives). */
  finalize(messageId: string): string {
    const content = this.buffers.get(messageId) ?? ''
    this.buffers.delete(messageId)
    this.scheduleFlush()
    return content
  }

  /** Iterate over all active buffers (used by disconnect handler). */
  entries(): IterableIterator<[string, string]> {
    return this.buffers.entries()
  }

  /** Clear all buffers (room switch, disconnect). */
  clear(): void {
    this.buffers.clear()
    this.scheduleFlush()
  }

  /** Subscribe for React useSyncExternalStore. */
  subscribe(listener: Listener): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  /** Snapshot for useSyncExternalStore. */
  getSnapshot(): number {
    return this.version
  }

  private scheduleFlush(): void {
    if (this.pendingFlush) return
    this.pendingFlush = true
    requestAnimationFrame(() => {
      this.pendingFlush = false
      this.version++
      for (const listener of this.listeners) {
        listener()
      }
    })
  }
}

export const streamingBuffer = new StreamingBuffer()
```

### 4.3 New hook: `src/hooks/useStreamingContent.ts`

Provides React-compatible access to the streaming buffer for a single message:

```typescript
import { useSyncExternalStore } from 'react'
import { streamingBuffer } from '@/stores/streaming-buffer'

export function useStreamingContent(messageId: string): {
  streamingText: string
  isStreaming: boolean
} {
  const _version = useSyncExternalStore(
    streamingBuffer.subscribe.bind(streamingBuffer),
    streamingBuffer.getSnapshot.bind(streamingBuffer),
  )

  // _version ensures re-render on flush. Read from buffer directly.
  return {
    streamingText: streamingBuffer.get(messageId),
    isStreaming: streamingBuffer.isStreaming(messageId),
  }
}
```

### 4.4 `src/hooks/useRoomWebhook.ts` — Handle `agent_token` SSE

Add a new case to `handleSSEMessage`:

```typescript
case 'agent_token': {
  const { message_id, agent_id, token } = msg.data
  if (!message_id || !token) break

  // Ensure a placeholder entity exists so the message bubble renders
  if (!store.entities[message_id]) {
    const agentName = agent_id ? await getAgentName(agent_id) : 'Agent'
    store.upsertMessage({
      id: message_id,
      roomId,
      messageType: 'agent',
      content: '',
      senderName: agentName,
      timestamp: msg.timestamp,
      agentId: agent_id,
      isEphemeral: true,
    }, 'sse')
  }

  // Append to the streaming buffer (does NOT touch the message store)
  streamingBuffer.append(message_id, token)
  break
}
```

Modify the existing `agent_response` case to finalize the buffer:

```typescript
case 'agent_response': {
  // Finalize streaming buffer (discard partial — full content is authoritative)
  streamingBuffer.finalize(msg.data.message_id)
  // ... existing upsert logic ...
  break
}
```

Modify the `task_update` case to finalize the buffer on terminal states (the backend
sends `task_update`, not `agent_response`, when streaming completes):

```typescript
case 'task_update': {
  // Finalize streaming buffer on terminal task_update
  if (isTerminalState(status)) {
    streamingBuffer.finalize(messageId)
  }
  store.upsertMessage({
    // ... existing fields ...
    isEphemeral: isTerminalState(status) ? false : undefined,
  }, 'sse')
  break
}
```

On SSE disconnect, promote any partial streaming content to entity content:

```typescript
// In the SSE disconnect handler
for (const [messageId, partial] of streamingBuffer.entries()) {
  if (partial) {
    store.upsertMessage({
      id: messageId,
      roomId,
      messageType: 'agent',
      content: partial,
      senderName: store.entities[messageId]?.senderName || 'Agent',
      timestamp: new Date().toISOString(),
    }, 'sse')
  }
}
streamingBuffer.clear()
```

### 4.5 `src/components/message-bubble.tsx` — Streaming-aware rendering

The agent message bubble should check the streaming buffer before falling back to
entity content:

```typescript
function AgentMessageBubble({ entity }: { entity: MessageEntity }) {
  const { streamingText, isStreaming } = useStreamingContent(entity.id)
  const displayContent = isStreaming ? streamingText : entity.content

  return (
    <div className="...">
      <MarkdownContent content={displayContent} />
      {isStreaming && <StreamingCursor />}
    </div>
  )
}
```

### 4.6 New component: `src/components/streaming-cursor.tsx`

A simple blinking cursor indicator appended to streaming text:

```typescript
export function StreamingCursor() {
  return (
    <span className="inline-block w-2 h-4 bg-foreground/60 animate-pulse ml-0.5 align-text-bottom" />
  )
}
```

---

## 5. Performance Considerations

### 5.1 Write Frequency

Agent token events can arrive at 50-200 events per second. Direct Zustand state
updates at this frequency would be catastrophic for React rendering.

The streaming buffer uses:
- **Mutable `Map`** — no allocations per `append` call beyond the new string. Note
  that string concatenation in V8 is O(n) in total length (strings are immutable),
  not O(1). For typical agent responses (< 10K chars) this is fast. For very long
  responses (50K+ chars), the array-of-chunks optimization described in 5.3 avoids
  repeated full-copy allocation.
- **`requestAnimationFrame` batching** — at most 1 React re-render per frame (60fps).
- **`useSyncExternalStore`** — minimal subscription overhead, no extra Zustand
  middleware.

### 5.2 Markdown Re-rendering

`MarkdownContent` is relatively expensive (parsing + highlighting). During streaming,
consider:

- **Option A**: Render streaming text as plain `<pre>` with a monospace font, switch to
  full markdown rendering only after `agent_response` arrives (simplest, fastest).
- **Option B**: Debounce markdown rendering to every 200ms during streaming, show raw
  text in between (balanced).
- **Option C**: Full markdown rendering on every frame (most visually appealing but
  potentially janky for complex markdown).

**Recommended**: Option B — debounce markdown rendering during streaming. Use a ref to
track the last-rendered markdown timestamp. If < 200ms since last render, show raw text;
otherwise re-render markdown. On finalization, always render full markdown.

**Layout performance note** (Vercel rule `rendering-content-visibility`): For very
long streaming responses that push other messages off-screen, consider adding
`content-visibility: auto` to off-screen message containers. This avoids recalculating
layout for the entire message list on every streaming re-render. This is an
implementation-time optimization and does not affect the streaming buffer design.

### 5.3 Memory

String concatenation creates new string objects. For very long responses (50K+ chars),
this could cause GC pressure. Mitigation: if the buffer exceeds 100KB, switch to an
array-of-chunks representation and join only on read. This is an optimization that can
be deferred.

---

## 6. State Management Changes

### 6.1 New: Streaming Buffer (`src/stores/streaming-buffer.ts`)

Singleton module-level instance. Not a Zustand store. Lifecycle:

- Created once on module load.
- `append()` called from SSE handler.
- `finalize()` called when `agent_response` arrives.
- `clear()` called on room switch or SSE disconnect.

### 6.2 Message Store

No structural changes. The streaming buffer is external. The message store only sees
the final content via the existing `agent_response` → `upsertMessage` flow.

One addition: when the first `agent_token` arrives for an unknown `message_id`, a
placeholder entity is created with `content: ''` and `isEphemeral: true`. This entity
gets replaced by the full `agent_response` entity.

### 6.3 Room UI Store

No changes.

---

## 7. Key Decisions

| Decision | Rationale |
|---|---|
| Separate ephemeral buffer (not Zustand) | Avoids triggering normalized store reconciliation, conflict resolution, and sort recomputation 100+ times per second. |
| `requestAnimationFrame` batching | Caps React re-renders at 60fps regardless of token arrival rate. |
| `useSyncExternalStore` for React binding | Standard React API for external stores. No dependencies, no middleware. |
| Placeholder entity on first token | Ensures the message bubble renders immediately, before the final response arrives. |
| Finalize buffer on terminal `task_update` or `agent_response` | The backend sends `task_update` (not `agent_response`) when streaming completes. Both paths finalize the buffer. |
| Convert task-status to agent-bubble on first token | When `task_submitted` arrives before `agent_token`, the entity is a task-status card. Setting `isEphemeral: true` triggers `resolveDisplayType` to return `agent-bubble`. |
| Promote partial content on disconnect | Prevents data loss if SSE disconnects mid-stream. The partial text is better than nothing. |
| Debounced markdown rendering | Full markdown parsing on every frame is too expensive. Debouncing at 200ms gives a smooth experience. |
| Client-side typewriter for non-streamed content | When content arrives all at once (agent doesn't stream), the `TypewriterManager` feeds text progressively into the streaming buffer so the existing cursor + throttled-render UI works transparently. Duration scales with content length (~800ms). |

---

## 8. Error Handling

| Scenario | Behavior |
|---|---|
| `agent_token` with unknown `message_id` (no entity) | Create placeholder entity. Buffer accumulates tokens. |
| `agent_token` for entity created by `task_submitted` | Re-upsert entity with `isEphemeral: true` to convert from task-status to agent-bubble. Buffer accumulates tokens. |
| `task_update` (completed) arrives after streaming | Finalize buffer, upsert final content with `isEphemeral: false`. Entity transitions to permanent agent-bubble. |
| `agent_response` arrives without prior `agent_token` events | Typewriter effect: create ephemeral entity, feed content progressively through `TypewriterManager`, then upsert final content on completion. |
| `task_update` (completed) arrives without prior streaming | Typewriter effect: same as above. Content is revealed progressively instead of appearing all at once. |
| SSE disconnect during streaming | Promote partial buffer content to entity content. Clear buffer. On reconnect, `reconcileWithDb` may update with the final content from the DB. |
| `agent_token` for a message that already has final content | Ignore — the entity already has authoritative content from `task_update`/`agent_response`. |
| Extremely rapid tokens (> 200/sec) | `requestAnimationFrame` batching ensures at most 60 React updates/sec regardless. String concatenation remains O(n) but is fast in V8. |

---

## 9. Out of Scope

- Streaming for `task_update` content (task status messages are discrete, not streamed).
- Streaming artifact content (covered in `ARTIFACT_RENDERING_DESIGN.md`).
- Backend changes — `agent_token` emission is already implemented.
- Streaming cancellation feedback (handled by existing `processing_status` logic).

---

## 10. Testing Strategy

### Implemented

- Unit test `StreamingBuffer`: `append`, `get`, `isStreaming`, `finalize`, `clear`,
  `subscribe`/`getSnapshot` batching, per-message snapshots.
- Unit test `TypewriterManager` + `startTypewriter`: progressive delivery, `finish()`
  jump-to-end, `abort()` without onComplete, `finishAll()`, concurrent typewriters.
- Integration test `handleSSEMessage` for `agent_token`: placeholder creation, buffer
  append, task-status → agent-bubble conversion.
- Integration test: full lifecycle (`task_submitted` → `agent_token` → `task_update`).
- Integration test: typewriter lifecycle for `task_update(completed)` without prior
  streaming, room-switch cleanup, `agent_response` typewriter, `agent_token` abort.
- Edge case: `agent_response` before any `agent_token` (direct response, no streaming).
- Edge case: SSE disconnect mid-stream with non-terminal task status, verify partial
  content promoted with `optimistic` source, DB reconciliation accepted.
- Edge case: multiple agents streaming simultaneously (different `message_id`s).
- Edge case: late `agent_token` after `agent_response` is ignored.
- Unit test `resolveDisplayType`: ephemeral without task → agent-bubble, ephemeral
  with non-terminal task → task-status (processing placeholder regression check).

### Deferred

- Unit test `useStreamingContent` hook (requires React test renderer; hook is < 20
  lines and covered implicitly by integration tests).
- Unit test `StreamingCursor` component (trivial CSS-only component, 15 lines).
- Performance test: simulate 1000 `agent_token` events at 100/sec, verify React
  re-renders stay at ~60fps. Mitigated by per-message snapshots and RAF batching.
