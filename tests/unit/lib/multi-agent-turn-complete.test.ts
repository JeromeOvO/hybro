import { describe, expect, it } from 'vitest'
import {
  hasActiveSynthesisGap,
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

  it('returns true when turnTerminalStatus is completed without turnCompletionKind and no summary entity', () => {
    const turn = makeTurn({
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
          agentId: 'supervisor_synthesis',
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
})

describe('isPreSynthesisGap', () => {
  it('returns true when all agents finish before synthesis starts on supervised turn', () => {
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
  it('returns true during pre-synthesis gap', () => {
    const turn = makeTurn({
      status: 'active',
      processingStatusLogs: [{ id: 'l1', message: 'Delegating to 2 agent(s)...', timestamp: '2026-01-01T00:00:01.000Z' }],
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
      ],
    })
    expect(shouldShowSynthesizingPhase(turn)).toBe(true)
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
