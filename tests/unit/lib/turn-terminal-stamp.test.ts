import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createProcessingLifecycle } from '@/hooks/room/processing-lifecycle'
import {
  canStampTurnTerminalFromEntityState,
  ensureTurnTerminalStampedFromBackendTruth,
  isBackendRunActiveForTurn,
  stampTurnTerminalFromBackendTruth,
} from '@/lib/room-timeline/turn-terminal-stamp'
import type { AgentResultViewModel, TurnViewModel } from '@/lib/room-timeline/types'
import { useMessageStore } from '@/stores/message-store'

vi.mock('@/lib/api/room', () => ({
  inquiryActiveRuns: vi.fn(),
}))

import { inquiryActiveRuns } from '@/lib/api/room'

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

describe('canStampTurnTerminalFromEntityState', () => {
  it('returns true when backend confirms done with explicit deterministic kind', () => {
    const turn = makeTurn({
      turnCompletionKind: 'deterministic',
      isSupervisorTurn: true,
      processingStatusLogs: [{ id: 'l1', message: 'Delegating to 2 agent(s)...', timestamp: '2026-01-01T00:00:01.000Z' }],
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
      ],
    })
    expect(canStampTurnTerminalFromEntityState(turn, undefined, false)).toBe(true)
  })

  it('returns false for supervisor turn without completion kind when backend run is inactive (anti-flash)', () => {
    const turn = makeTurn({
      isSupervisorTurn: true,
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
      ],
    })
    expect(canStampTurnTerminalFromEntityState(turn, undefined, false)).toBe(false)
  })

  it('returns true for supervisor turn with deterministic completion kind when backend confirms no synthesis', () => {
    const turn = makeTurn({
      isSupervisorTurn: true,
      turnCompletionKind: 'deterministic',
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
      ],
    })
    expect(canStampTurnTerminalFromEntityState(turn, undefined, false)).toBe(true)
  })

  it('returns true when backend confirms deterministic completion on active turn', () => {
    const turn = makeTurn({
      status: 'active',
      turnCompletionKind: 'deterministic',
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
      ],
    })
    expect(canStampTurnTerminalFromEntityState(turn, undefined, false, { lifecycleActive: true })).toBe(true)
  })

  it('returns false when backend confirms done while synthesis log is active', () => {
    const turn = makeTurn({
      status: 'active',
      processingStatusLogs: [{ id: 'l1', message: 'Synthesizing responses...', timestamp: '2026-01-01T00:00:03.000Z' }],
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
      ],
    })
    expect(canStampTurnTerminalFromEntityState(turn, undefined, false)).toBe(false)
  })

  it('blocks stamping when backendRunActive is null and synthesis gap is active', () => {
    const turn = makeTurn({
      status: 'active',
      processingStatusLogs: [{ id: 'l1', message: 'Synthesizing responses...', timestamp: '2026-01-01T00:00:03.000Z' }],
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
      ],
    })
    expect(canStampTurnTerminalFromEntityState(turn, undefined, null)).toBe(false)
  })

  it('returns true for supervisor turn when deterministic digest entity is present', () => {
    const turn = makeTurn({
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
    expect(canStampTurnTerminalFromEntityState(turn, undefined, false)).toBe(true)
  })

  it('returns false while backend run is still active', () => {
    const turn = makeTurn({
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
      ],
    })
    expect(canStampTurnTerminalFromEntityState(turn, undefined, true)).toBe(false)
  })

  it('returns false for LLM summary with streamed content (entity-first invariant)', () => {
    const turn = makeTurn({
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
        makeAgent({
          messageId: 'summary-u1',
          agentId: 'summary',
          isSummaryAgent: true,
          status: 'working',
          content: 'Combined answer text',
        }),
      ],
    })
    expect(canStampTurnTerminalFromEntityState(turn, undefined, false)).toBe(false)
  })

  it('returns false while LLM summary is working without content', () => {
    const turn = makeTurn({
      agentResults: [
        makeAgent({ messageId: 'a1', agentId: 'agent-a' }),
        makeAgent({ messageId: 'a2', agentId: 'agent-b' }),
        makeAgent({
          messageId: 'summary-u1',
          agentId: 'summary',
          isSummaryAgent: true,
          status: 'working',
          content: '',
        }),
      ],
    })
    expect(canStampTurnTerminalFromEntityState(turn, undefined, false)).toBe(false)
  })
})

describe('isBackendRunActiveForTurn', () => {
  it('returns true when trigger message id matches', () => {
    expect(isBackendRunActiveForTurn([{ trigger_message_id: 'u1' }], 'u1')).toBe(true)
  })
})

describe('stampTurnTerminalFromBackendTruth', () => {
  beforeEach(() => {
    useMessageStore.getState().clearRoom()
    useMessageStore.getState().setRoom('room-1')
  })

  it('stamps turnTerminalStatus when deterministic digest is present', () => {
    const lifecycle = createProcessingLifecycle(() => {})
    lifecycle.startProcessing('u1')

    useMessageStore.getState().upsertMany([
      {
        id: 'u1',
        roomId: 'room-1',
        messageType: 'user',
        content: 'hello',
        senderName: 'User',
        timestamp: '2026-01-01T00:00:00.000Z',
        clientRequestId: 'cr-1',
      },
      {
        id: 'a1',
        roomId: 'room-1',
        messageType: 'agent',
        content: 'A',
        senderName: 'Agent A',
        agentId: 'agent-a',
        relatedMessageId: 'u1',
        clientRequestId: 'cr-1',
        taskStatus: 'completed',
        timestamp: '2026-01-01T00:00:01.000Z',
      },
      {
        id: 'a2',
        roomId: 'room-1',
        messageType: 'agent',
        content: 'B',
        senderName: 'Agent B',
        agentId: 'agent-b',
        relatedMessageId: 'u1',
        clientRequestId: 'cr-1',
        taskStatus: 'completed',
        timestamp: '2026-01-01T00:00:02.000Z',
      },
      {
        id: 'summary-u1',
        roomId: 'room-1',
        messageType: 'agent',
        content: '2 agents responded. Expand below to read each answer.',
        senderName: 'HYBRO AI',
        agentId: 'summary',
        relatedMessageId: 'u1',
        clientRequestId: 'cr-1',
        taskStatus: 'completed',
        summaryOrigin: 'deterministic',
        timestamp: '2026-01-01T00:00:03.000Z',
      },
    ], 'sse')

    const stamped = stampTurnTerminalFromBackendTruth(
      'room-1',
      lifecycle,
      { relatedMessageId: 'u1', clientRequestId: 'cr-1' },
      false,
    )

    expect(stamped).toBe(true)
    expect(useMessageStore.getState().entities.u1?.turnTerminalStatus).toBe('completed')
  })

  it('infers deterministic completion kind when backend run is inactive without inquiry kind', () => {
    const lifecycle = createProcessingLifecycle(() => {})
    lifecycle.startProcessing('u1')

    useMessageStore.getState().upsertMany([
      {
        id: 'u1',
        roomId: 'room-1',
        messageType: 'user',
        content: 'hello',
        senderName: 'User',
        timestamp: '2026-01-01T00:00:00.000Z',
        clientRequestId: 'cr-1',
      },
      {
        id: 'a1',
        roomId: 'room-1',
        messageType: 'agent',
        content: 'A',
        senderName: 'Agent A',
        agentId: 'agent-a',
        relatedMessageId: 'u1',
        clientRequestId: 'cr-1',
        taskStatus: 'completed',
        timestamp: '2026-01-01T00:00:01.000Z',
      },
      {
        id: 'a2',
        roomId: 'room-1',
        messageType: 'agent',
        content: 'B',
        senderName: 'Agent B',
        agentId: 'agent-b',
        relatedMessageId: 'u1',
        clientRequestId: 'cr-1',
        taskStatus: 'completed',
        timestamp: '2026-01-01T00:00:02.000Z',
      },
    ], 'sse')

    const stamped = stampTurnTerminalFromBackendTruth(
      'room-1',
      lifecycle,
      { relatedMessageId: 'u1', clientRequestId: 'cr-1' },
      false,
    )

    expect(stamped).toBe(true)
    expect(useMessageStore.getState().entities.u1?.turnTerminalStatus).toBe('completed')
    expect(useMessageStore.getState().entities.u1?.turnCompletionKind).toBe('deterministic')
  })

  it('clears processing lifecycle when stamping failed terminal status', () => {
    const lifecycle = createProcessingLifecycle(() => {})
    lifecycle.startProcessing('u1')

    useMessageStore.getState().upsertMany([
      {
        id: 'u1',
        roomId: 'room-1',
        messageType: 'user',
        content: 'hello',
        senderName: 'User',
        timestamp: '2026-01-01T00:00:00.000Z',
        clientRequestId: 'cr-1',
      },
      {
        id: 'a1',
        roomId: 'room-1',
        messageType: 'agent',
        content: 'Error',
        senderName: 'Agent A',
        agentId: 'agent-a',
        relatedMessageId: 'u1',
        clientRequestId: 'cr-1',
        taskStatus: 'failed',
        timestamp: '2026-01-01T00:00:01.000Z',
      },
      {
        id: 'a2',
        roomId: 'room-1',
        messageType: 'agent',
        content: 'Error',
        senderName: 'Agent B',
        agentId: 'agent-b',
        relatedMessageId: 'u1',
        clientRequestId: 'cr-1',
        taskStatus: 'failed',
        timestamp: '2026-01-01T00:00:02.000Z',
      },
    ], 'sse')

    const stamped = stampTurnTerminalFromBackendTruth(
      'room-1',
      lifecycle,
      { relatedMessageId: 'u1', clientRequestId: 'cr-1' },
      false,
    )

    expect(stamped).toBe(true)
    expect(useMessageStore.getState().entities.u1?.turnTerminalStatus).toBe('failed')
    expect(lifecycle.isProcessingResolved()).toBe(true)
  })
})

describe('ensureTurnTerminalStampedFromBackendTruth', () => {
  beforeEach(() => {
    useMessageStore.getState().clearRoom()
    useMessageStore.getState().setRoom('room-1')
    vi.mocked(inquiryActiveRuns).mockReset()
  })

  it('stamps when inquiryActiveRuns is empty and all agents finished without synthesis', async () => {
    vi.mocked(inquiryActiveRuns).mockResolvedValue({
      success: true,
      active_runs: [],
      turn_completion_kind: 'deterministic',
    })

    const lifecycle = createProcessingLifecycle(() => {})
    lifecycle.startProcessing('u1')
    useMessageStore.getState().upsertMany([
      {
        id: 'u1',
        roomId: 'room-1',
        messageType: 'user',
        content: 'hello',
        senderName: 'User',
        timestamp: '2026-01-01T00:00:00.000Z',
        clientRequestId: 'cr-1',
        processingStatusLogs: [{ id: 'l1', message: 'Delegating to 2 agent(s)...', timestamp: '2026-01-01T00:00:01.000Z' }],
      },
      {
        id: 'a1',
        roomId: 'room-1',
        messageType: 'agent',
        content: 'A',
        senderName: 'Agent A',
        agentId: 'agent-a',
        relatedMessageId: 'u1',
        clientRequestId: 'cr-1',
        taskStatus: 'completed',
        timestamp: '2026-01-01T00:00:01.000Z',
      },
      {
        id: 'a2',
        roomId: 'room-1',
        messageType: 'agent',
        content: 'B',
        senderName: 'Agent B',
        agentId: 'agent-b',
        relatedMessageId: 'u1',
        clientRequestId: 'cr-1',
        taskStatus: 'completed',
        timestamp: '2026-01-01T00:00:02.000Z',
      },
    ], 'sse')

    const stamped = await ensureTurnTerminalStampedFromBackendTruth(
      'room-1',
      lifecycle,
      { relatedMessageId: 'u1' },
      async () => 'token',
    )

    expect(stamped).toBe(true)
    expect(useMessageStore.getState().entities.u1?.turnTerminalStatus).toBe('completed')
  })

  it('stamps when inquiryActiveRuns reports no active run and deterministic digest exists', async () => {
    vi.mocked(inquiryActiveRuns).mockResolvedValue({
      success: true,
      active_runs: [],
    })

    const lifecycle = createProcessingLifecycle(() => {})
    useMessageStore.getState().upsertMany([
      {
        id: 'u1',
        roomId: 'room-1',
        messageType: 'user',
        content: 'hello',
        senderName: 'User',
        timestamp: '2026-01-01T00:00:00.000Z',
        clientRequestId: 'cr-1',
      },
      {
        id: 'a1',
        roomId: 'room-1',
        messageType: 'agent',
        content: 'A',
        senderName: 'Agent A',
        agentId: 'agent-a',
        relatedMessageId: 'u1',
        clientRequestId: 'cr-1',
        taskStatus: 'completed',
        timestamp: '2026-01-01T00:00:01.000Z',
      },
      {
        id: 'a2',
        roomId: 'room-1',
        messageType: 'agent',
        content: 'B',
        senderName: 'Agent B',
        agentId: 'agent-b',
        relatedMessageId: 'u1',
        clientRequestId: 'cr-1',
        taskStatus: 'completed',
        timestamp: '2026-01-01T00:00:02.000Z',
      },
      {
        id: 'summary-u1',
        roomId: 'room-1',
        messageType: 'agent',
        content: '2 agents responded. Expand below to read each answer.',
        senderName: 'HYBRO AI',
        agentId: 'summary',
        relatedMessageId: 'u1',
        clientRequestId: 'cr-1',
        taskStatus: 'completed',
        summaryOrigin: 'deterministic',
        timestamp: '2026-01-01T00:00:03.000Z',
      },
    ], 'sse')

    const stamped = await ensureTurnTerminalStampedFromBackendTruth(
      'room-1',
      lifecycle,
      { relatedMessageId: 'u1' },
      async () => 'token',
    )

    expect(stamped).toBe(true)
    expect(useMessageStore.getState().entities.u1?.turnTerminalStatus).toBe('completed')
  })
})
