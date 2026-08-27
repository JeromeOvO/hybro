import type { AgentResultViewModel, FinalAnswerKind, TurnViewModel } from './types'
import { isSupervisorClarifyAgent } from '@/lib/system-agents'

export function getStripSourceResults(turn: TurnViewModel): AgentResultViewModel[] {
  return turn.agentResults.filter(r =>
    !r.isSummaryAgent && !r.isEphemeral && !isSupervisorClarifyAgent(r.agentId),
  )
}

export function getAgentIndexSummary(
  turn: TurnViewModel,
  sourceResults: AgentResultViewModel[],
  kind: FinalAnswerKind = turn.finalAnswer.kind,
): string {
  const total = sourceResults.length
  if (total === 0) return ''

  const isComplete = turn.phase === 'completed' || turn.status === 'completed'
  const done = sourceResults.filter(r => r.status === 'completed').length
  const working = sourceResults.filter(r => r.status === 'working').length

  switch (kind) {
    case 'deterministic_done':
      if (isComplete) return `Agent responses · ${total} call${total === 1 ? '' : 's'}`
      return [
        `Agent responses · ${total} call${total === 1 ? '' : 's'}`,
        done > 0 ? `${done} done` : '',
        working > 0 ? `${working} working` : '',
      ].filter(Boolean).join(' · ')
    case 'llm_synthesis':
      return `Sources · ${total} agent call${total === 1 ? '' : 's'}`
    case 'canceled':
      return `Canceled · ${total} call${total === 1 ? '' : 's'}`
    case 'failed': {
      const failedCount = sourceResults.filter(r => r.status === 'failed').length
      return `Failed · ${failedCount || total} of ${total} call${total === 1 ? '' : 's'}`
    }
    case 'hitl':
      return `Completed · ${total} call${total === 1 ? '' : 's'}`
    case 'pending':
    default:
      if (isComplete) return `Activity · ${total} call${total === 1 ? '' : 's'}`
      return [
        `Activity · ${total} call${total === 1 ? '' : 's'}`,
        done > 0 ? `${done} done` : '',
        working > 0 ? `${working} working` : '',
      ].filter(Boolean).join(' · ')
  }
}

export function getSupervisorStatusLine(turn: TurnViewModel): string | null {
  // Supervisor stage details are transient progress UI, never part of the
  // completed user-facing answer. Persisted/hydrated turns can still carry the
  // last stage (for example "Requesting ..."), so stop it at this boundary.
  if (turn.status !== 'active' || turn.phase === 'completed') return null

  const ephemeral = turn.agentResults.find(
    r => r.isEphemeral && (r.taskStatusMessage?.trim().length ?? 0) > 0,
  )
  const status = ephemeral?.taskStatusMessage?.trim()
    ?? turn.supervisorStage?.details?.trim()
    ?? null
  
  if (status && status.length > 200) {
    return null
  }
  
  const summary = turn.agentResults.find(r => r.isSummaryAgent)
  if (summary && status === summary.content.trim()) {
    return null
  }

  return status
}

/** Build dynamic progress label from agent results during collecting phase. */
export function getCollectingProgressLabel(turn: TurnViewModel): string {
  const real = turn.agentResults.filter(r =>
    !r.isSummaryAgent && !r.isEphemeral && !isSupervisorClarifyAgent(r.agentId),
  )
  if (real.length === 0) return 'Agents working on your request…'

  const completed = real.filter(r => r.status === 'completed')
  const failed = real.filter(r => r.status === 'failed')
  const working = real.filter(r => r.status === 'working')
  const terminal = completed.length + failed.length

  const activeAgent = working.find(r => r.taskStatusMessage?.trim())
  if (activeAgent?.taskStatusMessage) {
    const prefix = real.length > 1 ? `${terminal}/${real.length} done · ` : ''
    return `${prefix}${activeAgent.agentName}: ${activeAgent.taskStatusMessage.trim()}`
  }

  if (terminal > 0 && working.length > 0) {
    return `${terminal} of ${real.length} agents done · ${working.length} still working…`
  }

  if (failed.length === real.length && working.length === 0) {
    return `All ${real.length} agents failed`
  }

  if (completed.length > 0 && working.length === 0 && failed.length === 0) {
    return `All ${real.length} agents done · Preparing response…`
  }

  if (terminal > 0 && working.length === 0) {
    return `${completed.length} succeeded · ${failed.length} failed`
  }

  return `${real.length} agent${real.length === 1 ? '' : 's'} working on your request…`
}

/**
 * Whether AgentIndex should mount expanded. The last turn on a page matches the
 * post-completion layout (open index); older turns stay collapsed.
 */
export function defaultAgentIndexOpen(turn: TurnViewModel, isLastTurn: boolean): boolean {
  if (!isLastTurn) return false

  const { finalAnswer } = turn
  if (finalAnswer.kind === 'single') return false

  const isComplete =
    turn.phase === 'completed'
    || turn.status === 'completed'
    || turn.status === 'failed'
    || turn.status === 'partial'
  if (!isComplete) return true

  return (
    finalAnswer.kind === 'deterministic_done'
    || finalAnswer.kind === 'llm_synthesis'
    || finalAnswer.kind === 'canceled'
    || finalAnswer.kind === 'failed'
    || finalAnswer.kind === 'hitl'
  )
}
