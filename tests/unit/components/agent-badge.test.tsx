import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { AgentBadge } from '@/components/agent-badge'

vi.mock('@/lib/agent-colors', () => ({
  getAgentColorClasses: () => ({
    bg: 'bg-blue-100',
    border: 'border-blue-300',
    accent: 'bg-blue-500',
    text: 'text-blue-700',
    content: 'text-blue-900',
  }),
}))

vi.mock('@/components/agent-source-badge', () => ({
  AgentSourceBadge: ({ source, className }: { source: string; className?: string }) => (
    <span data-testid={`source-badge-${source}`} className={className} />
  ),
}))

vi.mock('@/components/ui/tooltip', () => ({
  TooltipProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

afterEach(() => {
  cleanup()
})

describe('AgentBadge', () => {
  it('renders agent name with a color dot', () => {
    render(<AgentBadge agentId="agent-1" agentName="Code Agent" />)
    expect(screen.getByText('Code Agent')).toBeTruthy()
    const dot = screen.getByText('Code Agent').previousElementSibling
    expect(dot).toBeTruthy()
    expect(dot?.getAttribute('aria-hidden')).toBe('true')
  })

  it('renders cloud source badge when agentSource is "cloud"', () => {
    render(<AgentBadge agentId="a1" agentName="TestBot" agentSource="cloud" />)
    expect(screen.getByTestId('source-badge-cloud')).toBeTruthy()
    expect(screen.queryByText('cloud')).toBeNull()
  })

  it('renders hub source badge when agentSource is "hub"', () => {
    render(<AgentBadge agentId="a1" agentName="LocalBot" agentSource="hub" />)
    expect(screen.getByTestId('source-badge-hub')).toBeTruthy()
    expect(screen.queryByText('hub')).toBeNull()
  })

  it('falls back to cloud badge when agentSource is undefined and agent is NOT deleted', () => {
    render(<AgentBadge agentId="a1" agentName="Bot" />)
    expect(screen.getByTestId('source-badge-cloud')).toBeTruthy()
  })

  it('does NOT render source badge when agent is deleted (no agentId)', () => {
    render(<AgentBadge agentName="Old Agent" />)
    expect(screen.queryByTestId('source-badge-cloud')).toBeNull()
    expect(screen.queryByTestId('source-badge-hub')).toBeNull()
  })

  it('does NOT render source badge when hideSource is true', () => {
    render(<AgentBadge agentId="a1" agentName="Bot" agentSource="cloud" hideSource />)
    expect(screen.queryByTestId('source-badge-cloud')).toBeNull()
  })

  it('shows "(deleted)" suffix when agentId is missing', () => {
    render(<AgentBadge agentName="Old Agent" />)
    expect(screen.getByText('Old Agent (deleted)')).toBeTruthy()
  })

  it('shows "Unknown Agent (deleted)" when agentName is empty and agentId is missing', () => {
    render(<AgentBadge agentName="" />)
    expect(screen.getByText('Unknown Agent (deleted)')).toBeTruthy()
  })

  it('applies dimmed styling for deleted agents', () => {
    const { container } = render(<AgentBadge agentName="Gone" />)
    expect(container.firstElementChild!.className).toContain('opacity-50')
  })

  it('does NOT show (deleted) when showDeletedIndicator is false even without agentId', () => {
    render(<AgentBadge agentName="Summary Agent" showDeletedIndicator={false} />)
    expect(screen.getByText('Summary Agent')).toBeTruthy()
    expect(screen.queryByText(/deleted/i)).toBeNull()
  })

  it('does NOT apply opacity-50 when showDeletedIndicator is false', () => {
    const { container } = render(<AgentBadge agentName="X" showDeletedIndicator={false} />)
    expect(container.firstElementChild!.className).not.toContain('opacity-50')
  })

  it('uses text-sm for sm size', () => {
    render(<AgentBadge agentId="a1" agentName="Bot" size="sm" />)
    expect(screen.getByText('Bot').className).toContain('text-sm')
  })

  it('uses text-base for md size', () => {
    render(<AgentBadge agentId="a1" agentName="Bot" size="md" />)
    expect(screen.getByText('Bot').className).toContain('text-base')
  })

  it('handles missing agentId with fallback styling', () => {
    render(<AgentBadge agentName="Unknown Agent" showDeletedIndicator={false} />)
    expect(screen.getByText('Unknown Agent')).toBeTruthy()
    const dot = screen.getByText('Unknown Agent').previousElementSibling
    expect(dot).toBeTruthy()
    expect(dot?.className).toContain('bg-muted-foreground')
  })
})
