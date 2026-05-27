import React from 'react'
import { describe, expect, it, vi } from 'vitest'
import userEvent from '@testing-library/user-event'
import { render, screen, within } from '../../../utils/test-utils'
import { AgentResponseDetailPane } from '@/components/conversation/AgentResponseDetailPane'
import { AGENT_THEMES, type AgentResponseDetail } from '@/lib/selectors/conversation-types'
import { TASK_STATE } from '@/lib/types/sse'

vi.mock('next/link', () => ({
  default: ({ children, href, onClick, ...rest }: { children: React.ReactNode; href: string; onClick?: React.MouseEventHandler; [k: string]: unknown }) => (
    <a href={href} onClick={onClick} {...rest}>{children}</a>
  ),
}))

const detail: AgentResponseDetail = {
  messageId: 'agent-1',
  agentId: 'researcher-1',
  agentName: 'Researcher Alex',
  display: {
    label: 'Completed',
    tone: 'muted',
    isAnimated: false,
    ariaLabel: 'Researcher Alex completed',
  },
  taskDescription: 'Research a2a agents',
  theme: AGENT_THEMES[0],
  content: '# Report\n\nA2A findings.',
  isStreaming: false,
  taskStatus: TASK_STATE.COMPLETED,
  taskStatusMessage: null,
  taskError: null,
  requestMessage: {
    id: 'user-1',
    roomId: 'room-1',
    messageType: 'user',
    content: 'help me research a2a agents',
    senderName: 'User',
    timestamp: '2026-01-01T00:00:00.000Z',
    source: 'db',
    sourceVersion: 1,
    displayType: 'user-bubble',
    isEphemeral: false,
    createdAt: 0,
    updatedAt: 0,
  },
}

describe('AgentResponseDetailPane', () => {
  it('renders a full-width pane header and matching response body', () => {
    const { container } = render(<AgentResponseDetailPane detail={detail} onClose={vi.fn()} />)

    expect(screen.getByTestId('agent-response-detail-pane')).toBeInTheDocument()
    const sticky = screen.getByTestId('agent-response-detail-sticky')
    expect(sticky).toBeInTheDocument()
    expect(within(sticky).getByTestId('agent-response-detail-header')).toBeInTheDocument()
    expect(sticky.querySelector('.conversation-agent-card')).toBeNull()
    expect(container.querySelector('.conversation-detail-agent-header')).toBeTruthy()
    expect(within(sticky).getByRole('button', { name: /close agent response/i })).toBeInTheDocument()
    expect(screen.getByText('Researcher Alex')).toBeInTheDocument()
    expect(screen.getByRole('status', { name: 'Researcher Alex completed' })).toHaveTextContent('Completed')
    expect(screen.queryByText('help me research a2a agents')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Report' })).toBeInTheDocument()
    expect(screen.getByText('A2A findings.')).toBeInTheDocument()
  })

  it('shows quoted user context when the request message includes a quote', () => {
    render(
      <AgentResponseDetailPane
        detail={{
          ...detail,
          requestMessage: {
            ...detail.requestMessage!,
            quotedText: 'task-lifecycle -> Full task',
            quotedSenderName: 'Spec Agent',
          },
        }}
        onClose={vi.fn()}
      />,
    )

    expect(screen.getByTestId('agent-detail-quoted-context')).toBeInTheDocument()
    expect(screen.getByText('Quoted from Spec Agent')).toBeInTheDocument()
    expect(screen.getByText('task-lifecycle -> Full task')).toBeInTheDocument()
  })

  it('closes from the header button', async () => {
    const onClose = vi.fn()
    const view = render(<AgentResponseDetailPane detail={detail} onClose={onClose} />)

    await userEvent.click(within(view.container).getByRole('button', { name: /close agent response/i }))

    expect(onClose).toHaveBeenCalled()
  })
})
