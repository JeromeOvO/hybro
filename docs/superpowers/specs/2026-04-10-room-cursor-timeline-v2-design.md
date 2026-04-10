# Room Conversation Timeline V2 — Cursor-Style Visual Redesign

> **Status:** Draft
> **Date:** 2026-04-10
> **Branch:** `feat/room-cursor-timeline-ui`
> **Builds on:** `docs/ROOM_TIMELINE_DESIGN.md` (Phase 1 implementation)
> **Scope:** Visual/UX redesign + data model extensions. Changes touch: component rendering layer, `TurnViewModel` (new fields), `build-turns.ts` (HITL status fix + summary selection + turnsAreEqual), `AgentResultViewModel` (new status value + fields), `system-agents.ts` (new narrowly-scoped helpers). Placeholder agent list flows via component props (not buildTurns). No changes to SSE handlers or message store.

---

## 1. What This Is

A visual redesign of the conversation turn rendering layer to match Cursor/TUI workflow aesthetics. Phase 1 established the data model (TurnViewModel, build-turns, event-log); this spec redesigns how that data is presented.

**Core principle:** Each agent result renders as an inline block with avatar + name + content, separated by subtle divider lines. No card borders, no backgrounds, no redundant information.

## 2. Problems Being Solved

| Problem | Current Behavior | Target |
|---------|-----------------|--------|
| No immediate feedback after sending | Blank space until first SSE event | Agent placeholder rows with shimmer "Thinking" appear instantly |
| HITL status stuck | "Awaiting input..." persists after user answers | Resolved HITL shows compact card; status updates correctly |
| HITL displayed as separate agent | "Question & Answer" looks like another agent in the list | HITL nested under parent agent (agent-sourced) or standalone card (supervisor-sourced) |
| Summary duplicates results | Summary + Agent Results show same content in expanded state | Summary visible only in collapsed state; hidden when expanded |
| Event rail too verbose | Timestamp list takes large space | Replaced with inline chips next to agent name ("4 steps", "3.2s") |
| No agent avatars | Only color dot + name | 28×28 rounded-square avatar via `getAgentAvatarUri()` |
| Small text / badge sizes | Agent names and content too small | Larger badge (text-base) and content (text-base) |
| Supervisor status unclear | No visual distinction for Supervisor orchestration | HYBRO AI header bar with stage status |

## 3. Design Decisions

All decisions confirmed through visual companion brainstorming (mockups V1–V7 + supervisor V1–V3).

### 3.1 Agent Result Layout — Inline Blocks with Avatar

Each agent result renders as:

```
[28×28 Avatar]  Agent Name    [inline chips]
                Content rendered as markdown...
                [Artifacts]
                [Resolved HITL compact card if applicable]
────────────────────────────────────────────── (1px divider)
```

**Avatar:** 28×28px, `border-radius: 6px` (rounded square). Generated via `getAgentAvatarUri(agentId)` (dicebear bottts). Static — no animation effects.

**Agent name:** `text-base font-semibold text-foreground` (larger than current `text-sm`).

**Inline chips:** Replace the event rail. Small pills next to agent name showing summary stats:
- Step count: `"4 steps"` — derived from event count for that agent
- Duration: `"3.2s"` — time from agent_started to agent_completed
- Style: `background: secondary, border-radius: 4px, padding: 1px 6px, font-size: 10px, color: muted-foreground`

**Divider:** `1px solid border` between agent blocks. No card backgrounds or shadows.

### 3.2 Waiting State — Shimmer Placeholder Rows

When the user sends a message, placeholder rows appear immediately for each target agent:

```
[Avatar]  Agent Name    Thinking        ← shimmer text
────────────────────────────────────────
[Avatar]  Agent Name    Thinking        ← shimmer text
```

**Lifecycle:**
1. User sends message → placeholder rows appear instantly (optimistic, from target agent list)
2. SSE `task_submitted` / content starts arriving → placeholder transitions to normal agent result with content
3. Placeholder is a transient state, not a permanent component

**Shimmer animation:** `background-clip: text` with gradient sweep. The text itself shimmers, not the background.

```css
.shimmer-text {
  background: linear-gradient(90deg, 
    hsl(var(--muted-foreground)) 0%, 
    hsl(var(--muted-foreground) / 0.6) 25%, 
    hsl(var(--foreground)) 50%, 
    hsl(var(--muted-foreground) / 0.6) 75%, 
    hsl(var(--muted-foreground)) 100%
  );
  background-size: 200% auto;
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  animation: shimmer 2.5s ease-in-out infinite;
}

@keyframes shimmer {
  0% { background-position: 200% center; }
  100% { background-position: -200% center; }
}
```

**Status text values:**
- `"Thinking"` — agent hasn't returned any content yet
- `"Generating"` — streaming content is arriving
- `"Needs input"` — HITL triggered (uses yellow shimmer variant, see §3.3)

**No completed indicator.** Once content fully arrives, the shimmer disappears and the normal content display takes over. There is no checkmark or "done" badge.

### 3.3 HITL Card Design

Two sources of HITL, two display patterns:

#### Agent-sourced HITL (real A2A agent triggers `input_required`)

HITL is nested inside the parent agent's result block:

**Waiting state:**
```
[Avatar]  Agent Name    Needs input     ← yellow shimmer
          ┌─────────────────────────────┐
          │ What would you like the     │  ← question card
          │ Excel spreadsheet to contain│     (border, slight bg)
          └─────────────────────────────┘
```

**Resolved state:**
```
[Avatar]  Agent Name    [inline chips]
          ┌─────────────────────────┐
          │ What would you like...  │  ← question truncated, muted
          │ • creators contact emails│  ← green dot + answer, emphasized
          └─────────────────────────┘
          Content from agent after HITL resolved...
```

#### Supervisor-sourced HITL (`supervisor_hitl` system agent)

Displayed as a standalone turn-level question card, NOT as an agent row:

```
  ┌─────────────────────────────────────┐
  │ (??) Needs input                    │  ← question mark icon + yellow shimmer
  │                                     │
  │ What date range would you like to   │
  │ analyze for engagement rates?       │
  └─────────────────────────────────────┘
```

- Icon: `CircleHelp` from lucide-react (question mark in circle), colored `text-yellow-500`
- Border: `border-yellow-500/20` (subtle yellow tint)
- Not associated with any specific agent avatar
- After resolution: collapses to compact card same as agent-sourced resolved pattern

**Yellow shimmer variant:**
```css
.shimmer-text-yellow {
  background: linear-gradient(90deg, 
    hsl(30, 80%, 30%) 0%, 
    hsl(40, 90%, 50%) 25%, 
    hsl(45, 95%, 65%) 50%, 
    hsl(40, 90%, 50%) 75%, 
    hsl(30, 80%, 30%) 100%
  );
  background-size: 200% auto;
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  animation: shimmer 2.5s ease-in-out infinite;
}
```

### 3.4 HITL Status Fix

**Root cause:** `buildAgentResult()` determines status from `entity.taskStatus`. When HITL is answered (`hitlResolved=true`, `hitlUserAnswer` exists), the `taskStatus` may still be `input_required` because the backend hasn't sent a terminal status update yet.

**Current `isStreaming` conflict:** `AgentResultCard` derives streaming from `result.status === 'awaiting_input' && result.content.length > 0` (line 61). Simply skipping the `'awaiting_input'` assignment and falling through to `'completed'` would prematurely kill the streaming shimmer.

**Fix:** Introduce a `'working'` status value (see §5.3 for full semantics). When HITL is answered but taskStatus hasn't reached terminal, set `status = 'working'` instead of `'awaiting_input'`. The component maps `'working'` + content to streaming display with shimmer "Generating", while simultaneously showing the resolved HITL compact card.

See §5.3 for the complete status derivation code and component mapping table.

### 3.5 Summary Block — Collapsed Only

**Collapsed turn:** Summary is the preview. Shows agent badge + title + body (2-line clamp). Click to expand.

**Expanded turn:** Summary is hidden. Only Agent Results (with full content) are displayed. This eliminates the duplication where Summary and Agent Results show the same information.

### 3.6 Event Rail → Inline Chips

The full event rail component (`TurnEventTimeline`) is removed from the default view. Events are distilled into inline chips next to each agent's name:

```
[Avatar]  Excel Generator Agent   4 steps   3.2s
```

**Chip data derivation:**
- **Step count:** Count of events for this agent in `turn.events` (filter by `agentId`)
- **Duration:** Difference between first `agent_started` and last `agent_completed` event timestamp for this agent

**This is an intentional reduction in process visibility.** The full event rail (timestamps, individual event types, HITL/artifact/progress time sequence) is removed in favor of summary-level chips. This trades granular debugging information for a cleaner Cursor-like experience. The `TurnEventTimeline` component is kept in the codebase but not rendered; it can be restored behind a dev-mode toggle if detailed process visibility is needed.

**No "Show process" toggle** in V2. If user feedback indicates the loss of event detail is a problem, re-adding a collapsible detail view is straightforward since the event data model is unchanged.

### 3.7 Supervisor Display

Supervisor mode adds a header bar above the agent results within a turn:

#### Processing state:
```
[HYBRO icon]  HYBRO AI  ·  Step 2 of 3 · Dispatching agents   ← shimmer
──────────────────────────────────────────────────────────────
[Agent results below...]
```

#### Completed state:
```
[HYBRO icon]  HYBRO AI  ·  3 agents · 12.4s                    ← static
──────────────────────────────────────────────────────────────
[Agent results below...]
```

**HYBRO AI icon:** Use `/favicon.svg` (the HYBRO blue tesseract). 18×18px in the header bar. Static — no rotation or animation.

**"HYBRO AI" text:** Brand gradient (`linear-gradient(to right, hsl(var(--color-hybro-bro)), hsl(var(--color-hybro-hy)))`), `font-weight: 600`, `font-size: 12px`.

**Stage status:** Shimmer text showing `details` from SSE `processing_status` events (e.g., "Evaluating...", "Dispatching agents..."). Combined with `stepNumber`/`totalSteps` as "Step N of M · {details}".

**Completed stats:** Static text showing agent count + total duration.

**Detection:** `isSupervisorTurn` is derived purely from turn content — the presence of supervisor-specific system agent entities (`supervisor_hitl` or `supervisor_synthesis`) in the turn. See §5.2. No dependency on `room.extend_info`.

### 3.8 Summary from HYBRO AI

The `supervisor_synthesis` system agent (currently named "Summary Agent") is renamed in display:

- **Display name:** "Summary from HYBRO AI"
- **Name styling:** Brand gradient text (same as HYBRO AI header)
- **Avatar:** HYBRO favicon icon (same `/favicon.svg`), 28×28px in a container with `border: 1px solid border, border-radius: 6px, background: background`
- **Rendered as normal agent row** (per option D — no special layout treatment)

Detection: `isSummarySystemAgent(agentId)` from `system-agents.ts` (see §5.7). Matches only the summary family (`supervisor_synthesis`, `debate_summary`, `non_debate_summary`, `summary`). Does NOT match `supervisor_hitl`.

### 3.9 Non-Supervisor HITL from Real Agents

When a real A2A agent (not `supervisor_hitl`) triggers HITL via `input_required` status:
- The HITL card is nested inside that agent's result block
- The agent's status shows yellow shimmer "Needs input"
- After resolution, the compact card appears above the agent's content

This is the most common HITL pattern (single agent asking a clarifying question).

## 4. Component Changes

### 4.1 New Components

| Component | File | Purpose |
|-----------|------|---------|
| `AgentPlaceholderRow` | `src/components/agent-placeholder-row.tsx` | Shimmer "Thinking" row with avatar + name. Transient loading state. |
| `HitlCompactCard` | `src/components/hitl-compact-card.tsx` | Resolved HITL display: question (truncated) + answer (emphasized). Used inside agent result blocks. |
| `HitlQuestionCard` | `src/components/hitl-question-card.tsx` | Active HITL question display with yellow border. Used for both agent-sourced (nested) and supervisor-sourced (standalone). |
| `SupervisorHeader` | `src/components/supervisor-header.tsx` | HYBRO AI icon + brand text + stage shimmer / completed stats. |
| `InlineChips` | `src/components/inline-chips.tsx` | Small pills showing step count + duration next to agent name. |

### 4.2 Modified Components

| Component | Changes |
|-----------|---------|
| `conversation-timeline.tsx` | New prop `roomAgentList`. Computes `pendingAgents` for active turn by diffing `roomAgentList` against `turn.agentResults`. Passes `pendingAgents` prop to `MemoizedTurn`. |
| `conversation-turn.tsx` | New prop `pendingAgents` (component-layer, not in TurnViewModel). Reads `turn.isSupervisorTurn` (TurnViewModel field). Renders `AgentPlaceholderRow` for pending agents. Hide `SummaryBlock` in expanded state. Remove `TurnEventTimeline` rendering. Add `SupervisorHeader` when `isSupervisorTurn`. |
| `agent-result-card.tsx` | Add avatar (28×28). Replace `HitlHistoryList` with `HitlCompactCard`/`HitlQuestionCard`. Add `InlineChips`. Replace `isStreaming` logic: `'working'` + content → streaming. Handle `'awaiting_input'` → yellow shimmer "Needs input". |
| `agent-badge.tsx` | Add avatar image support. Handle "Summary from HYBRO AI" display name + brand gradient for summary-family agents (detect via `isSummarySystemAgent()`, NOT `isSystemAgent()`). |
| `build-turns.ts` | Add `'working'` status: `hitlResolved + isInteractiveState → 'working'`. Populate `isSupervisorTurn` (via `isSupervisorSystemAgent()`), `supervisorStage` on TurnViewModel. Fix `selectSummary()` to use `isSummarySystemAgent()`. Compute `eventCount`/`durationMs` per agent. Extend `turnsAreEqual()` for new fields (§5.8). |
| `types.ts` | Extend `TurnViewModel`: `isSupervisorTurn`, `supervisorStage`. Extend `AgentResultViewModel`: `status` adds `'working'`, plus `isSummaryAgent`, `hitlResolved`, `hitlPending`, `eventCount`, `durationMs`. |
| `system-agents.ts` | Add `isSupervisorSystemAgent()` and `isSummarySystemAgent()` helpers with narrowly-scoped ID sets (§5.7). Existing `isSystemAgent()` unchanged. |

### 4.3 Removed/Replaced

| Component | Action |
|-----------|--------|
| `TurnEventTimeline` | Not rendered by default (inline chips replace it). Component kept but unused. |
| `HitlHistoryList` (in agent-result-card) | Replaced by `HitlCompactCard`. |
| `StatusIndicator` (in agent-result-card) | Replaced by shimmer text in agent name row. |

## 5. Data Model Changes

### 5.1 Placeholder Target Agents — Component-Layer Prop, Not TurnViewModel

**Problem:** Placeholder rows need the target agent list for the current turn. But `buildTurns()` only receives `entities`, `orderedIds`, and `events` (`build-turns.ts:31`). `MessageEntity` has no `resolvedAgents` or target list field (`types.ts:34`). The room's `room_agent_set` lives in `useRoomData` (`useRoomData.ts:83`), which is outside the message store / timeline builder input.

**Decision: Placeholder logic belongs in the component layer, not in buildTurns.**

`buildTurns()` signature is unchanged — it stays a pure function of message store data. Placeholder rendering is a transient UI concern driven by room-level data that changes independently of messages.

**Implementation:**

1. `ConversationTimeline` receives a new prop `roomAgentList: { agentId: string; agentName: string }[]`, passed from the page component which already has access via `useRoomData.getAgentList()`.

2. For the **active (last) turn only**, `ConversationTimeline` computes pending agents:
   ```ts
   const pendingAgents = isLastTurn
     ? roomAgentList.filter(a => !turn.agentResults.some(r => r.agentId === a.agentId))
     : []
   ```

3. `MemoizedTurn` receives `pendingAgents` as a separate prop (not part of `TurnViewModel`):
   ```ts
   <MemoizedTurn
     turn={turn}
     index={index}
     isActive={isLastTurn}
     pendingAgents={isLastTurn ? pendingAgents : EMPTY_ARRAY}
     onQuote={onQuote}
   />
   ```

4. `ConversationTurn` renders `AgentPlaceholderRow` for each entry in `pendingAgents`. When an agent's result arrives (appears in `turn.agentResults`), it drops out of the pending list automatically.

**Why not extend buildTurns:** Room agent data comes from `useRoomData` (TanStack Query), not the message store. Threading room data into `buildTurns` would create a cross-concern dependency (message store ↔ room query), violate the current clean separation, and force unnecessary turn rebuilds whenever room data refetches. The component layer already bridges these two data sources — that's where this logic belongs.

**Limitations:** For non-default targeting (mentions, saved groups), the room's `room_agent_set` may not exactly match the agents that will respond. Placeholders will still be correct for the common `room_default` case, and disappear as actual results arrive regardless.

### 5.2 TurnViewModel Extensions — isSupervisorTurn and supervisorStage

These fields CAN be derived from message store data (entity `agentId` values and SSE fields), so they belong in `TurnViewModel` populated by `buildTurns()`.

```ts
// src/lib/room-timeline/types.ts — additions to TurnViewModel
interface TurnViewModel {
  // ... existing fields ...
  
  /** Whether this turn was dispatched via Supervisor orchestration.
   *  Derived from presence of supervisor-specific system agent entities
   *  (supervisor_hitl, supervisor_synthesis) in the turn.
   *  Drives SupervisorHeader rendering. */
  isSupervisorTurn: boolean
  
  /** Supervisor stage details from latest processing_status SSE event.
   *  Only populated when isSupervisorTurn is true and turn is active. */
  supervisorStage?: {
    stepNumber?: number
    totalSteps?: number
    details?: string
  }
}
```

**Data source for `isSupervisorTurn`:** Check if any entity in the turn has an `agentId` matching a **supervisor-specific** system agent ID. NOT all system agents — only the two that are unique to supervisor orchestration:

```ts
import { isSupervisorSystemAgent } from '@/lib/system-agents'

// In buildTurn():
const isSupervisorTurn = turnEntities.some(e => isSupervisorSystemAgent(e.agentId))
```

Where `isSupervisorSystemAgent()` is a new helper in `system-agents.ts` (see §5.7) that matches only `supervisor_hitl` and `supervisor_synthesis`. This is distinct from `isSystemAgent()` which also matches `debate_summary`, `non_debate_summary`, and `summary` — those indicate other orchestration modes, not supervisor mode.

**Single rule — no §3.7 conflict:** The §3.7 mention of `room.extend_info.use_supervisor === true` is removed. `isSupervisorTurn` is derived purely from turn content (supervisor-specific entities present), not from room-level config. This avoids the need to thread room data into buildTurns and is a more reliable signal (a turn either has supervisor entities or it doesn't).

**Data source for `supervisorStage`:** From `MessageEntity.stepNumber`, `totalSteps`, and `taskContent` fields. These are set by SSE `processing_status` handler. Extract from the latest processing entity in the turn.

### 5.3 AgentResultViewModel Extensions

```ts
// src/lib/room-timeline/types.ts — additions to AgentResultViewModel
interface AgentResultViewModel {
  // ... existing fields ...
  
  /** New status value: HITL was answered, agent resumed work but hasn't
   *  reached terminal state yet. Distinct from 'awaiting_input' (HITL pending)
   *  and 'completed' (terminal). Components use this to show shimmer "Generating"
   *  and render the resolved HITL compact card simultaneously. */
  status: 'completed' | 'failed' | 'awaiting_input' | 'working'
  
  /** Whether this agent is a summary-family system agent
   *  (supervisor_synthesis, debate_summary, non_debate_summary, summary).
   *  Detected via isSummarySystemAgent() — NOT isSystemAgent() which also
   *  includes supervisor_hitl. Used for HYBRO AI visual treatment. */
  isSummaryAgent: boolean
  
  /** Resolved HITL: prompt and user answer. Separate from hitlHistory to
   *  distinguish "HITL answered, agent still working" from "historical HITL". */
  hitlResolved?: { prompt: string; answer: string }
  
  /** Whether agent has active (unanswered) HITL prompt */
  hitlPending?: { prompt: string }
  
  /** Event count and duration for inline chips */
  eventCount?: number
  durationMs?: number
}
```

### 5.4 Streaming / Status Semantics (Fix for Review Issue #2)

**Current problem:** `AgentResultCard` derives `isStreaming` from `result.status === 'awaiting_input' && result.content.length > 0` (line 61). This conflates "HITL pending with partial content" and "actively streaming". If we change resolved-HITL status to `'completed'`, streaming shimmer disappears prematurely.

**Solution:** Introduce `'working'` status. The status derivation in `buildAgentResult()` becomes:

```ts
let status: AgentResultViewModel['status'] = 'completed'
const hitlAnswered = entity.hitlResolved && !!entity.hitlUserAnswer

if (entity.taskStatus && isFailureState(entity.taskStatus)) {
  status = 'failed'
} else if (entity.taskStatus && isInteractiveState(entity.taskStatus)) {
  if (hitlAnswered) {
    // HITL was answered, agent resumed — still working
    status = 'working'
  } else {
    status = 'awaiting_input'
  }
} else if (entity.taskStatus && !isTerminalState(entity.taskStatus)) {
  status = 'working'
}
```

**Component mapping:**

| `status` | Shimmer text | HITL display | Content |
|----------|-------------|--------------|---------|
| `'working'` (no content) | "Thinking" (gray) | — | Hidden |
| `'working'` (has content) | "Generating" (gray) | Resolved compact card (if hitlResolved) | Streaming with cursor |
| `'awaiting_input'` | "Needs input" (yellow) | Active question card | Partial content if any |
| `'completed'` | None | Resolved compact card (if hitlResolved) | Full content |
| `'failed'` | None | — | Error message |

`AgentResultCard.isStreaming` is replaced by:
```ts
const isStreaming = result.status === 'working' && result.content.length > 0
```

### 5.5 Inline Chips Data Derivation

From `turn.events`, for each agent:
- `eventCount = turn.events.filter(e => e.agentId === agentId).length`
- `durationMs = lastCompleted.timestamp - firstStarted.timestamp` (if both exist)

Computed in `buildAgentResult()` by passing the turn's events array.

### 5.6 Summary Selection Fix (Fix for Review Issue #3)

**Current problem:** `selectSummary()` uses `agentName.toLowerCase().includes('supervisor')` string heuristic. This doesn't match the actual system agent names ("Summary Agent", "Question & Answer") and won't match the new display name "Summary from HYBRO AI".

**Fix:** Replace the string heuristic with `isSummarySystemAgent()` (see §5.7):

```ts
import { isSummarySystemAgent } from '@/lib/system-agents'

// In selectSummary():
// Priority 1: system summary agent (supervisor_synthesis, debate_summary, etc.)
const systemSummary = completedWithContent.find(r =>
  isSummarySystemAgent(r.agentId)
)
if (systemSummary) return buildSummaryFromResult(systemSummary)
```

Uses `isSummarySystemAgent()` which matches only summary-family IDs. No ad-hoc exclusions needed.

### 5.7 system-agents.ts — New Helpers

Add two narrowly-scoped helpers alongside the existing `isSystemAgent()`:

```ts
// src/lib/system-agents.ts — new additions

/** Supervisor-specific system agent IDs. Used for isSupervisorTurn derivation.
 *  These are the two agent IDs unique to supervisor orchestration mode. */
const SUPERVISOR_SYSTEM_AGENT_IDS = new Set(['supervisor_hitl', 'supervisor_synthesis'])

/** Summary-family system agent IDs. Used for HYBRO AI visual treatment
 *  (brand gradient name, HYBRO icon avatar, "Summary from HYBRO AI" display).
 *  Excludes supervisor_hitl which is NOT a summary agent. */
const SUMMARY_SYSTEM_AGENT_IDS = new Set([
  'supervisor_synthesis',
  'debate_summary',
  'non_debate_summary',
  'summary',
])

export function isSupervisorSystemAgent(agentId: string | undefined): boolean {
  return !!agentId && SUPERVISOR_SYSTEM_AGENT_IDS.has(agentId)
}

export function isSummarySystemAgent(agentId: string | undefined): boolean {
  return !!agentId && SUMMARY_SYSTEM_AGENT_IDS.has(agentId)
}
```

**Three helpers, three scopes:**

| Helper | Matches | Used by |
|--------|---------|---------|
| `isSystemAgent()` (existing) | All 5 system agent IDs | General system agent detection |
| `isSupervisorSystemAgent()` (new) | `supervisor_hitl`, `supervisor_synthesis` | `isSupervisorTurn` in buildTurns (§5.2) |
| `isSummarySystemAgent()` (new) | `supervisor_synthesis`, `debate_summary`, `non_debate_summary`, `summary` | `selectSummary()` (§5.6), `agent-badge.tsx` visual treatment (§3.8), `isSummaryAgent` field on AgentResultViewModel (§5.3) |

### 5.8 Incremental Build — turnsAreEqual Update

**Problem:** `turnsAreEqual()` (`build-turns.ts:412`) preserves referential identity for unchanged turns so `React.memo` can skip re-renders. The current comparison checks: `id`, `status`, `agentResults.length`, `userContent`, agent `messageId`/`status`/`content`/`artifacts`, and `events.length`. It does NOT check any of the new fields.

If `isSupervisorTurn`, `supervisorStage`, or per-agent `hitlResolved`/`hitlPending`/`eventCount`/`durationMs` change without the existing fields changing, `turnsAreEqual` returns true, the old reference is preserved, and React.memo skips the re-render. The new data is computed but never displayed.

**Fix:** Extend the comparison:

```ts
function turnsAreEqual(a: TurnViewModel, b: TurnViewModel): boolean {
  // ... existing checks (id, status, agentResults.length, userContent, events.length) ...

  // New TurnViewModel fields
  if (a.isSupervisorTurn !== b.isSupervisorTurn) return false
  if (a.supervisorStage?.stepNumber !== b.supervisorStage?.stepNumber) return false
  if (a.supervisorStage?.totalSteps !== b.supervisorStage?.totalSteps) return false
  if (a.supervisorStage?.details !== b.supervisorStage?.details) return false

  // Per-agent result: extend existing loop
  for (let i = 0; i < a.agentResults.length; i++) {
    // ... existing checks (messageId, status, content, artifacts) ...
    
    // New AgentResultViewModel fields
    if (a.agentResults[i].hitlResolved?.answer !== b.agentResults[i].hitlResolved?.answer) return false
    if (a.agentResults[i].hitlPending?.prompt !== b.agentResults[i].hitlPending?.prompt) return false
    if (a.agentResults[i].eventCount !== b.agentResults[i].eventCount) return false
    if (a.agentResults[i].durationMs !== b.agentResults[i].durationMs) return false
  }

  // Summary equality (already missing from current implementation)
  if ((a.summary?.sourceAgentId ?? null) !== (b.summary?.sourceAgentId ?? null)) return false
  if ((a.summary?.body ?? '') !== (b.summary?.body ?? '')) return false

  return true
}
```

**Note:** `targetAgentIds` (now a component-layer prop, not on TurnViewModel per §5.1) does NOT need to be checked here. `pendingAgents` is computed at render time from `roomAgentList` and `turn.agentResults` — changes to either trigger re-render through their own React update paths.

## 6. Animations

| Animation | CSS | `prefers-reduced-motion` |
|-----------|-----|--------------------------|
| Shimmer text (gray) | `background-clip: text` + gradient sweep, 2.5s | Static muted-foreground text |
| Shimmer text (yellow) | Same technique, amber gradient, 2.5s | Static amber text |
| Streaming cursor | `2px` inline block, `blink 1s step-end infinite` | Hidden |

All CSS-native. No JS animation libraries.

## 7. Accessibility

| Requirement | Implementation |
|-------------|----------------|
| Shimmer text | Has actual text content (not empty) — screen readers read "Thinking" |
| HITL cards | `role="status"` with `aria-label` describing state |
| Agent avatars | `alt=""` (decorative, name already in text) |
| Supervisor header | `role="status"` with `aria-live="polite"` (dynamic stage updates, not a page landmark) |
| Inline chips | `aria-label="4 steps, 3.2 seconds"` |

## 8. Test Plan

### 8.1 Data Path Tests (Critical — review issue #6)

These test the data model changes that feed the components. Must pass before component tests.

| Category | File | Tests |
|----------|------|-------|
| **Placeholder pending agents** | `conversation-timeline.test.ts` | `pendingAgents` computed correctly: `roomAgentList` minus agents in `turn.agentResults`; empty for non-active turns; empty when `roomAgentList` is empty |
| **System summary selection** | `build-turns.test.ts` | `selectSummary()` picks `supervisor_synthesis` over regular agents; does NOT pick `supervisor_hitl`; uses `isSummarySystemAgent()` not string heuristic |
| **HITL + streaming coexistence** | `build-turns.test.ts` | `hitlResolved=true` + `hitlUserAnswer` + `isInteractiveState` → status is `'working'` (not `'awaiting_input'`); `hitlResolved=false` + `isInteractiveState` → status stays `'awaiting_input'` |
| **isSupervisorTurn derivation** | `build-turns.test.ts` | Turn with `supervisor_synthesis` entity → `isSupervisorTurn=true`; turn with `debate_summary` only → `isSupervisorTurn=false`; turn with only real agents → `isSupervisorTurn=false` |
| **'working' status semantics** | `build-turns.test.ts` | Non-terminal non-interactive taskStatus → `'working'`; `AgentResultCard` maps `'working'` + content to streaming display; `'working'` + no content to "Thinking" shimmer |
| **turnsAreEqual new fields** | `build-turns.test.ts` | `turnsAreEqual` returns false when `isSupervisorTurn` changes; returns false when `supervisorStage.details` changes; returns false when agent `hitlResolved` changes; returns false when `summary` changes |
| **system-agents helpers** | `system-agents.test.ts` | `isSupervisorSystemAgent('supervisor_hitl')` → true; `isSupervisorSystemAgent('debate_summary')` → false; `isSummarySystemAgent('supervisor_synthesis')` → true; `isSummarySystemAgent('supervisor_hitl')` → false |

### 8.2 Component Tests

| Category | Tests |
|----------|-------|
| AgentPlaceholderRow | Renders avatar + name + shimmer text; disappears when result arrives |
| HitlCompactCard | Shows truncated question + emphasized answer; handles empty answer |
| HitlQuestionCard | Yellow border + shimmer; displays question text |
| SupervisorHeader | Shows stage shimmer when processing; shows stats when completed; hidden when `isSupervisorTurn=false` |
| InlineChips | Renders step count + duration; handles missing data gracefully |
| agent-result-card (updated) | Avatar renders; HITL compact card renders for resolved HITL; shimmer "Generating" for `'working'` status with content; "Needs input" yellow shimmer for `'awaiting_input'` |
| conversation-timeline (updated) | `pendingAgents` prop passed to active turn; empty for non-active turns |
| conversation-turn (updated) | `AgentPlaceholderRow` for `pendingAgents` prop; summary hidden in expanded state; event rail not rendered; SupervisorHeader rendered when `isSupervisorTurn=true` |
| agent-badge (updated) | "Summary from HYBRO AI" brand gradient for summary-family agents (`isSummarySystemAgent`); HYBRO icon avatar for summary agents only |

## 9. Out of Scope

- Backend changes (SSE protocol, message format, task state machine)
- New orchestration modes or workflow changes
- Debate mode visual treatment (follow-up)
- Mobile-specific responsive adjustments (follow-up)
- Turn navigation sidebar
- "Show process" detailed event view (can be re-added as dev feature)
