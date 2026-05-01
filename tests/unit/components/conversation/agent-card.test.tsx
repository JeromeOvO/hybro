import { describe, expect, it } from 'vitest'
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
})
