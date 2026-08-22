import { describe, expect, it } from 'vitest'
import type { TurnViewModel } from '@/lib/room-timeline/types'
import {
  buildDeterministicIntro,
  CANCELED_TURN_INTRO,
  FAILED_TURN_INTRO,
  deriveDisplayModeFromFinalAnswer,
  deriveFinalAnswer,
  derivePrimaryStreamFromFinalAnswer,
  isDeterministicDigestContent,
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
    processingStatusLogs: [],
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

describe('isDeterministicDigestContent', () => {
  it('matches coordinator deterministic digest stub', () => {
    expect(isDeterministicDigestContent(buildDeterministicIntro(2))).toBe(true)
  })

  it('rejects substantive LLM synthesis text', () => {
    expect(
      isDeterministicDigestContent('Combined analysis from both agents with detailed findings.'),
    ).toBe(false)
  })
})

describe('deriveFinalAnswer', () => {
  it('returns hitl when turn is awaiting_input', () => {
    const turn = makeTurn({
      status: 'awaiting_input',
      agentResults: [
        makeAgent({
          messageId: 'c1',
          agentId: 'system:clarifier',
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
          agentName: 'Cyber Broker Agent',
          hitlPending: { prompt: 'Need more info', source: 'agent' },
        }),
      ],
    })
    const result = deriveFinalAnswer(turn, ['a1'])
    expect(result.kind).toBe('hitl')
    expect(result.hitl?.source).toBe('agent')
    expect(result.hitl?.prompts[0]?.agentName).toBe('Cyber Broker Agent')
  })

  it('returns llm_synthesis when summary agent has content', () => {
    const turn = makeTurn({
      turnTerminalStatus: 'completed',
      agentResults: [
        makeAgent({ messageId: 'a1' }),
        makeAgent({ messageId: 'a2' }),
        makeAgent({
          messageId: 's1',
          agentId: 'system:hybro',
          isSummaryAgent: true,
          content: 'Combined answer',
        }),
      ],
    })
    const result = deriveFinalAnswer(turn, ['a1', 'a2', 's1'])
    expect(result.kind).toBe('llm_synthesis')
    expect(result.primaryMessageId).toBe('s1')
  })

  it('returns llm_synthesis for summary-* supervisor stream with substantive content', () => {
    const turn = makeTurn({
      turnTerminalStatus: 'completed',
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
        makeAgent({
          messageId: 'summary-u1',
          agentId: 'summary',
          isSummaryAgent: true,
          summaryOrigin: 'llm',
          content: 'Unified synthesis combining both agent perspectives in detail.',
        }),
      ],
    })
    const result = deriveFinalAnswer(turn, ['a1', 'a2', 'summary-u1'])
    expect(result.kind).toBe('llm_synthesis')
    expect(result.primaryMessageId).toBe('summary-u1')
  })

  it('returns deterministic_done when summary entity is deterministic', () => {
    const turn = makeTurn({
      turnTerminalStatus: 'completed',
      agentResults: [
        makeAgent({ messageId: 'a1' }),
        makeAgent({ messageId: 'a2' }),
        makeAgent({
          messageId: 's1',
          agentId: 'system:hybro',
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

  it('returns pending when processing log signals synthesis started', () => {
    const turn = makeTurn({
      status: 'active',
      processingStatusLogs: [{ id: 'l1', message: 'Synthesizing responses...', timestamp: '2026-01-01T00:00:03.000Z' }],
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a', content: 'A' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b', content: 'B' }),
      ],
    })
    expect(deriveFinalAnswer(turn, ['a1', 'a2']).kind).toBe('pending')
  })

  it('returns active llm_synthesis while the orchestrator is synthesizing', () => {
    const turn = makeTurn({
      status: 'active',
      isSupervisorTurn: true,
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a', status: 'completed', content: 'A' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b', status: 'completed', content: 'B' }),
        makeAgent({
          messageId: 'hybro-1',
          agentId: 'system:hybro',
          agentName: 'HYBRO AI',
          isSummaryAgent: true,
          summaryOrigin: 'llm',
          status: 'working',
          content: '',
        }),
      ],
    })
    expect(deriveFinalAnswer(turn, ['a1', 'a2'])).toMatchObject({
      kind: 'llm_synthesis',
      label: 'Synthesizing',
      summaryOrigin: 'llm',
      primaryMessageId: 'hybro-1',
    })
  })

  it('returns deterministic_done when supervisor turn has no synthesis gap', () => {
    const turn = makeTurn({
      status: 'active',
      turnTerminalStatus: 'completed',
      turnCompletionKind: 'deterministic',
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
      turnCompletionKind: 'deterministic',
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

  it('resolves a single substantive agent to a deterministic source card', () => {
    const turn = makeTurn({
      agentResults: [makeAgent({ messageId: 'a1', content: 'Only answer' })],
    })
    const result = deriveFinalAnswer(turn, ['a1'])
    expect(result.kind).toBe('deterministic_done')
  })

  it('keeps a single streaming agent pending (no inline flash)', () => {
    const turn = makeTurn({
      status: 'active',
      agentResults: [makeAgent({ messageId: 'a1', status: 'working', content: '' })],
    })
    const result = deriveFinalAnswer(turn, ['a1'])
    expect(result.kind).toBe('pending')
  })

  it('stays pending while turn is active even if all agents are terminal (anti-flash)', () => {
    const turn = makeTurn({
      status: 'active',
      turnTerminalStatus: undefined,
      isSupervisorTurn: true,
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a', content: 'A' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b', content: 'B' }),
      ],
    })
    expect(deriveFinalAnswer(turn, ['a1', 'a2']).kind).toBe('pending')
  })

  it('stays pending when all agents terminal but no completion signal yet', () => {
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

  it('returns deterministic_done when turnCompletionKind is deterministic before terminal stamp', () => {
    const turn = makeTurn({
      status: 'active',
      turnCompletionKind: 'deterministic',
      isSupervisorTurn: true,
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a', content: 'A' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b', content: 'B' }),
      ],
    })
    const result = deriveFinalAnswer(turn, ['a1', 'a2'])
    expect(result.kind).toBe('deterministic_done')
    expect(result.deterministicIntro).toContain('2 agents')
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
      turnCompletionKind: 'deterministic',
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
      turnCompletionKind: 'deterministic',
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

  it('returns deterministic_done for mixed terminal agents without turn stamp', () => {
    const turn = makeTurn({
      status: 'partial',
      processingStatusLogs: [{ id: 'l1', message: 'Synthesizing responses...', timestamp: '2026-01-01T00:00:03Z' }],
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a', status: 'completed', content: 'A' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b', status: 'failed', content: 'Error' }),
      ],
    })
    const result = deriveFinalAnswer(turn, ['a1', 'a2'])
    expect(result.kind).toBe('deterministic_done')
    expect(result.sections?.map(s => s.messageId)).toEqual(['a1', 'a2'])
  })

  it('returns deterministic_done for mixed terminal when turnTerminalStatus is completed', () => {
    const turn = makeTurn({
      status: 'partial',
      turnTerminalStatus: 'completed',
      agentResults: [
        makeAgent({ messageId: 'a1', status: 'completed', content: 'A' }),
        makeAgent({ messageId: 'a2', status: 'failed', content: 'Error' }),
      ],
    })
    expect(deriveFinalAnswer(turn, ['a1', 'a2']).kind).toBe('deterministic_done')
  })

  it('resolves to deterministic_done for completed supervisor turn when completion kind is undefined (no synthesis)', () => {
    const turn = makeTurn({
      status: 'active',
      isSupervisorTurn: true,
      turnTerminalStatus: 'completed',
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a', status: 'completed', content: 'A' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b', status: 'completed', content: 'B' }),
      ],
    })
    expect(deriveFinalAnswer(turn, ['a1', 'a2']).kind).toBe('deterministic_done')
  })

  it('stays pending after agents finish when synthesis is actively in flight', () => {
    const turn = makeTurn({
      status: 'active',
      turnCompletionKind: 'synthesis',
      processingStatusLogs: [{ id: 'l1', message: 'Synthesizing responses...', timestamp: '2026-01-01T00:00:03.000Z' }],
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a', status: 'completed', content: 'A' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b', status: 'completed', content: 'B' }),
      ],
    })
    expect(deriveFinalAnswer(turn, ['a1', 'a2']).kind).toBe('pending')
  })

  it('returns deterministic_done when synthesis kind is set but no synthesis ran', () => {
    const turn = makeTurn({
      status: 'active',
      turnCompletionKind: 'synthesis',
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a', status: 'completed', content: 'A' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b', status: 'completed', content: 'B' }),
      ],
    })
    expect(deriveFinalAnswer(turn, ['a1', 'a2']).kind).toBe('deterministic_done')
  })

  it('returns deterministic_done for supervisor turn after backend stamps deterministic kind', () => {
    const turn = makeTurn({
      status: 'active',
      isSupervisorTurn: true,
      turnTerminalStatus: 'completed',
      turnCompletionKind: 'deterministic',
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a', status: 'completed', content: 'A' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b', status: 'completed', content: 'B' }),
      ],
    })
    expect(deriveFinalAnswer(turn, ['a1', 'a2']).kind).toBe('deterministic_done')
  })

  it('returns llm_synthesis when LLM summary exists despite stamped deterministic kind', () => {
    const turn = makeTurn({
      status: 'completed',
      turnTerminalStatus: 'completed',
      turnCompletionKind: 'deterministic',
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a', status: 'completed', content: 'A' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b', status: 'completed', content: 'B' }),
        makeAgent({
          messageId: 'summary-u1',
          agentId: 'summary',
          isSummaryAgent: true,
          status: 'completed',
          content: 'Synthesized combined answer with detailed findings.',
        }),
      ],
    })
    expect(deriveFinalAnswer(turn, ['a1', 'a2', 'summary-u1']).kind).toBe('llm_synthesis')
  })

  it('returns failed when all agents failed without turn stamp (stuck Working regression)', () => {
    const turn = makeTurn({
      status: 'failed',
      agentResults: [
        makeAgent({ messageId: 'a1', status: 'failed', content: 'timeout' }),
        makeAgent({ messageId: 'a2', status: 'failed', content: 'timeout' }),
      ],
    })
    expect(deriveFinalAnswer(turn, ['a1', 'a2']).kind).toBe('failed')
  })

  it('returns failed when supervisor orchestrator failed before dispatching agents', () => {
    const turn = makeTurn({
      status: 'failed',
      turnTerminalStatus: 'failed',
      agentResults: [
        makeAgent({
          messageId: 'sys-u1',
          agentId: 'system:hybro',
          agentName: 'HYBRO AI',
          status: 'failed',
          content: '',
        }),
      ],
    })

    const result = deriveFinalAnswer(turn, ['sys-u1'])

    expect(result.kind).toBe('failed')
    expect(result.failedIntro).toBe(FAILED_TURN_INTRO)
    expect(
      result.kind === 'deterministic_done' ? result.deterministicIntro : '',
    ).not.toContain('0 agents responded')
  })

  it('returns failed when the turn is terminal but a stale supervisor placeholder is still working', () => {
    const turn = makeTurn({
      status: 'failed',
      turnTerminalStatus: 'failed',
      agentResults: [
        makeAgent({
          messageId: 'sys-u1',
          agentId: 'system:hybro',
          agentName: 'HYBRO AI',
          status: 'working',
          content: '',
        }),
      ],
    })

    const result = deriveFinalAnswer(turn, ['sys-u1'])

    expect(result.kind).toBe('failed')
    expect(result.failedIntro).toBe(FAILED_TURN_INTRO)
  })

  it('returns Synthesized for contentful system:hybro when turn is already terminal', () => {
    const turn = makeTurn({
      status: 'active',
      turnTerminalStatus: 'completed',
      turnCompletionKind: 'synthesis',
      agentResults: [
        makeAgent({
          messageId: 'a1',
          agentId: 'agent-a',
          status: 'completed',
          content: 'Story',
        }),
        makeAgent({
          messageId: 'sys-u1',
          agentId: 'system:hybro',
          agentName: 'HYBRO AI',
          status: 'working',
          content: 'Combined story and image answer.',
        }),
      ],
    })

    expect(deriveFinalAnswer(turn, ['a1', 'sys-u1'])).toMatchObject({
      kind: 'llm_synthesis',
      label: 'Synthesized',
      primaryMessageId: 'sys-u1',
    })
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
