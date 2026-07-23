# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
npm run dev          # Next.js dev server with Turbopack (port 3000)
npm run build        # Production build
npm run lint         # ESLint
npm run test         # Vitest (all projects, once)
npm run test:watch   # Vitest in watch mode
npm run test:e2e     # Playwright (Chromium, against localhost:3000)
```

Run a single test file:
```bash
npx vitest run src/stores/message-store/__tests__/store.test.ts
```

Run a single e2e test:
```bash
npx playwright test tests/e2e/some-test.spec.ts
```

Node version: 20.19 (`.nvmrc`). Install: `npm install`.

## Architecture

### Dual-Portal via Subdomain Routing

The app serves **two portals** from one Next.js deployment:

- `/c/*` — **Consumer portal** (hybro.ai): chat rooms, agent discovery, pricing
- `/d/*` — **Developer portal** (developer.hybro.ai): agent registration, hub management, inspector

`src/proxy.ts` (exported as middleware) inspects the request hostname and rewrites `/` → `/c/` or `/d/` accordingly. Local dev uses `localhost` → consumer, `dev.localhost` → developer, or `?_subdomain=developer` query param.

### Real-Time Chat Pipeline

The core user experience flows through:

1. **Room creation** (`/c/chat` → `useChatRoomCreation`) — creates room via REST, navigates to `/c/room/[id]`
2. **Room hydration** (`useRoomHydration`) — fetches persisted messages from DB, populates the message store
3. **SSE connection** (`src/lib/api/sse.ts` → `SSEConnection`) — fetch-based streaming with reconnect/backoff, receives `AnySSEFrame` events
4. **SSE handlers** (`src/hooks/room/sse-handlers/`) — parse frames, dispatch to message store and processing status
5. **Message store** (`src/stores/message-store/`) — Zustand store holding `MessageEntity` records keyed by ID, with `orderedIds` for display order
6. **Turn view models** (`src/lib/room-timeline/build-turns.ts`) — groups messages into conversation turns with primary stream, activity strip, and final answer surfaces
7. **Rendering** (`src/components/conversation/`) — `ConversationMessageList` → `TurnRenderer` → `TurnBody` → individual blocks (AgentCard, AgentContentBlock, FinalAnswerSurface, etc.)

`client_request_id` is the critical correlation key linking a user's sent message to the backend processing run and all resulting agent responses.

### State Management

- **`message-store`** — normalized entity store for all messages in the active room. Handles upserts from both DB hydration and live SSE frames.
- **`room-ui-store`** — per-room UI state: selected agent message for detail pane, scroll positions, local send sequence counter.
- **`streaming-store`** — buffer for in-flight streaming text chunks before they're committed to the message store.

### API Layer

- `src/lib/api-client.ts` — centralized fetch wrapper with `ApiError` class, auto-attaches Clerk auth headers
- `src/lib/api/` — domain-specific API modules (room, agent, hub, hitl, files, sse)
- Backend URL configured via `NEXT_PUBLIC_API_BASE_URL` + `NEXT_PUBLIC_API_PREFIX` (default: `http://localhost:8000/api/v1`)

### Authentication

Clerk handles auth. `@clerk/nextjs` provides `ClerkProvider` at root, middleware validates sessions via `clerkMiddleware` in `proxy.ts`. Client-side uses `useAuth().getToken()` for API calls.

### UI System

- Tailwind CSS v4 with CSS custom properties for theming (HSL-based light/dark tokens in `globals.css`)
- shadcn/ui primitives in `src/components/ui/`
- Conversation-specific design tokens in `src/components/conversation/conversation-tokens.css`
- Fonts: Plus Jakarta Sans (body), Space Grotesk (brand/headings via `font-spaceGrotesk` utility)

### Key Patterns

- **HITL (Human-in-the-Loop)**: agents can request user input mid-processing; handled via `hitl-overlay.ts` and `HitlResponseBar` component
- **Agent Groups**: users can organize agents into groups for targeted message dispatch
- **Chat Modes**: normal, debate, supervisor — determined per-message and stored in room settings
- **Resizable Detail Pane**: `RoomPageShell` uses `react-resizable-panels` for split-view agent response inspection on desktop, bottom sheet on mobile

## Coding Conventions

- TypeScript strict mode, `@/*` path alias from `src/`
- 2-space indent, single quotes, no semicolons
- PascalCase components, `use*` hooks, `*.test.ts` colocated or in `tests/unit/`
- Vitest projects: `stores` (node), `api` (node + msw setup), `components` (jsdom)
- Prefer feature folders; shared UI in `src/components/ui/`
- Commit messages: concise imperative subject with conventional prefixes (`feat:`, `fix:`, `docs:`, `chore:`), scoped to one change

## Documentation Updates

After changes affecting routes, data flow, API integrations, SSE/streaming, state management, module boundaries, auth, or major UI workflows — review and update `docs/System-Architecture.md`. Document the current behavior, not the implementation journey.

Do not add or commit `superpowers/` or related planning artifacts.

## Workflow Notes

- For review-only requests, do not edit files.
- For room/SSE/streaming/HITL/processing changes, treat `client_request_id` as critical correlation data.
- For visible UI changes, verify in a browser when feasible — check layout, text overflow, and interactions across viewports.
- Before editing, check `git status` and preserve other agents' or users' in-progress changes.
- In handoff summaries: include changed behavior, tests run, documentation updates, and any verification that could not be completed.

## Environment

Copy `.env.example` → `.env.local`. Key vars:
- `NEXT_PUBLIC_API_BASE_URL` — backend URL
- `NEXT_PUBLIC_CONSUMER_URL` / `NEXT_PUBLIC_DEVELOPER_URL` — subdomain routing
- `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` / `CLERK_SECRET_KEY` — auth
