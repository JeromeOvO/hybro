import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '../../../utils/test-utils'
import { CanonicalTurnTrace } from '@/components/conversation/CanonicalTurnTrace'
import type { TurnActivityItem, TurnProjection } from '@/lib/turn-lifecycle/types'
import { useTurnPresentationStore } from '@/stores/turn-presentation-store'

function turn(activity: TurnActivityItem[]): TurnProjection {
  return {
    id: 'run-1',
    runId: 'run-1',
    roomId: 'room-1',
    userMessageId: 'user-1',
    clientRequestId: 'client-1',
    state: 'active',
    startedAt: '2030-01-01T00:00:00.000Z',
    durationMs: 1000,
    internalTurns: [{
      internalTurnId: 'turn-1', attempt: 1, messageIds: [], toolCallIds: [], status: 'active',
    }],
    activity,
    finalCommitted: false,
    hitlInteractions: [],
    agentCallMessageIds: [],
  }
}

function decision(
  id: string,
  decisionValue: Extract<TurnActivityItem, { kind: 'decision' }>['decision'],
  overrides: Partial<Extract<TurnActivityItem, { kind: 'decision' }>> = {},
): Extract<TurnActivityItem, { kind: 'decision' }> {
  return {
    kind: 'decision',
    id,
    internalTurnId: 'turn-1',
    decision: decisionValue,
    order: 1,
    ...overrides,
  }
}

describe('CanonicalTurnTrace decision markers', () => {
  afterEach(() => {
    cleanup()
    useTurnPresentationStore.getState().clear()
  })

  it('renders all five model decision kinds', () => {
    render(
      <CanonicalTurnTrace
        turn={turn([
          decision('d1', 'interaction_received', { questionSummary: 'Which cloud?' }),
          decision('d2', 'answered_from_context', {
            agentLabel: 'Broker', questionSummary: 'Which cloud?',
            sourceSummary: 'from earlier messages and attachments',
          }),
          decision('d3', 'forwarded_to_user', {
            agentLabel: 'Broker', questionSummary: 'Which cloud?',
          }),
          decision('d4', 'no_progress', { reason: 'auto_reply_limit_reached' }),
          decision('d5', 'degraded_to_user', { reason: 'decision_turn_inconclusive' }),
        ])}
      />,
    )

    expect(screen.getByText('Agent requested input')).toBeDefined()
    expect(screen.getByText('Answered Broker from available information')).toBeDefined()
    expect(screen.getByText("Forwarding Broker's questions")).toBeDefined()
    expect(screen.getByText('Stopped: the agent made no progress')).toBeDefined()
    expect(screen.getByText('Handed the question to you')).toBeDefined()
    expect(screen.getByText('auto_reply_limit_reached')).toBeDefined()
    expect(screen.getByText('decision_turn_inconclusive')).toBeDefined()
  })
})
