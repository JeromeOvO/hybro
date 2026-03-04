# Message Pagination Design — Paginated Message Loading

> **Status: Not Started** — Design approved, pending implementation. Requires backend pagination API.

**Depends on**: Backend pagination API (contract specified in this document)
**Decoupled from**: All other frontend design docs

---

## 1. Problem Statement

Both the backend `inquiryRoomMessagesByRoomId` endpoint and the frontend API client
load ALL messages for a room in a single request with no pagination. The backend
fetches every user message and every agent message from MongoDB, combines them, sorts
by `message_created_at`, and returns the full array.

For rooms with long conversation histories (hundreds or thousands of messages), this
causes:

- **Slow initial load**: The backend query and network transfer grow linearly with
  message count. A room with 500 messages could take 2-5 seconds to load.
- **High memory usage**: All messages are deserialized and stored in the frontend
  message store simultaneously.
- **Wasted bandwidth**: Users typically only need to see the most recent messages on
  room entry. Older messages are rarely accessed.

---

## 2. Current State

### Backend (`hybro-multi-agents-backend`)

**Endpoint**: `POST /roomCenter/inquiryRoomMessagesByRoomId`

**Request model** (`models/request.py`):

```python
class RoomCenterRoomMessageRequest(BaseModel):
    room_id: str | None = None
    message_id: str | None = None
    message_type: str | None = None
    message_content: str | None = None
    message_created_at: datetime | None = None
    extend_info: dict[str, Any] | None = None
    message: RoomMessage | None = None
```

No `skip`, `limit`, `cursor`, or `page` fields.

**Note**: A `PaginationParams` model exists in the backend (`models/request.py`) but is
unused by this endpoint:

```python
class PaginationParams(BaseModel):
    page: int = 1
    limit: int = 20
```

**Service layer** (`services/room_services.py`): Fetches all user messages and all
agent messages for the room, concatenates, sorts by `message_created_at`, returns the
full list.

### Frontend

**API client** (`src/lib/api/room.ts`):

```typescript
export async function inquiryRoomMessagesByRoomId(
  room_id: string,
  getToken?: () => Promise<string | null>,
  signal?: AbortSignal,
): Promise<RoomCenterRoomMessageResponse> {
  return apiPost(`${API_BASE_URL}/inquiryRoomMessagesByRoomId`, { room_id }, getToken, signal)
}
```

Only passes `room_id`. No pagination parameters.

**Room webhook** (`src/hooks/useRoomWebhook.ts`): `hydrateFromDb` (line ~221) and
`reconcileWithDb` (line ~263) both call `inquiryRoomMessagesByRoomId` with no
pagination, loading everything into the message store.

**Message store**: `upsertMany` handles bulk inserts and deduplication. No
pagination-aware state.

---

## 3. Proposed Design

### 3.1 Architecture Overview

```
┌──────────────┐        ┌──────────────────────────────────────┐
│ Room Entry   │───────►│ Load newest 50 messages (page 1)     │
│              │        │ inquiryRoomMessagesByRoomId           │
│              │        │   { room_id, limit: 50 }             │
└──────────────┘        └────────────────┬─────────────────────┘
                                         │
                                         ▼
                        ┌──────────────────────────────────────┐
                        │ Message Store                        │
                        │ orderedIds: [msg_50, ..., msg_1]     │
                        │ hasMore: true                        │
                        └────────────────┬─────────────────────┘
                                         │
                               User scrolls to top
                               clicks "Load More"
                                         │
                                         ▼
                        ┌──────────────────────────────────────┐
                        │ Load next 50 messages (page 2)       │
                        │ inquiryRoomMessagesByRoomId           │
                        │   { room_id, limit: 50,              │
                        │     before: "2026-02-01T..." }       │
                        └────────────────┬─────────────────────┘
                                         │
                                         ▼
                        ┌──────────────────────────────────────┐
                        │ Message Store                        │
                        │ orderedIds: [msg_100, ..., msg_1]    │
                        │ hasMore: true/false                  │
                        └──────────────────────────────────────┘
```

### 3.2 Pagination Strategy: Cursor-Based

Use **cursor-based pagination** with `message_created_at` as the cursor, not
offset-based. Rationale:

- **No duplication**: New messages arriving between pages do not shift offsets.
- **Stable ordering**: `message_created_at` is immutable after creation.
- **Efficient MongoDB query**: `{ message_created_at: { $lt: cursor } }` uses the
  timestamp index.

The cursor is the `message_created_at` of the oldest message in the current set.

---

## 4. Backend Contract (to be implemented)

### 4.1 Request Model Changes

Extend `RoomCenterRoomMessageRequest` with pagination fields:

```python
class RoomCenterRoomMessageRequest(BaseModel):
    room_id: str | None = None
    # ... existing fields ...

    # Pagination (all optional; omitting returns latest messages)
    limit: int = Field(default=50, ge=1, le=200)
    before: datetime | None = None   # Cursor: return messages older than this
```

### 4.2 Response Model Changes

Extend `RoomCenterRoomMessageResponse` with pagination metadata:

```python
class RoomCenterRoomMessageResponse(BaseModel):
    room_id: str | None = None
    message_list: list[RoomMessage] | None = None
    success: bool
    error: str | None = None
    status_code: int = 200

    # Pagination metadata
    total_count: int | None = None   # Total messages in room (optional, for UI)
    has_more: bool = False           # Whether older messages exist
    oldest_timestamp: str | None = None  # Cursor for next page
```

### 4.3 Query Behavior

- If `before` is omitted: return the **newest** `limit` messages, sorted by
  `message_created_at DESC`.
- If `before` is provided: return the newest `limit` messages where
  `message_created_at < before`, sorted by `message_created_at DESC`.
- `has_more` is `True` if there are messages older than the oldest returned message.
- `oldest_timestamp` is the `message_created_at` of the oldest returned message
  (used as `before` cursor for the next page).
- `total_count` is the total number of messages in the room (for optional UI display
  like "Showing 50 of 342 messages").

### 4.4 Backward Compatibility

If `limit` is not provided in the request, the backend defaults to `limit=50` and
returns paginated results. This is a **breaking change** for clients that expect all
messages. To maintain backward compatibility during migration:

- Option A: Default `limit=0` means "all messages" (0 = no limit). Clients that don't
  send `limit` get the old behavior. Frontend sends `limit=50` explicitly.
- Option B: Add a `paginate: bool = False` flag. When `False`, returns all messages
  (old behavior). When `True`, uses `limit` and `before`.

**Recommended**: Option A (`limit=0` = all). Simplest, no extra flag.

---

## 5. Frontend Changes

### 5.1 `src/lib/api/room.ts` — Add pagination params

```typescript
export interface MessagePaginationParams {
  limit?: number
  before?: string  // ISO timestamp cursor
}

export async function inquiryRoomMessagesByRoomId(
  room_id: string,
  pagination?: MessagePaginationParams,
  getToken?: () => Promise<string | null>,
  signal?: AbortSignal,
): Promise<RoomCenterRoomMessageResponse> {
  const requestData = {
    room_id,
    ...(pagination?.limit !== undefined && { limit: pagination.limit }),
    ...(pagination?.before && { before: pagination.before }),
  }
  return apiPost(
    `${API_BASE_URL}/inquiryRoomMessagesByRoomId`,
    requestData,
    getToken,
    signal,
  )
}
```

### 5.2 `src/lib/types/response.ts` — Extend response type

Add pagination metadata to the response type:

```typescript
export interface RoomCenterRoomMessageResponse {
  room_id?: string
  message_list?: RoomMessage[]
  success: boolean
  error?: string | null
  status_code?: number
  // Pagination
  total_count?: number
  has_more?: boolean
  oldest_timestamp?: string
}
```

### 5.3 `src/hooks/useRoomWebhook.ts` — Paginated loading

**Replace `hydrateFromDb`**: Load only the first page on room entry.

```typescript
const hydrateFromDb = async () => {
  const response = await inquiryRoomMessagesByRoomId(
    roomId,
    { limit: PAGE_SIZE },
    getToken,
  )
  // ... existing conversion + upsert logic ...
  setHasMoreMessages(response.has_more ?? false)
  setOldestCursor(response.oldest_timestamp ?? null)
  store.markDbSynced()
}
```

**New state**:

```typescript
const [hasMoreMessages, setHasMoreMessages] = useState(false)
const [oldestCursor, setOldestCursor] = useState<string | null>(null)
const [loadingMore, setLoadingMore] = useState(false)
```

**New function `loadMoreMessages`**:

```typescript
const loadMoreMessages = async () => {
  if (!hasMoreMessages || loadingMore || !oldestCursor) return
  setLoadingMore(true)
  try {
    const response = await inquiryRoomMessagesByRoomId(
      roomId,
      { limit: PAGE_SIZE, before: oldestCursor },
      getToken,
    )
    const incoming = await convertApiMessages(response.message_list)
    store.upsertMany(incoming, 'db')
    setHasMoreMessages(response.has_more ?? false)
    setOldestCursor(response.oldest_timestamp ?? null)
  } finally {
    setLoadingMore(false)
  }
}
```

**Modify `reconcileWithDb`**: On SSE reconnect, only re-fetch the latest page (not
all messages). Existing messages in the store are preserved. This means there may be
"gaps" in the loaded message set (e.g., messages 100-51 loaded from initial page,
messages 50-1 not loaded, then messages 110-101 from reconnect). This is acceptable
because:

1. `upsertMany` merges by ID, so no duplicates occur.
2. `buildSortedIds` sorts by timestamp, so the gap is invisible in ordering.
3. The "Load More" button at the top continues to work -- its cursor is the oldest
   loaded message, so clicking it fills the gap naturally.
4. New SSE messages arriving during the disconnect are inserted at the correct
   position regardless of which pages are loaded.

**Expose from hook**: Return `hasMoreMessages`, `loadingMore`, `loadMoreMessages`
from the `useRoomWebhook` hook.

### 5.4 `src/components/room-messages.tsx` — Load More button

Add a "Load More" button at the top of the message list:

```tsx
{hasMoreMessages && (
  <div className="flex justify-center py-3">
    <Button
      variant="ghost"
      size="sm"
      onClick={loadMoreMessages}
      disabled={loadingMore}
    >
      {loadingMore ? (
        <>
          <Loader2 className="w-4 h-4 mr-2 animate-spin" />
          Loading...
        </>
      ) : (
        'Load older messages'
      )}
    </Button>
  </div>
)}
```

### 5.5 Scroll Position Preservation

When older messages are prepended to the top, the scroll position must be maintained
so the user does not jump. Implementation:

1. Before loading more: record `scrollHeight` and `scrollTop` of the message
   container.
2. After `upsertMany` and React re-render: compute `newScrollHeight - oldScrollHeight`
   and add the delta to `scrollTop`.
3. Use `useLayoutEffect` or `requestAnimationFrame` for the adjustment to avoid flicker.

---

## 6. State Management Changes

### 6.1 Message Store

No structural changes. `upsertMany` already handles merging and deduplication. The
store does not need to know about pagination — it just receives messages from any
source (initial page, additional pages, SSE).

### 6.2 Room UI Store

Add pagination state (or keep it in the hook as local state):

```typescript
// Option A: Local state in useRoomWebhook (recommended — pagination is per-room-view)
const [hasMoreMessages, setHasMoreMessages] = useState(false)
const [loadingMore, setLoadingMore] = useState(false)

// Option B: Room UI store (if pagination state needs to survive hook unmount)
// Not recommended — the hook already unmounts on room switch, and pagination
// state should reset on room entry.
```

**Recommended**: Local state in the hook. Pagination state is view-specific and should
reset when switching rooms.

### 6.3 React Query

React Query's `useInfiniteQuery` is an option but adds complexity for this use case.
The current architecture uses imperative fetching in `useRoomWebhook` (not React Query
for messages). Adding `useInfiniteQuery` would require refactoring the entire message
hydration flow.

**Recommended**: Keep imperative fetching for messages. Add `loadMoreMessages` as a
simple async function in `useRoomWebhook`. This is consistent with the existing
architecture and avoids a partial React Query migration.

If a future refactor moves all message fetching to React Query, `useInfiniteQuery`
would be the natural fit at that time.

---

## 7. Key Decisions

| Decision | Rationale |
|---|---|
| "Load More" button (not infinite scroll) | Simpler implementation, predictable UX, avoids scroll position restoration bugs. Users explicitly choose to load history. |
| Cursor-based (not offset-based) | Immune to message insertion shifting pages. Efficient MongoDB range query. |
| `limit=0` = all messages (backward compat) | Existing clients continue working without changes. Frontend explicitly opts into pagination. |
| Local state for pagination (not Zustand) | Pagination state is view-specific, resets on room switch, does not need persistence or cross-component access. |
| Imperative fetch (not `useInfiniteQuery`) | Consistent with existing `useRoomWebhook` architecture. Avoids partial React Query migration. |
| `PAGE_SIZE = 50` | Balances initial load speed with having enough context. A room with 10 messages loads everything; a room with 500 loads in 10 pages of 50. |

---

## 8. Error Handling

| Scenario | Behavior |
|---|---|
| `loadMoreMessages` API fails | Show error toast. Keep `hasMoreMessages: true` so the user can retry. Set `loadingMore: false`. |
| Backend returns `has_more: false` | Hide the "Load More" button. All messages are loaded. |
| Backend does not return pagination metadata (old backend) | Default `has_more` to `false`. The frontend behaves as before — loads whatever the backend returns. |
| SSE delivers a message that belongs to an older page (not yet loaded) | The message is inserted into the store by `upsertMessage`. It will be visible even if surrounding messages are not loaded. This is acceptable — it is better to show a new message than to hide it. |
| Concurrent `loadMoreMessages` calls | Guard with `loadingMore` flag. Second call is no-op. |

---

## 9. Future Enhancements (Out of Scope)

- **Virtual scrolling**: For rooms with 500+ messages loaded in memory, a virtualized
  list (`react-window` or `@tanstack/react-virtual`) would reduce DOM node count.
  This is a separate optimization that can be layered on top of pagination.
- **`content-visibility: auto`**: As an interim measure before full virtualization,
  applying `content-visibility: auto` (Vercel rule `rendering-content-visibility`) to
  off-screen message containers would reduce layout cost when 200+ messages are in the
  DOM. Can be done as a CSS-only change without React refactoring.
- **Message search with pagination**: Backend search endpoint that returns paginated
  results with highlights.
- **Unread message indicator**: Track last-read position and show "N new messages"
  badge.
- **Optimistic page caching**: Cache older pages in memory or IndexedDB to avoid
  re-fetching when scrolling back up.
- **`useInfiniteQuery` migration**: If the message fetching layer is refactored to
  React Query in the future, replace the imperative pagination with
  `useInfiniteQuery`.

---

## 10. Testing Strategy

- **Backend unit test**: `inquiryRoomMessagesByRoomId` with `limit=50` returns 50
  messages, `has_more=true`, correct `oldest_timestamp`. With `limit=50, before=X`
  returns the next 50.
- **Backend edge case**: Room with 30 messages, `limit=50` returns 30, `has_more=false`.
- **Backend edge case**: `limit=0` returns all messages (backward compat).
- **Frontend unit test**: `loadMoreMessages` calls API with correct cursor, upserts
  results, updates `hasMoreMessages`.
- **Frontend unit test**: "Load More" button renders when `hasMoreMessages=true`,
  hidden when `false`.
- **Frontend unit test**: Scroll position is preserved after loading more messages.
- **Integration test**: Load room, verify 50 messages, click "Load More", verify 100
  messages, verify scroll position stable.
- **Edge case**: New SSE message arrives while loading more — verify no duplicate
  or lost messages.
