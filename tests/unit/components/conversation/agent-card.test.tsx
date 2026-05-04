import { describe, expect, it, vi } from 'vitest'
import userEvent from '@testing-library/user-event'
import { render, screen } from '../../../utils/test-utils'
import { AgentCard } from '@/components/conversation/AgentCard'
import { AGENT_THEMES } from '@/lib/selectors/conversation-types'

describe('AgentCard', () => {
  it('uses conversation density classes for card and status sizing', () => {
    const { container } = render(
      <AgentCard
        agentId="agent-1"
        agentName="Planner"
        taskDescription="Plan the trip"
        theme={AGENT_THEMES[0]}
        display={{
          label: 'Completed',
          tone: 'muted',
          isAnimated: false,
          ariaLabel: 'Completed',
        }}
      />
    )

    expect(container.querySelector('.conversation-agent-card')).toBeTruthy()
    expect(screen.getByRole('status').className).toContain('conversation-agent-status')
  })

  it('opens the matching agent message when interactive', async () => {
    const onOpen = vi.fn()
    render(
      <AgentCard
        messageId="agent-message-1"
        agentId="agent-1"
        agentName="Planner"
        taskDescription="Plan the trip"
        theme={AGENT_THEMES[0]}
        display={{
          label: 'Completed',
          tone: 'muted',
          isAnimated: false,
          ariaLabel: 'Completed',
        }}
        onOpen={onOpen}
      />
    )

    await userEvent.click(screen.getByRole('button', { name: /open planner response/i }))

    expect(onOpen).toHaveBeenCalledWith('agent-message-1')
  })

  it('marks selected cards without changing the card element', () => {
    const { container } = render(
      <AgentCard
        messageId="agent-message-1"
        agentId="agent-1"
        agentName="Planner"
        taskDescription="Plan the trip"
        theme={AGENT_THEMES[0]}
        selected
        display={{
          label: 'Completed',
          tone: 'muted',
          isAnimated: false,
          ariaLabel: 'Completed',
        }}
      />
    )

    expect(container.querySelector('.conversation-agent-card')?.getAttribute('data-selected')).toBe('true')
  })

  it('does not render helper copy for input-required cards', () => {
    render(
      <AgentCard
        agentId="agent-1"
        agentName="Planner"
        taskDescription="Plan the trip"
        theme={AGENT_THEMES[0]}
        display={{
          label: 'Needs Input',
          tone: 'warning',
          isAnimated: true,
          ariaLabel: 'Planner needs input',
        }}
      />
    )

    expect(screen.getByRole('status', { name: 'Planner needs input' })).toHaveTextContent('Needs Input')
    expect(screen.queryByText('Agent is waiting for your response in the input panel below.')).not.toBeInTheDocument()
  })
})
