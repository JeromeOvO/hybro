# Frontend Issues Tracker

**Created**: March 16, 2026
**Purpose**: Authoritative, consolidated issue tracker for all frontend concerns. Issues previously scattered across individual feature docs are centralized here.

> **Architecture review**: See `FRONTEND_DESIGN_REVIEW.md` for the full architecture audit and scorecard.
> **Backend issues**: See `hybro-multi-agents-backend/docs/SYSTEM_DESIGN_REVIEW.md` for backend-specific concerns.

---

## Issue Status Summary

| Status | Count |
|--------|-------|
| Open | 16 |
| Resolved | 6 |
| Not Started (design exists) | 5 |
| Total | 27 |

---

## 1. Architecture Issues (from March 2026 audit)

### ARC-1 HIGH: God Component — `room-chat-input.tsx` (1,153 lines, 28 props)

**Source**: Architecture audit (March 16, 2026)
**Location**: `src/components/room-chat-input.tsx`
**Status**: Open

Manages text input, mention autocomplete, file attachments, group selection, supervisor/debate mode toggles, quote display, and keyboard shortcuts in a single component with 28 props.

**Impact**: Unmaintainable, untestable, high coupling between unrelated concerns.

**Recommendation**: Decompose into `<ChatInputEditor>`, `<ChatInputGroupControls>`, `<ChatInputModeToggle>`, `<ChatInputAttachments>`. Reduces from 28 to ~12 props per sub-component.

---

### ARC-2 HIGH: Missing Error Handling Infrastructure

**Source**: Architecture audit (March 16, 2026)
**Location**: `src/app/` (entire app router)
**Status**: Open

Zero `error.tsx`, `loading.tsx`, or `not-found.tsx` files in the app router. No React Error Boundary components. A rendering crash in any component takes down the entire page.

**Impact**: No graceful degradation; users see blank pages on errors instead of meaningful fallbacks.

**Recommendation**:
- Add `app/error.tsx` and `app/not-found.tsx`
- Create `<MessageRenderErrorBoundary>` wrapping `RoomMessages`
- Add route-level `loading.tsx` for `/c/room/[id]` and `/d/register`

---

### ARC-3 HIGH: Proxy Route Duplication (~400 lines)

**Source**: Architecture audit (March 16, 2026)
**Location**: `src/app/api/{agent,task,memory,orchestrationCenter}/[...endpoint]/route.ts`
**Status**: Open

4 proxy routes contain nearly identical try-catch blocks, error handling, and response wrapping. Most routes also don't propagate Authorization headers to the backend.

**Impact**: Bugs must be fixed in 4 places; auth headers silently missing on most routes.

**Recommendation**: Create shared `createProxyHandler(backendPath, options)` utility. Add consistent auth forwarding and timeout configuration.

---

### ARC-4 MEDIUM: Kitchen-Sink Hook — `useRoomWebhook` (21 return properties)

**Source**: Architecture audit (March 16, 2026)
**Location**: `src/hooks/room/useRoomWebhook.ts`
**Status**: Open

Composes 6 sub-hooks into one mega-hook returning 21 properties. Consumers can't use a single feature without loading the entire hook graph.

**Recommendation**: Let consumers compose smaller hooks directly instead of one orchestrator.

---

### ARC-5 MEDIUM: Monolithic SSE Handler (465 lines)

**Source**: Architecture audit (March 16, 2026)
**Location**: `src/hooks/room/sse-handlers/index.ts`
**Status**: Open

Single function handles 13 SSE message types in one massive switch statement (30-200 lines per case).

**Recommendation**: Refactor into handler map: `Record<SSEMessageType, (data, deps) => Promise<void>>` with one file per handler.

---

### ARC-6 MEDIUM: Excessive Hook Parameters

**Source**: Architecture audit (March 16, 2026)
**Location**: `src/hooks/room/useRoomActions.ts` (10 params), `src/hooks/room/useSendMessage.ts` (11 params)
**Status**: Open

Hooks accept 10-11 positional parameters including redundant state+setter pairs (e.g., `sending` + `setSending`).

**Recommendation**: Accept single deps object, or read directly from Zustand stores.

---

### ARC-7 MEDIUM: MessageEntity Size (89 fields)

**Source**: Architecture audit (March 16, 2026)
**Location**: `src/stores/message-store/types.ts`
**Status**: Open

Combines message data + task tracking + HITL state + artifacts into one type with 89 fields.

**Recommendation**: Consider sub-schemas: `Message`, `TaskStatus`, `HITLState`, `ArtifactData`.

---

### ARC-8 LOW: Non-Hook Modules in `/hooks/` Folder

**Source**: Architecture audit (March 16, 2026)
**Location**: `src/hooks/room/processing-lifecycle.ts`, `overlay-pending-hitl.ts`, `sse-handlers/`
**Status**: Open

Pure utility functions and factory patterns placed in hooks directory despite not being React hooks.

**Recommendation**: Move to `src/lib/` for clarity.

---

### ARC-9 LOW: Accessibility Gaps

**Source**: Architecture audit (March 16, 2026)
**Status**: Open

- Minimal ARIA attributes (only copy button and scroll-to-bottom)
- No `role` attributes on custom interactive elements
- DOM-created quote button bypasses React accessibility
- No ARIA live regions for mentions autocomplete or task status
- No keyboard navigation for custom dropdowns

---

### ARC-10 LOW: Code Duplication in Components

**Source**: Architecture audit (March 16, 2026)
**Status**: Open

- Expand/collapse button logic repeated 3x in `message-bubble.tsx`
- Loading spinner pattern repeated across chat/room pages
- Agent info display repeated across 3 components

**Recommendation**: Extract `<ExpandableContent>`, `<LoadingSpinner>`, `<AgentListBadge>`.

---

## 2. Feature Issues (from design docs)

### FEAT-1 MEDIUM: No Pagination for Room Messages

**Source**: `FRONTEND_DESIGN_REVIEW.md` issue 2.4 / `MESSAGE_PAGINATION_DESIGN.md`
**Location**: `src/lib/api/room.ts` (`inquiryRoomMessagesByRoomId`)
**Status**: Open — Design approved, blocked on backend pagination API

Message hydration loads all messages in a single request. Rooms with hundreds of messages will have slow initial load and high memory usage.

**Design**: `MESSAGE_PAGINATION_DESIGN.md` — cursor-based pagination with `limit` and `before` params.

---

### FEAT-2 MEDIUM: Artifact Rendering Not Implemented

**Source**: `ARTIFACT_RENDERING_DESIGN.md` / `MULTIMODAL_SUPPORT_DESIGN.md` Phase 1
**Status**: Not Started — Design approved

SSE `artifact_update` events are handled but rendering components (`ArtifactRenderer`, `PartRenderer`) not yet implemented.

**Design**: `ARTIFACT_RENDERING_DESIGN.md`

---

### FEAT-3 MEDIUM: User File Input Not Implemented

**Source**: `MULTIMODAL_SUPPORT_DESIGN.md` Phase 3
**Status**: Not Started — Design approved

Missing: file upload endpoint, chat input file button/drag-drop/paste, attachment preview, room creation with attachments.

**Dependency**: Phase 1 (FEAT-2) should land first.

---

### FEAT-4 LOW-MEDIUM: Task Retry Not Implemented

**Source**: `TASK_RETRY_DESIGN.md`
**Status**: Not Started — Design approved

Failed agent tasks can't be retried. `targetGroup` and `attachments` lost on DB hydration (not persisted in `RoomMessage`).

**Design**: `TASK_RETRY_DESIGN.md`

---

### FEAT-5 LOW-MEDIUM: Dead Code Cleanup (~1,675 lines)

**Source**: `DEAD_CODE_CLEANUP.md`
**Status**: Not Started — Plan approved

Workflow subsystem (631 lines), legacy API routes (790 lines), dead API modules (204 lines), dead functions (~50 lines).

**Plan**: `DEAD_CODE_CLEANUP.md` — 5-phase execution plan.

---

### FEAT-6 LOW: A2A SDK v1.0 Upgrade

**Source**: `A2A_UPGRADE_ROADMAP.md`
**Status**: Pending v1.0 SDK release

Phase 0: Pin SDK version, centralize AgentCard access, audit inline types.
Phase 1: SDK upgrade, TypeScript fixes, TaskState validation.
Phase 2: Adopt new features (contextId, verification badges).
Phase 3: Remove compatibility shims.

---

## 3. Known Limitations (from implemented features)

These are accepted limitations in shipped features, documented for awareness.

### Stale Task Detection (`STALE_TASK_DETECTION.md`)
- Client-side only using `Date.now()` — subject to clock skew
- Threshold hardcoded (10min pending, 24h HITL) — no backend coordination

### Processing Cancellation (`PROCESSING_CANCELLATION.md`)
- No per-task cancellation (cancels all room processing)
- 15-second timeout is a UX guard, not a confirmed failure
- No retry after cancellation (user must retype)

### SSE Reconnection (`SSE_RECONNECTION.md`)
- No polling fallback after 5 failed reconnect attempts
- Linear backoff only (no exponential with jitter)
- `reconcileWithDb` refetches ALL room messages, not just missed ones
- 1500ms reconciliation delay is arbitrary

### Agent Groups (`AGENT_GROUPS.md`)
- `localStorage` restoration is consumer-side (manual on init)
- No server-side group validation for stale agent IDs
- Agent loading in modal is eager (slow for 100+ agents)

### Mention System (`MENTION_SYSTEM.md`)
- Word-boundary only; agent names with spaces/hyphens may not autocomplete
- No mention support in quoted replies
- Full agent list loaded eagerly (no lazy search)
- ContentEditable DOM approach is complex (~110 lines) and fragile across browsers

### Quote Reply (`QUOTE_REPLY.md`)
- Text-only quotes (code blocks/images quoted as plain text)
- No visual quote display in message history (sent to backend but not rendered back)
- Single quote only (selecting new text replaces previous)
- No keyboard shortcut

### Supervisor Toggle (`SUPERVISOR_TOGGLE_DESIGN.md`)
- Deferred: Room header badge showing "Supervisor" when active
- Deferred: Custom processing indicator ("Supervisor coordinating agents...")

### Debate Mode (`DEBATE_MODE.md`)
- No visual indicator in room header
- No UI differentiation between debate/non-debate responses
- No per-message override

### Settings Dialog (`SETTINGS_DIALOG.md`)
- No email change (requires Clerk Dashboard)
- No 2FA management
- No notification preferences
- No optimistic updates (waits for Clerk API)

### HITL Frontend (`HITL_FRONTEND_DESIGN.md`)
- Text-only replies (no file attachments)
- Single-user only (room owner)
- No auth-required state handling (OAuth redirect)
- No notification badge for pending HITL outside room view

### API Layer (Architecture audit)
- SSE and files modules bypass centralized `apiClient` (direct `fetch()`)
- Inconsistent timeout configuration across proxy routes
- 3 `as any` type escapes for Pydantic RootModel variants
- Duplicate type definitions between `request.ts` and `room.ts`

---

## 4. Cross-Repo Frontend Requirements

Issues requiring frontend work that originate from backend design docs.

| # | Feature | Source | Status |
|---|---------|--------|--------|
| XREPO-1 | Hub Portal UI (agent management, pairing, dashboard) | `HYBRO_HUB_DESIGN.md` Phase 1 & 3 | Not Started |
| XREPO-2 | Trust Layer UI (trace viewer, policy admin, badges) | `HYBRO_TRUST_LAYER_DESIGN.md` Phase 5 | Not Started |
| XREPO-3 | Generic agent interactive states (`input_required` / `auth_required`) | `LONG_RUNNING_TASKS_DESIGN.md` #5 | Partially covered by HITL |

---

## 5. Resolved Issues

| # | Issue | Resolution | Date |
|---|-------|------------|------|
| OLD-1 | SSE auth token in URL query parameter (HIGH) | Migrated to fetch()-based SSE with `Authorization: Bearer` header | 2026-03 |
| OLD-2 | Optimistic update ID mismatch window (MEDIUM) | `client_request_id` correlation + atomic temp→real ID swap (commit `692acfc`) | 2026-03 |
| OLD-3 | No message size validation (MEDIUM) | 10k-char limit with visible counter in `room-chat-input.tsx` | 2026-03 |
| OLD-4 | `useRoomWebhook` god hook ~1,680 lines (LOW-MEDIUM) | Decomposed into 15 focused files in `src/hooks/room/` (commit `e0e040e`) | 2026-03 |
| OLD-5 | `useRoomUiStore` global singleton (LOW) | Flags keyed by `roomId` in `rooms: Record<RoomId, RoomFlags>` | 2026-03 |
| OLD-6 | Streaming one-token-per-line flashing | Unified agent bubble, eliminated `TaskStatusMessage` | 2026-03 |

---

## 6. Priority Ranking

### Immediate (high impact, low effort)
1. **ARC-2** — Add error boundaries and Next.js error/loading pages
2. **ARC-3** — DRY up proxy routes with shared handler utility
3. **FEAT-5** — Dead code cleanup (~1,675 lines)

### High Value (unlocks features or reduces debt)
4. **ARC-1** — Decompose `room-chat-input.tsx` God component
5. **FEAT-1** — Message pagination (blocked on backend API)
6. **FEAT-2** — Artifact rendering (Phase 1 of multimodal)
7. **ARC-5** — Refactor monolithic SSE handler into handler map

### Medium Value (polish and maintainability)
8. **ARC-4** — Simplify `useRoomWebhook` return interface
9. **ARC-6** — Reduce hook parameter counts
10. **ARC-7** — Split MessageEntity into sub-schemas
11. **FEAT-4** — Task retry
12. **FEAT-6** — A2A SDK v1.0 upgrade

### Low Priority (deferred)
13. **ARC-8** — Move non-hook modules to `/lib/`
14. **ARC-9** — Accessibility improvements
15. **ARC-10** — Extract shared presentational components
16. **FEAT-3** — User file input (depends on FEAT-2)
17. **XREPO-1/2/3** — Cross-repo features (backend-dependent)
