import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen, within } from '../../../utils/test-utils'
import { TurnRenderer } from '@/components/conversation/TurnRenderer'
import { useMessageStore } from '@/stores/message-store'
import type { TurnViewModel } from '@/lib/room-timeline/types'

beforeEach(() => {
  useMessageStore.getState().clearRoom()
  useMessageStore.getState().setRoom('room-1')
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
    expect(screen.getByRole('button', { name: /work logs/i })).toBeInTheDocument()
    expect(within(screen.getByRole('log')).getByText('Thinking...')).toBeInTheDocument()
  })
})
