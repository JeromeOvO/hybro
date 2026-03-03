# HITL Frontend Design — Human-in-the-Loop Inline Reply

> **Status: Implemented** | All planned features shipped.

**Depends on**: Backend HITL implementation (`hybro-multi-agents-backend/docs/HITL_DESIGN.md` Phases 1-8)
**Decoupled from**: All other frontend design docs (Artifact, Token Streaming, Supervisor Toggle, Pagination, Retry, Dead Code)

---

## 1. Problem Statement

When Supervisor V2 issues a `CLARIFY` action or an A2A agent returns `input_required`,
the backend pauses the processing loop and waits for user input. The frontend currently
shows these states as display-only amber cards (`task-status-message.tsx`) with no
interactive reply mechanism. Users must send a new chat message, which the backend cannot
correlate to the pending HITL request.

Once the backend HITL system is implemented, the frontend needs:

1. Handling for two new SSE event types (`hitl_input_requested`, `hitl_status_update`).
2. A collapsible HITL panel rendered in `RoomChatInput`'s `topSlot`, with text/choice/confirmation variants.
3. An API client for `POST /api/v1/rooms/:roomId/hitl/respond`.
4. Reconnect catch-up logic to restore missed HITL requests.
5. Expiry handling for timed-out requests.

---

## 2. Current State (Pre-Implementation)

> **Note**: This section describes the codebase state *before* HITL was implemented.
> It is retained for historical context. For the current implementation, see Sections 5 and 11.

### SSE Type Union (`src/lib/types/sse.ts`)

The `SSEMessage.type` union includes 8 values. Neither `hitl_input_requested` nor
`hitl_status_update` is present:

```typescript
type: 'connected' | 'user_message' | 'agent_response' | 'processing_status'
    | 'heartbeat' | 'error' | 'task_submitted' | 'task_update'
```

### Task Status Card (`src/components/task-status-message.tsx`)

Renders 6 visual states. `input-required` and `auth-required` show amber cards with
`AlertTriangle` / `KeyRound` icons and a status message, but no input field or submit
button. The component receives data via props from the message store entity.

### Message Entity (`src/stores/message-store/types.ts`)

`MessageEntity` has task-related fields (`taskRequiresInput`, `taskRequiresAuth`,
`taskStatusMessage`) but no HITL-specific fields (no `hitlRequestId`, `hitlPrompt`,
`hitlPromptType`, etc.).

### Room Webhook Hook (`src/hooks/useRoomWebhook.ts`)

`handleSSEMessage` switch has cases for all 8 current event types. No HITL handling
exists.

---

## 3. Backend Contract (from `HITL_DESIGN.md`)

### 3.1 SSE Events

**`hitl_input_requested`** — emitted when HITL input is needed:

```json
{
  "type": "hitl_input_requested",
  "room_id": "room_123",
  "timestamp": "2026-02-12T00:00:00Z",
  "data": {
    "request_id": "abc123",
    "message_id": "msg_456",
    "source": "agent",
    "agent_id": "agent_42",
    "agent_name": "Research Agent",
    "prompt": "Which date range should I search?",
    "prompt_type": "text",
    "choices": null,
    "step_number": 2,
    "total_steps": 5
  }
}
```

**`hitl_status_update`** — emitted when a HITL request is resolved/canceled/expired:

```json
{
  "type": "hitl_status_update",
  "room_id": "room_123",
  "timestamp": "2026-02-12T00:05:00Z",
  "data": {
    "request_id": "abc123",
    "status": "responded",
    "error_message": null
  }
}
```

### 3.2 REST Endpoints

**Submit reply**: `POST /api/v1/rooms/:roomId/hitl/respond`

```json
// Request
{ "request_id": "abc123", "user_input": "2024-2026" }

// Response
{ "status": "ok", "request_id": "abc123" }
```

**Pending requests** (for reconnect catch-up): `GET /api/v1/rooms/:roomId/hitl/pending`

```json
// Response
{
  "requests": [
    {
      "request_id": "abc123",
      "message_id": "msg_456",
      "source": "agent",
      "agent_id": "agent_42",
      "agent_name": "Research Agent",
      "prompt": "Which date range should I search?",
      "prompt_type": "text",
      "choices": null,
      "status": "pending",
      "expires_at": "2026-02-13T00:00:00Z",
      "created_at": "2026-02-12T00:00:00Z"
    }
  ]
}
```

### 3.3 Backend Data Models

```
HITLPromptType: "text" | "choice" | "confirmation"
HITLStatus:     "pending" | "responded" | "expired" | "canceled" | "error"
```

---

## 4. Proposed Design

### 4.1 Architecture Overview

```
SSE stream
    │
    ├── hitl_input_requested ──┐
    │                          ▼
    │                 useRoomWebhook.handleSSEMessage
    │                          │
    │                 upsertMessage() with HITL fields
    │                          │
    │                 MessageEntity (message store)
    │                          │
    │                 RoomChatInput topSlot → HitlPanel
    │                          │
    │                 HitlQuestionForm
    │                          │
    │                 respondToHitl() API call
    │                          │
    ├── hitl_status_update ────┤
    │                          ▼
    │                 upsertMessage() clears HITL fields
    │                 (or marks expired/canceled)
    │
    └── SSE reconnect ─────── fetchPendingHitlRequests()
                               │
                               ▼
                       Restore HITL form state
```

### 4.2 Data Flow

1. Backend emits `hitl_input_requested` with `request_id`, `prompt`, `prompt_type`, and
   optional `choices`.
2. `handleSSEMessage` creates/updates a `MessageEntity` with HITL fields, setting
   `displayType` to `task-status`.
3. The `HitlPanel` (rendered in `RoomChatInput` topSlot) detects unresolved HITL requests
   and renders a `HitlQuestionForm` (text input, choice buttons, or confirmation buttons
   depending on `prompt_type`). The `task-status-message` bubble is hidden while HITL is active.
4. User submits reply. The form calls `respondToHitl(roomId, requestId, userInput)`.
5. On success, locally mark `hitlResolved: true` (optimistic). Backend emits
   `hitl_status_update` with `status: "responded"` to confirm.
6. If the request expires before the user replies, backend emits `hitl_status_update`
   with `status: "expired"`. The form is removed and an expiry message is shown.

---

## 5. Files to Modify

### 5.1 `src/lib/types/sse.ts` — Add HITL event types

Add to `SSEMessage.type` union:

```typescript
type: '...' | 'hitl_input_requested' | 'hitl_status_update'
```

Add to `SSEMessage.data`:

```typescript
// HITL fields (for hitl_input_requested)
request_id?: string
source?: 'agent' | 'supervisor'
prompt?: string
prompt_type?: 'text' | 'choice' | 'confirmation'
choices?: string[] | null
// HITL status update fields
// status already exists (reused), error_message maps to existing error field
```

### 5.2 `src/stores/message-store/types.ts` — Extend MessageEntity

Add new fields to `MessageEntity`:

```typescript
// ── HITL (Human-in-the-Loop) ─────────────────────────────────
hitlRequestId?: string
hitlPrompt?: string
hitlPromptType?: 'text' | 'choice' | 'confirmation'
hitlChoices?: string[] | null
hitlExpiresAt?: string
hitlResolved?: boolean
```

Add corresponding optional fields to `IncomingMessage`.

### 5.2a `src/stores/message-store/upsert.ts` — Register HITL fields in merge + no-op

**Critical**: The current `mergeIncoming` and `isNoOpUpdate` functions enumerate fields
explicitly. New fields that are not added to both functions will be silently dropped
on update (merge won't propagate them) or will trigger infinite re-renders (no-op check
won't recognize them as unchanged).

**`mergeIncoming`** — add to both the `!existing` branch and the existing-entity branch:

```typescript
// ── HITL ──
hitlRequestId: incoming.hitlRequestId,
hitlPrompt: incoming.hitlPrompt,
hitlPromptType: incoming.hitlPromptType,
hitlChoices: incoming.hitlChoices,
hitlExpiresAt: incoming.hitlExpiresAt,
hitlResolved: incoming.hitlResolved,

// (existing-entity branch uses coalesce pattern):
hitlRequestId: incoming.hitlRequestId !== undefined ? incoming.hitlRequestId : existing.hitlRequestId,
hitlPrompt: incoming.hitlPrompt !== undefined ? incoming.hitlPrompt : existing.hitlPrompt,
hitlPromptType: incoming.hitlPromptType !== undefined ? incoming.hitlPromptType : existing.hitlPromptType,
hitlChoices: incoming.hitlChoices !== undefined ? incoming.hitlChoices : existing.hitlChoices,
hitlExpiresAt: incoming.hitlExpiresAt !== undefined ? incoming.hitlExpiresAt : existing.hitlExpiresAt,
hitlResolved: incoming.hitlResolved !== undefined ? incoming.hitlResolved : existing.hitlResolved,
```

**`isNoOpUpdate`** — the implementation checks all 10 HITL fields for no-op detection:

```typescript
existing.hitlResolved    === coalesce(incoming.hitlResolved, existing.hitlResolved) &&
existing.hitlPrompt      === coalesce(incoming.hitlPrompt, existing.hitlPrompt) &&
existing.hitlRequestId   === coalesce(incoming.hitlRequestId, existing.hitlRequestId) &&
existing.hitlPromptType  === coalesce(incoming.hitlPromptType, existing.hitlPromptType) &&
existing.hitlExpiresAt   === coalesce(incoming.hitlExpiresAt, existing.hitlExpiresAt) &&
arraysShallowEqual(existing.hitlChoices, coalesce(incoming.hitlChoices, existing.hitlChoices)) &&
existing.hitlGroupId     === coalesce(incoming.hitlGroupId, existing.hitlGroupId) &&
existing.hitlGroupTotal  === coalesce(incoming.hitlGroupTotal, existing.hitlGroupTotal) &&
existing.hitlGroupIndex  === coalesce(incoming.hitlGroupIndex, existing.hitlGroupIndex) &&
existing.hitlUserAnswer  === coalesce(incoming.hitlUserAnswer, existing.hitlUserAnswer) &&
```

All 10 HITL fields participate in no-op detection. `hitlChoices` uses `arraysShallowEqual`
since it's an array type.

HITL messages arrive via `task_submitted` / `task_update` (which set `taskStatus` to
`input-required`), so they already resolve to `task-status`. The existing logic is
sufficient.

### 5.4 `src/hooks/useRoomWebhook.ts` — Add HITL SSE handlers

Add two new cases to `handleSSEMessage`:

**`hitl_input_requested`**: Find the existing message entity by `data.message_id`. Upsert
with HITL fields:

```typescript
case 'hitl_input_requested': {
  const { request_id, message_id, prompt, prompt_type, choices,
          agent_name, agent_id, step_number, total_steps } = msg.data
  store.upsertMessage({
    id: message_id,
    roomId,
    messageType: 'agent',
    content: prompt,
    senderName: agent_name || 'Agent',
    timestamp: msg.timestamp,
    agentId: agent_id,
    taskStatus: 'input-required',
    hitlRequestId: request_id,
    hitlPrompt: prompt,
    hitlPromptType: prompt_type || 'text',
    hitlChoices: choices,
    hitlResolved: false,
    stepNumber: step_number,
    totalSteps: total_steps,
  }, 'sse')
  // Maintain O(1) index for hitl_status_update lookups
  hitlRequestIndex.set(request_id, message_id)
  break
}
```

**`hitl_status_update`**: Find entity by `request_id`, update HITL resolved state:

```typescript
case 'hitl_status_update': {
  const { request_id, status: hitlStatus, error_message } = msg.data
  // Look up entity by request_id using the index map (O(1)).
  // The index is maintained by the hitl_input_requested handler.
  const entityId = hitlRequestIndex.get(request_id)
  const entity = entityId ? store.entities[entityId] : undefined
  if (entity) {
    // Map HITL status to task state:
    //   'responded' → keep current taskStatus (task continues processing)
    //   'expired'   → 'failed' (request timed out)
    //   'canceled'  → 'canceled' (user or system canceled)
    let resolvedTaskStatus = entity.taskStatus
    let resolvedTaskError = null as string | null
    if (hitlStatus === 'expired') {
      resolvedTaskStatus = 'failed'
      resolvedTaskError = error_message || 'Request expired'
    } else if (hitlStatus === 'canceled') {
      resolvedTaskStatus = 'canceled'
      resolvedTaskError = error_message || 'Request canceled'
    }
    // 'responded' → taskStatus stays as-is (the agent will send a
    // subsequent task_update to advance the state)

    store.upsertMessage({
      id: entity.id,
      roomId,
      messageType: 'agent',
      content: error_message || entity.content,
      senderName: entity.senderName,
      timestamp: msg.timestamp,
      hitlResolved: true,
      taskStatus: resolvedTaskStatus,
      taskError: resolvedTaskError,
    }, 'sse')
    hitlRequestIndex.delete(request_id)
  }
  break
}
```

**Performance note**: A naive `Object.values(store.entities).find(...)` would be O(n)
in the number of messages. Since there are at most 1-2 active HITL requests per room,
a lightweight `Map<requestId, entityId>` index (`hitlRequestIndex`) is maintained by
the `hitl_input_requested` handler and cleaned up by `hitl_status_update`. This gives
O(1) lookup per the `js-set-map-lookups` best practice.

**SSE reconnect catch-up**: In the reconnect handler (after `hydrateFromDb` or
`reconcileWithDb`), call `fetchPendingHitlRequests(roomId)` and upsert any pending
requests into the message store with their HITL fields. This restores the inline reply
form if the user refreshed the page while a HITL request was pending.

### 5.5 New file: `src/lib/api/hitl.ts` — HITL API client

```typescript
import { apiPost, apiGet } from '../api-client'
import { getApiUrl } from '../utils'

function hitlUrl(roomId: string): string {
  return getApiUrl(`rooms/${roomId}/hitl`)
}

export interface HitlRespondResponse {
  status: string
  request_id: string
}

export interface HitlPendingResponse {
  requests: HitlPendingRequest[]
}

const HITL_RESPOND_TIMEOUT_MS = 180_000

export async function respondToHitl(
  roomId: string,
  requestId: string,
  userInput: string,
  getToken?: () => Promise<string | null>,
): Promise<HitlRespondResponse> {
  return apiPost<HitlRespondResponse>(`${hitlUrl(roomId)}/respond`, {
    request_id: requestId,
    user_input: userInput,
  }, getToken, undefined, HITL_RESPOND_TIMEOUT_MS)
}

export async function fetchPendingHitlRequests(
  roomId: string,
  getToken?: () => Promise<string | null>,
): Promise<HitlPendingResponse> {
  return apiGet<HitlPendingResponse>(`${hitlUrl(roomId)}/pending`, getToken)
}
```

The `hitlUrl(roomId)` helper generates a room-scoped base URL. Both functions take
`roomId` as the first argument — the URL scheme is `rooms/{roomId}/hitl/respond` and
`rooms/{roomId}/hitl/pending`. The respond call uses a 180-second timeout (vs 60s
default) because the backend supervisor resume can take 60-120s.

The `HitlPendingRequest` interface mirrors the backend's HITL request schema, including
group fields (`group_id`, `group_total`, `group_index`) for multi-question HITL sessions.

Add to `src/lib/api/index.ts` barrel export.

### 5.6 `src/components/hitl-inline-reply-form.tsx` — HitlPanel & HitlQuestionForm

The `HitlPanel` component renders in `RoomChatInput`'s `topSlot`. When unresolved HITL
requests exist (from `useActiveHitlRequests`), it shows a collapsible panel with pagination.
Each request renders a `HitlQuestionForm` that handles the three prompt types.

`TaskStatusMessage` receives only 3 HITL display props (no form interaction):

```typescript
hitlPrompt?: string
hitlResolved?: boolean
hitlUserAnswer?: string
```

These are used solely to control the `input-required` card's visual state (hidden while
HITL is active, "Input provided" label once resolved). All interactive form logic lives
in `HitlQuestionForm` inside `HitlPanel`.

### 5.7 New component: `src/components/hitl-inline-reply-form.tsx`

Renders one of three variants based on `promptType`:

| `promptType` | UI Rendered |
|---|---|
| `text` | Text input + "Continue" button |
| `choice` | Custom styled option buttons with letter badges (`A`, `B`, `C`...) + "Other" text input |
| `confirmation` | Two styled buttons: "Approve" (`A` badge, emerald) / "Reject" (`B` badge, red) |

States: `idle`, `submitting` (spinner on button, input disabled), `submitted`
(success message, form hidden), `error` (red text, retry enabled).

On submit: calls `onSubmit(requestId, userInput)`. The parent (`page.tsx`)
passes `respondToHitlRequest` which calls the HITL API and
optimistically sets `hitlResolved: true` on the message entity.

**Accessibility**: The inline reply form must:
- Auto-focus the text input when the HITL card first appears (use `autoFocus` or a
  `useEffect` with `ref.focus()`).
- Include an `aria-label` on the input: `"Reply to {agentName}"`.
- For `choice` prompt type, use clear letter labels for each option button.
- For `confirmation` prompt type, use descriptive button labels ("Approve" / "Reject")
  rather than generic "Yes" / "No".
- Ensure keyboard navigation: `Enter` submits the text form, `Escape` does nothing
  (do not close -- the user may not realize the form is dismissable).

### 5.7a HITL UI Specification — Visual Layouts

This section defines the exact visual structure of the HITL inline reply form as
rendered inside the `RoomChatInput` component's `topSlot` as a collapsible `HitlPanel`.

**Prerequisites**: Install `Collapsible` from shadcn/ui (`npx shadcn@latest add collapsible`).
All other primitives (`Input`, `Button`) already exist.

#### Container: `RoomChatInput` topSlot (Collapsible Panel)

The HITL panel renders **above** the chat input editor via the `topSlot` prop.
When an active (unresolved) HITL request exists, the panel appears:

```
┌─────────────────────────────────────────────────────────────┐
│  ⓘ Questions                                    ▲ 1/2 ▼  △ │  ← header: collapse toggle + pagination
│                                                             │
│  "Which date range should I search?"  ← hitlPrompt         │
│                                                             │
│  ┌───────────────────────── HITL FORM ──────────────┐      │
│  │  (varies by promptType — text/choice/confirmation) │      │
│  └──────────────────────────────────────────────────┘      │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│  [chat editor / text area]                                  │
│  ─── GroupSelector ──────────────────────── Submit ─────── │
└─────────────────────────────────────────────────────────────┘
```

The `task-status-message` bubble is **hidden** (`return null`) when an active HITL request
exists (`hitlResolved === false`). Once resolved, it shows "Input provided" as a static label.

#### Implementation: `HitlQuestionForm` (3 prompt types)

The form component (`hitl-inline-reply-form.tsx`) renders inside the `HitlPanel`
collapsible. Each prompt type has a distinct interaction pattern:

**Variant A: `text`** — Free-form text input with "Continue" button. Implemented as an
`<Input>` + `<Button>` in a flex row. Submit on Enter (non-shift) or button click.

**Variant B: `choice`** — Custom styled option buttons (not `RadioGroup`). Each choice
renders as a full-width `<button>` with a letter badge (`A`, `B`, `C`...) and
selection ring. An "Other" option with a text `<Input>` is appended so users can provide
a custom answer outside the predefined choices. Selection state is tracked via
`selectedChoice` / `customChoice` with a `CUSTOM_CHOICE_SENTINEL` value. Submit sends
either the selected predefined choice or the custom text.

**Variant C: `confirmation`** — Two styled `<button>` elements with letter badges:
"Approve" (`A` badge, emerald theme, CheckCircle icon) and "Reject" (`B` badge, red
theme, XCircle icon). Each button calls `handleConfirmation('approved' | 'rejected')`
directly — no intermediate selection state, no `<form>` wrapper.

All variants share the same state machine:

```
idle ──(user submits)──→ submitting ──(API success)──→ submitted
                              │
                              └──(API error)──→ error ──(user retries)──→ submitting
```

| State | Visual |
|-------|--------|
| `idle` | Form visible, inputs enabled, no spinner |
| `submitting` | Inputs disabled, active button shows `<Loader2 animate-spin />` |
| `submitted` | Form replaced with: `<CheckCircle /> Reply sent` in emerald |
| `error` | Form re-enabled, red error text below form, retry via re-submit |

#### HitlPanel: Collapsible Container with Pagination

The `HitlPanel` wraps `HitlQuestionForm` in a `<Collapsible>` from shadcn/ui. The header
shows a `<HelpCircle>` icon, "Question" / "Questions (N/M answered)" label, and
`<ChevronUp>` / `<ChevronDown>` pagination buttons with a `{current}/{total}` indicator.

Integration in the room page:

```tsx
<RoomChatInput
  topSlot={activeHitlRequests.length > 0
    ? <HitlPanel requests={activeHitlRequests} onSubmit={respondToHitlRequest} />
    : undefined
  }
/>
```

After submission, the panel auto-advances to the next unanswered question in the group.

#### Form Entry Animation

The panel container uses `animate-in fade-in duration-200` for a smooth entry when
a new HITL request arrives via SSE.

#### Resolved State: Form Removal

When `hitlResolved` becomes `true` (either from optimistic update or SSE
`hitl_status_update`), the form is replaced with a contextual message based on the
resolution type:

| Resolution | Display |
|-----------|---------|
| `responded` (user replied) | `✓ Reply sent` in emerald |
| `expired` | Card transitions to **failed** state (red theme, XCircle icon, "Request expired" error text). The form is removed entirely. |
| `canceled` | Card transitions to **canceled** state (red theme, XCircle icon, "Request canceled" text). The form is removed entirely. |

The theme transition (amber → red for expired/canceled) happens naturally because
`hitl_status_update` handler sets `taskStatus` to `'failed'` or `'canceled'`, which
changes the task-status-message rendering branch. When HITL is active (`hitlResolved === false`),
the task-status-message returns `null` (hidden). Once resolved (`hitlResolved === true`), it
shows "Input provided".

### 5.8 `src/app/c/room/[id]/page.tsx` — Wire HitlPanel into RoomChatInput

**Critical wiring step**: The room page connects HITL to the UI via `RoomChatInput`'s
`topSlot` prop. When unresolved HITL requests exist, a `<HitlPanel>` renders above the
chat editor.

```tsx
<RoomChatInput
  topSlot={activeHitlRequests.length > 0
    ? <HitlPanel requests={activeHitlRequests} onSubmit={respondToHitlRequest} />
    : undefined
  }
/>
```

`activeHitlRequests` comes from the `useActiveHitlRequests()` selector (defined in
`useRoomMessages.ts`), which filters message entities for those with
`hitlResolved === false` and a non-empty `hitlRequestId`.

`respondToHitlRequest` is defined in `page.tsx` — it calls `respondToHitl()` from
the HITL API client and optimistically sets `hitlResolved: true` on the entity.

Note: `TaskStatusMessage` receives only display-relevant HITL props (`hitlPrompt`,
`hitlResolved`, `hitlUserAnswer`) — it does **not** receive `onHitlSubmit`,
`hitlRequestId`, `hitlPromptType`, or `hitlChoices`. The interactive form lives
entirely in `HitlPanel`, not in `TaskStatusMessage`.

`RoomMessagesProps` has no HITL-related props — only `onQuote`.

---

## 6. State Management Changes

### 6.1 Message Store

No new Zustand store needed. HITL fields are added directly to `MessageEntity` and
`IncomingMessage`. The existing `upsertMessage` + conflict resolution handles updates.

### 6.2 Room UI Store

No changes needed. The HITL state lives on the message entity, not ephemeral UI state.

### 6.3 React Query

No new queries needed. HITL uses SSE for real-time updates and a one-shot POST for
replies.

---

## 7. Key Decisions

| Decision | Rationale |
|---|---|
| Collapsible HITL panel in `RoomChatInput` topSlot (not inline in task-status card) | Provides a Cursor/Codex-style experience. Panel is always visible near the input area, supports pagination for multiple concurrent HITL requests, and collapses when not needed. |
| HITL fields on MessageEntity (not separate store) | HITL requests are 1:1 with message entities. Co-locating keeps rendering simple. |
| Optimistic `hitlResolved` on submit | Removes the form immediately for responsive UX. If the API fails, the form reappears with error state. |
| Reconnect catch-up via REST | SSE events are fire-and-forget. If the user misses `hitl_input_requested` during a disconnect, the pending request endpoint restores state. |
| `prompt_type` variants | Matches backend `HITLPromptType` enum. Specialized inputs (radio, buttons) are more usable than forcing free-text for all cases. |

---

## 8. Error Handling

| Scenario | Behavior |
|---|---|
| `respondToHitl()` returns HTTP error | Show inline error below the form. Re-enable the submit button. Do not set `hitlResolved`. |
| `respondToHitl()` returns 409 Conflict | Request was already claimed. Keep the optimistic state (panel dismissed, user reply shown). |
| `respondToHitl()` times out (AbortError) | The backend is still processing the supervisor resume (can take 60-120s). Keep the optimistic state; the eventual `hitl_status_update` SSE will reconcile. The HITL respond call uses a 180s timeout (vs 60s default) to reduce the likelihood of this path. |
| HITL request expired (SSE `hitl_status_update` with `status: "expired"`) | Remove the form. Show expiry message in the card. Transition card to failed state. |
| HITL request canceled (user canceled the processing) | Remove the form. Show "Canceled" state on the card. |
| SSE disconnect during pending HITL | On reconnect, `fetchPendingHitlRequests` restores the form. |
| Backend HITL events not enabled for a room | No `hitl_input_requested` events are emitted. The existing `input-required` display-only card continues to render as-is. The HITL panel code is inert — graceful degradation when the backend does not emit HITL events. |

---

## 9. Out of Scope

- Backend HITL implementation (see `hybro-multi-agents-backend/docs/HITL_DESIGN.md`).
- Multi-user HITL (only the room owner can reply; future extension).
- HITL for `auth-required` state (requires OAuth redirect flow, separate design).
- Notification badge for pending HITL requests outside the room view.
- HITL request history / audit log UI.
- Image/file attachments in HITL replies — HITL forms are text-only in this design.
  See `MULTIMODAL_SUPPORT_DESIGN.md` Phase 3 for adding file attachments to HITL
  replies once both HITL and user file input are independently stable.

---

## 10. Testing Strategy

- Unit test `HitlInlineReplyForm` rendering for all 3 `promptType` variants.
- Unit test submit handler: success path (form disappears), error path (form shows error).
- Unit test `handleSSEMessage` for `hitl_input_requested` and `hitl_status_update` events.
- Integration test: mock SSE stream, verify form appears on `hitl_input_requested`,
  submits on user input, disappears on `hitl_status_update`.
- Edge case: expiry during user typing (form should be replaced with expiry message).
- Edge case: SSE reconnect restores pending HITL form.

---

## 11. Code References

| Concept | File | Notes |
|---|---|---|
| Inline reply form (3 prompt types) | `src/components/hitl-inline-reply-form.tsx` | Text, choice, and confirmation variants with grouped question pagination |
| REST API client | `src/lib/api/hitl.ts` | `respondToHitl()` and `fetchPendingHitlRequests()` |
| HITL entity fields | `src/stores/message-store/types.ts` | 10 HITL fields on `MessageEntity` (lines 44-54) |
| Upsert merge/no-op logic | `src/stores/message-store/upsert.ts` | HITL-aware conflict resolution |
| Upsert tests | `src/stores/message-store/__tests__/hitl-upsert.test.ts` | 239 lines of HITL-specific upsert tests |
| SSE event handlers | `src/hooks/useRoomWebhook.ts` | `hitl_input_requested` and `hitl_status_update` cases in `handleSSEMessage` |
| Active requests selector | `src/hooks/useRoomMessages.ts` | `useActiveHitlRequests()` hook |
| Chat input integration | `src/components/room-chat-input.tsx` | HitlPanel rendered in `topSlot` prop |
| Task status card | `src/components/task-status-message.tsx` | `input-required` state rendering with resolved/unresolved indicators |
| Reconnect catch-up | `src/hooks/useRoomWebhook.ts` | Fetches pending HITL requests on SSE reconnect (lines 958-997) |
