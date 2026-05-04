# Room Conversation UI/UX Refactor — Design Spec

**Date**: 2026-04-27
**Status**: Draft (post-review v8)
**Scope**: Complete refactor of room conversation rendering. Clean up old UI first, then rebuild with new components on a canonical selector pipeline. Backend unchanged.

---

## 1. Goals

- Trustworthy agent attribution: every response clearly shows which agent produced it
- Easier conversation scanning: visual hierarchy makes long conversations readable
- HITL prompts never missed: clear status on AgentCard, interaction in input panel
- Historical rooms understandable on re-entry: hydrated conversations render correctly
- Simplify data architecture: one selector pipeline, no bridge layer, no redundant projections

## 2. Non-Goals

- Backend API or SSE protocol changes
- Chat input panel redesign (kept as-is)
- Feedback buttons (copy/like/dislike) — deferred
- Agent action buttons on cards — deferred
- Process step rendering (thinking/planning/tool_call) — backend does not provide structured process events; add when contract exists

---

## 3. Implementation Principles

1. **Delete old UI branches first, then build new.**
2. **Old code is rule reference only, not an inheritance base.** No view models, projections, or component structures carry over.
3. **New components are dumb.** They receive props and render. No business logic inside.
4. **All routing, HITL, status, attribution, ordering live in pure selectors/mappers.** Testable without React.
5. **No dormant UI for future backend features.** Extension points are typed interfaces, not dormant components.
6. **No boolean view flags.** No `globalNewConversationView`. Cleanup removes old views; new view becomes the only path.
7. **Clean the model first, then do the visual refactor.**
8. **`clientRequestId` is not the stable turn identity.** It is only a live correlation key for optimistic user messages, processing placeholders, and pre-POST SSE buffering. Stable/historical turns are anchored by persisted user message `id`, and agent routing prefers `relatedMessageId` to that user message.

---

## 4. Phased Rollout

### Phase 0 — Dead code cleanup (separate PR)

Remove confirmed dead code unrelated to renderer migration:
- page.tsx: `editingName` / `editNameValue` / `nameInputRef` state, `startEditingName` / `saveRoomName` / `cancelEditingName` callbacks, unused imports (`Pencil`, `Users`, `Check`, `XIcon`, `TooltipProvider`)
- `appearance-section.tsx`: orphaned file, delete
- `room-messages.tsx`: already-dead `setCollapseSignal`, unused `collapseSignal` state
- `TurnList.tsx`: constant `expandSignal` / `collapseSignal` that never change

### Phase 1 — Cut legacy bubble view and view-switching flags

- Remove `RoomPageShell` view-switching (`if (turnBasedTimeline) ... else ...`)
- Remove `globalTurnBasedTimeline` / `setGlobalTurnBasedTimeline` from `room-ui-store`
- Remove localStorage key `hybro:turnBasedTimeline`
- Delete `room-messages.tsx` (legacy bubble view) and its direct dependencies: `EntityUserBubble`, `EntityAgentBubble`, `groupMessagesByUserTurn`
- **Do NOT delete hooks by name.** After component deletion, run reference check (`grep -r`). Only delete hooks with zero remaining imports. If `useOrderedIds`, `useMessage`, or any other hook is still consumed by the temporary turn-based view, leave it until Phase 2.
- **Delete by symbol, not by file.** A module may export both legacy-only symbols and shared helpers (e.g., `message-groups.ts` exports `groupMessagesByUserTurn` used only by legacy view, but also `escapeCssIdent` used by `useMessageScrollAnchoring`). Run import-level reference check per exported symbol. Only delete the entire file if ALL exports are unreferenced.
- The page temporarily renders only the existing turn-based view (no flag)

### Phase 1.5 — Cut old turn/block UI business entry points

Before building new selectors, sever the old block-level component tree from business logic:

- **Goal**: Ensure `selectConversationTurns` (Phase 2) cannot accidentally share any pipeline with `buildTurnsIncremental` / `TurnViewModel`.
- Cut the import of `TurnList` / `OrchestraTurn` / `conversation-turn` / `agent-result-card` from the page. The page renders an empty placeholder (or a simple `<MessageList>` that just maps `orderedIds` to raw entity content — no turn logic).
- Do NOT delete these files yet — they become dead code, confirmed by reference check.
- Mark `useMessageStoreSync` (the bridge) as dead. It must not be imported by any new code. If it is still running because the temporary placeholder needs entity sync, add a `// DEAD — remove in Phase 5` header and ensure no new selector depends on it.
- **Invariant**: After Phase 1.5, `buildTurnsIncremental` and `TurnViewModel` have zero runtime consumers. New selectors (Phase 2) start from a clean base.

**Merge policy**: Phase 1.5 leaves the page in a degraded state (placeholder or raw entity list). It is NOT a mergeable PR on its own. Phase 1.5 through Phase 4 must be completed on the same feature branch and merged as a single PR (or a reviewable stack where Phase 1.5 is never deployed to production alone). The merge gate is: `ConversationMessageList` is wired in and all scenarios pass (Phase 4 checklist). If the PR must be split for review purposes, Phase 1.5 must not cut the production import of `TurnList` — instead, make the old components dead-code-ready (remove business logic dependencies, mark as deprecated) while keeping them rendered until Phase 4 replaces them.

### Phase 2 — Extract canonical selectors from message-store

Create pure selector functions in `src/lib/selectors/conversation.ts`:

```typescript
// Core routing — returns grouped turns, not flat blocks
selectConversationTurns(roomId, entities, orderedIds): ConversationTurnView[]
// routeAgentToTurn: persisted-first routing.
//   1. relatedMessageId chain -> persisted user message id (stable path)
//   2. clientRequestId -> optimistic user/placeholder in current live room only
//   3. 'unresolved'
// clientRequestId fallback must never determine ConversationTurnView.turnId;
// it only locates the current optimistic user entity, whose id may later be
// replaced by the persisted user message id via replaceMessageId.
routeAgentToTurn(entity, userMessageIds, entityById): string | 'unresolved'

// HITL (single source of truth)
selectPendingHitls(roomId, entities, orderedIds): PendingHitl[]
selectAgentHitlState(entity): HitlState | null

// Composer
selectComposerState(roomId, entities, orderedIds): ComposerState

// Display — pure mapper, not component logic
mapAgentDisplayProps(entity): AgentDisplayProps
selectMessageContent(entity): ContentView
```

**Turn grouping**: `selectConversationTurns` returns `ConversationTurnView[]`, not a flat `ConversationBlock[]`. Each turn is anchored by a user message and contains ordered blocks. This gives `ConversationTurn` its grouping boundary directly from the selector — no re-grouping needed in the component layer.

```typescript
interface ConversationTurnView {
  turnId: string                       // persisted user message id (stable), or cr:${clientRequestId}
                                       // (temporary, only before POST resolves), or '__unresolved__'
  userMessage: MessageEntity | null    // null only for unresolved bucket
  blocks: ConversationBlock[]          // agent cards, content, user answers, dividers
}
```

**Turn identity lifecycle**: When the user sends a message, the optimistic user entity has `id: 'cr:${clientRequestId}'`. This becomes `turnId` temporarily. Once POST resolves and `replaceMessageId` swaps the optimistic id for the persisted user message id, the selector produces `turnId` from the persisted id on its next run. Historical/hydrated rooms never see `cr:` prefixed turn ids — they always use persisted user message ids. `clientRequestId` is never stored as long-term turn identity.

Unresolved agent responses (routing tier 3) are collected into a synthetic turn with `turnId: '__unresolved__'` and `userMessage: null`. The component renders these with an "unattributed response" label.

**Render contract**: `ConversationTurn` branches on `userMessage === null`. When null (unresolved turn), it renders `UnresolvedAgentGroup` — a distinct component that shows an "Unattributed responses" header and lists blocks without `UserMessageBlock`. `UserMessageBlock` is never rendered for unresolved turns.

**Routing rules** (persisted-first, three-tier):
1. `relatedMessageId` → persisted user message `id` (chain traversal, max 2 hops). This is the stable routing path for persisted and hydrated messages.
2. `clientRequestId` match — live correlation only. Used for optimistic user messages, processing placeholders, and early SSE events that arrive before the real message `id` is available. Once the persisted user message `id` is known, routing falls back to tier 1.
3. Unresolved bucket — rendered with "unattributed response" label, never silently dropped, never auto-attached

**Data availability**: `relatedMessageId` is populated from `related_message_id` in both hydration API (`convert-api-message.ts:223`) and SSE events (`sse-handlers/index.ts:417,463,698`). If legacy historical data is missing `relatedMessageId`, those agents fall to tier 2 (`clientRequestId`) or tier 3 (unresolved). This is acceptable — tier 2 is a compat fallback for old data, not a long-term routing path. Do not backfill or migrate old data to fix this.

**Ephemeral entities** (`isEphemeral === true`) are not rendered as content blocks. However, `selectConversationTurns` converts each ephemeral processing placeholder into a **synthetic `agent_card` block** with `display: { label: 'Working', tone: 'accent', isAnimated: true }` and `agentName` from the ephemeral entity's `senderName`. This ensures the "Working" card appears immediately after the user sends a message, before any real agent entity arrives via SSE. Once a real agent entity with matching `clientRequestId` arrives and produces its own `agent_card` block, the synthetic block is superseded (deduplicated by the selector using `clientRequestId` overlap). If no ephemeral placeholder exists (e.g., hydrated room), only real agent entities produce cards.

**Prerequisite**: The current `useSendMessage` creates the processing placeholder (L81-91) without `clientRequestId`. The selector needs `clientRequestId` to: (a) route the placeholder to the optimistic user turn during the live send flow, and (b) deduplicate it when the real agent entity arrives. **Fix**: Add `clientRequestId` to the placeholder's `upsertMany` call in `useSendMessage`. This is the same `clientRequestId` already written to the optimistic user message (L78). No new field — just passing the existing value. Note: this `clientRequestId` is only used for live correlation — the placeholder is ephemeral and never persisted, so it never participates in historical/hydrated turn routing.

**HITL state**: Determined by `hitlRequestId && hitlResolved !== true`, NOT by `taskStatus === 'input-required' && no answer`. **Grouped HITL**: When `hitlGroupId` is present, an entity is part of a multi-question group. The selector returns the entire group (all indices) if any member is unanswered — this enables the existing paginated UI in `HitlResponseBar`. See `selectPendingHitls` for the full grouping algorithm.

### Phase 3 — Build new conversation components

All components are presentational. They receive typed props from selectors, render UI, and emit callbacks. No store subscriptions inside components (except the top-level list container).

```
ConversationMessageList          — scroll container, sticky logic, elastic spacer
  ConversationTurn               — groups blocks for one turn
    UserMessageBlock             — user message with truncation + expand
    AgentCard                    — agent status card with theme colors
    AgentContentBlock            — borderless markdown content
    UserAnswerCard               — HITL Q&A record
  ScrollToBottomButton           — scroll control with new-content badge
```

**Container hook** (`useConversationTurnViews`): Subscribes to message-store version, calls `selectConversationTurns(roomId, entities, orderedIds)`, returns stable `ConversationTurnView[]`. This is the **only** subscription point — it is not a projection store, just a memoized selector call.

**Composer migration**: `ComposerShell` switches from `useTurnEventStore(s => s.composerState)` to calling `selectComposerState(roomId, entities, orderedIds)` on message-store entities. Same interface, different data source. `HitlResponseBar` and `onRespondToHitl` remain unchanged.

### Phase 4 — Wire new renderer into page

- Replace existing turn-based view (`TurnList` / `OrchestraTurn`) with `ConversationMessageList`
- `useSendMessage`: Remove `createOptimisticTurn` / `removeTurn` calls. Optimistic user message (in message-store with `id: 'cr:${clientRequestId}'`) serves as temporary turn anchor. After POST resolves and `replaceMessageId` swaps the optimistic id for the persisted user message id, the stable turn anchor becomes the persisted id. `selectConversationTurns` handles both states transparently.
- Verify all scenarios: hydration, streaming, HITL, cancel, SSE reconnect, error states, empty room, room switch, long conversation performance

**Required selector tests** (prevent `clientRequestId` from becoming long-term key):
1. Optimistic turn initially has `turnId` = `cr:${clientRequestId}`
2. After `replaceMessageId`, `turnId` becomes the persisted user message id
3. Hydrated history never produces a `turnId` with `cr:` prefix
4. Agent entity with `relatedMessageId` routes to correct user message turn (not via `clientRequestId`)
5. Unresolved agent entity does NOT auto-attach to the most recent turn — it enters `__unresolved__` bucket
6. Ephemeral placeholder with `clientRequestId` produces synthetic working card in the correct optimistic turn
7. After real agent entity arrives, synthetic working card is deduplicated (only one card per agent)

### Phase 5 — Delete old turn infrastructure

After Phase 4 is verified:
- Delete `turn-event-store/` (store + all projections: composer, rail, content-slots)
- Delete `hooks/turn/` (`useMessageStoreSync`, `useTurnHydration`, `useTurnProjection`, `useTurnScroll`)
- Delete `components/turn/` (`TurnList`, `OrchestraTurn`, `expand-collapse-context`)
- Delete `components/composer/ComposerShell.tsx` if fully replaced, or refactor to use new selectors
- Delete `room-page-shell.tsx` view-switching wrapper
- Clean up any orphaned imports

---

## 5. Component Specifications

### 5.1 UserMessageBlock

Full-width box matching chat input panel width.

**Content**: User avatar (24px circle) + message text + timestamp.

**Truncation**: Max 3 lines (`max-height: 4.5em` at `line-height: 1.5`). Overflow gets `mask-image` fade on text element. **Click to expand**: toggles `max-height: none`, removes mask. Click again to re-truncate.

**Sticky behavior**: When scrolled past, content mirrors into sticky bar pinned `12px` below viewport top. Identical styling including truncation. Sticky version is not click-expandable. On sentinel change: fade out (200ms) → swap → fade in (200ms). Sticky bar's bottom border is the boundary — no gradient, no gap.

**Elastic spacer**: `min-height` spacer at scroll container bottom ensures last UserMessageBlock can reach sticky position.

### 5.2 AgentCard

Independent, extensible block component.

**Layout** (two rows):
- Row 1: Agent avatar (32px rounded-square, initials fallback from name) + Agent name + Status text (right-aligned)
- Row 2: `└` connector + truncated task description (single line ellipsis)

**Color themes**: Deterministic by `agentId` (fallback to `agentName`). Palette: green, blue, purple, amber, rose. Unresolved agents use a muted/default theme.

```typescript
function getAgentTheme(agentId: string | undefined, agentName: string) {
  const key = agentId ?? agentName
  let hash = 0
  for (let i = 0; i < key.length; i++) {
    hash = ((hash << 5) - hash + key.charCodeAt(i)) | 0
  }
  return AGENT_THEMES[Math.abs(hash) % AGENT_THEMES.length]
}
```

**States**: AgentCard is purely presentational. It does NOT interpret `taskStatus` or derive label/color — that is the job of `mapAgentDisplayProps`, a pure mapper in selectors.

**Mapper** (`mapAgentDisplayProps`): Converts `MessageEntity` → `AgentDisplayProps`:

```typescript
interface AgentDisplayProps {
  label: string           // e.g. "Working", "Completed · 2m ago", "Failed"
  tone: 'accent' | 'muted' | 'danger' | 'warning'
  isAnimated: boolean     // shimmer on/off
  ariaLabel: string       // "{agentName} — {label}"
}
```

Mapping table (implemented in mapper, NOT in component):

| taskStatus | label | tone | isAnimated |
|------------|-------|------|------------|
| submitted / working | "Working" | accent | true |
| working + has streaming content | "Streaming" | accent | true |
| completed | "Completed · {relative time}" | muted | false |
| failed | "Failed" | danger | false |
| rejected | "Rejected" | danger | false |
| canceled | "Canceled" | muted | false |
| input-required (HITL) | "Needs Input" | warning | true |
| auth-required | "Auth Required" | warning | false |

**AgentCard props**: `{ agentName, agentId, taskDescription, theme, display: AgentDisplayProps }`. Card renders `display.label` as status text, applies `display.tone` to color, toggles shimmer by `display.isAnimated`, sets `aria-label` from `display.ariaLabel`.

**HITL**: Card shows "Needs Input" status only. Muted hint below: "Agent is waiting for your response in the input panel below."

**Accessibility**: `role="status"` on status region. `aria-label` from `display.ariaLabel`.

### 5.3 AgentContentBlock

Borderless Markdown rendering. No wrapper, no card, no border.

**Typography**: `14px`, `line-height: 1.8`. Headings in primary text color. Inline code with surface background + card border.

**Streaming**: Typewriter cursor (`|` in accent-blue, 0.8s step-end blink). Removed on stream complete.

**Attribution**: May be prefixed with muted label like "Security Analyst:" when multiple agents respond in one turn.

### 5.4 UserAnswerCard

HITL Q&A record. Border card style.

- Label: "Response to {Agent Name}" (muted)
- Q: Agent's question (muted, indented)
- A: User's answer (normal color, indented)

### 5.5 ScrollToBottomButton

Extends existing button. New content indicator: small badge dot. Keyboard-focusable.

---

## 6. HITL — Single Source of Truth

All HITL state derives from shared selectors. Composer and renderer both consume the same functions.

```typescript
// In src/lib/selectors/conversation.ts

function selectPendingHitls(roomId, entities, orderedIds): PendingHitl[] {
  // Filter: entity.roomId === roomId && entity.hitlRequestId != null
  // Grouped HITL rule (mirrors useActiveHitlRequests):
  //   1. Collect group IDs that still have at least one unanswered question
  //      (hitlGroupId present && !hitlResolved && !hitlUserAnswer)
  //   2. Non-grouped entities: include if !hitlResolved
  //   3. Grouped entities: include ALL questions in a group that has
  //      at least one unanswered question (so the UI can show the full
  //      group with pagination, not just the unanswered items)
  // Return ordered list of { hitlId, agentName, question, messageId, groupId?, groupTotal?, groupIndex? }
}

function selectComposerState(roomId, entities, orderedIds): ComposerState {
  const pendingHitls = selectPendingHitls(roomId, entities, orderedIds)

  // isProcessing: true when at least one agent task is still running.
  // Filter criteria — ALL must be true:
  //   1. entity.roomId === roomId         (current room only)
  //   2. entity.isEphemeral !== true      (exclude processing placeholders)
  //   3. entity.messageType === 'agent'   (only agent entities)
  //   4. isPendingState(entity.taskStatus) (submitted | working)
  //
  // Interactive states are NOT processing:
  //   - input-required  → enters HITL mode via pendingHitls
  //   - auth-required   → future: separate auth flow, not processing spinner
  //
  // Terminal states (completed | failed | canceled | rejected) are excluded
  // by isPendingState.
  const hasActiveTask = orderedIds.some(id => {
    const e = entities[id]
    return e
      && e.roomId === roomId
      && !e.isEphemeral
      && e.messageType === 'agent'
      && e.taskStatus
      && isPendingState(e.taskStatus)
  })

  return {
    mode: pendingHitls.length > 0 ? 'hitl_responding' : 'normal',
    isProcessing: hasActiveTask,
    pendingHitls,
  }
}

function selectAgentHitlState(entity: MessageEntity): HitlState | null {
  if (!entity.hitlRequestId) return null
  return {
    hitlId: entity.hitlRequestId,
    resolved: entity.hitlResolved === true,
    question: entity.hitlPrompt ?? entity.content ?? entity.taskStatusMessage ?? '',
    answer: entity.hitlUserAnswer ?? null,
  }
}
```

**Cutover**: `ComposerShell` replaces `useTurnEventStore(s => s.composerState)` with `selectComposerState(roomId, entities, orderedIds)` wrapped in a `useMessageStore` subscription. Same consumer interface, different source.

---

## 7. SSE Dispatcher Fixes

**Fix 1 — Out-of-order terminal race**: Verify existing guard in `sse-handlers/index.ts` (L159-165). Confirm it accepts `agent_response` content when entity is terminal but has no content (only `taskError`). Add regression test.

**Fix 2 — Pending buffer TTL**: Increase from 30s to 120s. Add production-visible logging on eviction.

**Fix 3 — Single-write policy relaxation**: Allow terminal `task_update` to append error context even when streaming content exists.

---

## 8. Scroll Behavior

### 8.1 Auto-Scroll

- **At bottom** (within ~100px): auto-scroll on new content
- **Scrolled up**: no auto-scroll, ScrollToBottomButton shows badge

### 8.2 Room Entry / Re-Entry

1. Scroll to bottom
2. Sticky shows last UserMessageBlock
3. Elastic spacer ensures sticky works even with short final turn

### 8.3 Streaming Content

- At bottom: smooth auto-scroll
- Scrolled up: no change, badge on ScrollToBottomButton

---

## 9. Layout Rules

### 9.1 Width Hierarchy

```
Container (max-width: 800px)
├── UserMessageBlock + InputPanel    → padding: 0 var(--conversation-padding-outer)
└── Turn content (all other blocks)  → padding: 0 var(--conversation-padding-inner)
```

- `--conversation-padding-outer`: `16px` (desktop), `12px` (< 640px)
- `--conversation-padding-inner`: `32px` (desktop), `20px` (< 640px)

### 9.2 Spacing

- Between turns: `var(--conversation-gap-turn)` = `32px`
- Within turn: `var(--conversation-gap-block)` = `8px`
- Agent divider in same turn: `1px solid var(--conversation-border-subtle)`, margin `12px` top/bottom
- Top spacer: `12px` above sticky bar
- Sticky boundary: border-bottom is exact boundary, no gap, no gradient
- InputPanel boundary: border-top is exact boundary, same treatment as sticky

### 9.3 Accessibility & Motion

- `prefers-reduced-motion: reduce`: disable shimmer and fade transitions
- ProcessBlock toggle (future): `<button>` with `aria-expanded`
- AgentCard status: `role="status"`

---

## 10. CSS Design Tokens

All styles reference tokens, never raw hex in component code.

```css
:root {
  /* Backgrounds */
  --conversation-bg: #09090b;              /* zinc-950 */
  --conversation-surface: #0a0a0f;         /* AgentCard, block bodies */

  /* Borders */
  --conversation-border: #27272a;          /* zinc-800, cards + input */
  --conversation-border-subtle: #18181b;   /* zinc-900, dividers */

  /* Text */
  --conversation-text-primary: #fafafa;    /* zinc-50, headings, names */
  --conversation-text-secondary: #e4e4e7;  /* zinc-200, content body */
  --conversation-text-tertiary: #d4d4d8;   /* zinc-300, paragraphs */
  --conversation-text-muted: #71717a;      /* zinc-500, labels, times */
  --conversation-text-dim: #52525b;        /* zinc-600, completed status */

  /* Agent accent colors */
  --conversation-agent-green: #4ade80;
  --conversation-agent-blue: #3b82f6;
  --conversation-agent-purple: #a78bfa;
  --conversation-agent-amber: #fbbf24;
  --conversation-agent-rose: #fb7185;
  --conversation-agent-yellow: #eab308;    /* HITL */

  /* Layout */
  --conversation-padding-outer: 16px;
  --conversation-padding-inner: 32px;
  --conversation-gap-turn: 32px;
  --conversation-gap-block: 8px;
  --conversation-sticky-top: 12px;
  --conversation-max-width: 800px;

  /* Motion */
  --conversation-shimmer-duration: 3.5s;
  --conversation-fade-duration: 200ms;
  --conversation-chevron-duration: 150ms;
  --conversation-cursor-duration: 800ms;
}

@media (max-width: 639px) {
  :root {
    --conversation-padding-outer: 12px;
    --conversation-padding-inner: 20px;
  }
}

@media (prefers-reduced-motion: reduce) {
  :root {
    --conversation-shimmer-duration: 0s;
    --conversation-fade-duration: 0s;
  }
}
```

---

## 11. Animation Specifications

All durations reference tokens above.

| Animation | Token | Easing | Details |
|-----------|-------|--------|---------|
| Card shimmer | `--conversation-shimmer-duration` | ease-in-out | `::before` pseudo, `background-size: 300%`, left-to-right sweep |
| Sticky fade | `--conversation-fade-duration` | ease | Opacity transition on content swap |
| Typewriter cursor | `--conversation-cursor-duration` | step-end | Opacity blink |
| Scroll-to-bottom | native | smooth | `scrollBehavior: 'smooth'` |

---

## 12. Data Types Summary

```typescript
// ── Top-level: grouped turns ────────────────────────────────
// Output of selectConversationTurns
interface ConversationTurnView {
  turnId: string                    // persisted user message id (stable), cr:${clientRequestId}
                                    // (temporary, live only), or '__unresolved__'
  userMessage: MessageEntity | null // null only for unresolved bucket
  blocks: ConversationBlock[]
}

// ── Per-block within a turn ─────────────────────────────────
type ConversationBlock =
  | { type: 'agent_card'; agentId: string; agentName: string; display: AgentDisplayProps; taskDescription: string; theme: AgentTheme }
  | { type: 'agent_content'; agentId: string; agentName: string; content: string; isStreaming: boolean }
  | { type: 'user_answer'; agentName: string; question: string; answer: string }
  | { type: 'agent_divider' }
  | { type: 'unresolved_content'; entity: MessageEntity }

// ── Agent display — output of mapAgentDisplayProps ──────────
interface AgentDisplayProps {
  label: string
  tone: 'accent' | 'muted' | 'danger' | 'warning'
  isAnimated: boolean
  ariaLabel: string
}

// ── Composer state ──────────────────────────────────────────
interface ComposerState {
  mode: 'normal' | 'hitl_responding'
  isProcessing: boolean
  pendingHitls: PendingHitl[]
}

interface PendingHitl {
  hitlId: string
  agentName: string
  question: string
  messageId: string
  // Grouped HITL fields — present when hitlGroupId exists on entity
  groupId?: string
  groupTotal?: number
  groupIndex?: number
  isAnswered: boolean   // hitlResolved || !!hitlUserAnswer
}
// Grouped HITL contract: selectPendingHitls returns ALL questions in a
// group that still has at least one unanswered question. This matches
// the existing useActiveHitlRequests behavior — the consumer (composer/
// HitlResponseBar) can paginate through the full group, showing both
// answered and unanswered items. A group is "active" if any member has
// !hitlResolved && !hitlUserAnswer.

// ── HITL state ──────────────────────────────────────────────
interface HitlState {
  hitlId: string
  resolved: boolean
  question: string      // from entity.hitlPrompt ?? entity.content ?? entity.taskStatusMessage
  answer: string | null // from entity.hitlUserAnswer
}
```
