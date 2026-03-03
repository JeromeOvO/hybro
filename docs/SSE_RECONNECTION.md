# SSE Reconnection — Connection Management & Gap Recovery

> **Status: Implemented**

---

## 1. Overview

The real-time communication layer uses Server-Sent Events (SSE) via the native
`EventSource` API. The system handles connection lifecycle, automatic reconnection with
linear backoff, gap detection after disconnects, and multi-subsystem recovery (message
reconciliation, streaming buffer promotion, HITL request catch-up).

---

## 2. Architecture

### Three Layers

```
┌─────────────────────────────────────────────────┐
│  useRoomWebhook                                 │
│  (gap detection, reconciliation, HITL catch-up) │
└─────────────────────┬───────────────────────────┘
                      │ onMessage (returns sseConnected)
┌─────────────────────▼───────────────────────────┐
│  useRoomSSE                                     │
│  (React lifecycle, connect/disconnect on deps)  │
└─────────────────────┬───────────────────────────┘
                      │ creates / manages
┌─────────────────────▼───────────────────────────┐
│  SSEConnection class                            │
│  (EventSource wrapper, auto-reconnect, backoff) │
└─────────────────────────────────────────────────┘
```

### Layer 1: SSEConnection (Transport)

A class in `lib/api/sse.ts` that wraps the native `EventSource`:

- **Connect**: Obtains auth token, builds URL `{API_BASE_URL}/room/{roomId}/stream?token=...` (where `API_BASE_URL = getApiUrl('sse')`), creates `EventSource`.
- **Auto-reconnect**: On `onerror`, if not manually closed and `reconnectAttempts < 5`, schedules a reconnect with linear backoff (`reconnectDelay * attempts` — 1s, 2s, 3s, 4s, 5s).
- **Disconnect**: Sets `isManualClose = true`, cancels pending reconnect timer, closes `EventSource`.
- **Heartbeat filtering**: Silently drops `heartbeat` events before forwarding to `onMessage`.

### Layer 2: useRoomSSE (React Lifecycle)

A hook in `hooks/useRoomSSE.ts` that manages the SSEConnection instance:

- **Auto connect/disconnect**: Effect depends on `[roomId, enabled]`. When either changes, disconnects existing connection and reconnects.
- **Cleanup on unmount**: Disconnects SSEConnection.
- **Stable callbacks via refs**: `onMessageRef`, `onConnectionChangeRef`, `getTokenRef` prevent connection recreation when callback identities change.
- **Exposes**: `connected`, `connecting`, `error` state for consumers.

### Layer 3: useRoomWebhook (Gap Recovery)

The central hook handles what happens when SSE reconnects after a gap:

#### Disconnect Detection

`sseHadDisconnectionRef` (a `useRef<boolean>`) is set to `true` when SSE disconnects
while `processing` is active. This flags that events may have been missed during the gap.

#### Streaming Buffer Promotion

When SSE drops during active streaming:
1. All active typewriters are finished immediately.
2. Partial streaming content in `StreamingBuffer` is promoted to entity content with `'optimistic'` source and `taskStatus: null`.
3. The streaming buffer is cleared.

This prevents partial text from being lost if the connection drops mid-stream.

#### DB Reconciliation

When `processing_status` reaches a terminal state (completed/canceled) and `sseHadDisconnectionRef`
is `true`, a `reconcileWithDb()` call fires after a 1500ms delay:

1. Re-fetches all room messages from the API.
2. Runs `detectAndMarkStaleTasks()` on the results.
3. Runs `filterHydrationMessages()` for deduplication.
4. Calls `upsertMany(filtered, 'db')` — the source-priority system ensures SSE-sourced
   entities are not overwritten by stale DB data (`sse > db > optimistic`).

#### HITL Request Catch-up

When `sseConnected` transitions from `false` to `true` (detected via `prevSseConnectedRef`):
1. Calls `fetchPendingHitlRequests(roomId, getToken)`.
2. If pending requests exist, upserts each into the message store with all HITL fields populated and `hitlResolved: false`.
3. This restores any HITL prompts that were delivered while SSE was disconnected.

---

## 3. Source Priority Conflict Resolution

The message store's upsert logic uses source-priority ordering to resolve conflicts when
the same message arrives from multiple sources:

```
sse > db > optimistic
```

- **SSE source**: Real-time events — highest priority, always accepted.
- **DB source**: API hydration/reconciliation — accepted unless an SSE version already exists for a non-terminal task.
- **Optimistic source**: Client-side predictions — lowest priority, overwritten by any server data.

This ensures that DB reconciliation after a gap never overwrites fresher SSE data that
arrived before or after the gap.

---

## 4. Code References

| Concept | File | Notes |
|---|---|---|
| SSEConnection class | `src/lib/api/sse.ts` (lines 19-150) | Transport layer: EventSource, 5-retry linear backoff, heartbeat filter |
| useRoomSSE hook | `src/hooks/useRoomSSE.ts` | React lifecycle: auto connect/disconnect, stable callback refs |
| Disconnect detection | `src/hooks/useRoomWebhook.ts` (line 89) | `sseHadDisconnectionRef` ref |
| Streaming promotion | `src/hooks/useRoomWebhook.ts` (lines 926-956) | Partial buffer promoted to entity on disconnect |
| DB reconciliation | `src/hooks/useRoomWebhook.ts` (lines 349-370) | `reconcileWithDb()` — refetch, stale detect, filter, upsert |
| Post-processing reconciliation | `src/hooks/useRoomWebhook.ts` (lines 635-641) | Triggers reconciliation after gap if `sseHadDisconnectionRef` is true |
| HITL catch-up | `src/hooks/useRoomWebhook.ts` (lines 958-997) | Fetches pending HITL requests on SSE reconnect |
| Source priority | `src/stores/message-store/upsert.ts` | `sse > db > optimistic` conflict resolution |

---

## 5. Known Limitations

- **No polling fallback**: If SSE fails all 5 reconnect attempts, there is no fallback to polling. The UI stays in "Processing..." until the user manually refreshes.
- **Token in URL**: SSE auth token is passed as a URL query parameter because `EventSource` does not support custom headers. Mitigated by Clerk's short-lived JWTs (60s).
- **Linear backoff only**: The reconnect strategy uses linear backoff (1s, 2s, 3s...) rather than exponential. For flaky networks, exponential backoff with jitter would reduce thundering herd effects.
- **Full message refetch on reconciliation**: `reconcileWithDb` fetches all room messages, not just those created after the disconnection. This is wasteful for rooms with long message histories and will become a bigger concern once pagination is implemented.
- **1500ms reconciliation delay is arbitrary**: The delay before reconciliation is a heuristic to avoid racing with late SSE events. It may be too short for slow backends or too long for eager users.
