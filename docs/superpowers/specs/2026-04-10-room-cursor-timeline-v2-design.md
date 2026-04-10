# Room Conversation Timeline V2 — Cursor-Style Visual Redesign

> **Status:** Draft
> **Date:** 2026-04-10
> **Branch:** `feat/room-cursor-timeline-ui`
> **Builds on:** `docs/ROOM_TIMELINE_DESIGN.md` (Phase 1 implementation)
> **Scope:** Primarily visual/UX layer. One bugfix in `build-turns.ts` (HITL status). No changes to SSE handlers or message store.

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

**Fix:** In `buildAgentResult()`, add an early check before `isInteractiveState()`. When `hitlResolved=true` and `hitlUserAnswer` exists, the agent has resumed work — skip the `awaiting_input` assignment:

```ts
let status: AgentResultViewModel['status'] = 'completed'
const hitlAnswered = entity.hitlResolved && !!entity.hitlUserAnswer

if (entity.taskStatus && isFailureState(entity.taskStatus)) {
  status = 'failed'
} else if (entity.taskStatus && isInteractiveState(entity.taskStatus) && !hitlAnswered) {
  // Only show awaiting_input if HITL has NOT been answered yet
  status = 'awaiting_input'
} else if (entity.taskStatus && !isTerminalState(entity.taskStatus)) {
  status = 'awaiting_input'
}
```

When `hitlAnswered` is true, the `isInteractiveState` branch is skipped. Status falls through to `'completed'` (which the component treats as "show content normally"). The shimmer "Generating" text is driven by whether content is still streaming, not by this status field.

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

**No "Show process" toggle** in the inline chip design. If detailed event debugging is needed in the future, it can be re-added as a dev-mode feature.

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

**Detection:** Supervisor mode is active when `room.extend_info.use_supervisor === true`. The header only renders when supervisor-related messages are present in the turn.

### 3.8 Summary from HYBRO AI

The `supervisor_synthesis` system agent (currently named "Summary Agent") is renamed in display:

- **Display name:** "Summary from HYBRO AI"
- **Name styling:** Brand gradient text (same as HYBRO AI header)
- **Avatar:** HYBRO favicon icon (same `/favicon.svg`), 28×28px in a container with `border: 1px solid border, border-radius: 6px, background: background`
- **Rendered as normal agent row** (per option D — no special layout treatment)

Detection: `agentId` matches any of the system agent IDs in `SYSTEM_AGENTS` that map to "Summary Agent" (`supervisor_synthesis`, `debate_summary`, `non_debate_summary`, `summary`).

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
| `conversation-turn.tsx` | Add `AgentPlaceholderRow` for waiting agents. Hide `SummaryBlock` in expanded state. Remove `TurnEventTimeline` rendering. Add `SupervisorHeader` when supervisor mode. |
| `agent-result-card.tsx` | Add avatar (28×28). Replace `HitlHistoryList` with `HitlCompactCard`. Add `InlineChips`. Fix status logic for resolved HITL. Increase text sizes. |
| `agent-badge.tsx` | Add avatar image support. Handle "Summary from HYBRO AI" display name + brand gradient for system agents. |
| `build-turns.ts` | Fix `buildAgentResult()` to check `hitlResolved` before `isInteractiveState`. |
| `types.ts` | Add fields to `AgentResultViewModel`: `avatarUri?: string`, `eventCount?: number`, `durationMs?: number`. |

### 4.3 Removed/Replaced

| Component | Action |
|-----------|--------|
| `TurnEventTimeline` | Not rendered by default (inline chips replace it). Component kept but unused. |
| `HitlHistoryList` (in agent-result-card) | Replaced by `HitlCompactCard`. |
| `StatusIndicator` (in agent-result-card) | Replaced by shimmer text in agent name row. |

## 5. Data Flow

### 5.1 Waiting State Data Source

Placeholder rows need the target agent list before SSE events arrive. Sources:

1. **Room default agents:** `room.room_agent_set` — the resolved agent list for the room
2. **Message-level targeting:** If the user used mentions or a saved group, the target list is different

The agent list is available at send time from the message store / room data. The `ConversationTurn` component checks: if a turn has `status === 'active'` and some agents in the target list have no corresponding `AgentResultViewModel` yet, render `AgentPlaceholderRow` for each missing agent.

### 5.2 Inline Chips Data Derivation

From `turn.events`, for each agent:
- `eventCount = turn.events.filter(e => e.agentId === agentId).length`
- `durationMs = lastCompleted.timestamp - firstStarted.timestamp` (if both exist)

This can be computed in `buildAgentResult()` or in a utility function consumed by the component.

### 5.3 Supervisor Detection

```ts
const isSupervisorMode = room?.extend_info?.use_supervisor === true
const hasSupervisorMessages = turn.agentResults.some(r => 
  SYSTEM_AGENT_IDS.includes(r.agentId ?? '')
)
const showSupervisorHeader = isSupervisorMode && hasSupervisorMessages
```

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
| Supervisor header | `role="banner"` within turn |
| Inline chips | `aria-label="4 steps, 3.2 seconds"` |

## 8. Test Plan

| Category | Tests |
|----------|-------|
| AgentPlaceholderRow | Renders avatar + name + shimmer text; disappears when result arrives |
| HitlCompactCard | Shows truncated question + emphasized answer; handles empty answer |
| HitlQuestionCard | Yellow border + shimmer; displays question text |
| SupervisorHeader | Shows stage shimmer when processing; shows stats when completed; hidden when not supervisor mode |
| InlineChips | Renders step count + duration; handles missing data gracefully |
| agent-result-card (updated) | Avatar renders; HITL compact card renders for resolved HITL; status fix for hitlResolved |
| conversation-turn (updated) | Placeholder rows appear for waiting agents; summary hidden in expanded state; event rail not rendered |
| build-turns (status fix) | hitlResolved + hitlUserAnswer → status is not 'awaiting_input' |

## 9. Out of Scope

- Backend changes (SSE protocol, message format, task state machine)
- New orchestration modes or workflow changes
- Debate mode visual treatment (follow-up)
- Mobile-specific responsive adjustments (follow-up)
- Turn navigation sidebar
- "Show process" detailed event view (can be re-added as dev feature)
