// tests/unit/components/agent-result-stack.test.tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { AgentResultStack } from '@/components/agent-result-stack'
import type { AgentResultViewModel, TurnSummaryViewModel } from '@/lib/room-timeline/types'

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

function makeResult(overrides: Partial<AgentResultViewModel> = {}): AgentResultViewModel {
  return {
    agentId: 'agent-1',
    agentName: 'Agent',
    messageId: `msg-${Math.random().toString(36).slice(2, 6)}`,
    status: 'completed',
    content: 'Response content',
    artifacts: [],
    ...overrides,
  }
}

describe('AgentResultStack', () => {
  it('renders results sorted by status', () => {
    const results = [
      makeResult({ messageId: 'm-fail', agentId: 'a-fail', agentName: 'Failed Agent', status: 'failed', content: 'Error' }),
      makeResult({ messageId: 'm-ok', agentId: 'a-ok', agentName: 'OK Agent', status: 'completed', content: 'Good' }),
      makeResult({ messageId: 'm-wait', agentId: 'a-wait', agentName: 'Waiting Agent', status: 'awaiting_input', content: '' }),
    ]

    render(<AgentResultStack results={results} />)

    const stack = screen.getByTestId('agent-result-stack')
    const cards = Array.from(stack.querySelectorAll('[data-testid^="agent-result-"]'))
    // Order: completed with content (OK) → awaiting (Waiting) → failed (Failed)
    expect(cards[0].getAttribute('data-testid')).toBe('agent-result-m-ok')
    expect(cards[1].getAttribute('data-testid')).toBe('agent-result-m-wait')
    expect(cards[2].getAttribute('data-testid')).toBe('agent-result-m-fail')
  })

  it('summary source agent comes first', () => {
    const summary: TurnSummaryViewModel = {
      sourceAgentId: 'a-sup',
      sourceAgentName: 'Supervisor',
      title: 'Summary',
      body: 'Summary body',
    }
    const results = [
      makeResult({ messageId: 'm-1', agentId: 'a-normal', agentName: 'Normal Agent', content: 'Normal' }),
      makeResult({ messageId: 'm-2', agentId: 'a-sup', agentName: 'Supervisor', content: 'Supervised' }),
    ]

    const { container } = render(<AgentResultStack results={results} summary={summary} />)

    const stack = container.querySelector('[data-testid="agent-result-stack"]')
    expect(stack).toBeTruthy()
    const cards = Array.from(stack!.querySelectorAll('[data-testid^="agent-result-"]'))
    // Summary source (supervisor) should come first
    expect(cards[0].getAttribute('data-testid')).toBe('agent-result-m-2')
    expect(cards[1].getAttribute('data-testid')).toBe('agent-result-m-1')
  })

  it('empty results renders nothing', () => {
    const { container } = render(<AgentResultStack results={[]} />)
    expect(container.innerHTML).toBe('')
  })

  it('single result renders without stack spacing issues', () => {
    const results = [
      makeResult({ messageId: 'm-solo', agentName: 'Solo Agent', content: 'Solo response' }),
    ]

    const { container } = render(<AgentResultStack results={results} />)

    const stack = container.querySelector('[data-testid="agent-result-stack"]')
    expect(stack).toBeTruthy()
    expect(screen.getByText('Solo Agent')).toBeTruthy()
    expect(screen.getByText('Solo response')).toBeTruthy()
  })
})
