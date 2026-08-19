# Hybro Frontend Architecture

> Last scanned: 2026-06-04
>
> Source of truth: current repository files under `src/`, `tests/`, and root config files. Historical design notes in `docs/` are not treated as current architecture.

## 1. Overview

Hybro Frontend is a Next.js App Router application for the Hybro multi-agent platform. A single unified portal provides chat, agent inventory and registration, room timelines, HITL replies, file attachments, pricing, and public information pages.

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
| Auth | Local self-hosted identity adapter (`src/lib/auth.tsx`) |
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
| `.nvmrc` | recommended Node version (`20.19`) |
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
`-- (portal)/
    |-- layout.tsx
    |-- page.tsx
    |-- about/page.tsx
    |-- core/page.tsx
    |-- agents/page.tsx
    |-- agents/[id]/page.tsx
    |-- agents/new/page.tsx
    |-- chat/page.tsx
    |-- pricing/page.tsx
    |-- room/[id]/page.tsx
    `-- manage/
        |-- page.tsx
        |-- agents/page.tsx
        |-- agents/[id]/page.tsx
        `-- agents/new/page.tsx
```

### Unified routing

The application exposes one unprefixed route tree. Agent inventory, details,
and registration live at `/agents`, `/agents/[id]`, and `/agents/new`; chat
routes live at `/chat` and `/room/[id]`. There is no host-based route rewrite.
Legacy `/manage` and `/manage/agents*` paths redirect to their canonical
`/agents*` equivalents.

The `/agents` inventory merges visible registered agents with locally available
agents. Its **Discover Local Agents** action calls the authenticated
`POST /api/v1/local-agents/discovery` endpoint, waits for the backend discovery
cycle, and then invalidates both agent inventory queries. Directly discovered
`source=local` agents are displayed while active and use the same Local source
badge as Hub agents; Hub availability additionally depends on Hub liveness.

### Provider hierarchy

`src/app/layout.tsx` wraps the app with:

1. `ThemeProvider`
2. `QueryProvider`
3. `Toaster`
4. `CookieBanner`

The portal layout adds `BannerHost`, `SidebarProvider`,
`SettingsDialogProvider`, `PortalSidebar`, and `PortalHeader`.

## 6. Component Organization

```text
src/components/
|-- ui/                       # shadcn/ui primitives
|-- conversation/             # turn/timeline rendering system
|-- composer/                 # chat composer shell and HITL response bar
|-- portal/                   # unified sidebar/header/footer, Core page, Manage navigation
|-- open-source/              # Core page terminal animation
|-- providers/                # React Query provider
|-- settings/                 # settings dialog sections and helpers
|-- room-page-shell.tsx       # room workspace shell
|-- room-chat-input.tsx       # composer input, mentions, uploads
|-- group-selector.tsx
|-- group-management-modal.tsx
|-- consumer-agent-card.tsx
|-- artifact-list.tsx
|-- artifact-renderer.tsx
|-- attachment-preview.tsx
|-- markdown-content.tsx
|-- part-renderer.tsx
|-- mode-selector.tsx
|-- nav-agent.tsx
|-- nav-user.tsx
|-- nav-docs-button.tsx
|-- nav-discord-button.tsx
|-- require-auth.tsx
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
- `AgentCard.tsx`, `AgentContentBlock.tsx`, `AgentResultContent.tsx`, `AgentIndex.tsx`: agent response presentation. Multi-agent turns with LLM synthesis (`llm_synthesis`) show the combined answer in the primary surface and compact per-agent index rows below. Supervisor DONE without synthesis (`deterministic_done`) shows the digest intro in the primary surface and full per-agent bodies in the expanded `AgentIndex`. Substantive `summary-*` content classifies as `llm_synthesis`; the short coordinator digest stub (`"N agents responded. Expand below…"`) classifies as `deterministic`.
- `FinalAnswerSurface.tsx`, `SynthesisContent.tsx`: final/synthesis answer surfaces.
- `AgentResponseDetailPane.tsx`: right-side detail pane for a selected agent response.
- `ScrollToBottomButton.tsx`, `scroll-state.ts`: scroll affordances and state.
- `conversation-tokens.css`, `shimmer.css`: conversation-specific CSS tokens and loading effects. Reading typography uses 16px / 1.75 line-height, system UI sans, 400 weight (light and dark), neutral letter-spacing, 1em paragraph gaps, 2.75rem turn spacing, an 800px content column, and 14px table cell text.

`src/components/room-page-shell.tsx` owns the room workspace. It renders the conversation list, the composer dock, desktop resizable detail panes, and mobile detail sheets. It also wires selected message state from `room-ui-store`, streaming buffers from `useStreamBuffer`, and detail view models from `selectAgentResponseDetail`.

## 7. Hooks And Room Orchestration

Top-level hooks live in `src/hooks/`; room-specific orchestration lives in `src/hooks/room/`.

```text
src/hooks/
|-- useRoomWebhook.ts          # public re-export/entry hook
|-- useRoomSSE.ts              # low-level SSE connection hook
|-- useTurnViewModels.ts       # turn view model builder
|-- useChatRoomCreation.ts     # room creation and navigation
|-- useGroupManagement.ts      # saved groups and room group selection
|-- useScrollUserMessageOnSend.ts # one-time scroll into sticky zone on send
|-- usePrimaryStreamScroll.ts
|-- useStreamBuffer.ts
|-- useTextSelectionQuote.ts
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

`src/app/(portal)/room/[id]/page.tsx` is the room page. It is a client component that:

- Reads `roomId` from the route.
- Reads user/auth state from the local identity adapter.
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

### Agent Dispatch Privacy

Frontend message state treats `taskContent` as public display metadata only.
Internal dispatch prompts are not accepted from API/SSE payloads and must not be
rendered in timeline stage details or Agent response detail panes. Streaming
correlation continues to rely on `client_request_id`; privacy filtering must
not drop that correlation field.

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

- **Pre-parse** (`preprocessConversationMarkdown` in `normalize-conversation.ts`, `normalize-agent-list-markers.ts`, and `split-inline-ordered.ts`) runs before Streamdown: rewrites common agent list-marker mistakes (`1. • …` and bare `•` lines → `-` bullets; skips fenced code); inline ordered split for run-on `1. foo 2. bar` lines on list-item lines only (skips prose like `See step 1. For details`); supervisor-shaped lines (`3. **#3 — …`) only split before the next `N. **#N` marker so prose like `adoption in 4. The era` stays one item; inline splits are deferred while `isStreaming` is true; ATX heading lines and fenced code are skipped; bare `###` markers on their own line are folded into the next content line. Section-label promotion and list renumbering are **not** done in pre-parse.
- **Render-time remark plugins** (`conversation-remark-plugins.ts`, passed to Streamdown `remarkPlugins`) operate on the mdast tree Streamdown actually renders — no remark-stringify/reparse gap. The bundle includes `remark-gfm` because Streamdown replaces (not merges) default plugins when `remarkPlugins` is set. For conversation markdown, `parseMarkdownIntoBlocksFn={(md) => [md]}` parses the full message in one pass (including during streaming) so section/list surgery is not split across Streamdown blocks. Plugin order: `remark-gfm` → `remarkSplitSectionLists` → `remarkNestAdjacentBulletLists` → `remarkCoalesceOrderedLists` → `remarkAssignOrderedListStarts`.
- **Rehype pipeline** (`markdown-content.tsx`): custom `rehypePlugins` must include Streamdown's `defaultRehypePlugins` (`rehype-raw`, `rehype-sanitize`, `rehype-harden`) before `rehype-highlight`, because passing `rehypePlugins` replaces rather than merges defaults.
- Agent `message_text` is stored and returned by the backend as produced; markdown repair is client-side only. Hybro-controlled LLM synthesis prompts (`backend/common/prompts/markdown_response_format.py`) encourage `###` section headers for cleaner source text; the frontend AST pipeline is the universal compatibility layer for third-party agents.
- Completed agent tasks treat backend-projected `message_text` as the human-readable response and render non-text Task artifacts alongside it. Raw `TaskStatus.message` and Task history are never promoted by the client; when public `message_text` is absent, a completed text-only artifact remains the compatibility fallback.
- Agent entities retain the backend-published `extend_info.public_dispatch_text` separately from the short `public_task_label`. The main Agent Card keeps the compact label; the expanded response detail prefers the full dispatch text in its existing collapsible task region, followed by the agent `message_text` and artifacts.
- The renderer maps top-level `<ol>` elements to `style.counterReset = 'conv-section-ol <start - 1>'` from the mdast `start` prop. CSS counters in `conversation-tokens.css` provide visible `N.` markers for ordinary lists; items that already start with `#N` (supervisor-style `**#1 — …` rows) get `conv-hash-numbered-item` and suppress the extra counter.

Display helpers in `src/lib/streaming/display.ts` split live **text** (buffer) from **non-text artifacts** (files/data) during stream so the detail pane and activity strip can show file attachments while text is still growing. `AgentResponseDetailPane` uses `useDetailPaneScroll` with ChatGPT-aligned behavior: first open scrolls to top; reopening the same message restores saved scroll from `room-ui-store.detailScrollByMessageId`; optional tail-follow when pinned near bottom during stream; no scroll reset on stream complete; detail body uses `overflow-anchor: none`.

**Main feed scroll (`ConversationMessageList`):** The logical bottom of the feed is the `[data-content-end]` sentinel after the last turn — not the full `scrollHeight`, which includes the fixed `[data-scroll-spacer]` below it. `content-end-scroll.ts` centralizes `scrollToContentEnd`, `isNearContentEnd`, and snapshot `atBottom` detection. On first open (no saved position), the list scrolls to content-end. When revisiting a room, the last scroll position is restored from `room-ui-store.conversationScrollByRoom` (including an `atBottom` flag so rooms left pinned to the latest message still land at content-end). Scroll snapshots persist across `resetRoom` and are cleared on `resetAll`. Every user message bubble uses native CSS sticky (`.conversation-user-sticky { position: sticky; top: 0 }`) for both live and completed turns. On send (`localSendSeq`), `useScrollUserMessageOnSend` scrolls the sticky wrapper into the top of the scrollport once so sticky engages; CSS sticky then holds the question visible while HYBRO/agent content grows below. While the room is processing (`turnLive`), `tailFollowRef` (detail-pane-style sticky follow flag) stays true once the user reaches content-end and only clears on explicit user scroll (wheel/touchmove with movement, or `scrollTop` decrease while not programmatic), not when content growth temporarily moves the viewport away from the threshold. The 150ms programmatic-scroll suppress window only skips scroll-position re-enable inference and snapshot writes — it does not block user cancel. `usePrimaryStreamScroll` and layout-driven follow scroll whenever `tailFollowRef` is set. `.conversation-scroll-area` and `.conversation-frame` use `overflow-anchor: none`. Users who scroll away see the scroll-to-bottom button, which re-enables tail follow.

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

`hitl_request` and `hitl_response` are durable HITL lifecycle events rather than
strict turn-correlated streaming events. They may include `client_request_id`
when the backend can resolve it, but the UI must still apply them by `room_id`,
`request_id`, and `message_id` when it is absent or stale. Live HITL SSE and
`GET /api/v1/rooms/{room_id}/hitl/pending` hydration share the same message
projection path so a pending HITL appears whether the user stays on the page,
reconnects, or refreshes.
The projection preserves HITL `source` as first-class message state. Agent
requests therefore render the external agent name (for example,
`Cyber Broker Agent · Needs Input`), while supervisor requests render HYBRO AI.
Raw agent task states such as `input-required`, `auth-required`, and
`policy-required` are not actionable UI state by themselves: until the message
has a durable `hitlRequestId`, the timeline and agent header continue to show
Working while backend recovery runs silently. Only the durable HITL projection
may show Needs Input or an interaction component.
Hydration marks input-required messages that are absent from the pending set as
resolved so canceled or expired request metadata cannot recreate stale HITL UI.

`processing_status` requires `message_id`, non-empty `client_request_id`, a known status, and `details` as either an object or `null`. Active statuses such as `queued`, `processing`, and `awaiting_input` keep the user turn active; terminal statuses mark the correlated user turn and clear the send guard only when they target the user message rather than a per-agent task. HITL resume can introduce a new backend `client_request_id`; in that case, a terminal frame with an agent-task `message_id` is accepted only when `related_message_id` points at the resolved user turn and the new request id differs from the user message's original request id.

Failed and canceled user turns are absorbing lifecycle states: a delayed active
`processing_status` frame cannot restart their composer processing/Stop state.
When a live tab misses terminal SSE and the room snapshot reports no active run,
`useProcessingRestore` rechecks `inquiryActiveRuns` for the exact trigger,
reconciles messages from the database, and stops processing only after the
reloaded user message carries a terminal status. Before mutating the lifecycle
after those asynchronous checks, it verifies that the same user message still
owns processing and that no new send or pending run-event acknowledgement has
started. This preserves the send-race guard while allowing backend-confirmed
failures with zero agent tasks to recover without a page refresh.

**Multi-agent turn completion fallback:** Per-agent terminal SSE alone does not complete a multi-agent turn. The backend emits a `turn_completion_kind` field (`"synthesis"` or `"deterministic"`) as part of the COMPLETED `processing_status` SSE `details` and persists it on the user message `extend_info` before emitting the event. The frontend stores this as `turnCompletionKind` on the user `MessageEntity`.

`deriveFinalAnswer` promotes to `deterministic_done` when `turnCompletionKind === 'deterministic'`, a deterministic `summary-*` digest entity is present, mixed terminal agents resolve without synthesis, or `turnCompletionKind === 'synthesis'` was persisted but no synthesis evidence ever appeared (backend queue path can set synthesis kind even when no LLM step runs). When synthesis is actively in flight — processing logs, synthesis ephemerals, or a working empty LLM summary — the turn stays `pending`/`synthesizing` until content arrives. When `turnCompletionKind` is absent, supervisor turns stay pending until backend truth stamps or synthesis signals arrive; non-supervisor and mixed-failure paths use entity evidence and `isDeterministicCompletionExpected`.

`turnCompletionKind` is delivered via three redundant paths: (1) SSE `processing_status` COMPLETED `details`, (2) DB `extend_info.turn_completion_kind` on the user message (read during hydration/reconcile), (3) `inquiryActiveRuns` response (queried during truth-check when `trigger_message_id` is passed and no active run matches). This ensures correctness across SSE drops, page refreshes, and reconnects.

`hasActiveSynthesisGap` treats positive synthesis evidence (`turnPhase: 'synthesizing'` on processing logs, log lines containing "synthesiz" or "compiling summary", synthesis ephemerals, or a working non-deterministic summary agent) as synthesis in progress and drives `phase: synthesizing`. Stale synthesis logs are ignored once all real agents are terminal and at least one failed without an in-flight LLM summary entity — partial-failure turns promote to `deterministic_done` instead of staying on **Working**. Supervisor turns without `turnCompletionKind` stay pending in `deriveFinalAnswer` until synthesis resolves or backend truth stamps — preventing a flash of expanded `deterministic_done` bodies before synthesizing starts. Delegation logs, supervisor mode, and generic work logs are **not** synthesis evidence.

**Entity-first invariant:** When a non-deterministic `summary-*` entity has substantive LLM content, `deriveFinalAnswer` returns `llm_synthesis` even if `turnCompletionKind` was incorrectly stamped or inferred as `deterministic`. `turnHasSubstantiveLlmSynthesis` blocks backend-truth stamping and debounced recovery from overwriting synthesis turns.

Backend-truth stamping (`turn-terminal-stamp.ts`) uses `isBackendRunConfirmedNonSynthesisCompletion` when `inquiryActiveRuns` reports no active run: broader than `isDeterministicCompletionExpected` so supervisor no-synthesis turns can stamp even without a pre-set kind; it infers `turnCompletionKind: 'deterministic'` on stamp when inquiry did not return a kind. `isDeterministicCompletionExpected` still gates live `deriveFinalAnswer` promotion to avoid premature expand. Backend-truth passes `turnCompletionKind` atomically alongside `turnTerminalStatus` and queries `inquiryActiveRuns` with `trigger_message_id` when the SSE path was missed.

**Debounced recovery (`shouldScheduleTurnTerminalRecovery`):** schedules the 1.5s backend-truth check on terminal `FAILED`/`REJECTED`/`CANCELED`, or `COMPLETED` when all real agents are terminal. Summary-agent `agent_response` frames (`summary-*` / coordinator summary agent id) never schedule recovery. When `processing_status` COMPLETED arrives with `turn_completion_kind: 'synthesis'` after a prior deterministic stamp, the handler monotonically upgrades `turnCompletionKind` on the user entity.

Backend queue/resume/supervisor completion paths set `turn_completion_kind` from `_emit_unified_summary` return value (`synthesis` when LLM/supervisor synthesis is used — including when a duplicate `summary-*` row is skipped for fewer than two trajectory responses — and `deterministic` when the digest path runs). The kind is persisted on the user message after durable terminal `processing_status` COMPLETED wins (so a cancel CAS winner does not leave a stale kind). Terminal `processing_status` details include `turn_phase: 'terminal'`; synthesis stage emits `turn_phase: 'synthesizing'`. The frontend stores `turnPhase` on `ProcessingStatusLogEntry` when appending logs from SSE `details`.

**Hydrate repair for stuck `system:hybro`:** Older runs could leave `system:hybro` with answer text while task state stayed `submitted`. When `turnTerminalStatus === 'completed'` and the summary agent has non-empty content, `buildAgentResult` treats it as completed so refresh does not spin on Synthesizing. Live turns (no terminal stamp yet) keep contentful `submitted`/`working` as working so mid-stream synthesis still shows Synthesizing. `deriveFinalAnswer` returns `llm_synthesis` / Synthesized when the orchestrator has answer text and either its status or `turnTerminalStatus` is completed.

**Live streaming (target):** `artifact_update` is the primary path into `streaming-store.append(message_id, …)`. `agent_response_partial` (rare in production; delivery-layer alias) should shim into the same message-keyed append — not a separate turn-level buffer. **Checkpoints:** terminal `task_update` and final `agent_response` write to `message-store`, read the message-scoped buffer for fallback text, then clear that message's stream buffer (turn-level clear only on turn complete).

SSE artifact conversion is defensive at the client boundary. `task_update`
`parts` and `artifact_update` payloads drop legacy inline `file.bytes`; file
parts are renderable when they carry a durable `file_id` or a URI. Canonical
artifact events and synthetic terminal `${messageId}-parts` projections are
reconciled by stable part identity (`file_id`, then SHA-256, URI, or canonical
data), so live detail matches post-refresh hydration without collapsing distinct
same-name files. `file_unavailable` data renders as a safe unavailable-output
notice and is not counted as a file. `PartRenderer` does not create `data:` URLs
from inline bytes, so stale or malicious legacy SSE cannot surface private file
content in message state or rendered media.

Room DB synchronization lives under `src/lib/room-sync/`:

- `hydrate-room.ts`: initial hydration, reconcile, and HITL overlay orchestration.
- `apply-db-messages.ts`: applies fetched messages to the normalized store.
- `hitl-overlay.ts`: overlays pending HITL requests.
- `types.ts`: hydration result and option types.

`useRoomSSEConnection` handles reconnect behavior:

- Mirrors SSE connection state into `room-ui-store`.
- Rehydrates pending HITL requests on reconnect.
- Reconciles with DB after reconnect gaps.
- While a turn is processing, polls backend run truth every five seconds until a
  terminal state is observed. A transient poll/reconcile failure does not stop
  later checks, so a missed terminal SSE cannot leave the turn spinning until refresh.

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

- Injects local identity auth headers through `getClientAuthHeaders`.
- Supports abort signals and a default timeout.
- Wraps HTTP failures in `ApiError`.
- Logs client errors as warnings and server/unexpected errors as errors.

API modules live in `src/lib/api/`. Consumers import the specific module they
need rather than a shared barrel, keeping client bundles scoped to the active
feature:

```text
src/lib/api/
|-- agent.ts
|-- agent-group.ts
|-- room.ts
|-- sse.ts
|-- inspection.ts
|-- a2a-tasks.ts
|-- files.ts
`-- hitl.ts
```

Type definitions live in `src/lib/types/`:

```text
agent.ts, agent-group.ts, attachments.ts, chat-mode.ts, error.ts, index.ts,
quote.ts, request.ts, response.ts, sse.ts
```

Other library modules:

- `auth.ts`: local self-hosted identity and authentication-header adapter.
- `routes.ts`: canonical public and management route vocabulary.
- `utils.ts`: `cn`, `getApiUrl`, and formatting helpers.
- `consumer-nav.ts`, `nav-items.ts`: top-level navigation configuration.
- `system-agents.ts`: system/supervisor agent classification.
- `agent-avatar.ts`, `agent-icon-utils.ts`, `file-icon-utils.ts`: display helpers.
- `api/files.ts` and `hooks/useRoomFile.ts`: authenticated room-file upload,
  download, and preview blob lifecycle. The authenticated same-origin download
  path normalizes `NEXT_PUBLIC_API_PREFIX` to the same leading/trailing-slash
  form used by the Next rewrite.
- `selection-plain-text.ts`: quote/selection text extraction.
- `streaming/display.ts`: streaming display helpers.

### Send Message Routing

`src/lib/api/room.ts` sends every room message with a required
`client_request_id`, request-scoped `mode: 'direct' | 'supervisor'`, and one
canonical `agent_scope` discriminated union:

- Mention scope: `{ source: 'mention', agent_ids: [...] }` with a non-empty ID tuple.
- Room default: `{ source: 'room_default' }`.
- All visible active Agents: `{ source: 'all_agents' }`.
- Saved group: `{ source: 'saved_group', group_id }`; the Backend expands and
  authorizes membership, so the Frontend never sends group member IDs.

The Frontend does not emit legacy `mentioned_agent_ids`, `message_target_mode`,
`target_group_id`, `target_group`, or candidate-scope fields. Fast maps to
`direct`, Ultimate maps to `supervisor`, and room settings only provide the UI
default.

## 13. Unified Portal

The `(portal)` route group provides one shared shell without adding a URL
segment.

- `/`: redirects to `/core`. Hybro Core does not require sign-in.
- `/core`: Hybro Core product page. The hero composer is the same `RoomChatInput`
  as `/chat`, with group and mode menus visible but non-selectable and mention
  and attach buttons visible but non-clickable. While idle it typewrites the
  featured use cases from `src/lib/use-case-templates.ts` (Travel Planner,
  Story & Image Creator). The header logo links to `/core`. The logo wall lists Hermes, OpenClaw, Pi, Ollama,
  n8n, CrewAI, LangChain, and LangGraph. Send-on-demo creates a room named after the current
  use case, seeds those Agents, and prefills the prompt without auto-sending or a sign-in redirect.
- `/chat` and `/room/[id]`: chat creation and real-time room workspace.
- `/agents`: unified local inventory of registered Remote agents and currently
  discoverable Local agents.
- `/agents/[id]`: unified AgentCard detail with Share, Chat, and Remote-only
  Unregister actions.
- `/agents/new`: Remote agent registration.
- `/about`, `/pricing`: public pages.

Remote agents use the persisted backend `agent_status` without frontend health
probing. Local agents are shown only while `source === "hub"`, status is active,
and the hub is online; stale Local agents are hidden. Agent-detail chat actions
write a one-shot mention draft to `room-ui-store` and navigate to `/chat`; the
composer consumes the draft, renders the Agent mention, and focuses the input
without URL query parameters or creating a saved Team.

Featured use-case cards on `/chat` stay on the page. A card resolves its declared
Agents against the live catalog, finds the authenticated user's saved preset Team
by a stable use-case marker, and creates that Team through `/agentGroups` only
when it is absent. Creation includes an owner-scoped `preset_key`, so the Backend
also guarantees idempotency across concurrent tabs. Existing preset membership
is reconciled to the template's current Agent IDs before selection. The card then
selects the saved Team in the group selector and prefills the composer; room creation and
navigation do not occur until the user sends the message. Failed creates perform
one catalog refresh as a compatibility fallback.

The shared shell is implemented by `src/components/portal/` and exposes only New
Chat and Agents as primary navigation before chat history. Chat history uses the
lightweight authenticated `GET /roomCenter/history` resource through TanStack Query.
Pinned rooms render above Recent rooms; desktop drag handles persist pinned order
through the reorder mutation while Recent is derived from descending
`last_activity_at`. The section header can collapse or expand the history list.
Rename, pin/unpin, reorder, and delete mutations update the query cache
optimistically and roll back on failure. Active room states (`queued`,
`processing`, and `awaiting_input`) are returned in the list payload, so rooms
without active work remain unbadged and the sidebar does not issue per-room
requests. The query refreshes on focus and
polls every ten seconds only while an active state is present. Room creation
invalidates the authenticated user-scoped query under the shared
`ROOM_HISTORY_QUERY_KEY` prefix; the former global `rooms:refresh` browser event
is no longer used. Legacy `/manage/agents*` routes
are redirect-only compatibility paths. `src/lib/routes.ts` is the canonical
route vocabulary for application links.

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
`-- stores/              # Zustand message, streaming, and room UI stores
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

## Request-scoped execution mode and Agent scope

Every message send carries two immutable fields alongside `client_request_id`:

```ts
type ExecutionMode = 'direct' | 'supervisor'
type AgentScopeInput =
  | { source: 'mention'; agent_ids: [string, ...string[]] }
  | { source: 'room_default' }
  | { source: 'all_agents' }
  | { source: 'saved_group'; group_id: string }
```

Fast maps to `direct`; Ultimate maps to `supervisor`. The room's historical
`use_supervisor` value only selects the initial UI mode. Changing the selector does
not update Room settings and cannot race the subsequent send. `SendMessage` emits
only `mode` and `agent_scope`; saved-team member IDs are expanded and authorized by
the backend. Existing `client_request_id` optimistic-message replacement and early
SSE buffering remain unchanged.

Debate is not a `ChatMode`, Room setting, request flag, or handled SSE event. The
ModeSelector retains one disabled `Debate (Coming Soon)` row as display-only UI;
it has no selection handler and can never create a Debate request. Historical room
`debateMode` metadata is ignored when selecting the Fast/Ultimate UI default.
