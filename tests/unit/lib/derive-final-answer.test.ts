import { describe, expect, it } from 'vitest'
import type { TurnViewModel } from '@/lib/room-timeline/types'
import {
  buildDeterministicIntro,
  CANCELED_TURN_INTRO,
  FAILED_TURN_INTRO,
  deriveDisplayModeFromFinalAnswer,
  deriveFinalAnswer,
  derivePrimaryStreamFromFinalAnswer,
} from '@/lib/room-timeline/derive-final-answer'

function makeTurn(overrides: Partial<TurnViewModel>): TurnViewModel {
  return {
    id: 't1',
    roomId: 'r1',
    userMessageId: 'u1',
    userContent: 'hi',
    userAttachments: [],
    timestamp: '2026-01-01T00:00:00Z',
    status: 'completed',
    events: [],
    summary: null,
    agentResults: [],
    activeAgentIds: [],
    isSupervisorTurn: true,
    displayMode: 'parallel_results',
    phase: 'completed',
    finalAnswer: { kind: 'pending', label: 'Working' },
    ...overrides,
  }
}

function makeAgent(
  overrides: Partial<TurnViewModel['agentResults'][number]>,
): TurnViewModel['agentResults'][number] {
  return {
    messageId: 'm1',
    agentName: 'Agent',
    status: 'completed',
    content: 'response',
    isSummaryAgent: false,
    isEphemeral: false,
    artifacts: [],
    ...overrides,
  }
}

describe('buildDeterministicIntro', () => {
  it('pluralizes agent count', () => {
    expect(buildDeterministicIntro(2)).toContain('2 agents')
  })
})

describe('deriveFinalAnswer', () => {
  it('returns hitl when turn is awaiting_input', () => {
    const turn = makeTurn({
      status: 'awaiting_input',
      agentResults: [
        makeAgent({
          messageId: 'c1',
          agentId: 'supervisor_clarify',
          agentName: 'HYBRO AI',
          content: 'Which city?',
        }),
      ],
    })
    const result = deriveFinalAnswer(turn, [])
    expect(result.kind).toBe('hitl')
    expect(result.hitl?.prompts[0]?.prompt).toBe('Which city?')
  })

  it('returns hitl when agent has hitlPending', () => {
    const turn = makeTurn({
      status: 'active',
      agentResults: [
        makeAgent({
          messageId: 'a1',
          hitlPending: { prompt: 'Need more info' },
        }),
      ],
    })
    expect(deriveFinalAnswer(turn, ['a1']).kind).toBe('hitl')
  })

  it('returns llm_synthesis when summary agent has content', () => {
    const turn = makeTurn({
      turnTerminalStatus: 'completed',
      agentResults: [
        makeAgent({ messageId: 'a1' }),
        makeAgent({ messageId: 'a2' }),
        makeAgent({
          messageId: 's1',
          agentId: 'supervisor_synthesis',
          isSummaryAgent: true,
          content: 'Combined answer',
        }),
      ],
    })
    const result = deriveFinalAnswer(turn, ['a1', 'a2', 's1'])
    expect(result.kind).toBe('llm_synthesis')
    expect(result.primaryMessageId).toBe('s1')
  })

  it('returns deterministic_done when summary entity is deterministic', () => {
    const turn = makeTurn({
      turnTerminalStatus: 'completed',
      agentResults: [
        makeAgent({ messageId: 'a1' }),
        makeAgent({ messageId: 'a2' }),
        makeAgent({
          messageId: 's1',
          agentId: 'supervisor_synthesis',
          isSummaryAgent: true,
          summaryOrigin: 'deterministic',
          content: '2 agents responded.',
        }),
      ],
    })
    const result = deriveFinalAnswer(turn, ['a1', 'a2', 's1'])
    expect(result.kind).toBe('deterministic_done')
    expect(result.primaryMessageId).toBe('s1')
  })

  it('returns pending during synthesis gap', () => {
    const turn = makeTurn({
      status: 'active',
      phase: 'synthesizing',
      agentResults: [
        makeAgent({ messageId: 'a1', status: 'completed' }),
        makeAgent({ messageId: 'a2', status: 'completed' }),
        makeAgent({
          messageId: 'e1',
          isEphemeral: true,
          taskStatusMessage: 'Synthesizing responses',
        }),
      ],
    })
    expect(deriveFinalAnswer(turn, ['a1', 'a2']).kind).toBe('pending')
  })

  it('returns pending when supervisor turn has active synthesis gap', () => {
    const turn = makeTurn({
      status: 'active',
      isSupervisorTurn: true,
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a', content: 'A' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b', content: 'B' }),
        makeAgent({
          messageId: 'e1',
          isEphemeral: true,
          taskStatusMessage: 'Synthesizing responses',
        }),
      ],
    })
    expect(deriveFinalAnswer(turn, ['a1', 'a2']).kind).toBe('pending')
  })

  it('returns deterministic_done when supervisor turn has no synthesis gap', () => {
    const turn = makeTurn({
      status: 'active',
      turnTerminalStatus: 'completed',
      isSupervisorTurn: true,
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a', content: 'A' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b', content: 'B' }),
        makeAgent({
          messageId: 's0',
          agentId: 'supervisor_hitl',
          content: '',
        }),
      ],
    })
    expect(deriveFinalAnswer(turn, ['a1', 'a2', 's0']).kind).toBe('deterministic_done')
  })

  it('returns deterministic_done when room terminal despite stale synthesizing ephemeral', () => {
    const turn = makeTurn({
      status: 'active',
      turnTerminalStatus: 'completed',
      isSupervisorTurn: true,
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a', content: 'A' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b', content: 'B' }),
        makeAgent({
          messageId: 'e1',
          isEphemeral: true,
          taskStatusMessage: 'Synthesizing responses',
        }),
      ],
    })
    expect(deriveFinalAnswer(turn, ['a1', 'a2']).kind).toBe('deterministic_done')
  })

  it('returns single for one substantive agent', () => {
    const turn = makeTurn({
      agentResults: [makeAgent({ messageId: 'a1', content: 'Only answer' })],
    })
    const result = deriveFinalAnswer(turn, ['a1'])
    expect(result.kind).toBe('single')
    expect(result.primaryMessageId).toBe('a1')
  })

  it('returns single for a streaming single agent', () => {
    const turn = makeTurn({
      status: 'active',
      agentResults: [makeAgent({ messageId: 'a1', status: 'working', content: '' })],
    })
    const result = deriveFinalAnswer(turn, ['a1'])
    expect(result.kind).toBe('single')
    expect(result.primaryMessageId).toBe('a1')
  })

  it('stays pending when all agents done but turnTerminalStatus not set (anti-flash)', () => {
    const turn = makeTurn({
      status: 'completed',
      turnTerminalStatus: undefined,
      isSupervisorTurn: true,
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a', content: 'A' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b', content: 'B' }),
      ],
    })
    expect(deriveFinalAnswer(turn, ['a1', 'a2']).kind).toBe('pending')
  })

  it('returns canceled when turnTerminalStatus is canceled', () => {
    const turn = makeTurn({
      status: 'completed',
      turnTerminalStatus: 'canceled',
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a', content: 'Task was canceled' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b', content: 'Task was canceled' }),
      ],
    })
    const result = deriveFinalAnswer(turn, ['a1', 'a2'])
    expect(result.kind).toBe('canceled')
    expect(result.canceledIntro).toBe(CANCELED_TURN_INTRO)
  })

  it('returns canceled when all agents failed with cancel copy (inferred failed stamp)', () => {
    const turn = makeTurn({
      status: 'failed',
      turnTerminalStatus: 'failed',
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a', status: 'failed', content: 'Task was canceled' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b', status: 'failed', content: 'Task was canceled' }),
        makeAgent({ messageId: 'a3', agentId: 'agent-c', status: 'failed', content: 'Task was canceled' }),
        makeAgent({ messageId: 'a4', agentId: 'agent-d', status: 'failed', content: 'Task was canceled' }),
      ],
    })
    expect(deriveFinalAnswer(turn, ['a1', 'a2', 'a3', 'a4']).kind).toBe('canceled')
  })

  it('returns failed when all agents failed after restart', () => {
    const turn = makeTurn({
      status: 'failed',
      turnTerminalStatus: 'failed',
      agentResults: [
        makeAgent({ messageId: 'a1', status: 'failed', content: 'Task failed due to timeout' }),
        makeAgent({ messageId: 'a2', status: 'failed', content: 'Task failed due to timeout' }),
        makeAgent({ messageId: 'a3', status: 'failed', content: 'Task failed due to timeout' }),
        makeAgent({ messageId: 'a4', status: 'failed', content: 'Task failed due to timeout' }),
      ],
    })
    const result = deriveFinalAnswer(turn, ['a1', 'a2', 'a3', 'a4'])
    expect(result.kind).toBe('failed')
    expect(result.failedIntro).toBe(FAILED_TURN_INTRO)
  })

  it('returns failed when all agents failed without turnTerminalStatus (hydration gap)', () => {
    const turn = makeTurn({
      status: 'failed',
      agentResults: [
        makeAgent({ messageId: 'a1', status: 'failed', content: 'error' }),
        makeAgent({ messageId: 'a2', status: 'failed', content: 'error' }),
      ],
    })
    expect(deriveFinalAnswer(turn, ['a1', 'a2']).kind).toBe('failed')
  })

  it('does not use deterministic_done when deterministic summary exists on canceled turn', () => {
    const turn = makeTurn({
      status: 'completed',
      turnTerminalStatus: 'canceled',
      agentResults: [
        makeAgent({ messageId: 'a1', status: 'failed', content: 'Task was canceled' }),
        makeAgent({ messageId: 'a2', status: 'failed', content: 'Task was canceled' }),
        makeAgent({
          messageId: 's1',
          agentId: 'summary',
          isSummaryAgent: true,
          summaryOrigin: 'deterministic',
          content: '4 agents responded. Expand below to read each answer.',
        }),
      ],
    })
    expect(deriveFinalAnswer(turn, ['a1', 'a2', 's1']).kind).toBe('canceled')
  })

  it('returns deterministic_done for terminal multi-agent without synthesis', () => {
    const turn = makeTurn({
      status: 'completed',
      turnTerminalStatus: 'completed',
      isSupervisorTurn: false,
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a', content: 'A' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b', content: 'B' }),
      ],
    })
    const result = deriveFinalAnswer(turn, ['a1', 'a2'])
    expect(result.kind).toBe('deterministic_done')
    expect(result.deterministicIntro).toContain('2 agents')
    expect(result.sections?.map(s => s.messageId)).toEqual(['a1', 'a2'])
  })

  it('orders sections by scaffold agentMessageIds', () => {
    const turn = makeTurn({
      status: 'completed',
      turnTerminalStatus: 'completed',
      isSupervisorTurn: false,
      agentResults: [
        makeAgent({ messageId: 'a1', content: 'A' }),
        makeAgent({ messageId: 'a2', content: 'B' }),
      ],
    })
    const result = deriveFinalAnswer(turn, ['a2', 'a1'])
    expect(result.sections?.map(s => s.messageId)).toEqual(['a2', 'a1'])
  })

  it('returns pending while agents are working', () => {
    const turn = makeTurn({
      status: 'active',
      agentResults: [
        makeAgent({ messageId: 'a1', status: 'working', content: '' }),
        makeAgent({ messageId: 'a2', status: 'completed', content: 'B' }),
      ],
    })
    expect(deriveFinalAnswer(turn, ['a1', 'a2']).kind).toBe('pending')
  })
})

describe('derivePrimaryStreamFromFinalAnswer', () => {
  it('uses primaryMessageId when set', () => {
    expect(
      derivePrimaryStreamFromFinalAnswer({
        kind: 'llm_synthesis',
        label: 'Synthesized',
        primaryMessageId: 's1',
      }),
    ).toBe('s1')
  })

  it('returns undefined for virtual deterministic_done without entity', () => {
    expect(
      derivePrimaryStreamFromFinalAnswer({
        kind: 'deterministic_done',
        label: 'Combined agent responses',
        deterministicIntro: buildDeterministicIntro(2),
      }),
    ).toBeUndefined()
  })
})

describe('deriveDisplayModeFromFinalAnswer', () => {
  it('maps deterministic_done to summary_with_sources for multi-agent', () => {
    const turn = makeTurn({
      agentResults: [
        makeAgent({ messageId: 'a1' }),
        makeAgent({ messageId: 'a2' }),
      ],
    })
    expect(
      deriveDisplayModeFromFinalAnswer(turn, {
        kind: 'deterministic_done',
        label: 'Combined agent responses',
      }),
    ).toBe('summary_with_sources')
  })
})
