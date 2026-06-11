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

export function isMixedTerminalMultiAgentTurn(
  real: AgentResultViewModel[],
): boolean {
  if (real.length < 2 || !allRealTerminal(real)) return false
  return real.some(r => r.status === 'completed') && real.some(r => r.status === 'failed')
}

/**
 * True when the turn should resolve to combined/deterministic done rather than
 * waiting for LLM synthesis. Avoids flashing deterministic_done before synthesis
 * signals arrive on supervisor / synthesis-planned turns.
 */
export function isDeterministicCompletionExpected(
  turn: TurnViewModel,
  real: AgentResultViewModel[] = getStripSourceResults(turn),
  turnCompletionKind: TurnViewModel['turnCompletionKind'] = turn.turnCompletionKind,
): boolean {
  if (turnCompletionKind === 'synthesis') return false
  if (turnCompletionKind === 'deterministic') return true

  if (isMixedTerminalMultiAgentTurn(real)) {
    return true
  }

  if (hasActiveSynthesisGap(turn)) return false
  if (hasSynthesisSignalInProcessingLogs(turn)) return false

  const summary = turn.agentResults.find(r => r.isSummaryAgent)
  if (
    summary
    && isDeterministicSummary(summary)
    && (summary.status === 'working' || summary.content.trim().length > 0)
  ) {
    return true
  }
  if (summary && !isDeterministicSummary(summary)) {
    if (summary.content.trim().length > 0) return false
    if (summary.status === 'working') return false
  }

  if (turn.turnTerminalStatus === 'failed' || turn.turnTerminalStatus === 'canceled') {
    return true
  }

  if (real.length >= 2 && allRealTerminal(real) && real.every(r => r.status === 'failed')) {
    return true
  }

  // Stamped or hydrated terminal turn with explicit non-synthesis completion.
  if (
    turn.turnTerminalStatus === 'completed'
    && turn.turnCompletionKind !== 'synthesis'
    && real.length >= 2
    && allRealTerminal(real)
    && !hasActiveSynthesisGap(turn)
  ) {
    return true
  }

  // Non-supervisor parallel rooms complete deterministically once terminal.
  if (
    turn.turnTerminalStatus === 'completed'
    && !turn.isSupervisorTurn
    && real.length >= 2
    && allRealTerminal(real)
    && real.every(r => r.status === 'completed')
    && !hasActiveSynthesisGap(turn)
  ) {
    return true
  }

  return false
}

/**
 * True when the backend reports no active run and there is no evidence that LLM
 * synthesis is still in flight. Used for terminal stamping — broader than
 * isDeterministicCompletionExpected so supervisor no-synthesis turns can complete
 * when inquiry missed turn_completion_kind but the run is finished.
 */
export function isBackendRunConfirmedNonSynthesisCompletion(
  turn: TurnViewModel,
  real: AgentResultViewModel[] = getStripSourceResults(turn),
  turnCompletionKind: TurnViewModel['turnCompletionKind'] = turn.turnCompletionKind,
): boolean {
  // Supervisor turns must have an explicit turnCompletionKind from the backend
  // to be stamped (unless a deterministic digest is already present),
  // otherwise they could be transitioning to synthesis.
  const summary = turn.agentResults.find(r => r.isSummaryAgent)
  if (
    turn.isSupervisorTurn
    && turnCompletionKind === undefined
    && !isDeterministicSummary(summary)
  ) {
    return false
  }

  if (turnCompletionKind === 'synthesis') return false
  if (real.length < 2 || !allRealTerminal(real)) return false
  if (turnHasSubstantiveLlmSynthesis(turn)) return false
  if (hasActiveSynthesisGap(turn)) return false
  if (hasSynthesisSignalInProcessingLogs(turn)) return false

  if (
    summary?.status === 'working'
    && !isDeterministicSummary(summary)
    && summary.content.trim().length === 0
  ) {
    return false
  }

  return true
}

function logEntryIndicatesSynthesis(entry: { message: string; turnPhase?: string }): boolean {
  if (entry.turnPhase === 'synthesizing') return true
  const msg = entry.message.toLowerCase()
  return msg.includes('synthesiz') || msg.includes('compiling summary')
}

/** Backend often signals synthesis via user processing_status logs before summary entities arrive. */
export function hasSynthesisSignalInProcessingLogs(turn: TurnViewModel): boolean {
  return (turn.processingStatusLogs ?? []).some(logEntryIndicatesSynthesis)
}

export function hasActiveSynthesisGap(turn: TurnViewModel): boolean {
  if (turn.turnTerminalStatus === 'failed' || turn.turnTerminalStatus === 'canceled') return false
  if (turn.turnTerminalStatus === 'completed' && turn.turnCompletionKind !== 'synthesis') return false

  const real = getStripSourceResults(turn)
  if (
    real.length >= 2
    && allRealTerminal(real)
    && real.some(r => r.status === 'failed')
  ) {
    const summaryForFailure = turn.agentResults.find(r => r.isSummaryAgent)
    const llmSynthesisInFlight =
      summaryForFailure
      && !isDeterministicSummary(summaryForFailure)
      && summaryForFailure.status === 'working'
      && summaryForFailure.content.trim().length === 0
    const synthesisEphemeral = turn.agentResults.some(
      r => r.isEphemeral && isSynthesisGapEphemeral(r),
    )
    if (!llmSynthesisInFlight && !synthesisEphemeral) {
      return false
    }
  }

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
 * Reserved for a narrow window before synthesis signals arrive. Delegation logs,
 * supervisor mode, and generic work logs are not evidence of synthesis — only
 * positive synthesis signals (see hasActiveSynthesisGap) should hold the turn.
 */
export function isPreSynthesisGap(
  turn: TurnViewModel,
  real: AgentResultViewModel[] = getStripSourceResults(turn),
): boolean {
  if (real.length < 2) return false
  if (!allRealTerminal(real)) return false
  if (real.some(r => r.status === 'working')) return false
  if (turn.turnTerminalStatus && turn.turnCompletionKind !== 'synthesis') return false
  if (turn.turnCompletionKind === 'deterministic') return false
  if (hasActiveSynthesisGap(turn)) return false

  const summary = turn.agentResults.find(r => r.isSummaryAgent)
  if (summary && isDeterministicSummary(summary)) return false
  if (summary && !isDeterministicSummary(summary) && hasLlmSummaryWithContent(summary)) return false

  const hasOrchestrationContext =
    turn.isSupervisorTurn || turn.turnCompletionKind === 'synthesis'
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

/** True when a non-deterministic summary agent has streamed or completed LLM synthesis. */
export function turnHasSubstantiveLlmSynthesis(
  turn: TurnViewModel,
): boolean {
  const summary = turn.agentResults.find(r => r.isSummaryAgent)
  return hasLlmSummaryWithContent(summary)
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

  if (!isDeterministicCompletionExpected(turn, real)) return false

  const summary = turn.agentResults.find(r => r.isSummaryAgent)
  if (summary && !isDeterministicSummary(summary) && hasLlmSummaryWithContent(summary)) {
    return false
  }

  return true
}

export { hasLlmSummaryWithContent }
