// src/lib/room-timeline/build-turns.ts

import type { MessageEntity } from '@/stores/message-store/types'
import type {
  TurnViewModel,
  TurnStatus,
  AgentResultViewModel,
  TurnSummaryViewModel,
  TimelineEventViewModel,
  RawTimelineEvent,
} from './types'
import { isTerminalState, isFailureState, isInteractiveState } from '@/lib/types/sse'
import type { TaskState } from '@/lib/types/sse'
import { isSystemAgent, isSupervisorSystemAgent, isSummarySystemAgent } from '@/lib/system-agents'

// ── Constants ──────────────────────────────────────────────────

const SYSTEM_TURN_ID = 'system-turn'

// ── Core turn construction ─────────────────────────────────────

/**
 * Build an ordered list of TurnViewModels from the message store state.
 *
 * Turn boundary: each `messageType === 'user'` starts a new turn.
 * Agent messages route to a turn via:
 *   1. relatedMessageId (cross-turn routing)
 *   2. Most recent user turn before the agent message timestamp
 *
 * Agent messages before the first user message get a synthetic system turn.
 */
export function buildTurns(
  entities: Record<string, MessageEntity>,
  orderedIds: string[],
  events: readonly RawTimelineEvent[],
): TurnViewModel[] {
  if (orderedIds.length === 0) return []

  // Phase 1: identify user message boundaries (turn roots)
  const userMessageIds: string[] = []
  const userMessageIndexById = new Map<string, number>()

  for (const id of orderedIds) {
    const entity = entities[id]
    if (!entity) continue
    if (entity.messageType === 'user') {
      userMessageIndexById.set(id, userMessageIds.length)
      userMessageIds.push(id)
    }
  }

  // Phase 2: build turn scaffolds
  type TurnScaffold = {
    userMessageId: string | null
    userEntity: MessageEntity | null
    agentMessageIds: string[]
  }

  const turnScaffolds: TurnScaffold[] = []
  let systemTurnScaffold: TurnScaffold | null = null

  // Create one scaffold per user message
  for (const umId of userMessageIds) {
    turnScaffolds.push({
      userMessageId: umId,
      userEntity: entities[umId],
      agentMessageIds: [],
    })
  }

  // Phase 3: route agent messages to turns
  // Track user turn ordering for fallback timestamp routing
  let currentTurnIndex = -1

  for (const id of orderedIds) {
    const entity = entities[id]
    if (!entity) continue

    if (entity.messageType === 'user') {
      const idx = userMessageIndexById.get(id)
      if (idx !== undefined) currentTurnIndex = idx
      continue
    }

    // Agent message — find its turn
    const targetTurn = routeAgentToTurn(
      entity,
      turnScaffolds,
      userMessageIndexById,
      currentTurnIndex,
      entities,
    )

    if (targetTurn !== null) {
      turnScaffolds[targetTurn].agentMessageIds.push(id)
    } else {
      // No user turn found — synthetic system turn
      if (!systemTurnScaffold) {
        systemTurnScaffold = {
          userMessageId: null,
          userEntity: null,
          agentMessageIds: [],
        }
      }
      systemTurnScaffold.agentMessageIds.push(id)
    }
  }

  // Phase 4: assemble TurnViewModels
  const turns: TurnViewModel[] = []

  // System turn first (if any)
  if (systemTurnScaffold) {
    turns.push(assembleTurn(SYSTEM_TURN_ID, systemTurnScaffold, entities, events))
  }

  for (let i = 0; i < turnScaffolds.length; i++) {
    const scaffold = turnScaffolds[i]
    const turnId = scaffold.userMessageId ?? `turn-${i}`
    turns.push(assembleTurn(turnId, scaffold, entities, events))
  }

  return turns
}

// ── Agent-to-turn routing ──────────────────────────────────────

function routeAgentToTurn(
  agent: MessageEntity,
  scaffolds: Array<{ userMessageId: string | null }>,
  userMessageIndexById: Map<string, number>,
  currentTurnIndex: number,
  entities: Record<string, MessageEntity>,
): number | null {
  // Priority 1: relatedMessageId routing
  if (agent.relatedMessageId) {
    // Find which turn contains (or IS) the related message
    const directIdx = userMessageIndexById.get(agent.relatedMessageId)
    if (directIdx !== undefined) return directIdx

    // relatedMessageId might point to an agent message — find that agent's turn
    const relatedEntity = entities[agent.relatedMessageId]
    if (relatedEntity?.relatedMessageId) {
      const transIdx = userMessageIndexById.get(relatedEntity.relatedMessageId)
      if (transIdx !== undefined) return transIdx
    }
  }

  // Priority 2: current turn by ordering position
  if (currentTurnIndex >= 0) return currentTurnIndex

  // No user turn yet — will go to system turn
  return null
}

// ── Turn assembly ──────────────────────────────────────────────

function assembleTurn(
  turnId: string,
  scaffold: {
    userMessageId: string | null
    userEntity: MessageEntity | null
    agentMessageIds: string[]
  },
  entities: Record<string, MessageEntity>,
  events: readonly RawTimelineEvent[],
): TurnViewModel {
  // Filter events for this turn first (needed by buildAgentResult for inline chips)
  const turnEvents = filterEventsForTurn(scaffold, entities, events)

  const agentResults = scaffold.agentMessageIds
    .map((id) => buildAgentResult(entities[id], turnEvents))
    .filter((r): r is AgentResultViewModel => r !== null)

  const status = deriveTurnStatus(agentResults)
  const summary = selectSummary(agentResults)
  const activeAgentIds = agentResults
    .filter((r) => r.status !== 'completed' && r.status !== 'failed')
    .map((r) => r.agentId)
    .filter((id): id is string => id !== undefined)

  // Supervisor detection (spec §5.2)
  const isSupervisorTurn = agentResults.some(r => isSupervisorSystemAgent(r.agentId))

  // Supervisor stage from latest entity with step/stage data.
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
}

// ── Agent result construction ──────────────────────────────────

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

// ── Turn status derivation ─────────────────────────────────────

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

// ── Summary selection ──────────────────────────────────────────

/**
 * Select the best agent result as the turn summary.
 * Priority:
 *   1. System summary agent (supervisor_synthesis, debate_summary, etc.)
 *   2. Highest-priority completed agent (first completed with content)
 *   3. Latest completed non-empty agent
 * Returns null if no agent has completed with content.
 */
export function selectSummary(
  agentResults: AgentResultViewModel[],
): TurnSummaryViewModel | null {
  const completedWithContent = agentResults.filter(
    (r) => r.status === 'completed' && r.content.trim().length > 0,
  )

  if (completedWithContent.length === 0) return null

  // Priority 1: system summary agent
  const systemSummary = completedWithContent.find((r) =>
    isSummarySystemAgent(r.agentId),
  )
  if (systemSummary) return buildSummaryFromResult(systemSummary)

  // Priority 2: first completed with content, excluding non-summary system agents
  // (e.g. supervisor_hitl produces HITL question text, not meaningful summaries)
  const nonSystemOrSummary = completedWithContent.filter(
    (r) => !isSystemAgent(r.agentId) || isSummarySystemAgent(r.agentId),
  )
  const first = nonSystemOrSummary[0] ?? completedWithContent[0]
  return first ? buildSummaryFromResult(first) : null
}

function buildSummaryFromResult(
  result: AgentResultViewModel,
): TurnSummaryViewModel {
  const lines = result.content.trim().split('\n')
  // Title: first non-empty line (strip markdown heading chars)
  let title = (lines[0] ?? '').replace(/^#{1,6}\s*/, '').trim()
  if (title.length > 80) title = title.slice(0, 77) + '...'

  // Body: remaining lines
  const body = lines.slice(1).join('\n').trim() || result.content.trim()

  return {
    sourceAgentId: result.agentId,
    sourceAgentName: result.agentName,
    title: title || result.agentName,
    body,
  }
}

// ── Event filtering ────────────────────────────────────────────

function filterEventsForTurn(
  scaffold: {
    userMessageId: string | null
    agentMessageIds: string[]
  },
  entities: Record<string, MessageEntity>,
  events: readonly RawTimelineEvent[],
): TimelineEventViewModel[] {
  if (events.length === 0) return []

  // Collect agentIds that belong to this turn
  const turnAgentIds = new Set<string>()
  for (const id of scaffold.agentMessageIds) {
    const entity = entities[id]
    if (entity?.agentId) turnAgentIds.add(entity.agentId)
  }

  // Determine time boundaries for this turn
  const userEntity = scaffold.userMessageId ? entities[scaffold.userMessageId] : null
  const turnStart = userEntity?.timestamp ?? ''

  // Filter events that belong to this turn's agents within the time window
  const filtered: TimelineEventViewModel[] = []
  let counter = 0

  for (const raw of events) {
    // Must belong to one of this turn's agents (or be a user_prompt matching the turn)
    if (raw.kind === 'user_prompt') {
      if (scaffold.userMessageId && raw.timestamp === turnStart) {
        filtered.push(rawToViewModel(raw, `${scaffold.userMessageId}-evt-${counter++}`))
      }
      continue
    }

    // Agent events: match by agentId
    if (raw.agentId && turnAgentIds.has(raw.agentId)) {
      // Only include events after the turn start (or if no start, include all)
      if (!turnStart || raw.timestamp >= turnStart) {
        filtered.push(rawToViewModel(raw, `evt-${raw.agentId}-${counter++}`))
      }
    }
  }

  return filtered
}

function rawToViewModel(raw: RawTimelineEvent, id: string): TimelineEventViewModel {
  return {
    id,
    kind: raw.kind,
    timestamp: raw.timestamp,
    agentId: raw.agentId,
    agentName: raw.agentName,
    label: raw.label,
    body: raw.body,
    artifactPayload: raw.artifactPayload,
    hitlPayload: raw.hitlPayload,
    isLive: false,
    isHiddenInCompact: raw.kind === 'agent_progress',
  }
}

// ── Incremental derivation ─────────────────────────────────────

/**
 * Incrementally rebuild turns: only rebuilds the active (last) turn.
 * Older turns maintain referential identity so React.memo skips re-render.
 *
 * When a relatedMessageId points to an older turn, that specific turn
 * is rebuilt individually.
 */
export function buildTurnsIncremental(
  prevTurns: TurnViewModel[],
  entities: Record<string, MessageEntity>,
  orderedIds: string[],
  events: readonly RawTimelineEvent[],
): TurnViewModel[] {
  // If no previous turns, delegate to full build
  if (prevTurns.length === 0) {
    return buildTurns(entities, orderedIds, events)
  }

  // Full rebuild to get the "truth"
  const fullTurns = buildTurns(entities, orderedIds, events)

  if (fullTurns.length === 0) return fullTurns

  // If turn count changed, we need a new user message — return full rebuild
  // but preserve referential identity for unchanged older turns
  const result: TurnViewModel[] = []

  for (let i = 0; i < fullTurns.length; i++) {
    const newTurn = fullTurns[i]
    const prevTurn = i < prevTurns.length ? prevTurns[i] : null

    if (prevTurn && turnsAreEqual(prevTurn, newTurn)) {
      // Preserve referential identity
      result.push(prevTurn)
    } else {
      result.push(newTurn)
    }
  }

  return result
}

/**
 * Shallow equality check for turn identity preservation.
 * Checks structural equality without deep-comparing content.
 */
function turnsAreEqual(a: TurnViewModel, b: TurnViewModel): boolean {
  if (a.id !== b.id) return false
  if (a.status !== b.status) return false
  if (a.agentResults.length !== b.agentResults.length) return false
  if (a.userContent !== b.userContent) return false

  // V2 TurnViewModel fields
  if (a.isSupervisorTurn !== b.isSupervisorTurn) return false
  if (a.supervisorStage?.stepNumber !== b.supervisorStage?.stepNumber) return false
  if (a.supervisorStage?.totalSteps !== b.supervisorStage?.totalSteps) return false
  if (a.supervisorStage?.details !== b.supervisorStage?.details) return false

  // Check that all agent results match (including artifacts)
  for (let i = 0; i < a.agentResults.length; i++) {
    if (a.agentResults[i].messageId !== b.agentResults[i].messageId) return false
    if (a.agentResults[i].status !== b.agentResults[i].status) return false
    if (a.agentResults[i].content !== b.agentResults[i].content) return false
    if (a.agentResults[i].artifacts.length !== b.agentResults[i].artifacts.length) return false
    for (let j = 0; j < a.agentResults[i].artifacts.length; j++) {
      if (a.agentResults[i].artifacts[j].artifactId !== b.agentResults[i].artifacts[j].artifactId) return false
      if (a.agentResults[i].artifacts[j].isStreaming !== b.agentResults[i].artifacts[j].isStreaming) return false
      if (a.agentResults[i].artifacts[j].parts.length !== b.agentResults[i].artifacts[j].parts.length) return false
    }
    // V2 AgentResultViewModel fields
    if (a.agentResults[i].hitlResolved?.prompt !== b.agentResults[i].hitlResolved?.prompt) return false
    if (a.agentResults[i].hitlResolved?.answer !== b.agentResults[i].hitlResolved?.answer) return false
    if (a.agentResults[i].hitlPending?.prompt !== b.agentResults[i].hitlPending?.prompt) return false
    if (a.agentResults[i].eventCount !== b.agentResults[i].eventCount) return false
    if (a.agentResults[i].durationMs !== b.agentResults[i].durationMs) return false
  }

  // Check events count changed
  if (a.events.length !== b.events.length) return false

  // Summary equality
  if ((a.summary?.sourceAgentId ?? null) !== (b.summary?.sourceAgentId ?? null)) return false
  if ((a.summary?.title ?? '') !== (b.summary?.title ?? '')) return false
  if ((a.summary?.body ?? '') !== (b.summary?.body ?? '')) return false

  return true
}
