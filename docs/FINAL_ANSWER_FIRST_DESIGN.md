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

- Backend API or SSE protocol changes (**V1** — frontend derives from existing entity state; **Phase 5** adds `TurnCompletionHint` on terminal events)
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
  | 'awaiting_input'       // Supervisor CLARIFY — paused, waiting for user response
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

  // Supervisor CLARIFY pauses the run and requests user input via HITL.
  // Show the clarification question prominently with any partial results so far.
  if (turn.status === 'awaiting_input') return 'awaiting_input'

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
| Supervisor → CLARIFY | HITL request created, run paused | `awaiting_input` |
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

**Optional enhancement (Open Question #8)**: `LiveActivityFeed` may accept a `pendingAgents` prop (computed at component layer from the room's agent list, not in `TurnViewModel`) to show shimmer rows for agents the supervisor delegated to but that haven't yet sent `task_submitted`. Aligns with the April 10 V2 timeline plan.

```
┌─────────────────────────────────────────────┐
│ [HYBRO AI Avatar]  HYBRO AI  ·  Needs Input │
│                                             │
│ ┌─ Question ──────────────────────────────┐ │
│ │ "Which region should we focus on for    │ │
│ │  the campaign analysis?"                │ │
│ │                                         │ │
│ │ [Choice A]  [Choice B]  [Choice C]     │ │
│ └─────────────────────────────────────────┘ │
│                                             │
│ ┌─ Progress so far (optional) ──────────┐   │
│ │ [avatar] Agent A — Completed           │  │
│ │ [avatar] Agent B — Completed           │  │
│ └────────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
```

- The HITL question is prominently displayed (not hidden in the composer)
- Any agents that already completed before CLARIFY are shown in a secondary section
- The `HitlResponseBar` in the composer dock provides the reply mechanism (unchanged)
- Once the user responds, `turn.status` reverts to `'active'` and mode returns to `'working'`
- Resolved HITL Q&A appears as a `UserAnswerCard` in the expanded activity view

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

  /** True when this result came from an ephemeral placeholder entity. */
  isEphemeral?: boolean  // ADD — used by suppression logic in assembleTurn
}
```

Note: `isStreaming` is NOT part of the view model type. It's derived at render time by the component checking `useStreamingStore`. This keeps `buildTurns` pure — the view model reflects persisted/entity state only.

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
├── ClarificationPrompt.tsx   — renders HITL question + partial progress
└── LiveActivityFeed.tsx       — renders working-state agent results
```

`TurnRenderer` is responsible for the full turn layout:

```typescript
function TurnRenderer({ turn, ... }: { turn: TurnViewModel }) {
  // UserMessageBlock expects a full MessageEntity — read from store by ID.
  // This is a single targeted subscription, not a full entities scan.
  const userEntity = useMessageStore(s =>
    turn.userMessageId ? s.entities[turn.userMessageId] : undefined
  )

  return (
    <div className="conversation-turn">
      {userEntity && (
        <div className="conversation-user-sticky">
          <UserMessageBlock entity={userEntity} />
        </div>
      )}
      {/* Display mode dispatch */}
      {turn.displayMode === 'single_agent' && <AgentResultContent ... />}
      {turn.displayMode === 'summary_with_sources' && <SynthesisWithSources ... />}
      {turn.displayMode === 'parallel_results' && <ParallelResults ... />}
      {turn.displayMode === 'awaiting_input' && <ClarificationPrompt ... />}
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

### Performance Consideration

The hook subscribes to the full `entities` object — any entity mutation (including streaming token appends) triggers `useMemo` recalculation. `buildTurnsIncremental` mitigates render cost (unchanged turns keep identity), but the **computation itself** runs on every store update.

For rooms with 50+ turns, this becomes expensive. V1 mitigation:

```typescript
// Option A: subscribe to a version counter instead of the full entities object
const version = useMessageStore(s => s.version)  // bumped on any mutation
const entities = useMessageStore.getState().entities  // read non-reactively inside useMemo

const next = useMemo(
  () => buildTurnsIncremental(prev.current, 
    useMessageStore.getState().entities,
    useMessageStore.getState().orderedIds,
    events),
  [version, events],  // re-run only when version bumps
)
```

```typescript
// Option B: throttle rebuilds during streaming (30fps cap)
// Use requestAnimationFrame or a 33ms debounce on the version signal
```

Both options preserve correctness while bounding computation frequency. Choose during implementation based on profiling. The message store already tracks mutations via source/version — extending it with a reactive counter is minimal.

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
- **Future (Open Question #5)**: If any source has artifacts, show `"· N deliverables"` in the trigger so users know files exist before expanding

---

## 10. Edge Cases

| Case | Handling |
|---|---|
| Summary exists but empty/failed | `deriveTurnDisplayMode` checks for `isSummaryAgent && status === 'completed' && content.trim().length > 0`. Empty/failed summary → fall back to `parallel_results`. |
| Single agent + supervisor enabled | Supervisor may DONE without synthesis for 1-agent turns → `single_agent`. |
| HITL Q&A during processing | Supervisor CLARIFY → `awaiting_input` mode with prominent question. Agent-level HITL (from `input_required`) shows in `LiveActivityFeed` for that specific agent. `HitlResponseBar` in composer dock handles reply (unchanged). |
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

## 10.2. `clientRequestId` Routing (Defensive Hardening)

`buildTurns`'s `routeAgentToTurn` currently uses: `relatedMessageId` → positional fallback. The production `route-agent.ts` adds a middle tier: `clientRequestId` correlation.

**Current risk level: Low**. The backend always sets `related_message_id` on `task_submitted` and `task_update` events (verified in `sse_services.py` and `room_message_center.py`). The `agent_response` SSE type no longer delivers regular agent results — `task_update` handles that now. `agent_response` is only used for coordinator summaries (which pass `related_message_id`). Positional fallback handles the optimistic window correctly because `pending-turn-buffer` ensures entities only enter the store after their user message.

**Recommendation**: Add `clientRequestId` as a fallback tier (between `relatedMessageId` and positional) as defensive hardening. Cost: ~5 lines. Benefit: prevents mis-routing if the backend ever regresses or a new agent type skips `task_submitted`.

```typescript
// In routeAgentToTurn (build-turns.ts)
// After relatedMessageId check fails:
if (agent.clientRequestId) {
  for (let i = 0; i < scaffolds.length; i++) {
    const userEntity = entities[scaffolds[i].userMessageId ?? '']
    if (userEntity?.clientRequestId === agent.clientRequestId) return i
  }
}
// Then fall through to positional
```

---

## 11. Migration Plan

### Phase 1: Extend `buildTurns` (no rendering changes)

1. Add ephemeral handling to `buildAgentResult`
2. Add `displayMode` field to `TurnViewModel` and compute it in `assembleTurn`
3. Add `isEphemeral` flag to `AgentResultViewModel`
4. Implement ephemeral suppression in `assembleTurn` (port from `selectConversationTurns`)
5. Add `clientRequestId` fallback to `routeAgentToTurn` (defensive — see Section 10.2)
6. Add comprehensive tests for all display mode derivations (vitest infrastructure exists: `tests/unit/lib/build-turns.test.ts`)
7. Create `useTurnViewModels` hook

### Phase 2: Build new rendering components

8. Create `TurnRenderer` dispatching on `displayMode`
9. Create `SynthesisContent` (wraps `MarkdownContent` + `ArtifactList`)
10. Create `CollapsedSources` (Radix `Collapsible` + compact `AgentCard` list)
11. Create `AgentResultContent` (with streaming overlay)
12. Create `ParallelResults` and `LiveActivityFeed`
13. Create `ClarificationPrompt` (HITL question + partial progress for `awaiting_input`)
14. Wire `AgentCard` click → existing `onOpenAgentDetail` → existing `AgentResponseDetailPane`

### Phase 3: Swap rendering pipeline

15. In `ConversationMessageList`, replace `useConversationTurnViews` with `useTurnViewModels`
16. Replace `ConversationTurn` with `TurnRenderer`
17. Verify all modes render correctly (single, summary, parallel, awaiting_input, working)
18. Verify streaming, HITL, detail pane, scroll-to-bottom all still work

### Phase 4: Delete old pipeline

19. Delete `src/lib/selectors/select-conversation-turns.ts`
20. Delete `src/lib/selectors/route-agent.ts`
21. Delete `src/hooks/useConversationTurnViews.ts`
22. Delete `src/components/conversation/ConversationTurn.tsx` (old block dispatcher)
23. Delete `src/components/conversation/UnresolvedAgentGroup.tsx`
24. Keep: `conversation-types.ts` (themes, shared types), `map-agent-display.ts`, `select-hitl.ts`, `select-agent-response-detail.ts`, `select-composer-state.ts`

### Phase 5: Backend display contract

The frontend currently derives display mode entirely from entity state. This works for V1 but becomes fragile as orchestration patterns multiply. Phase 5 shifts ownership: the backend declares display intent, the frontend renders it.

25. Add `TurnCompletionHint` to the terminal `processing_status` SSE event:

```typescript
interface TurnCompletionHint {
  display_mode: 'summary' | 'parallel' | 'single' | 'clarify'
  primary_message_id?: string   // the "answer" — summary agent's message_id
  source_message_ids?: string[] // contributing agent message_ids
  orchestration_mode?: 'supervisor' | 'queue' | 'direct'
}
```

26. Frontend persists hint on the turn (stored in `TurnViewModel.backendHint`)
27. `deriveTurnDisplayMode` uses `backendHint.display_mode` when present, falls back to entity-derived heuristic for old turns without hints
28. Backend populates hint from supervisor trajectory / coordinator decision

**Why not optional**: Every new orchestration mode (iterative refinement, agent voting, multi-turn chains) would otherwise require new frontend heuristics. The backend already knows the orchestration intent from the trajectory — surfacing it is a one-time cost that prevents ongoing frontend complexity growth.

---

## 12. Files Affected

### New files
- `src/hooks/useTurnViewModels.ts`
- `src/components/conversation/TurnRenderer.tsx`
- `src/components/conversation/SynthesisContent.tsx`
- `src/components/conversation/CollapsedSources.tsx`
- `src/components/conversation/AgentResultContent.tsx`
- `src/components/conversation/ParallelResults.tsx`
- `src/components/conversation/ClarificationPrompt.tsx`
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
5. **Artifact surfacing in collapsed sources**: When source agents produce artifacts (Excel files, images, PDFs) that get hidden behind "N agents contributed," users lose access to deliverables. Should `CollapsedSources` show a "Produced N artifacts" indicator? Or should artifacts from source agents be auto-extracted into the synthesis section as a "Deliverables" row? (Critical for real-world workflows — generating reports, files, images)
6. **Synthesis quality verification**: After sources collapse, users can't easily compare the synthesis against raw agent outputs. Should `summary_with_sources` include an explicit "Verify / Compare all results" affordance beyond the generic "N agents contributed" trigger? Power users in enterprise workflows need to validate agent work, not just consume summaries.
7. **Hub vs Cloud agent differentiation**: Hub agents run on the user's personal device — their failure means something different (device offline vs cloud error), and their latency characteristics differ. Should `LiveActivityFeed` show hub/cloud badges? Should the UI handle "hub agent timeout due to device disconnect" differently than cloud agent failure?
8. **`pendingAgents` shimmer rows in working mode**: The V2 timeline plan (April 10) introduced `pendingAgents` as a component-layer prop for showing expected agents that haven't started yet. When the supervisor delegates to 3 agents and only 1 has sent `task_submitted`, `LiveActivityFeed` could show placeholder rows for the other 2. Should `LiveActivityFeed` accept an optional `pendingAgents` prop from the room's agent list?

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
| `awaiting_input` as distinct display mode | Supervisor CLARIFY is a key differentiator — showing nothing during clarification breaks the intelligent workflow UX |
| Backend display contract in Phase 5 (not optional) | Frontend heuristic derivation becomes fragile as orchestration patterns multiply; backend should declare intent |
