import { describe, expect, it } from 'vitest'
import {
  hasActiveSynthesisGap,
  isBackendRunConfirmedNonSynthesisCompletion,
  isDeterministicCompletionExpected,
  isMixedTerminalMultiAgentTurn,
  isMultiAgentTurnReadyForDeterministicDone,
  isPreSynthesisGap,
  shouldShowSynthesizingPhase,
} from '@/lib/room-timeline/multi-agent-turn-complete'
import type { AgentResultViewModel, TurnViewModel } from '@/lib/room-timeline/types'

function makeAgent(overrides: Partial<AgentResultViewModel> = {}): AgentResultViewModel {
  return {
    agentId: 'agent-a',
    agentName: 'Agent A',
    messageId: 'a1',
    status: 'completed',
    content: 'Answer',
    artifacts: [],
    isSummaryAgent: false,
    ...overrides,
  }
}

function makeTurn(overrides: Partial<TurnViewModel> = {}): TurnViewModel {
  return {
    id: 'turn-1',
    roomId: 'room-1',
    userMessageId: 'u1',
    userContent: 'hello',
    userAttachments: [],
    timestamp: '2026-01-01T00:00:00.000Z',
    status: 'completed',
    events: [],
    summary: null,
    agentResults: [],
    activeAgentIds: [],
    isSupervisorTurn: true,
    processingStatusLogs: [],
    displayMode: 'working',
    finalAnswer: { kind: 'pending', label: 'Working' },
    ...overrides,
  }
}

describe('isMultiAgentTurnReadyForDeterministicDone', () => {
  it('returns false when derived turn is completed but turnTerminalStatus is unset', () => {
    const turn = makeTurn({
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
      ],
    })
    expect(isMultiAgentTurnReadyForDeterministicDone(turn)).toBe(false)
  })

  it('returns true when turnTerminalStatus is completed with turnCompletionKind=deterministic', () => {
    const turn = makeTurn({
      turnTerminalStatus: 'completed',
      turnCompletionKind: 'deterministic',
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
      ],
    })
    expect(isMultiAgentTurnReadyForDeterministicDone(turn)).toBe(true)
  })

  it('returns false when turnTerminalStatus is completed with turnCompletionKind=synthesis', () => {
    const turn = makeTurn({
      turnTerminalStatus: 'completed',
      turnCompletionKind: 'synthesis',
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
      ],
    })
    expect(isMultiAgentTurnReadyForDeterministicDone(turn)).toBe(false)
  })

  it('returns true when turnTerminalStatus is completed without turnCompletionKind on supervisor turn', () => {
    const turn = makeTurn({
      turnTerminalStatus: 'completed',
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
      ],
    })
    expect(isMultiAgentTurnReadyForDeterministicDone(turn)).toBe(true)
  })

  it('returns true when turnTerminalStatus is completed without turnCompletionKind on non-supervisor turn', () => {
    const turn = makeTurn({
      isSupervisorTurn: false,
      turnTerminalStatus: 'completed',
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
      ],
    })
    expect(isMultiAgentTurnReadyForDeterministicDone(turn)).toBe(true)
  })

  it('returns true when turnTerminalStatus is completed without turnCompletionKind but deterministic entity present', () => {
    const turn = makeTurn({
      turnTerminalStatus: 'completed',
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
        makeAgent({
          messageId: 'summary-1',
          agentId: 'system:hybro',
          isSummaryAgent: true,
          summaryOrigin: 'deterministic',
          content: '2 agents responded',
        }),
      ],
    })
    expect(isMultiAgentTurnReadyForDeterministicDone(turn)).toBe(true)
  })

  it('returns false when turnTerminalStatus is completed but LLM summary exists', () => {
    const turn = makeTurn({
      turnTerminalStatus: 'completed',
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
        makeAgent({
          messageId: 'summary-u1',
          agentId: 'summary',
          isSummaryAgent: true,
          status: 'working',
          content: 'Here is the combined...',
        }),
      ],
    })
    expect(isMultiAgentTurnReadyForDeterministicDone(turn)).toBe(false)
  })

  it('returns false when turnTerminalStatus is completed and synthesis signal in logs but no summary entity', () => {
    const turn = makeTurn({
      turnTerminalStatus: 'completed',
      processingStatusLogs: [{ id: 'l1', message: 'Synthesizing responses...', timestamp: '2026-01-01T00:00:03.000Z' }],
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
      ],
    })
    expect(isMultiAgentTurnReadyForDeterministicDone(turn)).toBe(false)
  })

  it('returns false while turn is still active', () => {
    const turn = makeTurn({
      status: 'active',
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
      ],
    })
    expect(isMultiAgentTurnReadyForDeterministicDone(turn)).toBe(false)
  })

  it('returns false while a synthesis gap ephemeral is active', () => {
    const turn = makeTurn({
      status: 'completed',
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
        makeAgent({
          messageId: 'ephemeral-1',
          isEphemeral: true,
          status: 'working',
          taskStatusMessage: 'Synthesizing final answer…',
          content: '',
        }),
      ],
    })
    expect(isMultiAgentTurnReadyForDeterministicDone(turn)).toBe(false)
  })

  it('returns true when turnCompletionKind is deterministic without turnTerminalStatus', () => {
    const turn = makeTurn({
      status: 'active',
      turnCompletionKind: 'deterministic',
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
      ],
    })
    expect(isMultiAgentTurnReadyForDeterministicDone(turn)).toBe(true)
  })

  it('returns true when deterministic summary entity arrives over SSE', () => {
    const turn = makeTurn({
      status: 'active',
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
        makeAgent({
          messageId: 'summary-u1',
          agentId: 'summary',
          isSummaryAgent: true,
          summaryOrigin: 'deterministic',
          content: '2 agents responded. Expand below to read each answer.',
        }),
      ],
    })
    expect(isMultiAgentTurnReadyForDeterministicDone(turn)).toBe(true)
  })
})

describe('isDeterministicCompletionExpected', () => {
  it('returns true for supervisor turn with terminal stamp but unknown completion kind', () => {
    const turn = makeTurn({
      turnTerminalStatus: 'completed',
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
      ],
    })
    expect(isDeterministicCompletionExpected(turn)).toBe(true)
  })

  it('returns true for mixed terminal agents without synthesis', () => {
    const turn = makeTurn({
      agentResults: [
        makeAgent({ messageId: 'a1', status: 'completed', content: 'A' }),
        makeAgent({ messageId: 'a2', status: 'failed', content: 'Error' }),
      ],
    })
    expect(isMixedTerminalMultiAgentTurn(turn.agentResults)).toBe(true)
    expect(isDeterministicCompletionExpected(turn)).toBe(true)
  })

  it('returns false when inquiry reports synthesis kind', () => {
    const turn = makeTurn({
      agentResults: [
        makeAgent({ messageId: 'a1', status: 'completed', content: 'A' }),
        makeAgent({ messageId: 'a2', status: 'completed', content: 'B' }),
      ],
    })
    expect(isDeterministicCompletionExpected(turn, undefined, 'synthesis')).toBe(false)
  })

  it('returns true for supervisor turn with stamped deterministic completion', () => {
    const turn = makeTurn({
      isSupervisorTurn: true,
      turnTerminalStatus: 'completed',
      turnCompletionKind: 'deterministic',
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
      ],
    })
    expect(isDeterministicCompletionExpected(turn)).toBe(true)
  })

  it('returns false when non-deterministic LLM summary has content', () => {
    const turn = makeTurn({
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
        makeAgent({
          messageId: 'summary-u1',
          agentId: 'summary',
          isSummaryAgent: true,
          content: 'Synthesized combined answer from both agents.',
        }),
      ],
    })
    expect(isDeterministicCompletionExpected(turn)).toBe(false)
  })
})

describe('isBackendRunConfirmedNonSynthesisCompletion', () => {
  it('returns true for supervisor turn when backend run is inactive and no synthesis evidence', () => {
    const turn = makeTurn({
      isSupervisorTurn: true,
      turnCompletionKind: 'deterministic',
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
      ],
    })
    expect(isBackendRunConfirmedNonSynthesisCompletion(turn)).toBe(true)
  })

  it('returns false when inquiry reports synthesis kind', () => {
    const turn = makeTurn({
      agentResults: [
        makeAgent({ messageId: 'a1', status: 'completed', content: 'A' }),
        makeAgent({ messageId: 'a2', status: 'completed', content: 'B' }),
      ],
    })
    expect(isBackendRunConfirmedNonSynthesisCompletion(turn, undefined, 'synthesis')).toBe(false)
  })

  it('returns false when LLM summary entity has substantive content', () => {
    const turn = makeTurn({
      agentResults: [
        makeAgent({ messageId: 'a1', status: 'completed', content: 'A' }),
        makeAgent({ messageId: 'a2', status: 'completed', content: 'B' }),
        makeAgent({
          messageId: 'summary-u1',
          agentId: 'summary',
          isSummaryAgent: true,
          content: 'Combined synthesis answer.',
        }),
      ],
    })
    expect(isBackendRunConfirmedNonSynthesisCompletion(turn)).toBe(false)
  })
})

describe('hasActiveSynthesisGap', () => {
  it('returns false when turnTerminalStatus is set even with stale synthesis log', () => {
    const turn = makeTurn({
      turnTerminalStatus: 'completed',
      processingStatusLogs: [{ id: 'l1', message: 'Synthesizing responses...', timestamp: '2026-01-01T00:00:03.000Z' }],
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
      ],
    })
    expect(hasActiveSynthesisGap(turn)).toBe(false)
  })

  it('returns false when deterministic digest entity is present despite synthesis logs', () => {
    const turn = makeTurn({
      processingStatusLogs: [{ id: 'l1', message: 'Synthesizing responses...', timestamp: '2026-01-01T00:00:03.000Z' }],
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
        makeAgent({
          messageId: 'summary-u1',
          agentId: 'summary',
          isSummaryAgent: true,
          summaryOrigin: 'deterministic',
          content: '2 agents responded. Expand below to read each answer.',
        }),
      ],
    })
    expect(hasActiveSynthesisGap(turn)).toBe(false)
  })

  it('returns true when synthesis signal in logs and no resolution', () => {
    const turn = makeTurn({
      processingStatusLogs: [{ id: 'l1', message: 'Synthesizing responses...', timestamp: '2026-01-01T00:00:03.000Z' }],
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
      ],
    })
    expect(hasActiveSynthesisGap(turn)).toBe(true)
  })

  it('returns true when processing log has turnPhase synthesizing', () => {
    const turn = makeTurn({
      processingStatusLogs: [{
        id: 'l1',
        message: 'Working on your request',
        turnPhase: 'synthesizing',
        timestamp: '2026-01-01T00:00:03.000Z',
      }],
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
      ],
    })
    expect(hasActiveSynthesisGap(turn)).toBe(true)
  })

  it('returns false when some agents failed and synthesis never started', () => {
    const turn = makeTurn({
      processingStatusLogs: [{ id: 'l1', message: 'Synthesizing responses...', timestamp: '2026-01-01T00:00:03.000Z' }],
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a', status: 'completed', content: 'A' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b', status: 'failed', content: 'Error' }),
      ],
    })
    expect(hasActiveSynthesisGap(turn)).toBe(false)
  })

  it('returns true when turnTerminalStatus is failed and all agents terminal', () => {
    const turn = makeTurn({
      turnTerminalStatus: 'failed',
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a', status: 'completed', content: 'A' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b', status: 'failed', content: 'Error' }),
      ],
    })
    expect(isMultiAgentTurnReadyForDeterministicDone(turn)).toBe(true)
  })
})

describe('isPreSynthesisGap', () => {
  it('returns true when only delegation logs exist on active supervisor turn (pre-synthesis gap)', () => {
    const turn = makeTurn({
      status: 'active',
      isSupervisorTurn: true,
      processingStatusLogs: [{ id: 'l1', message: 'Delegating to 2 agent(s)...', timestamp: '2026-01-01T00:00:01.000Z' }],
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
      ],
    })
    expect(isPreSynthesisGap(turn)).toBe(true)
  })

  it('returns false for hydrated multi-agent turn without orchestration context', () => {
    const turn = makeTurn({
      status: 'completed',
      isSupervisorTurn: false,
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
      ],
    })
    expect(isPreSynthesisGap(turn)).toBe(false)
  })

  it('returns false once synthesizing ephemeral appears', () => {
    const turn = makeTurn({
      status: 'active',
      isSupervisorTurn: true,
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
        makeAgent({
          messageId: 'e1',
          isEphemeral: true,
          status: 'working',
          taskStatusMessage: 'Synthesizing responses',
          content: '',
        }),
      ],
    })
    expect(isPreSynthesisGap(turn)).toBe(false)
  })

  it('returns false when deterministic digest entity is present', () => {
    const turn = makeTurn({
      status: 'active',
      isSupervisorTurn: true,
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
        makeAgent({
          messageId: 'summary-u1',
          agentId: 'summary',
          isSummaryAgent: true,
          summaryOrigin: 'deterministic',
          content: '2 agents responded. Expand below to read each answer.',
        }),
      ],
    })
    expect(isPreSynthesisGap(turn)).toBe(false)
  })

  it('returns false when processing log signals synthesis started', () => {
    const turn = makeTurn({
      status: 'active',
      processingStatusLogs: [{ id: 'l1', message: 'Synthesizing responses...', timestamp: '2026-01-01T00:00:03.000Z' }],
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
      ],
    })
    expect(isPreSynthesisGap(turn)).toBe(false)
    expect(hasActiveSynthesisGap(turn)).toBe(true)
  })
})

describe('shouldShowSynthesizingPhase', () => {
  it('returns false when only delegation logs exist after agents finish', () => {
    const turn = makeTurn({
      status: 'active',
      isSupervisorTurn: false,
      processingStatusLogs: [{ id: 'l1', message: 'Delegating to 2 agent(s)...', timestamp: '2026-01-01T00:00:01.000Z' }],
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
      ],
    })
    expect(shouldShowSynthesizingPhase(turn)).toBe(false)
  })

  it('returns false when turnCompletionKind is deterministic', () => {
    const turn = makeTurn({
      status: 'active',
      turnCompletionKind: 'deterministic',
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
      ],
    })
    expect(shouldShowSynthesizingPhase(turn)).toBe(false)
  })

  it('returns true when synthesis log is present', () => {
    const turn = makeTurn({
      status: 'active',
      processingStatusLogs: [{ id: 'l1', message: 'Synthesizing responses...', timestamp: '2026-01-01T00:00:03.000Z' }],
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
      ],
    })
    expect(shouldShowSynthesizingPhase(turn)).toBe(true)
  })
})
