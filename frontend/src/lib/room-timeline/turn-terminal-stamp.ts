import { buildTurns } from './build-turns'
import { isCanceledMultiAgentTurn } from './derive-final-answer'
import {
  hasActiveSupervisorPlanningEphemeral,
  hasActiveSynthesisGap,
  isBackendRunConfirmedNonSynthesisCompletion,
  turnHasSubstantiveLlmSynthesis,
} from './multi-agent-turn-complete'
import type { TaskState } from '@/lib/types/sse'
import { TASK_STATE } from '@/lib/types/sse'
import { getStripSourceResults } from './turn-live-shell'
import type { AgentResultViewModel, TurnViewModel } from './types'
import { findProcessingStatusUserEntity } from '@/hooks/room/processing-status-log'
import type { ProcessingLifecycle } from '@/hooks/room/processing-lifecycle'
import { inquiryActiveRuns } from '@/lib/api/room'
import { useMessageStore } from '@/stores/message-store'

export type ActiveRunRef = { trigger_message_id?: string | null }

function allRealTerminal(real: AgentResultViewModel[]): boolean {
  if (real.length === 0) return false
  return real.every(r => r.status === 'completed' || r.status === 'failed')
}

function isDeterministicSummary(summary: AgentResultViewModel | undefined): boolean {
  return summary?.summaryOrigin === 'deterministic'
}

/**
 * Entity-level gate for stamping turnTerminalStatus.
 * When backendRunActive is true, the room run is still in flight — do not stamp.
 * When false, backend confirms the run finished (safe fallback if SSE was missed).
 * When null, only stamp when a deterministic digest entity is already present.
 */
export function canStampTurnTerminalFromEntityState(
  turn: TurnViewModel,
  real: AgentResultViewModel[] = getStripSourceResults(turn),
  backendRunActive: boolean | null = null,
  options?: { lifecycleActive?: boolean },
  turnCompletionKind?: 'synthesis' | 'deterministic',
): boolean {
  if (turn.turnTerminalStatus) return false
  if (turn.status === 'awaiting_input') return false
  if (real.length < 2) return false
  if (!allRealTerminal(real)) return false
  if (real.some(r => r.status === 'working')) return false

  if (backendRunActive === true) return false
  if (turnHasSubstantiveLlmSynthesis(turn)) return false

  const summary = turn.agentResults.find(r => r.isSummaryAgent)

  if (backendRunActive === false) {
    if (
      summary?.status === 'working'
      && !isDeterministicSummary(summary)
      && summary.content.trim().length === 0
    ) {
      return false
    }
    return isBackendRunConfirmedNonSynthesisCompletion(turn, real, turnCompletionKind)
  }

  if (hasActiveSynthesisGap(turn)) return false
  if (hasActiveSupervisorPlanningEphemeral(turn)) return false
  if (
    summary?.status === 'working'
    && !isDeterministicSummary(summary)
    && summary.content.trim().length === 0
  ) {
    return false
  }

  return Boolean(
    summary
    && isDeterministicSummary(summary)
    && (summary.status === 'working' || summary.content.trim().length > 0),
  )
}

export function isBackendRunActiveForTurn(
  activeRuns: readonly ActiveRunRef[] | null | undefined,
  userMessageId: string,
): boolean {
  if (!activeRuns?.length) return false
  return activeRuns.some(run => run.trigger_message_id === userMessageId)
}

export function terminalStatusForTurn(turn: TurnViewModel): 'completed' | 'failed' | 'canceled' {
  if (isCanceledMultiAgentTurn(turn)) return 'canceled'
  if (turn.status === 'failed') return 'failed'
  const real = turn.agentResults.filter(r => !r.isSummaryAgent && !r.isEphemeral)
  if (real.length > 0 && real.every(r => r.status === 'failed')) return 'failed'
  return 'completed'
}

function isActiveLifecycleTurn(
  lifecycle: ProcessingLifecycle,
  userMessageId: string,
  clientRequestId?: string | null,
): boolean {
  const lifecycleMessageId = lifecycle.getMessageId()
  if (lifecycleMessageId && lifecycleMessageId === userMessageId) return true

  const pendingAck = lifecycle.getPendingRunEventAck()
  if (pendingAck && clientRequestId && pendingAck === clientRequestId) return true

  return false
}

function writeTurnTerminalStatus(
  roomId: string,
  user: { id: string; content: string; senderName: string; timestamp: string },
  terminalStatus: 'completed' | 'failed' | 'canceled',
  lifecycle: ProcessingLifecycle,
  clientRequestId?: string | null,
  turnCompletionKind?: 'synthesis' | 'deterministic',
): void {
  const store = useMessageStore.getState()
  store.upsertMessage({
    id: user.id,
    roomId,
    messageType: 'user',
    content: user.content,
    senderName: user.senderName,
    timestamp: user.timestamp,
    turnTerminalStatus: terminalStatus,
    turnCompletionKind,
  }, 'sse')

  if (
    isActiveLifecycleTurn(lifecycle, user.id, clientRequestId)
    && (terminalStatus === 'completed' || terminalStatus === 'failed' || terminalStatus === 'canceled')
  ) {
    lifecycle.markProcessingResolved()
    lifecycle.stopProcessing({ clearMessageId: false })
    lifecycle.disarmCancelTimeout()
    store.removeMessage(lifecycle.placeholderId(roomId))
    lifecycle.dismissPlaceholder()
  }
}

export function buildTurnForRecoveryHint(
  roomId: string,
  hint: {
    clientRequestId?: string | null
    relatedMessageId?: string | null
  },
): TurnViewModel | undefined {
  const store = useMessageStore.getState()
  if (store.roomId !== roomId) return undefined

  const user = findProcessingStatusUserEntity(roomId, {
    messageId: hint.relatedMessageId,
    clientRequestId: hint.clientRequestId,
    preferClientRequestId: true,
  })
  if (!user) return undefined

  return buildTurnForUser(roomId, user.id)
}

/**
 * Gate debounced backend-truth recovery so synthesis turns are not stamped deterministic.
 */
export function shouldScheduleTurnTerminalRecovery(
  turn: TurnViewModel | undefined,
  taskStatus: TaskState,
): boolean {
  if (turn) {
    if (turnHasSubstantiveLlmSynthesis(turn)) return false
    if (hasActiveSynthesisGap(turn)) return false
    if (turn.turnCompletionKind === 'synthesis') return false
  }

  if (
    taskStatus === TASK_STATE.FAILED
    || taskStatus === TASK_STATE.REJECTED
    || taskStatus === TASK_STATE.CANCELED
  ) {
    return true
  }

  if (taskStatus === TASK_STATE.COMPLETED && turn) {
    const real = getStripSourceResults(turn)
    return allRealTerminal(real)
  }

  return false
}

function buildTurnForUser(roomId: string, userId: string): TurnViewModel | undefined {
  const store = useMessageStore.getState()
  if (store.roomId !== roomId) return undefined
  const roomOrderedIds = store.orderedIds.filter(id => store.entities[id]?.roomId === roomId)
  return buildTurns(store.entities, roomOrderedIds, []).find(t => t.userMessageId === userId)
}

export function stampTurnTerminalFromBackendTruth(
  roomId: string,
  lifecycle: ProcessingLifecycle,
  hint: {
    clientRequestId?: string | null
    relatedMessageId?: string | null
  },
  backendRunActive: boolean | null,
  turnCompletionKind?: 'synthesis' | 'deterministic',
): boolean {
  const store = useMessageStore.getState()
  if (store.roomId !== roomId) return false

  const user = findProcessingStatusUserEntity(roomId, {
    messageId: hint.relatedMessageId,
    clientRequestId: hint.clientRequestId,
    preferClientRequestId: true,
  })
  if (!user || user.turnTerminalStatus) return false

  const turn = buildTurnForUser(roomId, user.id)
  if (!turn) return false

  const real = getStripSourceResults(turn)
  const lifecycleActive = isActiveLifecycleTurn(
    lifecycle,
    user.id,
    user.clientRequestId ?? hint.clientRequestId,
  )
  if (!canStampTurnTerminalFromEntityState(
    turn,
    real,
    backendRunActive,
    { lifecycleActive },
    turnCompletionKind,
  )) {
    return false
  }

  const resolvedCompletionKind =
    turnCompletionKind
    ?? (
      backendRunActive === false
      && isBackendRunConfirmedNonSynthesisCompletion(turn, real, turnCompletionKind)
        ? 'deterministic'
        : undefined
    )

  writeTurnTerminalStatus(
    roomId,
    user,
    terminalStatusForTurn(turn),
    lifecycle,
    user.clientRequestId ?? hint.clientRequestId,
    resolvedCompletionKind,
  )
  return true
}

export async function ensureTurnTerminalStampedFromBackendTruth(
  roomId: string,
  lifecycle: ProcessingLifecycle,
  hint: {
    clientRequestId?: string | null
    relatedMessageId?: string | null
  },
  getToken?: (() => Promise<string | null>) | undefined,
): Promise<boolean> {
  const store = useMessageStore.getState()
  if (store.roomId !== roomId) return false

  const user = findProcessingStatusUserEntity(roomId, {
    messageId: hint.relatedMessageId,
    clientRequestId: hint.clientRequestId,
    preferClientRequestId: true,
  })
  if (!user || user.turnTerminalStatus) return false

  let backendRunActive: boolean | null = null
  let turnCompletionKind: 'synthesis' | 'deterministic' | undefined
  if (getToken) {
    try {
      const result = await inquiryActiveRuns(roomId, getToken, undefined, user.id)
      if (result.success) {
        backendRunActive = isBackendRunActiveForTurn(result.active_runs, user.id)
        const kind = result.turn_completion_kind
        if (kind === 'synthesis' || kind === 'deterministic') {
          turnCompletionKind = kind
        }
      }
    } catch {
      backendRunActive = null
    }
  }

  return stampTurnTerminalFromBackendTruth(roomId, lifecycle, hint, backendRunActive, turnCompletionKind)
}

const pendingBackendTruthChecks = new Map<string, ReturnType<typeof setTimeout>>()

export function scheduleTurnTerminalBackendTruthCheck(
  roomId: string,
  lifecycle: ProcessingLifecycle,
  hint: {
    clientRequestId?: string | null
    relatedMessageId?: string | null
  },
  getToken?: (() => Promise<string | null>) | undefined,
  debounceMs = 1500,
): void {
  const key = `${roomId}:${hint.relatedMessageId ?? hint.clientRequestId ?? 'unknown'}`
  const existing = pendingBackendTruthChecks.get(key)
  if (existing) clearTimeout(existing)

  pendingBackendTruthChecks.set(key, setTimeout(() => {
    pendingBackendTruthChecks.delete(key)
    void ensureTurnTerminalStampedFromBackendTruth(roomId, lifecycle, hint, getToken)
  }, debounceMs))
}

export function clearScheduledTurnTerminalBackendTruthChecks(): void {
  for (const timer of pendingBackendTruthChecks.values()) {
    clearTimeout(timer)
  }
  pendingBackendTruthChecks.clear()
}
