# Normalized Message Store — Design Document

**Status:** Steps 1–4 implemented; post-implementation review completed (Gaps 15–20 added)
**Date:** 2026-02-17
**Author:** Architecture Review

---

## Table of Contents

1. [Problem Statement](#problem-statement)
2. [Design Goals](#design-goals)
3. [Architecture Overview](#architecture-overview)
4. [Core Data Model](#core-data-model)
5. [The Store](#the-store)
6. [Write Gateway — `upsertMessage`](#write-gateway--upsertmessage)
7. [Data Source Integration](#data-source-integration)
8. [Reading from the Store — Selector Hooks](#reading-from-the-store--selector-hooks)
9. [Updated Component Rendering](#updated-component-rendering)
10. [Auto-Scroll — Clean Separation](#auto-scroll--clean-separation)
11. [Design Review — Identified Gaps and Resolutions](#design-review--identified-gaps-and-resolutions)
12. [Migration Path](#migration-path)
13. [Files Inventory](#files-inventory)
14. [Summary](#summary)

---

## Problem Statement

The current chat message architecture maintains **two parallel representations** of the
same message list:

1. **React Query (`messagesQuery.data`)** — DB-backed "source of record," fetched via
   `inquiryRoomMessagesByRoomId`.
2. **Zustand (`liveMessagesByRoom`)** — real-time overlay from SSE events and optimistic
   updates.

These are merged at read time in a `useMemo` inside `useRoomWebhook`:

```ts
const messages = useMemo(() => {
  const map = new Map<string, MessageData>()
  ;(messagesQuery.data || []).forEach(msg => map.set(msg.id, msg))
  liveMessages.forEach(msg => map.set(msg.id, msg))
  return Array.from(map.values()).sort(/* ... */)
}, [messagesQuery.data, liveMessages])
```

This creates several concrete problems:

### 1. Scroll disruption on post-processing refetch

When a multi-agent workflow completes, the SSE `processing_status: completed` handler
fires a delayed refetch (`useRoomWebhook.ts` line ~682):

```ts
setTimeout(() => { messagesQueryRef.current?.refetch() }, 1500)
```

This full DB reload triggers a complete re-render of the message list. The resulting DOM
height changes (from component type swaps, expand/collapse state resets, and re-sorting)
cause the user's scroll position to jump.

### 2. Component type flicker

The SSE layer creates task messages as `type: 'task'`. The DB layer's post-processing
converts completed tasks with content to `type: 'agent'`. When the refetch replaces
the live version with the DB version, the message swaps between `TaskStatusMessage` and
`MessageBubble` components — different DOM structures with different heights.

The rendering layer has a workaround (`shouldRenderTaskAsAgent` in `room-messages.tsx`)
that partially papers over this, but the underlying type divergence remains.

### 3. The live layer is lossy but treated as sufficient

SSE events can be missed (network hiccups, reconnection gaps). The system acknowledges
this with the "belt-and-suspenders" refetch comment, but the refetch itself is
disruptive. There is no middle ground between "trust SSE completely" and "reload
everything from DB."

### 4. Over-sensitive query key

The `messagesQuery` key includes `allAgentsQuery.data?.length`, meaning the entire
message list re-fetches and re-converts whenever the agents catalog loads. This mixes
a presentation concern (agent name resolution) into the data-fetching cache key.

### 5. No conflict resolution strategy

When the Map merge produces the final list, the last writer wins (live messages
overwrite DB messages by insertion order). There are no rules for which source should
win in which situation — a stale DB fetch can overwrite a fresh SSE update, or vice
versa.

---

## Design Goals

1. **Single source of truth** — one store, one write path for all message data.
2. **Granular updates** — changing one message does not re-create or re-render the
   entire list.
3. **No scroll disruption** — reconciliation with the DB is invisible to the user.
4. **SSE-first during a session** — DB is for initial hydration and gap-filling, not
   routine replacement.
5. **Deterministic display type** — the rendering component for a message is resolved
   once at write time, not at render time. Both SSE and DB produce the same display
   type for the same message state.
6. **Preserve existing API contracts** — `RoomMessages` props, SSE event types, and
   backend APIs remain unchanged.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│                     Data Sources                         │
│                                                          │
│  ┌──────────────┐   ┌───────────────┐   ┌────────────┐  │
│  │  DB Fetch     │   │  SSE Events   │   │  User      │  │
│  │  (initial +   │   │  (live)       │   │  Input     │  │
│  │   reconcile)  │   │               │   │            │  │
│  └──────┬────────┘   └──────┬────────┘   └─────┬──────┘  │
│         │                   │                   │        │
└─────────┼───────────────────┼───────────────────┼────────┘
          │                   │                   │
          ▼                   ▼                   ▼
   ┌──────────────────────────────────────────────────┐
   │       upsertMessage() / upsertMany()             │
   │    (single write gateway with conflict rules)    │
   └─────────────────────┬────────────────────────────┘
                         │
                         ▼
   ┌──────────────────────────────────────────────────┐
   │          Normalized Message Store                 │
   │          (Zustand with subscribeWithSelector)     │
   │                                                   │
   │  entities: Record<MessageId, MessageEntity>       │
   │  orderedIds: string[]  (pre-sorted)               │
   │  roomId: string                                   │
   │  hydratedFromDb: boolean                          │
   │  lastDbSyncAt: number | null                      │
   │  sseGapDetected: boolean                          │
   └─────────────────────┬────────────────────────────┘
                         │
                         ▼
   ┌──────────────────────────────────────────────────┐
   │        useRoomMessages() hook                     │
   │        (selector-based reads)                     │
   │                                                   │
   │  • useOrderedMessages(): MessageEntity[]          │
   │  • useMessage(id): MessageEntity | undefined      │
   │  • useMessageCount(): number                      │
   └─────────────────────┬────────────────────────────┘
                         │
                         ▼
   ┌──────────────────────────────────────────────────┐
   │        RoomMessages component                     │
   │   (per-message selectors → isolated re-renders)   │
   └──────────────────────────────────────────────────┘
```

**Key structural change:** Today, messages flow through two stores (React Query + Zustand
`liveMessagesByRoom`) and are merged in a `useMemo`. In the new design, all writes go
through a single `upsertMessage` gateway into one normalized Zustand store. React Query
is removed from the message path entirely (it remains for room settings and agent catalog).

---

## Core Data Model

### `MessageEntity` — the normalized record

The current `MessageData` interface (defined in `room-messages.tsx`) serves as both a
data transfer object and a render model. The new design replaces it with a clearly-layered
entity that carries provenance metadata for conflict resolution.

```ts
// stores/message-store/types.ts

/** Which pipeline last wrote this entity. */
type MessageSource = 'db' | 'sse' | 'optimistic'

/**
 * Display type — resolved once at write time.
 * Determines which React component renders this message.
 */
type DisplayType = 'user-bubble' | 'agent-bubble' | 'task-status'

interface MessageEntity {
  // ── Identity ──────────────────────────────────────────────
  id: string                       // message_id (stable across sources)
  roomId: string

  // ── Core content ──────────────────────────────────────────
  messageType: 'user' | 'agent'   // original backend type (immutable)
  content: string
  senderName: string
  agentId?: string
  userId?: string

  // ── Task state (agent messages backed by A2A tasks) ───────
  taskStatus?: TaskState
  taskError?: string | null
  taskStatusMessage?: string | null
  taskRequiresInput?: boolean
  taskRequiresAuth?: boolean
  taskContent?: string            // description of work being done
  taskCreatedAt?: string
  taskUpdatedAt?: string

  // ── Ordering ──────────────────────────────────────────────
  timestamp: string               // canonical, for primary sort
  stepNumber?: number             // workflow step (1-indexed)
  totalSteps?: number

  // ── Provenance & conflict resolution ──────────────────────
  source: MessageSource           // who wrote this version
  sourceVersion: number           // monotonic, increments per write to this entity
  displayType: DisplayType        // drives component selection at render time
  isEphemeral: boolean            // true for processing placeholders, cancel confirmations
  createdAt: number               // local epoch ms — first insert
  updatedAt: number               // local epoch ms — last upsert
}
```

**Key differences from current `MessageData`:**

| Field | Purpose |
|-------|---------|
| `displayType` | Resolved at write time by `resolveDisplayType()`. Both SSE and DB produce the same value for the same state. Eliminates component-type flicker. |
| `source` / `sourceVersion` | Enable conflict resolution — the store knows who wrote the current version and whether an incoming update is newer. |
| `isEphemeral` | Cleanly separates synthetic UI messages (processing placeholder, cancel confirmation) from real persisted messages. DB writes never overwrite ephemeral messages; removal is always explicit. |
| `messageType` | Immutable. Reflects what the backend said (`'user'` or `'agent'`). Display decisions live in `displayType`, not here. |

### `IncomingMessage` — the write-path input

Data sources don't construct `MessageEntity` directly. They build an `IncomingMessage`
(a partial shape) and pass it to `upsertMessage`, which fills in provenance fields:

```ts
// stores/message-store/types.ts

interface IncomingMessage {
  id: string
  roomId: string
  messageType: 'user' | 'agent'
  content: string
  senderName: string
  timestamp: string

  // All optional — omitted fields preserve existing values on update
  agentId?: string
  userId?: string
  taskStatus?: TaskState
  taskError?: string | null
  taskStatusMessage?: string | null
  taskRequiresInput?: boolean
  taskRequiresAuth?: boolean
  taskContent?: string
  taskCreatedAt?: string
  taskUpdatedAt?: string
  stepNumber?: number
  totalSteps?: number
  isEphemeral?: boolean
}
```

### `resolveDisplayType` — single source of rendering truth

This function replaces three scattered locations in the current code:
- `shouldRenderTaskAsAgent()` in `room-messages.tsx`
- The post-processing `.map()` in `messagesQuery.queryFn` (lines 397–422 of
  `useRoomWebhook.ts`)
- Implicit `type: MESSAGE_TYPE.TASK` assignments in SSE handlers

```ts
// stores/message-store/resolve-display-type.ts

function resolveDisplayType(msg: {
  messageType: 'user' | 'agent'
  taskStatus?: TaskState
  content?: string
  isEphemeral?: boolean
}): DisplayType {
  // User messages are always user bubbles
  if (msg.messageType === 'user') return 'user-bubble'

  // Agent message with no task → regular agent bubble
  if (!msg.taskStatus) return 'agent-bubble'

  // Completed task with content → agent bubble (successful response)
  if (msg.taskStatus === 'completed' && msg.content?.trim()) {
    return 'agent-bubble'
  }

  // Everything else: working, failed, canceled, input_required, etc.
  return 'task-status'
}
```

---

## The Store

### `useMessageStore` — Zustand normalized store

The store uses `subscribeWithSelector` middleware so consumers can subscribe to
fine-grained slices (e.g., a single entity by ID) without re-rendering on unrelated
changes.

```ts
// stores/message-store/index.ts

import { create } from 'zustand'
import { subscribeWithSelector } from 'zustand/middleware'

interface MessageStoreState {
  // ── Per-room entity storage ───────────────────────────────
  entities: Record<string, MessageEntity>  // messageId → entity
  orderedIds: string[]                     // pre-sorted message IDs for current room
  roomId: string | null                    // active room

  // ── Sync metadata ────────────────────────────────────────
  hydratedFromDb: boolean        // has the initial DB load completed?
  lastDbSyncAt: number | null    // timestamp of last DB reconciliation
  sseGapDetected: boolean        // SSE reconnected during processing?
  version: number                // global counter — increments on any mutation

  // ── Write operations ─────────────────────────────────────
  upsertMessage:    (msg: IncomingMessage, source: MessageSource) => void
  upsertMany:       (msgs: IncomingMessage[], source: MessageSource) => void
  removeMessage:    (id: string) => void
  setRoom:          (roomId: string) => void
  clearRoom:        () => void
  markDbSynced:     () => void
  setSseGapDetected:(v: boolean) => void
}

export const useMessageStore = create<MessageStoreState>()(
  subscribeWithSelector((set, get) => ({
    entities: {},
    orderedIds: [],
    roomId: null,
    hydratedFromDb: false,
    lastDbSyncAt: null,
    sseGapDetected: false,
    version: 0,

    upsertMessage: (incoming, source) => { /* see next section */ },

    upsertMany: (msgs, source) => {
      // Batch: apply upsertMessage logic for each, but produce a single state update.
      set((state) => {
        let newEntities = { ...state.entities }
        let idsChanged = false

        for (const incoming of msgs) {
          const result = applyUpsert(newEntities, state.orderedIds, incoming, source)
          if (result) {
            newEntities = result.entities
            idsChanged = idsChanged || result.idsChanged
          }
        }

        const newOrderedIds = idsChanged
          ? buildSortedIds(newEntities)
          : state.orderedIds

        return {
          entities: newEntities,
          orderedIds: newOrderedIds,
          version: state.version + 1,
        }
      })
    },

    removeMessage: (id) => set((state) => {
      if (!state.entities[id]) return state
      const { [id]: _, ...rest } = state.entities
      return {
        entities: rest,
        orderedIds: state.orderedIds.filter(oid => oid !== id),
        version: state.version + 1,
      }
    }),

    setRoom: (roomId) => set({
      roomId,
      entities: {},
      orderedIds: [],
      hydratedFromDb: false,
      lastDbSyncAt: null,
      sseGapDetected: false,
      version: 0,
    }),

    clearRoom: () => set({
      roomId: null,
      entities: {},
      orderedIds: [],
      hydratedFromDb: false,
      lastDbSyncAt: null,
      sseGapDetected: false,
      version: 0,
    }),

    markDbSynced: () => set({
      hydratedFromDb: true,
      lastDbSyncAt: Date.now(),
    }),

    setSseGapDetected: (v) => set({ sseGapDetected: v }),
  }))
)
```

---

## Write Gateway — `upsertMessage`

This is the heart of the design. Every message write — from SSE, DB fetch, or optimistic
UI — passes through this function. It applies conflict resolution rules and suppresses
re-renders when nothing visible changed.

### Conflict Resolution Rules

| # | Rule | Rationale |
|---|------|-----------|
| 1 | **Never downgrade a terminal task status.** If the entity is already `completed` / `failed` / `canceled`, reject an incoming `working` / `submitted` status. | A slow DB fetch or out-of-order SSE event should not revert a task that has already finished. |
| 2 | **SSE wins over DB for non-terminal states.** If the entity was last written by SSE and is still in progress, skip an incoming DB write. | During active processing, SSE has fresher data. The DB snapshot is already stale by the time it arrives. |
| 3 | **DB wins for terminal states.** Allow DB to overwrite SSE data when the incoming status is terminal. | The DB has the canonical final content (properly formatted by the backend), which may differ from the abbreviated SSE version. |
| 4 | **Skip no-op updates.** If the incoming data doesn't change any rendering-visible fields, don't produce a new state. | Prevents unnecessary re-renders during reconciliation when most messages haven't changed. |
| 5 | **Never overwrite ephemeral messages from DB.** Ephemeral messages (processing placeholder, cancel confirmation) exist only in the UI. DB writes skip them. | Ephemeral messages are managed exclusively by the UI lifecycle, removed explicitly. |

### Implementation

```ts
// stores/message-store/upsert.ts

/**
 * Core upsert logic, extracted so it can be used by both single and batch writes.
 * Returns null if the update was rejected or is a no-op.
 */
function applyUpsert(
  entities: Record<string, MessageEntity>,
  orderedIds: string[],
  incoming: IncomingMessage,
  source: MessageSource,
): { entities: Record<string, MessageEntity>; idsChanged: boolean } | null {
  const existing = entities[incoming.id]

  // ── Rule 5: Never overwrite ephemeral from DB ──
  if (existing?.isEphemeral && source === 'db') {
    return null
  }

  if (existing) {
    // ── Rule 1: Never downgrade terminal status ──
    if (
      existing.taskStatus &&
      isTerminalState(existing.taskStatus) &&
      incoming.taskStatus &&
      !isTerminalState(incoming.taskStatus)
    ) {
      return null
    }

    // ── Rule 2: SSE wins over DB for non-terminal ──
    if (
      existing.source === 'sse' &&
      source === 'db' &&
      existing.taskStatus &&
      !isTerminalState(existing.taskStatus)
    ) {
      return null
    }

    // ── Rule 4: Skip no-op updates ──
    if (isNoOpUpdate(existing, incoming, source)) {
      return null
    }
  }

  // ── Build the new entity ──
  const displayType = resolveDisplayType({
    messageType: incoming.messageType,
    taskStatus: incoming.taskStatus,
    content: incoming.content,
    isEphemeral: incoming.isEphemeral,
  })

  const entity: MessageEntity = {
    // Preserve fields not present in incoming (e.g. stepNumber from SSE
    // when DB update only carries content)
    ...(existing || {}),
    // Overlay incoming fields
    ...incoming,
    // Computed / provenance fields
    displayType,
    source,
    sourceVersion: (existing?.sourceVersion ?? 0) + 1,
    updatedAt: Date.now(),
    createdAt: existing?.createdAt ?? Date.now(),
    isEphemeral: incoming.isEphemeral ?? existing?.isEphemeral ?? false,
  }

  const newEntities = { ...entities, [entity.id]: entity }
  const idsChanged = !existing // new message added

  return { entities: newEntities, idsChanged }
}
```

### No-Op Detection

```ts
function isNoOpUpdate(
  existing: MessageEntity,
  incoming: IncomingMessage,
  source: MessageSource,
): boolean {
  const incomingDisplayType = resolveDisplayType({
    messageType: incoming.messageType ?? existing.messageType,
    taskStatus: incoming.taskStatus ?? existing.taskStatus,
    content: incoming.content ?? existing.content,
  })

  return (
    existing.content      === (incoming.content ?? existing.content) &&
    existing.taskStatus   === (incoming.taskStatus ?? existing.taskStatus) &&
    existing.taskError    === (incoming.taskError ?? existing.taskError) &&
    existing.senderName   === (incoming.senderName ?? existing.senderName) &&
    existing.stepNumber   === (incoming.stepNumber ?? existing.stepNumber) &&
    existing.totalSteps   === (incoming.totalSteps ?? existing.totalSteps) &&
    existing.displayType  === incomingDisplayType
  )
}
```

### Sorted ID Maintenance

```ts
/**
 * Build a sorted array of message IDs from the entities map.
 * Sort order: timestamp (primary), stepNumber within same workflow batch
 * (timestamps within 60s), then message ID for stability.
 */
function buildSortedIds(entities: Record<string, MessageEntity>): string[] {
  return Object.values(entities)
    .sort((a, b) => {
      const aTime = new Date(a.timestamp).getTime()
      const bTime = new Date(b.timestamp).getTime()
      const timeDiff = aTime - bTime

      // Within the same workflow batch (< 60s apart), sort by step number
      if (
        a.stepNumber != null && b.stepNumber != null &&
        Math.abs(timeDiff) < 60_000
      ) {
        const stepDiff = a.stepNumber - b.stepNumber
        if (stepDiff !== 0) return stepDiff
      }

      if (timeDiff !== 0) return timeDiff

      // Tiebreakers
      const stepA = a.stepNumber ?? Infinity
      const stepB = b.stepNumber ?? Infinity
      if (stepA !== stepB) return stepA - stepB

      return a.id.localeCompare(b.id)
    })
    .map(e => e.id)
}
```

---

## Data Source Integration

All data sources produce `IncomingMessage` objects and call `upsertMessage` (or
`upsertMany`). The data sources themselves become thin adapters — they no longer carry
display logic.

### 1. Initial DB Hydration

On room entry, a one-time fetch loads messages from the database and hydrates the store.
This replaces the current `messagesQuery` (React Query) entirely for the message path.

```ts
// hooks/useRoomData.ts (new hook, replaces messages portion of useRoomWebhook)

async function hydrateFromDb(roomId: string, getToken: GetTokenFn) {
  const store = useMessageStore.getState()
  if (store.hydratedFromDb && store.roomId === roomId) return // already hydrated

  store.setRoom(roomId)

  const response = await inquiryRoomMessagesByRoomId(roomId, getToken)
  if (!response.success || !response.message_list) return

  const incoming: IncomingMessage[] = await Promise.all(
    response.message_list.map(msg => convertApiMessage(msg, getAgentName))
  )

  store.upsertMany(incoming, 'db')
  store.markDbSynced()
}
```

**Note:** `convertApiMessage` is a simplified version of today's
`convertApiMessageToMessageData`. It produces an `IncomingMessage` with
`messageType: 'user' | 'agent'` — it does **not** perform the type-conversion logic
that currently lives in the `messagesQuery.queryFn` post-processing. That logic is now
handled by `resolveDisplayType` inside the store's write path.

### 2. SSE Events

Each SSE event type maps to an `upsertMessage` call. The handler becomes a thin adapter.

```ts
// hooks/useRoomSSEHandler.ts (extracted from useRoomWebhook handleSSEMessage)

case 'task_submitted': {
  const d = sseMessage.data!
  store.upsertMessage({
    id:          d.message_id!,
    roomId,
    messageType: 'agent',
    content:     '',
    senderName:  d.agent_name || await getAgentName(d.agent_id!),
    agentId:     d.agent_id,
    taskStatus:  (d.status as TaskState) || 'working',
    taskContent: d.task_content,
    stepNumber:  d.step_number,
    totalSteps:  d.total_steps,
    timestamp:   normalizeTimestampOrNow(d.created_at || sseMessage.timestamp),
  }, 'sse')

  // Remove processing placeholder — first real task arrived
  store.removeMessage(`processing-placeholder-${roomId}`)
  break
}

case 'task_update': {
  const d = sseMessage.data!
  store.upsertMessage({
    id:                d.message_id!,
    roomId,
    messageType:       'agent',
    content:           d.content || '',
    senderName:        d.agent_name || await getAgentName(d.agent_id!),
    agentId:           d.agent_id,
    taskStatus:        d.status as TaskState,
    taskError:         d.error || null,
    taskStatusMessage: d.status_message || null,
    taskRequiresInput: d.requires_input,
    taskRequiresAuth:  d.requires_auth,
    taskContent:       d.task_content,
    timestamp:         normalizeTimestampOrNow(d.created_at || sseMessage.timestamp),
  }, 'sse')
  break
}

case 'user_message': {
  const d = sseMessage.data!
  if (d.content) {
    store.upsertMessage({
      id:          d.message_id || `sse-${Date.now()}`,
      roomId,
      messageType: 'user',
      content:     d.content,
      senderName:  d.user_id || 'User',
      userId:      d.user_id,
      timestamp:   normalizeTimestampOrNow(sseMessage.timestamp),
    }, 'sse')
  }
  break
}

case 'agent_response': {
  const d = sseMessage.data!
  if (d.content !== undefined && d.agent_id) {
    store.upsertMessage({
      id:          d.message_id || `sse-agent-${Date.now()}`,
      roomId,
      messageType: 'agent',
      content:     d.content,
      senderName:  await getAgentName(d.agent_id),
      agentId:     d.agent_id,
      timestamp:   normalizeTimestampOrNow(sseMessage.timestamp),
    }, 'sse')
  }
  break
}
```

**What moved out of the SSE handler:** All display-type logic, component selection,
and state management side effects (`setProcessing`, `setCancelling`, etc.) are no longer
entangled with message writes. Processing/cancellation state remains in
`useRoomUiStore` and is updated by a separate concern.

### 3. Optimistic User Input

When the user sends a message, an optimistic entity is inserted immediately:

```ts
// In sendUserMessage:

// Step 1: Insert optimistic user message
const tempId = `temp-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`
store.upsertMessage({
  id:          tempId,
  roomId,
  messageType: 'user',
  content:     userInput,
  senderName:  userName,
  userId,
  timestamp:   new Date().toISOString(),
}, 'optimistic')

// Step 2: Insert ephemeral processing placeholder
store.upsertMessage({
  id:          `processing-placeholder-${roomId}`,
  roomId,
  messageType: 'agent',
  content:     '',
  senderName:  'HYBRO AI',
  taskStatus:  'working' as TaskState,
  taskContent: 'Processing your request...',
  timestamp:   new Date(Date.now() + 1).toISOString(),
  isEphemeral: true,
}, 'optimistic')

// Step 3: After backend confirms with real message_id:
store.removeMessage(tempId)
store.upsertMessage({
  id:          realMessageId,  // from SendMessage response
  roomId,
  messageType: 'user',
  content:     userInput,
  senderName:  userName,
  userId,
  timestamp:   new Date().toISOString(),
}, 'optimistic')
```

### 4. Reconciliation — Silent DB Sync

The current "belt-and-suspenders" refetch is replaced by a **conditional, non-disruptive
reconciliation**. It only runs when there is evidence of data loss, and it uses
`upsertMany` which applies per-entity conflict resolution.

```ts
// hooks/useRoomData.ts

async function reconcileWithDb(roomId: string, getToken: GetTokenFn) {
  const response = await inquiryRoomMessagesByRoomId(roomId, getToken)
  if (!response.success || !response.message_list) return

  const incoming: IncomingMessage[] = await Promise.all(
    response.message_list.map(msg => convertApiMessage(msg, getAgentName))
  )

  // upsertMany applies conflict resolution per-entity:
  // - Messages that haven't changed → isNoOpUpdate → no re-render
  // - Messages that changed → entity updates → only that message re-renders
  // - New messages from DB (SSE missed) → inserted into sorted position
  const store = useMessageStore.getState()
  store.upsertMany(incoming, 'db')
  store.markDbSynced()
}
```

**When to reconcile:**

```ts
// In the processing_status handler:

case 'processing_status': {
  if (isProcessingDone(status)) {
    setProcessing(false)
    setCancelling(false)

    // Only reconcile if SSE had a gap during this processing cycle
    const store = useMessageStore.getState()
    if (store.sseGapDetected) {
      setTimeout(() => reconcileWithDb(roomId, getToken), 1500)
      store.setSseGapDetected(false)
    }
    // Otherwise: SSE was connected the entire time → store is already complete
  }
  break
}
```

**Detecting SSE gaps:**

```ts
// In useRoomSSE, when reconnection occurs:
onClose: () => {
  // If we were processing when SSE disconnected, flag it
  const { processing } = useRoomUiStore.getState()
  if (processing) {
    useMessageStore.getState().setSseGapDetected(true)
  }
}
```

This means the reconciliation refetch **only fires when needed** (SSE dropped during
processing), and even when it does fire, the per-entity upsert logic ensures only
genuinely changed messages trigger re-renders.

---

## Reading from the Store — Selector Hooks

Components never read the raw store. They use selector hooks that only re-render when
their specific slice changes.

```ts
// hooks/useRoomMessages.ts

import { useMessageStore } from '@/stores/message-store'

/** Ordered message IDs only. Only re-renders when IDs are added/removed/reordered. */
export function useOrderedIds(): string[] {
  return useMessageStore(s => s.orderedIds)
}

/** Full ordered message list (convenience). Re-renders on any entity change —
 *  use for derived computations (e.g. lastAgentMessageId), NOT for the primary render path. */
export function useOrderedMessages(): MessageEntity[] {
  return useMessageStore(
    useShallow(s => s.orderedIds.map(id => s.entities[id]).filter(Boolean))
  )
}

/** Single message by ID. Only re-renders when that specific entity changes. */
export function useMessage(id: string): MessageEntity | undefined {
  return useMessageStore(s => s.entities[id])
}

/** Message count only (for auto-scroll logic). */
export function useMessageCount(): number {
  return useMessageStore(s => s.orderedIds.length)
}

/** Whether initial DB load is complete. */
export function useMessagesHydrated(): boolean {
  return useMessageStore(s => s.hydratedFromDb)
}
```

The `useOrderedIds` selector returns a string array that only changes when
messages are added, removed, or reordered (because `orderedIds` is rebuilt via
`buildSortedIds` only when `idsChanged` is true). Entity updates to existing
messages do not change `orderedIds`, so the parent `RoomMessages` does not
re-render. See Gap 8 for the rationale behind this split.

---

## Updated Component Rendering

### `RoomMessages` — list component

The list component becomes simpler. It maps over `orderedIds` and renders a thin
`MemoizedMessage` wrapper for each. The wrapper subscribes to its own entity via
`useMessage(id)` — so when reconciliation updates message #5 but not #1–4 and #6–10,
**only message #5 re-renders**.

```tsx
// components/room-messages.tsx (updated)

export function RoomMessages({ onQuote }: { onQuote?: (data: QuoteData) => void }) {
  const orderedIds = useOrderedIds()
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const [shouldAutoScroll, setShouldAutoScroll] = useState(true)

  // ... existing scroll-tracking logic (handleScroll, checkIfNearBottom) ...

  if (!useMessagesHydrated()) {
    return <LoadingState />
  }

  return (
    <div className="h-full flex relative">
      <div
        ref={scrollContainerRef}
        data-message-scroll-container="true"
        onScroll={handleScroll}
        className="flex-1 h-full w-full overflow-y-auto"
      >
        <div className="py-4 min-h-full px-4 sm:px-6 max-w-4xl mx-auto">
          {orderedIds.length === 0 ? (
            <EmptyState />
          ) : (
            <>
              {/* Expand/collapse pill — unchanged */}
              <div className="space-y-4">
                {orderedIds.map(id => (
                  <MemoizedMessage
                    key={id}
                    id={id}
                    onQuote={onQuote}
                  />
                ))}
              </div>
              <div ref={messagesEndRef} className="h-4" />
            </>
          )}
        </div>
      </div>
    </div>
  )
}
```

### `MemoizedMessage` — per-message subscriber

```tsx
const MemoizedMessage = React.memo(function MemoizedMessage({
  id,
  onQuote,
}: {
  id: string
  onQuote?: (data: QuoteData) => void
}) {
  const entity = useMessage(id)
  if (!entity) return null

  switch (entity.displayType) {
    case 'user-bubble':
      return <UserMessageBubble message={entity} />

    case 'agent-bubble':
      return (
        <AgentMessageBubble
          message={entity}
          onQuote={onQuote}
          // expand/collapse props derived from entity or parent context
        />
      )

    case 'task-status':
      return (
        <TaskStatusMessage
          internalId={entity.id}
          agentId={entity.agentId}
          agentName={entity.senderName}
          initialStatus={(entity.taskStatus || 'working') as TaskState}
          content={entity.content || null}
          error={entity.taskError}
          statusMessage={entity.taskStatusMessage}
          stepNumber={entity.stepNumber}
          totalSteps={entity.totalSteps}
          taskContent={entity.taskContent}
          taskCreatedAt={entity.taskCreatedAt || entity.timestamp}
        />
      )
  }
})
```

**Key rendering improvement:** The `displayType` is already resolved and stored in the
entity. It does not change unless `upsertMessage` determines the rendering should
change (and in that case, it's because the message genuinely transitioned — e.g., a
working task completed). There are no surprise component swaps from reconciliation.

---

## Auto-Scroll — Clean Separation

The current auto-scroll logic depends on the `messages` array reference changing,
which fires on every refetch. The new design separates the signal for "new message
arrived" from "existing message updated."

```tsx
// In RoomMessages:

const messageCount = useMessageCount()
const prevCountRef = useRef(messageCount)

useEffect(() => {
  if (messageCount > prevCountRef.current) {
    // Genuinely new message(s) added
    const messages = useMessageStore.getState()
    const lastId = messages.orderedIds[messages.orderedIds.length - 1]
    const lastEntity = lastId ? messages.entities[lastId] : null

    if (lastEntity?.source === 'optimistic' && lastEntity.messageType === 'user') {
      // User just sent a message → always scroll to bottom
      messagesEndRef.current?.scrollIntoView({ behavior: 'auto' })
    } else if (shouldAutoScroll) {
      // Agent message arrived while user is near bottom → scroll
      messagesEndRef.current?.scrollIntoView({ behavior: 'auto' })
    }
    // Otherwise: reconciliation gap-fill or SSE backfill → don't scroll
  }

  prevCountRef.current = messageCount
}, [messageCount, shouldAutoScroll])
```

**Critical difference from today:** Entity updates (status changes, content fills) do
not change `messageCount`, so they **never** trigger auto-scroll. Only genuinely new
messages do. This completely eliminates the scroll jump for the reconciliation case.

---

## Design Review — Identified Gaps and Resolutions

The following issues were found during a cross-reference audit of the design against the
existing codebase. Each gap is documented with its resolution.

### Gap 1: Stale Task Detection at Hydration Time

**What the current code does:** The `messagesQuery.queryFn` (lines 348–422 of
`useRoomWebhook.ts`) runs stale-task detection on every DB fetch. Non-terminal tasks
older than 10 minutes are converted to `task_status: 'failed'` with a timeout error.
This prevents abandoned tasks from showing as perpetually "working."

**What the design missed:** The `hydrateFromDb` and `reconcileWithDb` functions call
`upsertMany(incoming, 'db')` with raw converted messages. There is no stale-task
detection in the write path.

**Resolution:** Add a `detectStaleTasks` post-processing step to `convertApiMessage`
or as a separate pass before `upsertMany`. This runs at hydration and reconciliation
time — the same places the current code runs it.

```ts
// stores/message-store/stale-detection.ts

const STALE_TASK_THRESHOLD_MS = 10 * 60 * 1000  // 10 minutes, matches backend

function detectAndMarkStaleTasks(messages: IncomingMessage[]): IncomingMessage[] {
  return messages.map(msg => {
    if (
      msg.messageType === 'agent' &&
      msg.taskStatus &&
      !isTerminalState(msg.taskStatus) &&
      isStale(msg.taskUpdatedAt || msg.timestamp, STALE_TASK_THRESHOLD_MS)
    ) {
      return {
        ...msg,
        taskStatus: 'failed' as TaskState,
        taskError: 'Task timed out — no updates received within the expected timeframe',
        content: msg.content || 'Task failed due to timeout',
      }
    }
    return msg
  })
}

// Usage in hydrateFromDb / reconcileWithDb:
const incoming = await Promise.all(response.message_list.map(convertApiMessage))
const withStaleDetection = detectAndMarkStaleTasks(incoming)
store.upsertMany(withStaleDetection, 'db')
```

### Gap 2: Cancellation Workflow — Batch Status Updates

**What the current code does:** When the user cancels, `cancelProcessing` (line 1037)
optimistically sets all non-terminal task entities to `task_status: 'canceled'`. When
the SSE `processing_status: canceled` event arrives (line 627), it also:
1. Inserts an ephemeral "Processing stopped by user" cancel-confirmation message.
2. Iterates both live messages **and** query-loaded messages to cancel non-terminal tasks
   (lines 642–668). The dual-iteration is needed because the current architecture
   splits messages across two stores.

**What the design missed:** The `MemoizedMessage` pattern and `upsertMessage` are
designed for individual updates. Batch-cancelling all non-terminal tasks in the store
needs an efficient batch-mutation operation. The current `cancelProcessing` also needs
to read the store to find non-terminal tasks.

**Resolution:** Add a `cancelAllNonTerminal` batch action to the store, and have the
cancellation/processing_status handlers use it.

```ts
// In useMessageStore:

cancelAllNonTerminal: (roomId: string) => set((state) => {
  let changed = false
  const newEntities = { ...state.entities }

  for (const [id, entity] of Object.entries(newEntities)) {
    if (
      entity.roomId === roomId &&
      entity.taskStatus &&
      !isTerminalState(entity.taskStatus) &&
      !entity.isEphemeral
    ) {
      newEntities[id] = {
        ...entity,
        taskStatus: 'canceled' as TaskState,
        displayType: 'task-status',  // re-resolve: canceled → task-status
        sourceVersion: entity.sourceVersion + 1,
        updatedAt: Date.now(),
      }
      changed = true
    }
  }

  return changed
    ? { entities: newEntities, version: state.version + 1 }
    : state
}),
```

The cancellation flow then becomes:

```ts
// In cancelProcessing:
await cancelMessage(messageId, getToken)
store.cancelAllNonTerminal(roomId)

// In processing_status CANCELED handler:
store.cancelAllNonTerminal(roomId)  // catches any remaining
store.upsertMessage({               // cancel confirmation message
  id: `cancel-confirm-${Date.now()}`,
  roomId,
  messageType: 'agent',
  content: 'Processing was stopped by the user.',
  senderName: 'System',
  taskStatus: 'canceled' as TaskState,
  taskContent: 'Processing stopped by user',
  timestamp: new Date().toISOString(),
  isEphemeral: true,
}, 'optimistic')
```

This replaces the current dual-store iteration pattern (live messages + query messages)
with a single store scan.

### Gap 3: Processing Placeholder Restore on Page Refresh

**What the current code does:** `useRoomWebhook` lines 501–559 check
`room.processing_message_id` after initial load. If the room has an active processing
state, and no task messages have arrived yet, and the user message is not stale (> 2
min), it re-inserts the processing placeholder. This handles the case where a user
refreshes mid-processing.

**What the design missed:** The `hydrateFromDb` function loads messages but does not
check room-level processing state. The placeholder restore logic has no equivalent.

**Resolution:** Add a `restoreProcessingState` step that runs after hydration, using
the room data:

```ts
// hooks/useRoomData.ts — after hydrateFromDb completes:

function restoreProcessingState(room: Room, roomId: string) {
  if (!room.processing_message_id) return

  const store = useMessageStore.getState()

  // Check if user message is stale (> 2 min)
  const triggerMsg = store.entities[room.processing_message_id]
  if (triggerMsg && isStale(triggerMsg.timestamp, 2 * 60 * 1000)) return

  // Check if task messages already exist
  const hasTaskEntities = Object.values(store.entities).some(
    e => e.roomId === roomId && e.displayType === 'task-status'
  )
  if (hasTaskEntities) return

  // Check if placeholder already exists
  const placeholderId = `processing-placeholder-${roomId}`
  if (store.entities[placeholderId]) return

  // Restore placeholder
  store.upsertMessage({
    id: placeholderId,
    roomId,
    messageType: 'agent',
    content: '',
    senderName: 'HYBRO AI',
    taskStatus: 'working' as TaskState,
    taskContent: 'Processing your request...',
    timestamp: new Date().toISOString(),
    isEphemeral: true,
  }, 'optimistic')

  // Restore processing state in UI store
  setProcessing(true)
  currentProcessingMessageId.current = room.processing_message_id
}
```

### Gap 4: Expand/Collapse State Management

**What the current code does:** `RoomMessages` maintains elaborate expand/collapse state
for agent message bubbles:
- `lastAgentMessageId` — the most recent agent bubble is auto-expanded.
- `autoCollapseVersion` — increments when a new agent bubble appears, causing older
  non-user-expanded bubbles to collapse.
- `collapseSignal` / `expandAll` — bulk expand/collapse toggle.
- `userExpandedIds` — tracks which messages the user explicitly expanded.

These are passed as props to each `MessageBubble` (`defaultExpanded`, `isLatestAgent`,
`collapseSignal`, `autoCollapseVersion`, `isUserExpanded`).

**What the design missed:** The `MemoizedMessage` component in the design only receives
`id` and `onQuote`. The expand/collapse mechanism requires parent-level state
(`lastAgentMessageId`, `collapseSignal`, `autoCollapseVersion`, `userExpandedIds`)
that must be passed through to each `AgentMessageBubble`.

**Resolution:** The expand/collapse state stays in `RoomMessages` as local React state
(it's pure UI state, not data state — it doesn't belong in the message store). The
`MemoizedMessage` wrapper needs to accept these props from the parent:

```tsx
// Updated MemoizedMessage:

interface MemoizedMessageProps {
  id: string
  isLatestAgent: boolean
  collapseSignal: number
  autoCollapseVersion: number
  isUserExpanded: boolean
  onUserToggle: (id: string, expanded: boolean) => void
  onQuote?: (data: QuoteData) => void
}

const MemoizedMessage = React.memo(function MemoizedMessage({
  id,
  isLatestAgent,
  collapseSignal,
  autoCollapseVersion,
  isUserExpanded,
  onUserToggle,
  onQuote,
}: MemoizedMessageProps) {
  const entity = useMessage(id)
  if (!entity) return null

  switch (entity.displayType) {
    case 'user-bubble':
      return <UserMessageBubble message={entity} />

    case 'agent-bubble':
      return (
        <AgentMessageBubble
          message={entity}
          defaultExpanded={isLatestAgent}
          collapseSignal={collapseSignal}
          autoCollapseVersion={autoCollapseVersion}
          isLatestAgent={isLatestAgent}
          isUserExpanded={isUserExpanded}
          onUserToggle={onUserToggle}
          onQuote={onQuote}
        />
      )

    case 'task-status':
      return <TaskStatusMessage /* ... */ />
  }
})
```

And in the `RoomMessages` render loop:

```tsx
{orderedIds.map(id => (
  <MemoizedMessage
    key={id}
    id={id}
    isLatestAgent={id === lastAgentMessageId}
    collapseSignal={collapseSignal}
    autoCollapseVersion={autoCollapseVersion}
    isUserExpanded={userExpandedIds.has(id)}
    onUserToggle={handleUserToggle}
    onQuote={onQuote}
  />
))}
```

**Note on `React.memo` effectiveness:** Passing `collapseSignal` and
`autoCollapseVersion` (which change globally) means `React.memo` won't prevent
re-renders for these prop changes. This is acceptable — the current code also
re-renders all message bubbles on these changes. The key win is that **entity data
updates** (from reconciliation) no longer cause re-renders, because the entity
reference only changes when `upsertMessage` determines a meaningful update occurred.
Collapse/expand changes are user-initiated and expected.

### Gap 5: Optimistic Rollback on Send Failure

**What the current code does:** When `sendUserMessage` fails (line 1008), it calls
`resetRoomLiveState(roomId)` to wipe all live messages, then refetches from DB. This
is a blunt recovery that throws away all live state.

**What the design missed:** The design's optimistic insert section (Section 7.3) does
not describe failure rollback.

**Resolution:** On send failure, remove only the specific optimistic messages:

```ts
// In sendUserMessage catch block:
store.removeMessage(tempId)                               // optimistic user message
store.removeMessage(`processing-placeholder-${roomId}`)   // processing placeholder

// Then reconcile to recover any messages that might have been lost:
await reconcileWithDb(roomId, getToken)
```

This is more targeted than the current `resetRoomLiveState` approach — it doesn't
destroy SSE-delivered messages from prior successful workflows.

### Gap 6: `displayType` Transitions During Task Lifecycle

**What the design says:** `displayType` is "resolved once at write time."

**Potential issue:** During a task's lifecycle, `displayType` legitimately transitions:
1. `task_submitted` → `displayType: 'task-status'` (working, no content)
2. `task_update` completed with content → `displayType: 'agent-bubble'`

This means `upsertMessage` for a task completion will change `displayType` from
`'task-status'` to `'agent-bubble'`, causing `MemoizedMessage` to swap from rendering
`TaskStatusMessage` to `AgentMessageBubble`.

**Why this is OK:** This transition is **expected and correct** — it happens when the
task genuinely completes during the live session. The user sees a task bubble turn
into an agent response. The key difference from the current bug is:
- **Current:** The same transition happens **again** 1.5s later during reconciliation,
  causing a redundant visual disruption.
- **New design:** The transition happens once (when SSE delivers `task_update`
  completed). The subsequent DB reconciliation sees the entity is already
  `displayType: 'agent-bubble'` and `isNoOpUpdate` returns true → no re-render.

**No code change needed**, but this should be documented as expected behavior in
`resolveDisplayType`.

### Gap 7: The `processing_status` Handler Does More Than Refetch

> **See also Gap 12** for the same issue with `task_update` terminal-state
> side effects.

**What the current code does:** The `processing_status: completed/canceled/failed`
handler (lines 610–684) performs many side effects beyond the refetch:
- Clears processing/cancelling state
- Clears cancel timeout safety net
- Removes processing placeholder
- Shows banners (info/error)
- Inserts cancel confirmation message (for canceled)
- Batch-cancels non-terminal tasks (for canceled)
- Tracks `placeholderDismissedRef` and `cancelTimedOutRef`

**What the design implies:** The SSE handler becomes a "thin adapter" that only calls
`store.upsertMessage`. But these side effects are processing-lifecycle concerns, not
message-data concerns.

**Resolution:** The `processing_status` handler continues to live in `useRoomWebhook`
(or a successor hook). It is **not** part of the message store. The handler:
1. Updates `useRoomUiStore` (processing, cancelling, banners).
2. Calls `store.removeMessage()` for the placeholder.
3. Calls `store.cancelAllNonTerminal()` for canceled status.
4. Calls `store.upsertMessage()` for the cancel confirmation.
5. Conditionally calls `reconcileWithDb()` if SSE gap detected.

The "thin adapter" description in Section 7.2 applies specifically to `task_submitted`,
`user_message`, and `agent_response` events. The `processing_status`,
`task_update` (when terminal — see Gap 12), `error`, and `heartbeat` events remain
richer handlers that coordinate between the message store and the UI state store.

### Gap 8: `useOrderedMessages` Selector Causes Unnecessary Parent Re-renders

**What the design proposes:**

```ts
export function useOrderedMessages(): MessageEntity[] {
  return useMessageStore(
    useShallow(s => s.orderedIds.map(id => s.entities[id]).filter(Boolean))
  )
}
```

**Potential issue:** This selector maps IDs to entity objects on every store
mutation. When any entity is updated via `upsertMessage`, that entity gets a new
object reference. `useShallow` compares the returned array element-by-element;
the changed reference at position N causes `useShallow` to report a new value,
triggering a re-render of `RoomMessages`. This means **every entity update
re-renders the parent** — even though `React.memo` on `MemoizedMessage`
prevents child re-renders (because `id` is a stable string and collapse props
haven't changed), the parent still runs its render function and iterates
`messages.map(...)` on every single store mutation.

This undermines the "granular updates" design goal (Goal #2).

**Resolution:** Split the list-level selector into a pure ID selector. The
parent `RoomMessages` subscribes only to the ordered ID list (which only changes
when messages are added or removed). Each `MemoizedMessage` fetches its own
entity via `useMessage(id)`.

```ts
/** Ordered message IDs only. Only re-renders when IDs are added/removed/reordered. */
export function useOrderedIds(): string[] {
  return useMessageStore(s => s.orderedIds)
}

/** Single message by ID. Only re-renders when that specific entity changes. */
export function useMessage(id: string): MessageEntity | undefined {
  return useMessageStore(s => s.entities[id])
}

/** Message count only (for auto-scroll logic). */
export function useMessageCount(): number {
  return useMessageStore(s => s.orderedIds.length)
}
```

And the `RoomMessages` render loop becomes:

```tsx
const orderedIds = useOrderedIds()

// ...

{orderedIds.map(id => (
  <MemoizedMessage
    key={id}
    id={id}
    isLatestAgent={id === lastAgentMessageId}
    collapseSignal={collapseSignal}
    autoCollapseVersion={autoCollapseVersion}
    isUserExpanded={userExpandedIds.has(id)}
    onUserToggle={handleUserToggle}
    onQuote={onQuote}
  />
))}
```

The existing `useOrderedMessages()` can be kept for convenience but should be
documented as intentionally non-granular (suitable for derived computations like
`lastAgentMessageId` where you need entity data, but not for the primary render
path).

### Gap 9: `isNoOpUpdate` — `null` vs `undefined` Confusion for Nullable Fields

**What the design proposes:**

```ts
existing.taskError === (incoming.taskError ?? existing.taskError)
```

**Potential issue:** `IncomingMessage` defines `taskError?: string | null`.
The JavaScript `??` (nullish coalescing) operator treats **both** `null` and
`undefined` as "not provided," falling back to `existing.taskError`. This means
`incoming.taskError = null` (intentionally clear the error) is
indistinguishable from `incoming.taskError = undefined` (field not provided) —
the no-op check always reports "no change" when trying to clear a nullable
field to `null`. The store would silently keep the stale error.

This affects `taskError`, `taskStatusMessage`, and any other `string | null`
field on `IncomingMessage`.

**Resolution:** Use explicit `undefined` checks for nullable fields in the
no-op comparison:

```ts
function isNoOpUpdate(
  existing: MessageEntity,
  incoming: IncomingMessage,
  source: MessageSource,
): boolean {
  // Helper: if incoming field is undefined, treat as "not changing" → use existing
  // If incoming field is explicitly null or a value, it IS a change candidate
  const coalesce = <T,>(incomingVal: T | undefined, existingVal: T): T =>
    incomingVal === undefined ? existingVal : incomingVal

  const incomingDisplayType = resolveDisplayType({
    messageType: incoming.messageType ?? existing.messageType,
    taskStatus: incoming.taskStatus ?? existing.taskStatus,
    content: incoming.content ?? existing.content,
  })

  return (
    existing.content        === coalesce(incoming.content, existing.content) &&
    existing.taskStatus     === coalesce(incoming.taskStatus, existing.taskStatus) &&
    existing.taskError      === coalesce(incoming.taskError, existing.taskError) &&
    existing.senderName     === coalesce(incoming.senderName, existing.senderName) &&
    existing.stepNumber     === coalesce(incoming.stepNumber, existing.stepNumber) &&
    existing.totalSteps     === coalesce(incoming.totalSteps, existing.totalSteps) &&
    existing.displayType    === incomingDisplayType
  )
}
```

The same `coalesce` approach must also be applied in the `applyUpsert` merge
(`...incoming` spread) for nullable fields. When `incoming.taskError` is
`undefined`, the spread must not overwrite the existing value with `undefined`;
when it is `null`, it must write `null`.

### Gap 10: Empty Agent Messages Without Task Status Could Leak Through

**What the current code does:** The `messagesQuery.queryFn` post-processing
(line 384–396) filters out agent messages that have: no content AND are
non-terminal AND are not recent tasks AND are not stale. This catches edge-case
agent messages that exist in the DB with empty content and no meaningful state.

**What the design missed:** The `hydrateFromDb` and `reconcileWithDb` functions
pass all converted messages straight to `upsertMany` without any filtering.

`resolveDisplayType` would resolve an agent message with no `taskStatus` and
empty `content` to `displayType: 'agent-bubble'` — resulting in an empty agent
bubble in the UI.

**Resolution:** Add a validation pass before `upsertMany` at hydration and
reconciliation time that filters or annotates empty, content-less agent messages:

```ts
function filterHydrationMessages(messages: IncomingMessage[]): IncomingMessage[] {
  return messages.filter(msg => {
    // Always keep user messages
    if (msg.messageType === 'user') return true

    // Agent messages must have content OR a meaningful task status
    const hasContent = msg.content && msg.content.trim().length > 0
    const hasTaskStatus = !!msg.taskStatus

    return hasContent || hasTaskStatus
  })
}

// Usage in hydrateFromDb / reconcileWithDb:
const incoming = await Promise.all(response.message_list.map(convertApiMessage))
const withStaleDetection = detectAndMarkStaleTasks(incoming)
const filtered = filterHydrationMessages(withStaleDetection)
store.upsertMany(filtered, 'db')
```

### Gap 11: Cancellation Timeout Safety Net Not Addressed

**What the current code does:** After calling `cancelMessage()`, the handler
starts a 15-second timeout (line 1079):

```ts
cancelTimeoutRef.current = setTimeout(() => {
  const { cancelling } = useRoomUiStore.getState()
  if (cancelling) {
    cancelTimedOutRef.current = true   // suppress future SSE banners
    setCancelling(false)
    setProcessing(false)
    banner.warning('Cancellation timed out — the agent may still be running')
  }
}, 15000)
```

When the timeout fires before the SSE `processing_status: canceled` event
arrives, it:
1. Sets `cancelTimedOutRef = true` — this flag gates all subsequent banner
   calls in `processing_status` and `task_update` handlers.
2. Clears `cancelling` and `processing` state.
3. Shows a "timed out" warning.

The design's Gap 2 covers `cancelAllNonTerminal` but does not mention this
timeout safety net or the `cancelTimedOutRef` flag that suppresses duplicate
banners.

**Resolution:** The 15-second timeout, `cancelTimeoutRef`, and
`cancelTimedOutRef` remain in the hook (not in the message store — they are
pure UI-lifecycle concerns). They must be preserved alongside the cancel flow:

```ts
// In cancelProcessing:
await cancelMessage(messageId, getToken)
store.cancelAllNonTerminal(roomId)

// Start 15s safety net (unchanged from current)
cancelTimeoutRef.current = setTimeout(() => {
  const { cancelling } = useRoomUiStore.getState()
  if (cancelling) {
    cancelTimedOutRef.current = true
    setCancelling(false)
    setProcessing(false)
    banner.warning('Cancellation timed out — the agent may still be running')
  }
}, 15000)

// In processing_status and task_update handlers:
// Gate banner calls behind `if (!cancelTimedOutRef.current)` — same as today
```

### Gap 12: `task_update` Terminal State Has Side Effects Beyond Store Write

**What the current code does:** When a `task_update` SSE event arrives with a
terminal status (lines 806–828 of `useRoomWebhook.ts`), the handler also:
1. Calls `setProcessing(false)` and `setCancelling(false)`
2. Clears the cancellation timeout safety net
3. Shows error/rejection banners (gated by `cancelTimedOutRef`)
4. Resets `cancelTimedOutRef`

**What the design implies:** Section 7.2 describes SSE handlers as "thin
adapters" that only call `store.upsertMessage()`. This is accurate for
`task_submitted`, `user_message`, and `agent_response`. But `task_update`
with a terminal status is a **rich handler** — it must also coordinate UI
state, just like `processing_status`.

**Resolution:** Document that `task_update` is a dual-concern handler:

```ts
case 'task_update': {
  // 1. Data concern → write to normalized store
  store.upsertMessage({ /* ... */ }, 'sse')

  // 2. UI concern → if terminal, coordinate processing state
  if (isTerminalState(status)) {
    setProcessing(false)
    setCancelling(false)
    if (cancelTimeoutRef.current) {
      clearTimeout(cancelTimeoutRef.current)
      cancelTimeoutRef.current = null
    }
    if (!cancelTimedOutRef.current) {
      if (status === 'failed') banner.error(error || 'Task failed')
      if (status === 'rejected') banner.error(error || 'Task was rejected')
    }
    cancelTimedOutRef.current = false
  }
  break
}
```

The "thin adapter" description in Section 7.2 should be updated to exclude
`task_update` terminal cases.

### Gap 13: No Room-Switch Caching — Performance Regression

**What the current system does:** React Query caches `messagesQuery.data` per
`queryKey`. When the user navigates away from room A to room B and back to
room A, React Query serves the cached messages immediately (stale while
revalidating). The user sees messages instantly on re-entry.

**What the design proposes:** `setRoom(roomId)` wipes all entities, and
`hydrateFromDb` performs a fresh fetch. Navigating back to room A requires a
full DB fetch with a loading spinner.

**Impact:** Users who frequently switch between rooms (e.g., monitoring
multiple agent conversations) would see a loading flash on every switch.

**Resolution options:**

1. **Accept the regression** — if room switching is rare, a single fetch
   per entry is acceptable. Document this as a known trade-off.

2. **Per-room LRU cache** — change the store to hold
   `rooms: Record<RoomId, { entities, orderedIds, ... }>` with an LRU eviction
   policy (e.g., keep the 3 most recent rooms). `setRoom` swaps the active
   slice instead of wiping.

3. **Hybrid approach** — keep a lightweight React Query cache alongside the
   store, used only to pre-populate `hydrateFromDb` with stale data before the
   fresh fetch arrives.

Recommended: **Option 1** (accept) for the initial implementation, with a note
to revisit if user feedback indicates room switching is a frequent workflow.

### Gap 14: Manual `refreshMessages` Not Replicated

**What the current code does:** The hook exposes `refreshMessages()` which
calls `messagesQuery.refetch()`. This is returned as part of the hook's public
API (line 1184 of `useRoomWebhook.ts`).

**What the design missed:** With React Query removed, there is no equivalent
programmatic refresh function.

**Resolution:** Add a `refreshMessages` function that delegates to
`reconcileWithDb`:

```ts
const refreshMessages = useCallback(async () => {
  console.log('🔄 Manual message refresh requested')
  await reconcileWithDb(roomId, getToken)
}, [roomId, getToken])
```

This preserves the existing API contract while using the new non-disruptive
reconciliation path.

### Gap 15: Derived Expand/Collapse State Stale After `displayType` Transitions

**Found during:** Post-implementation review of Steps 1–4.

**What the implementation does:** In `RoomMessages`, `allAgentIds` and
`lastAgentMessageId` are computed via `useMemo` that calls
`useMessageStore.getState()` inside the memo body, with `[orderedIds]` as
the sole dependency:

```ts
const allAgentIds = useMemo(() => {
  const store = useMessageStore.getState()
  return orderedIds.filter(id => {
    const e = store.entities[id]
    return e && (e.displayType === 'agent-bubble')
  })
}, [orderedIds])
```

**The problem:** When a task completes and its `displayType` transitions from
`'task-status'` to `'agent-bubble'`, `orderedIds` does **not** change (it is
only rebuilt when messages are added/removed). Therefore the `useMemo` does
not recompute. The completed task is rendered correctly (because
`MemoizedMessage` subscribes to its own entity), but:

- It is not counted in `allAgentIds` → the expand/collapse pill is wrong.
- It is not found by `lastAgentMessageId` → auto-expand doesn't fire.

**Impact:** Medium. The user sees the correct component, but expand/collapse
behavior is stale until another message is added.

**Resolution:** Subscribe to the store's `version` counter as an additional
dependency to force recomputation on any entity change:

```ts
const storeVersion = useMessageStore(s => s.version)

const allAgentIds = useMemo(() => {
  const store = useMessageStore.getState()
  return orderedIds.filter(id => {
    const e = store.entities[id]
    return e && (e.displayType === 'agent-bubble')
  })
}, [orderedIds, storeVersion])

const lastAgentMessageId = useMemo(() => {
  const store = useMessageStore.getState()
  for (let i = orderedIds.length - 1; i >= 0; i--) {
    const e = store.entities[orderedIds[i]]
    if (e && e.displayType === 'agent-bubble') return orderedIds[i]
  }
  return null
}, [orderedIds, storeVersion])
```

This causes the parent to re-run these memos on every store mutation, but
since the memos are cheap (array filter + property check), and `React.memo`
on `MemoizedMessage` prevents child re-renders when props haven't changed,
the performance cost is negligible. The alternative — a dedicated selector
that returns only agent-bubble IDs — would be more complex without a
meaningful gain.

### Gap 16: SSE `task_update` Handler Sends `null` Instead of `undefined` for Missing Fields

**Found during:** Post-implementation review of Steps 1–4.

**What the implementation does:** In the `task_update` SSE handler:

```ts
taskError: sseMessage.data.error || null,
taskStatusMessage: sseMessage.data.status_message || null,
```

**The problem:** When `sseMessage.data.error` is `undefined` (field not
present in the SSE payload), `undefined || null` evaluates to `null`. The
store's `mergeIncoming` treats `null` as "explicitly clear this field" — it
will wipe out an existing `taskError` on the entity. This means a
`task_update` event that doesn't carry error information will erase any
previously stored error.

The same applies to `taskStatusMessage`.

**Impact:** Medium. During a `task_update` that only carries content or
status (no error field), existing error/statusMessage values on the entity
are incorrectly cleared.

**Resolution:** Use undefined-preserving coalescing:

```ts
case 'task_update': {
  // ...
  store.upsertMessage({
    // ...
    taskError: sseMessage.data.error !== undefined ? (sseMessage.data.error || null) : undefined,
    taskStatusMessage: sseMessage.data.status_message !== undefined
      ? (sseMessage.data.status_message || null)
      : undefined,
    taskRequiresInput: sseMessage.data.requires_input,   // pass through as-is
    taskRequiresAuth: sseMessage.data.requires_auth,     // pass through as-is
    // ...
  }, 'sse')
}
```

The rule: if the SSE payload doesn't include a field, pass `undefined` so
`mergeIncoming` preserves the existing value. If it explicitly includes the
field (even as empty string or `null`), pass a value so the store updates.

### Gap 17: `taskRequiresInput` / `taskRequiresAuth` Coerced to `false` When Absent

**Found during:** Post-implementation review of Steps 1–4.

**What the implementation does:** In the `task_update` SSE handler:

```ts
taskRequiresInput: sseMessage.data.requires_input || false,
taskRequiresAuth: sseMessage.data.requires_auth || false,
```

**The problem:** When `sseMessage.data.requires_input` is `undefined` (the
SSE payload doesn't include this field), `undefined || false` evaluates to
`false`. This explicit `false` overwrites any existing `true` value on the
entity via `mergeIncoming`, because `false !== undefined`.

This could prematurely clear an `input_required` state if a subsequent
`task_update` arrives without these fields.

**Impact:** Medium-low. Only affects cases where a task is in
`input_required` state and a follow-up update event arrives without the
`requires_input` field.

**Resolution:** Pass through as-is (see Gap 16 resolution). `undefined`
will be preserved by `mergeIncoming` as "not provided."

### Gap 18: `isNoOpUpdate` Missing Several Rendering-Visible Fields

**Found during:** Post-implementation review of Steps 1–4.

**What the implementation does:** `isNoOpUpdate` compares: `content`,
`taskStatus`, `taskError`, `senderName`, `stepNumber`, `totalSteps`, and
`displayType`.

**What it misses:**

| Field | Why it matters |
|-------|---------------|
| `taskStatusMessage` | Displayed as status detail in `TaskStatusMessage` component |
| `taskContent` | Displayed as the task description in `TaskStatusMessage` |
| `taskRequiresInput` | Affects rendering of input-required UI in `TaskStatusMessage` |
| `taskRequiresAuth` | Affects rendering of auth-required UI in `TaskStatusMessage` |

**Impact:** Medium. If a `task_update` changes only `taskStatusMessage` or
`taskContent` without changing `taskStatus` or `content`, `isNoOpUpdate`
returns `true` and the update is silently dropped. The UI won't reflect
the change.

**Resolution:** Add the missing fields to the comparison:

```ts
function isNoOpUpdate(
  existing: MessageEntity,
  incoming: IncomingMessage,
  _source: MessageSource,
): boolean {
  const incomingDisplayType = resolveDisplayType({
    messageType: incoming.messageType ?? existing.messageType,
    taskStatus: coalesce(incoming.taskStatus, existing.taskStatus) as TaskState | undefined,
    content: incoming.content ?? existing.content,
  })

  return (
    existing.content           === coalesce(incoming.content, existing.content) &&
    existing.taskStatus        === coalesce(incoming.taskStatus, existing.taskStatus) &&
    existing.taskError         === coalesce(incoming.taskError, existing.taskError) &&
    existing.taskStatusMessage === coalesce(incoming.taskStatusMessage, existing.taskStatusMessage) &&
    existing.senderName        === coalesce(incoming.senderName, existing.senderName) &&
    existing.stepNumber        === coalesce(incoming.stepNumber, existing.stepNumber) &&
    existing.totalSteps        === coalesce(incoming.totalSteps, existing.totalSteps) &&
    existing.taskContent       === coalesce(incoming.taskContent, existing.taskContent) &&
    existing.taskRequiresInput === coalesce(incoming.taskRequiresInput, existing.taskRequiresInput) &&
    existing.taskRequiresAuth  === coalesce(incoming.taskRequiresAuth, existing.taskRequiresAuth) &&
    existing.displayType       === incomingDisplayType
  )
}
```

### Gap 19: `cancelAllNonTerminal` Hardcodes `displayType` Instead of Calling `resolveDisplayType`

**Found during:** Post-implementation review of Steps 1–4.

**What the implementation does:** In the store's `cancelAllNonTerminal`
action:

```ts
newEntities[id] = {
  ...entity,
  taskStatus: 'canceled' as TaskState,
  displayType: 'task-status',  // hardcoded
  sourceVersion: entity.sourceVersion + 1,
  updatedAt: Date.now(),
}
```

**The problem:** This hardcodes `displayType: 'task-status'` instead of
calling `resolveDisplayType()`. While `canceled` status does currently
produce `'task-status'`, this is fragile — it bypasses the "single source
of rendering truth" principle. If `resolveDisplayType` logic ever changes
(e.g., special handling for canceled tasks with content), this hardcoded
value would be wrong.

**Impact:** Low. Functionally correct today, but inconsistent with the
design principle.

**Resolution:** Call `resolveDisplayType`:

```ts
import { resolveDisplayType } from './resolve-display-type'

cancelAllNonTerminal: (roomId) => set((state) => {
  // ...
  newEntities[id] = {
    ...entity,
    taskStatus: 'canceled' as TaskState,
    displayType: resolveDisplayType({
      messageType: entity.messageType,
      taskStatus: 'canceled' as TaskState,
      content: entity.content,
      isEphemeral: entity.isEphemeral,
    }),
    sourceVersion: entity.sourceVersion + 1,
    updatedAt: Date.now(),
  }
  // ...
})
```

### Gap 20: `convertApiMessageToIncoming` Cannot Clear Errors from DB

**Found during:** Post-implementation review of Steps 1–4.

**What the implementation does:** In `convert-api-message.ts`:

```ts
taskError: taskError ?? undefined,
```

When `extractTaskError` returns a falsy value (no error found),
`taskError` is set to `undefined`. The store's `mergeIncoming` treats
`undefined` as "not provided" → preserves the existing value. This means
if a task entity previously had a `taskError` (e.g., from an SSE event),
and the DB version of the same message has no error, the DB reconciliation
**cannot** clear the stale error.

**Impact:** Medium-low. In practice, terminal tasks rarely have their
error cleared by the backend. The most likely scenario is a task that
failed and was later retried to completion — the DB would have
`taskStatus: 'completed'` with no error, but the entity would retain the
stale error string from the failed attempt.

**Resolution:** When `extractTaskError` returns falsy for an agent message
with a task, explicitly pass `null` to signal "clear the error":

```ts
const messageTask = apiMessage.message_content?.message_task
// ...
taskError: messageTask
  ? (taskError || null)    // task present → null means "no error"
  : undefined,             // no task → don't touch the field
```

This way, DB reconciliation for task messages can clear stale errors while
non-task agent messages don't interfere.

---

## Migration Path

This is not a big-bang rewrite. The migration is incremental — each step can be shipped
independently, and steps 1–3 are zero-risk because they don't change the UI.

### Step 1: Create the store and write tests

**Files created:**
- `stores/message-store/types.ts` — `MessageEntity`, `IncomingMessage`, `MessageSource`, `DisplayType`
- `stores/message-store/resolve-display-type.ts` — `resolveDisplayType()`
- `stores/message-store/upsert.ts` — `applyUpsert()`, `isNoOpUpdate()`, `buildSortedIds()`
- `stores/message-store/index.ts` — `useMessageStore` Zustand store
- `hooks/useRoomMessages.ts` — selector hooks

**Tests:**
- Conflict resolution rules (all 5 rules, positive and negative cases)
- `resolveDisplayType` for all message/task state combinations
- `isNoOpUpdate` correctly detects no-ops vs. real changes
- `buildSortedIds` produces correct order for mixed timestamp/step scenarios
- `upsertMany` batches correctly and only triggers one state update

**Risk:** None. No existing code is modified.

### Step 2: Wire SSE handlers to write to new store (dual-write)

**Files modified:**
- `hooks/useRoomWebhook.ts` — in `handleSSEMessage`, add `store.upsertMessage()`
  calls alongside existing `addLiveMessage` / `replaceLiveMessage` calls.

**Verification:** Log diffs between the old live messages and the new store entities
to confirm parity. The UI still reads from the old path.

**Risk:** Low. The new store is a write-only shadow; nothing reads from it yet.

### Step 3: Wire DB fetch to write to new store (dual-write)

**Files modified:**
- `hooks/useRoomWebhook.ts` — after `messagesQuery.queryFn` resolves, also call
  `store.upsertMany(incoming, 'db')`.

**Verification:** After page load and after reconciliation refetch, compare store
contents with `messagesQuery.data` to confirm parity.

**Risk:** Low. Still a shadow store.

### Step 4: Switch rendering to read from new store

**Files modified:**
- `components/room-messages.tsx` — replace `messages` prop with `useOrderedMessages()`
  selector hook. Add `MemoizedMessage` per-message component.
- `app/c/room/[id]/page.tsx` — stop passing `messages` prop to `RoomMessages`.
- `hooks/useRoomWebhook.ts` — remove the `messages` useMemo, the
  `liveMessagesByRoom` reads, and the `messagesQuery` React Query hook.

**Files removed / cleaned:**
- `liveMessagesByRoom` slice from `stores/room-ui-store.ts` (along with
  `addLiveMessage`, `replaceLiveMessage`, `removeLiveMessage`)
- The SSE handler no longer calls old Zustand live message methods
- Remove `messagesQuery` (React Query) entirely from `useRoomWebhook`

**Risk:** Medium. This is the swap-over. Feature-flag it if needed:

```ts
const USE_NORMALIZED_STORE = process.env.NEXT_PUBLIC_USE_NORMALIZED_STORE === 'true'

// In RoomMessages:
const messages = USE_NORMALIZED_STORE
  ? useOrderedMessages()
  : props.messages  // old path
```

### Step 5: Replace automatic refetch with conditional reconciliation

**Files modified:**
- `hooks/useRoomWebhook.ts` — remove the `setTimeout(() => refetch(), 1500)` in the
  `processing_status` handler. Replace with conditional `reconcileWithDb()` gated
  on `sseGapDetected`.
- `hooks/useRoomSSE.ts` — set `sseGapDetected = true` on SSE close during processing.

**Risk:** Low after Step 4 is stable. The refetch was a safety net for the old
dual-source model. The normalized store's conflict resolution makes it unnecessary
in the happy path.

---

## Files Inventory

Summary of all files created, modified, and removed:

| Action | File | Description |
|--------|------|-------------|
| **Create** | `stores/message-store/types.ts` | `MessageEntity`, `IncomingMessage`, type definitions |
| **Create** | `stores/message-store/resolve-display-type.ts` | `resolveDisplayType()` |
| **Create** | `stores/message-store/upsert.ts` | `applyUpsert()`, `isNoOpUpdate()`, `buildSortedIds()` |
| **Create** | `stores/message-store/stale-detection.ts` | `detectAndMarkStaleTasks()` (Gap 1) |
| **Create** | `stores/message-store/hydration-filter.ts` | `filterHydrationMessages()` (Gap 10) |
| **Create** | `stores/message-store/index.ts` | `useMessageStore` Zustand store (incl. `cancelAllNonTerminal` from Gap 2) |
| **Create** | `hooks/useRoomMessages.ts` | Selector hooks (`useOrderedIds`, `useOrderedMessages`, `useMessage`, etc.) (Gap 8) |
| **Create** | `hooks/useRoomData.ts` | `hydrateFromDb()`, `reconcileWithDb()`, `restoreProcessingState()`, `refreshMessages()` (Gap 3, Gap 14) |
| **Modify** | `hooks/useRoomWebhook.ts` | Remove `messagesQuery`, `messages` memo, live message calls; add store writes; keep processing/cancellation lifecycle handlers (incl. cancel timeout safety net from Gap 11, `task_update` terminal side effects from Gap 12) |
| **Modify** | `components/room-messages.tsx` | Read from store selectors via `useOrderedIds`; add `MemoizedMessage` with expand/collapse props (Gap 4, Gap 8) |
| **Modify** | `app/c/room/[id]/page.tsx` | Remove `messages` prop pass-through |
| **Modify** | `hooks/useRoomSSE.ts` | Set `sseGapDetected` on disconnect during processing |
| **Modify** | `stores/room-ui-store.ts` | Remove `liveMessagesByRoom` slice and related methods |

---

## Summary

| Current Problem | How This Design Fixes It |
|-----------------|--------------------------|
| Scroll jump on post-processing refetch | Reconciliation is per-entity upsert; unchanged messages don't re-render. `messageCount`-based scroll only triggers for genuinely new messages. |
| Component type flicker (Task → Agent swap) | `displayType` resolved once at write time via `resolveDisplayType()`. Both SSE and DB produce the same value for the same state. Transitions happen once during live SSE, not again during reconciliation (Gap 6). |
| Full list re-render on any change | `useOrderedIds()` selector + per-message `useMessage(id)` selectors via `MemoizedMessage` isolate re-renders. Parent only re-renders when messages are added/removed (Gap 8). |
| Dual source of truth (React Query + Zustand) | Single normalized Zustand store. Single `upsertMessage` write path with conflict resolution. |
| "Belt-and-suspenders" refetch always needed | Reconciliation only fires after detected SSE gaps. Conflict resolution makes it non-disruptive even when it does fire. |
| Agent name in query key causing refetches | Agent names resolved at write time and stored in entity. No query key dependency. |
| Scattered type-conversion logic | `resolveDisplayType()` is the single source of truth, replacing 3 scattered locations. |
| Stale tasks shown as perpetually "working" | `detectAndMarkStaleTasks()` runs at hydration and reconciliation time (Gap 1). |
| Cancellation requires scanning two stores | `cancelAllNonTerminal()` store action scans one normalized store (Gap 2). |
| Processing placeholder lost on page refresh | `restoreProcessingState()` runs after hydration, using `room.processing_message_id` (Gap 3). |
| Blunt `resetRoomLiveState` on send failure | Targeted removal of specific optimistic messages + reconciliation (Gap 5). |
| Nullable field clearing treated as no-op | `isNoOpUpdate` uses `undefined`-only checks to distinguish "field not provided" from "field set to null" (Gap 9). |
| Empty agent messages from DB showing as blank bubbles | `filterHydrationMessages()` drops content-less agent messages without task status (Gap 10). |
| Cancellation timeout safety net lost | 15-second timeout + `cancelTimedOutRef` banner suppression preserved in hook (Gap 11). |
| `task_update` terminal side effects omitted | Handler explicitly coordinates UI state on terminal task updates (Gap 12). |
| Manual `refreshMessages` API removed | `refreshMessages()` delegates to `reconcileWithDb()` (Gap 14). |
| Expand/collapse stale after `displayType` transition | `allAgentIds` / `lastAgentMessageId` subscribe to store `version` to recompute when entities change (Gap 15). |
| SSE handler clears existing errors when field is absent | `task_update` uses `undefined`-preserving coalescing instead of `|| null` for nullable fields (Gap 16). |
| `taskRequiresInput/Auth` reset to `false` when SSE omits field | Pass through raw SSE values instead of coercing `undefined` to `false` (Gap 17). |
| `isNoOpUpdate` misses rendering-visible fields | `taskStatusMessage`, `taskContent`, `taskRequiresInput`, `taskRequiresAuth` added to comparison (Gap 18). |
| `cancelAllNonTerminal` bypasses `resolveDisplayType` | Calls `resolveDisplayType()` instead of hardcoding `'task-status'` (Gap 19). |
| DB reconciliation cannot clear stale errors | `convertApiMessageToIncoming` sends `null` for task messages without errors (Gap 20). |
