import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen, within } from '../../../utils/test-utils'
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
    expect(screen.getAllByRole('button', { name: /turn trace/i })).toHaveLength(1)
    expect(within(screen.getByRole('log')).getByText('Thinking...')).toBeInTheDocument()
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
    expect(screen.getAllByRole('button', { name: /turn trace/i })).toHaveLength(1)
    expect(within(screen.getByRole('log')).getByText('Thinking...')).toBeInTheDocument()
    expect(within(screen.getByRole('log')).getByText(/LLM call · gpt-5-mini/)).toBeInTheDocument()
  })
})
