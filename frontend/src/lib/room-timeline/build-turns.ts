// src/lib/room-timeline/build-turns.ts

import type { MessageEntity } from '@/stores/message-store/types'
import type {
  TurnViewModel,
  TurnStatus,
  TurnPhase,
  AgentResultViewModel,
  TurnSummaryViewModel,
  TimelineEventViewModel,
  RawTimelineEvent,
} from './types'
import { isTerminalState, isFailureState, isInteractiveState } from '@/lib/types/sse'
import { isSystemAgent, isSupervisorSystemAgent, isSummarySystemAgent, isSupervisorClarifyAgent } from '@/lib/system-agents'
import {
  deriveDisplayModeFromFinalAnswer,
  deriveFinalAnswer,
  derivePrimaryStreamFromFinalAnswer,
} from './derive-final-answer'
import {
  hasActiveSynthesisGap,
  shouldHoldTurnActiveForOpenRoomRun,
  shouldShowSynthesizingPhase,
  shouldShowSynthesizingPhaseForResults,
  type OpenRoomRunContext,
} from './multi-agent-turn-complete'
import { getStripSourceResults } from './turn-live-shell'

// ── Constants ──────────────────────────────────────────────────

const SYSTEM_TURN_ID = 'system-turn'

export interface TurnBuildOptions {
  roomProcessingActive?: boolean
  activeRunTriggerMessageIds?: ReadonlySet<string>
}

function publicAgentStatusMessage(
  entity: MessageEntity,
  status: AgentResultViewModel['status'],
): string | null | undefined {
  const taskStatusMessage = typeof entity.taskStatusMessage === 'string'
    ? entity.taskStatusMessage.trim()
    : ''
  if (taskStatusMessage.length > 0) return taskStatusMessage

  if (status === 'working') {
    if (entity.content.trim().length === 0 && entity.taskStatus === 'working') return 'Working'
  }

  return entity.taskStatusMessage
}

function publicSupervisorStageDetails(entity: MessageEntity): string | undefined {
  const taskStatusMessage = typeof entity.taskStatusMessage === 'string'
    ? entity.taskStatusMessage.trim()
    : ''
  if (taskStatusMessage.length > 0) return taskStatusMessage

  return entity.taskStatus === 'working' ? 'Working' : undefined
}

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
  options: TurnBuildOptions = {},
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
    turns.push(assembleTurn(SYSTEM_TURN_ID, systemTurnScaffold, entities, events, options))
  }

  for (let i = 0; i < turnScaffolds.length; i++) {
    const scaffold = turnScaffolds[i]
    const turnId = scaffold.userMessageId ?? `turn-${i}`
    // Pass the next turn's user message timestamp as an upper bound for event filtering.
    // This prevents events from turn N+1 leaking into turn N when the same agent appears
    // in both turns.
    const nextScaffold = turnScaffolds[i + 1]
    const nextTurnStart = nextScaffold?.userEntity?.timestamp
    turns.push(assembleTurn(
      turnId,
      scaffold,
      entities,
      events,
      options,
      {
        nextTurnStart,
        isLatestUserTurn: i === turnScaffolds.length - 1,
      },
    ))
  }

  return turns
}

// ── Agent-to-turn routing ──────────────────────────────────────

function routeAgentToTurn(
  agent: MessageEntity,
  scaffolds: Array<{ userMessageId: string | null; userEntity?: MessageEntity | null }>,
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

  // Priority 2: clientRequestId correlation (defensive hardening)
  if (agent.clientRequestId) {
    for (let i = 0; i < scaffolds.length; i++) {
      const userEntity = scaffolds[i].userEntity ?? (scaffolds[i].userMessageId ? entities[scaffolds[i].userMessageId!] : null)
      if (userEntity?.clientRequestId === agent.clientRequestId) return i
    }
  }

  // Priority 3: current turn by ordering position
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
  buildOptions: TurnBuildOptions = {},
  assembleOptions: {
    nextTurnStart?: string
    isLatestUserTurn?: boolean
  } = {},
): TurnViewModel {
  const { nextTurnStart, isLatestUserTurn = false } = assembleOptions
  const openRoomRunContext: OpenRoomRunContext = {
    userMessageId: scaffold.userMessageId,
    turnTerminalStatus: scaffold.userEntity?.turnTerminalStatus,
    turnCompletionKind: scaffold.userEntity?.turnCompletionKind,
    processingStatusLogs: scaffold.userEntity?.processingStatusLogs ?? [],
    activeRunTriggerMessageIds: buildOptions.activeRunTriggerMessageIds,
    roomProcessingActive: buildOptions.roomProcessingActive,
    isLatestUserTurn,
  }

  // Filter events for this turn first (needed by buildAgentResult for inline chips)
  const turnEvents = filterEventsForTurn(scaffold, entities, events, nextTurnStart)

  const rawAgentResults = scaffold.agentMessageIds
    .map((id) =>
      buildAgentResult(entities[id], turnEvents, scaffold.userEntity?.turnTerminalStatus),
    )
    .filter((r): r is AgentResultViewModel => r !== null)

  // Deduplicate: when both task_update and agent_response SSE events create
  // separate entities for the same agentId, keep the one with the most content
  // (or terminal status). This avoids rendering the same agent response twice.
  const dedupedResults = deduplicateAgentResults(rawAgentResults)
  const agentResults = suppressEphemeralResults(dedupedResults, entities, scaffold.userEntity)

  // Supervisor detection (spec §5.2)
  // Check against all results (including suppressed ephemerals) to preserve supervisor turn
  // status during pre-synthesis gap (anti-flashing).
  const hasSupervisorProcessingLogs =
    hasSupervisorContinuationLog(scaffold.userEntity?.processingStatusLogs)
    && !isPersistedTerminalTurn(scaffold.userEntity?.turnTerminalStatus)
  const isSupervisorTurn =
    dedupedResults.some(r => isSupervisorSystemAgent(r.agentId))
    || hasSupervisorProcessingLogs

  const status = deriveTurnStatus(agentResults, {
    isSupervisorTurn,
    turnTerminalStatus: scaffold.userEntity?.turnTerminalStatus,
    turnCompletionKind: scaffold.userEntity?.turnCompletionKind,
    processingStatusLogs: scaffold.userEntity?.processingStatusLogs ?? [],
    openRoomRunContext,
  })
  const summary = selectSummary(agentResults)
  const activeAgentIds = agentResults
    .filter((r) => r.status !== 'completed' && r.status !== 'failed')
    .map((r) => r.agentId)
    .filter((id): id is string => id !== undefined)

  // Supervisor stage from latest entity with step/stage data.
  let supervisorStage: TurnViewModel['supervisorStage']
  if (isSupervisorTurn) {
    for (let i = scaffold.agentMessageIds.length - 1; i >= 0; i--) {
      const e = entities[scaffold.agentMessageIds[i]]
      if (!e) continue
      const details = publicSupervisorStageDetails(e)
      if (e.stepNumber != null || e.totalSteps != null || details) {
        supervisorStage = {
          stepNumber: e.stepNumber,
          totalSteps: e.totalSteps,
          details,
        }
        break
      }
    }
  }

  const turn: TurnViewModel = {
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
    turnTerminalStatus: scaffold.userEntity?.turnTerminalStatus,
    turnCompletionKind: scaffold.userEntity?.turnCompletionKind,
    processingStatusLogs: scaffold.userEntity?.processingStatusLogs ?? [],
    displayMode: 'single_agent', // placeholder, set below
    finalAnswer: { kind: 'pending', label: 'Working' }, // placeholder
    liveRunBuildContext: {
      roomProcessingActive: buildOptions.roomProcessingActive,
      isLatestUserTurn,
      activeRunTriggerMessageIds: buildOptions.activeRunTriggerMessageIds,
    },
  }

  turn.finalAnswer = deriveFinalAnswer(turn, scaffold.agentMessageIds)
  turn.displayMode = deriveDisplayModeFromFinalAnswer(turn, turn.finalAnswer)
  turn.phase = deriveTurnPhase(turn, openRoomRunContext)
  turn.primaryStreamMessageId = derivePrimaryStreamFromFinalAnswer(turn.finalAnswer)
  turn.primaryMessageId = turn.primaryStreamMessageId

  return turn
}

function hasSupervisorContinuationLog(
  logs: MessageEntity['processingStatusLogs'] | undefined,
): boolean {
  if (!logs?.length) return false
  return logs.some((entry) => {
    const message = entry.message.toLowerCase()
    return (
      message.includes('evaluating agent results')
      || message.includes('synthesiz')
      || message.includes('orchestrat')
      || message.includes('hybro')
    )
  })
}

function isPersistedTerminalTurn(
  status: TurnViewModel['turnTerminalStatus'] | undefined,
): boolean {
  return status === 'completed' || status === 'failed' || status === 'canceled'
}

// ── Agent result construction ──────────────────────────────────

function buildAgentResult(
  entity: MessageEntity | undefined,
  turnEvents: RawTimelineEvent[],
  turnTerminalStatus?: TurnViewModel['turnTerminalStatus'],
): AgentResultViewModel | null {
  if (!entity) return null

  // Empty placeholder mapping
  if (entity.id.startsWith('empty-placeholder-')) {
    const status: AgentResultViewModel['status'] = 'working'
    return {
      agentId: entity.agentId ?? entity.id,
      agentName: entity.senderName,
      agentSource: entity.agentSource,
      messageId: entity.id,
      clientRequestId: entity.clientRequestId,
      status,
      content: '',
      artifacts: [],
      isSummaryAgent: isSummarySystemAgent(entity.agentId),
      taskStatusMessage: publicAgentStatusMessage(entity, status),
      isEphemeral: true,
    }
  }

  if (entity.isEphemeral) {
    const status: AgentResultViewModel['status'] = 'working'
    return {
      agentId: entity.agentId ?? entity.id,
      agentName: entity.senderName,
      agentSource: entity.agentSource,
      messageId: entity.id,
      clientRequestId: entity.clientRequestId,
      status,
      content: '',
      artifacts: [],
      isSummaryAgent: isSummarySystemAgent(entity.agentId),
      taskStatusMessage: publicAgentStatusMessage(entity, status),
      isEphemeral: true,
    }
  }

  // Status derivation (spec §5.4)
  let status: AgentResultViewModel['status'] = 'completed'
  const hitlAnswered = entity.hitlResolved === true || !!entity.hitlUserAnswer

  if (entity.taskStatus && isFailureState(entity.taskStatus)) {
    status = 'failed'
  } else if (entity.taskStatus && isInteractiveState(entity.taskStatus)) {
    if (hitlAnswered) {
      status = isSupervisorClarifyAgent(entity.agentId) ? 'completed' : 'working'
    } else if (entity.hitlRequestId) {
      status = 'awaiting_input'
    } else {
      // A remote agent may request input while the orchestrator is still
      // resolving it from existing context. Only a durable HITL request is
      // actionable by the user; keep the internal recovery state as working.
      status = 'working'
    }
  } else if (entity.taskStatus && !isTerminalState(entity.taskStatus)) {
    // Legacy rows: synthesis left system:hybro as submitted while message_text
    // already held the answer. Repair only after the turn is durably complete
    // so live streaming still shows synthesizing.
    if (
      isSummarySystemAgent(entity.agentId)
      && entity.content.trim().length > 0
      && turnTerminalStatus === 'completed'
    ) {
      status = 'completed'
    } else {
      status = 'working'
    }
  }

  // HITL split (spec §5.3)
  let hitlResolved: AgentResultViewModel['hitlResolved']
  let hitlPending: AgentResultViewModel['hitlPending']
  if (entity.hitlPrompt && entity.hitlUserAnswer) {
    hitlResolved = { prompt: entity.hitlPrompt, answer: entity.hitlUserAnswer }
  } else if (
    entity.hitlRequestId
    && entity.hitlPrompt
    && !hitlAnswered
    && entity.hitlResolved !== true
  ) {
    hitlPending = {
      prompt: entity.hitlPrompt,
      source: entity.hitlSource ?? 'agent',
    }
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
    clientRequestId: entity.clientRequestId,
    status,
    content: entity.content,
    artifacts: entity.artifacts ?? [],
    taskStatusMessage: publicAgentStatusMessage(entity, status),
    hitlHistory: hitlHistory.length > 0 ? hitlHistory : undefined,
    isSummaryAgent: isSummarySystemAgent(entity.agentId),
    summaryOrigin: entity.summaryOrigin,
    hitlResolved,
    hitlPending,
    eventCount,
    durationMs,
  }
}

// ── Agent result deduplication ─────────────────────────────────

/**
 * Deduplicate agent results with the same agentId within a turn.
 * This handles the case where both task_update and agent_response SSE events
 * create separate entities for the same agent (different message IDs) with
 * substantially the same content.
 *
 * Within a single turn, the same agentId can appear multiple times due to:
 * 1. SSE race conditions — agent_response + task_update/task_submitted create
 *    separate entities with the same or similar content. → Deduplicate.
 * 2. relatedMessageId routing — a late response routed to an older turn produces
 *    a second entity with genuinely different content. → Keep both.
 *
 * Heuristic: if content is identical, one side is empty, or both share the same
 * first 100 characters (same response, different completeness), it's a duplicate.
 * Genuinely different responses from relatedMessageId won't share the same prefix.
 */
function deduplicateAgentResults(results: AgentResultViewModel[]): AgentResultViewModel[] {
  const seen = new Map<string, AgentResultViewModel>()
  const output: AgentResultViewModel[] = []

  for (const r of results) {
    if (!r.agentId) {
      output.push(r)
      continue
    }

    const existing = seen.get(r.agentId)
    if (!existing) {
      seen.set(r.agentId, r)
      output.push(r)
      continue
    }

    // Check if this is an SSE duplicate or a genuinely different response
    const a = existing.content.trim()
    const b = r.content.trim()
    const prefixLen = Math.min(100, Math.min(a.length, b.length))
    const isDuplicate =
      a === b ||
      a.length === 0 ||
      b.length === 0 ||
      (prefixLen > 0 && a.slice(0, prefixLen) === b.slice(0, prefixLen))

    if (!isDuplicate) {
      // Genuinely different content (e.g. relatedMessageId late response)
      output.push(r)
      seen.set(r.agentId, r)
      continue
    }

    // SSE duplicate — pick the better result: terminal status > artifacts > longer content.
    const existingTerminal = existing.status === 'completed' || existing.status === 'failed'
    const incomingTerminal = r.status === 'completed' || r.status === 'failed'
    const existingHasArtifacts = existing.artifacts.length > 0
    const incomingHasArtifacts = r.artifacts.length > 0

    let winner: AgentResultViewModel
    if (existingTerminal && !incomingTerminal) {
      winner = existing
    } else if (!existingTerminal && incomingTerminal) {
      winner = r
    } else if (existingHasArtifacts && !incomingHasArtifacts) {
      winner = existing
    } else if (!existingHasArtifacts && incomingHasArtifacts) {
      winner = r
    } else {
      winner = r.content.length >= existing.content.length ? r : existing
    }

    if (winner !== existing) {
      const mergedWinner = {
        ...winner,
        // Preserve status hints regardless of which duplicate wins.
        taskStatusMessage: winner.taskStatusMessage || existing.taskStatusMessage || r.taskStatusMessage,
      }
      const idx = output.indexOf(existing)
      if (idx >= 0) output[idx] = mergedWinner
      seen.set(r.agentId, mergedWinner)
    } else if (!existing.taskStatusMessage && r.taskStatusMessage) {
      const enriched = { ...existing, taskStatusMessage: r.taskStatusMessage }
      const idx = output.indexOf(existing)
      if (idx >= 0) output[idx] = enriched
      seen.set(r.agentId, enriched)
    }
  }

  return output
}

// ── Ephemeral suppression ────────────────────────────────────

/** Ephemeral placeholder that bridges the gap before synthesis streaming starts. */
function isSynthesisGapEphemeral(result: AgentResultViewModel): boolean {
  if (result.isSummaryAgent && result.status === 'working') return true
  const stage = result.taskStatusMessage?.trim().toLowerCase() ?? ''
  return stage.includes('synthesiz')
}

function allRealAgentsTerminal(results: readonly AgentResultViewModel[]): boolean {
  const real = results.filter(r => !r.isEphemeral && !r.isSummaryAgent)
  if (real.length === 0) return false
  return real.every(r => r.status === 'completed' || r.status === 'failed')
}

/**
 * Suppress ephemeral placeholder results when real agents exist, unless the
 * placeholder bridges an active synthesis gap (summary agent working or
 * "Synthesizing..." stage text).
 */
function suppressEphemeralResults(
  results: AgentResultViewModel[],
  entities: Record<string, MessageEntity>,
  userEntity: MessageEntity | null,
): AgentResultViewModel[] {
  const terminalTurn = userEntity?.turnTerminalStatus
  if (terminalTurn === 'completed' || terminalTurn === 'failed' || terminalTurn === 'canceled') {
    return results.filter(r => !r.isEphemeral)
  }

  const clientReqIdsWithRealAgent = new Set<string>()
  const clientReqIdsWithWorkingAgent = new Set<string>()

  for (const r of results) {
    if (r.isEphemeral) continue
    const entity = entities[r.messageId]
    if (!entity?.clientRequestId) continue
    clientReqIdsWithRealAgent.add(entity.clientRequestId)
    if (r.status === 'working') {
      clientReqIdsWithWorkingAgent.add(entity.clientRequestId)
    }
  }

  const hasAnyRealAgent = results.some(r => !r.isEphemeral)
  const allRealTerminal = allRealAgentsTerminal(results)

  return results.filter((r) => {
    if (!r.isEphemeral) return true

    // DONE path: all agents finished, no synthesis in progress — drop Planning ephemerals.
    if (allRealTerminal && !isSynthesisGapEphemeral(r)) return false

    const entity = entities[r.messageId]
    const crId = entity?.clientRequestId

    if (!crId) {
      if (hasAnyRealAgent && !isSynthesisGapEphemeral(r)) return false
      return true
    }

    const hasRealAgent = clientReqIdsWithRealAgent.has(crId)
    const hasWorkingAgent = clientReqIdsWithWorkingAgent.has(crId)

    if (hasRealAgent && isSynthesisGapEphemeral(r) && !hasWorkingAgent) return true
    if (hasRealAgent) return false
    return true
  })
}

// ── Turn phase derivation ──────────────────────────────────────

export function deriveTurnPhase(
  turn: TurnViewModel,
  openRoomRunContext?: OpenRoomRunContext,
): TurnPhase {
  const resolvedOpenRoomRunContext: OpenRoomRunContext = {
    userMessageId: turn.userMessageId,
    turnTerminalStatus: turn.turnTerminalStatus,
    turnCompletionKind: turn.turnCompletionKind,
    processingStatusLogs: turn.processingStatusLogs,
    isSupervisorTurn: turn.isSupervisorTurn,
    ...turn.liveRunBuildContext,
    ...openRoomRunContext,
  }
  const orchestrator = turn.agentResults.find(r => r.agentId === "system:hybro")
  const real = getStripSourceResults(turn)
  const allRealTerminal =
    real.length > 0
    && real.every(r => r.status === "completed" || r.status === "failed")

  if (orchestrator) {
    if (turn.status === "completed" || turn.status === "failed" || turn.status === "partial") return "completed"
    if (turn.status === "awaiting_input" || turn.finalAnswer.kind === "hitl") return "collecting"
    
    if (real.length === 0) return "collecting"
    if (real.some(r => r.status === "working")) return "collecting"

    // Contentful orchestrator is already the final answer (incl. hydrate repair).
    if (
      orchestrator.content.trim().length > 0
      && orchestrator.summaryOrigin !== "deterministic"
      && (orchestrator.status === "completed" || turn.turnTerminalStatus === "completed")
    ) {
      return "completed"
    }
    
    if (orchestrator.status === "working") return "synthesizing"
    return "answering"
  }

  const summaryResult = turn.agentResults.find(r => r.isSummaryAgent)
  if (
    summaryResult?.status === 'working'
    || (allRealTerminal && hasActiveSynthesisGap(turn))
    || shouldShowSynthesizingPhase(turn, real, resolvedOpenRoomRunContext)
  ) {
    return 'synthesizing'
  }

  if (turn.status === 'completed' || turn.status === 'failed' || turn.status === 'partial') {
    return 'completed'
  }

  const inSynthesisGap =
    turn.status === 'active'
    && allRealTerminal
    && !summaryResult
    && turn.agentResults.some(r => r.isEphemeral && isSynthesisGapEphemeral(r))

  if (inSynthesisGap) return 'synthesizing'

  if (
    turn.status === 'active'
    && turn.processingStatusLogs.length > 0
    && (
      real.length === 0
      || turn.agentResults.some(r => r.isEphemeral && r.status === 'working')
    )
  ) {
    return 'collecting'
  }
  if (real.some(r => r.status === 'working')) return 'collecting'
  if (turn.status === 'awaiting_input' || turn.finalAnswer.kind === 'hitl') return 'collecting'
  return 'answering'
}

// ── Turn status derivation ─────────────────────────────────────

function deriveTurnStatus(
  agentResults: AgentResultViewModel[],
  opts: {
    isSupervisorTurn: boolean
    turnTerminalStatus?: TurnViewModel['turnTerminalStatus']
    turnCompletionKind?: TurnViewModel['turnCompletionKind']
    processingStatusLogs?: TurnViewModel['processingStatusLogs']
    openRoomRunContext: OpenRoomRunContext
  },
): TurnStatus {
  if (opts.turnTerminalStatus === 'canceled') return 'failed'

  const substantive = agentResults.filter(r => !r.isEphemeral)
  const hasAwaitingInput = substantive.some((r) => r.status === 'awaiting_input')
  const hasFailed = substantive.some((r) => r.status === 'failed')
  const hasCompleted = substantive.some((r) => r.status === 'completed')

  const orchestrator = agentResults.find(r => r.agentId === "system:hybro")
  const real = substantive.filter(r => !r.isSummaryAgent)
  const allRealTerminal =
    real.length > 0 && real.every(r => r.status === 'completed' || r.status === 'failed')

  if (opts.turnTerminalStatus === 'failed') return 'failed'

  if (orchestrator) {
    if (orchestrator.status === "awaiting_input" || hasAwaitingInput) return "awaiting_input"
    if (orchestrator.status === "working" && !allRealTerminal) return "active"
  }

  if (substantive.length === 0) {
    if (agentResults.length === 0) return 'active'
    return agentResults.some(r => r.status === 'working') ? 'active' : 'completed'
  }

  const hasWorking = substantive.some((r) =>
    r.status === 'working' && !r.isSummaryAgent && !isSupervisorClarifyAgent(r.agentId)
  )
  const allFailed = substantive.every((r) => r.status === 'failed')
  const allCompleted = substantive.every((r) => r.status === 'completed')
  const hasRealFailed = real.some((r) => r.status === 'failed')
  const hasRealCompleted = real.some((r) => r.status === 'completed')
  const hasSupervisorContinuationLogs =
    opts.isSupervisorTurn
    && hasSupervisorContinuationLog(opts.processingStatusLogs)
    && !isPersistedTerminalTurn(opts.turnTerminalStatus)

  const summaryAgent = substantive.find(r => r.isSummaryAgent)
  const hasSummaryContent =
    summaryAgent?.status === 'working'
    || (summaryAgent?.content.trim().length ?? 0) > 0

  const inSynthesisGap =
    allRealAgentsTerminal(agentResults)
    && agentResults.some(r => r.isEphemeral && isSynthesisGapEphemeral(r))
    && !substantive.some(r => r.isSummaryAgent && r.status === 'working')

  const synthesisGapActive =
    agentResults.some(r => r.isEphemeral && isSynthesisGapEphemeral(r))
    || (summaryAgent?.status === 'working' && (summaryAgent.content.trim().length ?? 0) === 0)
    || (opts.processingStatusLogs ?? []).some(entry =>
      entry.turnPhase === 'synthesizing'
      || entry.message.toLowerCase().includes('synthesiz'),
    )

  const hasPlanningEphemeral = agentResults.some(
    r => r.isEphemeral && r.status === 'working' && !isSynthesisGapEphemeral(r),
  )

  const preOrchestrationGap =
    hasPlanningEphemeral
    && real.length >= 2
    && allRealAgentsTerminal(agentResults)
    && !hasSummaryContent
    && !synthesisGapActive
    && !isPersistedTerminalTurn(opts.turnTerminalStatus)

  const awaitingSynthesisGap =
    real.length >= 2
    && allRealAgentsTerminal(agentResults)
    && !hasSummaryContent
    && synthesisGapActive
    && !isPersistedTerminalTurn(opts.turnTerminalStatus)

  const awaitingMultiAgentSynthesis = shouldShowSynthesizingPhaseForResults(
    agentResults,
    {
      ...opts.openRoomRunContext,
      isSupervisorTurn: opts.isSupervisorTurn,
      turnTerminalStatus: opts.turnTerminalStatus,
      turnCompletionKind: opts.turnCompletionKind,
      processingStatusLogs: opts.processingStatusLogs,
    },
  )

  const awaitingOpenRoomRun = shouldHoldTurnActiveForOpenRoomRun(agentResults, {
    ...opts.openRoomRunContext,
    isSupervisorTurn: opts.isSupervisorTurn,
    turnTerminalStatus: opts.turnTerminalStatus,
    turnCompletionKind: opts.turnCompletionKind,
    processingStatusLogs: opts.processingStatusLogs,
  })

  if (hasWorking) return 'active'
  if (hasAwaitingInput) return 'awaiting_input'
  if (allRealTerminal && hasRealFailed && hasRealCompleted) return 'partial'
  if (allRealTerminal && hasRealFailed) return 'failed'
  if (hasSupervisorContinuationLogs && allCompleted) return 'active'
  if (
    inSynthesisGap
    || awaitingSynthesisGap
    || preOrchestrationGap
    || awaitingMultiAgentSynthesis
    || awaitingOpenRoomRun
  ) {
    return 'active'
  }
  if (allFailed) return 'failed'
  if (allCompleted) return 'completed'
  if (hasCompleted && hasFailed) return 'partial'

  return 'active'
}

// ── Summary selection ──────────────────────────────────────────

/**
 * Select the best agent result as the turn summary.
 * Priority:
 *   1. System summary agent (system:hybro, etc.)
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
  nextTurnStart?: string,
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

  // Effective upper bound: max(nextTurnStart, latest entity timestamp in this turn).
  // This ensures late replies routed via relatedMessageId (whose entities and events
  // arrive after the next user message) still have their events included.
  let effectiveEnd = nextTurnStart
  if (nextTurnStart) {
    for (const id of scaffold.agentMessageIds) {
      const e = entities[id]
      if (e?.timestamp && e.timestamp > nextTurnStart) {
        effectiveEnd = undefined // late reply detected — disable upper bound for this turn
        break
      }
    }
  }

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

    // Agent events: match by agentId within the turn's time window
    if (raw.agentId && turnAgentIds.has(raw.agentId)) {
      const afterStart = !turnStart || raw.timestamp >= turnStart
      const beforeEnd = !effectiveEnd || raw.timestamp < effectiveEnd
      if (afterStart && beforeEnd) {
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
  options: TurnBuildOptions = {},
): TurnViewModel[] {
  // If no previous turns, delegate to full build
  if (prevTurns.length === 0) {
    return buildTurns(entities, orderedIds, events, options)
  }

  // Full rebuild to get the "truth"
  const fullTurns = buildTurns(entities, orderedIds, events, options)

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
  if (a.displayMode !== b.displayMode) return false
  if (a.phase !== b.phase) return false
  if (a.primaryStreamMessageId !== b.primaryStreamMessageId) return false
  if (a.turnTerminalStatus !== b.turnTerminalStatus) return false
  if (a.turnCompletionKind !== b.turnCompletionKind) return false
  if (!processingStatusLogsEqual(a.processingStatusLogs, b.processingStatusLogs)) return false
  if (a.finalAnswer.kind !== b.finalAnswer.kind) return false
  if (a.finalAnswer.primaryMessageId !== b.finalAnswer.primaryMessageId) return false
  if (a.finalAnswer.deterministicIntro !== b.finalAnswer.deterministicIntro) return false
  if ((a.finalAnswer.sections?.length ?? 0) !== (b.finalAnswer.sections?.length ?? 0)) return false
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
      for (let k = 0; k < a.agentResults[i].artifacts[j].parts.length; k++) {
        const left = a.agentResults[i].artifacts[j].parts[k]
        const right = b.agentResults[i].artifacts[j].parts[k]
        if (left.kind !== right.kind) return false
        if (left.text !== right.text) return false
        if (left.file?.fileId !== right.file?.fileId) return false
        if (left.file?.name !== right.file?.name) return false
        if (left.file?.mime_type !== right.file?.mime_type) return false
        if (left.file?.sizeBytes !== right.file?.sizeBytes) return false
        if (left.file?.sha256 !== right.file?.sha256) return false
        if (JSON.stringify(left.data) !== JSON.stringify(right.data)) return false
      }
    }
    // V2 AgentResultViewModel fields
    if (a.agentResults[i].hitlResolved?.prompt !== b.agentResults[i].hitlResolved?.prompt) return false
    if (a.agentResults[i].hitlResolved?.answer !== b.agentResults[i].hitlResolved?.answer) return false
    if (a.agentResults[i].hitlPending?.prompt !== b.agentResults[i].hitlPending?.prompt) return false
    if (a.agentResults[i].eventCount !== b.agentResults[i].eventCount) return false
    if (a.agentResults[i].durationMs !== b.agentResults[i].durationMs) return false
    if (a.agentResults[i].isEphemeral !== b.agentResults[i].isEphemeral) return false
  }

  // Check events count changed
  if (a.events.length !== b.events.length) return false

  // Summary equality
  if ((a.summary?.sourceAgentId ?? null) !== (b.summary?.sourceAgentId ?? null)) return false
  if ((a.summary?.title ?? '') !== (b.summary?.title ?? '')) return false
  if ((a.summary?.body ?? '') !== (b.summary?.body ?? '')) return false

  return true
}

function processingStatusLogsEqual(
  a: TurnViewModel['processingStatusLogs'],
  b: TurnViewModel['processingStatusLogs'],
): boolean {
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) {
    if (a[i].id !== b[i].id) return false
    if (a[i].message !== b[i].message) return false
    if (a[i].timestamp !== b[i].timestamp) return false
  }
  return true
}
