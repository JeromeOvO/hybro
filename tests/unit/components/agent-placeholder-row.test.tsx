import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { AgentPlaceholderRow } from '@/components/agent-placeholder-row'

vi.mock('@/lib/agent-avatar', () => ({
  getAgentAvatarUri: (seed: string) => `data:image/svg+xml;seed=${seed}`,
}))

afterEach(() => cleanup())

describe('AgentPlaceholderRow', () => {
  it('renders agent name', () => {
    render(<AgentPlaceholderRow agentId="a1" agentName="Weather Bot" />)
    expect(screen.getByText('Weather Bot')).toBeTruthy()
  })

  it('renders avatar image', () => {
    const { container } = render(<AgentPlaceholderRow agentId="a1" agentName="Bot" />)
    const img = container.querySelector('img')
    expect(img).toBeTruthy()
    expect(img!.getAttribute('src')).toContain('seed=a1')
  })

  it('renders shimmer "Thinking" text', () => {
    render(<AgentPlaceholderRow agentId="a1" agentName="Bot" />)
    expect(screen.getByText('Thinking')).toBeTruthy()
  })

  it('has shimmer-text class on status text', () => {
    render(<AgentPlaceholderRow agentId="a1" agentName="Bot" />)
    const thinking = screen.getByText('Thinking')
    expect(thinking.className).toContain('shimmer-text')
  })
})
