# Processing Cancellation — Cancel In-Flight Tasks

> **Status: Implemented**

---

## 1. Overview

When agents are processing a user message, a "Stop" button replaces the "Send" button in
the chat input. Clicking it sends a cancellation request to the backend, batch-cancels all
non-terminal tasks in the message store, and starts a 15-second timeout safety net. If the
backend confirms cancellation via SSE, the UI transitions cleanly. If not, the timeout
auto-resets the UI state.

---

## 2. User Flow

1. User sends a message; the input enters "Processing" state (spinner + "Stop" button).
2. User clicks the Stop button.
3. The button transitions to "Cancelling..." state (disabled, destructive spinner).
4. The backend receives a cancel request and attempts to abort the running task.
5. **Success path**: SSE delivers a `task_update` with `canceled` status or `processing_status: CANCELED`. The UI resets to idle.
6. **Timeout path**: If no SSE confirmation within 15 seconds, the safety net fires — clears cancelling state, clears processing, and shows a warning banner: "Cancellation timed out — the agent may still be running."

---

## 3. Architecture

### State Machine

```
 Idle ──[send]──> Sending ──[sse: task_submitted]──> Processing
                                                        │
                                                   [click Stop]
                                                        │
                                                        ▼
                                                   Cancelling
                                                    │       │
                                       [sse: canceled]   [15s timeout]
                                                    │       │
                                                    ▼       ▼
                                                   Idle   Idle + warning
```

Four mutually exclusive UI states in the chat input button area:
- **Sending**: Disabled spinner with ping animation.
- **Processing**: Active Stop button (Square icon).
- **Cancelling**: Disabled destructive spinner, "Cancelling..." text.
- **Idle**: Send button (enabled when input is non-empty).

### Cancel Flow

`cancelProcessing()` in `useRoomWebhook.ts`:

1. Reads `currentProcessingMessageId.current` — the message ID of the active task.
2. If no active task, shows a warning banner and returns.
3. Sets `cancelling: true` in `useRoomUiStore`.
4. Calls `cancelMessage(messageId, getToken)` — POST to `{API_BASE_URL}/message/{messageId}/cancel` (where `API_BASE_URL = getApiUrl('sse')`).
5. Calls `cancelAllNonTerminal(roomId)` on the message store — batch-sets all non-terminal tasks in the room to `canceled` status.
6. Starts a 15-second `setTimeout` — if `cancelling` is still true when it fires, resets the UI and shows a timeout warning.

### Batch Cancellation

`cancelAllNonTerminal(roomId)` in the message store iterates all entities for the room.
For each entity with a non-terminal `taskStatus` that isn't ephemeral:
- Sets `taskStatus: 'canceled'`.
- Recomputes `displayType` via `resolveDisplayType()`.
- Increments `sourceVersion` and `updatedAt`.

This provides immediate visual feedback before the SSE confirmation arrives.

### SSE Confirmation

When `processing_status: CANCELED` arrives via SSE, `useRoomWebhook` clears the cancelling
state, clears the timeout, and resets processing flags. The task status cards already show
"canceled" from the batch update.

---

## 4. Code References

| Concept | File | Notes |
|---|---|---|
| Cancel button states | `src/components/room-chat-input.tsx` (lines 815-876) | 4-state conditional rendering |
| `cancelProcessing()` | `src/hooks/useRoomWebhook.ts` (lines 1211-1245) | API call + batch cancel + timeout safety net |
| `cancelMessage()` API | `src/lib/api/sse.ts` (lines 168-185) | POST `{API_BASE_URL}/message/{messageId}/cancel` |
| `cancelAllNonTerminal()` | `src/stores/message-store/index.ts` (lines 112-142) | Batch-cancel non-terminal tasks in a room |
| Processing state store | `src/stores/room-ui-store.ts` | `sending`, `processing`, `cancelling` flags |
| SSE cancellation handler | `src/hooks/useRoomWebhook.ts` | `processing_status: CANCELED` case clears all state |

---

## 5. Known Limitations

- **No per-task cancellation**: The cancel button cancels all processing for the room, not a specific agent's task. There is no UI for cancelling a single agent while letting others continue.
- **Timeout warning is imprecise**: The 15-second timeout does not mean cancellation failed — the backend may still cancel the task after the timeout. The warning is a UX guard, not a status assertion.
- **State is global, not room-scoped**: `cancelling` is stored in `useRoomUiStore` as a global flag. If the user navigates to a different room before the timeout fires, the timeout still runs against the old room's context. This is safe today (single-room-per-tab) but would be a bug with multi-room views.
- **No retry after cancel**: After cancellation, the original message is not automatically re-sent. Users must manually re-type or use the chat history.
