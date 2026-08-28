import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '../../../utils/test-utils'
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
  it('does not render a Trace for progress-only updates', () => {
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
    expect(screen.queryByRole('button', { name: /turn trace/i })).not.toBeInTheDocument()
    expect(screen.queryByText('Progress')).not.toBeInTheDocument()
    expect(screen.queryByText('Thinking...')).not.toBeInTheDocument()
  })

  it('keeps accepted Tool activity live while the parent Turn awaits input', () => {
    useMessageStore.getState().upsertMessage({
      id: 'user-1',
      roomId: 'room-1',
      messageType: 'user',
      content: 'weather?',
      senderName: 'Test',
      timestamp: '2026-06-04T01:00:00.000Z',
      clientRequestId: 'request-1',
    }, 'db')
    useTraceStore.getState().applyRunEvent({
      eventId: 'event-1',
      runId: 'run-1',
      type: 'tool_call_accepted',
      correlationId: 'request-1',
      payload: { call_id: 'call-1', tool_name: 'Weather Agent' },
    })

    const { container } = render(<TurnRenderer turn={makeTurn({
      status: 'awaiting_input',
      displayMode: 'awaiting_input',
      finalAnswer: { kind: 'hitl', label: 'Needs input' },
    })} />)

    expect(screen.getByRole('button', { name: /waiting for input,/i })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Called Weather Agent')).toBeInTheDocument()
    expect(screen.getByText('Running')).toBeInTheDocument()
    expect(screen.queryByText('Outcome unavailable')).not.toBeInTheDocument()
    expect(container.querySelector('.conversation-trace-separator')).not.toBeInTheDocument()
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

  it('uses terminal Turn authority for accepted-only history when run status is absent', () => {
    useMessageStore.getState().upsertMessage({
      id: 'user-1', roomId: 'room-1', messageType: 'user', content: 'weather?',
      senderName: 'Test', timestamp: '2026-06-04T01:00:00.000Z', clientRequestId: 'cr-1',
    }, 'db')
    useTraceStore.getState().applyRunEvent({
      eventId: 'event-1', runId: 'run-without-status', type: 'tool_call_accepted',
      correlationId: 'cr-1', payload: { call_id: 'call-1', tool_name: 'Weather Agent' },
    })

    const { container } = render(<TurnRenderer turn={makeTurn({
      status: 'completed', phase: 'completed', processingStatusLogs: [],
      finalAnswer: { kind: 'deterministic_done', label: 'Combined agent responses' },
    })} />)

    expect(container.querySelector('.conversation-trace-separator')).toHaveAttribute('data-slot', 'separator')
    fireEvent.click(screen.getByRole('button', { name: /finished,/i }))
    expect(screen.getByText('Outcome unavailable')).toBeInTheDocument()
    expect(screen.queryByText('Waiting for tool output…')).not.toBeInTheDocument()
  })

  it('shows Tool activity without Model response or Progress metadata', () => {
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
    useTraceStore.getState().applyRunEvent({
      eventId: 'event-2',
      runId: 'run-1',
      type: 'tool_call_accepted',
      correlationId: 'cr-1',
      payload: { call_id: 'call-1', tool_name: 'Weather Agent' },
    })

    render(<TurnRenderer turn={makeTurn()} />)

    expect(screen.getByRole('button', { name: /running,/i })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Called Weather Agent')).toBeInTheDocument()
    expect(screen.queryByText('HYBRO AI')).not.toBeInTheDocument()
    expect(screen.queryByText('Model response')).not.toBeInTheDocument()
    expect(screen.queryByText('gpt-5-mini')).not.toBeInTheDocument()
    expect(screen.queryByText('Progress')).not.toBeInTheDocument()
    expect(screen.queryByText('Thinking...')).not.toBeInTheDocument()
  })
})
