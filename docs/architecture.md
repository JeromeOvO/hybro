# Hybro Frontend — Interaction Architecture

> Last updated: 2026-02-25

## Table of Contents

- [1. Overview](#1-overview)
- [2. Tech Stack](#2-tech-stack)
- [3. Environment Variables](#3-environment-variables)
- [4. Directory Structure](#4-directory-structure)
- [5. Subdomain Routing & Dual-Portal Architecture](#5-subdomain-routing--dual-portal-architecture)
- [6. Provider Hierarchy](#6-provider-hierarchy)
- [7. Data Flow Architecture](#7-data-flow-architecture)
- [8. API Layer](#8-api-layer)
- [9. State Management](#9-state-management)
- [10. Real-Time Communication (SSE)](#10-real-time-communication-sse)
- [11. Chat Room Interaction Flow](#11-chat-room-interaction-flow)
- [12. Authentication](#12-authentication)
- [13. Key Interaction Diagrams](#13-key-interaction-diagrams)
- [14. Conventions & Patterns for Contributors](#14-conventions--patterns-for-contributors)
- [15. Known Issues, Risks & Future Improvements](#15-known-issues-risks--future-improvements)

---

## 1. Overview

Hybro Frontend is a **Next.js 15 (App Router)** application that serves as the user interface for the Hybro AI multi-agent platform — an open **A2A (Agent-to-Agent)** network. The app is split into two portals served from a single codebase via subdomain-based routing:

- **Consumer Portal** (`hybro.ai` / `localhost:3000`) — End users chat with AI agents in "rooms"
- **Developer Portal** (`developer.hybro.ai` / `dev.localhost:3000`) — Developers register, manage, and inspect agents

The frontend communicates with a Python backend via REST APIs and receives real-time updates through Server-Sent Events (SSE).

---

## 2. Tech Stack

| Category | Technology |
|---|---|
| Framework | Next.js 15 (App Router, Turbopack) |
| Language | TypeScript |
| UI Components | shadcn/ui (Radix primitives, New York style) |
| Styling | Tailwind CSS v4 |
| State Management | Zustand (message store, room UI store) |
| Server State | TanStack React Query v5 |
| Authentication | Clerk (`@clerk/nextjs`) |
| Real-Time | Server-Sent Events (SSE) via `EventSource` |
| Forms | React Hook Form + Zod v4 |
| Markdown | react-markdown + remark-gfm + rehype-highlight |
| A2A Protocol | `@a2a-js/sdk` |
| Icons | Lucide React |
| Notifications | Sonner (toast) + custom Banner system |
| Testing | Vitest + Testing Library |

---

## 3. Environment Variables

Key environment variables (see `.env.example`):

| Variable | Purpose | Example |
|---|---|---|
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | Clerk auth frontend key | `pk_test_...` |
| `CLERK_SECRET_KEY` | Clerk auth server key | `sk_test_...` |
| `NEXT_PUBLIC_API_BASE_URL` | Backend API base URL | `http://localhost:8000` |
| `NEXT_PUBLIC_API_PREFIX` | Backend API path prefix | `/api/v1` |
| `NEXT_PUBLIC_CONSUMER_URL` | Consumer portal origin | `http://localhost:3000` |
| `NEXT_PUBLIC_DEVELOPER_URL` | Developer portal origin | `http://dev.localhost:3000` |
| `NEXT_PUBLIC_ENABLE_WAITLIST` | Enable Clerk waitlist mode | `true` / `false` |
| `NEXT_PUBLIC_INSPECTION_TIMEOUT_MS` | Timeout for agent inspection calls | `300000` |

The `getApiUrl(key)` helper in `lib/utils.ts` builds backend URLs as: `${NEXT_PUBLIC_API_BASE_URL}${NEXT_PUBLIC_API_PREFIX}/${key}`.

---

## 4. Directory Structure

```
src/
├── app/                          # Next.js App Router
│   ├── layout.tsx                # Root layout (Clerk, Theme, QueryProvider)
│   ├── (auth)/                   # Auth pages (centered layout)
│   │   ├── layout.tsx            #   Auth layout (centered, background image)
│   │   ├── sign-in/[[...sign-in]]/page.tsx  # Clerk SignIn (+ waitlist check)
│   │   └── sign-up/[[...sign-up]]/page.tsx  # Clerk SignUp (+ waitlist check)
│   ├── c/                        # Consumer portal routes
│   │   ├── layout.tsx            #   Consumer layout (sidebar + header)
│   │   ├── page.tsx              #   Landing page (redirects to /chat if signed in)
│   │   ├── chat/page.tsx         #   New chat creation page (main entry point)
│   │   ├── room/[id]/page.tsx    #   Chat room page (real-time messaging)
│   │   ├── agents/               #   Agent marketplace (list + detail)
│   │   ├── about/page.tsx        #   About page
│   │   └── pricing/page.tsx      #   Pricing page
│   ├── d/                        # Developer portal routes
│   │   ├── layout.tsx            #   Developer layout (sidebar + header)
│   │   ├── page.tsx              #   Dashboard (signed in) or docs (signed out)
│   │   ├── register/page.tsx     #   Register new agent (multi-step form)
│   │   ├── agents/               #   Manage agents (list + detail)
│   │   ├── inspector/page.tsx    #   A2A Agent Inspector
│   │   └── docs/page.tsx         #   Developer documentation
│   ├── api/                      # Next.js API Route Proxies (LEGACY — unused by current client)
│   │   ├── agent/[...endpoint]/  #   Proxy → Backend /api/v1/agent/*
│   │   ├── roomCenter/[...endpoint]/
│   │   ├── orchestrationCenter/[...endpoint]/
│   │   ├── sse/[...endpoint]/    #   SSE stream proxy (GET/POST)
│   │   ├── task/[...endpoint]/
│   │   ├── memory/[...endpoint]/
│   │   ├── inspectionCenter/[...endpoint]/
│   │   └── health/               #   Health check proxy
│   └── privacy/page.tsx          # Privacy policy
│
├── components/
│   ├── consumer/                 # Consumer-specific
│   │   ├── consumer-sidebar.tsx  #   Sidebar with room list, nav
│   │   └── consumer-header.tsx   #   Top header bar
│   ├── developer/                # Developer-specific
│   │   ├── developer-sidebar.tsx #   Sidebar with nav, agents
│   │   ├── developer-header.tsx  #   Top header bar
│   │   └── agent-settings-card.tsx # Agent config form card
│   ├── providers/                # Context providers
│   │   ├── ClerkAuthProvider.tsx  #   Sets defaultGetToken for apiClient
│   │   └── query-provider.tsx    #   TanStack React Query setup
│   ├── settings/                 # User settings dialog
│   │   ├── settings-dialog.tsx   #   Main settings dialog shell
│   │   ├── settings-dialog-provider.tsx # Dialog open/close context
│   │   ├── profile-section.tsx   #   Profile edit (name, avatar)
│   │   ├── password-section.tsx  #   Change password
│   │   ├── sessions-section.tsx  #   Active sessions management
│   │   ├── danger-zone-section.tsx #  Delete account
│   │   ├── settings-card.tsx     #   Reusable card wrapper
│   │   ├── form-group.tsx        #   Form field wrapper
│   │   ├── loading-button.tsx    #   Button with spinner
│   │   └── password-input.tsx    #   Password visibility toggle
│   ├── ui/                       # shadcn/ui primitives (25 components)
│   ├── room-messages.tsx         # Message list renderer
│   ├── room-chat-input.tsx       # Chat input with @mentions, groups
│   ├── message-bubble.tsx        # Individual message display
│   ├── task-status-message.tsx   # A2A task status card
│   ├── workflow-message.tsx      # Workflow visualization (DEAD CODE)
│   ├── workflow-container.tsx    # Workflow step container (DEAD CODE)
│   ├── agent-card.tsx            # Agent display card
│   ├── agent-selector.tsx        # Multi-agent selector
│   ├── group-selector.tsx        # Agent group selector
│   ├── group-management-modal.tsx # CRUD modal for groups
│   ├── room-setting-form.tsx     # Room configuration form
│   ├── markdown-content.tsx      # Markdown renderer
│   ├── developer-docs-content.tsx # Developer docs page content
│   ├── cookie-banner.tsx         # GDPR cookie consent banner
│   ├── theme-provider.tsx        # next-themes wrapper
│   ├── theme-toggle.tsx          # Dark/light mode toggle button
│   ├── icons.tsx                 # Custom SVG icons (GitHub, Discord, etc.)
│   ├── logo.tsx                  # Hybro logo component
│   ├── framework-badges.tsx      # Framework/tool icon badges
│   ├── video-embed.tsx           # YouTube video embed (lazy-load)
│   ├── nav-main.tsx              # Sidebar main navigation
│   ├── nav-agent.tsx             # Sidebar agent nav item
│   ├── nav-user.tsx              # User avatar/dropdown in sidebar
│   ├── nav-discord-button.tsx    # Discord link in sidebar
│   └── upgrade-button.tsx        # Pricing/upgrade sidebar button
│
├── hooks/
│   ├── useRoomWebhook.ts         # Core room orchestration hook
│   ├── useRoomSSE.ts             # SSE connection management
│   ├── useRoomMessages.ts        # Message store selectors
│   ├── useWorkflow.ts            # Workflow lifecycle management (DEAD CODE)
│   ├── useChatRoomCreation.ts    # Room creation + navigation
│   ├── useMyAgents.ts            # Developer's registered agents
│   ├── useGroupManagement.ts     # Agent group CRUD + selection
│   ├── useAutoHideScroll.ts      # UI scroll behavior
│   └── use-mobile.ts             # Mobile viewport detection
│
├── stores/
│   ├── room-ui-store.ts          # Ephemeral UI state (sending, SSE status)
│   └── message-store/            # Normalized message entity store
│       ├── index.ts              #   Store definition + actions
│       ├── types.ts              #   MessageEntity, IncomingMessage
│       ├── upsert.ts             #   Merge-on-write logic
│       ├── resolve-display-type.ts  # Message display type derivation
│       ├── hydration-filter.ts   #   Filter stale/duplicate on DB load
│       ├── stale-detection.ts    #   Mark stuck tasks as stale
│       ├── convert-api-message.ts #  API → IncomingMessage converter
│       └── __tests__/            #   Unit tests for each module
│
├── lib/
│   ├── api/                      # API client functions
│   │   ├── index.ts              #   Barrel export
│   │   ├── agent.ts              #   Agent CRUD
│   │   ├── agent-group.ts        #   Agent group CRUD
│   │   ├── room.ts               #   Room + messaging APIs
│   │   ├── sse.ts                #   SSE connection class + status
│   │   ├── task.ts               #   Task query APIs
│   │   ├── orchestration.ts      #   Workflow orchestration APIs
│   │   ├── memory.ts             #   Chat memory APIs
│   │   ├── inspection.ts         #   Agent inspection APIs
│   │   ├── health.ts             #   Health check
│   │   └── a2a-tasks.ts          #   A2A task status polling
│   ├── types/                    # TypeScript type definitions
│   │   ├── index.ts              #   Barrel export
│   │   ├── agent.ts              #   Agent, AgentCard
│   │   ├── agent-group.ts        #   AgentGroup
│   │   ├── room.ts               #   Room settings
│   │   ├── sse.ts                #   SSE messages, TaskState
│   │   ├── request.ts            #   API request types
│   │   ├── response.ts           #   API response types
│   │   ├── memory.ts             #   Chat memory types
│   │   ├── health.ts             #   Health check types
│   │   └── error.ts              #   Error types
│   ├── api-client.ts             # Centralized fetch wrapper (auth, timeout, abort)
│   ├── auth.ts                   # Client-side Clerk token management
│   ├── urls.ts                   # Cross-subdomain URL helpers
│   ├── utils.ts                  # cn(), getApiUrl(), formatIfJson()
│   ├── time.ts                   # Timestamp normalization utilities
│   ├── system-agents.ts          # System agent name mappings
│   ├── agent-colors.ts           # Agent color assignments
│   ├── sidebar-styles.ts         # Sidebar styling utilities
│   ├── nav-items.ts              # Navigation configuration
│   ├── consumer-nav.ts           # Consumer portal nav items
│   ├── developer-nav.ts          # Developer portal nav items
│   └── clerk-error.ts            # Clerk error utilities
│
└── middleware.ts                  # Subdomain routing middleware
```

---

## 5. Subdomain Routing & Dual-Portal Architecture

The application uses a **middleware-based subdomain rewrite** pattern to serve two distinct portals from a single Next.js deployment:

```
┌────────────────────────────────────────────────────────┐
│                   Incoming Request                      │
│        (hybro.ai  OR  developer.hybro.ai)              │
└────────────────────┬───────────────────────────────────┘
                     │
              ┌──────▼──────┐
              │  Middleware  │
              │ (Clerk Auth  │
              │ + Subdomain  │
              │   Rewrite)   │
              └──────┬──────┘
                     │
          ┌──────────┼──────────┐
          │                     │
    ┌─────▼─────┐         ┌────▼──────┐
    │  /c/*     │         │  /d/*     │
    │ Consumer  │         │ Developer │
    │  Portal   │         │  Portal   │
    └───────────┘         └───────────┘
```

**Routing Rules:**
- `developer.hybro.ai` / `dev.localhost:3000` → Rewrites `/*` to `/d/*`
- `hybro.ai` / `localhost:3000` → Rewrites `/*` to `/c/*`
- Shared paths (`/api/*`, `/sign-in`, `/sign-up`, `/_next/*`, `/privacy`) are never rewritten
- Local dev supports `?_subdomain=developer` query param as fallback

Each portal has its own:
- **Layout** (`c/layout.tsx`, `d/layout.tsx`) with portal-specific sidebar and header
- **Navigation** structure configured in `lib/consumer-nav.ts` and `lib/developer-nav.ts`

---

## 6. Provider Hierarchy

The root layout establishes a nested provider chain that wraps the entire application:

```
<html>
  <body>
    <ClerkProvider>                    ← Auth context (Clerk SDK)
      <ClerkAuthProvider>              ← Sets defaultGetToken for apiClient
        <ThemeProvider>                ← Dark/light theme (next-themes)
          <QueryProvider>              ← TanStack React Query
            {children}                 ← Page content
          </QueryProvider>
          <Toaster />                  ← Sonner notifications
          <CookieBanner />             ← GDPR consent
        </ThemeProvider>
      </ClerkAuthProvider>
    </ClerkProvider>
  </body>
</html>
```

Portal-level layouts add:
```
<SidebarProvider>                      ← shadcn sidebar context
  <SettingsDialogProvider>             ← User settings modal
    <Portal-Sidebar />                 ← Consumer or Developer sidebar
    <SidebarInset>
      <Portal-Header />               ← Consumer or Developer header
      <main>{children}</main>
    </SidebarInset>
  </SettingsDialogProvider>
</SidebarProvider>
```

---

## 7. Data Flow Architecture

### 7.1 Client-Side State Architecture

The frontend uses a **three-layer state model**:

```
┌─────────────────────────────────────────────────────────────┐
│                    State Architecture                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Layer 1: Server State (TanStack React Query)               │
│  ┌─────────────────────────────────────────────────┐       │
│  │ • Room settings        queryKey: ['room', id]    │       │
│  │ • Agent catalog         queryKey: ['agents', '*']│       │
│  │ • My agents (developer) queryKey: ['agents','my']│       │
│  │ • Agent groups          (local state in hook)    │       │
│  └─────────────────────────────────────────────────┘       │
│                                                             │
│  Layer 2: Normalized Entity Store (Zustand)                 │
│  ┌─────────────────────────────────────────────────┐       │
│  │ useMessageStore                                  │       │
│  │ • entities: Record<id, MessageEntity>            │       │
│  │ • orderedIds: string[]                           │       │
│  │ • roomId, hydratedFromDb, version                │       │
│  │ • upsertMessage(), upsertMany(), removeMessage() │       │
│  │ • cancelAllNonTerminal(), setRoom(), clearRoom() │       │
│  └─────────────────────────────────────────────────┘       │
│                                                             │
│  Layer 2b: Streaming Display Buffers (Zustand)              │
│  ┌─────────────────────────────────────────────────┐       │
│  │ useStreamingStore                                │       │
│  │ • buffers: Record<messageId, StreamBuffer>       │       │
│  │ • Ephemeral token/artifact chunks during SSE     │       │
│  │ • Cleared on task_update checkpoint / reconcile  │       │
│  │ • UI: useStreamBuffer(id), useResultStreamDisplay│       │
│  │ • Pure helpers: lib/streaming/display.ts         │       │
│  └─────────────────────────────────────────────────┘       │
│                                                             │
│  Layer 3: Ephemeral UI State (Zustand)                      │
│  ┌─────────────────────────────────────────────────┐       │
│  │ useRoomUiStore                                   │       │
│  │ • Per-room flags keyed by roomId (sending, etc.) │       │
│  │ • sseEnabled, sseConnected, sseError             │       │
│  │ • pendingRoomData (cross-page data transfer)     │       │
│  └─────────────────────────────────────────────────┘       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 7.2 Message Store — Normalized Entity Pattern

The message store uses a **normalized entity** pattern (similar to Redux Entity Adapter) for high-performance rendering:

```
                    ┌──────────────────────┐
  Sources:          │   useMessageStore    │      Selectors:
                    │                      │
  DB Hydration ────►│  entities: {         │────► useOrderedIds()
  SSE Events  ────►│    "msg-1": {...},   │────► useMessage(id)
  Optimistic  ────►│    "msg-2": {...},   │────► useOrderedMessages()
                    │  }                   │────► useMessageCount()
                    │  orderedIds: [...]   │────► useMessagesHydrated()
                    │                      │
                    └──────────────────────┘

  Source Priority: sse > db > optimistic
  (Higher-priority sources overwrite lower-priority data)
```

Each `MessageEntity` carries:
- `id`, `roomId`, `messageType` (user | agent)
- `displayType` (user-bubble | agent-bubble | task-status)
- `content`, `senderName`, `agentId`, `userId`
- `taskStatus`, `taskContent`, `taskError`, `stepNumber`, `totalSteps`
- `sourceVersion`, `source` (sse | db | optimistic), `timestamp`
- `isEphemeral` (boolean — processing placeholders, cancel confirmations)

#### Display Type Resolution (`resolve-display-type.ts`)

The `displayType` is computed at write time (`user-bubble` | `agent-bubble`). Agent messages use a unified bubble; **phase** (waiting, streaming, HITL, failed, complete) is derived at render time via turn view models and `lib/streaming/display.ts` helpers.

Live token streaming reads `useStreamingStore` buffers; permanent content lands in `useMessageStore` on `task_update` checkpoint.

#### Upsert Conflict Resolution Rules (`upsert.ts`)

When multiple sources write the same message ID, these rules determine the outcome:

1. **Never downgrade terminal status** — If existing entity has a terminal `taskStatus` (completed/failed/canceled/rejected), an incoming non-terminal status is rejected.
2. **SSE wins over DB for non-terminal states** — If the entity was written by SSE and is non-terminal, a DB write is rejected (SSE is more up-to-date).
3. **Never overwrite ephemeral from DB** — Ephemeral messages (processing placeholders) are protected from DB reconciliation.
4. **Skip no-op updates** — If no rendering-visible fields changed, the update is silently dropped.
5. **undefined preserves, null clears** — In `mergeIncoming()`, `undefined` fields keep the existing value; explicit `null` clears it.

#### Message Sort Order (`buildSortedIds()`)

Messages are sorted by: timestamp (primary) → stepNumber within same workflow batch (messages < 60s apart) → message ID (tiebreaker).

---

## 8. API Layer

### 8.1 API Client Architecture

All API calls flow through a centralized `apiClient` function:

```
Component / Hook
      │
      ▼
  lib/api/*.ts           ← Domain-specific API functions
      │
      ▼
  lib/api-client.ts      ← Centralized fetch wrapper
      │                     • Auto-injects Clerk Bearer token
      │                     • 60s default timeout
      │                     • AbortController support
      │                     • ApiError class (4xx vs 5xx)
      ▼
  Backend API             ← Python backend at NEXT_PUBLIC_API_BASE_URL
  (direct calls)
```

**The frontend calls the backend directly** (not through Next.js API routes for most calls). The `getApiUrl()` helper constructs URLs like: `{NEXT_PUBLIC_API_BASE_URL}{NEXT_PUBLIC_API_PREFIX}/{service}`.

Next.js API routes (`/api/*`) exist as **legacy proxy stubs** that forward to the same backend. They are **not actively used** by the current `lib/api/*.ts` client layer, which calls the backend directly via `getApiUrl()`. These proxy routes may have been used in an earlier architecture or kept for potential future use (e.g., adding server-side auth or rate limiting at the edge).

### 8.2 API Domains

| Module | `getApiUrl()` Key | Actual Path | Key Operations |
|---|---|---|---|
| **Agent** | `agent` | `/api/v1/agent` | registerAgent, getAgent, getAllActiveAgents, updateAgent, deleteAgent |
| **Agent Group** | `agentGroups` | `/api/v1/agentGroups` | CRUD for user-defined agent groups |
| **Room** | `roomCenter` | `/api/v1/roomCenter` | createNewRoom, inquiryRoomSetting, sendMessage, suggestAgents |
| **SSE** | `sse` | `/api/v1/sse` | SSE stream connection, status check, cancelMessage |
| **Task** | `task` | `/api/v1/task` | queryTask, queryBaseTask, getAllSessions |
| **Orchestration** | `orchestrationCenter` | `/api/v1/orchestrationCenter` | decomposeTask, assignAgents, runWorkflow, summarize |
| **Memory** | `memoryCenter` | `/api/v1/memoryCenter` | addChatContext, getChatContextBySessionId |
| **Inspection** | `inspectionCenter` | `/api/v1/inspectionCenter` | inspectAgentCard, inspectA2AConnection |
| **A2A Tasks** | `a2a-tasks` | `/api/v1/a2a-tasks` | getTaskStatus, listRoomTasks, listUserPendingTasks |
| **Health** | *(no prefix)* | `/health` | Backend health check (no auth) |

---

## 9. State Management

### 9.1 Zustand Stores

**`useMessageStore`** (Normalized Entity Store):
- Per-room message storage with `setRoom()` / `clearRoom()` lifecycle
- Merge-on-write upsert with source priority (`sse > db > optimistic`)
- Automatic display type derivation via `resolveDisplayType()`
- Stale task detection on DB hydration
- Hydration filtering (deduplication, stale removal)

**`useRoomUiStore`** (Ephemeral UI Store):
- Transient flags: `sending`, `processing`, `cancelling`, `updatingRoom`
- SSE connection state: `sseEnabled`, `sseConnected`, `sseError`
- Cross-page data: `pendingRoomData` (passes initial message from chat page to room page)

### 9.2 React Query Usage

- **Room settings**: `queryKey: ['room', roomId]` — fetched once per room visit, refetched on settings change
- **Agent catalog**: `queryKey: ['agents', 'active']` — staleTime 24h, used for @mentions and agent name resolution
- **My agents**: `queryKey: ['agents', 'my']` — developer's registered agents, 30s stale time

---

## 10. Real-Time Communication (SSE)

### 10.1 SSE Connection Architecture

```
┌──────────────┐                                            ┌─────────────┐
│   Browser    │                                            │   Backend   │
│              │                                            │   (Python)  │
│ EventSource ─┼──GET (direct to backend)──────────────────►│  SSE Server │
│              │◄─────────────── text/event-stream ─────────┤             │
└──────────────┘                                            └─────────────┘
```

The `SSEConnection` class calls the backend **directly** via `getApiUrl('sse')` (e.g., `{BACKEND_URL}/api/v1/sse/room/{id}/stream`). Auth tokens are passed as query parameters since `EventSource` doesn't support custom headers.

> **Note:** Next.js API routes at `/api/sse/[...endpoint]/` exist as an SSE proxy fallback, but the current `SSEConnection` implementation bypasses them in favor of direct backend calls.

### 10.2 SSE Event Types

| Event Type | Description | Data Fields |
|---|---|---|
| `connected` | Connection established | — |
| `heartbeat` | Keep-alive | — |
| `user_message` | Echo of user message | message_id, user_id, content |
| `agent_response` | Direct agent response | message_id, agent_id, content |
| `processing_status` | Processing lifecycle | status (processing/completed/canceled/failed/rate_limited) |
| `task_submitted` | New A2A task created | message_id, task_id, agent_name, status |
| `task_update` | A2A task state change | message_id, status (TaskState), content, error |
| `error` | Error notification | error, error_type, rate limit details |

### 10.3 SSE Hook Hierarchy

```
useRoomWebhook (orchestration)
    │
    ├── useRoomSSE (connection management)
    │       │
    │       └── SSEConnection class (EventSource wrapper)
    │           • Auto-reconnect (5 attempts, linear backoff: 1s, 2s, 3s...)
    │           • Manual disconnect on room change
    │
    ├── handleSSEMessage callback
    │       │
    │       ├── user_message    → upsertMessage(source: 'sse')
    │       ├── agent_response  → upsertMessage(source: 'sse')
    │       ├── task_submitted  → remove placeholder + upsert task
    │       ├── task_update     → upsert with TaskState
    │       ├── processing_status
    │       │     ├── "processing" → setProcessing(true)
    │       │     └── "completed"  → setProcessing(false), reconcile
    │       └── error           → banner notification
    │
    └── Reconciliation
            • On SSE disconnect during processing → refetch from DB
            • On send error → reconcileWithDb()
```

---

## 11. Chat Room Interaction Flow

### 11.1 Room Creation Flow

```
Chat Page (/c/chat)
    │
    │  User types message + selects agent group
    │
    ▼
useChatRoomCreation.createAndNavigate()
    │
    ├── 1. createNewRoom() API call
    │      (room_name, agents, debate mode, group)
    │
    ├── 2. Store pending data in useRoomUiStore
    │      (initialMessage, targetGroup)
    │
    ├── 3. Dispatch 'rooms:refresh' event (sidebar)
    │
    └── 4. router.push(`/room/${roomId}`)
              │
              ▼
Room Page (/c/room/[id])
    │
    ├── 5. useRoomWebhook initializes:
    │      • Load room settings (React Query)
    │      • Hydrate messages from DB → useMessageStore
    │      • Establish SSE connection
    │
    ├── 6. Consume pendingRoomData from store
    │      → sendUserMessage(initialMessage, targetGroup)
    │
    └── 7. Enter real-time SSE loop
```

### 11.2 Message Send Flow

```
User types message
    │
    ▼
handleSendMessage(text, targetGroup, quoteData?)
    │
    ├── 1. Optimistic insert: user message + processing placeholder
    │      (source: 'optimistic', isEphemeral: true for placeholder)
    │
    ├── 2. API call: SendMessage(room_id, text, target_group, ...)
    │      → Backend creates message + auto-triggers orchestration
    │
    ├── 3. Swap temp ID → real message_id in store
    │      setSending(false), setProcessing(true)
    │
    ├── 4. SSE: task_submitted events arrive
    │      → Remove processing placeholder
    │      → Upsert task entity with status "working"
    │
    ├── 5. SSE: task_update events arrive
    │      → Update task status (working → completed/failed)
    │      → Merge content into entity
    │
    └── 6. SSE: processing_status "completed"
           → setProcessing(false)
           → Reconcile with DB if SSE had disconnections
```

### 11.3 Cancellation Flow

```
User clicks Cancel
    │
    ├── 1. setCancelling(true)
    ├── 2. API call: cancelMessage(messageId)
    ├── 3. Batch cancel all non-terminal tasks in store
    ├── 4. Start 15s safety timeout
    │
    └── SSE: processing_status "canceled"
           → setCancelling(false), setProcessing(false)
           → Insert cancel confirmation message
           → Banner: "Processing stopped by user"
           
    (If timeout fires before SSE confirmation:)
           → Banner: "Cancellation timed out"
           → Force clear processing/cancelling state
```

---

## 12. Authentication

```
┌─────────────────────────────────────────┐
│              Clerk Auth Flow             │
├─────────────────────────────────────────┤
│                                         │
│  1. ClerkProvider (root layout)         │
│     └── Manages auth state globally     │
│                                         │
│  2. ClerkAuthProvider (custom)          │
│     └── Calls setDefaultGetToken()      │
│         so apiClient can auto-attach    │
│         Bearer tokens                   │
│                                         │
│  3. Middleware (clerkMiddleware)         │
│     └── Validates sessions              │
│     └── Authorized parties:             │
│         hybro.ai, developer.hybro.ai    │
│         localhost:3000, dev.localhost    │
│                                         │
│  4. API Client (apiClient)              │
│     └── getClientAuthHeaders()          │
│         → Bearer ${token} header        │
│                                         │
│  5. SSE Connection                      │
│     └── Token as query param            │
│         (EventSource limitation)        │
│                                         │
└─────────────────────────────────────────┘
```

Unauthenticated users are redirected to `/sign-in` or shown the waitlist modal (configurable via `NEXT_PUBLIC_ENABLE_WAITLIST`).

---

## 13. Key Interaction Diagrams

### 13.1 Full Room Page Component Hierarchy

```
RoomChatPage
├── header
│   ├── Room name + agent count
│   ├── SSE status indicator (green/yellow/red dot)
│   ├── Debate mode badge
│   └── Settings button → Dialog
│       └── RoomSettingForm (name, agents, debate mode)
│
├── main (flex-1)
│   └── RoomMessages
│       └── orderedIds.map(id =>
│           └── MemoizedMessage(id)        ← Per-message isolation
│               ├── if user-bubble → MessageBubble (user)
│               ├── if agent-bubble → MessageBubble (agent, markdown)
│               ├── if task-status → TaskStatusMessage
│               │   └── Workflow step, status badge, content
│               └── if isEphemeral → Processing placeholder
│           )
│
├── footer
│   └── RoomChatInput
│       ├── GroupSelector (All Agents / Room Team / custom)
│       ├── Textarea with @mention support
│       ├── Quote indicator (reply-to)
│       └── Send / Cancel buttons
│
└── GroupManagementModal
    └── CRUD for agent groups
```

### 13.2 Developer Portal Flow

```
Developer Portal (/d)
├── Register Agent (/d/register)
│   ├── Enter agent URL
│   ├── Fetch agent card (A2A protocol)
│   ├── Inspect & validate A2A compliance
│   ├── Configure settings (rate limits, visibility)
│   └── Register → Backend stores agent
│
├── My Agents (/d/agents)
│   ├── List registered agents (useMyAgents hook)
│   └── Agent Detail (/d/agents/[id])
│       └── Update settings, toggle active/inactive
│
├── Inspector (/d/inspector)
│   └── A2A compliance testing tool (external link)
│
└── Docs (/d/docs)
    └── Developer documentation
```

### 13.3 Agent Group & Targeting System

```
┌─────────────────────────────────────────────────────┐
│               Message Targeting Flow                 │
├─────────────────────────────────────────────────────┤
│                                                     │
│  GroupSelector (in chat input)                      │
│  ├── "All Agents" (builtin)                         │
│  │     → Backend routes to best matching agents     │
│  │       via suggestAgents() + semantic routing      │
│  ├── "Room Team" (builtin, if room has agents)      │
│  │     → Only room's assigned agents respond        │
│  └── Custom Groups (user-created)                   │
│        → User-defined sets of agents                │
│                                                     │
│  Override Logic:                                    │
│  • Default: Room Team (if agents) or All Agents     │
│  • User can override per-message via GroupSelector  │
│  • Override persists in localStorage per room       │
│  • "Clear override" reverts to default              │
│                                                     │
└─────────────────────────────────────────────────────┘
```

### 13.4 Workflow Orchestration (Legacy)

The `useWorkflow` hook manages a multi-step task orchestration lifecycle:

```
Base Task Created
    │
    ├── DECOMPOSED: Task split into meta tasks
    │   └── handleAssignAgents()
    │
    ├── AGENTS_ASSIGNED: Each meta task has an agent
    │   └── handleRunWorkflow()
    │
    ├── RUNNING: Agents executing meta tasks
    │   └── handleSummarizeResults()
    │
    └── COMPLETED: Base task has final summary
```

This is used for complex multi-agent workflows where tasks are decomposed, assigned, executed, and summarized through the Orchestration Center backend APIs.

---

## 14. Conventions & Patterns for Contributors

### 14.1 Adding a New Page

1. Create a file under `src/app/c/` (consumer) or `src/app/d/` (developer) following Next.js App Router conventions.
2. Consumer pages are accessed at `hybro.ai/{path}`, developer pages at `developer.hybro.ai/{path}`. The middleware rewrites these to `/c/{path}` and `/d/{path}` internally.
3. All page components that use hooks (`useState`, `useEffect`, Clerk hooks, etc.) must include `'use client'` at the top. Server components (layouts, metadata-only pages) do not.
4. Auth-gated pages should use `useUser()` from Clerk and redirect unauthenticated users.

### 14.2 Adding a New API Endpoint

1. Add a function in the appropriate `src/lib/api/*.ts` file.
2. Use `apiGet`, `apiPost`, `apiPut`, or `apiDelete` from `lib/api-client.ts` — they auto-inject auth tokens.
3. Define request/response types in `src/lib/types/`.
4. Export from `src/lib/api/index.ts` barrel file.
5. The Next.js API route proxies under `src/app/api/` are **legacy/unused** — do not add new ones.

### 14.3 Adding a New SSE Event Handler

1. Define the event type in `src/lib/types/sse.ts` (add to the `SSEMessage.type` union).
2. Handle it in the `handleSSEMessage` callback inside `src/hooks/useRoomWebhook.ts`.
3. Write to the message store via `useMessageStore.getState().upsertMessage()` with the appropriate source (`'sse'`).

### 14.4 Code Style & Patterns

- **State**: Use Zustand for client-only state, React Query for server-fetched data. Do not use React Context for state that changes frequently.
- **Components**: shadcn/ui components live in `src/components/ui/`. Business components live directly in `src/components/`.
- **Hooks**: Custom hooks go in `src/hooks/`. Name them `use{Feature}.ts`.
- **Notifications**: Use `banner.success()`, `banner.error()`, `banner.info()`, `banner.warning()` from `@/components/ui/banner` for in-app banners.
- **Styling**: Tailwind CSS v4 with `cn()` utility from `lib/utils.ts`. Dark mode is the default theme.
- **Testing**: Unit tests use Vitest + Testing Library. Test files live in `__tests__/` directories adjacent to their source. Run `npm test` or `npm run test:watch`.

### 14.5 Key Gotchas

- `getApiUrl()` calls the **backend directly** (not via Next.js API routes). The browser must have direct network access to `NEXT_PUBLIC_API_BASE_URL`.
- SSE uses `EventSource` which requires same-origin OR the backend must set `Access-Control-Allow-Origin` headers for cross-origin SSE to work.
- The `useRoomWebhook` hook is the single orchestration point for the room page — it coordinates React Query, Zustand message store, Zustand UI store, and SSE. Any new room-level feature should integrate through this hook.
- `pendingRoomData` in `useRoomUiStore` is used to pass the initial message from the chat creation page (`/c/chat`) to the room page (`/c/room/[id]`). It is consumed once on room load.
- Agent group override state persists in `localStorage` per room via key `room-{roomId}-override-group`.

---

## 15. Known Issues, Risks & Future Improvements

### 15.1 Architecture Issues

#### Room orchestration split; SSE handler still large

**Files:** `src/hooks/room/useRoomWebhook.ts` (~220 lines, thin orchestrator), `src/hooks/room/sse-handlers/index.ts` (~850 lines)

`useRoomWebhook` now composes focused hooks (`useRoomData`, `useRoomHydration`, `useRoomSSEConnection`, `useSendMessage`, `useRoomActions`, `processing-lifecycle`). SSE event handling lives in `createSSEDispatcher()` and still mutates `useMessageStore` / `useStreamingStore` via `getState()` (and room UI indirectly via lifecycle).

**Risk:** The SSE dispatcher remains hard to test in isolation and couples the event protocol to store shapes.

**Recommendation:** Continue splitting `sse-handlers/` into per-type handlers + `applyRoomCommands`. DB hydration is unified in `src/lib/room-sync/` (`hydrateRoomFromDb`); see `docs/ROOM_SYNC_REFACTOR.md`.

---

#### Dead Code: Workflow Subsystem

**Files:** `src/hooks/useWorkflow.ts`, `src/components/workflow-message.tsx`, `src/components/workflow-container.tsx`

The entire workflow orchestration subsystem (decompose → assign → run → summarize) is **never imported** by any page or parent component. `WorkflowContainer` is only referenced within its own file. These files are dead code from a legacy multi-step orchestration approach that has been superseded by the SSE-based task flow.

**Recommendation:** Remove or archive these files to reduce confusion. If the workflow UI is needed again, it should be rebuilt against the current SSE/message-store architecture. See `DEAD_CODE_CLEANUP.md` for the full removal plan.

---

#### Dead Code: Legacy API Route Proxies

**Files:** All routes under `src/app/api/` (agent, roomCenter, orchestrationCenter, task, memory, inspectionCenter, health)

These Next.js API route proxy handlers forward requests to the backend, but the current `lib/api/*.ts` client layer bypasses them entirely via direct `getApiUrl()` calls. They add ~500 lines of maintenance burden with no active consumers.

**Recommendation:** Remove unless there's a specific plan to use them (e.g., server-side token injection, rate limiting at edge). See `DEAD_CODE_CLEANUP.md` for the full removal plan.

---

#### Dead Code: `createAndParseUserMessage` and `processRoomUserMessage`

**Files:** `src/lib/api/room.ts` (line 133), `src/lib/api/orchestration.ts` (line 130)

These functions are defined but never called — superseded by the unified `SendMessage` API which handles message creation and processing orchestration in a single backend call.

**Recommendation:** Remove to reduce API surface confusion.

---

### 15.2 Security Risks

#### SSE Auth Token in URL Query Parameter

**File:** `src/lib/api/sse.ts` (line 53)

The Clerk JWT is passed as `?token=...` in the SSE URL because `EventSource` does not support custom headers. While the code redacts the token in console logs, the token is visible in:
- Browser network inspector (URL column)
- Server access logs
- Any proxy/CDN that logs full URLs
- Browser history

**Risk:** Token leakage through URL logging. Clerk JWTs are short-lived (60s default), which limits the window, but it's still a security surface.

**Recommendation:** Consider migrating from `EventSource` to `fetch()`-based SSE streaming, which supports custom `Authorization` headers. Alternatively, use a one-time-use connection token exchanged server-side.

---

#### No CSRF Protection on API Calls

The `apiClient` sends authenticated requests directly to the Python backend with `Bearer` tokens. Since tokens are obtained from Clerk's `getToken()` and not from cookies, traditional CSRF attacks are mitigated. However, the backend must ensure it validates the Bearer token on every request and does not also accept cookie-based auth, which would re-introduce CSRF risk.

---

### 15.3 Reliability Risks

#### SSE as Single Real-Time Channel (No Fallback)

The room page relies entirely on `EventSource` for live updates. If SSE fails or is blocked (corporate firewalls, aggressive proxies), users see no task updates after sending a message. The only recovery is a manual page refresh or the `reconcileWithDb()` call that fires after processing completes — but if SSE never delivers the "completed" signal, the UI stays in "Processing..." indefinitely.

**Mitigation in place:**
- Auto-reconnect with 5 attempts + linear backoff
- Stale task detection on DB hydration (10-minute threshold)
- Processing placeholder restore on page reload
- SSE disconnection tracking + post-processing reconciliation

**Recommendation:** Add a polling fallback: if SSE is disconnected for >30s during active processing, start a slow poll (e.g., every 10s) against `inquiryRoomMessagesByRoomId` until SSE reconnects.

---

#### No Pagination for Room Messages

**File:** `src/lib/api/room.ts` — `inquiryRoomMessagesByRoomId`

The message hydration loads **all messages for a room** in a single request. For rooms with hundreds or thousands of messages, this will cause:
- Slow initial load times
- High memory usage in the browser (all entities held in Zustand)
- Large JSON parse blocking the main thread

**Recommendation:** Implement cursor-based pagination: load the most recent N messages, then fetch older messages on scroll-up. The normalized store already supports incremental `upsertMany` which makes this straightforward.

---

#### Streaming subscriptions in UI

**Files:** `src/hooks/useStreamBuffer.ts`, `src/lib/streaming/display.ts`, `src/components/room-page-shell.tsx`

Components should subscribe to `s.buffers[messageId]` (via `useStreamBuffer` / `useResultStreamDisplay`), not the full `buffers` map, so unrelated token chunks do not re-render the room shell or index rows.

---

### 15.4 Code Quality

#### Verbose `console.log` Throughout Production Code

There are **~84 `console.log` calls** across hooks and API files (emoji-prefixed debug statements like `🚀`, `✅`, `❌`, `📨`). While `next.config.ts` has `removeConsole` configured, it only strips `console.log` in **production builds** — dev builds and staging environments still log extensively.

The API layer (`room.ts`, `orchestration.ts`) also logs full request/response payloads via `JSON.stringify(requestData, null, 2)`, which may expose sensitive data in dev consoles.

**Recommendation:** Replace ad-hoc `console.log` calls with a structured logger (e.g., a thin wrapper that respects log levels). Remove payload logging from API functions or gate it behind a debug flag.

---

#### Test Coverage Concentrated in Message Store

Tests exist for `stores/message-store/` (7 test files) and streaming-related stores (streaming-lifecycle, typewriter). There are **no tests** for:
- Hooks (`useRoomWebhook`, `useRoomSSE`, `useChatRoomCreation`, `useGroupManagement`)
- API client functions (`lib/api/*.ts`, `lib/api-client.ts`)
- Components (room page, chat page, message rendering)
- Middleware (subdomain routing logic)

**Recommendation:** Priority test additions:
1. `middleware.ts` — subdomain routing logic (pure function, easy to unit test)
2. `lib/api-client.ts` — error handling, timeout, abort behavior
3. `useRoomWebhook` — SSE message handling, optimistic update lifecycle, cancellation flow (use `@testing-library/react` + MSW for mocking)

---

#### Inconsistent API Function Naming

API functions mix naming conventions:
- `PascalCase`: `SendMessage()` in `room.ts`
- `camelCase`: `createNewRoom()`, `inquiryRoomSetting()`, `suggestAgents()`
- Backend-style: `inquiryRoomsByRoomOwnerId()`, `getAgentCardFromUrl()`

**Recommendation:** Standardize to `camelCase` verb-noun pattern (e.g., `sendMessage`, `getRoomSettings`, `listRoomsByOwner`).

---

### 15.5 Performance Considerations

#### Full Agent Catalog Loaded on Every Room Entry

**File:** `src/hooks/useRoomWebhook.ts` (line 109)

`allAgentsQuery` fetches the entire active agents list on room entry (staleTime 24h, so cached across rooms within a session). This is used for agent name resolution in SSE messages. For a platform with hundreds of registered agents, this payload will grow linearly.

**Recommendation:** Switch to on-demand agent name resolution: maintain a lightweight cache and fetch individual agent names only when encountering an unknown `agent_id` in an SSE event. Or have the backend include `agent_name` in every SSE event payload (partially done already).

---

#### No Virtualized Message List

**File:** `src/components/room-messages.tsx`

All messages render as DOM elements (with per-message isolation via `useMessage(id)` selectors). For rooms with 100+ messages, this creates a large DOM tree. The normalized store and shallow selectors minimize re-renders, but the DOM size itself remains a concern.

**Recommendation:** Adopt a virtualized list (e.g., `@tanstack/react-virtual`) that only renders messages visible in the viewport, plus a small overscan buffer.

---

#### Room Settings Updated via 3 Sequential API Calls

**File:** `src/hooks/useRoomWebhook.ts` — `updateRoomSettings()`

Updating room settings fires up to 3 separate API calls sequentially: `updateRoomName` → `updateRoomAgentSet` → `updateRoomExtendInfo`. If any call fails midway, the room is left in a partially-updated state.

**Recommendation:** Create a single backend endpoint (e.g., `PATCH /roomCenter/updateRoom`) that accepts all fields atomically.

---

### 15.6 Future Improvement Ideas

| Area | Improvement | Impact | Status |
|---|---|---|---|
| **Real-time** | Migrate SSE to WebSocket or `fetch()`-based streaming for header-based auth | Security, reliability | Open |
| **Offline** | Add optimistic message queue with retry for network failures | UX | Open |
| **Performance** | Message pagination + virtual scrolling | Scalability | Design doc: `MESSAGE_PAGINATION_DESIGN.md` |
| **Testing** | E2E tests with Playwright for critical flows (room creation -> message -> response) | Reliability | Open |
| **Observability** | Structured error reporting (Sentry/LogRocket) replacing console.log | Debugging | Open |
| **DX** | Storybook for component library (shadcn/ui + business components) | Development speed | Open |
| **API** | Typed API client generated from OpenAPI spec (backend -> frontend types) | Type safety, DRY | Open |
| **State** | Persist partial message store in IndexedDB for instant room re-entry | UX | Open |
| **A11y** | Keyboard navigation audit for chat input, message list, and modals | Accessibility | Open |
| **i18n** | Extract hardcoded English strings into a localization framework | Internationalization | Open |
| **Multi-modal** | Agent artifact rendering + user file uploads | Feature completeness | Design docs: `ARTIFACT_RENDERING_DESIGN.md`, `MULTIMODAL_SUPPORT_DESIGN.md` |
| **Reliability** | Retry UI for failed tasks | UX | Design doc: `TASK_RETRY_DESIGN.md` |
| **Maintenance** | Remove ~1,675 lines of dead code | Code health | Design doc: `DEAD_CODE_CLEANUP.md` |
