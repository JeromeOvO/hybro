import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '../../../utils/test-utils'
import { TurnRenderer } from '@/components/conversation/TurnRenderer'
import { useMessageStore } from '@/stores/message-store'
import { useTraceStore } from '@/stores/trace-store'
import type { TurnViewModel } from '@/lib/room-timeline/types'

beforeEach(() => {
  useMessageStore.getState().clearRoom()
  useMessageStore.getState().setRoom('room-1')
  useTraceStore.getState().setRoom('room-1')
})

afterEach(() => {
  cleanup()
})

function makeTurn(overrides: Partial<TurnViewModel> = {}): TurnViewModel {
  return {
    id: 'user-1',
    roomId: 'room-1',
    userMessageId: 'user-1',
    userContent: 'how are you?',
    userAttachments: [],
    timestamp: '2026-06-04T01:00:00.000Z',
    status: 'active',
    events: [],
    summary: null,
    agentResults: [],
    activeAgentIds: [],
    isSupervisorTurn: false,
    displayMode: 'working',
    phase: 'collecting',
    processingStatusLogs: [
      {
        id: 'processing-log-1',
        message: 'Thinking...',
        timestamp: '2026-06-04T01:00:01.000Z',
      },
    ],
    finalAnswer: { kind: 'pending', label: 'Working' },
    ...overrides,
  }
}

describe('TurnRenderer', () => {
  it('renders the processing surface before any agent result exists', () => {
    useMessageStore.getState().upsertMessage({
      id: 'user-1',
      roomId: 'room-1',
      messageType: 'user',
      content: 'how are you?',
      senderName: 'Test',
      timestamp: '2026-06-04T01:00:00.000Z',
      processingStatusLogs: [
        {
          id: 'processing-log-1',
          message: 'Thinking...',
          timestamp: '2026-06-04T01:00:01.000Z',
        },
      ],
    }, 'optimistic')

    render(<TurnRenderer turn={makeTurn()} />)

    expect(screen.getByText('how are you?')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /work logs/i })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /turn trace/i }))
    expect(within(screen.getByRole('log')).getByText('Thinking...')).toBeInTheDocument()
  })

  it('renders agent cards even when the detail drawer callback is absent', () => {
    useMessageStore.getState().upsertMessage({
      id: 'user-1',
      roomId: 'room-1',
      messageType: 'user',
      content: 'weather?',
      senderName: 'Test',
      timestamp: '2026-06-04T01:00:00.000Z',
    }, 'db')

    render(<TurnRenderer isLastTurn turn={makeTurn({
      status: 'completed',
      phase: 'completed',
      processingStatusLogs: [],
      finalAnswer: {
        kind: 'deterministic_done',
        label: 'Combined agent responses',
        deterministicIntro: '1 agent responded.',
      },
      agentResults: [{
        agentId: 'weather-agent',
        agentName: 'Weather Agent',
        messageId: 'call-1',
        status: 'completed',
        content: 'Sunny',
        artifacts: [],
        isSummaryAgent: false,
        isEphemeral: false,
      }],
    })} />)

    expect(screen.getByRole('status', { name: 'Weather Agent — Completed' })).toBeInTheDocument()
  })

  it('renders separate cards for repeated calls to the same agent', () => {
    useMessageStore.getState().upsertMessage({
      id: 'user-1',
      roomId: 'room-1',
      messageType: 'user',
      content: 'weather twice',
      senderName: 'Test',
      timestamp: '2026-06-04T01:00:00.000Z',
    }, 'db')
    const result = {
      agentId: 'weather-agent',
      agentName: 'Weather Agent',
      status: 'completed' as const,
      content: 'Sunny',
      artifacts: [],
      isSummaryAgent: false,
      isEphemeral: false,
    }

    render(<TurnRenderer isLastTurn turn={makeTurn({
      status: 'completed',
      phase: 'completed',
      processingStatusLogs: [],
      finalAnswer: { kind: 'deterministic_done', label: 'Combined agent responses' },
      agentResults: [
        { ...result, messageId: 'call-1' },
        { ...result, messageId: 'call-2', content: 'Cloudy' },
      ],
    })} />)

    expect(screen.getAllByRole('status', { name: 'Weather Agent — Completed' })).toHaveLength(2)
  })

  it('combines work updates and technical trace into one persisted panel', () => {
    useMessageStore.getState().upsertMessage({
      id: 'user-1',
      roomId: 'room-1',
      messageType: 'user',
      content: 'how are you?',
      senderName: 'Test',
      timestamp: '2026-06-04T01:00:00.000Z',
      clientRequestId: 'cr-1',
    }, 'db')
    useTraceStore.getState().applyRunEvent({
      eventId: 'event-1',
      runId: 'run-1',
      type: 'llm_call_completed',
      correlationId: 'cr-1',
      payload: { model: 'gpt-5-mini', duration_ms: 120 },
    })

    render(<TurnRenderer turn={makeTurn()} />)

    expect(screen.queryByRole('button', { name: /work logs/i })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /turn trace/i }))
    expect(within(screen.getByRole('log')).getByText('Thinking...')).toBeInTheDocument()
    expect(within(screen.getByRole('log')).getByText('Model response')).toBeInTheDocument()
    expect(within(screen.getByRole('log')).getByText('gpt-5-mini')).toBeInTheDocument()
  })
})
