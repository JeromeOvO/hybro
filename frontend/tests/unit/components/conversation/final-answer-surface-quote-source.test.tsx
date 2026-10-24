import React from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '../../../utils/test-utils'
import { FinalAnswerSurface } from '@/components/conversation/FinalAnswerSurface'
import type { TurnViewModel } from '@/lib/room-timeline/types'

afterEach(() => {
  cleanup()
})

function canceledTurn(overrides: Partial<TurnViewModel>): TurnViewModel {
  return {
    id: 'turn-1',
    conversationId: 'room-1',
    userMessageId: 'user-1',
    userContent: 'Plan the launch',
    userAttachments: [],
    timestamp: '2026-06-03T12:00:00.000Z',
    status: 'partial',
    events: [],
    summary: null,
    agentResults: [],
    activeAgentIds: [],
    isSupervisorTurn: false,
    displayMode: 'parallel_results',
    phase: 'completed',
    processingStatusLogs: [],
    finalAnswer: { kind: 'canceled', label: 'Canceled', canceledIntro: 'Request stopped.' },
    ...overrides,
  }
}

function quoteRegion(): HTMLElement | null {
  return screen.getByText('Request stopped.').closest('[data-quote-message-id]')
}

describe('FinalAnswerSurface quote source', () => {
  it('quotes terminal content as the persisted user turn', () => {
    render(<FinalAnswerSurface turn={canceledTurn({ id: 'user-1', userMessageId: 'user-1' })} />)

    const region = quoteRegion()
    expect(region).toHaveAttribute('data-quote-message-id', 'user-1')
    expect(region).toHaveAttribute('data-quote-source-kind', 'user_turn')
  })

  it('does not make a placeholder turn without a user message quotable', () => {
    render(<FinalAnswerSurface turn={canceledTurn({ id: 'turn-0', userMessageId: null })} />)

    expect(quoteRegion()).toBeNull()
  })

  it('does not make an optimistic user turn quotable', () => {
    render(<FinalAnswerSurface turn={canceledTurn({ id: 'cr:req-1', userMessageId: 'cr:req-1' })} />)

    expect(quoteRegion()).toBeNull()
  })
})
