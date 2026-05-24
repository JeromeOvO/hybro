import { describe, expect, it } from 'vitest'
import type { TurnViewModel } from '@/lib/room-timeline/types'
import { getCollectingProgressLabel } from '@/lib/room-timeline/turn-live-shell'

function makeTurn(overrides: Partial<TurnViewModel>): TurnViewModel {
  return {
    id: 't1',
    roomId: 'r1',
    userMessageId: 'u1',
    userContent: 'hi',
    userAttachments: [],
    timestamp: '2026-01-01T00:00:00Z',
    status: 'failed',
    events: [],
    summary: null,
    agentResults: [],
    activeAgentIds: [],
    isSupervisorTurn: true,
    displayMode: 'parallel_results',
    phase: 'completed',
    finalAnswer: { kind: 'failed', label: 'Failed' },
    ...overrides,
  }
}

function makeAgent(
  overrides: Partial<TurnViewModel['agentResults'][number]>,
): TurnViewModel['agentResults'][number] {
  return {
    messageId: 'm1',
    agentName: 'Agent',
    status: 'failed',
    content: 'failed',
    isSummaryAgent: false,
    isEphemeral: false,
    artifacts: [],
    ...overrides,
  }
}

describe('getCollectingProgressLabel', () => {
  it('does not say Preparing response when all agents failed', () => {
    const turn = makeTurn({
      agentResults: [
        makeAgent({ messageId: 'a1' }),
        makeAgent({ messageId: 'a2' }),
        makeAgent({ messageId: 'a3' }),
        makeAgent({ messageId: 'a4' }),
      ],
    })
    const label = getCollectingProgressLabel(turn)
    expect(label).toBe('All 4 agents failed')
    expect(label).not.toContain('Preparing')
  })
})
