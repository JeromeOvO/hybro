# Room Conversation Timeline V2 — Cursor-Style Visual Redesign

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the conversation turn rendering layer to match Cursor/TUI workflow aesthetics — inline blocks with avatars, shimmer placeholder rows, fixed HITL status, supervisor header, inline chips replacing the event rail.

**Architecture:** Extend existing `TurnViewModel` / `AgentResultViewModel` types with new fields (`isSupervisorTurn`, `supervisorStage`, `'working'` status, `isSummaryAgent`, HITL split, inline chip data). Add 5 new leaf components. Rewrite `agent-result-card.tsx` for avatar + shimmer layout. Thread `roomAgentList` prop from page → timeline → turn for placeholder agent computation. No SSE handler or message store changes.

**Tech Stack:** Next.js 16, React 19, Tailwind CSS 4, Vitest, testing-library/react

**Spec:** `docs/superpowers/specs/2026-04-10-room-cursor-timeline-v2-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/lib/system-agents.ts` | Modify | Add `isSupervisorSystemAgent()` + `isSummarySystemAgent()` helpers |
| `src/lib/room-timeline/types.ts` | Modify | Extend TurnViewModel + AgentResultViewModel with new fields |
| `src/app/globals.css` | Modify | Add `shimmer-text-yellow` CSS class |
| `src/lib/room-timeline/build-turns.ts` | Modify | `'working'` status, `isSupervisorTurn`, `supervisorStage`, summary fix, HITL split, inline chip data, `turnsAreEqual` |
| `src/components/inline-chips.tsx` | Create | Step count + duration pills next to agent name |
| `src/components/hitl-compact-card.tsx` | Create | Resolved HITL: truncated question + emphasized answer |
| `src/components/hitl-question-card.tsx` | Create | Active HITL question with yellow border + shimmer |
| `src/components/agent-placeholder-row.tsx` | Create | Shimmer "Thinking" row with avatar + name |
| `src/components/supervisor-header.tsx` | Create | HYBRO AI icon + brand text + stage shimmer/stats |
| `src/components/agent-badge.tsx` | Modify | Avatar image, summary-family brand gradient treatment |
| `src/components/agent-result-card.tsx` | Modify | Major rewrite: avatar, shimmer states, HITL cards, inline chips |
| `src/components/agent-result-stack.tsx` | Modify | `sortPriority` handles `'working'` status |
| `src/components/conversation-turn.tsx` | Modify | `pendingAgents` prop, SupervisorHeader, hide summary when expanded, remove TurnEventTimeline |
| `src/components/conversation-timeline.tsx` | Modify | `roomAgentList` prop, compute `pendingAgents` |
| `src/components/room-messages.tsx` | Modify | Thread `roomAgentList` prop |
| `src/app/c/room/[id]/page.tsx` | Modify | Pass `roomAgentList` from `useRoomData.getAgentList()` |

## Key Design Decisions

- **Placeholder agents are component-layer props**, not in TurnViewModel. `buildTurns()` stays a pure function of message store data only.
- **`'working'` status** resolves the HITL/streaming conflict: `hitlResolved + isInteractiveState → 'working'` (not `'awaiting_input'`).
- **`isSupervisorTurn`** is derived from entity agentIds via `isSupervisorSystemAgent()`, not from `room.extend_info`.
- **Summary selection** uses `isSummarySystemAgent()` (4 IDs), not the old string heuristic `agentName.includes('supervisor')`.
- **Inline chips** replace the event rail; `TurnEventTimeline` kept but not rendered.
- **Max-height truncation** (from Phase 1 Task 3) already in place — no changes needed.
- **Ephemeral processing placeholders are filtered out** in `buildAgentResult()`. The HYBRO AI placeholder (`isEphemeral: true`, no `agentId`) is written by `useSendMessage`, `useProcessingRestore`, and the `processing_status` SSE handler. V2 per-agent placeholders (`pendingAgents`) replace its visual function. To avoid "double loading UI", `buildAgentResult()` skips ephemeral entities without `agentId`. Supervisor stage data (`taskContent`, `stepNumber`, `totalSteps`) is still extracted from these entities in `assembleTurn()` for `supervisorStage`.
- **Summary-family agents hide source badge.** System agents have no real `agentSource`, so showing a fallback cloud icon is misleading. `agent-badge.tsx` suppresses the source badge when `isSummary` is true.

---

### Task 1: Foundation — system-agents helpers, type extensions, CSS shimmer

**Files:**
- Modify: `src/lib/system-agents.ts`
- Modify: `src/lib/room-timeline/types.ts`
- Modify: `src/app/globals.css`
- Create: `tests/unit/lib/system-agents.test.ts`

- [ ] **Step 1: Write failing tests for system-agents helpers**

Create `tests/unit/lib/system-agents.test.ts`:

```ts
// tests/unit/lib/system-agents.test.ts
import { describe, it, expect } from 'vitest'
import {
  isSystemAgent,
  isSupervisorSystemAgent,
  isSummarySystemAgent,
} from '@/lib/system-agents'

describe('isSupervisorSystemAgent', () => {
  it('returns true for supervisor_hitl', () => {
    expect(isSupervisorSystemAgent('supervisor_hitl')).toBe(true)
  })

  it('returns true for supervisor_synthesis', () => {
    expect(isSupervisorSystemAgent('supervisor_synthesis')).toBe(true)
  })

  it('returns false for debate_summary', () => {
    expect(isSupervisorSystemAgent('debate_summary')).toBe(false)
  })

  it('returns false for non_debate_summary', () => {
    expect(isSupervisorSystemAgent('non_debate_summary')).toBe(false)
  })

  it('returns false for summary', () => {
    expect(isSupervisorSystemAgent('summary')).toBe(false)
  })

  it('returns false for undefined', () => {
    expect(isSupervisorSystemAgent(undefined)).toBe(false)
  })

  it('returns false for random agent id', () => {
    expect(isSupervisorSystemAgent('agent-123')).toBe(false)
  })
})

describe('isSummarySystemAgent', () => {
  it('returns true for supervisor_synthesis', () => {
    expect(isSummarySystemAgent('supervisor_synthesis')).toBe(true)
  })

  it('returns true for debate_summary', () => {
    expect(isSummarySystemAgent('debate_summary')).toBe(true)
  })

  it('returns true for non_debate_summary', () => {
    expect(isSummarySystemAgent('non_debate_summary')).toBe(true)
  })

  it('returns true for summary', () => {
    expect(isSummarySystemAgent('summary')).toBe(true)
  })

  it('returns false for supervisor_hitl', () => {
    expect(isSummarySystemAgent('supervisor_hitl')).toBe(false)
  })

  it('returns false for undefined', () => {
    expect(isSummarySystemAgent(undefined)).toBe(false)
  })

  it('returns false for random agent id', () => {
    expect(isSummarySystemAgent('agent-123')).toBe(false)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm run test -- tests/unit/lib/system-agents.test.ts --reporter=verbose`
Expected: FAIL (functions don't exist yet)

- [ ] **Step 3: Implement system-agents helpers**

In `src/lib/system-agents.ts`, add after the existing `SYSTEM_AGENTS` object (before `isSystemAgent`):

```ts
/** Supervisor-specific system agent IDs. Used for isSupervisorTurn derivation. */
const SUPERVISOR_SYSTEM_AGENT_IDS = new Set(['supervisor_hitl', 'supervisor_synthesis'])

/** Summary-family system agent IDs. Used for HYBRO AI visual treatment.
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm run test -- tests/unit/lib/system-agents.test.ts --reporter=verbose`
Expected: All PASS

- [ ] **Step 5: Extend types.ts**

In `src/lib/room-timeline/types.ts`:

**TurnViewModel** — add after `activeAgentIds`:
```ts
  /** Whether this turn was dispatched via Supervisor orchestration.
   *  Derived from presence of supervisor_hitl / supervisor_synthesis entities. */
  isSupervisorTurn: boolean
  /** Supervisor stage details (active turns only). */
  supervisorStage?: {
    stepNumber?: number
    totalSteps?: number
    details?: string
  }
```

**AgentResultViewModel** — replace the `status` line and add new fields after `hitlHistory`:
```ts
  status: 'completed' | 'failed' | 'awaiting_input' | 'working'
  // ... existing fields ...
  /** Whether this agent is a summary-family system agent. */
  isSummaryAgent: boolean
  /** Resolved HITL: prompt and user answer. */
  hitlResolved?: { prompt: string; answer: string }
  /** Active (unanswered) HITL prompt. */
  hitlPending?: { prompt: string }
  /** Event count for inline chips. */
  eventCount?: number
  /** Duration in ms for inline chips. */
  durationMs?: number
```

- [ ] **Step 6: Add shimmer-text-yellow to globals.css**

In `src/app/globals.css`, add after the `.dark .shimmer-text` block (after line 349):

```css
/* Yellow "Needs input" shimmer for HITL states */
.shimmer-text-yellow {
  background: linear-gradient(
    90deg,
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
  animation: shimmer-sweep 2.5s ease-in-out infinite;
}
```

- [ ] **Step 7: Commit**

```bash
git add src/lib/system-agents.ts src/lib/room-timeline/types.ts src/app/globals.css tests/unit/lib/system-agents.test.ts
git commit -m "feat: foundation for V2 timeline — system-agent helpers, type extensions, yellow shimmer CSS"
```

---

### Task 2: build-turns.ts data model changes

**Files:**
- Modify: `src/lib/room-timeline/build-turns.ts`
- Modify: `tests/unit/lib/build-turns.test.ts`

This is the largest single task. It changes: status derivation, isSupervisorTurn, supervisorStage, isSummaryAgent, hitlResolved/hitlPending, eventCount/durationMs, selectSummary, and turnsAreEqual.

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/lib/build-turns.test.ts`. First add the import at the top:

```ts
import { selectSummary, buildTurnsIncremental } from '@/lib/room-timeline/build-turns'
```

Then add a new describe block at the end:

```ts
describe('buildTurns – V2 data model', () => {
  // ── Ephemeral placeholder filtering ───────────────────────

  it('filters out ephemeral processing placeholder (no agentId)', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const placeholder = makeEntity({
      id: 'placeholder-1',
      messageType: 'agent',
      senderName: 'HYBRO AI',
      isEphemeral: true,
      // no agentId
      taskStatus: 'working' as any,
      taskContent: 'Processing your request…',
      timestamp: '2026-01-01T00:00:01Z',
    })
    const realAgent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:02Z',
      agentId: 'agent-real-1',
    })
    const turns = buildTurns(
      entitiesToMap([user, placeholder, realAgent]),
      ['u1', 'placeholder-1', 'a1'],
      [],
    )
    // Placeholder should NOT appear in agent results
    expect(turns[0].agentResults).toHaveLength(1)
    expect(turns[0].agentResults[0].agentId).toBe('agent-real-1')
  })

  // ── 'working' status ──────────────────────────────────────

  it('non-terminal non-interactive taskStatus produces working status', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
      taskStatus: 'submitted' as any,
      content: '',
    })
    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    expect(turns[0].agentResults[0].status).toBe('working')
  })

  it('hitlResolved + isInteractiveState produces working, not awaiting_input', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
      taskStatus: 'input-required' as any,
      hitlResolved: true,
      hitlUserAnswer: 'last 30 days',
      hitlPrompt: 'What date range?',
      content: 'Analyzing engagement...',
    })
    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    expect(turns[0].agentResults[0].status).toBe('working')
    expect(turns[0].agentResults[0].hitlResolved).toEqual({
      prompt: 'What date range?',
      answer: 'last 30 days',
    })
  })

  it('unresolved interactive state produces awaiting_input with hitlPending', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
      taskStatus: 'input-required' as any,
      hitlResolved: false,
      hitlPrompt: 'What date range?',
      content: '',
    })
    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    expect(turns[0].agentResults[0].status).toBe('awaiting_input')
    expect(turns[0].agentResults[0].hitlPending).toEqual({
      prompt: 'What date range?',
    })
  })

  // ── isSupervisorTurn ──────────────────────────────────────

  it('turn with supervisor_synthesis entity has isSupervisorTurn=true', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
      agentId: 'supervisor_synthesis',
      senderName: 'Summary Agent',
    })
    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    expect(turns[0].isSupervisorTurn).toBe(true)
  })

  it('turn with supervisor_hitl entity has isSupervisorTurn=true', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
      agentId: 'supervisor_hitl',
      senderName: 'Question & Answer',
    })
    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    expect(turns[0].isSupervisorTurn).toBe(true)
  })

  it('turn with debate_summary only has isSupervisorTurn=false', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
      agentId: 'debate_summary',
      senderName: 'Summary Agent',
    })
    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    expect(turns[0].isSupervisorTurn).toBe(false)
  })

  it('turn with only real agents has isSupervisorTurn=false', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
      agentId: 'agent-real-1',
    })
    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    expect(turns[0].isSupervisorTurn).toBe(false)
  })

  // ── isSummaryAgent ────────────────────────────────────────

  it('supervisor_synthesis agent has isSummaryAgent=true', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
      agentId: 'supervisor_synthesis',
    })
    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    expect(turns[0].agentResults[0].isSummaryAgent).toBe(true)
  })

  it('supervisor_hitl agent has isSummaryAgent=false', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
      agentId: 'supervisor_hitl',
    })
    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    expect(turns[0].agentResults[0].isSummaryAgent).toBe(false)
  })

  it('regular agent has isSummaryAgent=false', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
    })
    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    expect(turns[0].agentResults[0].isSummaryAgent).toBe(false)
  })
})

describe('selectSummary – V2 fix', () => {
  it('picks supervisor_synthesis over regular agents', () => {
    const results = [
      {
        agentId: 'agent-1',
        agentName: 'Excel Agent',
        messageId: 'msg-1',
        status: 'completed' as const,
        content: 'Excel result',
        artifacts: [],
        isSummaryAgent: false,
      },
      {
        agentId: 'supervisor_synthesis',
        agentName: 'Summary Agent',
        messageId: 'msg-2',
        status: 'completed' as const,
        content: 'Summary of all results',
        artifacts: [],
        isSummaryAgent: true,
      },
    ]
    const summary = selectSummary(results)
    expect(summary).not.toBeNull()
    expect(summary!.sourceAgentId).toBe('supervisor_synthesis')
  })

  it('does NOT pick supervisor_hitl as summary', () => {
    const results = [
      {
        agentId: 'supervisor_hitl',
        agentName: 'Question & Answer',
        messageId: 'msg-1',
        status: 'completed' as const,
        content: 'HITL question text',
        artifacts: [],
        isSummaryAgent: false,
      },
      {
        agentId: 'agent-1',
        agentName: 'Data Agent',
        messageId: 'msg-2',
        status: 'completed' as const,
        content: 'Data analysis result',
        artifacts: [],
        isSummaryAgent: false,
      },
    ]
    const summary = selectSummary(results)
    expect(summary).not.toBeNull()
    // Should pick agent-1 (first completed with content), NOT supervisor_hitl
    expect(summary!.sourceAgentId).toBe('agent-1')
  })
})

describe('buildTurnsIncremental – identity regression', () => {
  it('summary.title change causes turn to lose referential identity', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
      agentId: 'supervisor_synthesis',
      senderName: 'Summary Agent',
      taskStatus: 'completed',
      content: '# Original Title\nBody text here',
    })

    const entities1 = entitiesToMap([user, agent])
    const prevTurns = buildTurns(entities1, ['u1', 'a1'], [])
    expect(prevTurns[0].summary?.title).toBe('Original Title')

    // Change the summary title by updating agent content
    const agent2 = { ...agent, content: '# Updated Title\nBody text here' }
    const entities2 = entitiesToMap([user, agent2])
    const nextTurns = buildTurnsIncremental(prevTurns, entities2, ['u1', 'a1'], [])

    expect(nextTurns[0].summary?.title).toBe('Updated Title')
    // Must be a NEW object — referential identity must break
    expect(nextTurns[0]).not.toBe(prevTurns[0])
  })

  it('hitlResolved.prompt change causes turn to lose referential identity', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
      taskStatus: 'input-required' as any,
      hitlResolved: true,
      hitlUserAnswer: 'yes',
      hitlPrompt: 'Original question?',
      content: 'Working...',
    })

    const entities1 = entitiesToMap([user, agent])
    const prevTurns = buildTurns(entities1, ['u1', 'a1'], [])
    expect(prevTurns[0].agentResults[0].hitlResolved?.prompt).toBe('Original question?')

    // Change the HITL prompt (e.g. correction from backend)
    const agent2 = { ...agent, hitlPrompt: 'Corrected question?' }
    const entities2 = entitiesToMap([user, agent2])
    const nextTurns = buildTurnsIncremental(prevTurns, entities2, ['u1', 'a1'], [])

    expect(nextTurns[0].agentResults[0].hitlResolved?.prompt).toBe('Corrected question?')
    // Must be a NEW object — referential identity must break
    expect(nextTurns[0]).not.toBe(prevTurns[0])
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm run test -- tests/unit/lib/build-turns.test.ts --reporter=verbose`
Expected: FAIL (new fields don't exist, status logic unchanged)

- [ ] **Step 3: Implement build-turns.ts changes**

**3a. Add imports** at top of `src/lib/room-timeline/build-turns.ts`:

```ts
import { isSupervisorSystemAgent, isSummarySystemAgent } from '@/lib/system-agents'
```

**3b. Replace `buildAgentResult()` function** (lines 198-230):

```ts
function buildAgentResult(
  entity: MessageEntity | undefined,
  turnEvents: readonly RawTimelineEvent[],
): AgentResultViewModel | null {
  if (!entity) return null

  // Skip ephemeral processing placeholders (HYBRO AI global placeholder).
  // These have isEphemeral=true and no agentId. V2 per-agent placeholders
  // (pendingAgents prop) replace their visual function. Supervisor stage data
  // is extracted separately in assembleTurn().
  if (entity.isEphemeral && !entity.agentId) return null

  // Status derivation (spec §5.4)
  let status: AgentResultViewModel['status'] = 'completed'
  const hitlAnswered = entity.hitlResolved && !!entity.hitlUserAnswer

  if (entity.taskStatus && isFailureState(entity.taskStatus)) {
    status = 'failed'
  } else if (entity.taskStatus && isInteractiveState(entity.taskStatus)) {
    if (hitlAnswered) {
      status = 'working'
    } else {
      status = 'awaiting_input'
    }
  } else if (entity.taskStatus && !isTerminalState(entity.taskStatus)) {
    status = 'working'
  }

  // HITL split (spec §5.3)
  let hitlResolved: AgentResultViewModel['hitlResolved']
  let hitlPending: AgentResultViewModel['hitlPending']
  if (entity.hitlPrompt && entity.hitlResolved && entity.hitlUserAnswer) {
    hitlResolved = { prompt: entity.hitlPrompt, answer: entity.hitlUserAnswer }
  } else if (entity.hitlPrompt && !entity.hitlResolved) {
    hitlPending = { prompt: entity.hitlPrompt }
  }

  // Legacy hitlHistory for backward compat
  const hitlHistory: { prompt: string; answer: string }[] = []
  if (hitlResolved) {
    hitlHistory.push(hitlResolved)
  }

  // Inline chips data (spec §5.5)
  const agentEvents = entity.agentId
    ? turnEvents.filter(e => e.agentId === entity.agentId)
    : []
  const eventCount = agentEvents.length > 0 ? agentEvents.length : undefined

  let durationMs: number | undefined
  if (entity.agentId && agentEvents.length > 0) {
    const started = agentEvents.find(e => e.kind === 'agent_started')
    const completedEvts = agentEvents.filter(e => e.kind === 'agent_completed')
    const lastCompleted = completedEvts[completedEvts.length - 1]
    if (started && lastCompleted) {
      durationMs = new Date(lastCompleted.timestamp).getTime() - new Date(started.timestamp).getTime()
    }
  }

  return {
    agentId: entity.agentId,
    agentName: entity.senderName,
    agentSource: entity.agentSource,
    messageId: entity.id,
    status,
    content: entity.content,
    artifacts: entity.artifacts ?? [],
    hitlHistory: hitlHistory.length > 0 ? hitlHistory : undefined,
    isSummaryAgent: isSummarySystemAgent(entity.agentId),
    hitlResolved,
    hitlPending,
    eventCount,
    durationMs,
  }
}
```

**3c. Update `assembleTurn()` function** to pass events to `buildAgentResult`, populate `isSupervisorTurn` and `supervisorStage`:

Replace the agentResults mapping line (current line 167-169):
```ts
  const agentResults = scaffold.agentMessageIds
    .map((id) => buildAgentResult(entities[id]))
    .filter((r): r is AgentResultViewModel => r !== null)
```
with:
```ts
  // Filter events for this turn first (needed by buildAgentResult for inline chips)
  const turnEvents = filterEventsForTurn(scaffold, entities, events)

  const agentResults = scaffold.agentMessageIds
    .map((id) => buildAgentResult(entities[id], turnEvents))
    .filter((r): r is AgentResultViewModel => r !== null)
```

**IMPORTANT:** Pass `turnEvents` (not `events`) to `buildAgentResult`. Using the full `events` array would compute eventCount/durationMs across all turns instead of per-turn.

And remove the later `const turnEvents = filterEventsForTurn(...)` call (current line 179) since it's now above.

Add after `activeAgentIds` computation, before the return statement:

```ts
  // Supervisor detection (spec §5.2)
  const isSupervisorTurn = agentResults.some(r => isSupervisorSystemAgent(r.agentId))

  // Supervisor stage from latest entity with step/stage data.
  // Scans ALL entities (including ephemeral placeholders that were filtered
  // from agentResults) because the HYBRO AI processing placeholder carries
  // the stage details (taskContent, stepNumber, totalSteps) during early
  // supervisor orchestration before real agent entities arrive.
  let supervisorStage: TurnViewModel['supervisorStage']
  if (isSupervisorTurn) {
    for (let i = scaffold.agentMessageIds.length - 1; i >= 0; i--) {
      const e = entities[scaffold.agentMessageIds[i]]
      if (e && (e.stepNumber != null || e.totalSteps != null || e.taskContent)) {
        supervisorStage = {
          stepNumber: e.stepNumber,
          totalSteps: e.totalSteps,
          details: e.taskContent,
        }
        break
      }
    }
  }
```

Update the return to include the new fields:

```ts
  return {
    id: turnId,
    roomId: scaffold.userEntity?.roomId ?? '',
    userMessageId: scaffold.userMessageId,
    userContent: scaffold.userEntity?.content ?? '',
    userAttachments: scaffold.userEntity?.attachments ?? [],
    timestamp: scaffold.userEntity?.timestamp ?? entities[scaffold.agentMessageIds[0]]?.timestamp ?? '',
    status,
    events: turnEvents,
    summary,
    agentResults,
    activeAgentIds,
    isSupervisorTurn,
    supervisorStage,
  }
```

**3d. Update `deriveTurnStatus()`** to handle `'working'`:

Replace the function body:
```ts
function deriveTurnStatus(agentResults: AgentResultViewModel[]): TurnStatus {
  if (agentResults.length === 0) return 'active'

  const hasWorking = agentResults.some((r) => r.status === 'working')
  const hasAwaitingInput = agentResults.some((r) => r.status === 'awaiting_input')
  const hasFailed = agentResults.some((r) => r.status === 'failed')
  const hasCompleted = agentResults.some((r) => r.status === 'completed')
  const allFailed = agentResults.every((r) => r.status === 'failed')
  const allCompleted = agentResults.every((r) => r.status === 'completed')

  if (hasWorking) return 'active'
  if (hasAwaitingInput) return 'awaiting_input'
  if (allFailed) return 'failed'
  if (allCompleted) return 'completed'
  if (hasCompleted && hasFailed) return 'partial'

  return 'active'
}
```

**3e. Update `selectSummary()`** to use `isSummarySystemAgent`:

Replace lines 270-274 (the supervisor detection):
```ts
  // Priority 1: system summary agent
  const systemSummary = completedWithContent.find((r) =>
    isSummarySystemAgent(r.agentId),
  )
  if (systemSummary) return buildSummaryFromResult(systemSummary)
```

**3f. Extend `turnsAreEqual()`** (spec §5.8):

Add after the existing events.length check (before `return true`):

```ts
  // V2 TurnViewModel fields
  if (a.isSupervisorTurn !== b.isSupervisorTurn) return false
  if (a.supervisorStage?.stepNumber !== b.supervisorStage?.stepNumber) return false
  if (a.supervisorStage?.totalSteps !== b.supervisorStage?.totalSteps) return false
  if (a.supervisorStage?.details !== b.supervisorStage?.details) return false

  // V2 AgentResultViewModel fields (extend existing per-agent loop)
  for (let i = 0; i < a.agentResults.length; i++) {
    if (a.agentResults[i].hitlResolved?.prompt !== b.agentResults[i].hitlResolved?.prompt) return false
    if (a.agentResults[i].hitlResolved?.answer !== b.agentResults[i].hitlResolved?.answer) return false
    if (a.agentResults[i].hitlPending?.prompt !== b.agentResults[i].hitlPending?.prompt) return false
    if (a.agentResults[i].eventCount !== b.agentResults[i].eventCount) return false
    if (a.agentResults[i].durationMs !== b.agentResults[i].durationMs) return false
  }

  // Summary equality
  if ((a.summary?.sourceAgentId ?? null) !== (b.summary?.sourceAgentId ?? null)) return false
  if ((a.summary?.title ?? '') !== (b.summary?.title ?? '')) return false
  if ((a.summary?.body ?? '') !== (b.summary?.body ?? '')) return false
```

Note: The per-agent V2 checks should be added inside the existing per-agent loop (lines 419-429), after the artifacts check. The code above shows them separately for clarity — when implementing, merge them into the existing loop.

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm run test -- tests/unit/lib/build-turns.test.ts --reporter=verbose`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/lib/room-timeline/build-turns.ts tests/unit/lib/build-turns.test.ts
git commit -m "feat: build-turns V2 — working status, supervisor detection, summary fix, turnsAreEqual"
```

---

### Task 3: Leaf components — InlineChips, HitlCompactCard, HitlQuestionCard

**Files:**
- Create: `src/components/inline-chips.tsx`
- Create: `src/components/hitl-compact-card.tsx`
- Create: `src/components/hitl-question-card.tsx`
- Create: `tests/unit/components/inline-chips.test.tsx`
- Create: `tests/unit/components/hitl-cards.test.tsx`

- [ ] **Step 1: Write failing tests for InlineChips**

Create `tests/unit/components/inline-chips.test.tsx`:

```tsx
import React from 'react'
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { InlineChips } from '@/components/inline-chips'

afterEach(() => cleanup())

describe('InlineChips', () => {
  it('renders step count when eventCount provided', () => {
    render(<InlineChips eventCount={4} />)
    expect(screen.getByText('4 steps')).toBeTruthy()
  })

  it('renders "1 step" for singular', () => {
    render(<InlineChips eventCount={1} />)
    expect(screen.getByText('1 step')).toBeTruthy()
  })

  it('renders duration when durationMs provided', () => {
    render(<InlineChips durationMs={3200} />)
    expect(screen.getByText('3.2s')).toBeTruthy()
  })

  it('renders both chips together', () => {
    render(<InlineChips eventCount={4} durationMs={3200} />)
    expect(screen.getByText('4 steps')).toBeTruthy()
    expect(screen.getByText('3.2s')).toBeTruthy()
  })

  it('renders nothing when no data', () => {
    const { container } = render(<InlineChips />)
    expect(container.firstElementChild?.children.length ?? 0).toBe(0)
  })

  it('has proper aria-label', () => {
    render(<InlineChips eventCount={4} durationMs={3200} />)
    expect(screen.getByLabelText('4 steps, 3.2 seconds')).toBeTruthy()
  })
})
```

- [ ] **Step 2: Write failing tests for HITL cards**

Create `tests/unit/components/hitl-cards.test.tsx`:

```tsx
import React from 'react'
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { HitlCompactCard } from '@/components/hitl-compact-card'
import { HitlQuestionCard } from '@/components/hitl-question-card'

afterEach(() => cleanup())

describe('HitlCompactCard', () => {
  it('renders truncated question and emphasized answer', () => {
    render(<HitlCompactCard prompt="What date range would you like?" answer="last 30 days" />)
    expect(screen.getByText('What date range would you like?')).toBeTruthy()
    expect(screen.getByText('last 30 days')).toBeTruthy()
  })

  it('has role status', () => {
    render(<HitlCompactCard prompt="Question?" answer="Answer" />)
    expect(screen.getByRole('status')).toBeTruthy()
  })

  it('shows green dot before answer', () => {
    const { container } = render(<HitlCompactCard prompt="Q?" answer="A" />)
    // Green dot indicator
    expect(container.querySelector('.bg-green-500')).toBeTruthy()
  })
})

describe('HitlQuestionCard', () => {
  it('renders question text', () => {
    render(<HitlQuestionCard prompt="What date range would you like?" />)
    expect(screen.getByText('What date range would you like?')).toBeTruthy()
  })

  it('shows Needs input shimmer label', () => {
    render(<HitlQuestionCard prompt="Question?" />)
    expect(screen.getByText('Needs input')).toBeTruthy()
  })

  it('has yellow-tinted border', () => {
    const { container } = render(<HitlQuestionCard prompt="Q?" />)
    const card = container.firstElementChild!
    expect(card.className).toContain('border-yellow-500/20')
  })

  it('has role status with aria-label', () => {
    render(<HitlQuestionCard prompt="Question?" />)
    expect(screen.getByRole('status')).toBeTruthy()
  })
})
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `npm run test -- tests/unit/components/inline-chips.test.tsx tests/unit/components/hitl-cards.test.tsx --reporter=verbose`
Expected: FAIL (files don't exist)

- [ ] **Step 4: Implement InlineChips**

Create `src/components/inline-chips.tsx`:

```tsx
'use client'

interface InlineChipsProps {
  eventCount?: number
  durationMs?: number
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

export function InlineChips({ eventCount, durationMs }: InlineChipsProps) {
  const hasData = eventCount != null || durationMs != null
  const parts: string[] = []
  if (eventCount != null) parts.push(`${eventCount} step${eventCount !== 1 ? 's' : ''}`)
  if (durationMs != null) parts.push(`${formatDuration(durationMs)} seconds`.replace(/(\d+\.\d)s seconds/, '$1 seconds'))

  const ariaLabel = hasData
    ? parts.map(p => {
        // "4 steps" → "4 steps", "3.2s" → "3.2 seconds"
        if (p.endsWith('s') && !p.endsWith('steps')) return p.slice(0, -1) + ' seconds'
        return p
      }).join(', ')
    : undefined

  return (
    <span className="inline-flex items-center gap-1.5" aria-label={ariaLabel}>
      {eventCount != null && (
        <span className="inline-flex bg-secondary rounded px-1.5 py-px text-[10px] text-muted-foreground">
          {eventCount} step{eventCount !== 1 ? 's' : ''}
        </span>
      )}
      {durationMs != null && (
        <span className="inline-flex bg-secondary rounded px-1.5 py-px text-[10px] text-muted-foreground">
          {formatDuration(durationMs)}
        </span>
      )}
    </span>
  )
}
```

Wait — the aria-label logic is wrong. Let me simplify:

```tsx
'use client'

interface InlineChipsProps {
  eventCount?: number
  durationMs?: number
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

export function InlineChips({ eventCount, durationMs }: InlineChipsProps) {
  const chips: { label: string; ariaText: string }[] = []

  if (eventCount != null) {
    const label = `${eventCount} step${eventCount !== 1 ? 's' : ''}`
    chips.push({ label, ariaText: label })
  }

  if (durationMs != null) {
    const label = formatDuration(durationMs)
    const seconds = (durationMs / 1000).toFixed(1)
    chips.push({ label, ariaText: `${seconds} seconds` })
  }

  if (chips.length === 0) return <span className="inline-flex items-center gap-1.5" />

  return (
    <span
      className="inline-flex items-center gap-1.5"
      aria-label={chips.map(c => c.ariaText).join(', ')}
    >
      {chips.map((chip) => (
        <span
          key={chip.label}
          className="inline-flex bg-secondary rounded px-1.5 py-px text-[10px] text-muted-foreground"
        >
          {chip.label}
        </span>
      ))}
    </span>
  )
}
```

- [ ] **Step 5: Implement HitlCompactCard**

Create `src/components/hitl-compact-card.tsx`:

```tsx
'use client'

interface HitlCompactCardProps {
  prompt: string
  answer: string
}

export function HitlCompactCard({ prompt, answer }: HitlCompactCardProps) {
  return (
    <div
      role="status"
      aria-label={`Resolved: ${prompt} — ${answer}`}
      className="bg-background border border-border rounded-lg px-3 py-2 mt-2"
    >
      <p className="text-xs text-muted-foreground truncate mb-1">{prompt}</p>
      <div className="flex items-center gap-1.5">
        <span className="w-1 h-1 rounded-full bg-green-500 shrink-0" />
        <span className="text-xs font-medium text-foreground">{answer}</span>
      </div>
    </div>
  )
}
```

- [ ] **Step 6: Implement HitlQuestionCard**

Create `src/components/hitl-question-card.tsx`:

```tsx
'use client'

import { CircleHelp } from 'lucide-react'

interface HitlQuestionCardProps {
  prompt: string
}

export function HitlQuestionCard({ prompt }: HitlQuestionCardProps) {
  return (
    <div
      role="status"
      aria-label={`Agent needs input: ${prompt}`}
      className="bg-background border border-yellow-500/20 rounded-lg px-3 py-3 mt-2"
    >
      <div className="flex items-center gap-1.5 mb-2">
        <CircleHelp className="h-3.5 w-3.5 text-yellow-500 shrink-0" />
        <span className="shimmer-text-yellow text-xs font-medium">Needs input</span>
      </div>
      <p className="text-sm text-foreground/80 leading-relaxed">{prompt}</p>
    </div>
  )
}
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `npm run test -- tests/unit/components/inline-chips.test.tsx tests/unit/components/hitl-cards.test.tsx --reporter=verbose`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add src/components/inline-chips.tsx src/components/hitl-compact-card.tsx src/components/hitl-question-card.tsx tests/unit/components/inline-chips.test.tsx tests/unit/components/hitl-cards.test.tsx
git commit -m "feat: add InlineChips, HitlCompactCard, HitlQuestionCard components"
```

---

### Task 4: AgentPlaceholderRow + SupervisorHeader components

**Files:**
- Create: `src/components/agent-placeholder-row.tsx`
- Create: `src/components/supervisor-header.tsx`
- Create: `tests/unit/components/agent-placeholder-row.test.tsx`
- Create: `tests/unit/components/supervisor-header.test.tsx`

- [ ] **Step 1: Write failing tests for AgentPlaceholderRow**

Create `tests/unit/components/agent-placeholder-row.test.tsx`:

```tsx
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { AgentPlaceholderRow } from '@/components/agent-placeholder-row'

vi.mock('@/lib/agent-avatar', () => ({
  getAgentAvatarUri: (seed: string) => `data:image/svg+xml;seed=${seed}`,
}))

afterEach(() => cleanup())

describe('AgentPlaceholderRow', () => {
  it('renders agent name', () => {
    render(<AgentPlaceholderRow agentId="a1" agentName="Weather Bot" />)
    expect(screen.getByText('Weather Bot')).toBeTruthy()
  })

  it('renders avatar image', () => {
    const { container } = render(<AgentPlaceholderRow agentId="a1" agentName="Bot" />)
    const img = container.querySelector('img')
    expect(img).toBeTruthy()
    expect(img!.getAttribute('src')).toContain('seed=a1')
  })

  it('renders shimmer "Thinking" text', () => {
    render(<AgentPlaceholderRow agentId="a1" agentName="Bot" />)
    expect(screen.getByText('Thinking')).toBeTruthy()
  })

  it('has shimmer-text class on status text', () => {
    render(<AgentPlaceholderRow agentId="a1" agentName="Bot" />)
    const thinking = screen.getByText('Thinking')
    expect(thinking.className).toContain('shimmer-text')
  })
})
```

- [ ] **Step 2: Write failing tests for SupervisorHeader**

Create `tests/unit/components/supervisor-header.test.tsx`:

```tsx
import React from 'react'
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { SupervisorHeader } from '@/components/supervisor-header'

afterEach(() => cleanup())

describe('SupervisorHeader', () => {
  it('renders HYBRO AI text', () => {
    render(<SupervisorHeader isCompleted={false} />)
    expect(screen.getByText('HYBRO AI')).toBeTruthy()
  })

  it('shows shimmer stage text when processing', () => {
    render(
      <SupervisorHeader
        isCompleted={false}
        stepNumber={2}
        totalSteps={3}
        details="Dispatching agents"
      />,
    )
    expect(screen.getByText('Step 2 of 3 · Dispatching agents')).toBeTruthy()
  })

  it('shows static stats when completed', () => {
    render(
      <SupervisorHeader
        isCompleted={true}
        agentCount={3}
        totalDurationMs={12400}
      />,
    )
    expect(screen.getByText('3 agents · 12.4s')).toBeTruthy()
  })

  it('has role status with aria-live', () => {
    render(<SupervisorHeader isCompleted={false} />)
    const header = screen.getByRole('status')
    expect(header.getAttribute('aria-live')).toBe('polite')
  })

  it('renders HYBRO favicon icon', () => {
    render(<SupervisorHeader isCompleted={false} />)
    const icon = screen.getByAltText('HYBRO AI')
    expect(icon.getAttribute('src')).toBe('/favicon.svg')
  })
})
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `npm run test -- tests/unit/components/agent-placeholder-row.test.tsx tests/unit/components/supervisor-header.test.tsx --reporter=verbose`
Expected: FAIL

- [ ] **Step 4: Implement AgentPlaceholderRow**

Create `src/components/agent-placeholder-row.tsx`:

```tsx
'use client'

import { getAgentAvatarUri } from '@/lib/agent-avatar'

interface AgentPlaceholderRowProps {
  agentId: string
  agentName: string
}

export function AgentPlaceholderRow({ agentId, agentName }: AgentPlaceholderRowProps) {
  const avatarUri = getAgentAvatarUri(agentId)

  return (
    <div className="flex gap-3 py-3 border-b border-border last:border-b-0">
      <img
        src={avatarUri}
        alt=""
        aria-hidden="true"
        className="w-7 h-7 rounded-md shrink-0 mt-0.5"
      />
      <div className="flex items-center gap-2">
        <span className="text-base font-semibold text-foreground">{agentName}</span>
        <span className="shimmer-text text-sm text-muted-foreground">Thinking</span>
      </div>
    </div>
  )
}
```

- [ ] **Step 5: Implement SupervisorHeader**

Create `src/components/supervisor-header.tsx`:

```tsx
'use client'

interface SupervisorHeaderProps {
  isCompleted: boolean
  stepNumber?: number
  totalSteps?: number
  details?: string
  agentCount?: number
  totalDurationMs?: number
}

function formatDuration(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`
}

export function SupervisorHeader({
  isCompleted,
  stepNumber,
  totalSteps,
  details,
  agentCount,
  totalDurationMs,
}: SupervisorHeaderProps) {
  let statusText: string
  if (isCompleted) {
    const parts: string[] = []
    if (agentCount != null) parts.push(`${agentCount} agent${agentCount !== 1 ? 's' : ''}`)
    if (totalDurationMs != null) parts.push(formatDuration(totalDurationMs))
    statusText = parts.join(' · ') || 'Completed'
  } else {
    const parts: string[] = []
    if (stepNumber != null && totalSteps != null) parts.push(`Step ${stepNumber} of ${totalSteps}`)
    if (details) parts.push(details)
    statusText = parts.join(' · ') || 'Processing...'
  }

  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-center gap-2.5 pb-3 mb-3 border-b border-border"
    >
      <img
        src="/favicon.svg"
        alt="HYBRO AI"
        className="w-[18px] h-[18px] shrink-0"
      />
      <span className="text-brand-gradient text-xs font-semibold">HYBRO AI</span>
      <span className="text-muted-foreground/50 text-[11px]">·</span>
      {isCompleted ? (
        <span className="text-xs text-muted-foreground">{statusText}</span>
      ) : (
        <span className="shimmer-text text-xs text-muted-foreground">{statusText}</span>
      )}
    </div>
  )
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `npm run test -- tests/unit/components/agent-placeholder-row.test.tsx tests/unit/components/supervisor-header.test.tsx --reporter=verbose`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add src/components/agent-placeholder-row.tsx src/components/supervisor-header.tsx tests/unit/components/agent-placeholder-row.test.tsx tests/unit/components/supervisor-header.test.tsx
git commit -m "feat: add AgentPlaceholderRow and SupervisorHeader components"
```

---

### Task 5: agent-badge.tsx — avatar + summary-family brand treatment

**Files:**
- Modify: `src/components/agent-badge.tsx`
- Modify: `tests/unit/components/agent-badge.test.tsx`

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/components/agent-badge.test.tsx`. First add mock for agent-avatar at top-level:

```tsx
vi.mock('@/lib/agent-avatar', () => ({
  getAgentAvatarUri: (seed: string) => `data:image/svg+xml;seed=${seed}`,
}))

vi.mock('@/lib/system-agents', () => ({
  isSummarySystemAgent: (id: string | undefined) =>
    ['supervisor_synthesis', 'debate_summary', 'non_debate_summary', 'summary'].includes(id ?? ''),
}))
```

Add tests inside existing describe block:

```tsx
// --- Avatar rendering ---

it('renders avatar image when agentId provided', () => {
  const { container } = render(<AgentBadge agentId="a1" agentName="Bot" size="md" />)
  const img = container.querySelector('img')
  expect(img).toBeTruthy()
  expect(img!.getAttribute('src')).toContain('seed=a1')
})

it('does NOT render avatar when agentId missing', () => {
  const { container } = render(<AgentBadge agentName="Bot" showDeletedIndicator={false} />)
  expect(container.querySelector('img')).toBeNull()
})

// --- Summary agent brand treatment ---

it('renders brand gradient name for summary-family agents', () => {
  render(<AgentBadge agentId="supervisor_synthesis" agentName="Summary Agent" size="md" />)
  const name = screen.getByText('Summary from HYBRO AI')
  expect(name.className).toContain('text-brand-gradient')
})

it('renders HYBRO favicon for summary-family agents', () => {
  const { container } = render(<AgentBadge agentId="supervisor_synthesis" agentName="Summary Agent" size="md" />)
  const faviconImg = container.querySelector('img[src="/favicon.svg"]')
  expect(faviconImg).toBeTruthy()
})

it('does NOT use brand gradient for non-summary agents', () => {
  render(<AgentBadge agentId="agent-1" agentName="Bot" size="md" />)
  expect(screen.getByText('Bot').className).not.toContain('text-brand-gradient')
})

it('does NOT use brand gradient for supervisor_hitl', () => {
  render(<AgentBadge agentId="supervisor_hitl" agentName="Q&A" size="md" />)
  expect(screen.getByText('Q&A').className).not.toContain('text-brand-gradient')
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm run test -- tests/unit/components/agent-badge.test.tsx --reporter=verbose`
Expected: New tests FAIL

- [ ] **Step 3: Implement agent-badge.tsx changes**

Replace `src/components/agent-badge.tsx`:

```tsx
'use client'

import { cn } from '@/lib/utils'
import { getAgentColorClasses } from '@/lib/agent-colors'
import { AgentSourceBadge } from './agent-source-badge'
import { TooltipProvider } from '@/components/ui/tooltip'
import { getAgentAvatarUri } from '@/lib/agent-avatar'
import { isSummarySystemAgent } from '@/lib/system-agents'

interface AgentBadgeProps {
  agentId?: string
  agentName: string
  agentSource?: 'hub' | 'cloud'
  size?: 'sm' | 'md'
  hideSource?: boolean
  showDeletedIndicator?: boolean
}

const SIZE_CLASSES = {
  sm: { avatar: 'w-5 h-5', text: 'text-sm', gap: 'gap-1.5', icon: 'h-3 w-3' },
  md: { avatar: 'w-7 h-7', text: 'text-base', gap: 'gap-2', icon: 'h-3.5 w-3.5' },
} as const

export function AgentBadge({
  agentId,
  agentName,
  agentSource,
  size = 'sm',
  hideSource = false,
  showDeletedIndicator,
}: AgentBadgeProps) {
  const colors = agentId ? getAgentColorClasses(agentId) : null
  const isDeleted = showDeletedIndicator ?? !agentId
  const isSummary = isSummarySystemAgent(agentId)
  const s = SIZE_CLASSES[size]

  // Summary agents get special display name + brand treatment
  const displayName = isSummary
    ? 'Summary from HYBRO AI'
    : isDeleted
      ? `${agentName || 'Unknown Agent'} (deleted)`
      : agentName

  // Source badge: suppress for summary-family agents (no real agentSource,
  // fallback cloud icon would be misleading)
  const effectiveSource = (hideSource || isSummary)
    ? undefined
    : isDeleted
      ? undefined
      : agentSource ?? 'cloud'

  // Avatar: summary → HYBRO favicon, regular → dicebear, deleted → none
  const avatarSrc = isSummary
    ? '/favicon.svg'
    : agentId
      ? getAgentAvatarUri(agentId)
      : undefined

  return (
    <span className={cn('inline-flex items-center', s.gap, isDeleted && 'opacity-50')}>
      {avatarSrc ? (
        <img
          src={avatarSrc}
          alt=""
          aria-hidden="true"
          className={cn('rounded-md shrink-0', s.avatar, isSummary && 'border border-border bg-background p-0.5')}
        />
      ) : (
        <span
          className={cn('rounded-full shrink-0 h-2 w-2', colors ? colors.accent : 'bg-muted-foreground')}
          aria-hidden="true"
        />
      )}
      <span
        className={cn(
          'font-semibold truncate',
          s.text,
          isSummary
            ? 'text-brand-gradient'
            : colors ? colors.text : 'text-muted-foreground',
        )}
      >
        {displayName}
      </span>
      {effectiveSource && (
        <TooltipProvider delayDuration={200}>
          <AgentSourceBadge source={effectiveSource} className={s.icon} />
        </TooltipProvider>
      )}
    </span>
  )
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm run test -- tests/unit/components/agent-badge.test.tsx --reporter=verbose`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/components/agent-badge.tsx tests/unit/components/agent-badge.test.tsx
git commit -m "feat: agent-badge avatar + summary-family HYBRO AI brand treatment"
```

---

### Task 6: agent-result-card.tsx + agent-result-stack.tsx rewrite

**Files:**
- Modify: `src/components/agent-result-card.tsx`
- Modify: `src/components/agent-result-stack.tsx`
- Modify: `tests/unit/components/agent-result-card.test.tsx`

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/components/agent-result-card.test.tsx`.

First update the `makeResult` helper to include new fields:

```tsx
function makeResult(overrides: Partial<AgentResultViewModel> = {}): AgentResultViewModel {
  return {
    agentId: 'agent-1',
    agentName: 'Test Agent',
    messageId: 'msg-1',
    status: 'completed',
    content: 'This is the agent response content.',
    artifacts: [],
    isSummaryAgent: false,
    ...overrides,
  }
}
```

Add new tests:

```tsx
it('shows shimmer "Generating" for working status with content', () => {
  const { container } = render(
    <AgentResultCard result={makeResult({ status: 'working', content: 'Partial...' })} />,
  )
  expect(container.querySelector('.shimmer-text')).toBeTruthy()
  const card = screen.getByTestId('agent-result-msg-1')
  expect(card.getAttribute('aria-busy')).toBe('true')
})

it('shows shimmer "Thinking" for working status without content', () => {
  render(
    <AgentResultCard result={makeResult({ status: 'working', content: '' })} />,
  )
  expect(screen.getByText('Thinking')).toBeTruthy()
})

it('shows yellow shimmer "Needs input" for awaiting_input status', () => {
  const { container } = render(
    <AgentResultCard result={makeResult({ status: 'awaiting_input', content: '' })} />,
  )
  expect(screen.getByText('Needs input')).toBeTruthy()
  expect(container.querySelector('.shimmer-text-yellow')).toBeTruthy()
})

it('renders HitlCompactCard for resolved HITL', () => {
  render(
    <AgentResultCard
      result={makeResult({
        hitlResolved: { prompt: 'What range?', answer: 'last 30 days' },
      })}
    />,
  )
  expect(screen.getByText('What range?')).toBeTruthy()
  expect(screen.getByText('last 30 days')).toBeTruthy()
})

it('renders HitlQuestionCard for pending HITL', () => {
  render(
    <AgentResultCard
      result={makeResult({
        status: 'awaiting_input',
        hitlPending: { prompt: 'What date range?' },
      })}
    />,
  )
  expect(screen.getByText('What date range?')).toBeTruthy()
  expect(screen.getByText('Needs input')).toBeTruthy()
})

it('renders InlineChips when eventCount/durationMs present', () => {
  render(
    <AgentResultCard
      result={makeResult({ eventCount: 4, durationMs: 3200 })}
    />,
  )
  expect(screen.getByText('4 steps')).toBeTruthy()
  expect(screen.getByText('3.2s')).toBeTruthy()
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm run test -- tests/unit/components/agent-result-card.test.tsx --reporter=verbose`
Expected: New tests FAIL

- [ ] **Step 3: Rewrite agent-result-card.tsx**

Replace `src/components/agent-result-card.tsx`:

```tsx
// src/components/agent-result-card.tsx
'use client'

import React from 'react'
import { cn } from '@/lib/utils'
import { AgentBadge } from './agent-badge'
import { TruncatedContent } from './truncated-content'
import { ArtifactList } from './artifact-list'
import { InlineChips } from './inline-chips'
import { HitlCompactCard } from './hitl-compact-card'
import { HitlQuestionCard } from './hitl-question-card'
import { AlertTriangle } from 'lucide-react'
import type { AgentResultViewModel } from '@/lib/room-timeline/types'
import type { QuoteData } from './message-bubble'

// ── Status text ────────────────────────────────────────────────

function StatusText({ result }: { result: AgentResultViewModel }) {
  const { status, content } = result

  switch (status) {
    case 'working':
      return (
        <span className="shimmer-text text-sm text-muted-foreground">
          {content.length > 0 ? 'Generating' : 'Thinking'}
        </span>
      )
    case 'awaiting_input':
      return (
        <span className="shimmer-text-yellow text-sm text-muted-foreground">
          Needs input
        </span>
      )
    case 'failed':
      return (
        <span className="inline-flex items-center gap-1 text-xs text-destructive">
          <AlertTriangle className="h-3 w-3" />
          Failed
        </span>
      )
    case 'completed':
      return null
  }
}

// ── Main component ──────────────────────────────────────────────

interface AgentResultCardProps {
  result: AgentResultViewModel
  onQuote?: (data: QuoteData) => void
}

export function AgentResultCard({ result, onQuote }: AgentResultCardProps) {
  const isStreaming = result.status === 'working' && result.content.length > 0
  const isEmpty = result.content.trim().length === 0 && result.status === 'completed'
  const isFailed = result.status === 'failed'
  const isWorking = result.status === 'working'
  const isAwaitingInput = result.status === 'awaiting_input'

  return (
    <div
      className="py-3 border-b border-border last:border-b-0"
      aria-busy={isStreaming || (isWorking && result.content.length === 0) ? 'true' : undefined}
      data-testid={`agent-result-${result.messageId}`}
    >
      {/* Header: badge + status + inline chips */}
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="flex items-center gap-2 min-w-0">
          <AgentBadge
            agentId={result.agentId}
            agentName={result.agentName}
            agentSource={result.agentSource}
            size="md"
            showDeletedIndicator={result.status !== 'awaiting_input' && result.status !== 'working' && !result.agentId}
          />
          <InlineChips eventCount={result.eventCount} durationMs={result.durationMs} />
        </div>
        <StatusText result={result} />
      </div>

      {/* Pending HITL question card */}
      {isAwaitingInput && result.hitlPending && (
        <HitlQuestionCard prompt={result.hitlPending.prompt} />
      )}

      {/* Resolved HITL compact card */}
      {result.hitlResolved && (
        <HitlCompactCard prompt={result.hitlResolved.prompt} answer={result.hitlResolved.answer} />
      )}

      {/* Content */}
      {isEmpty ? (
        <p className="text-xs text-muted-foreground italic mt-1">
          No response content
        </p>
      ) : isFailed ? (
        <p className="text-xs text-destructive mt-1">{result.content || 'An error occurred'}</p>
      ) : result.content.length > 0 ? (
        <div className={cn('mt-2', isStreaming && 'shimmer-text')}>
          <TruncatedContent
            content={result.content}
            maxLines={6}
            className="text-foreground"
            markdownClassName="text-base"
          />
        </div>
      ) : null}

      {/* Artifacts */}
      <ArtifactList artifacts={result.artifacts} />
    </div>
  )
}
```

- [ ] **Step 4: Update agent-result-stack.tsx sortPriority**

In `src/components/agent-result-stack.tsx`, update `sortPriority` to handle `'working'`:

Replace the function:
```tsx
function sortPriority(
  result: AgentResultViewModel,
  summarySourceId: string | undefined,
): number {
  if (summarySourceId && result.agentId === summarySourceId) return 0
  if (result.status === 'completed' && result.content.trim().length > 0) return 1
  if (result.status === 'working') return 2
  if (result.status === 'awaiting_input') return 3
  if (result.status === 'failed') return 4
  if (result.status === 'completed' && result.content.trim().length === 0) return 5
  return 6
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `npm run test -- tests/unit/components/agent-result-card.test.tsx --reporter=verbose`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/components/agent-result-card.tsx src/components/agent-result-stack.tsx tests/unit/components/agent-result-card.test.tsx
git commit -m "feat: rewrite agent-result-card with shimmer states, HITL cards, inline chips"
```

---

### Task 7: conversation-turn.tsx — layout changes

**Files:**
- Modify: `src/components/conversation-turn.tsx`
- Modify: `tests/unit/components/conversation-turn.test.tsx`

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/components/conversation-turn.test.tsx`.

First add mocks at top-level:

```tsx
vi.mock('@/components/agent-placeholder-row', () => ({
  AgentPlaceholderRow: ({ agentId, agentName }: { agentId: string; agentName: string }) => (
    <div data-testid={`placeholder-${agentId}`}>{agentName} — Thinking</div>
  ),
}))

vi.mock('@/components/supervisor-header', () => ({
  SupervisorHeader: ({ isCompleted }: { isCompleted: boolean }) => (
    <div data-testid="supervisor-header">{isCompleted ? 'Completed' : 'Processing'}</div>
  ),
}))
```

Update `makeTurn` to include new fields:

```tsx
function makeTurn(overrides: Partial<TurnViewModel> = {}): TurnViewModel {
  return {
    // ... existing defaults ...
    isSupervisorTurn: false,
    ...overrides,
  }
}
```

Add tests:

```tsx
it('renders AgentPlaceholderRow for pending agents', () => {
  const turn = makeTurn({ status: 'active' })
  const pendingAgents = [
    { agentId: 'a2', agentName: 'Data Bot' },
    { agentId: 'a3', agentName: 'Image Bot' },
  ]
  render(<MemoizedTurn turn={turn} index={0} isActive={true} pendingAgents={pendingAgents} />)
  expect(screen.getByTestId('placeholder-a2')).toBeTruthy()
  expect(screen.getByTestId('placeholder-a3')).toBeTruthy()
  expect(screen.getByText('Data Bot — Thinking')).toBeTruthy()
})

it('does NOT render placeholders for non-active turns', () => {
  const turn = makeTurn({ status: 'completed' })
  const pendingAgents = [{ agentId: 'a2', agentName: 'Bot' }]
  render(<MemoizedTurn turn={turn} index={0} isActive={false} pendingAgents={pendingAgents} />)
  expect(screen.queryByTestId('placeholder-a2')).toBeNull()
})

it('renders SupervisorHeader when isSupervisorTurn=true and expanded', () => {
  const turn = makeTurn({ isSupervisorTurn: true })
  render(<MemoizedTurn turn={turn} index={0} isActive={true} />)
  expect(screen.getByTestId('supervisor-header')).toBeTruthy()
})

it('does NOT render SupervisorHeader when isSupervisorTurn=false', () => {
  const turn = makeTurn({ isSupervisorTurn: false })
  render(<MemoizedTurn turn={turn} index={0} isActive={true} />)
  expect(screen.queryByTestId('supervisor-header')).toBeNull()
})

it('hides SummaryBlock in expanded state', () => {
  const turn = makeTurn({
    summary: {
      sourceAgentId: 'agent-1',
      sourceAgentName: 'Bot',
      title: 'Summary title',
      body: 'Summary body',
    },
  })
  // Active turn is always expanded
  render(<MemoizedTurn turn={turn} index={0} isActive={true} />)
  expect(screen.queryByTestId('turn-summary')).toBeNull()
})

it('shows SummaryBlock in collapsed state', () => {
  const turn = makeTurn({
    summary: {
      sourceAgentId: 'agent-1',
      sourceAgentName: 'Bot',
      title: 'Summary title',
      body: 'Summary body',
    },
  })
  render(<MemoizedTurn turn={turn} index={0} isActive={false} />)
  expect(screen.getByTestId('turn-summary')).toBeTruthy()
})

it('does NOT render TurnEventTimeline even when events are present', () => {
  // Regression guard: TurnEventTimeline renders elements with data-testid
  // "live-dot" and "show-process-toggle". If the event rail is accidentally
  // re-added, these testids will appear and this test will catch it.
  const turn = makeTurn({
    events: [{
      id: 'e1', kind: 'agent_started', timestamp: '2026-01-01T00:00:00Z',
      agentId: 'a1', agentName: 'Bot', label: 'Started', isLive: false, isHiddenInCompact: false,
    }],
  })
  render(<MemoizedTurn turn={turn} index={0} isActive={true} />)
  // TurnEventTimeline's characteristic UI elements must be absent
  expect(screen.queryByTestId('live-dot')).toBeNull()
  expect(screen.queryByTestId('show-process-toggle')).toBeNull()
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm run test -- tests/unit/components/conversation-turn.test.tsx --reporter=verbose`
Expected: New tests FAIL

- [ ] **Step 3: Update conversation-turn.tsx**

Major changes:
1. Add imports for new components
2. New `pendingAgents` prop
3. Render `SupervisorHeader` when `isSupervisorTurn`
4. Remove `TurnEventTimeline` rendering
5. Hide `SummaryBlock` in expanded state
6. Render `AgentPlaceholderRow` for pending agents

Replace `src/components/conversation-turn.tsx`:

```tsx
// src/components/conversation-turn.tsx
'use client'

import React, { useState, useCallback, useEffect } from 'react'
import { cn } from '@/lib/utils'
import { AlertTriangle, ChevronRight } from 'lucide-react'
import { AgentBadge } from './agent-badge'
import { AgentResultStack } from './agent-result-stack'
import { AgentPlaceholderRow } from './agent-placeholder-row'
import { SupervisorHeader } from './supervisor-header'
import type { TurnViewModel } from '@/lib/room-timeline/types'
import { LinkifiedContent } from './markdown-content'
import { UserAttachmentCard } from './message-bubble'
import type { QuoteData } from './message-bubble'

// -- User prompt block -------------------------------------------------------

function UserPromptBlock({
  content,
  attachments,
}: {
  content: string
  attachments: TurnViewModel['userAttachments']
}) {
  if (!content && (!attachments || attachments.length === 0)) return null

  return (
    <div className="flex justify-end w-full" data-testid="user-prompt-wrapper">
      <div className="max-w-[80%] space-y-2 rounded-xl p-4 shadow-sm bg-secondary text-secondary-foreground">
        {content && (
          <div className="text-sm font-medium leading-relaxed whitespace-pre-wrap break-words">
            <LinkifiedContent content={content} />
          </div>
        )}
        {attachments && attachments.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {attachments.map((att) => (
              <UserAttachmentCard key={att.fileId} attachment={att} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// -- Summary block -----------------------------------------------------------

function SummaryBlock({ summary }: { summary: TurnViewModel['summary'] }) {
  if (!summary) return null

  return (
    <div className="mt-2 space-y-1" data-testid="turn-summary">
      <div className="flex items-center gap-2">
        <AgentBadge
          agentId={summary.sourceAgentId}
          agentName={summary.sourceAgentName}
          size="sm"
          hideSource
          showDeletedIndicator={false}
        />
      </div>
      <p className="text-base font-semibold text-foreground leading-snug">
        {summary.title}
      </p>
      <p className="text-sm text-muted-foreground line-clamp-3">
        {summary.body}
      </p>
    </div>
  )
}

// -- Warning line for failed turns -------------------------------------------

function FailedWarning() {
  return (
    <div className="flex items-center gap-1.5 text-xs text-destructive mt-1">
      <AlertTriangle className="h-3.5 w-3.5" />
      <span>One or more agents failed in this turn</span>
    </div>
  )
}

// -- Main component ----------------------------------------------------------

interface ConversationTurnProps {
  turn: TurnViewModel
  index: number
  isActive: boolean
  pendingAgents?: { agentId: string; agentName: string }[]
  onQuote?: (data: QuoteData) => void
}

function ConversationTurn({ turn, index, isActive, pendingAgents, onQuote }: ConversationTurnProps) {
  const [isExpanded, setIsExpanded] = useState(isActive)

  useEffect(() => {
    if (!isActive) {
      setIsExpanded(false)
    }
  }, [isActive])

  const handleToggle = useCallback(() => {
    if (!isActive) {
      setIsExpanded(prev => !prev)
    }
  }, [isActive])

  const showExpanded = isActive || isExpanded

  const promptPreview = turn.userContent
    ? turn.userContent.slice(0, 50) + (turn.userContent.length > 50 ? '...' : '')
    : 'System turn'

  // Supervisor header data
  const isCompleted = turn.status === 'completed' || turn.status === 'partial' || turn.status === 'failed'

  return (
    <article
      className="space-y-4"
      aria-label={`Turn ${index + 1}: ${promptPreview}`}
    >
      {/* User prompt */}
      <div
        className={cn(
          'cursor-default rounded-sm',
          !isActive && !showExpanded && 'cursor-pointer focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2',
        )}
        onClick={!isActive && !showExpanded ? handleToggle : undefined}
        role={!isActive && !showExpanded ? 'button' : undefined}
        tabIndex={!isActive && !showExpanded ? 0 : undefined}
        onKeyDown={
          !isActive && !showExpanded
            ? (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  handleToggle()
                }
              }
            : undefined
        }
      >
        <UserPromptBlock
          content={turn.userContent}
          attachments={turn.userAttachments}
        />
      </div>

      {/* Collapsed state: summary + failed warning */}
      {!showExpanded && (
        <>
          <SummaryBlock summary={turn.summary} />
          {(turn.status === 'failed' || turn.status === 'partial') && (
            <FailedWarning />
          )}
          {turn.agentResults.length > 0 && !turn.summary && (
            <button
              type="button"
              onClick={handleToggle}
              className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2 rounded-sm"
              aria-label={`Expand turn: ${turn.agentResults.length} agent${turn.agentResults.length !== 1 ? 's' : ''} responded`}
            >
              <ChevronRight className="h-3 w-3" />
              <span>
                {turn.agentResults.length} agent{turn.agentResults.length !== 1 ? 's' : ''} responded
              </span>
            </button>
          )}
        </>
      )}

      {/* Expanded state: supervisor header + agent results + placeholders */}
      {showExpanded && (
        <>
          {/* Supervisor header (V2) */}
          {turn.isSupervisorTurn && (
            <SupervisorHeader
              isCompleted={isCompleted}
              stepNumber={turn.supervisorStage?.stepNumber}
              totalSteps={turn.supervisorStage?.totalSteps}
              details={turn.supervisorStage?.details}
              agentCount={turn.agentResults.length}
              totalDurationMs={turn.agentResults.reduce((sum, r) => sum + (r.durationMs ?? 0), 0) || undefined}
            />
          )}

          {/* Failed warning */}
          {(turn.status === 'failed' || turn.status === 'partial') && (
            <FailedWarning />
          )}

          {/* Agent result stack */}
          <AgentResultStack
            results={turn.agentResults}
            summary={turn.summary}
            onQuote={onQuote}
          />

          {/* Placeholder rows for pending agents (active turn only) */}
          {isActive && pendingAgents && pendingAgents.length > 0 && (
            <div>
              {pendingAgents.map((agent) => (
                <AgentPlaceholderRow
                  key={agent.agentId}
                  agentId={agent.agentId}
                  agentName={agent.agentName}
                />
              ))}
            </div>
          )}

          {/* Collapse button for non-active expanded turns */}
          {!isActive && (
            <button
              type="button"
              onClick={handleToggle}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2 rounded-sm"
              aria-label="Collapse turn"
            >
              Collapse
            </button>
          )}
        </>
      )}
    </article>
  )
}

export const MemoizedTurn = React.memo(ConversationTurn)
```

Key changes from original:
- Removed `TurnEventTimeline` import and rendering
- Added `AgentPlaceholderRow` and `SupervisorHeader` imports + rendering
- New `pendingAgents` prop
- Summary hidden in expanded state (only shown in collapsed state)
- SupervisorHeader rendered when `turn.isSupervisorTurn`

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm run test -- tests/unit/components/conversation-turn.test.tsx --reporter=verbose`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/components/conversation-turn.tsx tests/unit/components/conversation-turn.test.tsx
git commit -m "feat: conversation-turn V2 — supervisor header, placeholders, hide summary when expanded"
```

---

### Task 8: Prop threading — conversation-timeline.tsx + room-messages.tsx + page.tsx

**Files:**
- Modify: `src/components/conversation-timeline.tsx`
- Modify: `src/components/room-messages.tsx`
- Modify: `src/app/c/room/[id]/page.tsx`

- [ ] **Step 1: Update conversation-timeline.tsx**

Add `roomAgentList` prop and compute `pendingAgents`:

Change interface:
```tsx
interface ConversationTimelineProps {
  roomAgentList?: { agentId: string; agentName: string }[]
  onQuote?: (data: QuoteData) => void
}
```

Update function signature:
```tsx
export function ConversationTimeline({ roomAgentList, onQuote }: ConversationTimelineProps) {
```

Add a constant for empty array (outside component, at module level):
```tsx
const EMPTY_AGENTS: { agentId: string; agentName: string }[] = []
```

In the render, replace the `MemoizedTurn` call (lines 220-225):

```tsx
{turns.map((turn, index) => {
  const isLastTurn = index === turns.length - 1
  const pendingAgents = isLastTurn && roomAgentList
    ? roomAgentList.filter(a => !turn.agentResults.some(r => r.agentId === a.agentId))
    : EMPTY_AGENTS
  return (
    <React.Fragment key={turn.id}>
      {index > 0 && (
        <div
          className="h-px bg-border/50 mx-4"
          role="separator"
          aria-hidden="true"
        />
      )}
      <MemoizedTurn
        turn={turn}
        index={index}
        isActive={isLastTurn}
        pendingAgents={pendingAgents}
        onQuote={onQuote}
      />
    </React.Fragment>
  )
})}
```

- [ ] **Step 2: Update room-messages.tsx**

Thread `roomAgentList` prop:

```tsx
'use client'

import { ConversationTimeline } from './conversation-timeline'
import type { QuoteData } from './message-bubble'

interface RoomMessagesProps {
  roomAgentList?: { agentId: string; agentName: string }[]
  onQuote?: (data: QuoteData) => void
}

export function RoomMessages({ roomAgentList, onQuote }: RoomMessagesProps) {
  return <ConversationTimeline roomAgentList={roomAgentList} onQuote={onQuote} />
}
```

- [ ] **Step 3: Update page.tsx**

In `src/app/c/room/[id]/page.tsx`, find the `<RoomMessages>` usage (line 450-452) and add the prop.

First, the page needs to compute `roomAgentList`. Find where `agentList` is already computed (should be from `useRoomData`). The existing code at line ~467 shows `agents={agentList}` being passed to `RoomChatInput`, so `agentList` is already available.

Check the shape: `getAgentList()` returns `{ id: name }[]` mapped as `{ id: string; name: string }[]`.

The prop expects `{ agentId: string; agentName: string }[]`, so add a mapping:

```tsx
const roomAgentList = useMemo(
  () => agentList.map(a => ({ agentId: a.id, agentName: a.name })),
  [agentList],
)
```

Add `useMemo` to the React import if not already there.

Pass to `RoomMessages`:
```tsx
<RoomMessages
  roomAgentList={roomAgentList}
  onQuote={handleQuote}
/>
```

- [ ] **Step 4: Run full test suite**

Run: `npm run test`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/components/conversation-timeline.tsx src/components/room-messages.tsx src/app/c/room/[id]/page.tsx
git commit -m "feat: thread roomAgentList prop for placeholder agent rows"
```

---

## Execution Order

```
Task 1 (foundation: system-agents, types, CSS)
    ↓
Task 2 (build-turns data model)
    ↓
Task 3 (leaf components: InlineChips, HitlCompactCard, HitlQuestionCard)
    ↓  (can run in parallel with Task 4)
Task 4 (AgentPlaceholderRow, SupervisorHeader)
    ↓
Task 5 (agent-badge avatar + summary)
    ↓
Task 6 (agent-result-card + agent-result-stack rewrite)
    ↓
Task 7 (conversation-turn layout)
    ↓
Task 8 (prop threading: timeline + room-messages + page)
```

Tasks 1→2 are strict dependencies (types must exist before build-turns). Tasks 3 and 4 are independent leaf components. Tasks 5-8 must be sequential (each depends on prior).

---

## Verification

After all tasks complete:

1. **Run full test suite**: `npm run test`
2. **Run dev server**: `npm run dev`
3. **Visual checks in browser** (navigate to a room with active agents):
   - Agents show 28×28 rounded-square avatars (dicebear bottts)
   - Agent names are `text-base font-semibold`
   - Shimmer "Thinking" for agents that haven't returned content
   - Shimmer "Generating" for agents with streaming content
   - Yellow shimmer "Needs input" for unanswered HITL
   - Resolved HITL shows compact card (truncated question + green-dot answer)
   - Active HITL shows question card with yellow border
   - Inline chips ("4 steps", "3.2s") appear next to agent names
   - No event rail / TurnEventTimeline visible
   - Summary visible in collapsed turns, hidden in expanded
   - Supervisor mode: HYBRO AI header with brand gradient text + stage shimmer
   - Completed supervisor: static stats ("3 agents · 12.4s")
   - Summary from HYBRO AI: brand gradient name, HYBRO favicon avatar
   - Placeholder rows appear after sending, disappear as results arrive
   - `supervisor_hitl` does NOT get summary visual treatment
   - Non-active turns collapse correctly, expand on click
4. **HITL flow verification**:
   - Trigger agent HITL → yellow shimmer + question card
   - Answer HITL → compact card appears, status changes to "Generating"
   - Agent completes → shimmer disappears, full content displayed
5. **Supervisor flow verification**:
   - Send message in supervisor room → HYBRO AI header appears
   - Stage shimmer shows "Step N of M · Dispatching agents..."
   - On completion → static stats replace shimmer
   - Summary agent shows "Summary from HYBRO AI" with brand gradient
