# Room Conversation Timeline Design Doc

> **Status:** Approved, ready to implement
> **Date:** 2026-04-09
> **Branch:** `feat/room-cursor-timeline-ui`
> **Reviews:** Design (2 rounds, 9/10), Engineering (1 round, CLEAR), Codex (1 round, 6 findings resolved)
> **Spec:** `docs/superpowers/specs/2026-04-09-room-conversation-cursor-ui-design.md`

---

## 1. What This Is

A redesign of Hybro's room conversation UI from a flat message list to a **Cursor-style turn-based timeline**.

The core change: the rendering unit shifts from `MessageEntity` to `TurnViewModel`. Each time a user sends a message, all subsequent agent responses are grouped into a single "turn", displayed as: user prompt, event rail, summary, agent result stack.

The existing MessageEntity store, SSE protocol, and hydration pipeline remain unchanged. Only a new derived view-model layer is added on top.

## 2. Why

The current UI has the **wrong rendering unit**:

- A single user request triggers multiple agent responses, but they render as unrelated bubble sequences
- Multi-agent collaboration has no visible structure... feels like reading a random message stream
- HITL is split between the timeline and the bottom panel with no shared context
- Artifacts appear as message attachments, not as workflow events
- Comparing different agent results within the same turn requires scanning the entire transcript

Users should see a **team working together**, not a message list.

## 3. Constraints

1. No changes to the backend SSE protocol
2. No replacement of the MessageEntity Zustand store
3. Single-column layout (no split-pane comparison)
4. Borderless block style (Cursor/TUI aesthetic, no card borders or shadows)
5. 6 implementation phases for incremental rollout
6. Summary selected from existing agent results, no additional AI generation calls

## 4. Architecture

### 4.1 View-Model Overlay

```
MessageEntity Store (unchanged)
    ↓ derived selectors
TurnViewModel[] (new)
    ↓ render
ConversationTimeline → ConversationTurn[] → Event Rail + Summary + Agent Results
```

A derived layer on top of the existing store. No changes to the data pipeline.

### 4.2 Data Model

```ts
// --- src/lib/room-timeline/types.ts ---

type TurnStatus = 'active' | 'awaiting_input' | 'completed' | 'failed' | 'partial'

interface TurnViewModel {
  id: string
  roomId: string
  userMessageId: string | null
  userContent: string
  userAttachments: AttachmentData[]
  timestamp: string
  status: TurnStatus
  events: TimelineEventViewModel[]
  summary: TurnSummaryViewModel | null
  agentResults: AgentResultViewModel[]
  activeAgentIds: string[]
}

type TimelineEventKind =
  | 'user_prompt'
  | 'agent_started'
  | 'agent_progress'
  | 'hitl_requested'
  | 'hitl_answered'
  | 'artifact_emitted'
  | 'agent_completed'
  | 'agent_failed'

interface TimelineEventViewModel {
  id: string
  kind: TimelineEventKind
  timestamp: string
  agentId?: string
  agentName?: string
  label: string
  body?: string
  artifactPayload?: ArtifactData
  hitlPayload?: { prompt: string; answer?: string }
  isLive: boolean
  isHiddenInCompact: boolean
}

interface TurnSummaryViewModel {
  sourceAgentId?: string
  sourceAgentName: string
  title: string
  body: string
  confidence?: 'high' | 'medium' | 'low'
}

interface AgentResultViewModel {
  agentId?: string
  agentName: string
  agentSource?: 'hub' | 'cloud'
  messageId: string
  status: 'completed' | 'failed' | 'awaiting_input'
  content: string
  artifacts: ArtifactData[]
  hitlHistory?: { prompt: string; answer: string }[]
}
```

### 4.3 Turn Construction Rules

**Boundary detection:**
- Each `messageType === 'user'` message starts a new turn
- Subsequent agent messages belong to the most recent user turn

**Message routing priority:**
1. `relatedMessageId` present → route to the corresponding turn (cross-turn routing)
2. No `relatedMessageId` → timestamp + user message boundary fallback

**Edge cases:**
- Agent messages before the first user message → synthetic system turn
- All agents failed in a turn → `status: 'failed'`

### 4.4 Incremental Derivation

`buildTurnsIncremental()` only rebuilds the active turn on SSE updates. Older turns maintain referential stability so `React.memo` skips re-rendering. When a `relatedMessageId` points to an older turn, that specific turn is rebuilt individually.

### 4.5 Summary Selection Priority

1. Supervisor/system summary agent result (if present)
2. Designated room summary agent (if configured)
3. Highest-priority completed agent result (product heuristics)
4. Latest completed non-empty agent result (fallback)

### 4.6 Event Accumulator

```
SSE Handler → event-log.ts (append-only) → buildTurns reads events
```

New file `src/lib/room-timeline/event-log.ts` captures timeline events at the SSE handler level (before message normalization / content merge). In-memory store, lost on page refresh. Acceptable because old turns collapse and hide the event rail by default.

## 5. Visual Design

### 5.1 Core Style: Borderless Blocks

All new components use borderless block style:

- No `border`, no `box-shadow`, no `background-color` for hierarchy
- Visual hierarchy through **type scale**, **color weight**, and **spacing** only
- The only border in the timeline: `1px` separator line between turns (`border-border`)

### 5.2 Page Layout

```
┌─────────────────────────────────────────────┐
│  Room Header                                │
├─────────────────────────────────────────────┤
│                                             │
│  Turn N-2 (collapsed): prompt + summary     │
│  ── 1px separator, 24px gap ──              │
│  Turn N-1 (collapsed): prompt + summary     │
│  ── 1px separator, 24px gap ──              │
│  Turn N (active, expanded):                 │
│    User prompt                              │
│    ├── Event rail (live, compact)           │
│    │   · user_prompt      0.1s              │
│    │   · agent_started    AgentA  0.3s      │
│    │   · agent_started    AgentB  0.5s      │
│    │   · agent_progress   AgentA  2.1s      │
│    │   · artifact_emitted AgentB  3.4s      │
│    │   · agent_completed  AgentA  4.2s      │
│    ├── Summary block (appears when first    │
│    │   agent completes)                     │
│    ├── Agent Result: AgentA                 │
│    │   [content, 6-line truncated]          │
│    └── Agent Result: AgentB                 │
│        [content, artifacts inline]          │
│                                             │
├─────────────────────────────────────────────┤
│  [HITL Panel if active]                     │
│  [Chat Input / Composer]                    │
└─────────────────────────────────────────────┘
```

### 5.3 Spacing Scale

| Context | Spacing |
|---------|---------|
| Between sections within a turn (prompt → events → summary → results) | `16px` (`space-y-4`) |
| Between turns | `24px` gap + `1px` separator line |
| Between agent result blocks | `12px` (`space-y-3`) |
| Event rail row internal padding | `4px` vertical |

### 5.4 Ordering

- Turns: **bottom-up** (latest at bottom), consistent with chat convention
- Agent results: summary source agent first → completed non-empty → awaiting_input → failed → empty terminal

### 5.5 Event Rail

**Desktop:**
- Compact log style, **20-24px row height**
- Monospace timestamps, dots on a thin 1px vertical rule (left-aligned), agent color pill, event label
- No connector lines between dots

**Mobile (< 768px):**
- Defaults to collapsed, showing a one-line summary (e.g., "6 events")
- Tap to expand with **44px row height** for touch targets

**Default-visible events:** user_prompt, agent_started, hitl_requested, hitl_answered, artifact_emitted, agent_completed, agent_failed

**Default-hidden:** streaming status updates, task status churn, repeated artifact updates

**"Show process" toggle:** Reveals hidden low-signal events

### 5.6 Event Animations

| Animation | Normal | `prefers-reduced-motion` |
|-----------|--------|--------------------------|
| Event slide-in | Slide from left, 150ms ease-out | Opacity fade only, no transform |
| Dot pulse | Single pulse on arrival | Static dot |
| Live breathing glow | Opacity 0.7→1.0, 2s cycle | Static highlight |
| Collapsible | Existing collapsible-down/up | Handled by globals.css |

All CSS-native animations, no external animation libraries. New keyframes defined in `globals.css`.

### 5.7 First-Second Experience: Immediate Events

After the user sends a message:
- **0.0s**: User prompt card appears (optimistic update)
- **0.1s**: `user_prompt` event slides into the event rail
- **0.2-0.5s**: `agent_started` events slide in as agents begin processing
- No shimmer placeholder, no "thinking" indicator. The event rail IS the loading state.

### 5.8 Content Truncation

Agent result content exceeding **6 lines** is truncated with:
- Gradient fade (`bg-linear-to-t from-background to-transparent`)
- "Show more" text button below the fade
- Click expands to full content

Shared `TruncatedContent` component with `maxLines` prop.

### 5.9 Summary Block Visual Treatment

Differentiated from agent result blocks through typographic hierarchy (not borders/shadows):
- Title: `16px / text-base font-semibold` (result blocks use `14px / text-sm`)
- Extra `8px` top margin above summary vs between result blocks
- Agent color dot or pill next to the agent name (using `accent` class)

### 5.10 Collapse Behavior

**Older turns (completed):** Default to showing only user prompt + summary. Event rail and result blocks are hidden.

**Failed turns:** Collapsed state shows `Warning: N agents failed: "error..."` instead of just the user prompt.

**Active turn:** Always expanded.

Click/tap on a collapsed turn expands it fully. User-expanded turns persist their expanded state.

### 5.11 Empty / Loading States

| Component | Loading | Empty | Error |
|-----------|---------|-------|-------|
| ConversationTimeline | Shimmer skeleton (3 placeholder turns) | "Start the conversation" with gradient icon (reuse existing) | Error banner with retry |
| Event Rail (active turn) | No placeholder, events appear as they arrive | Empty rail, no extra placeholder | N/A |
| Summary Block | Not shown until an agent completes | Not shown if no agent completed | N/A |
| Agent Result Stack | Not shown while agents are running | Not shown | N/A |
| Agent Result Block | Shimmer for streaming content | "Completed with no output" one-liner | Red status + error text (collapsed) |

Key rule: while agents are running, the active turn shows only the user prompt and the live event rail. No placeholder cards.

### 5.12 Agent Colors

Uses the existing `AGENT_COLOR_PALETTE` from `src/lib/agent-colors.ts`:
- 8 colors: sky, violet, teal, rose, amber, emerald, indigo, pink
- `getAgentColorClasses(agentId)` for hash-based assignment, stable per agent
- Each palette entry provides: `bg`, `border`, `accent`, `text`, `content` classes
- AgentBadge uses `accent` class for the color dot, `text` class for the name

## 6. Component Structure

### 6.1 New Files (10)

| File | Purpose | Est. Lines |
|------|---------|------------|
| `src/lib/room-timeline/types.ts` | TurnViewModel and related type definitions | ~80 |
| `src/lib/room-timeline/build-turns.ts` | Turn construction + summary selection + event building + incremental derivation | ~200 |
| `src/lib/room-timeline/event-log.ts` | Append-only event accumulator | ~60 |
| `src/components/conversation-timeline.tsx` | Timeline entry point, replaces room-messages rendering logic | ~120 |
| `src/components/conversation-turn.tsx` | Single turn renderer (prompt + events + summary + results) | ~200 |
| `src/components/turn-event-timeline.tsx` | Event rail + show process toggle | ~150 |
| `src/components/agent-result-stack.tsx` | Sorted result block container | ~60 |
| `src/components/agent-result-card.tsx` | Single agent result (streaming / completed / failed) | ~180 |
| `src/components/agent-badge.tsx` | Shared agent identity (name + color dot + optional source badge) | ~40 |
| `src/components/truncated-content.tsx` | Shared content truncation (maxLines + gradient fade + expand) | ~50 |

**Total: ~1,140 lines of new code**

Extraction trigger: if `conversation-turn.tsx` exceeds 250 lines, extract UserPrompt and/or Summary sections into independent files.

### 6.2 Modified Files (4)

| File | Changes |
|------|---------|
| `src/components/room-messages.tsx` | Replace `orderedIds.map` with `<ConversationTimeline>` |
| `src/hooks/useRoomMessages.ts` | Add `useConversationTurns()`, `useActiveTurn()`, `useTurnById()`, `useHitlTurnContext()` |
| `src/hooks/room/sse-handlers/index.ts` | Add event capture to event-log.ts |
| `src/app/globals.css` | Add keyframes: event-slide-in, dot-pulse, breathing-glow |

### 6.3 Reuse Existing Patterns

| Pattern | Source | Reuse In |
|---------|--------|----------|
| 8-color agent palette | `agent-colors.ts` | Event row agent pills, result block agent identity |
| `derivePhase()` function | `message-bubble.tsx` | Map to `TurnStatus` for turn-level state |
| shadcn `Collapsible` | artifact-list, HITL panel | "Show process" toggle, turn expand/collapse |
| Shimmer animation | `message-bubble.tsx` waiting phase | Loading skeletons in timeline |
| Typewriter animation | `message-bubble.tsx` streaming phase | Streaming content in active result blocks |
| Auto-scroll near-bottom | `room-messages.tsx` | Anchor to active turn instead of raw message |
| Gradient fade truncation | `message-bubble.tsx` long messages | 6-line truncation in result blocks |
| `MemoizedMessage` pattern | `room-messages.tsx` | `MemoizedTurn` for per-turn subscription |

## 7. HITL Design

**Hybrid model:** Timeline records HITL history, bottom panel handles active interaction.

- `hitl_requested` event card: requesting agent, question, pending state
- `hitl_answered` event: question + user answer + processing resumed
- Bottom HITL Panel shows agent identity, owning turn label, jump link back to timeline event
- `useHitlTurnContext(hitlMessageId)` exposed in Phase 2 to avoid duplicating grouping logic in the page-level HITL panel

## 8. Artifact Design

- When an artifact is created, an `artifact_emitted` event row is inserted in the event rail with inline rendering
- Streaming artifacts: merge updates for the same artifact ID into a single evolving event
- Agent result blocks show final artifacts again (different context: timeline = what happened, result = what was produced)

## 9. Accessibility

| Requirement | Implementation |
|-------------|----------------|
| Turn structure | `<article>` element with `aria-label="Turn N: {prompt preview}"` |
| Event rail | `role="log"` with `aria-live="polite"` for new events |
| Toggle buttons | `<button>` elements with descriptive `aria-label` |
| Status communication | Icon + text, never color-only (color-blind safe) |
| Keyboard navigation | Tab navigates between turns, Enter/Space expands collapsed turn |
| Streaming content | `aria-busy="true"` while streaming |
| Focus rings | Reuse existing `focus-visible:ring-ring/50 ring-[3px]` pattern |
| Reduced motion | All new animations degrade gracefully with `prefers-reduced-motion` |

## 10. Dark Mode

Uses existing CSS custom properties, no additional overrides needed:
- Turn separator line: `hsl(var(--border))` (adapts automatically)
- Event rail dots: agent palette dark variants (`dark:text-{color}-400`)
- Text hierarchy: `text-foreground` (primary), `text-muted-foreground` (secondary)

## 11. Performance

| Strategy | Mechanism |
|----------|-----------|
| Incremental derivation | Only rebuild active turn on SSE updates; old turns maintain referential stability |
| Zustand shallow | `useConversationTurns()` uses `shallow` comparator |
| React.memo | `MemoizedTurn` wraps each turn, skips re-rendering unchanged turns |
| Lazy rendering | Hidden event rows and collapsed turn details are not rendered |
| ErrorBoundary | Wraps `ConversationTimeline`, falls back to old flat message list on error |

## 12. Implementation Phases

### Phase 1: View-Model Layer
- `types.ts` + `build-turns.ts` + `event-log.ts`
- Turn construction, summary selection, event building, incremental derivation
- 25 unit tests

### Phase 2: Replace Rendering + Scroll + ErrorBoundary + HITL Context
- `conversation-timeline.tsx` replaces room-messages.tsx rendering
- Port existing scroll logic verbatim (near-bottom 100px, programmatic scroll flag, auto-scroll)
- ErrorBoundary with fallback to old flat list
- `useHitlTurnContext()` exposed
- 5 incremental derivation tests + 4 component tests

### Phase 3: Compact Event Flow
- `turn-event-timeline.tsx` (event rail + show process toggle + animations)
- SSE handler event capture into event-log.ts
- 5 component tests

### Phase 4: Summary + Result Blocks
- `agent-result-stack.tsx` + `agent-result-card.tsx`
- Summary rendering, result sorting, 6-line truncation
- 10 component tests

### Phase 5: Inline Artifacts + HITL History
- Artifact rendering in event rail
- Bottom HITL Panel connected to matching timeline events
- HITL history display in agent result cards

### Phase 6: Polish
- Visual polish
- Mobile adjustments
- Accessibility pass
- Turn-anchored scroll upgrade (replace ported scroll logic)
- 3 E2E tests

**Parallelization strategy:**
- Lane A: Phase 1 → 2 → 3 (sequential)
- Lane B: Phase 4 (independent, merge after Phase 2 lands)
- Lane C: AgentBadge + TruncatedContent (independent, merge anytime)

## 13. Test Plan

**52+ test paths:**

| Category | File | Cases |
|----------|------|-------|
| View-Model | `build-turns.test.ts` | 25 |
| Incremental Derivation | `build-turns-incremental.test.ts` | 5 |
| ConversationTimeline | `conversation-timeline.test.tsx` | 4 |
| ConversationTurn | `conversation-turn.test.tsx` | 6 |
| TurnEventTimeline | `turn-event-timeline.test.tsx` | 5 |
| AgentResultStack | `agent-result-stack.test.tsx` | 4 |
| AgentResultCard | `agent-result-card.test.tsx` | 6 |
| AgentBadge | `agent-badge.test.tsx` | 3 |
| TruncatedContent | `truncated-content.test.tsx` | 4 |
| **Unit subtotal** | | **62** |
| E2E | `room-timeline.spec.ts` | 3 |
| **Total** | | **65** |

Critical test scenarios:
- Turn grouping boundaries (relatedMessageId routing, synthetic turn, late arrival)
- Summary selection priority chain
- Incremental derivation referential stability
- Collapse/expand state persistence
- Mobile event rail collapse
- Streaming with no scroll jumps

## 14. Risks

| Risk | Mitigation |
|------|------------|
| Grouping inaccuracies | relatedMessageId priority + clear fallback rules + 25 unit tests |
| Duplicated content | Centralized dedupe rules in builder layer |
| Component bloat | 250-line extraction trigger |
| Performance regression | Incremental derivation + Zustand shallow + React.memo |
| New components white-screen | ErrorBoundary falls back to old flat message list |
| Events lost on refresh | Acceptable: old turns collapse and hide event rail |

## 15. Deferred Items

Tracked in `TODOS.md`:
1. **Backend persistent event stream** — Replace frontend event-log.ts (depends on hybro-backend Phase 7)
2. **Artifact event normalization** — Preserve artifact emission history in SSE ingest
3. **Turn navigation sidebar** — Quick navigation for rooms with 50+ turns

## 16. Success Criteria

- Users can visually distinguish conversation turns without reading content
- Event rail shows agent progress in real-time during active turns
- Multi-agent results are grouped and comparable within a single turn
- Older turns collapse to 2-3 lines for fast scanning
- Zero regressions in SSE streaming, HITL, or artifact display
