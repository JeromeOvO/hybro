# Final Answer First — Chat UX Redesign

**Date**: 2026-05-23 (updated 2026-05-24)
**Status**: V1 implemented (Phases 1–4) · V2 superseded by V3 · **V3 implemented (unified final answer)**
**Scope**: Redesign conversation rendering so multi-agent turns show only the final answer by default, with intermediate agent activity collapsible on demand. Single pipeline on `TurnViewModel`. **V3 unifies all turn outcomes behind one `FinalAnswer` model (LLM synthesis, deterministic digest, HITL, single-agent).**

---

## 1. Problem Statement

### 1.1 Completed-turn noise (V1 — addressed)

Today, multi-agent supervisor turns render all agent responses (individual + synthesis) at the same visual level. Users see every intermediate step and the final summary interleaved, creating noise. The desired experience (like Cursor's approach): show the final optimal result prominently, hide intermediate multi-agent conversation/debate behind an expandable section.

### 1.2 Live-turn scroll disruption (V2 — open)

V1 fixed the **completed** turn layout but not the **working** experience. While agents run, the viewport jumps:

1. **`LiveActivityFeed` stacks full agent cards** — each agent gets `AgentCard` + growing `AgentContentBlock` in normal document flow. Every new agent and every streaming token increases `scrollHeight`.
2. **Global auto-scroll on store mutations** — `ConversationMessageList` scrolls on `messageStore.version` + `scrollHeight` change when near bottom. Token streaming uses `streaming-store` separately and does not bump message store version.
3. **Hard mode swaps remount the turn body** — `TurnRenderer` switches entire subtrees per `displayMode`, including `summary_with_sources` while still live.
4. **Completion boundary** — switching to V1 layout when `status → completed` remounts content and expands full `ParallelResults` on DONE path.

Sticky user messages (`conversation-user-sticky`) help context but do not stop the answer area from growing unpredictably. V2 targets a **stable primary output slot + bounded activity strip**, with scroll anchored to the primary stream only.

## 2. Goals

### V1 (implemented)

- **Final answer first**: synthesis/summary content is the primary visible output of a multi-agent turn
- **Details on demand**: individual agent responses are collapsed into a "N agents contributed" section the user can expand
- **One rendering pipeline**: consolidate onto `TurnViewModel` (`build-turns.ts`), eliminating the parallel `selectConversationTurns` path
- **Mode-aware display**: backend orchestration mode (supervisor/coordinator/single) determines what "final answer" means

### V2 (superseded by V3)

> V2's goals were absorbed into V3's unified architecture. The intermediate V2 approach (primary surface + activity strip + focus stream) was never fully shipped. V3 achieved scroll stability through `usePrimaryStreamScroll` and the simpler `FinalAnswerSurface` + `AgentIndex` layout.

- ~~Stable viewport during work~~: achieved via `usePrimaryStreamScroll` (follows primary stream only)
- ~~Primary surface + activity strip~~: replaced by `FinalAnswerSurface` + `AgentIndex`
- ~~Scroll follows the answer~~: achieved via `primaryStreamMessageId` + `ResizeObserver`
- ~~Morphing transitions~~: achieved via stable `TurnBody` shell with `key={turn.id}`

### V3 (implemented — §17)

- **One final-answer contract** for every turn: `FinalAnswer.kind` drives rendering
- **Tiered generation**: LLM synthesis when appropriate; deterministic agent digest on supervisor DONE; no extra LLM for token savings
- **HITL preempts final answer**: blocked turns show the question, not synthesis or digest
- **One shell**: primary → agent index; strip always opens panel; no parallel-only branches

## 3. Non-Goals

- Backend API or SSE protocol changes (**V1** — frontend derives from existing entity state; **Phase 5** adds `TurnCompletionHint` on terminal events)
- Motion/FLIP animation polish (deferred; V2 uses layout stability via min-height and shell reuse, not animated transitions)
- Activity timeline expansion panel with per-step event log (deferred — `TurnViewModel.events` is ready when needed)
- Changing the composer, HITL response bar, or agent detail pane

---

## 4. Architecture Decision

### Consolidate on `TurnViewModel` (delete `selectConversationTurns`)

| Criterion | `selectConversationTurns` (removed) | `TurnViewModel` / `build-turns.ts` |
|---|---|---|
| Summary detection | None — treats all agents equally | `selectSummary()`, `isSummaryAgent` built in |
| Supervisor awareness | None | `isSupervisorTurn`, `supervisorStage` |
| Agent result model | Flat `ConversationBlock[]` | `AgentResultViewModel` with status, HITL, duration |
| Collapse/expand support | None | `isHiddenInCompact` on events |
| Streaming buffers | Merged at selector level | Component layer via `useStreamingStore` |
| Ephemeral placeholders | Handled inline | `suppressEphemeralResults()` with synthesis-gap rules |
| Incremental rebuild | External `turnsEqual` in hook | Built-in `buildTurnsIncremental` |
| Production usage | ~~Was rendering~~ Deleted Phase 4 | **Active** — `ConversationMessageList` |

**Decision**: Extend `TurnViewModel` with ephemeral handling, build new rendering components, then remove `selectConversationTurns`. **Status: implemented (V1).**

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

Display mode is now a **derived property** of `FinalAnswer.kind`, computed via `deriveDisplayModeFromFinalAnswer()`:

```typescript
function deriveDisplayModeFromFinalAnswer(
  turn: TurnViewModel,
  finalAnswer: FinalAnswerViewModel,
): TurnDisplayMode {
  const realCount = getStripSourceResults(turn).length

  switch (finalAnswer.kind) {
    case 'hitl':
      return 'awaiting_input'
    case 'llm_synthesis':
    case 'deterministic_done':
      return realCount >= 2 ? 'summary_with_sources' : 'single_agent'
    case 'single':
      return 'single_agent'
    case 'pending':
      return turn.status === 'active' ? 'working' : 'parallel_results'
    default:
      return 'working'
  }
}
```

The primary derivation logic lives in `deriveFinalAnswer()` (see §17.5). `displayMode` is kept for incremental migration and CSS hooks but no longer drives rendering dispatch — `FinalAnswer.kind` does.

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
- Source agents in a `Collapsible` (Radix) — **collapsed by default on completed/historical turns**; **expanded on the active (last) turn while `turn.status !== 'completed'`** (see Collapse Behavior, §7)
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

### `working` (V1 — current)

```
┌─────────────────────────────────────────────┐
│ [shimmer] Agent Name — Working              │
│   └ Working on your request...              │
│ ─────────────────────────────────────────── │
│ [avatar] Other Agent — Streaming            │
│ <Streaming content with cursor>             │
└─────────────────────────────────────────────┘
```

V1 renders `LiveActivityFeed`: full `AgentResultContent` per agent (card + streaming body) in document flow. When the turn completes, display mode re-derives and the subtree swaps to `summary_with_sources`, `parallel_results`, or `single_agent`.

**Known limitation (drives V2):** This transcript-style layout causes viewport jump during multi-agent runs. See §16.

**Optional enhancement (Open Question #8):** `pendingAgents` shimmer rows for delegated agents that have not yet sent `task_submitted`.

### `awaiting_input`

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

**V2 note:** CLARIFY should use `TurnPrimarySurface` for the question; partial agent progress moves to `TurnActivityStrip` (compact rows), not full cards.

## 7. Transition: Working → Complete

The display mode re-derives on every store update. The transition sequence:

1. **Agents working** → `displayMode = 'working'` → `LiveActivityFeed` with full agent cards
2. **All agents complete, synthesis decision pending** → ephemeral placeholder updates to "Synthesizing responses..." → `turn.status` stays `'active'` via `inSynthesisGap` in `deriveTurnStatus`; `displayMode` stays `'working'` until summary entity appears
3. **Summary `task_submitted` arrives** → summary entity appears with `status: 'working'` → `displayMode` flips to `'summary_with_sources'` (`hasSynthesisInProgress`). `SynthesisWithSources` replaces `LiveActivityFeed` (V1 remount — see V2 §16).
4. **Summary streaming begins** → streaming buffer fills → `SynthesisContent` renders live content via `useStreamingStore`
5. **Summary completes** → entity `status` becomes `'completed'`, content persisted → same `summary_with_sources` mode, now fully stable

**V1 remount gap:** Step 3 swaps `LiveActivityFeed` for `SynthesisWithSources` via `TurnRenderer`'s display-mode dispatch. V2 keeps a shared `TurnPrimarySurface` shell so synthesis streams in the same slot without subtree replacement.

**Critical dependency — no flash between modes**: The gap between "last agent completes" and "summary `task_submitted` arrives" is bridged by the ephemeral placeholder. The backend sends `processing_status(details="Synthesizing responses...")` immediately after deciding to synthesize. Ephemeral suppression (§8) keeps synthesis-gap ephemerals visible when `allRealAgentsTerminal && !hasWorkingAgent`, maintaining `turn.status = 'active'` and preventing a flash to `parallel_results`.

Without correct ephemeral handling, the user would see: `working` → `parallel_results` (1–3s while supervisor decides) → `summary_with_sources`.

**Stuck-spinner class of bugs (fixed in V1):** When supervisor chooses DONE (no synthesis), a `"Planning next action..."` ephemeral could survive after all agents completed, forcing `displayMode = 'working'` until refresh. Fixed by: (1) suppressing non-synthesis ephemerals when `allRealAgentsTerminal`, (2) ignoring ephemerals in `deriveTurnStatus` terminal detection, (3) skipping PROCESSING placeholder upsert when `userEntity.turnTerminalStatus` is set, (4) `pruneStaleProcessingPlaceholder()` after DB hydration when no active non-ephemeral tasks remain.

### Collapse Behavior

- **Last turn on page load (completed)**: `AgentIndex` starts **expanded** — full agent bodies for `deterministic_done`, source strips for `llm_synthesis`/`hitl` (`defaultAgentIndexOpen(turn, isLastTurn)`).
- **Active last turn (still working)**: index starts expanded while collecting/synthesizing.
- **Older turns**: sources start **collapsed**.
- **User manually collapsed/expanded**: respect React local state for the component lifetime. No localStorage in V1.
- **Next turn starts**: when `isLastTurn` becomes false, `AgentIndex` collapses via `useEffect`.
- **Hydration inference**: `stampInferredTurnTerminalStatus` runs after DB hydrate only; skips turns whose `userMessageId` appears in `room.active_runs` (reload mid-synthesis). Supervisor DONE (2+ agents) persists a deterministic summary entity with `summary_origin: "deterministic"` so reload does not depend on inference alone.

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

### Ephemeral Handling (implemented)

`buildAgentResult` emits synthetic `working` results for ephemeral placeholders (`isEphemeral: true`). `suppressEphemeralResults()` in `assembleTurn` applies these rules (in order):

1. **Terminal turn** — if `userEntity.turnTerminalStatus` is `completed` | `failed` | `canceled`, drop all ephemerals.
2. **DONE path** — if all real (non-ephemeral, non-summary) agents are terminal and the ephemeral is **not** a synthesis-gap placeholder, suppress it. This removes stale `"Planning next action..."` rows after supervisor chooses DONE.
3. **Missing `clientRequestId`** — if any real agent exists and the ephemeral is not a synthesis-gap placeholder, suppress it.
4. **Same `clientRequestId`** — if a real agent shares the turn's `clientRequestId`, suppress the ephemeral **except** when it is a synthesis-gap ephemeral and no real agent is still `working` (bridges the gap before summary `task_submitted`).

**Synthesis-gap ephemeral** (`isSynthesisGapEphemeral`): summary agent with `status === 'working'`, OR `taskStatusMessage` containing `"synthesiz"` (case-insensitive).

**Defensive layers outside `build-turns`:**

| Layer | Behavior |
|---|---|
| SSE PROCESSING handler | Skip placeholder upsert when `userEntity.turnTerminalStatus` is already terminal |
| SSE PROCESSING upsert | Pass `clientRequestId` on placeholder so suppression can correlate |
| `useRoomHydration` | `pruneStaleProcessingPlaceholder()` after reconcile when no active non-ephemeral tasks |

`deriveTurnStatus` ignores ephemerals for substantive status (`inSynthesisGap` keeps turn `active` during synthesis decision window).

### V2 additions (planned)

```typescript
interface TurnViewModel {
  // ... existing fields ...

  phase?: 'collecting' | 'synthesizing' | 'answering' | 'completed'
  /** Message whose stream renders in TurnPrimarySurface (may change mid-turn). */
  primaryStreamMessageId?: string
  /** Scroll-follow target; usually equals primaryStreamMessageId. */
  primaryMessageId?: string
}
```

Derived in `assembleTurn` via `deriveTurnPhase()` and `derivePrimaryStreamMessageId()` — see §16.8. Phase 5 `backendHint.primary_message_id` updates content only; shell `key={turn.id}` stays stable.

---

## 9. Component Architecture

### Current components (V3 — implemented)

```
src/components/conversation/
├── TurnRenderer.tsx           — user message + TurnBody dispatch
├── TurnBody.tsx               — FinalAnswerSurface + AgentIndex layout
├── FinalAnswerSurface.tsx     — unified primary slot (switches on finalAnswer.kind)
├── AgentIndex.tsx             — collapsible agent list (compact rows or full bodies)
├── SynthesisContent.tsx       — summary body + artifacts + streaming
├── AgentResultContent.tsx     — one agent result (card + content + streaming)
├── AgentCard.tsx              — agent header with status/theme/animation
├── AgentContentBlock.tsx      — markdown body + artifacts rendering
├── UserMessageBlock.tsx       — user message display
├── UserAnswerCard.tsx         — resolved HITL Q&A card
└── AgentResponseDetailPane.tsx — side panel for agent detail view
```

**Deleted V1 components** (replaced by V3 equivalents):
- `SynthesisWithSources.tsx` → `FinalAnswerSurface` (llm_synthesis case)
- `CollapsedSources.tsx` → `AgentIndex`
- `ParallelResults.tsx` → `AgentIndex` with `deterministic_done` (full bodies when expanded)
- `ClarificationPrompt.tsx` → `FinalAnswerSurface` (hitl case) + `AgentIndex`
- `LiveActivityFeed.tsx` → `FinalAnswerSurface` (pending case) + `AgentIndex`
- `ConversationTurn.tsx` → `TurnRenderer`
- `UnresolvedAgentGroup.tsx` → removed

**V2 components (never built — superseded by V3):**
- `TurnPrimarySurface.tsx` → replaced by `FinalAnswerSurface`
- `TurnActivityStrip.tsx` → replaced by `AgentIndex`

### Hook (implemented)

```typescript
// src/hooks/useTurnViewModels.ts
export function useTurnViewModels(roomId: string): TurnViewModel[] {
  const version = useMessageStore(s => s.version)
  const prev = useRef<TurnViewModel[]>([])

  const next = useMemo(() => {
    const { entities, orderedIds } = useMessageStore.getState()
    const roomOrderedIds = filterRoomMessages(roomId, entities, orderedIds)
    return buildTurnsIncremental(prev.current, entities, roomOrderedIds, EMPTY_EVENTS)
  }, [roomId, version])

  prev.current = next
  return next
}
```

`buildTurnsIncremental` preserves referential identity per unchanged turn. Room-scoped filtering prevents cross-room entity bleed.

### Performance (remaining)

Option B (throttle rebuilds to ~30fps during streaming) is deferred. Profile with 50+ turn rooms before implementing.

### Streaming at Component Level

Components that render agent content subscribe to streaming buffers directly:

```typescript
function AgentResultContent({ result }: { result: AgentResultViewModel }) {
  const buffer = useStreamingStore(s => s.buffers[result.messageId])
  const content = buffer?.text ?? result.content
  const isStreaming = buffer ? !buffer.isComplete : result.status === 'working'
  const artifacts = buffer?.artifacts ?? result.artifacts
  // ... render AgentCard + AgentContentBlock
}
```

`SynthesisContent` uses the same pattern for the summary agent. `FinalAnswerSurface` dispatches on `finalAnswer.kind` to choose which content component to render.

### AgentIndex Interaction

```typescript
interface AgentIndexProps {
  turn: TurnViewModel
  sourceResults: AgentResultViewModel[]
  selectedAgentMessageId?: string
  onOpenDetail: (messageId: string) => void
}
```

Behavior:
- Hidden for `kind === 'single'` (one agent, no index needed)
- If `selectedAgentMessageId` matches a source result, force-expand (user is viewing a source in the detail pane)
- For `deterministic_done`: shows full `AgentResultContent` per agent when expanded
- For all other kinds: shows compact `AgentCard` rows (click → panel)
- Trigger text: contextual label based on `FinalAnswer.kind` (see `getAgentIndexSummary`)
- Default state: expanded during live turns, collapsed for completed/historical turns

---

## 10. Edge Cases

| Case | Handling |
|---|---|
| Summary exists but empty/failed | `deriveTurnDisplayMode` checks for `isSummaryAgent && status === 'completed' && content.trim().length > 0`. Empty/failed summary → fall back to `parallel_results`. |
| Single agent + supervisor enabled | Supervisor may DONE without synthesis for 1-agent turns → `single_agent`. |
| HITL Q&A during processing | Supervisor CLARIFY → `awaiting_input` mode with prominent question. Agent-level HITL (from `input_required`) shows in `LiveActivityFeed` for that specific agent. `HitlResponseBar` in composer dock handles reply (unchanged). |
| Budget-exhausted forced synthesis | Treated as normal `summary_with_sources`. |
| Cancellation mid-processing | `turn.status` → `'failed'` or `'partial'`; `displayMode` derives from remaining agent results (typically `parallel_results` if multiple agents finished). |
| Stuck "Planning..." spinner after DONE | Suppress non-synthesis ephemerals when all real agents terminal; skip PROCESSING upsert when `turnTerminalStatus` set; prune placeholder on hydration. See §7. |
| Page reload / DB hydration | Mode derived from persisted entity state. No transition animations on hydration. Collapsible starts **collapsed** for historical turns. |
| SSE reconnection mid-turn | Existing `reconcileWithDb` fires. Entities update → `buildTurnsIncremental` re-derives. |
| Ephemeral suppression | See §8 — synthesis-gap exception, DONE-path Planning suppression, `turnTerminalStatus` gate, `clientRequestId` correlation. |
| Two summary agents (legacy compat) | `selectSummary` priority: `supervisor_synthesis` > `debate_summary` > `non_debate_summary` > `summary`. First match wins. |
| `selectSummary` fallback to non-system agent | Does NOT trigger `summary_with_sources`. `deriveTurnDisplayMode` checks `isSummaryAgent` on results, not `turn.summary` existence. |
| Detail pane open + sources collapse | If `AgentResponseDetailPane` is showing a source agent, the `CollapsedSources` component auto-expands (reads `selectedAgentMessageId` from room-ui-store). |
| Parallel results ordering | Ordered by agent message `timestamp` (arrival order from backend). Future: support `relevance_order` from supervisor trajectory. |
| Scroll jump during multi-agent work | V1 known issue — full `LiveActivityFeed` + `messageStore.version` scroll. V2 §16 (note: tokens use `streaming-store`, not message store). |
| User scrolled up during streaming | `userPausedRef` + `ScrollToBottomButton`; V2 adds `streaming-store` + `ResizeObserver` follow on primary stream only. |
| Parallel DONE at completion | V2 live shell uses focus stream + strip when `isParallelOnlyTurn` (§16.14); synthesis path keeps answer-first strip. |
| `displayMode` during active synthesis | `summary_with_sources` while `status === 'active'` — V2 uses `TurnBody` shell regardless (§16.4). |

---

## 10.1. Accessibility Requirements

`CollapsedSources` must meet WCAG 2.1 AA:
- Trigger uses `aria-expanded` (provided by Radix Collapsible)
- Trigger has accessible name: `"N agents contributed, expandable"` via `aria-label`
- Content panel linked via `aria-controls` (provided by Radix)
- Failed agents announced: `"Agent Name — Failed"` in aria-label on status badge
- Keyboard: Enter/Space toggles collapsible (Radix default)

---

## 10.2. `clientRequestId` Routing (implemented)

`routeAgentToTurn` in `build-turns.ts` uses three tiers: `relatedMessageId` → `clientRequestId` → positional fallback.

`src/lib/selectors/route-agent.ts` is **kept** — still used by `select-agent-response-detail.ts` for detail-pane routing. Do not delete in Phase 4.

---

## 11. Migration Plan

### Phases 1–4: V1 (complete)

| Phase | Status | Notes |
|---|---|---|
| 1 — Extend `buildTurns` | Done | Ephemeral handling, `displayMode`, `clientRequestId` routing, tests |
| 2 — Components | Done | `TurnRenderer`, `SynthesisWithSources`, `LiveActivityFeed`, etc. |
| 3 — Pipeline swap | Done | `ConversationMessageList` → `useTurnViewModels` + `TurnRenderer` |
| 4 — Delete old pipeline | Done | Removed `select-conversation-turns`, `useConversationTurnViews`, `ConversationTurn`, `UnresolvedAgentGroup`. **Kept** `route-agent.ts`. |

Post-V1 fixes (not in original plan): Planning ephemeral suppression, `turnTerminalStatus` SSE skip, `pruneStaleProcessingPlaceholder`.

### Phase 5: Backend display contract (planned)

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

### Phases 6–10: V2 stable viewport — SUPERSEDED

> These phases were superseded by V3 (§17). Scroll stability was achieved through `usePrimaryStreamScroll` + `FinalAnswerSurface` + `AgentIndex` with `overflow-anchor` management. See §16 for historical context.

---

## 12. Files Affected

### Current V3 components
- `src/components/conversation/TurnRenderer.tsx`
- `src/components/conversation/TurnBody.tsx`
- `src/components/conversation/FinalAnswerSurface.tsx`
- `src/components/conversation/AgentIndex.tsx`
- `src/components/conversation/SynthesisContent.tsx`
- `src/components/conversation/AgentResultContent.tsx`

### Data layer
- `src/lib/room-timeline/build-turns.ts` — turn construction, ephemeral handling, displayMode, phase, finalAnswer
- `src/lib/room-timeline/derive-final-answer.ts` — `deriveFinalAnswer()`, `deriveDisplayModeFromFinalAnswer()`, `derivePrimaryStreamFromFinalAnswer()`
- `src/lib/room-timeline/turn-live-shell.ts` — strip helpers, status line, source result filtering
- `src/lib/room-timeline/map-result-display.ts` — display props for AgentCard
- `src/lib/room-timeline/types.ts` — `TurnViewModel`, `FinalAnswerViewModel`, `FinalAnswerKind`, `TurnDisplayMode`, `TurnPhase`

### Hooks
- `src/hooks/useTurnViewModels.ts` — derives `TurnViewModel[]` from message store
- `src/hooks/usePrimaryStreamScroll.ts` — scroll-follow for primary stream

### Infrastructure (modified for V1/V3)
- `src/components/conversation/ConversationMessageList.tsx` — `useTurnViewModels` + `TurnRenderer`
- `src/hooks/room/sse-handlers/index.ts` — terminal turn placeholder skip, `clientRequestId` on upsert
- `src/hooks/room/useRoomHydration.ts` — `pruneStaleProcessingPlaceholder`

### Deleted files (V1 Phase 4 + V3)
- `src/lib/selectors/select-conversation-turns.ts`
- `src/hooks/useConversationTurnViews.ts`
- `src/components/conversation/ConversationTurn.tsx`
- `src/components/conversation/UnresolvedAgentGroup.tsx`
- `src/components/conversation/SynthesisWithSources.tsx`
- `src/components/conversation/CollapsedSources.tsx`
- `src/components/conversation/ParallelResults.tsx`
- `src/components/conversation/ClarificationPrompt.tsx`
- `src/components/conversation/LiveActivityFeed.tsx`

### Kept unchanged
- `src/components/conversation/AgentCard.tsx`
- `src/components/conversation/AgentContentBlock.tsx`
- `src/components/conversation/AgentResponseDetailPane.tsx`
- `src/components/conversation/UserMessageBlock.tsx`
- `src/components/conversation/UserAnswerCard.tsx`
- `src/components/conversation/ScrollToBottomButton.tsx`
- `src/lib/selectors/route-agent.ts` — still used by `select-agent-response-detail.ts`
- `src/components/conversation/conversation-tokens.css`
- `src/components/composer/*`
- `src/stores/message-store/*`
- `src/stores/streaming-store/*`
- `src/lib/system-agents.ts`

---

## 13. Testing Strategy

### V1 (implemented)
- **Unit tests for `buildTurns`**: display mode derivation, ephemeral suppression (Planning, synthesis gap, `turnTerminalStatus`), routing
- **E2E (Playwright)**: synthesis prominent, collapsed sources expandable, detail pane on source click

### V2 (planned)
- **Scroll stability**: measure unintended scroll delta during 3-agent turn (strip collapsed)
- **No primary remount**: verify `TurnPrimarySurface` same instance across `working` → `summary_with_sources`
- **Streaming-store scroll**: primary stream follow without `messageStore.version` bumps
- **Parallel DONE (no synthesis)**: focus stream + activity strip in live shell (§16.14)
- **Refactor `ClarificationPrompt`**: no full `ParallelResults` during CLARIFY

---

## 14. Open Questions

1. **Parallel results ordering**: timestamp order currently; backend step ordering deferred.
2. **Room-level config override**: `display_preference` for power users — deferred.
3. **Cross-turn source references**: deferred.
4. **Agent result ranking in parallel mode**: requires backend ranking — deferred.
5. **Artifact surfacing in AgentIndex**: show deliverable count in trigger — high priority for file-producing workflows.
6. **Synthesis quality verification**: explicit "Compare all results" affordance — deferred.
7. ~~**Hub vs Cloud agent differentiation**~~ — **Resolved:** `agentSource` on `AgentResultViewModel`; badge shown in `AgentCard`.
8. ~~**`pendingAgents` shimmer rows**~~ — **Deferred:** V3 uses `pending` shimmer in `FinalAnswerSurface` instead of per-agent shimmers.
9. **Follow-live toggle**: explicit user control vs implicit post-send follow — deferred.
10. ~~**Single-agent working**: hide activity strip; stream only in primary~~ — **Resolved:** `kind === 'single'` streams directly in `FinalAnswerSurface`; `AgentIndex` returns null.
11. ~~**Collecting-phase primary policy**~~ — **Resolved:** shimmer-only default (`kind: 'pending'`).
12. ~~**Agent-level HITL during multi-agent**~~ — **Resolved:** `kind: 'hitl'` preempts final answer; question shown in primary via `HitlPrimary`.

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
| Keep `route-agent.ts` after Phase 4 | Still required by `select-agent-response-detail.ts`; only inlined routing in `build-turns.ts` |
| `useTurnViewModels` uses version counter | Option A implemented — avoids subscribing to full `entities` object |
| Planning ephemeral suppressed on DONE | Prevents stuck HYBRO AI spinner when supervisor skips synthesis |
| Primary answer above AgentIndex (not below) | Answer-first pattern aligned with Cursor/Perplexity |
| `key={turn.id}` not `primaryMessageId` | Synthesis start changes stream message id; shell stays, content swaps |
| Streaming scroll uses `streaming-store` | Message store version does not bump per token; `usePrimaryStreamScroll` follows buffer updates |
| `deriveFinalAnswer` drives rendering | One decision table (`FinalAnswer.kind`) replaces mode-specific component branching |
| Deterministic DONE over parallel stack | HYBRO intro in primary, full bodies in collapsible AgentIndex |
| Shimmer-only during multi-agent collecting | Answer slot reserved for synthesis; avoids false "first agent = answer" hierarchy |
| HITL preempts final answer | Per-agent `hitlPending` or supervisor CLARIFY always shows question |
| Single streaming agent shows directly | `kind: 'single'` renders immediately with no shimmer delay |
| AgentIndex click always opens panel | One rule (no contextual routing); compare via panel |

---
## 16. Stable Viewport During Agent Work (V2) — SUPERSEDED

> **This entire section is historical context.** The V2 approach (focus stream, contextual strip routing, min-height watermark, `TurnPrimarySurface` + `TurnActivityStrip`) was **superseded by V3 (§17)** before implementation. V3 achieved stable viewport through simpler means:
>
> - `FinalAnswerSurface` as single stable primary slot (no mode-swap remounts)
> - `usePrimaryStreamScroll` follows `primaryStreamMessageId` only
> - `AgentIndex` with `overflow-anchor: none` prevents strip from shifting primary
> - `TurnBody` shell with `key={turn.id}` stays mounted through phase transitions
>
> The key V2 insights that carried forward into V3:
> - Primary surface + bounded activity strip (became `FinalAnswerSurface` + `AgentIndex`)
> - Scroll follows the primary stream tail, not all height changes
> - Shell persists through `working` → `completed` without remount
> - `streaming-store` subscription for scroll-follow (not `messageStore.version`)
>
> **Do not implement any §16 subsections.** They are retained only for design history.

<details>
<summary>§16 subsections (historical — click to expand)</summary>

### Original V2 problem statement

| Cause | Impact |
|---|---|
| `LiveActivityFeed` full cards in flow | Each new agent added large vertical blocks |
| `messageStore.version` scroll | Jumped on new agents (not per token — streaming used separate store) |
| `displayMode` mode swap | `working` → `summary_with_sources` remounted subtrees |
| Completion boundary | Full `ParallelResults` on DONE path exploded layout |

### V2 approaches (never built)

- §16.3: `TurnPrimarySurface` + `TurnActivityStrip` layout → became `FinalAnswerSurface` + `AgentIndex`
- §16.4: `useV2LiveShell` dispatch gate → replaced by V3's `TurnBody` always rendering `FinalAnswerSurface`
- §16.5: Shimmer-only collecting phase → implemented as `kind: 'pending'` in `FinalAnswerSurface`
- §16.7: `ResizeObserver` scroll anchoring → implemented in `usePrimaryStreamScroll`
- §16.8: `TurnPhase` + `primaryStreamMessageId` → implemented in `build-turns.ts`
- §16.9: Parallel DONE → replaced by `deterministic_done` in V3
- §16.14: Focus stream (parallel-only) → replaced by `deterministic_done` with full bodies in `AgentIndex`
- §16.15: Contextual strip routing → replaced by universal "click → panel" in `AgentIndex`
- §16.16: Min-height watermark → not needed (V3 doesn't swap agent bodies in primary)

</details>

---

## 17. Unified Final Answer (V3)

**Status:** Implemented (Phases 7A–7D complete). §16.14–16.16 never built (superseded before implementation).

### 17.1 Problem

V2 accumulated mode-specific branches:

- `parallel_results` vs `summary_with_sources` vs focus stream
- Strip click routes to focus **or** panel depending on mode
- Min-height watermark to fix layout shift from swapping agent bodies in primary
- `CollapsedSources` vs `TurnActivityStrip` — two similar components

Root cause: **supervisor DONE** emits no synthesis entity, so the frontend invented a parallel UI path. The fix is not more patches — it is **one final-answer model** with tiered *generation*, unified *rendering*.

### 17.2 Core principle

Every turn exposes exactly one **final answer slot** in the primary surface:

```text
[Sticky user message]
[FinalAnswer — kind drives content]
[AgentIndex — compact rows, always opens panel]
```

**Final answer** does not always mean LLM synthesis. It means “the thing the user should read first for this turn.”

### 17.3 Tiered generation (not always LLM)

| Generator | When | Tokens | Entity |
|---|---|---|---|
| **LLM synthesis** | Supervisor `SYNTHESIZE`; coordinator `summarize_agent_responses` (fast/sequential multi-agent); debate summary | Yes | `supervisor_synthesis`, `debate_summary`, `non_debate_summary`, … |
| **Deterministic DONE** | Supervisor **DONE**, 2+ agents, room terminal committed; coordinator summary **failed/empty** fallback | **No** | Persisted `supervisor_synthesis` with `summary_origin: "deterministic"`, or frontend virtual intro until entity arrives |
| **Single agent** | One substantive agent | No | Agent message itself |
| **HITL** | Unresolved clarify / `input_required` | No | Question, not an answer |
| **Pending** | Collecting or synthesis gap (supervisor has not committed DONE vs SYNTHESIZE) | No | Shimmer / status |

**Do not** always call LLM to compile the final answer. Supervisor DONE explicitly means individual responses are sufficient — a deterministic presenter intro satisfies UX consistency without token cost.

**Do not** replace coordinator LLM summary with deterministic DONE in fast multi-agent mode; deterministic DONE fills the **supervisor DONE gap** (and coordinator failure fallback).

**Answer-first DONE presentation:** HYBRO AI header + short deterministic intro in primary; full agent bodies live in collapsed **AgentIndex** (expand to read). Do **not** stack all agent markdown in primary — that causes flash and breaks final-answer-first rhythm.

### 17.4 `FinalAnswer` view model

**Status: Implemented** in `src/lib/room-timeline/types.ts`.

```typescript
type FinalAnswerKind =
  | 'pending'              // collecting / synthesis gap — shimmer
  | 'hitl'                 // blocked on user input
  | 'llm_synthesis'        // unified LLM answer (streams)
  | 'deterministic_done'   // HYBRO presenter intro; agent bodies in AgentIndex
  | 'single'               // one agent body

type SummaryOrigin = 'llm' | 'deterministic'

interface FinalAnswerSection {
  messageId: string
  agentId?: string
  agentName: string
  content: string
  artifacts: ArtifactData[]
  status: 'working' | 'completed' | 'failed' | 'awaiting_input'
}

interface FinalAnswerHitlPrompt {
  messageId: string
  agentName: string
  prompt: string
  resolved?: { prompt: string; answer: string }
}

interface FinalAnswerHitlViewModel {
  source: 'supervisor' | 'agent'
  prompts: FinalAnswerHitlPrompt[]
}

interface FinalAnswerViewModel {
  kind: FinalAnswerKind
  label: 'Synthesized' | 'Combined agent responses' | 'Working' | 'Needs input'
  primaryMessageId?: string
  deterministicIntro?: string
  summaryOrigin?: SummaryOrigin
  sections?: FinalAnswerSection[]
  hitl?: FinalAnswerHitlViewModel
}
```

> **Note:** The original design specified richer HITL fields (`promptType`, `choices`, `activePromptId`, `hitlId`). The current implementation uses the simplified model above. Rich HITL types can be added when the multi-HITL pager in the composer is implemented.

Renderer: `FinalAnswerSurface` dispatches on `kind` to render the appropriate content.

### 17.5 `deriveFinalAnswer()` — decision table

**Status: Implemented** in `src/lib/room-timeline/derive-final-answer.ts`.

Priority order (first match wins):

| # | Condition | `kind` |
|---|---|---|
| 1 | Unresolved HITL on turn (`hitlPending` or `turn.status === 'awaiting_input'`) | `hitl` |
| 2 | `isSummaryAgent` with `summary_origin: "deterministic"` | `deterministic_done` |
| 3 | `isSummaryAgent` working or has LLM content | `llm_synthesis` |
| 4 | Synthesis gap active (2+ real agents terminal, no summary, room not terminal) | `pending` |
| 5 | Exactly 1 real agent (any status — working or completed) | `single` |
| 6 | Multiple agents still `working` | `pending` |
| 7 | 2+ agents terminal, no LLM summary entity | `deterministic_done` |

**Key behavior:** Single-agent turns return `kind: 'single'` immediately when the agent entity appears, even while streaming. This ensures the UI renders the agent's content directly without showing a shimmer delay. The `pending` check for "working" agents only applies to multi-agent turns (rule 6).

**Critical:** Do not show `deterministic_done` while all agents are done but supervisor has not yet committed DONE vs SYNTHESIZE (synthesis gap) or before `turnTerminalStatus` is set on the user entity. Showing full agent bodies in primary then replacing with synthesis causes a flash. Stay on `pending` (HYBRO shimmer) until committed.

**Edge cases:**

- Supervisor `synthesis_text` emitted but &lt; 2 trajectory responses → backend skips summary entity → `single` (not deterministic_done).
- `selectSummary()` must continue to ignore `supervisor_hitl` / clarify text.
- Partial turn (some failed): AgentIndex includes failed agents with error status.
- Debate mode: prefer `llm_synthesis` when `debate_summary` exists; flat deterministic_done is not a substitute for debate structure.
- Optional compare mode: `agent_digest` inline section stack only when backend sets explicit `presentation: "inline_sections"` (future).

### 17.6 Deterministic DONE — answer-first presentation

When `kind === 'deterministic_done'`, render **HYBRO AI header + short intro** in primary; agent bodies live in **AgentIndex** (collapsed by default):

```text
[HYBRO AI · Combined agent responses]
2 agents responded. Expand below to read each answer.

[AgentIndex — collapsed]
  Agent A — full body when expanded
  Agent B — full body when expanded
```

Rules:

- **HYBRO header:** Always show HYBRO AI card on deterministic_done — orchestrator/presenter voice, **not** LLM author voice. Status label: `Combined agent responses`, not `Synthesizing`.
- **Primary body:** Short deterministic intro only (virtual frontend copy or backend entity content). No agent markdown in primary.
- **AgentIndex:** Full `AgentResultContent` per agent when expanded; collapsed by default on terminal turns.
- **Order:** delegation order from turn scaffold (`agentMessageIds`), not completion order.
- **Gap behavior:** While supervisor outcome is ambiguous, stay on `pending` shimmer — never mount deterministic_done early.

Optional backend: on supervisor DONE, emit persisted message with `summary_origin: "deterministic"` so live, refresh, and export share one entity (recommended over frontend-only virtual intro).

### 17.6.1 Agent digest (deprecated default)

`agent_digest` inline section stack in primary is **deprecated** as the default DONE path. Retain only for explicit compare-mode opt-in (`presentation: "inline_sections"`). Do not use for standard multi-agent finishes.

### 17.7 Persistence and backend contract

| Approach | Pros | Cons |
|---|---|---|
| Frontend-only virtual digest | No backend change | Refresh/export/copy inconsistent; no `messageId` for streaming-store |
| Backend emit on DONE | Same entity model as synthesis; SSE parity | Small backend change |

**Recommended:** extend `_emit_unified_summary` or add `_emit_deterministic_digest` on supervisor DONE (no LLM call). Set `extend_info.summary_origin = "deterministic"`.

**Phase 5 hint (optional):** `turn_completed` payload `{ "final_answer_kind": "digest" | "synthesis" }` so frontend can show digest immediately without flashing before synthesis on ambiguous gaps.

### 17.8 HITL handling

**HITL preempts final answer.** While blocked, `kind === 'hitl'` — never `llm_synthesis` or `agent_digest`.

Two sources, one UX:

| Source | Signal | Origin |
|---|---|---|
| Supervisor **CLARIFY** | `turn.status === 'awaiting_input'`, `supervisor_clarify` entity | `source: 'supervisor'` |
| Agent **`input_required`** | Agent `hitlPending`, `hitlRequestId` on entity | `source: 'agent'` |

**Surface split (unchanged from §16.10, composer not in scope):**

| Surface | Role |
|---|---|
| **Primary** | Read-only context — amber "Needs Input" card with prompt(s) |
| **Composer (`HitlResponseBar`)** | **Only** input path — text / choice / confirmation; multi-HITL pager |
| **AgentIndex** | Progress — completed agents; highlight awaiting row; click → panel |
| **Normal chat** | Disabled in `hitl_responding` mode |

Do **not** put submit forms in primary. Do **not** show digest or synthesis while HITL unresolved.

**After resolve:** show `UserAnswerCard` (question + answer) inline; turn resumes (`pending`) then eventual `llm_synthesis` or `deterministic_done`.

**Multi-HITL groups:** composer pager is source of truth for active question; primary shows current prompt (sync with pager) or short "N questions pending" header.

**ClarificationPrompt replaced:** `AgentIndex` shows completed agents; click opens panel for "progress so far" (final-answer-first).

**Current implementation** (simplified from original design — no `promptType`/`choices`/`activePromptId` yet):

```typescript
interface FinalAnswerHitlPrompt {
  messageId: string
  agentName: string
  prompt: string
  resolved?: { prompt: string; answer: string }
}

interface FinalAnswerHitlViewModel {
  source: 'supervisor' | 'agent'
  prompts: FinalAnswerHitlPrompt[]
}
```

> **Future:** When multi-HITL pager and choice-type prompts are implemented, extend `FinalAnswerHitlPrompt` with `promptType`, `choices`, `hitlId`, and add `activePromptId` to the view model.

### 17.9 Unified shell layout

Same layout for **all** multi-agent live turns:

```text
[User message — sticky]
[FinalAnswerSurface]
[AgentIndex — one component, replaces CollapsedSources + TurnActivityStrip]
```

| `FinalAnswer.kind` | Primary | AgentIndex label | Index click / content |
|---|---|---|---|
| `pending` | HYBRO shimmer / status | Activity · N agents | compact rows → panel |
| `hitl` | Question card(s) | Completed / Activity | compact rows → panel |
| `llm_synthesis` | HYBRO stream | Sources · N contributed | compact rows → panel |
| `deterministic_done` | HYBRO header + short intro | Agent responses · N | **full bodies when expanded** → panel |
| `single` | Agent body | Hidden or single row | → panel optional |

**Strip click:** always `openAgentDetail(messageId)` — one rule, no focus stream, no contextual routing.

**Deterministic DONE + index:** primary does **not** duplicate agent bodies. AgentIndex holds full `AgentResultContent` when expanded; collapsed by default on terminal turns.

**Layout order:** primary → index (same as synthesis today). No strip-above-primary; no min-height watermark.

### 17.10 Scroll and live behavior

| Kind | Scroll-follow target |
|---|---|
| `llm_synthesis` | Summary agent stream (`usePrimaryStreamScroll`) |
| `deterministic_done` | HYBRO intro block only (no agent markdown in primary) |
| `single` | Agent message stream |
| `pending` / `hitl` | Status line only; no agent markdown scroll |

### 17.11 Token economics summary

| Path | Extra LLM compile? |
|---|---|
| Supervisor SYNTHESIZE | Yes (intended) |
| Coordinator multi-agent (fast mode) | Yes (already today) |
| Supervisor DONE → deterministic_done | **No** |
| Optional user "Synthesize this turn" (future) | Yes (explicit opt-in) |

### 17.12 Migration — delete or deprecate

Remove after §17 ships:

- `isParallelOnlyTurn()`, `deriveParallelFocusMessageId()`
- Parallel-only focus stream in `TurnPrimarySurface` / `TurnBody`
- Min-height watermark (`ResizeObserver` in primary)
- Contextual strip routing (`onFocusAgent` vs `onOpenDetail` split)
- Live `parallel_results` path in `TurnBody` / `TurnRenderer`
- Duplicate `CollapsedSources` + `TurnActivityStrip` → single `AgentIndex`

Keep:

- `AgentResponseDetailPane` (panel compare)
- `HitlResponseBar` (composer)
- History `ParallelResults` optional for archived turns, or unify on `FinalAnswerSurface` for all turns

### 17.13 Rollout phases

| Phase | Scope | Status |
|---|---|---|
| **7A** | `deriveFinalAnswer()` + `FinalAnswerViewModel` in `build-turns.ts`; unit tests for decision table | Done |
| **7B** | `FinalAnswerSurface` + `AgentIndex`; wire live shell (`TurnBody`) | Done |
| **7C** | `deterministic_done` answer-first shell; synthesis-gap race fix; frontend virtual intro | Done |
| **7D** | HITL `kind: 'hitl'` integration; `ClarificationPrompt` retired | Done |
| **7E** | §16.14–16.16 code paths never built; `turn_completed` hint deferred to Phase 5 | N/A |

### 17.14 Success metrics

- Zero `displayMode === 'parallel_results'` in live last turn (all multi-agent live → `summary_with_sources` equivalent via `FinalAnswer`).
- One strip click rule — no mode-specific handler table in components.
- No `ResizeObserver` min-height watermark.
- HITL turns never render digest or synthesis until resolved.
- DONE multi-agent turns show content without extra LLM latency or token cost.
- Strip + panel: user can compare agents without duplicate primary+panel bodies on default click.
