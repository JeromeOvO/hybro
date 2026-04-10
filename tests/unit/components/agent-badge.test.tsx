import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'

import { AgentBadge } from '@/components/agent-badge'

describe('AgentBadge', () => {
  it('renders agent name with a color dot', () => {
    render(<AgentBadge agentId="agent-1" agentName="Code Agent" />)

    expect(screen.getByText('Code Agent')).toBeTruthy()
    const dot = screen.getByText('Code Agent').previousElementSibling
    expect(dot).toBeTruthy()
    expect(dot?.getAttribute('aria-hidden')).toBe('true')
  })

  it('shows source badge when agentSource is provided', () => {
    render(
      <AgentBadge agentId="agent-1" agentName="Hub Agent" agentSource="hub" />,
    )

    expect(screen.getByText('Hub Agent')).toBeTruthy()
    expect(screen.getByText('hub')).toBeTruthy()
  })

  it('handles missing agentId with fallback styling', () => {
    render(<AgentBadge agentName="Unknown Agent" />)

    expect(screen.getByText('Unknown Agent')).toBeTruthy()
    const dot = screen.getByText('Unknown Agent').previousElementSibling
    expect(dot).toBeTruthy()
    expect(dot?.className).toContain('bg-muted-foreground')
  })
})
