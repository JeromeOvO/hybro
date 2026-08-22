import type {
  AgentResultViewModel,
  FinalAnswerHitlViewModel,
  FinalAnswerSection,
  FinalAnswerViewModel,
  SummaryOrigin,
  TurnViewModel,
} from './types'
import {
  hasActiveSynthesisGap,
  hasSynthesisSignalInProcessingLogs,
  isMultiAgentTurnReadyForDeterministicDone,
  isPreSynthesisGap,
  shouldShowSynthesizingPhase,
  turnHasSubstantiveLlmSynthesis,
} from './multi-agent-turn-complete'
import { getStripSourceResults } from './turn-live-shell'

export function buildDeterministicIntro(agentCount: number): string {
  return `${agentCount} agent${agentCount === 1 ? '' : 's'} responded. Expand below to read each answer.`
}

/** Matches the non-LLM coordinator digest stub (`_emit_deterministic_digest`). */
export function isDeterministicDigestContent(content: string): boolean {
  const text = content.trim()
  if (!text) return false
  return /^\d+ agents? responded\. Expand below to read each answer\.?$/i.test(text)
}

export const CANCELED_TURN_INTRO =
  'Processing was stopped. Expand below for per-agent status.'

export const FAILED_TURN_INTRO =
  'This request could not be completed. Expand below for per-agent errors.'

function isAgentResultCanceled(result: AgentResultViewModel): boolean {
  const text = `${result.content} ${result.taskStatusMessage ?? ''}`.toLowerCase()
  return text.includes('cancel')
}

/** True when every real agent failed and the turn did not succeed. */
export function isFailedMultiAgentTurn(
  turn: TurnViewModel,
  real: AgentResultViewModel[] = getStripSourceResults(turn),
): boolean {
  if (real.length < 2) return false
  if (isCanceledMultiAgentTurn(turn, real)) return false
  if (turn.turnTerminalStatus === 'failed') return true
  if (turn.status === 'failed') return true
  if (!allRealTerminal(real)) return false
  if (real.some(r => r.status === 'completed')) return false
  return real.every(r => r.status === 'failed')
}

/** True when the room or all terminal agent tasks were user-canceled. */
export function isCanceledMultiAgentTurn(
  turn: TurnViewModel,
  real: AgentResultViewModel[] = getStripSourceResults(turn),
): boolean {
  if (real.length < 2) return false
  if (turn.turnTerminalStatus === 'canceled') return true

  const terminal = real.filter(r => r.status === 'completed' || r.status === 'failed')
  if (terminal.length !== real.length) return false
  return terminal.length > 0 && terminal.every(isAgentResultCanceled)
}

function allRealTerminal(real: AgentResultViewModel[]): boolean {
  if (real.length === 0) return false
  return real.every(r => r.status === 'completed' || r.status === 'failed')
}

function isDeterministicSummary(summary: AgentResultViewModel): boolean {
  return summary.summaryOrigin === 'deterministic'
}

function hasLlmSynthesisContent(summary: AgentResultViewModel | undefined): boolean {
  if (!summary) return false
  if (isDeterministicSummary(summary)) return false
  if (summary.status === 'working') return true
  return summary.content.trim().length > 0
}

function hasDeterministicSummaryEntity(summary: AgentResultViewModel | undefined): boolean {
  if (!summary) return false
  if (!isDeterministicSummary(summary)) return false
  return summary.status === 'working' || summary.content.trim().length > 0
}

/** Hold pending while a synthesis gap is active (any orchestration mode). */
function shouldHoldPendingForSynthesisGap(turn: TurnViewModel, real: AgentResultViewModel[]): boolean {
  if (real.length < 2) return false
  if (!allRealTerminal(real)) return false

  const summary = turn.agentResults.find(r => r.isSummaryAgent)
  if (hasLlmSynthesisContent(summary)) return false
  if (hasDeterministicSummaryEntity(summary)) return false

  if (turn.turnTerminalStatus === 'failed' || turn.turnTerminalStatus === 'canceled') {
    return false
  }
  if (turn.turnTerminalStatus === 'completed' && turn.turnCompletionKind !== 'synthesis') {
    return false
  }

  if (
    real.some(r => r.status === 'failed')
    && allRealTerminal(real)
    && !hasLlmSynthesisContent(summary)
    && !hasDeterministicSummaryEntity(summary)
  ) {
    return false
  }

  if (hasActiveSynthesisGap(turn)) return true

  if (turn.turnCompletionKind === 'synthesis') {
    const llmSummary = turn.agentResults.find(
      r => r.isSummaryAgent && r.summaryOrigin !== 'deterministic',
    )
    if (
      llmSummary?.status === 'working'
      && llmSummary.content.trim().length === 0
    ) {
      return true
    }
  }

  return false
}

function isTurnTerminal(status: TurnViewModel['status']): boolean {
  return status === 'completed' || status === 'partial' || status === 'failed'
}

function buildHitlFinalAnswer(
  turn: TurnViewModel,
  real: AgentResultViewModel[],
): FinalAnswerViewModel {
  const supervisorAgent = turn.agentResults.find(
    r => r.agentId === 'system:clarifier',
  )
  const pendingAgents = real.filter(r => r.hitlPending)

  const prompts: FinalAnswerHitlViewModel['prompts'] = []

  if (turn.status === 'awaiting_input') {
    const clarifyAgent = turn.agentResults.find(
      r => r.hitlPending || r.agentId === 'system:clarifier',
    )
    const prompt =
      clarifyAgent?.hitlPending?.prompt
      ?? clarifyAgent?.content
      ?? turn.supervisorStage?.details
      ?? ''
    if (prompt.trim()) {
      prompts.push({
        messageId: clarifyAgent?.messageId ?? turn.id,
        agentName: clarifyAgent?.agentName ?? 'HYBRO AI',
        prompt: prompt.trim(),
      })
    }
  }

  for (const agent of pendingAgents) {
    if (!agent.hitlPending) continue
    if (prompts.some(p => p.messageId === agent.messageId)) continue
    prompts.push({
      messageId: agent.messageId,
      agentName: agent.agentName,
      prompt: agent.hitlPending.prompt,
    })
  }

  if (prompts.length === 0 && supervisorAgent?.content.trim()) {
    prompts.push({
      messageId: supervisorAgent.messageId,
      agentName: supervisorAgent.agentName,
      prompt: supervisorAgent.content.trim(),
    })
  }

  const source = prompts.some(prompt => {
    const result = turn.agentResults.find(item => item.messageId === prompt.messageId)
    return (
      result?.hitlPending?.source === 'supervisor'
      || result?.agentId === 'system:clarifier'
    )
  })
    ? 'supervisor'
    : 'agent'

  return {
    kind: 'hitl',
    label: 'Needs input',
    hitl: { source, prompts },
  }
}

function orderDigestSections(
  real: AgentResultViewModel[],
  agentMessageIds: readonly string[],
): FinalAnswerSection[] {
  const byId = new Map(real.map(r => [r.messageId, r]))
  const ordered: AgentResultViewModel[] = []

  for (const id of agentMessageIds) {
    const r = byId.get(id)
    if (r && !r.isSummaryAgent && !r.isEphemeral) {
      ordered.push(r)
      byId.delete(id)
    }
  }

  for (const r of real) {
    if (byId.has(r.messageId)) ordered.push(r)
  }

  return ordered.map(r => ({
    messageId: r.messageId,
    agentId: r.agentId,
    agentName: r.agentName,
    content: r.content,
    artifacts: r.artifacts,
    status: r.status,
  }))
}

function buildCanceledFinalAnswer(): FinalAnswerViewModel {
  return {
    kind: 'canceled',
    label: 'Canceled',
    canceledIntro: CANCELED_TURN_INTRO,
  }
}

function buildFailedFinalAnswer(): FinalAnswerViewModel {
  return {
    kind: 'failed',
    label: 'Failed',
    failedIntro: FAILED_TURN_INTRO,
  }
}

function buildDeterministicDoneFinalAnswer(
  turn: TurnViewModel,
  real: AgentResultViewModel[],
  agentMessageIds: readonly string[],
  summary: AgentResultViewModel | undefined,
): FinalAnswerViewModel {
  const intro =
    summary?.content.trim()
    || buildDeterministicIntro(real.length)

  return {
    kind: 'deterministic_done',
    label: 'Combined agent responses',
    summaryOrigin: 'deterministic',
    primaryMessageId: summary?.messageId,
    deterministicIntro: intro,
    sections: orderDigestSections(real, agentMessageIds),
  }
}

/**
 * V3 §17.5 — derive unified final answer for a turn.
 */
export function deriveFinalAnswer(
  turn: TurnViewModel,
  agentMessageIds: readonly string[],
): FinalAnswerViewModel {
  const real = getStripSourceResults(turn)
  const summary = turn.agentResults.find(r => r.isSummaryAgent)
  const orchestrator = turn.agentResults.find(r => r.agentId === "system:hybro")
  const terminalTurnWithEmptyWorkingSummary =
    isTurnTerminal(turn.status)
    && summary?.status === 'working'
    && summary.content.trim().length === 0

  // --- 1. HITL Priority ---
  if (
    turn.status === "awaiting_input" ||
    real.some(r => r.hitlPending) ||
    turn.agentResults.some(r => r.hitlPending && !r.isSummaryAgent)
  ) {
    return buildHitlFinalAnswer(turn, real)
  }

  // --- 2. Canceled/Failed (Hardware priority) ---
  if (
    turn.turnTerminalStatus === 'canceled'
    || isCanceledMultiAgentTurn(turn, real)
  ) {
    return buildCanceledFinalAnswer()
  }
  if (
    turn.turnTerminalStatus === 'failed'
    || isFailedMultiAgentTurn(turn, real)
  ) {
    return buildFailedFinalAnswer()
  }
  if (
    orchestrator?.status === "failed" &&
    turn.status === "failed"
  ) {
    return buildFailedFinalAnswer()
  }

  // --- 3. Single Agent (source card, no inline full content) ---
  // A lone agent answer is shown as a compact source card with a
  // deterministic intro ("1 agent responded. Expand below…") instead of
  // being inlined into the body. This keeps a single-agent turn from
  // flashing full content that then collapses once a second agent joins.
  const activeSupervisorTurn =
    turn.isSupervisorTurn
    && !isTurnTerminal(turn.status)
    && (
      orchestrator != null
      || turn.processingStatusLogs.length > 0
      || real.length > 1
    )
  if (
    real.length === 1
    && !activeSupervisorTurn
    && !(orchestrator && orchestrator.content.trim().length > 0)
  ) {
    if (allRealTerminal(real)) {
      return buildDeterministicDoneFinalAnswer(turn, real, agentMessageIds, summary)
    }
    return { kind: "pending", label: "Working" }
  }

  // --- 4. Orchestrator-driven Derivation (New System Agent Architecture) ---
  if (orchestrator) {
    const orchestratorHasAnswer = orchestrator.content.trim().length > 0
    if (
      orchestratorHasAnswer
      && orchestrator.summaryOrigin !== "deterministic"
      && !isDeterministicDigestContent(orchestrator.content)
      && (
        orchestrator.status === "completed"
        || turn.turnTerminalStatus === "completed"
      )
    ) {
      const llmSummary = turn.agentResults.find(
        r => r.isSummaryAgent && r.summaryOrigin !== "deterministic"
      )
      return {
        kind: "llm_synthesis",
        label: "Synthesized",
        summaryOrigin: "llm",
        primaryMessageId: llmSummary?.messageId ?? summary?.messageId ?? orchestrator.messageId,
      }
    }

    if (orchestrator.status === "working" && !isTurnTerminal(turn.status)) {
      const isSynthesizing = shouldShowSynthesizingPhase(turn, real)
        || orchestrator.taskStatusMessage?.toLowerCase().includes('synthesiz')
        || turn.processingStatusLogs.some(log => log.message.toLowerCase().includes('synthesiz'))

      if (isSynthesizing) {
        const llmSummary = turn.agentResults.find(
          r => r.isSummaryAgent && r.summaryOrigin !== "deterministic"
        )
        return {
          kind: "llm_synthesis",
          label: "Synthesizing",
          summaryOrigin: "llm",
          primaryMessageId: llmSummary?.messageId ?? summary?.messageId,
        }
      }
      return { kind: "pending", label: "Working" }
    }

    if (
      !terminalTurnWithEmptyWorkingSummary
      && (hasLlmSynthesisContent(summary) || turnHasSubstantiveLlmSynthesis(turn))
    ) {
      const llmSummary = turn.agentResults.find(
        r => r.isSummaryAgent && r.summaryOrigin !== "deterministic"
      )
      return {
        kind: "llm_synthesis",
        label: "Synthesized",
        summaryOrigin: "llm",
        primaryMessageId: llmSummary?.messageId ?? summary?.messageId,
      }
    }

    if (isPreSynthesisGap(turn, real) || shouldHoldPendingForSynthesisGap(turn, real)) {
      return { kind: "pending", label: "Working" }
    }

    return buildDeterministicDoneFinalAnswer(turn, real, agentMessageIds, summary)
  }

  // --- 5. Legacy Fallback (Backward Compatibility for old turns without system:hybro) ---
  if (hasDeterministicSummaryEntity(summary)) {
    return buildDeterministicDoneFinalAnswer(turn, real, agentMessageIds, summary)
  }

  if (hasLlmSynthesisContent(summary) || turnHasSubstantiveLlmSynthesis(turn)) {
    const llmSummary = turn.agentResults.find(
      r => r.isSummaryAgent && r.summaryOrigin !== "deterministic"
    )
    return {
      kind: "llm_synthesis",
      label: "Synthesized",
      summaryOrigin: "llm",
      primaryMessageId: llmSummary?.messageId ?? summary?.messageId,
    }
  }

  if (
    turn.turnCompletionKind === "deterministic" &&
    real.length >= 2 &&
    allRealTerminal(real) &&
    !hasActiveSynthesisGap(turn) &&
    !turnHasSubstantiveLlmSynthesis(turn)
  ) {
    return buildDeterministicDoneFinalAnswer(turn, real, agentMessageIds, summary)
  }

  if (shouldHoldPendingForSynthesisGap(turn, real)) {
    return { kind: "pending", label: "Working" }
  }

  if (
    turn.turnCompletionKind === "synthesis" &&
    real.length >= 2 &&
    allRealTerminal(real) &&
    !hasActiveSynthesisGap(turn) &&
    !hasSynthesisSignalInProcessingLogs(turn) &&
    !hasLlmSynthesisContent(summary)
  ) {
    return buildDeterministicDoneFinalAnswer(turn, real, agentMessageIds, summary)
  }

  if (real.some(r => r.status === "working")) {
    return { kind: "pending", label: "Working" }
  }

  if (isMultiAgentTurnReadyForDeterministicDone(turn, real)) {
    return buildDeterministicDoneFinalAnswer(turn, real, agentMessageIds, summary)
  }

  if (real.length >= 2) {
    return { kind: "pending", label: "Working" }
  }

  return { kind: "pending", label: "Working" }
}

/** Map final answer kind to legacy displayMode for incremental migration. */
export function deriveDisplayModeFromFinalAnswer(
  turn: TurnViewModel,
  finalAnswer: FinalAnswerViewModel,
): TurnViewModel['displayMode'] {
  const realCount = getStripSourceResults(turn).length

  switch (finalAnswer.kind) {
    case 'hitl':
      return 'awaiting_input'
    case 'llm_synthesis':
    case 'deterministic_done':
    case 'canceled':
    case 'failed':
      return realCount >= 2 ? 'summary_with_sources' : 'single_agent'
    case 'single':
      return 'single_agent'
    case 'pending':
      return turn.status === 'active' ? 'working' : 'parallel_results'
    default:
      return 'working'
  }
}

export function derivePrimaryStreamFromFinalAnswer(
  finalAnswer: FinalAnswerViewModel,
): string | undefined {
  if (finalAnswer.primaryMessageId) return finalAnswer.primaryMessageId
  return undefined
}

export { isSupervisorClarifyAgent } from '@/lib/system-agents'

export function parseSummaryOrigin(value: unknown): SummaryOrigin | undefined {
  if (value === 'deterministic' || value === 'llm') return value
  return undefined
}
