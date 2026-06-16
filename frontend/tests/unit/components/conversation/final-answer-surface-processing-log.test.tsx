import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '../../../utils/test-utils'
import { FinalAnswerSurface } from '@/components/conversation/FinalAnswerSurface'
import type { TurnViewModel } from '@/lib/room-timeline/types'

vi.mock('next/link', () => ({
  default: ({ children, href, onClick, ...rest }: { children: React.ReactNode; href: string; onClick?: React.MouseEventHandler; [k: string]: unknown }) => (
    <a href={href} onClick={onClick} {...rest}>{children}</a>
  ),
}))

afterEach(() => {
  cleanup()
})

function makeTurn(overrides: Partial<TurnViewModel> = {}): TurnViewModel {
  return {
    id: 'turn-1',
    roomId: 'room-1',
    userMessageId: 'user-1',
    userContent: 'Plan the launch',
    userAttachments: [],
    timestamp: '2026-06-03T12:00:00.000Z',
    status: 'active',
    events: [],
    summary: null,
    agentResults: [],
    activeAgentIds: [],
    isSupervisorTurn: true,
    displayMode: 'working',
    phase: 'collecting',
    processingStatusLogs: [],
    finalAnswer: { kind: 'pending', label: 'Working' },
    ...overrides,
  }
}

describe('FinalAnswerSurface processing logs', () => {
  it('renders processing status logs while the turn is collecting', () => {
    render(
      <FinalAnswerSurface
        turn={makeTurn({
          processingStatusLogs: [
            {
              id: 'processing-log-1',
              message: 'Dispatching agents',
              timestamp: '2026-06-03T12:00:01.000Z',
            },
          ],
        })}
      />,
    )

    expect(screen.getByRole('button', { name: /work logs/i })).toBeInTheDocument()
    expect(within(screen.getByRole('log')).getByText('Dispatching agents')).toBeInTheDocument()
  })

  it('uses the processing log as the collecting placeholder surface', () => {
    render(
      <FinalAnswerSurface
        turn={makeTurn({
          processingStatusLogs: [
            {
              id: 'processing-log-thinking',
              message: 'Thinking...',
              timestamp: '2026-06-03T12:00:01.000Z',
            },
          ],
        })}
      />,
    )

    expect(within(screen.getByRole('log')).getByText('Thinking...')).toBeInTheDocument()
    expect(screen.queryByText('Agents working on your request…')).not.toBeInTheDocument()
  })

  it('renders processing status logs below synthesis content while streaming', () => {
    const { container } = render(
      <FinalAnswerSurface
        turn={makeTurn({
          phase: 'synthesizing',
          finalAnswer: { kind: 'llm_synthesis', label: 'Synthesized', primaryMessageId: 'summary-1' },
          agentResults: [
            {
              messageId: 'summary-1',
              agentId: 'supervisor_synthesis',
              agentName: 'HYBRO AI',
              status: 'working',
              content: 'Combined answer text',
              artifacts: [],
              isSummaryAgent: true,
            },
          ],
          processingStatusLogs: [
            {
              id: 'processing-log-1',
              message: 'Synthesizing responses',
              timestamp: '2026-06-03T12:00:01.000Z',
            },
          ],
        })}
      />,
    )

    expect(screen.getByRole('button', { name: /work logs/i })).toBeInTheDocument()
    expect(within(screen.getByRole('log')).getByText('Synthesizing responses')).toBeInTheDocument()
    expect(screen.getByText('Combined answer text')).toBeInTheDocument()
    expect(screen.getByLabelText('HYBRO AI — Synthesizing')).toBeInTheDocument()

    const processingLog = container.querySelector('.conversation-processing-log')
    const synthesisContent = container.querySelector('[data-quote-source-kind="synthesis"]')
    expect(processingLog).not.toBeNull()
    expect(synthesisContent).not.toBeNull()
    expect(
      processingLog!.compareDocumentPosition(synthesisContent!)
      & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
  })

  it('renders processing status logs for a single-agent collecting turn', () => {
    render(
      <FinalAnswerSurface
        turn={makeTurn({
          phase: 'collecting',
          finalAnswer: { kind: 'single', label: 'Working' },
          agentResults: [
            {
              messageId: 'agent-1',
              agentId: 'agent-1',
              agentName: 'Researcher',
              status: 'working',
              content: '',
              artifacts: [],
              isSummaryAgent: false,
            },
          ],
          processingStatusLogs: [
            {
              id: 'processing-log-1',
              message: 'Researcher is working',
              timestamp: '2026-06-03T12:00:01.000Z',
            },
          ],
        })}
      />,
    )

    expect(screen.getByRole('button', { name: /work logs/i })).toBeInTheDocument()
    expect(within(screen.getByRole('log')).getByText('Researcher is working')).toBeInTheDocument()
  })

  it('uses the processing log component with default Thinking text when there are no logs', () => {
    render(<FinalAnswerSurface turn={makeTurn()} />)

    expect(screen.getByRole('button', { name: /work logs/i })).toBeInTheDocument()
    expect(within(screen.getByRole('log')).getByText('Thinking...')).toBeInTheDocument()
    expect(screen.queryByText('Agents working on your request…')).not.toBeInTheDocument()
  })

  it('keeps processing status logs visible after a single-agent turn completes', () => {
    const { container } = render(
      <FinalAnswerSurface
        turn={makeTurn({
          status: 'completed',
          phase: 'completed',
          finalAnswer: { kind: 'single', label: 'Combined agent responses', primaryMessageId: 'agent-1' },
          agentResults: [
            {
              messageId: 'agent-1',
              agentId: 'agent-1',
              agentName: 'HITL Mock Agent',
              status: 'completed',
              content: 'Mock HITL request approved. Task completed.',
              artifacts: [],
              isSummaryAgent: false,
            },
          ],
          processingStatusLogs: [
            {
              id: 'processing-log-1',
              message: 'Thinking...',
              timestamp: '2026-06-03T12:00:01.000Z',
            },
            {
              id: 'processing-log-2',
              message: 'Waiting for HITL approval',
              timestamp: '2026-06-03T12:00:02.000Z',
            },
          ],
        })}
      />,
    )

    expect(screen.getByRole('button', { name: /work logs/i })).toBeInTheDocument()
    expect(within(screen.getByRole('log')).getByText('Waiting for HITL approval')).toBeInTheDocument()
    expect(screen.getByText('Mock HITL request approved. Task completed.')).toBeInTheDocument()
    expect(container.querySelector('.conversation-processing-log')).not.toHaveClass('conversation-processing-log-running')
  })
})
