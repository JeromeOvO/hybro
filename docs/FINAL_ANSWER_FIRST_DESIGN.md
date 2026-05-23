# Final Answer First — Chat UX Redesign

**Date**: 2026-05-23
**Status**: Draft
**Scope**: Redesign conversation rendering so multi-agent turns show only the final synthesized answer by default, with intermediate agent activity collapsible on demand. Single pipeline on `TurnViewModel`.

---

## 1. Problem Statement

Today, multi-agent supervisor turns render all agent responses (individual + synthesis) at the same visual level. Users see every intermediate step and the final summary interleaved, creating noise. The desired experience (like Cursor's approach): show the final optimal result prominently, hide intermediate multi-agent conversation/debate behind an expandable section.

## 2. Goals

- **Final answer first**: synthesis/summary content is the primary visible output of a multi-agent turn
- **Details on demand**: individual agent responses are collapsed into a "N agents contributed" section the user can expand
- **Smooth live experience**: during processing, show live activity; on completion, transition to the clean final-answer view
- **One rendering pipeline**: consolidate onto `TurnViewModel` (`build-turns.ts`), eliminating the parallel `selectConversationTurns` path
- **Mode-aware display**: backend orchestration mode (supervisor/coordinator/single) determines what "final answer" means

## 3. Non-Goals

- Backend API or SSE protocol changes (V1 — frontend derives everything from existing entity state)
- Animation/motion polish (deferred to V2)
- Activity timeline expansion panel with per-step event log (deferred — `TurnViewModel.events` is ready when needed)
- Changing the composer, HITL response bar, or agent detail pane

---

## 4. Architecture Decision

### Consolidate on `TurnViewModel` (delete `selectConversationTurns`)

| Criterion | `selectConversationTurns` | `TurnViewModel` / `build-turns.ts` |
|---|---|---|
| Summary detection | None — treats all agents equally | `selectSummary()`, `isSummaryAgent` built in |
| Supervisor awareness | None | `isSupervisorTurn`, `supervisorStage` |
| Agent result model | Flat `ConversationBlock[]` | `AgentResultViewModel` with status, HITL, duration |
| Collapse/expand support | None | `isHiddenInCompact` on events |
| Streaming buffers | Merged at selector level | Not handled — **solved at component level** |
| Ephemeral placeholders | Handled inline | Filters them out — **needs small addition** |
| Incremental rebuild | External `turnsEqual` in hook | Built-in `buildTurnsIncremental` |
| Production usage | Currently rendering (since Apr 27) | Never connected to rendering |
| Age | ~4 weeks | ~6 weeks |

**Decision**: Extend `TurnViewModel` with ephemeral handling, build new rendering components, then remove `selectConversationTurns`.

**Rationale**: Adding summary/display-mode/collapse semantics to `selectConversationTurns` would mean reimplementing 80% of `TurnViewModel` inside it. The streaming gap in `TurnViewModel` is smaller and solved at the component layer (matching how `AgentResponseDetailPane` already works). Neither system is battle-tested at scale; choose the one with better architectural bones.

---

## 5. Display Modes

Each completed turn renders in one of four modes, derived from its `agentResults` and `summary`:

```typescript
type TurnDisplayMode =
  | 'single_agent'         // One agent responded — show directly
  | 'summary_with_sources' // Synthesis exists — show synthesis, collapse sources
  | 'parallel_results'     // Multiple agents, no synthesis — show all equally
  | 'working'              // Still processing — show live activity
```

### Derivation Logic

```typescript
function deriveTurnDisplayMode(turn: TurnViewModel): TurnDisplayMode {
  const realAgents = turn.agentResults.filter(r => !r.isSummaryAgent)

  // IMPORTANT: Do NOT use `turn.summary !== null` here.
  // selectSummary() returns a summary even when no synthesis was generated
  // (it picks the "best" regular agent as a fallback). We must check whether
  // an actual system summary agent completed with content.
  const summaryResult = turn.agentResults.find(r => r.isSummaryAgent)
  const hasCompletedSynthesis = summaryResult?.status === 'completed'
    && summaryResult.content.trim().length > 0

  // Summary is streaming — show it prominently even though it's not yet "completed".
  // The component will overlay live buffer content via useStreamingStore.
  const hasSynthesisInProgress = summaryResult?.status === 'working'

  if (hasCompletedSynthesis && realAgents.length >= 2) return 'summary_with_sources'
  if (hasSynthesisInProgress && realAgents.length >= 1) return 'summary_with_sources'
  if (turn.status === 'active') return 'working'
  if (realAgents.length === 1) return 'single_agent'
  if (realAgents.length > 1) return 'parallel_results'
  return 'single_agent'
}
```

**Key insight**: The mode switches to `summary_with_sources` as soon as the summary agent entity appears (even while streaming), NOT when it completes. This ensures the user sees the synthesis building in real-time. The `SynthesisContent` component reads the streaming buffer to show live content, while `AgentResultContent` inside `CollapsedSources` shows the already-completed source agent content.

**Why not `turn.summary !== null`?** `selectSummary()` has fallback priorities that return a summary built from the first completed regular agent when no system summary agent exists. If the supervisor chose DONE (no synthesis), `turn.summary` would still be non-null, incorrectly triggering `summary_with_sources`. Checking `isSummaryAgent` directly on `agentResults` is the only reliable signal.

### When Each Mode Activates

| Backend Action | Result | Display Mode |
|---|---|---|
| Supervisor → SYNTHESIZE | `synthesis_text` emitted, summary entity created | `summary_with_sources` |
| Supervisor → DONE | No synthesis, individual responses are final | `parallel_results` |
| Queue mode, 2+ agents | Coordinator generates debate/non-debate summary | `summary_with_sources` |
| Single agent (any mode) | One agent response | `single_agent` |
| Budget exhausted | Forced synthesis generated | `summary_with_sources` |
| In progress | Agents still working | `working` |

---

## 6. Visual Design Per Mode

### `single_agent`

```
┌─────────────────────────────────────────────┐
│ [Agent Avatar]  Agent Name  ·  Completed    │
│                                             │
│ <Full markdown response content>            │
│                                             │
└─────────────────────────────────────────────┘
```

No changes from today's rendering.

### `summary_with_sources`

```
┌─────────────────────────────────────────────┐
│ [HYBRO AI Avatar]  HYBRO AI                 │
│                                             │
│ <Full synthesized markdown content>         │
│ <Artifacts if any>                          │
│                                             │
│ ┌─ ▶ 3 agents contributed ────────────────┐ │
│ │  [avatar] YouTube Creator — Completed   │ │
│ │  [avatar] Transfer Agent  — Completed   │ │
│ │  [avatar] Paid Creator    — Failed      │ │
│ └─────────────────────────────────────────┘ │
└─────────────────────────────────────────────┘
```

- Synthesis content is primary and always visible
- Source agents in a `Collapsible` (Radix) — collapsed by default on completed turns, expanded during live streaming
- Clicking a source agent card opens the existing `AgentResponseDetailPane`
- Failed agents show red indicator in the collapsed list

### `parallel_results`

```
┌─────────────────────────────────────────────┐
│ [avatar] YouTube Creator — Completed        │
│ <Full content>                              │
│ ─────────────────────────────────────────── │
│ [avatar] Transfer Agent — Completed         │
│ <Full content>                              │
│ ─────────────────────────────────────────── │
│ [avatar] Paid Creator — Completed           │
│ <Full content>                              │
└─────────────────────────────────────────────┘
```

All agents shown at equal prominence (similar to today's multi-agent view).

### `working`

```
┌─────────────────────────────────────────────┐
│ [shimmer] Agent Name — Working              │
│   └ Working on your request...              │
│ ─────────────────────────────────────────── │
│ [avatar] Other Agent — Streaming            │
│ <Streaming content with cursor>             │
└─────────────────────────────────────────────┘
```

Live activity shown as today. When the turn completes, morphs into one of the above modes.

---

## 7. Transition: Working → Complete

The display mode re-derives on every store update. The transition sequence:

1. **Agents working** → `displayMode = 'working'` → live agent cards with shimmer
2. **All agents complete, synthesis decision pending** → ephemeral placeholder updates to "Synthesizing responses..." → `displayMode` stays `'working'` because the ephemeral result has `status: 'working'`
3. **Summary `task_submitted` arrives** → summary entity appears with `taskStatus: 'working'` → `displayMode` flips to `'summary_with_sources'` (the `hasSynthesisInProgress` path). Source agents collapse, synthesis area shows streaming placeholder.
4. **Summary streaming begins** → streaming buffer fills → `SynthesisContent` renders live content via `useStreamingStore`
5. **Summary completes** → entity `status` becomes `'completed'`, content persisted → same `summary_with_sources` mode, now fully stable

**Critical dependency — no flash between modes**: The gap between "last agent completes" and "summary `task_submitted` arrives" is bridged by the ephemeral placeholder. The backend sends `processing_status(details="Synthesizing responses...")` immediately after deciding to synthesize, which updates the ephemeral entity. The ephemeral suppression logic (Section 8) keeps it visible when `hasSupervisorStage && !hasWorkingAgent`, maintaining `turn.status = 'active'` and `displayMode = 'working'` during this 1-3s gap.

Without correct ephemeral handling, the user would see a flash: `working` → `parallel_results` (1-3 seconds while supervisor LLM decides) → `summary_with_sources`.

### Collapse Behavior

- **Turns from current session (live)**: sources section starts **expanded** while the turn is active. On completion, it stays expanded until the user sends a new message (next turn starts). This respects the user's attention — they were watching the agents work and want to see the final arrangement before it collapses.
- **Historical turns (hydrated from DB)**: sources always start **collapsed**. The user is catching up on past results — they want the answer, not the process.
- **User manually collapsed/expanded**: respect the choice for the lifetime of the component (React state). No localStorage persistence needed for V1.

---

## 8. Data Model Changes to `TurnViewModel`

### Add to `TurnViewModel`:

```typescript
interface TurnViewModel {
  // ... existing fields (unchanged) ...

  /** Resolved display mode for rendering dispatch. */
  displayMode: TurnDisplayMode
}
```

`displayMode` is computed in `assembleTurn()` after `agentResults` and `summary` are populated.

### Add to `AgentResultViewModel`:

```typescript
interface AgentResultViewModel {
  // ... existing fields (unchanged) ...

  /** Whether this result is currently streaming (buffer exists and not complete). */
  isStreaming: boolean  // ADD — derived at component level, not stored in view model
}
```

Note: `isStreaming` is NOT stored in the view model. It's derived at render time by the component checking `useStreamingStore`. This keeps `buildTurns` pure.

### Ephemeral Handling (New)

Remove the `if (entity.isEphemeral) return null` filter in `buildAgentResult`. Instead:

```typescript
function buildAgentResult(entity: MessageEntity, ...): AgentResultViewModel | null {
  if (!entity) return null

  if (entity.isEphemeral) {
    // Emit a synthetic "working" result for the ephemeral placeholder
    return {
      agentId: entity.agentId ?? entity.id,
      agentName: entity.senderName,
      agentSource: entity.agentSource,
      messageId: entity.id,
      status: 'working',
      content: '',
      artifacts: [],
      isSummaryAgent: false,
      taskStatusMessage: entity.taskContent,
      isEphemeral: true, // NEW flag for suppression logic
    }
  }

  // ... rest of existing logic unchanged ...
}
```

Then in `assembleTurn`, apply the same suppression logic as `selectConversationTurns`:
- If real agents exist for the same turn, suppress ephemeral results
- Exception: supervisor stage ephemerals (non-empty `taskStatusMessage`) when no agent is currently working

---

## 9. Component Architecture

### New Components

```
src/components/conversation/
├── TurnRenderer.tsx           — renders user message + dispatches on turn.displayMode
├── SynthesisContent.tsx       — renders summary body + artifacts
├── CollapsedSources.tsx       — Radix Collapsible with agent list
├── AgentResultContent.tsx     — renders one agent result (with streaming overlay)
├── ParallelResults.tsx        — renders all agent results at equal prominence
└── LiveActivityFeed.tsx       — renders working-state agent results
```

`TurnRenderer` is responsible for the full turn layout:

```typescript
function TurnRenderer({ turn, ... }: { turn: TurnViewModel }) {
  return (
    <div className="conversation-turn">
      {turn.userMessageId && (
        <div className="conversation-user-sticky">
          <UserMessageBlock entity={entities[turn.userMessageId]} />
        </div>
      )}
      {/* Display mode dispatch */}
      {turn.displayMode === 'single_agent' && <AgentResultContent ... />}
      {turn.displayMode === 'summary_with_sources' && <SynthesisWithSources ... />}
      {turn.displayMode === 'parallel_results' && <ParallelResults ... />}
      {turn.displayMode === 'working' && <LiveActivityFeed ... />}
    </div>
  )
}
```

### Hook

```typescript
// src/hooks/useTurnViewModels.ts
export function useTurnViewModels(roomId: string): TurnViewModel[] {
  const entities = useMessageStore(s => s.entities)
  const orderedIds = useMessageStore(s => s.orderedIds)

  // V1: pass empty events array. The event log (event-log.ts) is a plain Map
  // with no reactivity — changes to it don't trigger re-renders. Events are
  // only needed for durationMs/eventCount (activity log expansion, Phase 2).
  // When Phase 2 adds the activity timeline, convert event-log.ts to a Zustand
  // store slice or use messageStore.version as a proxy trigger.
  const events: readonly RawTimelineEvent[] = EMPTY_EVENTS

  // buildTurnsIncremental internally preserves referential identity for
  // unchanged turns (via turnsAreEqual). We only need a top-level length/id
  // check to avoid propagating a new array reference when nothing changed.
  const prev = useRef<TurnViewModel[]>([])
  const next = useMemo(
    () => buildTurnsIncremental(prev.current, entities, orderedIds, events),
    [entities, orderedIds, events],
  )
  prev.current = next
  return next
}

const EMPTY_EVENTS: readonly RawTimelineEvent[] = []
```

Note: `buildTurnsIncremental` already preserves referential identity per-turn internally. The hook does not need an additional equality check wrapper — `useMemo` + the incremental builder's identity preservation is sufficient for React.memo on child components.

### Streaming at Component Level

Components that render agent content subscribe to streaming buffers directly:

```typescript
function AgentResultContent({ result }: { result: AgentResultViewModel }) {
  const buffer = useStreamingStore(s => s.buffers[result.messageId])
  const content = buffer ? buffer.text : result.content
  const isStreaming = buffer ? !buffer.isComplete : false
  const artifacts = buffer ? buffer.artifacts : result.artifacts

  return (
    <>
      <MarkdownContent content={content} isStreaming={isStreaming} />
      {artifacts?.length > 0 && <ArtifactList artifacts={artifacts} />}
    </>
  )
}
```

`SynthesisContent` uses the same pattern for the summary agent. When `deriveTurnDisplayMode` enters `summary_with_sources` on `task_submitted` (summary status=working), the streaming buffer may not exist yet. `SynthesisContent` handles this gracefully:

```typescript
function SynthesisContent({ summaryResult }: { summaryResult: AgentResultViewModel }) {
  const buffer = useStreamingStore(s => s.buffers[summaryResult.messageId])
  const content = buffer?.text || summaryResult.content
  const isStreaming = buffer ? !buffer.isComplete : (summaryResult.status === 'working')

  if (!content && isStreaming) {
    return <SynthesisStreamingPlaceholder />  // shimmer/typing indicator
  }

  return (
    <>
      <MarkdownContent content={content} isStreaming={isStreaming} />
      {/* Artifacts rendered when available */}
    </>
  )
}
```

This matches `AgentResponseDetailPane`'s existing pattern and keeps `buildTurns` pure/testable.

### CollapsedSources Interaction

```typescript
interface CollapsedSourcesProps {
  sourceResults: AgentResultViewModel[]
  selectedAgentMessageId?: string  // from room-ui-store
  onOpenDetail: (messageId: string) => void
  defaultExpanded: boolean  // true for live turns, false for hydrated
}
```

Behavior:
- If `selectedAgentMessageId` matches a source result, force-expand the collapsible (user is viewing a source in the detail pane)
- Each source agent renders as a compact `AgentCard` variant (existing component, no rightAction, `interactive=true`)
- Clicking opens `AgentResponseDetailPane` via the same `onOpenAgentDetail` flow (desktop side pane or mobile sheet)
- Trigger text: `"N agents contributed"` with status indicators (green check for completed, red dot for failed)

---

## 10. Edge Cases

| Case | Handling |
|---|---|
| Summary exists but empty/failed | `deriveTurnDisplayMode` checks for `isSummaryAgent && status === 'completed' && content.trim().length > 0`. Empty/failed summary → fall back to `parallel_results`. |
| Single agent + supervisor enabled | Supervisor may DONE without synthesis for 1-agent turns → `single_agent`. |
| HITL Q&A during processing | `HitlResponseBar` in composer dock (unchanged). Resolved HITL appears in expanded source detail via `hitlResolved` on `AgentResultViewModel`. |
| Budget-exhausted forced synthesis | Treated as normal `summary_with_sources`. |
| Cancellation mid-processing | `turn.status` becomes `'failed'` → stay in `working` mode briefly, then show whatever content exists as `parallel_results`. |
| Page reload / DB hydration | Mode derived from persisted entity state. No transition animations on hydration. Collapsible starts **collapsed** for historical turns. |
| SSE reconnection mid-turn | Existing `reconcileWithDb` fires. Entities update → `buildTurnsIncremental` re-derives. |
| Ephemeral suppression | Ephemeral results with `isEphemeral: true` suppressed when real agents for same turn exist, UNLESS it's a supervisor stage update and no agent is working (keeps `displayMode = 'working'` during synthesis gap). |
| Two summary agents (legacy compat) | `selectSummary` priority: `supervisor_synthesis` > `debate_summary` > `non_debate_summary` > `summary`. First match wins. |
| `selectSummary` fallback to non-system agent | Does NOT trigger `summary_with_sources`. `deriveTurnDisplayMode` checks `isSummaryAgent` on results, not `turn.summary` existence. |
| Detail pane open + sources collapse | If `AgentResponseDetailPane` is showing a source agent, the `CollapsedSources` component auto-expands (reads `selectedAgentMessageId` from room-ui-store). |
| Parallel results ordering | Ordered by agent message `timestamp` (arrival order from backend). Future: support `relevance_order` from supervisor trajectory. |

---

## 10.1. Accessibility Requirements

`CollapsedSources` must meet WCAG 2.1 AA:
- Trigger uses `aria-expanded` (provided by Radix Collapsible)
- Trigger has accessible name: `"N agents contributed, expandable"` via `aria-label`
- Content panel linked via `aria-controls` (provided by Radix)
- Failed agents announced: `"Agent Name — Failed"` in aria-label on status badge
- Keyboard: Enter/Space toggles collapsible (Radix default)

---

## 11. Migration Plan

### Phase 1: Extend `buildTurns` (no rendering changes)

1. Add ephemeral handling to `buildAgentResult`
2. Add `displayMode` field to `TurnViewModel` and compute it in `assembleTurn`
3. Add `isEphemeral` flag to `AgentResultViewModel`
4. Implement ephemeral suppression in `assembleTurn` (port from `selectConversationTurns`)
5. Add comprehensive tests for all display mode derivations
6. Create `useTurnViewModels` hook

### Phase 2: Build new rendering components

7. Create `TurnRenderer` dispatching on `displayMode`
8. Create `SynthesisContent` (wraps `MarkdownContent` + `ArtifactList`)
9. Create `CollapsedSources` (Radix `Collapsible` + compact `AgentCard` list)
10. Create `AgentResultContent` (with streaming overlay)
11. Create `ParallelResults` and `LiveActivityFeed`
12. Wire `AgentCard` click → existing `onOpenAgentDetail` → existing `AgentResponseDetailPane`

### Phase 3: Swap rendering pipeline

13. In `ConversationMessageList`, replace `useConversationTurnViews` with `useTurnViewModels`
14. Replace `ConversationTurn` with `TurnRenderer`
15. Verify all modes render correctly (single, summary, parallel, working)
16. Verify streaming, HITL, detail pane, scroll-to-bottom all still work

### Phase 4: Delete old pipeline

17. Delete `src/lib/selectors/select-conversation-turns.ts`
18. Delete `src/lib/selectors/route-agent.ts`
19. Delete `src/hooks/useConversationTurnViews.ts`
20. Delete `src/components/conversation/ConversationTurn.tsx` (old block dispatcher)
21. Delete `src/components/conversation/UnresolvedAgentGroup.tsx`
22. Keep: `conversation-types.ts` (themes, shared types), `map-agent-display.ts`, `select-hitl.ts`, `select-agent-response-detail.ts`, `select-composer-state.ts`

### Phase 5 (Optional): Backend hint

23. Add `display_hint` field to `processing_status` SSE event (completed frame) — `"summary"` | `"parallel"` | `"single"`
24. Frontend uses hint to override derived mode for ambiguous cases

---

## 12. Files Affected

### New files
- `src/hooks/useTurnViewModels.ts`
- `src/components/conversation/TurnRenderer.tsx`
- `src/components/conversation/SynthesisContent.tsx`
- `src/components/conversation/CollapsedSources.tsx`
- `src/components/conversation/AgentResultContent.tsx`
- `src/components/conversation/ParallelResults.tsx`
- `src/components/conversation/LiveActivityFeed.tsx`

### Modified files
- `src/lib/room-timeline/build-turns.ts` — ephemeral handling, displayMode
- `src/lib/room-timeline/types.ts` — `TurnDisplayMode`, `displayMode` field
- `src/components/conversation/ConversationMessageList.tsx` — swap hook + renderer
- `src/components/room-page-shell.tsx` — minor (detail pane wiring unchanged)

### Deleted files (Phase 4)
- `src/lib/selectors/select-conversation-turns.ts`
- `src/lib/selectors/route-agent.ts`
- `src/hooks/useConversationTurnViews.ts`
- `src/components/conversation/ConversationTurn.tsx`
- `src/components/conversation/UnresolvedAgentGroup.tsx`

### Kept unchanged
- `src/components/conversation/AgentCard.tsx`
- `src/components/conversation/AgentContentBlock.tsx`
- `src/components/conversation/AgentResponseDetailPane.tsx`
- `src/components/conversation/UserMessageBlock.tsx`
- `src/components/conversation/UserAnswerCard.tsx`
- `src/components/conversation/ScrollToBottomButton.tsx`
- `src/components/conversation/scroll-state.ts`
- `src/components/conversation/conversation-tokens.css`
- `src/components/composer/*`
- `src/stores/message-store/*`
- `src/stores/streaming-store/*`
- `src/lib/system-agents.ts`

---

## 13. Testing Strategy

- **Unit tests for `buildTurns`**: deriveTurnDisplayMode with all mode combinations, ephemeral suppression, summary selection edge cases
- **Unit tests for new components**: render snapshots for each display mode
- **Integration test**: SSE sequence → store updates → useTurnViewModels → verify mode transitions (working → summary_with_sources)
- **E2E (Playwright)**: send message in supervisor room, verify synthesis appears prominently, verify collapsed sources are expandable, verify detail pane opens on source click

---

## 14. Open Questions

1. **Parallel results ordering**: In `parallel_results` mode, should agents be ordered by completion time, by name, or by the backend's step ordering? (V1: use timestamp order)
2. **Room-level config override**: Should rooms support a `display_preference` setting (`auto` | `always_show_all` | `always_summary`) for power users who want to see everything?
3. **Cross-turn source references**: When a user asks a follow-up about a specific agent's response from a previous turn, should the UI link back to that collapsed source? (Deferred — each turn is independent for V1)
4. **Agent result ranking in parallel mode**: When supervisor chooses DONE, should the frontend highlight the "best" agent based on supervisor reasoning? (Requires backend to pass ranking data — deferred)

---

## 15. Resolved Design Decisions

| Decision | Rationale |
|---|---|
| Use `isSummaryAgent` on results, not `turn.summary` existence | `selectSummary()` has fallback that returns non-null for any turn with content — would incorrectly trigger collapse for DONE-action turns |
| Events array empty for V1 | Event log is a non-reactive plain Map; making it reactive is deferred to activity-timeline Phase 2 |
| Sources expanded for live turns, collapsed for history | Respects user attention during live processing; reduces noise for historical browsing |
| Ephemeral handling prevents mode flash | The "Synthesizing responses..." ephemeral bridges the 1-3s gap between last agent completing and summary `task_submitted` arriving |
| Streaming solved at component level | Matches existing `AgentResponseDetailPane` pattern; keeps `buildTurns` pure and testable |
| No auto-collapse on synthesis complete | Disorienting to collapse content the user was actively watching; collapse on next turn start instead |
| Switch to `summary_with_sources` on `task_submitted`, not `completed` | Users should see synthesis streaming live — waiting for completion would show a blank/working state for several seconds |
| `buildTurnsIncremental` handles referential stability | No external `turnsEqual` wrapper needed in the hook — the builder already preserves per-turn identity |
