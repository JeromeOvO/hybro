import type { AgentResultViewModel, TurnViewModel } from './types'
import { getStripSourceResults } from './turn-live-shell'

function isSynthesisGapEphemeral(result: AgentResultViewModel): boolean {
  if (result.isSummaryAgent && result.status === 'working') return true
  const stage = result.taskStatusMessage?.trim().toLowerCase() ?? ''
  return stage.includes('synthesiz')
}

function allRealTerminal(real: AgentResultViewModel[]): boolean {
  if (real.length === 0) return false
  return real.every(r => r.status === 'completed' || r.status === 'failed')
}

function isDeterministicSummary(summary: AgentResultViewModel | undefined): boolean {
  return summary?.summaryOrigin === 'deterministic'
}

function hasDeterministicSummaryEntity(summary: AgentResultViewModel | undefined): boolean {
  if (!summary) return false
  if (!isDeterministicSummary(summary)) return false
  return summary.status === 'working' || summary.content.trim().length > 0
}

function hasLlmSynthesisStarted(summary: AgentResultViewModel | undefined): boolean {
  if (!summary) return false
  if (isDeterministicSummary(summary)) return false
  return summary.status === 'working' || summary.content.trim().length > 0
}

/** Backend often signals synthesis via user processing_status logs before summary entities arrive. */
export function hasSynthesisSignalInProcessingLogs(turn: TurnViewModel): boolean {
  return (turn.processingStatusLogs ?? []).some(entry =>
    entry.message.toLowerCase().includes('synthesiz'),
  )
}

export function hasActiveSynthesisGap(turn: TurnViewModel): boolean {
  if (turn.turnTerminalStatus === 'failed' || turn.turnTerminalStatus === 'canceled') return false
  if (turn.turnTerminalStatus === 'completed' && turn.turnCompletionKind !== 'synthesis') return false

  const summary = turn.agentResults.find(r => r.isSummaryAgent)
  if (
    summary
    && isDeterministicSummary(summary)
    && (summary.status === 'working' || summary.content.trim().length > 0)
  ) {
    return false
  }

  if (hasSynthesisSignalInProcessingLogs(turn)) return true
  if (turn.agentResults.some(r => r.isEphemeral && isSynthesisGapEphemeral(r))) {
    return true
  }
  if (!summary || isDeterministicSummary(summary)) return false
  if (summary.content.trim().length > 0) return false
  return summary.status === 'working'
}

/**
 * All real agents finished but orchestration has not emitted synthesis or
 * turn-level terminal status yet (window before "Synthesizing…" ephemeral).
 */
export function isPreSynthesisGap(
  turn: TurnViewModel,
  real: AgentResultViewModel[] = getStripSourceResults(turn),
): boolean {
  if (real.length < 2) return false
  if (!allRealTerminal(real)) return false
  if (real.some(r => r.status === 'working')) return false
  if (turn.turnTerminalStatus && turn.turnCompletionKind !== 'synthesis') return false
  if (hasActiveSynthesisGap(turn)) return false

  const summary = turn.agentResults.find(r => r.isSummaryAgent)
  if (hasDeterministicSummaryEntity(summary)) return false
  if (hasLlmSynthesisStarted(summary)) return false

  const hasOrchestrationContext =
    (turn.processingStatusLogs?.length ?? 0) > 0
    || turn.isSupervisorTurn
    || turn.turnCompletionKind === 'synthesis'
  if (!hasOrchestrationContext) return false

  return true
}

/**
 * True when the primary surface should show the synthesizing phase (pre-synthesis
 * window, active synthesis gap, or LLM summary streaming without content yet).
 */
export function shouldShowSynthesizingPhase(
  turn: TurnViewModel,
  real: AgentResultViewModel[] = getStripSourceResults(turn),
): boolean {
  if (real.length < 2) return false
  if (!allRealTerminal(real)) return false
  if (real.some(r => r.status === 'working')) return false

  if (turn.turnTerminalStatus === 'failed' || turn.turnTerminalStatus === 'canceled') {
    return false
  }
  if (turn.turnTerminalStatus === 'completed' && turn.turnCompletionKind !== 'synthesis') {
    return false
  }

  const summary = turn.agentResults.find(r => r.isSummaryAgent)
  if (
    summary
    && !isDeterministicSummary(summary)
    && (summary.status === 'working' || summary.content.trim().length > 0)
  ) {
    return summary.status === 'working' || summary.content.trim().length === 0
  }

  if (hasActiveSynthesisGap(turn)) return true
  if (turn.turnCompletionKind === 'synthesis' && !summary?.content?.trim()) return true
  return isPreSynthesisGap(turn, real)
}

/** Lighter entry point for deriveTurnStatus before a full TurnViewModel exists. */
export function shouldShowSynthesizingPhaseForResults(
  agentResults: AgentResultViewModel[],
  context: {
    turnTerminalStatus?: TurnViewModel['turnTerminalStatus']
    turnCompletionKind?: TurnViewModel['turnCompletionKind']
    processingStatusLogs?: TurnViewModel['processingStatusLogs']
    isSupervisorTurn?: boolean
  },
): boolean {
  return shouldShowSynthesizingPhase({
    id: '',
    roomId: '',
    userMessageId: null,
    userContent: '',
    userAttachments: [],
    timestamp: '',
    status: 'active',
    events: [],
    summary: null,
    agentResults,
    activeAgentIds: [],
    isSupervisorTurn: context.isSupervisorTurn ?? false,
    displayMode: 'working',
    finalAnswer: { kind: 'pending', label: 'Working' },
    turnTerminalStatus: context.turnTerminalStatus,
    turnCompletionKind: context.turnCompletionKind,
    processingStatusLogs: context.processingStatusLogs ?? [],
    phase: 'collecting',
  })
}

export function hasActiveSupervisorPlanningEphemeral(turn: TurnViewModel): boolean {
  return turn.agentResults.some(
    r => r.isEphemeral && r.status === 'working' && !isSynthesisGapEphemeral(r),
  )
}

function hasLlmSummaryWithContent(summary: AgentResultViewModel | undefined): boolean {
  if (!summary || isDeterministicSummary(summary)) return false
  if (summary.content.trim().length > 0) return true
  return summary.status === 'working'
}

/**
 * True when a multi-agent turn can show combined responses without waiting for
 * turn-level processing_status SSE (missed-frame recovery + anti-flash guard).
 */
export function isMultiAgentTurnReadyForDeterministicDone(
  turn: TurnViewModel,
  real: AgentResultViewModel[] = getStripSourceResults(turn),
): boolean {
  if (real.length < 2) return false
  if (!allRealTerminal(real)) return false
  if (real.some(r => r.status === 'working')) return false

  if (turn.turnTerminalStatus === 'completed') {
    if (turn.turnCompletionKind === 'synthesis') return false
    if (turn.turnCompletionKind === 'deterministic') return true

    // Fallback when turnCompletionKind is undefined (truth-check didn't return it
    // or old data without the field):
    const summary = turn.agentResults.find(r => r.isSummaryAgent)

    // No summary agent entity: if processing logs have synthesis signal,
    // synthesis was planned but entity hasn't arrived yet → hold pending
    if (!summary) {
      if (hasSynthesisSignalInProcessingLogs(turn)) return false
      return true
    }

    if (isDeterministicSummary(summary)) {
      if (summary.status === 'working' || summary.content.trim().length > 0) return true
    }

    // Summary exists but is non-deterministic with no content → synthesis may be pending
    return false
  }

  if (hasActiveSynthesisGap(turn)) return false
  if (hasActiveSupervisorPlanningEphemeral(turn)) return false

  const summary = turn.agentResults.find(r => r.isSummaryAgent)
  if (summary?.status === 'working' && !isDeterministicSummary(summary)) return false

  const deterministicSummary =
    summary
    && isDeterministicSummary(summary)
    && (summary.status === 'working' || summary.content.trim().length > 0)
  if (deterministicSummary) return true

  return false
}

export { hasLlmSummaryWithContent }
