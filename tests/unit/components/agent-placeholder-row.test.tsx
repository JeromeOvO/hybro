import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { AgentPlaceholderRow } from '@/components/agent-placeholder-row'

vi.mock('@/lib/agent-colors', () => ({
  getAgentColorClasses: () => ({
    bg: 'bg-blue-100',
    border: 'border-blue-300',
    accent: 'bg-blue-500',
    text: 'text-blue-700',
    content: 'text-blue-900',
  }),
}))

afterEach(() => cleanup())

describe('AgentPlaceholderRow', () => {
  it('renders agent name', () => {
    render(<AgentPlaceholderRow agentId="a1" agentName="Weather Bot" />)
    expect(screen.getByText('Weather Bot')).toBeTruthy()
  })

  // Removed dot indicator test since we now use AgentBadge with avatar

  it('renders shimmer "Thinking" text', () => {
    render(<AgentPlaceholderRow agentId="a1" agentName="Bot" />)
    expect(screen.getByText('Thinking')).toBeTruthy()
  })

  it('has shimmer-text class on status text', () => {
    render(<AgentPlaceholderRow agentId="a1" agentName="Bot" />)
    const thinking = screen.getByText('Thinking')
    expect(thinking.className).toContain('shimmer-text')
  })

  it('has data-testid for integration tests', () => {
    render(<AgentPlaceholderRow agentId="a1" agentName="Bot" />)
    expect(screen.getByTestId('placeholder-a1')).toBeTruthy()
  })
})
