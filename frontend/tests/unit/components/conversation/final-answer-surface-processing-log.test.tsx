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
    expect(screen.queryByLabelText('HYBRO AI — Synthesizing')).not.toBeInTheDocument()
    expect(screen.queryByText('HYBRO AI')).not.toBeInTheDocument()

    const processingLog = container.querySelector('.conversation-processing-log')
    const synthesisContent = container.querySelector('[data-quote-source-kind="synthesis"]')
    expect(processingLog).not.toBeNull()
    expect(synthesisContent).not.toBeNull()
    expect(
      processingLog!.compareDocumentPosition(synthesisContent!)
      & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
  })

  it('does not render a stale task label above a completed synthesis', () => {
    render(
      <FinalAnswerSurface
        turn={makeTurn({
          status: 'completed',
          phase: 'completed',
          supervisorStage: {
            details: 'Requesting Cyber Insurer Agent',
          },
          finalAnswer: { kind: 'llm_synthesis', label: 'Synthesized', primaryMessageId: 'summary-1' },
          agentResults: [
            {
              messageId: 'summary-1',
              agentId: 'system:hybro',
              agentName: 'HYBRO AI',
              status: 'completed',
              content: 'The requested result is ready.',
              artifacts: [],
              isSummaryAgent: true,
              taskStatusMessage: 'Requesting Cyber Insurer Agent',
            },
          ],
        })}
      />,
    )

    expect(screen.getByText('The requested result is ready.')).toBeInTheDocument()
    expect(screen.queryByText('Requesting Cyber Insurer Agent')).not.toBeInTheDocument()
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

  it('keeps only the work log running while HITL content stays in the composer', () => {
    const { container } = render(
      <FinalAnswerSurface
        turn={makeTurn({
          status: 'awaiting_input',
          phase: 'collecting',
          finalAnswer: {
            kind: 'hitl',
            label: 'Needs input',
            hitl: {
              source: 'agent',
              prompts: [{
                messageId: 'hitl-1',
                agentName: 'Travel Planner Agent',
                prompt: 'Where are you going?',
              }],
            },
          },
          agentResults: [
            {
              messageId: 'hitl-1',
              agentId: 'travel-planner',
              agentName: 'Travel Planner Agent',
              status: 'awaiting_input',
              content: 'Where are you going?',
              artifacts: [],
              isSummaryAgent: false,
              hitlPending: { prompt: 'Where are you going?', source: 'agent' },
            },
          ],
          processingStatusLogs: [
            {
              id: 'processing-log-1',
              message: 'Delegating to Travel Planner Agent',
              timestamp: '2026-06-03T12:00:01.000Z',
            },
          ],
        })}
      />,
    )

    expect(screen.queryByText('Where are you going?')).not.toBeInTheDocument()
    expect(screen.getByText('Delegating to Travel Planner Agent')).toBeInTheDocument()
    expect(screen.queryByLabelText('HYBRO AI — Working')).not.toBeInTheDocument()
    expect(container.querySelector('.conversation-processing-log')).toHaveClass(
      'conversation-processing-log-running',
    )
  })

  it('keeps the work log running after HITL while the turn is still pending', () => {
    const { container } = render(
      <FinalAnswerSurface
        turn={makeTurn({
          status: 'active',
          phase: 'collecting',
          finalAnswer: { kind: 'pending', label: 'Working' },
          agentResults: [
            {
              messageId: 'agent-1',
              agentId: 'travel-planner',
              agentName: 'Travel Planner Agent',
              status: 'working',
              content: '',
              artifacts: [],
              isSummaryAgent: false,
              hitlResolved: { prompt: 'Where are you going?', answer: 'New York City' },
            },
          ],
          processingStatusLogs: [
            {
              id: 'processing-log-1',
              message: 'Applying your answers…',
              timestamp: '2026-06-03T12:00:02.000Z',
            },
          ],
        })}
      />,
    )

    expect(screen.queryByLabelText('HYBRO AI — Working')).not.toBeInTheDocument()
    expect(container.querySelector('.conversation-processing-log')).toHaveClass(
      'conversation-processing-log-running',
    )
  })

  it('keeps the work log running while an active turn already shows deterministic_done', () => {
    const { container } = render(
      <FinalAnswerSurface
        turn={makeTurn({
          status: 'active',
          phase: 'collecting',
          finalAnswer: {
            kind: 'deterministic_done',
            label: 'Combined agent responses',
            deterministicIntro: '1 agent responded. Expand below to read each answer.',
            sections: [],
          },
          agentResults: [
            {
              messageId: 'agent-1',
              agentId: 'travel-planner',
              agentName: 'Travel Planner Agent',
              status: 'completed',
              content: 'Five-day NYC itinerary…',
              artifacts: [],
              isSummaryAgent: false,
            },
          ],
          processingStatusLogs: [
            {
              id: 'processing-log-1',
              message: 'Synthesizing responses',
              timestamp: '2026-06-03T12:00:02.000Z',
            },
          ],
        })}
      />,
    )

    expect(screen.queryByLabelText('HYBRO AI — Combined agent responses')).not.toBeInTheDocument()
    expect(screen.getByText('1 agent responded. Expand below to read each answer.')).toBeInTheDocument()
    expect(container.querySelector('.conversation-processing-log')).toHaveClass(
      'conversation-processing-log-running',
    )
  })

  it('does not fabricate a HYBRO AI card after the turn completes', () => {
    const { container } = render(
      <FinalAnswerSurface
        turn={makeTurn({
          status: 'completed',
          phase: 'completed',
          finalAnswer: {
            kind: 'deterministic_done',
            label: 'Combined agent responses',
            deterministicIntro: '1 agent responded. Expand below to read each answer.',
            sections: [],
          },
          agentResults: [
            {
              messageId: 'agent-1',
              agentId: 'travel-planner',
              agentName: 'Travel Planner Agent',
              status: 'completed',
              content: 'Five-day NYC itinerary…',
              artifacts: [],
              isSummaryAgent: false,
            },
          ],
        })}
      />,
    )

    expect(screen.queryByLabelText('HYBRO AI — Combined agent responses')).not.toBeInTheDocument()
    expect(screen.getByText('1 agent responded. Expand below to read each answer.')).toBeInTheDocument()
    expect(container.querySelector('.conversation-avatar-working')).toBeNull()
  })

  it('shows a running work log while phase is synthesizing', () => {
    const { container } = render(
      <FinalAnswerSurface
        turn={makeTurn({
          status: 'active',
          phase: 'synthesizing',
          finalAnswer: { kind: 'pending', label: 'Working' },
          processingStatusLogs: [
            {
              id: 'processing-log-1',
              message: 'Synthesizing responses',
              timestamp: '2026-06-03T12:00:02.000Z',
            },
          ],
        })}
      />,
    )

    expect(screen.queryByLabelText('HYBRO AI — Synthesizing')).not.toBeInTheDocument()
    expect(container.querySelector('.conversation-processing-log')).toHaveClass(
      'conversation-processing-log-running',
    )
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
