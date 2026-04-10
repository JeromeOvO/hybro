// tests/unit/components/agent-result-card.test.tsx
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
          status: 'awaiting_input',
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
})
