import { describe, expect, it, vi } from 'vitest'
import userEvent from '@testing-library/user-event'
import { render, screen, within } from '../../../utils/test-utils'
import { AgentResponseDetailPane } from '@/components/conversation/AgentResponseDetailPane'
import { AGENT_THEMES, type AgentResponseDetail } from '@/lib/selectors/conversation-types'
import { TASK_STATE } from '@/lib/types/sse'

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
  it('renders a sticky readonly agent card and matching response body', () => {
    render(<AgentResponseDetailPane detail={detail} onClose={vi.fn()} />)

    expect(screen.getByTestId('agent-response-detail-pane')).toBeInTheDocument()
    expect(screen.getByTestId('agent-response-detail-sticky')).toBeInTheDocument()
    expect(screen.getByText('Researcher Alex')).toBeInTheDocument()
    expect(screen.queryByText('help me research a2a agents')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Report' })).toBeInTheDocument()
    expect(screen.getByText('A2A findings.')).toBeInTheDocument()
  })

  it('closes from the header button', async () => {
    const onClose = vi.fn()
    const view = render(<AgentResponseDetailPane detail={detail} onClose={onClose} />)

    await userEvent.click(within(view.container).getByRole('button', { name: /close agent response/i }))

    expect(onClose).toHaveBeenCalled()
  })
})
