# Frontend Design Review: Hybro Frontend

**Date**: March 9, 2026
**Scope**: Frontend architecture review for `hybro-frontend`, complementing the backend `SYSTEM_DESIGN_REVIEW.md`

> **Backend review**: See `hybro-multi-agents-backend/docs/SYSTEM_DESIGN_REVIEW.md` for backend-specific issues.
> **Full architecture**: See `architecture.md` in this directory for the comprehensive frontend architecture documentation.

---

## 1. Architecture Overview

| Layer            | Technology                               |
| ---------------- | ---------------------------------------- |
| Framework        | Next.js 15 (App Router) + React 19       |
| Auth             | Clerk (`@clerk/nextjs` ^6.24.0)          |
| Server State     | TanStack React Query 5                   |
| Client State     | Zustand 5                                |
| Styling          | Tailwind CSS v4 + shadcn/ui (Radix)      |
| Real-time        | SSE via `SSEConnection` class (auto-reconnect, linear backoff) |
| Message Store    | Normalized entity store (Zustand 5 + `useSyncExternalStore` streaming buffer) |
| Forms            | React Hook Form + Zod 4                  |
| Markdown         | react-markdown + remark-gfm + rehype     |

The frontend serves two portals via subdomain routing:
- **Consumer** (`hybro.ai` -> `/c/*`): Chat rooms, agent marketplace
- **Developer** (`developer.hybro.ai` -> `/d/*`): Agent registration, inspector

### Core Client-Side Data Flow

```
User types message
    |-> RoomChatInput.onSubmit()
        |-> useRoomWebhook.sendUserMessage()
            |-> Message store: upsert optimistic user message + processing placeholder
            |-> POST /api/v1/roomCenter/sendMessage
            |   |-> Returns real messageId
            |   |-> Message store: replace temp ID with real ID
            |-> SSE stream delivers events:
                |-> task_submitted   -> upsert task-status entity
                |-> task_update      -> update task state (working/completed/failed/input-required)
                |-> agent_token      -> append to StreamingBuffer (rAF batched)
                |-> agent_response   -> finalize streaming, create authoritative entity
                |-> hitl_input_requested -> upsert with HITL fields
                |-> artifact_update  -> stream artifact chunks
                |-> processing_status=completed -> setProcessing(false), reconcile

Final UI = Normalized entity store (deduped by ID, three-way merge: db > sse > optimistic)
```

---

## 2. Identified Issues & Risks

> **Authoritative tracker**: This document is the authoritative issue tracker for frontend concerns. `architecture.md` Section 15 provides deeper architectural context and additional code-quality observations; overlapping issues (SSE token, pagination, god hook, global singleton) are tracked here for status purposes.


### 2.1 HIGH: SSE Auth Token in URL Query Parameter

**Location**: `src/lib/api/sse.ts` (line 53)

Because `EventSource` cannot send custom HTTP headers, the Clerk JWT is passed as a URL query parameter:

```
GET /api/v1/sse/room/{roomId}/stream?token=<clerk-jwt>
```

**Impact**:
- Tokens appear in **browser network inspector**, **browser history**, and **referrer headers**.
- While Clerk JWTs are short-lived (60s default), the token is visible during its validity window.
- Server-side token logging is a backend concern (see backend `SYSTEM_DESIGN_REVIEW.md` issue 2.6).

**Recommendation**:
- Migrate from `EventSource` to `fetch()`-based SSE streaming using `ReadableStream`, which supports custom `Authorization` headers.
- Alternatively, exchange the Clerk JWT for a short-lived SSE nonce via a backend endpoint before opening the stream.

> **Backend side**: See `hybro-multi-agents-backend/docs/SYSTEM_DESIGN_REVIEW.md` issue 2.6 for the server-side nonce endpoint recommendation.

---

### 2.2 MEDIUM: Optimistic Update ID Mismatch Window

**Location**: `src/hooks/useRoomWebhook.ts` (`sendUserMessage`)

The frontend adds an optimistic user message with a temporary ID, then replaces it with the real server-assigned ID after the API call returns. Between these two steps, any SSE event referencing the real `messageId` won't match the temp ID.

**Impact**: If the SSE `user_message` event arrives before the temp-to-real ID swap completes (`removeMessage(tempId)` + `upsertMessage({id: realId})` in the normalized store), the UI briefly shows duplicate user messages. The Zustand deduplication logic works by ID, so the temp and real IDs are treated as separate messages.

**Recommendation**:
- Include a `client_request_id` in the `sendMessage` payload and have the backend echo it in the SSE `user_message` event, allowing correlation without timing dependency.
- Or: delay adding the optimistic message until the real ID is available (sacrificing instant feedback for correctness).
- Or: deduplicate by `(content, user_id, timestamp_within_threshold)` in addition to ID.

---

### 2.3 MEDIUM: No Message Size Validation on Frontend

**Location**: Chat input components

No explicit validation on user message content size exists on the frontend.

**Impact**:
- Users can paste or type arbitrarily large messages without feedback.
- Large messages cause slow API calls and can exceed backend/LLM limits.

**Recommendation**: Add character limit enforcement (e.g., max 10,000 characters) with a visible counter in the chat input. The backend should also enforce this authoritatively (see backend `SYSTEM_DESIGN_REVIEW.md` issue 2.10).

---

### 2.4 MEDIUM: No Pagination for Room Messages

**Location**: `src/lib/api/room.ts` (`inquiryRoomMessagesByRoomId`)

Message hydration loads **all messages for a room** in a single request.

**Impact**: For rooms with hundreds or thousands of messages:
- Slow initial load times
- High memory usage (all entities held in Zustand)
- Large JSON parse blocking the main thread

**Recommendation**: Implement cursor-based pagination: load the most recent N messages, then fetch older messages on scroll-up. The normalized store already supports incremental `upsertMany`.

> **Cross-reference**: See `architecture.md` issue 15.3 and `MESSAGE_PAGINATION_DESIGN.md` for the full design.

---

### 2.5 LOW-MEDIUM: `useRoomWebhook` God Hook (~1680 lines)

**Location**: `src/hooks/useRoomWebhook.ts`

The central room orchestration hook manages SSE handling, message sending, room settings, cancellation, DB hydration, reconciliation, agent name resolution, placeholder lifecycle, timeout safety nets, HITL reconnect catch-up, and streaming buffer coordination.

**Impact**: High cognitive load for contributors; hard to test in isolation; any change can introduce subtle regressions.

**Recommendation**: Split into focused composable hooks: `useRoomHydration`, `useSSEMessageHandler`, `useSendMessage`, `useCancelProcessing`, `useAgentNameResolver`, `useHitlReconnect`.

> **Cross-reference**: See `architecture.md` issue 15.1 for the original analysis.

---

### 2.6 LOW: `useRoomUiStore` is a Global Singleton

**Location**: `src/stores/room-ui-store.ts`

`sending`, `processing`, `cancelling` flags are global, not scoped per room. If the app ever supports multi-room views or room switching without full unmount, state bleeds between rooms.

**Recommendation**: Key these flags by `roomId` inside the store, or scope them as local state within `useRoomWebhook`.

---

## 3. Implementation Progress

### 3.1 HITL Frontend (Phase 6 — In Progress)

**New Files:**
- `src/lib/api/hitl.ts` — `fetchPendingHitlRequests()`, `respondToHitl()` (3-minute timeout)
- `src/components/hitl-inline-reply-form.tsx` — Inline reply component with:
  - 3 prompt types: text, choice, confirmation
  - Multi-question grouping with pagination (`group_id`, `group_total`, `group_index`)
  - Auto-advance to next unanswered question in a group
  - Read-only Q&A display for previously answered questions
  - "Other" option for custom choice responses

**SSE Event Types** (`src/lib/types/sse.ts`):
- Added: `hitl_input_requested`, `hitl_status_update`, `artifact_update`
- Task states: kebab-case from A2A spec (`submitted`, `working`, `input-required`, `auth-required`, `completed`, `canceled`, `failed`, `rejected`)

**Message Entity HITL Fields** (`src/stores/message-store/types.ts`):
- `hitlRequestId`, `hitlPrompt`, `hitlPromptType`, `hitlChoices`
- `hitlExpiresAt`, `hitlResolved`, `hitlGroupId`, `hitlGroupTotal`, `hitlGroupIndex`, `hitlUserAnswer`

---

### 3.2 Normalized Message Store (Completed 2026-03-09)

Major refactoring of the frontend message state management.

**Core Files** (`src/stores/message-store/`):
- `index.ts` — Zustand store with per-room entity storage, reconciliation metadata, upsert operations
- `types.ts` — `MessageEntity` with multimodal support (artifacts, attachments), HITL fields, task state, provenance metadata
- `upsert.ts` — Three-way merge logic with source precedence (db > sse > optimistic)
- `resolve-display-type.ts` — Single source of truth for component rendering (user-bubble, agent-bubble, task-status)
- `convert-api-message.ts` — Converts `RoomMessage` API format to normalized `IncomingMessage`
- `hydration-filter.ts` — Filters empty agent messages during DB hydration
- `stale-detection.ts` — Marks stuck tasks as failed (10min pending, 24h interactive)

**Key Design Decisions:**
- Entity-based normalization (one flat map per room, keyed by message ID)
- Three-way merge ensures server data always wins over optimistic predictions
- `sourceVersion` monotonic counter prevents stale overwrites

---

### 3.3 Streaming Architecture (Completed 2026-03-09)

**StreamingBuffer** (`src/stores/streaming-buffer.ts`):
- NOT a Zustand store; uses raw mutable state with `useSyncExternalStore`
- Handles 50-200 tokens/sec with `requestAnimationFrame` batching
- Per-message versioning for selective React re-renders
- O(1) append, finalize, isStreaming operations
- Clears on room switch

**TypewriterManager** (`src/stores/typewriter.ts`):
- Progressive text reveal for bulk responses (not real token streaming)
- Duration scales with content: ~400ms (short) to ~1000ms (long)
- 16ms tick interval at 60fps
- Finishes any existing typewriter before starting new one

---

### 3.4 SSE Connection Management (Completed 2026-03-09)

**SSEConnection** (`src/lib/api/sse.ts`):
- Auto-reconnection with linear backoff (max 5 attempts, delay = 1000ms * attempt)
- Heartbeat message filtering
- Graceful disconnect with `isManualClose` flag
- Connection state tracking via `EventSource.readyState`

**useRoomSSE** (`src/hooks/useRoomSSE.ts`):
- React hook wrapper with stable callback refs (prevents reconnection loops)
- Separates enabled/connected/connecting/error states
- Handles connection lifecycle (connect/disconnect/reconnect on roomId change)

---

### 3.5 Resilience Features (Completed 2026-03-09)

**Reconciliation on SSE Disconnect (Gap 14 fix):**
- `sseHadDisconnectionRef` tracks if SSE dropped during processing
- Silent DB sync fires after processing completes IF disconnection was detected
- Skipped on happy path (no disconnect)

**Cancellation Safety Net (FE-3):**
- 30s timeout ref for cancel signal (`cancelTimeoutRef`)
- `cancelTimedOutRef` flag prevents duplicate banners after timeout
- Uses `cancelMessage()` from `sse.ts` to notify backend
- Batch cancel all non-terminal tasks on processing_status done

**Processing Placeholder Restoration:**
- Checks if room has active `processing_message_id` on page reload
- Skips placeholder if message is >2min stale
- Restores processing state without re-triggering backend

---

### 3.6 Test Coverage

| Test File | Covers |
|---|---|
| `store.test.ts` | Normalized store operations, room-scoped entities |
| `upsert.test.ts` | Three-way merge, artifact merging, source precedence |
| `hydration-filter.test.ts` | Empty message filtering during DB hydration |
| `hitl-upsert.test.ts` | HITL group handling and Q&A state |
| `stale-detection.test.ts` | Stale task marking for timeouts |
| `resolve-display-type.test.ts` | Display type resolution logic |
| `streaming-lifecycle.test.ts` | Token streaming and typewriter interaction |
| `typewriter.test.ts` | Typewriter effect timing and cleanup |

**Not yet tested:**
- Hooks (`useRoomWebhook`, `useRoomSSE`, `useChatRoomCreation`, `useGroupManagement`)
- API client functions (`lib/api/*.ts`, `lib/api-client.ts`)
- Components (room page, chat page, message rendering)
- Middleware (subdomain routing logic)

> **Cross-reference**: See `architecture.md` issue 15.4 for full testing gap analysis.

---

## 4. Issue Summary

| #    | Severity   | Issue                                            | Impact                     |
| ---- | ---------- | ------------------------------------------------ | -------------------------- |
| 2.1  | High       | SSE auth token in URL query parameter             | Token exposure             |
| 2.2  | Medium     | Optimistic update ID mismatch window              | Duplicate UI messages      |
| 2.3  | Medium     | No message size validation on frontend            | UX / backend overflow      |
| 2.4  | Medium     | No pagination for room messages                   | Slow load, memory growth   |
| 2.5  | Low-Medium | `useRoomWebhook` god hook (~1680 lines)           | Maintenance burden         |
| 2.6  | Low        | `useRoomUiStore` global singleton                 | Multi-room state bleed     |

> Additional issues documented in `architecture.md` Section 15 (dead code, performance, code quality, verbose logging, test gaps).

---

## 5. Issue Status Tracking

| #    | Issue                                            | Status       | Resolution Notes                                                                 |
| ---- | ------------------------------------------------ | ------------ | -------------------------------------------------------------------------------- |
| 2.1  | SSE auth token in URL query parameter             | Resolved     | Migrated from EventSource to fetch()-based SSE with `Authorization: Bearer` header |
| 2.2  | Optimistic update ID mismatch window              | Open         | Could be helped by backend `client_request_id` echo in SSE events               |
| 2.3  | No message size validation on frontend            | Open         | Add char limit to chat input; backend enforcement needed too (backend SDR 2.10)  |
| 2.4  | No pagination for room messages                   | Open         | Design doc exists: `MESSAGE_PAGINATION_DESIGN.md`                                |
| 2.5  | `useRoomWebhook` god hook                         | Open         | Decomposition planned; see `architecture.md` issue 15.1                          |
| 2.6  | `useRoomUiStore` global singleton                 | Open         | Low priority; only a risk with multi-room views                                  |
