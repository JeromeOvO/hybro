# HITL Frontend Design — Human-in-the-Loop Inline Reply

**Status**: Not started
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
2. An inline reply form embedded in the task-status card.
3. An API client for `POST /api/v1/hitl/respond`.
4. Reconnect catch-up logic to restore missed HITL requests.
5. Expiry handling for timed-out requests.

---

## 2. Current State

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

**Submit reply**: `POST /api/v1/hitl/respond`

```json
// Request
{ "request_id": "abc123", "user_input": "2024-2026" }

// Response
{ "success": true, "request_id": "abc123" }
```

**Pending requests** (for reconnect catch-up): `GET /api/v1/hitl/requests/{room_id}/pending`

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
HITLStatus:     "pending" | "responded" | "expired" | "canceled"
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
    │                 task-status-message.tsx
    │                          │
    │                 HitlInlineReplyForm
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
3. `task-status-message.tsx` detects the HITL fields and renders an inline reply form
   (text input, choice buttons, or confirmation buttons depending on `prompt_type`).
4. User submits reply. The form calls `respondToHitl(request_id, user_input)`.
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

**`isNoOpUpdate`** — add these comparisons to the return expression:

```typescript
existing.hitlResolved    === coalesce(incoming.hitlResolved, existing.hitlResolved) &&
existing.hitlPrompt      === coalesce(incoming.hitlPrompt, existing.hitlPrompt) &&
existing.hitlRequestId   === coalesce(incoming.hitlRequestId, existing.hitlRequestId) &&
```

Only `hitlResolved`, `hitlPrompt`, and `hitlRequestId` need no-op checks because they
are the only HITL fields that change on update (the others are set once at creation).
If `hitlExpiresAt` becomes render-visible in the future, add it too.

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

const API_BASE_URL = getApiUrl('hitl')
```

**Import convention note**: This codebase uses `../api-client` for `apiPost`/`apiGet`
and `../utils` for `getApiUrl` — NOT `./client`. The `getApiUrl` function already
prepends the base URL and `/api/v1/` prefix (from `NEXT_PUBLIC_API_PREFIX`), so the
argument must be just the service name (`'hitl'`), not a full path like `'/api/v1/hitl'`
which would produce a double-prefix (`/api/v1//api/v1/hitl`). Follow the same pattern
as `room.ts` line 16: `getApiUrl('roomCenter')`.

```typescript
export async function respondToHitl(
  requestId: string,
  userInput: string,
  getToken?: () => Promise<string | null>,
): Promise<{ success: boolean; request_id: string }> {
  return apiPost(`${API_BASE_URL}/respond`, {
    request_id: requestId,
    user_input: userInput,
  }, getToken)
}

export async function fetchPendingHitlRequests(
  roomId: string,
  getToken?: () => Promise<string | null>,
): Promise<{ requests: HitlPendingRequest[] }> {
  return apiGet(`${API_BASE_URL}/requests/${roomId}/pending`, getToken)
}

export interface HitlPendingRequest {
  request_id: string
  message_id: string
  source: 'agent' | 'supervisor'
  agent_id?: string
  agent_name?: string
  prompt: string
  prompt_type: 'text' | 'choice' | 'confirmation'
  choices?: string[] | null
  status: 'pending'
  expires_at?: string
  created_at: string
}
```

Add to `src/lib/api/index.ts` barrel export.

### 5.6 `src/components/task-status-message.tsx` — Embed inline reply form

Modify the `input-required` rendering branch. When `hitlRequestId` is present and
`hitlResolved` is false, render `HitlInlineReplyForm` below the prompt text.

New props on `TaskStatusMessageProps`:

```typescript
// HITL fields
hitlRequestId?: string
hitlPrompt?: string
hitlPromptType?: 'text' | 'choice' | 'confirmation'
hitlChoices?: string[] | null
hitlResolved?: boolean
onHitlSubmit?: (requestId: string, userInput: string) => Promise<void>
```

### 5.7 New component: `src/components/hitl-inline-reply-form.tsx`

Renders one of three variants based on `promptType`:

| `promptType` | UI Rendered |
|---|---|
| `text` | Text input + "Send" button |
| `choice` | Radio buttons for each `choices` item + "Submit" button |
| `confirmation` | Two buttons: "Approve" / "Reject" |

States: `idle`, `submitting` (spinner on button, input disabled), `submitted`
(success message, form hidden), `error` (red text, retry enabled).

On submit: calls `onHitlSubmit(requestId, userInput)`. The parent
(`task-status-message.tsx`) passes a handler that calls `respondToHitl()` and
optimistically sets `hitlResolved: true` on the message entity.

**Accessibility**: The inline reply form must:
- Auto-focus the text input when the HITL card first appears (use `autoFocus` or a
  `useEffect` with `ref.focus()`).
- Include an `aria-label` on the input: `"Reply to {agentName}"`.
- For `choice` prompt type, use `role="radiogroup"` with `aria-label`.
- For `confirmation` prompt type, use descriptive button labels ("Approve" / "Reject")
  rather than generic "Yes" / "No".
- Ensure keyboard navigation: `Enter` submits the text form, `Escape` does nothing
  (do not close -- the user may not realize the form is dismissable).

### 5.7a HITL UI Specification — Visual Layouts

This section defines the exact visual structure of the HITL inline reply form as
rendered inside the `task-status-message.tsx` `input-required` card.

**Prerequisites**: Install `RadioGroup` from shadcn/ui (`npx shadcn@latest add radio-group`).
All other primitives (`Input`, `Textarea`, `Button`, `Label`, `Form`) already exist.

#### Container: `input-required` Task Card (Existing)

The HITL form renders **inside** the existing amber-themed `input-required` card.
The card already has this visual structure:

```
┌─────────────────────────────────────────────────────────────┐
│  border-amber-200  bg-amber-50  (dark: border-amber-500/20 │
│                                  bg-amber-500/12)           │
│                                                             │
│  ┌──┐  Agent Name          ⚠ Input required · Step 2/5     │
│  │⚠ │  ────────────                                        │
│  └──┘                                                       │
│                                                             │
│  "Which date range should I search?"  ← hitlPrompt         │
│                                                             │
│  ┌─────────────────────────────── NEW: HITL FORM ──┐       │
│  │  (varies by promptType — see below)              │       │
│  └──────────────────────────────────────────────────┘       │
│                                                             │
│  ⏱ 12s elapsed                                              │
└─────────────────────────────────────────────────────────────┘
```

The form is inserted between the prompt text and the elapsed timer, separated by an
amber-themed `border-t` on the form container (not a `<Separator>` component, because
the border needs to match the amber card color scheme).

#### Variant A: `text` Prompt Type

Most common variant. Free-form text reply.

```
┌─ HITL Form (text) ──────────────────────────────────────────┐
│                                                              │
│  ┌──────────────────────────────────────────┐  ┌─────────┐  │
│  │  Type your reply...                      │  │  Send ➤ │  │
│  └──────────────────────────────────────────┘  └─────────┘  │
│                                                              │
│  (error state:)                                              │
│  ✕ Failed to send reply. Try again.                         │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**Implementation**:

```tsx
<form onSubmit={handleSubmit} className="mt-3 pt-3 border-t border-amber-200/60
  dark:border-amber-500/20">
  <div className="flex gap-2 items-start">
    <Input
      ref={inputRef}
      value={input}
      onChange={(e) => setInput(e.target.value)}
      placeholder="Type your reply..."
      disabled={isSubmitting}
      aria-label={`Reply to ${agentName}`}
      className="flex-1 bg-white/80 dark:bg-white/5 border-amber-300
        dark:border-amber-500/30 focus-visible:ring-amber-400"
      onKeyDown={(e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault()
          handleSubmit(e)
        }
      }}
    />
    <Button
      type="submit"
      disabled={isSubmitting || !input.trim()}
      size="sm"
      className="bg-amber-600 hover:bg-amber-700 text-white shrink-0"
    >
      {isSubmitting ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Send'}
    </Button>
  </div>
  {error && (
    <p className="mt-1.5 text-xs text-red-600 dark:text-red-400 flex items-center gap-1">
      <XCircle className="h-3 w-3" />
      {error}
    </p>
  )}
</form>
```

**Styling rationale**: The form uses amber-tinted borders and the amber-600 primary
button to stay within the `input-required` color scheme. The input background uses
`bg-white/80` (light) / `bg-white/5` (dark) for subtle contrast against the amber card.

#### Variant B: `choice` Prompt Type

Renders radio buttons for predefined choices.

```
┌─ HITL Form (choice) ────────────────────────────────────────┐
│                                                              │
│  ○  2023-2024                                               │
│  ◉  2024-2025                      (selected)               │
│  ○  2025-2026                                               │
│  ○  All available years                                     │
│                                                              │
│  ┌──────────┐                                               │
│  │  Submit   │                                               │
│  └──────────┘                                               │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**Implementation**:

```tsx
<form onSubmit={handleSubmit} className="mt-3 pt-3 border-t border-amber-200/60
  dark:border-amber-500/20">
  <RadioGroup
    value={selectedChoice}
    onValueChange={setSelectedChoice}
    aria-label={`Choose a response for ${agentName}`}
    className="space-y-2"
  >
    {choices.map((choice, i) => (
      <div key={i} className="flex items-center gap-2.5">
        <RadioGroupItem
          value={choice}
          id={`hitl-choice-${requestId}-${i}`}
          disabled={isSubmitting}
          className="border-amber-400 text-amber-600
            data-[state=checked]:bg-amber-600 data-[state=checked]:border-amber-600"
        />
        <Label
          htmlFor={`hitl-choice-${requestId}-${i}`}
          className="text-sm font-normal cursor-pointer text-amber-900
            dark:text-amber-100"
        >
          {choice}
        </Label>
      </div>
    ))}
  </RadioGroup>
  <Button
    type="submit"
    disabled={isSubmitting || !selectedChoice}
    size="sm"
    className="mt-3 bg-amber-600 hover:bg-amber-700 text-white"
  >
    {isSubmitting ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Submit'}
  </Button>
  {error && (
    <p className="mt-1.5 text-xs text-red-600 dark:text-red-400 flex items-center gap-1">
      <XCircle className="h-3 w-3" />
      {error}
    </p>
  )}
</form>
```

**Prerequisite**: Run `npx shadcn@latest add radio-group` to install `RadioGroup` and
`RadioGroupItem` into `src/components/ui/`. This adds `@radix-ui/react-radio-group`.

#### Variant C: `confirmation` Prompt Type

Binary decision. Two buttons, no text input.

```
┌─ HITL Form (confirmation) ──────────────────────────────────┐
│                                                              │
│  ┌─────────────┐    ┌──────────────┐                        │
│  │  ✓ Approve   │    │  ✕ Reject    │                        │
│  └─────────────┘    └──────────────┘                        │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**Implementation**:

```tsx
<div className="mt-3 pt-3 border-t border-amber-200/60
  dark:border-amber-500/20 flex gap-2">
  <Button
    onClick={() => handleConfirmation('approved')}
    disabled={isSubmitting}
    size="sm"
    className="bg-emerald-600 hover:bg-emerald-700 text-white"
  >
    {isSubmitting && lastAction === 'approved'
      ? <Loader2 className="h-4 w-4 animate-spin" />
      : <><CheckCircle className="h-4 w-4 mr-1" /> Approve</>
    }
  </Button>
  <Button
    onClick={() => handleConfirmation('rejected')}
    disabled={isSubmitting}
    variant="outline"
    size="sm"
    className="border-red-300 text-red-600 hover:bg-red-50
      dark:border-red-500/30 dark:text-red-400 dark:hover:bg-red-500/10"
  >
    {isSubmitting && lastAction === 'rejected'
      ? <Loader2 className="h-4 w-4 animate-spin" />
      : <><XCircle className="h-4 w-4 mr-1" /> Reject</>
    }
  </Button>
  {error && (
    <p className="ml-2 self-center text-xs text-red-600 dark:text-red-400">
      {error}
    </p>
  )}
</div>
```

**Color choice**: Approve uses emerald (matching the "completed" task state), Reject
uses red outline (matching the "failed" state). This gives users clear visual
association: green = positive, red = negative.

#### All Variants: State Transitions

```
idle ──(user submits)──→ submitting ──(API success)──→ submitted
                              │
                              └──(API error)──→ error ──(user retries)──→ submitting
```

| State | Visual |
|-------|--------|
| `idle` | Form visible, inputs enabled, no spinner |
| `submitting` | Inputs disabled, submit button shows `<Loader2 animate-spin />`, amber pulse overlay on card |
| `submitted` | Form replaced with: `<p className="text-sm text-emerald-600 dark:text-emerald-400 flex items-center gap-1.5"><CheckCircle className="h-4 w-4" /> Reply sent</p>` |
| `error` | Form re-enabled, red error text below form, retry via re-submit |

**Optimistic UX**: The `submitted` state appears immediately on API call (before
response). If the backend returns an error, the state reverts to `error` and the form
reappears.

#### Form Entry Animation

When the HITL form section appears (on `hitl_input_requested` SSE), use the same entry
animation as the task card itself:

```
animate-in fade-in slide-in-from-bottom-1 duration-200
```

This is slightly faster than the card's `duration-300` to feel like an additive reveal
rather than a competing animation.

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
changes the task-status-message rendering branch.

### 5.8 `src/components/room-messages.tsx` — Pass HITL props to TaskStatusMessage

**Critical wiring step**: The current `task-status` case in `room-messages.tsx` (around
line 97) passes only task fields to `<TaskStatusMessage>`. The new HITL props must be
forwarded from the entity, and the `onHitlSubmit` handler must be wired in.

Update the `case 'task-status'` rendering branch:

```tsx
case 'task-status':
  return (
    <TaskStatusMessage
      internalId={entity.id}
      agentId={entity.agentId}
      agentName={entity.senderName}
      initialStatus={(entity.taskStatus || TASK_STATE.WORKING) as TaskState}
      content={entity.content || null}
      error={entity.taskError}
      statusMessage={entity.taskStatusMessage}
      stepNumber={entity.stepNumber}
      totalSteps={entity.totalSteps}
      taskContent={entity.taskContent}
      taskCreatedAt={entity.taskCreatedAt || entity.timestamp}
      // ── HITL props (new) ──
      hitlRequestId={entity.hitlRequestId}
      hitlPrompt={entity.hitlPrompt}
      hitlPromptType={entity.hitlPromptType}
      hitlChoices={entity.hitlChoices}
      hitlResolved={entity.hitlResolved}
      onHitlSubmit={onHitlSubmit}
    />
  )
```

The `onHitlSubmit` handler is provided by `useRoomWebhook` (or a parent wrapper) and
passed down through `RoomMessagesProps`. Add it to the props interface:

```typescript
interface RoomMessagesProps {
  onQuote?: (data: QuoteData) => void
  onHitlSubmit?: (requestId: string, userInput: string) => Promise<void>
}
```

The handler calls `respondToHitl()` and optimistically sets `hitlResolved` on the entity.
It is defined in `useRoomWebhook.ts` alongside `retryMessage` and `sendUserMessage`.

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
| Inline form inside task-status card (not chat input) | Prevents confusion about which request the reply targets. Multiple agents could have concurrent HITL requests. |
| HITL fields on MessageEntity (not separate store) | HITL requests are 1:1 with message entities. Co-locating keeps rendering simple. |
| Optimistic `hitlResolved` on submit | Removes the form immediately for responsive UX. If the API fails, the form reappears with error state. |
| Reconnect catch-up via REST | SSE events are fire-and-forget. If the user misses `hitl_input_requested` during a disconnect, the pending request endpoint restores state. |
| `prompt_type` variants | Matches backend `HITLPromptType` enum. Specialized inputs (radio, buttons) are more usable than forcing free-text for all cases. |

---

## 8. Error Handling

| Scenario | Behavior |
|---|---|
| `respondToHitl()` returns HTTP error | Show inline error below the form. Re-enable the submit button. Do not set `hitlResolved`. |
| HITL request expired (SSE `hitl_status_update` with `status: "expired"`) | Remove the form. Show expiry message in the card. Transition card to failed state. |
| HITL request canceled (user canceled the processing) | Remove the form. Show "Canceled" state on the card. |
| SSE disconnect during pending HITL | On reconnect, `fetchPendingHitlRequests` restores the form. |
| Backend HITL not yet implemented | No `hitl_input_requested` events are emitted. The existing `input-required` display-only card continues to render as-is. The new code is inert until the backend is ready. |

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
