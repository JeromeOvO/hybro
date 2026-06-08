# Hybro Frontend Architecture

> Last scanned: 2026-06-04
>
> Source of truth: current repository files under `src/`, `tests/`, and root config files. Historical design notes in `docs/` are not treated as current architecture.

## 1. Overview

Hybro Frontend is a Next.js App Router application for the Hybro multi-agent platform. A single codebase serves two portals:

- Consumer portal: chat, agent discovery, room timelines, HITL replies, file attachments, hub status, pricing, and public information pages.
- Developer portal: developer dashboard, agent registration and management, discovery API keys, agent inspection, documentation, and hub status.

The app talks to the backend through REST APIs and room-scoped Server-Sent Events (SSE). The room UI uses normalized message state, transient streaming buffers, selector-driven view models, and a conversation renderer built around turns rather than raw message rows.

## 2. Current Scan Summary

| Area | Current count / source |
|---|---|
| Source files | 263 files under `src/` |
| Test/support files | 103 files under `tests/` |
| App Router files | 28 files under `src/app/` |
| Component files | 109 files under `src/components/` |
| shadcn/ui primitives | 27 files under `src/components/ui/` |
| Conversation components | 17 files under `src/components/conversation/` |
| Hooks | 41 files under `src/hooks/` |
| Library modules | 65 files under `src/lib/` |
| Stores | 19 files under `src/stores/` |

## 3. Tech Stack

| Category | Technology |
|---|---|
| Framework | Next.js 16 App Router with Turbopack |
| Runtime UI | React 19 |
| Language | TypeScript |
| Styling | Tailwind CSS v4, CSS variables, project CSS tokens |
| Component system | shadcn/ui with Radix primitives, `components.json`, New York style |
| Icons | Lucide React |
| Forms | React Hook Form + Zod |
| Auth | Clerk (`@clerk/nextjs`) |
| Server state | TanStack React Query |
| Client state | Zustand |
| Real-time transport | SSE over `fetch()` streaming |
| Markdown/rendering | Streamdown + rehype-highlight |
| Agent protocol | `@a2a-js/sdk` |
| Testing | Vitest, Testing Library, MSW, Playwright |

## 4. Root Config And Tooling

| File | Purpose |
|---|---|
| `package.json` | npm scripts and dependency manifest |
| `package-lock.json` | locked dependency graph |
| `.nvmrc` | recommended Node version (`20.9`) |
| `next.config.ts` | Next.js configuration |
| `tsconfig.json` | TypeScript compiler configuration |
| `eslint.config.mjs` | ESLint 9 configuration |
| `vitest.config.ts` | unit/integration test configuration |
| `playwright.config.ts` | e2e test configuration |
| `components.json` | shadcn/ui generator aliases, Tailwind CSS entry, icon library |
| `postcss.config.mjs` | Tailwind/PostCSS pipeline |

Available package scripts:

```bash
npm run dev
npm run build
npm run start
npm run lint
npm run test
npm run test:watch
npm run test:coverage
npm run test:ui
npm run test:e2e
npm run test:e2e:ui
npm run test:e2e:headed
npm run test:all
```

`npm run lint` currently invokes `next lint`; with the current Next.js CLI this may fail before linting by treating `lint` as a project directory. Build and tests are the practical validation gates until the lint script is updated.

## 5. App Routing

Routes are defined under `src/app/`.

```text
src/app/
|-- layout.tsx
|-- globals.css
|-- favicon.ico
|-- robots.ts
|-- sitemap.ts
|-- privacy/page.tsx
|-- (auth)/
|   |-- layout.tsx
|   |-- sign-in/[[...sign-in]]/page.tsx
|   `-- sign-up/[[...sign-up]]/page.tsx
|-- c/
|   |-- layout.tsx
|   |-- page.tsx
|   |-- about/page.tsx
|   |-- about/about-cta-button.tsx
|   |-- agents/page.tsx
|   |-- agents/[id]/page.tsx
|   |-- chat/page.tsx
|   |-- hub/page.tsx
|   |-- pricing/page.tsx
|   `-- room/[id]/page.tsx
`-- d/
    |-- layout.tsx
    |-- page.tsx
    |-- agents/page.tsx
    |-- agents/[id]/page.tsx
    |-- discovery-api-keys/page.tsx
    |-- docs/page.tsx
    |-- hub/page.tsx
    |-- inspector/page.tsx
    `-- register/page.tsx
```

### Subdomain routing

`src/proxy.ts` owns subdomain routing. It runs Clerk middleware and rewrites incoming requests:

- `developer.*` and `dev.*` hosts rewrite to `/d/*`.
- Consumer hosts rewrite to `/c/*`.
- Shared paths are not rewritten: `/api/*`, `/_next/*`, `/sign-in`, `/sign-up`, `/privacy`, `/robots.txt`, and `/sitemap.xml`.
- Static assets are skipped.
- Local development supports `?_subdomain=developer` as a developer-portal fallback.

### Provider hierarchy

`src/app/layout.tsx` wraps the app with:

1. `ClerkProvider`
2. `ClerkAuthProvider`
3. `ThemeProvider`
4. `QueryProvider`
5. `Toaster`
6. `CookieBanner`

Portal layouts add shell providers and chrome:

- `src/app/c/layout.tsx`: `BannerHost`, `SidebarProvider`, `SettingsDialogProvider`, `ConsumerSidebar`, `ConsumerHeader`.
- `src/app/d/layout.tsx`: `BannerHost`, `SidebarProvider`, `SettingsDialogProvider`, `DeveloperSidebar`, `DeveloperHeader`.

## 6. Component Organization

```text
src/components/
|-- ui/                       # shadcn/ui primitives
|-- conversation/             # turn/timeline rendering system
|-- composer/                 # chat composer shell and HITL response bar
|-- consumer/                 # consumer portal sidebar/header/footer
|-- developer/                # developer portal sidebar/header/agent settings
|-- providers/                # Clerk auth bridge and React Query provider
|-- settings/                 # settings dialog sections and helpers
|-- room-page-shell.tsx       # room workspace shell
|-- room-chat-input.tsx       # composer input, mentions, uploads
|-- room-setting-form.tsx     # room settings and room agent defaults
|-- room-default-agents-editor.tsx
|-- group-selector.tsx
|-- group-management-modal.tsx
|-- agent-selector.tsx
|-- agent-card.tsx
|-- consumer-agent-card.tsx
|-- hitl-compact-card.tsx
|-- hitl-inline-reply-form.tsx
|-- hitl-question-card.tsx
|-- artifact-list.tsx
|-- artifact-renderer.tsx
|-- attachment-preview.tsx
|-- markdown-content.tsx
|-- part-renderer.tsx
|-- mode-selector.tsx
|-- nav-main.tsx
|-- nav-agent.tsx
|-- nav-user.tsx
|-- nav-hub.tsx
|-- nav-docs-button.tsx
|-- nav-discord-button.tsx
|-- require-auth.tsx
|-- developer-docs-content.tsx
|-- hub-page-content.tsx
|-- use-case-card.tsx
|-- cookie-banner.tsx
|-- theme-provider.tsx
|-- theme-toggle.tsx
|-- logo.tsx
|-- icons.tsx
`-- video-embed.tsx
```

### Conversation renderer

The current room UI is centered on `src/components/conversation/`:

- `ConversationMessageList.tsx`: top-level message/timeline list.
- `TurnRenderer.tsx`, `TurnBody.tsx`: turn-level rendering.
- `UserMessageBlock.tsx`, `UserAttachmentCard.tsx`, `UserAnswerCard.tsx`: user-side turn content.
- `AgentCard.tsx`, `AgentContentBlock.tsx`, `AgentResultContent.tsx`, `AgentIndex.tsx`: agent response presentation.
- `FinalAnswerSurface.tsx`, `SynthesisContent.tsx`: final/synthesis answer surfaces.
- `AgentResponseDetailPane.tsx`: right-side detail pane for a selected agent response.
- `ScrollToBottomButton.tsx`, `scroll-state.ts`: scroll affordances and state.
- `conversation-tokens.css`, `shimmer.css`: conversation-specific CSS tokens and loading effects.

`src/components/room-page-shell.tsx` owns the room workspace. It renders the conversation list, the composer dock, desktop resizable detail panes, and mobile detail sheets. It also wires selected message state from `room-ui-store`, streaming buffers from `useStreamBuffer`, and detail view models from `selectAgentResponseDetail`.

## 7. Hooks And Room Orchestration

Top-level hooks live in `src/hooks/`; room-specific orchestration lives in `src/hooks/room/`.

```text
src/hooks/
|-- useRoomWebhook.ts          # public re-export/entry hook
|-- useRoomSSE.ts              # low-level SSE connection hook
|-- useRoomMessages.ts         # message selectors
|-- useChatRoomCreation.ts     # room creation and navigation
|-- useGroupManagement.ts      # saved groups and room group selection
|-- useHubStatus.ts            # hub availability
|-- useMessageScrollAnchoring.ts  # legacy; superseded by useTurnFocusScroll for main feed
|-- useTurnFocusScroll.ts       # ChatGPT-style user-message focus + dynamic spacer
|-- useMyAgents.ts
|-- usePrimaryStreamScroll.ts
|-- useStreamBuffer.ts
|-- useTextSelectionQuote.ts
|-- useTurnViewModels.ts
`-- use-mobile.ts
```

```text
src/hooks/room/
|-- useRoomWebhook.ts          # orchestrates room data, SSE, sends, actions
|-- useAgentCatalog.ts
|-- useRoomData.ts
|-- useRoomHydration.ts
|-- useProcessingRestore.ts
|-- useRoomReset.ts
|-- useRoomSSEConnection.ts
|-- useSendMessage.ts
|-- useRoomActions.ts
|-- processing-lifecycle.ts
|-- types.ts
`-- sse-handlers/
```

`useRoomWebhook` composes the room feature:

1. Reads per-room UI flags from `room-ui-store`.
2. Loads agents through `useAgentCatalog`.
3. Loads room settings and room agents through `useRoomData`.
4. Creates a per-room `ProcessingLifecycle`.
5. Resets room-local state when room changes.
6. Hydrates and reconciles DB messages through `useRoomHydration`.
7. Restores active processing placeholders with `useProcessingRestore`.
8. Creates an SSE dispatcher with `createSSEDispatcher`.
9. Connects SSE with `useRoomSSEConnection`.
10. Sends messages through `useSendMessage`.
11. Exposes room actions from `useRoomActions`.

## 8. Room Page Interaction Flow

`src/app/c/room/[id]/page.tsx` is the consumer room page. It is a client component that:

- Reads `roomId` from the route.
- Reads user/auth state from Clerk.
- Calls `useRoomWebhook`.
- Manages local chat mode, quote state, and prefilled input handoff.
- Uses `useGroupManagement` for saved groups and room-team defaults.
- Consumes pending room handoff data from `room-ui-store`.
- Persists chat-mode changes lazily before sending.
- Pre-writes room agent membership for empty rooms when a saved group is selected.
- Passes a `TimelineAdapter` into `RoomPageShell`.

High-level flow:

```text
Room route
  -> RoomChatPage
    -> useRoomWebhook
      -> room setting query
      -> initial DB hydration
      -> SSE connect
      -> normalized message store writes
    -> useGroupManagement
    -> RoomPageShell
      -> ConversationMessageList
      -> ComposerShell
      -> AgentResponseDetailPane / mobile Sheet
```

## 9. State Management

### `src/stores/message-store/`

The message store is the normalized source of truth for persistent room messages.

Main responsibilities:

- Store entities by message id and ordered ids per current room.
- Merge DB, HTTP, and SSE writes through `applyUpsert`.
- Replace optimistic IDs with server IDs.
- Convert backend messages into `IncomingMessage`.
- Filter hydration data.
- Detect stale tasks.
- Infer terminal state from active-run context.
- Resolve display type for renderer consumers.

Key files:

- `index.ts`
- `types.ts`
- `upsert.ts`
- `convert-api-message.ts`
- `hydration-filter.ts`
- `stale-detection.ts`
- `infer-turn-terminal-status.ts`
- `resolve-display-type.ts`

### `src/stores/room-ui-store.ts`

The room UI store contains ephemeral per-room UI state:

- sending / processing / cancelling / updating flags
- SSE enabled / connected / error state
- initial hydration marker
- pending room handoff data
- selected agent-response detail state

### `src/stores/streaming-store/`

The streaming store contains transient live artifact/text buffers. It is intentionally separate from `message-store`: streaming artifacts are displayed live, then cleared after DB reconcile or task checkpoint persistence.

Live buffer text is derived via `extractStreamTextFromArtifacts`, which concatenates all text-only artifacts in emission order (matching backend final assembly). Persisted entity text still uses `extractTextFromArtifacts` (last text-only artifact) for thinking + answer agents — this asymmetry is intentional today and disappears under the AG-UI roadmap (`REASONING_*` events split thinking from answer at the wire layer).

**Streaming invariants** (enforced after the convergence plan in [`docs/STREAMING_UI_ISSUES_AND_FIXES.md`](STREAMING_UI_ISSUES_AND_FIXES.md)):

- **I1** — One live ingest pipeline. All live streaming text flows through `streaming-store.append(message_id, …)`. `agent_response_partial` is a compat shim that maps `content_delta` to a synthetic artifact and calls the same append.
- **I2** — Live buffer key is always `message_id`. `client_request_id` is correlation/cleanup metadata, never a buffer key or display merge dimension.
- **I3** — Live text equals persisted text. `extractStreamTextFromArtifacts` over the live artifact list equals backend `extract_parts_from_artifacts` over the persisted artifact list at terminal.
- **I4** — Detail pane content for terminal entities comes from `message-store`, never from the live buffer (strict terminal guard in `selectAgentResponseDetail`).
- **I5** — Per-agent terminal SSE clears that message's buffer only. Turn-level clear runs exactly once per turn, on user-turn terminal `processing_status`.
- **I6** — `streaming-store/append` does not import `mergeArtifacts` from `message-store/upsert`. Live merge is `mergeStreamArtifacts` (disjoint-segment push, prefix-relation replace).
- **I7** — Streaming UI (badge, cursor, Streamdown caret) is driven only by an incomplete live buffer while the agent view-model status is `working`. Terminal agent status always wins over a stale buffer. Late `artifact_update` frames after terminal `task_update` are ignored and any leftover buffer for that `message_id` is cleared.

**Conversation markdown normalization** (`src/lib/markdown/`, applied in `MarkdownContent` when `className` includes `conversation-markdown-body`):

- **Pre-parse** (`preprocessConversationMarkdown` in `normalize-conversation.ts` + `split-inline-ordered.ts`) runs before Streamdown: inline ordered split for run-on `1. foo 2. bar` lines; ATX heading lines and fenced code are skipped; bare `###` markers on their own line are folded into the next content line. Section-label promotion and list renumbering are **not** done in pre-parse.
- **Render-time remark plugins** (`conversation-remark-plugins.ts`, passed to Streamdown `remarkPlugins`) operate on the mdast tree Streamdown actually renders — no remark-stringify/reparse gap. The bundle includes `remark-gfm` because Streamdown replaces (not merges) default plugins when `remarkPlugins` is set. For completed conversation markdown, `parseMarkdownIntoBlocksFn={(md) => [md]}` parses the full message in one pass so section/list surgery is not split across Streamdown blocks. Plugin order: `remark-gfm` → `remarkSplitSectionLists` → `remarkNestAdjacentBulletLists` → `remarkCoalesceOrderedLists` → `remarkAssignOrderedListStarts`.
- Agent `message_text` is stored and returned by the backend as produced; markdown repair is client-side only. Hybro-controlled LLM synthesis prompts (`multi-agents-backend/common/prompts/markdown_response_format.py`) encourage `###` section headers for cleaner source text; the frontend AST pipeline is the universal compatibility layer for third-party agents.
- The renderer maps top-level `<ol>` elements to `style.counterReset = 'conv-section-ol <start - 1>'` from the mdast `start` prop. CSS counters in `conversation-tokens.css` provide visible `N.` markers.

Display helpers in `src/lib/streaming/display.ts` split live **text** (buffer) from **non-text artifacts** (files/data) during stream so the detail pane and activity strip can show file attachments while text is still growing. `AgentResponseDetailPane` uses `useDetailPaneScroll` with ChatGPT-aligned behavior: first open scrolls to top; reopening the same message restores saved scroll from `room-ui-store.detailScrollByMessageId`; optional tail-follow when pinned near bottom during stream; no scroll reset on stream complete; detail body uses `overflow-anchor: none`.

**Main feed scroll (`ConversationMessageList`):** On first open (no saved position), the list scrolls to the bottom. When revisiting a room, the last scroll position is restored from `room-ui-store.conversationScrollByRoom` (including an `atBottom` flag so rooms left pinned to the latest message still land at the bottom). Scroll snapshots persist across `resetRoom` and are cleared on `resetAll`. On send (`localSendSeq`), `useTurnFocusScroll` anchors the last user message near the top (~50px offset) and grows a dynamic bottom spacer (`data-scroll-spacer`) so the answer expands downward without forced tail-chase. If the user sends before initial DB hydration finishes, hydration scroll restore is skipped and focus-scroll is re-applied so the default scroll-to-bottom does not land in the empty spacer below the turn. While the room is processing, `usePrimaryStreamScroll` follows the growing answer tail when focus mode is active unless the user scrolled away. `.conversation-frame` uses `overflow-anchor: none` to prevent browser scroll anchoring from fighting the spacer. Users who scroll away see the existing scroll-to-bottom affordance.

Known issues and the convergence plan are in [`docs/STREAMING_UI_ISSUES_AND_FIXES.md`](STREAMING_UI_ISSUES_AND_FIXES.md).

## 10. SSE And Room Sync

SSE handling is split into small handlers under `src/hooks/room/sse-handlers/`.

```text
src/hooks/room/sse-handlers/
|-- dispatch.ts
|-- correlation.ts
|-- pending-turn-buffer.ts
|-- apply-commands.ts
|-- artifacts.ts
|-- types.ts
`-- handlers/
    |-- agent-response.ts
    |-- processing-status.ts
    |-- task-submitted.ts
    |-- task-update.ts
    |-- artifact-update.ts
    |-- hitl.ts
    `-- misc.ts
```

`src/lib/types/sse.ts` defines the final room SSE frame envelope as `{ type, room_id, timestamp, data }`. The handled room frame types are:

- Connection/system: `connected`, `heartbeat`, `error`, `run_event`, `cancellation`.
- Turn and task updates: `processing_status`, `task_submitted`, `task_update`, `artifact_update`.
- Agent output: `agent_response_partial`, `agent_response`.
- HITL and orchestration: `hitl_request`, `hitl_response`, `hub_agent_event`, `debate_round`.

Legacy `user_message`, `turn_event`, `hitl_input_requested`, and `hitl_status_update` frames are not part of the handled room SSE contract. Unknown frame types are ignored after a debug log.

`createSSEDispatcher` resolves correlation before dispatch. Turn-correlated events must include a non-empty `client_request_id`; events without it are dropped defensively. Events that can arrive before the HTTP send response resolves the optimistic user message are buffered by `client_request_id`, then flushed once `useSendMessage` maps the request id to the server message id.

`processing_status` requires `message_id`, non-empty `client_request_id`, a known status, and `details` as either an object or `null`. Active statuses such as `queued`, `processing`, and `awaiting_input` keep the user turn active; terminal statuses mark the correlated user turn and clear the send guard only when they target the user message rather than a per-agent task. HITL resume can introduce a new backend `client_request_id`; in that case, a terminal frame with an agent-task `message_id` is accepted only when `related_message_id` points at the resolved user turn and the new request id differs from the user message's original request id.

**Live streaming (target):** `artifact_update` is the primary path into `streaming-store.append(message_id, …)`. `agent_response_partial` (rare in production; delivery-layer alias) should shim into the same message-keyed append — not a separate turn-level buffer. **Checkpoints:** terminal `task_update` and final `agent_response` write to `message-store`, read the message-scoped buffer for fallback text, then clear that message's stream buffer (turn-level clear only on turn complete).

Room DB synchronization lives under `src/lib/room-sync/`:

- `hydrate-room.ts`: initial hydration, reconcile, and HITL overlay orchestration.
- `apply-db-messages.ts`: applies fetched messages to the normalized store.
- `hitl-overlay.ts`: overlays pending HITL requests.
- `types.ts`: hydration result and option types.

`useRoomSSEConnection` handles reconnect behavior:

- Mirrors SSE connection state into `room-ui-store`.
- Rehydrates pending HITL requests on reconnect.
- Reconciles with DB after reconnect gaps.
- Uses a 15-second safety net to clear stuck processing state when the backend reports no active run.

## 11. Timeline And View Models

Timeline construction lives under `src/lib/room-timeline/`.

```text
src/lib/room-timeline/
|-- build-turns.ts
|-- derive-final-answer.ts
|-- event-log.ts
|-- map-result-display.ts
|-- message-groups.ts
|-- turn-agent-terminal.ts
|-- turn-live-shell.ts
`-- types.ts
```

`buildTurns` groups normalized messages into turn view models. User messages define turn boundaries. Agent messages route by `relatedMessageId`, `clientRequestId`, and fallback order. System messages before the first user message are placed in a synthetic system turn.

Selectors under `src/lib/selectors/` adapt store state into UI-specific slices:

- `select-hitl.ts`
- `select-composer-state.ts`
- `select-agent-response-detail.ts`
- `map-agent-display.ts`
- `route-agent.ts`
- `conversation-types.ts`

## 12. API Layer

`src/lib/api-client.ts` is the shared fetch wrapper. It:

- Injects Clerk auth headers through `getClientAuthHeaders`.
- Supports abort signals and a default timeout.
- Wraps HTTP failures in `ApiError`.
- Logs client errors as warnings and server/unexpected errors as errors.

API modules live in `src/lib/api/`:

```text
src/lib/api/
|-- index.ts
|-- agent.ts
|-- agent-group.ts
|-- room.ts
|-- sse.ts
|-- task.ts
|-- inspection.ts
|-- health.ts
|-- a2a-tasks.ts
|-- discovery-api-keys.ts
|-- files.ts
|-- hitl.ts
`-- hub.ts
```

Type definitions live in `src/lib/types/`:

```text
agent.ts, agent-group.ts, attachments.ts, chat-mode.ts, error.ts,
health.ts, memory.ts, quote.ts, request.ts, response.ts, room.ts, sse.ts
```

Other library modules:

- `auth.ts`: Clerk token bridge.
- `urls.ts`: cross-subdomain URL helpers.
- `utils.ts`: `cn`, `getApiUrl`, and formatting helpers.
- `consumer-nav.ts`, `developer-nav.ts`, `nav-items.ts`: navigation configuration.
- `system-agents.ts`: system/supervisor agent classification.
- `agent-avatar.ts`, `agent-icon-utils.ts`, `file-icon-utils.ts`: display helpers.
- `presigned-url.ts`: attachment URL helpers.
- `selection-plain-text.ts`: quote/selection text extraction.
- `streaming/display.ts`: streaming display helpers.

### Send Message Routing

`src/lib/api/room.ts` sends room messages with a required `client_request_id` and one canonical dispatch shape:

- Mention dispatch: `mentioned_agent_ids` as a non-empty string tuple.
- Room default or all agents: `message_target_mode` as `room_default` or `all_agents`.
- Saved group: `message_target_mode: 'saved_group'` plus `target_group_id`.

The frontend no longer emits the legacy `target_group` field. `src/lib/types/agent-group.ts` validates that mention routing and target-mode routing are mutually exclusive before the request is sent.

## 13. Portals

### Consumer portal

Consumer portal routes are under `src/app/c/`.

Primary pages:

- `/c`: landing/entry behavior.
- `/c/chat`: new room creation and use-case cards.
- `/c/room/[id]`: real-time room workspace.
- `/c/agents` and `/c/agents/[id]`: agent marketplace/profile.
- `/c/hub`: hub status.
- `/c/about`, `/c/pricing`: public pages.

Primary shell:

- `src/components/consumer/consumer-sidebar.tsx`
- `src/components/consumer/consumer-header.tsx`
- `src/components/consumer/consumer-footer.tsx`

### Developer portal

Developer portal routes are under `src/app/d/`.

Primary pages:

- `/d`: dashboard.
- `/d/register`: agent registration.
- `/d/agents` and `/d/agents/[id]`: agent management.
- `/d/inspector`: A2A inspector.
- `/d/discovery-api-keys`: discovery API keys.
- `/d/docs`: developer docs content.
- `/d/hub`: hub status.

Primary shell:

- `src/components/developer/developer-sidebar.tsx`
- `src/components/developer/developer-header.tsx`
- `src/components/developer/agent-settings-card.tsx`
- `src/components/developer/agent-avatar-upload.tsx`

## 14. Testing Layout

```text
tests/
|-- setup/
|   |-- vitest.setup.ts
|   |-- msw-server.ts
|   |-- msw-handlers.ts
|   `-- mock-fetch-sse.ts
|-- unit/
|   |-- components/
|   |-- hooks/
|   |-- lib/
|   `-- stores/
|-- e2e/
|   |-- global-setup.ts
|   |-- auth.spec.ts
|   |-- authenticated-flows.spec.ts
|   |-- chat.spec.ts
|   |-- error-handling.spec.ts
|   |-- room.spec.ts
|   |-- room-timeline.spec.ts
|   `-- fixtures/auth.ts
|-- fixtures/index.ts
`-- utils/test-utils.tsx
```

Unit coverage is broad across components, hooks, API clients, room timeline logic, selectors, and stores. Playwright coverage is organized around auth, chat, room, room timeline, authenticated flows, and error handling.

## 15. Current Directory Inventory

```text
src/
|-- app/                 # Next.js App Router routes and layouts
|-- components/          # UI, portal shells, room workspace, conversation renderer
|-- hooks/               # public hooks and room orchestration
|-- lib/                 # API clients, type definitions, selectors, room sync, timeline logic
|-- stores/              # Zustand message, streaming, and room UI stores
`-- proxy.ts             # Clerk + subdomain routing proxy
```

Important generated/local-only files:

- `tsconfig.tsbuildinfo` is TypeScript incremental build cache and is ignored by `*.tsbuildinfo`.
- `.next/`, coverage output, and test artifacts are not architecture sources.

## 16. Contributor Notes

- Keep route-level code under `src/app/`; shared UI belongs under `src/components/`.
- Prefer the existing shadcn/ui primitives in `src/components/ui/`.
- Use `src/lib/api-client.ts` for backend requests instead of raw fetch wrappers.
- Keep permanent room message data in `message-store`; keep transient stream display data in `streaming-store`.
- Add room realtime behavior through `src/hooks/room/sse-handlers/` and preserve correlation buffering rules.
- Add turn/timeline display logic under `src/lib/room-timeline/` or `src/lib/selectors/` instead of inside rendering components.
- Do not document files from deleted or historical docs as current source structure.
