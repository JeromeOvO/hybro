# Task Retry Design — Retry UI for Failed Tasks

> **Status: Not Started** — Design approved, pending implementation.

**Depends on**: None (backend APIs exist for re-sending messages)
**Decoupled from**: All other frontend design docs **except** `MULTIMODAL_SUPPORT_DESIGN.md` Phase 3 — the `retryMessage` snippet forwards `attachments` to `sendUserMessage`, which requires the 4-arg signature introduced by Phase 3. **If implemented before Phase 3**, remove the `attachments` parameter from the `sendUserMessage` calls in `retryMessage` (§4.6); the retry will be text-only but fully functional. Re-add the parameter when Phase 3 lands.

---

## 1. Problem Statement

When an A2A task fails, is rejected, or hits a rate limit, the frontend displays a red
error card (`task-status-message.tsx`) with the error message. There is no "Retry" button.
The user's only recovery option is to type and send a new message manually, which:

- Requires the user to remember or re-type the original message.
- Does not indicate the relationship between the retry and the original failure.
- For rate-limited errors, the user has no guidance on when to retry.

The backend provides `retry_after_seconds` hints in task status updates and rate-limit
error SSE events. These hints are received by the frontend but not surfaced in the UI.

---

## 2. Current State

### Task Status Card (`src/components/task-status-message.tsx`)

Failed/rejected/canceled states render a red card with `XCircle` icon and error text.
No interactive elements (no retry button, no countdown).

```typescript
// Line 292-335: isFailureState(status) branch
// Renders: agent avatar, error/content text, collapsible toggle
// Does NOT render: retry button, countdown timer
```

### SSE Error Events with Rate Limit Info (`src/lib/types/sse.ts`)

The SSE data includes rate-limit fields:

```typescript
retry_after_seconds?: number
user_requests_used?: number
user_requests_limit?: number
```

These fields are present in the SSE type but are not used by any component.

### Message Entity (`src/stores/message-store/types.ts`)

No `retryAfterSeconds` or `originalMessageText` fields on `MessageEntity`.

### Backend Retry Semantics

The backend has `retryMetaTask` (Orchestration API), but this is part of the legacy
workflow system and not used by the room message flow. The room message flow uses
`sendMessage`, which creates a new message and auto-triggers background processing.

For room-based retry, the correct approach is to re-send the original user message via
`POST /roomCenter/sendMessage`. This creates a fresh message-processing cycle with no
stale state.

---

## 3. Proposed Design

### 3.1 Overview

Add retry capabilities to failed task cards and rate-limited error messages:

1. **Retry button** on failed/rejected task cards.
2. **Countdown timer** on rate-limited errors with automatic retry enablement.
3. **Retry action** re-sends the original user message via `SendMessage`.

### 3.2 Retry Flow

```
┌─────────────────────────────────────────────┐
│ Failed Task Card                            │
│                                             │
│ ✕ Research Agent                            │
│   Task failed: Connection timed out         │
│                                             │
│   [↻ Retry]                                │
└──────────────────────┬──────────────────────┘
                       │
                User clicks "Retry"
                       │
                       ▼
┌─────────────────────────────────────────────┐
│ 1. Look up the related user message         │
│    (via relatedMessageId on the entity)     │
│ 2. Extract original message text            │
│ 3. Call SendMessage with same text +        │
│    same room + same target group            │
│    (fallback: "all_agents" if original      │
│     target_group not recoverable — see §4.6)│
│ 4. Old failed card stays (as history)       │
│ 5. New processing begins (new SSE events)   │
└─────────────────────────────────────────────┘
```

### 3.3 Rate-Limit Flow

```
┌─────────────────────────────────────────────┐
│ Rate Limited Error Card                     │
│                                             │
│ ⚠ Research Agent                            │
│   Rate limit exceeded                       │
│   5/10 user requests used                   │
│                                             │
│   Retry available in 45s  [↻ Retry (45)]   │
└──────────────────────┬──────────────────────┘
                       │
                Timer counts down
                       │
                       ▼
┌─────────────────────────────────────────────┐
│ Rate Limited Error Card                     │
│                                             │
│ ⚠ Research Agent                            │
│   Rate limit exceeded                       │
│                                             │
│   [↻ Retry]  (now enabled)                 │
└─────────────────────────────────────────────┘
```

---

## 4. Files to Modify

### 4.1 `src/stores/message-store/types.ts` — Add retry fields

Add to `MessageEntity`:

```typescript
// ── Retry ────────────────────────────────────────────────────
retryAfterSeconds?: number
relatedUserMessageId?: string
// ── Target group (for retry routing) ─────────────────────────
targetGroup?: string
```

Add corresponding optional fields to `IncomingMessage`.

`retryAfterSeconds` is populated from:
- `task_update` SSE events (via the backend's rate-limit hint).
- `error` SSE events with `error_type: "rate_limit_exceeded"`.

`relatedUserMessageId` links the agent/task message back to the user message that
triggered it. This is already available via the `related_message_id` field on SSE
events and DB messages — it just needs to be stored on the entity.

`targetGroup` stores the routing group used for the original message (e.g.,
`"all_agents"`, `"room_team"`, or a custom group ID). This is set on user messages
from the `sendUserMessage` call site and must be preserved so that retry can re-send
to the same target group. Populated from:
- The `target_group` field on the `sendUserMessage` optimistic upsert.
- The `target_group` field on SSE user message events (if available).
- Defaults to `"all_agents"` if not present (matching `SendMessage` API default).

### 4.1a `src/stores/message-store/upsert.ts` — Register retry fields in merge + no-op

**Critical**: The current `mergeIncoming` and `isNoOpUpdate` functions enumerate fields
explicitly. New fields that are not added will be silently dropped on update.

**`mergeIncoming`** — add to both the `!existing` branch and the existing-entity branch:

```typescript
// ── Retry ──
retryAfterSeconds: incoming.retryAfterSeconds,
relatedUserMessageId: incoming.relatedUserMessageId,
targetGroup: incoming.targetGroup,

// (existing-entity branch uses coalesce pattern):
retryAfterSeconds: incoming.retryAfterSeconds !== undefined ? incoming.retryAfterSeconds : existing.retryAfterSeconds,
relatedUserMessageId: incoming.relatedUserMessageId !== undefined ? incoming.relatedUserMessageId : existing.relatedUserMessageId,
targetGroup: incoming.targetGroup !== undefined ? incoming.targetGroup : existing.targetGroup,
```

**`isNoOpUpdate`** — add this comparison:

```typescript
existing.retryAfterSeconds === coalesce(incoming.retryAfterSeconds, existing.retryAfterSeconds) &&
```

Only `retryAfterSeconds` needs no-op checking because it's the only retry field that
changes on update (a rate-limit error can arrive with a different `retryAfterSeconds`
than the initial failure). `relatedUserMessageId` is set once at creation and never
updated, so it doesn't need a no-op check.

### 4.2 `src/hooks/useRoomWebhook.ts` — Populate retry fields

In the `task_update` and `error` SSE handlers, extract and store retry-related data:

```typescript
case 'task_update': {
  // ... existing logic ...
  store.upsertMessage({
    // ... existing fields ...
    retryAfterSeconds: msg.data.retry_after_seconds,
  }, 'sse')
  break
}

case 'error': {
  if (msg.data.error_type === 'rate_limit_exceeded') {
    store.upsertMessage({
      // ... existing fields ...
      retryAfterSeconds: msg.data.retry_after_seconds,
    }, 'sse')
  }
  break
}
```

In `convertApiMessageToIncoming` (`src/stores/message-store/convert-api-message.ts`),
map `related_message_id` to `relatedUserMessageId` for agent messages:

```typescript
relatedUserMessageId: apiMessage.related_message_id ?? undefined,
```

**`targetGroup` cannot be populated from DB**: `RoomMessage` does not carry
`target_group`. This means DB-hydrated user messages will have
`targetGroup: undefined`. Since `mergeIncoming` uses the coalesce pattern
(`undefined` → preserve existing), the optimistic value survives DB
reconciliation **as long as the entity already exists in the store**. However,
on a full re-hydration (cold load, page refresh), `targetGroup` is lost.

This is a known gap. See the Known Limitation entry in §6 Key Decisions.
To fully close it, the backend `RoomMessage` response needs a `target_group` field.

### 4.3 `src/components/task-status-message.tsx` — Add retry button

**New props**:

```typescript
interface TaskStatusMessageProps {
  // ... existing props ...
  retryAfterSeconds?: number
  onRetry?: () => void
}
```

**In the failed/rejected branch** (line ~292-335), add a retry button:

```tsx
{onRetry && (
  <div className="mt-3 pt-2 border-t border-red-200 dark:border-red-500/20">
    <RetryButton
      retryAfterSeconds={retryAfterSeconds}
      onRetry={onRetry}
      colorScheme="red"
    />
  </div>
)}
```

**In the rate-limited error rendering** (if treated as a failure state), add the
countdown + retry button.

### 4.4 New component: `src/components/retry-button.tsx`

```typescript
interface RetryButtonProps {
  retryAfterSeconds?: number
  onRetry: () => void
  colorScheme?: 'red' | 'amber'
}

export function RetryButton({ retryAfterSeconds, onRetry, colorScheme = 'red' }: RetryButtonProps) {
  const { secondsRemaining, canRetry } = useRetryCountdown(retryAfterSeconds)

  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={onRetry}
      disabled={!canRetry}
      className={/* color scheme classes */}
    >
      <RotateCcw className="w-3.5 h-3.5 mr-1.5" />
      {canRetry
        ? 'Retry'
        : `Retry (${secondsRemaining}s)`
      }
    </Button>
  )
}
```

### 4.5 New hook: `src/hooks/useRetryCountdown.ts`

```typescript
export function useRetryCountdown(initialSeconds?: number): {
  secondsRemaining: number
  canRetry: boolean
} {
  const [secondsRemaining, setSecondsRemaining] = useState(initialSeconds ?? 0)

  useEffect(() => {
    if (!initialSeconds || initialSeconds <= 0) return

    setSecondsRemaining(initialSeconds)
    const interval = setInterval(() => {
      setSecondsRemaining(prev => {
        if (prev <= 1) {
          clearInterval(interval)
          return 0
        }
        return prev - 1
      })
    }, 1000)

    return () => clearInterval(interval)
  }, [initialSeconds])

  return {
    secondsRemaining,
    canRetry: secondsRemaining <= 0,
  }
}
```

### 4.6 `src/hooks/useRoomWebhook.ts` — Retry handler

Add a `retryMessage` function that the room page passes to task status cards:

```typescript
const retryMessage = async (failedEntityId: string) => {
  const entity = store.entities[failedEntityId]
  if (!entity?.relatedUserMessageId) {
    banner.error('Cannot retry — original message not found')
    return
  }

  // Find the original user message in the store
  const userEntity = store.entities[entity.relatedUserMessageId]
  if (userEntity?.content) {
    // Happy path: message is in the store.
    // targetGroup may be undefined if the entity was DB-hydrated (RoomMessage
    // does not carry target_group). sendUserMessage defaults to "all_agents".
    // attachments may also be undefined for DB-hydrated entities.
    //
    // NOTE: The `attachments` parameter requires the 4-arg sendUserMessage
    // signature from MULTIMODAL_SUPPORT_DESIGN.md Phase 3. If implementing
    // this doc before Phase 3, omit the 4th argument (text-only retry).
    await sendUserMessage(
      userEntity.content,
      userEntity.targetGroup,
      undefined, // no quote on retry
      userEntity.attachments, // Phase 3 only — remove if Phase 3 not yet landed
    )
    return
  }

  // Pagination edge case: the original user message may have been evicted
  // from the store (e.g., it's on an earlier page that hasn't been loaded).
  // Fall back to the existing room messages API to find it.
  try {
    const resp = await inquiryRoomMessagesByRoomId(entity.roomId, getToken)
    const originalMsg = resp?.message_list?.find(
      m => m.message_id === entity.relatedUserMessageId
    )
    const originalContent = originalMsg?.message_content?.message_text
    if (!originalContent) {
      banner.error('Cannot retry — original message content not available')
      return
    }
    // target_group is not persisted on RoomMessage; default to "all_agents".
    // attachments are also not persisted on RoomMessage; retry is text-only.
    // This is a KNOWN LIMITATION: retry via fallback may change routing for
    // messages originally sent to a specific agent group, and will drop any
    // file attachments from the original message. Acceptable because:
    // 1. The fallback is rare (pagination evicted the original message).
    // 2. "all_agents" is the most common target group.
    // 3. If the backend adds target_group + attachments to RoomMessage, this
    //    can be fixed.
    await sendUserMessage(originalContent)
  } catch {
    banner.error('Cannot retry — failed to fetch original message')
  }
}
```

**API contract note**: The fallback uses the existing `inquiryRoomMessagesByRoomId(room_id)`
which returns `RoomCenterRoomMessageResponse.message_list: RoomMessage[]`. It does NOT
use a hypothetical `messageIds` filter — the current backend does not support single-
message queries. The full message list is fetched and searched client-side. This is
acceptable because:

1. The fallback is rare (only when pagination evicts the original message).
2. The room already loaded these messages recently (high probability of HTTP cache hit).
3. If `MESSAGE_PAGINATION_DESIGN.md` is implemented first, the paginated API can be
   used instead (fetch the page containing the target message by timestamp cursor).

Expose `retryMessage` from the hook. The room page passes it as `onRetry` prop to
message components.

**Target group routing**: Both retry paths call `sendUserMessage(content, targetGroup)`.
`targetGroup` is only reliably present when the original user message entity retains
its optimistic write (i.e., the user sent the message in the current session and no
full re-hydration has replaced it). In all other cases — DB-hydrated entities, fallback
fetch from API — `targetGroup` is `undefined` and `sendUserMessage` defaults to
`"all_agents"`. This is documented as a known limitation in §6 Key Decisions. The
permanent fix requires the backend to include `target_group` on `RoomMessage`.

### 4.7 `src/components/room-messages.tsx` — Pass retry handler

When rendering a `task-status` display type, pass the retry handler:

```tsx
<TaskStatusMessage
  {...taskProps}
  onRetry={() => retryMessage(entity.id)}
/>
```

---

## 5. State Management Changes

### 5.1 Message Store

Three new optional fields on `MessageEntity` and `IncomingMessage`:
- `retryAfterSeconds: number`
- `relatedUserMessageId: string`
- `targetGroup: string` — stores the routing group for retry preservation

No new stores.

### 5.2 Room UI Store

No changes. The retry countdown is local to the `RetryButton` component via
`useRetryCountdown`.

---

## 6. Key Decisions

| Decision | Rationale |
|---|---|
| Retry re-sends via `SendMessage` (not `retryMetaTask`) | `retryMetaTask` is part of the legacy workflow system. `SendMessage` is the standard room message flow and creates a clean processing cycle. |
| Old failed card stays as history | Users can see the failure context. The new message starts fresh. |
| Countdown timer for rate limits | Prevents users from repeatedly hitting the rate limit. Shows exactly when retry is available. |
| Retry button only on failed/rejected (not canceled) | Canceled tasks were intentionally stopped by the user. Auto-showing retry would be confusing. |
| `relatedUserMessageId` links to original | Enables finding the original message text without requiring the retry button to carry the text as a prop. |
| `targetGroup` stored on user messages | Enables retry to re-send to the same target group. Without this, retry would default to `"all_agents"`, silently changing routing for messages sent to specific agent groups. |
| **Known limitation**: retry may default to `"all_agents"` and drop attachments | `RoomMessage` (the backend DB response) does not carry `target_group` or `attachments`. This affects **both** retry paths: (1) happy path — if the original optimistic user message has been replaced by a DB-hydrated entity (page refresh, SSE reconnect with full re-hydration), `targetGroup` and `attachments` are `undefined` and `sendUserMessage` defaults to `"all_agents"` with text-only content; (2) fallback path — the fetched `RoomMessage` also lacks both fields. Mitigations: (a) `mergeIncoming` coalesce preserves the optimistic `targetGroup` and `attachments` on incremental reconciliation (the common case), (b) `"all_agents"` is the most common group, (c) text-only retry is still useful even without attachments, (d) fully fixable when backend adds `target_group` and `attachments` to `RoomMessage`. |
| Rate-limit info fields (used/limit) shown in card | Gives users context about why they were rate-limited and how close they are to the limit. |

---

## 7. Error Handling

| Scenario | Behavior |
|---|---|
| `onRetry` fails (SendMessage API error) | Show error toast. Retry button remains enabled for another attempt. |
| Original user message not in store (e.g., was in an older, unpaginated page) | Show "Cannot retry — original message not found" toast. Disable retry button. |
| `retry_after_seconds` is 0 or negative | Retry button is immediately enabled (no countdown). |
| `retry_after_seconds` not provided | Retry button is immediately enabled (no countdown). |
| User clicks retry while already sending/processing | Guard with `sending` flag from room UI store. Disable retry button while sending. |

---

## 8. Out of Scope

- Backend retry-task endpoint for the room message flow (currently only `retryMetaTask`
  for legacy workflows). If the backend adds a dedicated room-message retry endpoint in
  the future, the frontend can switch from re-sending to calling that endpoint.
- Automatic retry (retry without user interaction). The user must explicitly click.
- Retry count tracking (limiting retries to N attempts). The backend's rate limiter
  naturally handles abuse.
- Retry for `auth-required` state (requires an OAuth flow, separate design).
- Per-agent retry (retrying just one agent in a multi-agent dispatch).

---

## 9. Testing Strategy

- Unit test `useRetryCountdown`: verify countdown from N to 0, `canRetry` transitions
  from `false` to `true`.
- Unit test `RetryButton`: renders disabled with countdown, enables when countdown
  reaches 0.
- Unit test `TaskStatusMessage` with `onRetry` prop: retry button renders in failed
  state, does not render in working state.
- Unit test `retryMessage`: finds original user message, calls `sendUserMessage` with
  correct text.
- Unit test `retryMessage` fallback: original message not in store (pagination),
  mock `inquiryRoomMessagesByRoomId` returns the message, calls `sendUserMessage`.
- Edge case: `retryMessage` called when original message not in store AND backend
  fetch also fails — shows error banner.
- Edge case: `retryMessage` called when `relatedUserMessageId` is missing.
- Edge case: rate-limited error with 0 `retry_after_seconds`.
- Integration test: trigger a failed task via mock SSE, click retry, verify new
  message is sent.
