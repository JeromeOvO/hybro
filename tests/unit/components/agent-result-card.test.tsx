// tests/unit/components/agent-result-card.test.tsx
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { AgentResultCard } from '@/components/agent-result-card'
import type { AgentResultViewModel } from '@/lib/room-timeline/types'

// Mock agent-colors
vi.mock('@/lib/agent-colors', () => ({
  getAgentColorClasses: () => ({
    bg: 'bg-blue-100',
    border: 'border-blue-300',
    accent: 'bg-blue-500',
    text: 'text-blue-700',
    content: 'text-blue-900',
  }),
}))

vi.mock('@/components/markdown-content', () => ({
  MarkdownContent: ({ content, className }: { content: string; className?: string }) => (
    <div data-testid="markdown-content" className={className}>
      {content}
    </div>
  ),
}))

vi.mock('@/components/agent-source-badge', () => ({
  AgentSourceBadge: ({ source, className }: { source: string; className?: string }) => (
    <span data-testid={`source-badge-${source}`} className={className} />
  ),
}))

vi.mock('@/components/ui/tooltip', () => ({
  TooltipProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

vi.mock('@/lib/agent-avatar', () => ({
  getAgentAvatarUri: (seed: string) => `data:image/svg+xml;seed=${seed}`,
}))

vi.mock('@/lib/system-agents', () => ({
  isSummarySystemAgent: () => false,
}))

afterEach(() => {
  cleanup()
})

function makeResult(overrides: Partial<AgentResultViewModel> = {}): AgentResultViewModel {
  return {
    agentId: 'agent-1',
    agentName: 'Test Agent',
    messageId: 'msg-1',
    status: 'completed',
    content: 'This is the agent response content.',
    artifacts: [],
    isSummaryAgent: false,
    ...overrides,
  }
}

describe('AgentResultCard', () => {
  it('renders completed result with content', () => {
    render(<AgentResultCard result={makeResult()} />)

    expect(screen.getByText('Test Agent')).toBeTruthy()
    expect(screen.getByText('This is the agent response content.')).toBeTruthy()
    // No status indicator for completed
    expect(screen.queryByText('Failed')).toBeNull()
  })

  it('renders failed result with error message', () => {
    render(
      <AgentResultCard
        result={makeResult({
          status: 'failed',
          content: 'Connection timeout to agent',
        })}
      />,
    )

    expect(screen.getByText('Test Agent')).toBeTruthy()
    expect(screen.getByText('Failed')).toBeTruthy()
    expect(screen.getByText('Connection timeout to agent')).toBeTruthy()
  })

  it('shows shimmer for streaming content', () => {
    const { container } = render(
      <AgentResultCard
        result={makeResult({
          status: 'working',
          content: 'Partial streaming response...',
        })}
      />,
    )

    // The aria-busy attribute indicates streaming
    const card = screen.getByTestId('agent-result-msg-1')
    expect(card.getAttribute('aria-busy')).toBe('true')
    // shimmer-text class is applied to the content wrapper
    expect(container.querySelector('.shimmer-text')).toBeTruthy()
  })

  it('shows "No response content" for empty completed result', () => {
    render(
      <AgentResultCard
        result={makeResult({
          content: '',
          status: 'completed',
        })}
      />,
    )

    expect(screen.getByText('No response content')).toBeTruthy()
  })

  it('truncates long content', () => {
    const longContent = Array.from({ length: 30 }, (_, i) => `Line ${i + 1}: some content here`).join('\n')

    render(<AgentResultCard result={makeResult({ content: longContent })} />)

    // The TruncatedContent component handles truncation
    // We verify the content body is present
    expect(screen.getByTestId('truncated-content-body')).toBeTruthy()
  })

  it('renders HITL history Q&A pairs', () => {
    render(
      <AgentResultCard
        result={makeResult({
          hitlHistory: [
            { prompt: 'What is the target region?', answer: 'North America' },
            { prompt: 'Confirm budget?', answer: 'Yes, approved' },
          ],
        })}
      />,
    )

    expect(screen.getByText('Human-in-the-loop')).toBeTruthy()
    expect(screen.getByText('Q: What is the target region?')).toBeTruthy()
    expect(screen.getByText('A: North America')).toBeTruthy()
    expect(screen.getByText('Q: Confirm budget?')).toBeTruthy()
    expect(screen.getByText('A: Yes, approved')).toBeTruthy()
  })

  it('does not render HITL section when no history', () => {
    render(<AgentResultCard result={makeResult()} />)

    expect(screen.queryByText('Human-in-the-loop')).toBeNull()
  })

  it('renders artifacts list', () => {
    render(
      <AgentResultCard
        result={makeResult({
          artifacts: [
            { artifactId: 'art-1', name: 'report.pdf', parts: [] },
            { artifactId: 'art-2', name: 'chart.png', parts: [] },
          ],
        })}
      />,
    )

    expect(screen.getByText('report.pdf')).toBeTruthy()
    expect(screen.getByText('chart.png')).toBeTruthy()
  })

  it('uses md size for agent badge in result header', () => {
    render(<AgentResultCard result={makeResult()} />)
    const nameEl = screen.getByText('Test Agent')
    expect(nameEl.className).toContain('text-base')
  })

  it('uses py-3 padding on result card container', () => {
    render(<AgentResultCard result={makeResult()} />)
    const card = screen.getByTestId('agent-result-msg-1')
    expect(card.className).toContain('py-3')
  })

  it('renders content with text-base via markdownClassName', () => {
    render(<AgentResultCard result={makeResult({ content: 'Some text' })} />)
    const md = screen.getByTestId('markdown-content')
    expect(md.className).toContain('text-base')
  })

  // --- V2: Shimmer status states ---

  it('shows shimmer "Generating" for working status with content', () => {
    const { container } = render(
      <AgentResultCard result={makeResult({ status: 'working', content: 'Partial...' })} />,
    )
    expect(screen.getByText('Generating')).toBeTruthy()
    expect(container.querySelector('.shimmer-text')).toBeTruthy()
    const card = screen.getByTestId('agent-result-msg-1')
    expect(card.getAttribute('aria-busy')).toBe('true')
  })

  it('shows shimmer "Thinking" for working status without content', () => {
    render(
      <AgentResultCard result={makeResult({ status: 'working', content: '' })} />,
    )
    expect(screen.getByText('Thinking')).toBeTruthy()
  })

  it('shows yellow shimmer "Needs input" for awaiting_input status', () => {
    const { container } = render(
      <AgentResultCard result={makeResult({ status: 'awaiting_input', content: '' })} />,
    )
    expect(screen.getByText('Needs input')).toBeTruthy()
    expect(container.querySelector('.shimmer-text-yellow')).toBeTruthy()
  })

  it('renders HitlCompactCard for resolved HITL', () => {
    render(
      <AgentResultCard
        result={makeResult({
          hitlResolved: { prompt: 'What range?', answer: 'last 30 days' },
        })}
      />,
    )
    expect(screen.getByText('What range?')).toBeTruthy()
    expect(screen.getByText('last 30 days')).toBeTruthy()
  })

  it('renders HitlQuestionCard for pending HITL', () => {
    render(
      <AgentResultCard
        result={makeResult({
          status: 'awaiting_input',
          hitlPending: { prompt: 'What date range?' },
        })}
      />,
    )
    expect(screen.getByText('What date range?')).toBeTruthy()
    // "Needs input" appears in both StatusText header and HitlQuestionCard
    expect(screen.getAllByText('Needs input').length).toBeGreaterThanOrEqual(1)
  })

  it('renders InlineChips when eventCount/durationMs present', () => {
    render(
      <AgentResultCard
        result={makeResult({ eventCount: 4, durationMs: 3200 })}
      />,
    )
    expect(screen.getByText('4 steps')).toBeTruthy()
    expect(screen.getByText('3.2s')).toBeTruthy()
  })
})
