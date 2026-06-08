import { describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'
import type { TurnViewModel } from '@/lib/room-timeline/types'
import { TurnRenderer } from '@/components/conversation/TurnRenderer'
import { useMessageStore } from '@/stores/message-store'

vi.mock('@/components/conversation/TurnBody', () => ({
  TurnBody: () => <div data-testid="turn-body" />,
}))

vi.mock('@/components/conversation/UserMessageBlock', () => ({
  UserMessageBlock: () => <div data-testid="user-message" />,
}))

function baseTurn(overrides: Partial<TurnViewModel> = {}): TurnViewModel {
  return {
    id: 'turn-1',
    roomId: 'room-1',
    userMessageId: 'user-1',
    userContent: 'Hello',
    userAttachments: [],
    timestamp: '2026-01-01T00:00:00Z',
    status: 'active',
    events: [],
    summary: null,
    agentResults: [],
    activeAgentIds: [],
    isSupervisorTurn: true,
    processingStatusLogs: [{ message: 'Working', timestamp: '2026-01-01T00:00:01Z' }],
    finalAnswer: { kind: 'pending' },
    displayMode: 'working',
    phase: 'collecting',
    primaryStreamMessageId: undefined,
    ...overrides,
  } as TurnViewModel
}

describe('TurnRenderer sticky user message', () => {
  it('disables sticky positioning on the live last turn', () => {
    useMessageStore.setState({
      entities: {
        'user-1': {
          id: 'user-1',
          roomId: 'room-1',
          messageType: 'user',
          content: 'Hello',
          timestamp: '2026-01-01T00:00:00Z',
        },
      },
      orderedIds: ['user-1'],
      version: 1,
      hydratedFromDb: true,
    })

    const { container, rerender } = render(
      <TurnRenderer turn={baseTurn()} isLastTurn />,
    )

    expect(container.querySelector('.conversation-user-sticky--static')).toBeTruthy()

    rerender(
      <TurnRenderer turn={baseTurn({ status: 'completed' })} isLastTurn={false} />,
    )

    expect(container.querySelector('.conversation-user-sticky--static')).toBeNull()
    expect(container.querySelector('.conversation-user-sticky')).toBeTruthy()
  })
})
