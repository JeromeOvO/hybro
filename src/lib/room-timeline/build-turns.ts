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
  const agentResults = scaffold.agentMessageIds
    .map((id) => buildAgentResult(entities[id]))
    .filter((r): r is AgentResultViewModel => r !== null)

  const status = deriveTurnStatus(agentResults)
  const summary = selectSummary(agentResults)
  const activeAgentIds = agentResults
    .filter((r) => r.status !== 'completed' && r.status !== 'failed')
    .map((r) => r.agentId)
    .filter((id): id is string => id !== undefined)

  // Filter events that belong to this turn (by timestamp range)
  const turnEvents = filterEventsForTurn(scaffold, entities, events)

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
  }
}

// ── Agent result construction ──────────────────────────────────

function buildAgentResult(entity: MessageEntity | undefined): AgentResultViewModel | null {
  if (!entity) return null

  let status: AgentResultViewModel['status'] = 'completed'
  if (entity.taskStatus && isFailureState(entity.taskStatus)) {
    status = 'failed'
  } else if (entity.taskStatus && isInteractiveState(entity.taskStatus)) {
    status = 'awaiting_input'
  } else if (entity.taskStatus && !isTerminalState(entity.taskStatus)) {
    // Still processing — treat as awaiting_input for UI purposes
    status = 'awaiting_input'
  }

  // Build HITL history from resolved prompts
  const hitlHistory: { prompt: string; answer: string }[] = []
  if (entity.hitlPrompt && entity.hitlUserAnswer && entity.hitlResolved) {
    hitlHistory.push({
      prompt: entity.hitlPrompt,
      answer: entity.hitlUserAnswer,
    })
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
  }
}

// ── Turn status derivation ─────────────────────────────────────

function deriveTurnStatus(agentResults: AgentResultViewModel[]): TurnStatus {
  if (agentResults.length === 0) return 'active'

  const hasActive = agentResults.some((r) => r.status === 'awaiting_input')
  const hasFailed = agentResults.some((r) => r.status === 'failed')
  const hasCompleted = agentResults.some((r) => r.status === 'completed')
  const allFailed = agentResults.every((r) => r.status === 'failed')
  const allCompleted = agentResults.every((r) => r.status === 'completed')

  if (hasActive) return agentResults.some((r) => r.status === 'awaiting_input' && r.content === '') ? 'active' : 'awaiting_input'
  if (allFailed) return 'failed'
  if (allCompleted) return 'completed'
  if (hasCompleted && hasFailed) return 'partial'

  return 'active'
}

// ── Summary selection ──────────────────────────────────────────

/**
 * Select the best agent result as the turn summary.
 * Priority:
 *   1. Supervisor result (agentName contains 'supervisor' case-insensitive)
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

  // Priority 1: supervisor result
  const supervisor = completedWithContent.find((r) =>
    r.agentName.toLowerCase().includes('supervisor'),
  )
  if (supervisor) return buildSummaryFromResult(supervisor)

  // Priority 2: first completed with content (highest priority by ordering)
  const first = completedWithContent[0]
  return buildSummaryFromResult(first)
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
  // For now, return an empty array. Events will be wired in Phase 3
  // when SSE handler captures events into the event-log.
  // This placeholder allows the turn builder to compile and pass tests.
  return []
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

  // Check that all agent message IDs match
  for (let i = 0; i < a.agentResults.length; i++) {
    if (a.agentResults[i].messageId !== b.agentResults[i].messageId) return false
    if (a.agentResults[i].status !== b.agentResults[i].status) return false
    if (a.agentResults[i].content !== b.agentResults[i].content) return false
  }

  return true
}
