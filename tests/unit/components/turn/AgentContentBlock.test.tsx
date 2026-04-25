import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AgentContentBlock } from '@/components/turn/AgentContentBlock'
import type { ContentSlotView } from '@/stores/turn-event-store/types'

// Mock complex dependencies
vi.mock('@/components/markdown-content', () => ({
  MarkdownContent: ({ content }: { content: string }) => <div>{content}</div>,
}))

vi.mock('@/components/artifact-renderer', () => ({
  ArtifactRenderer: ({ artifact }: { artifact: { artifactId: string } }) => (
    <div data-testid={`artifact-${artifact.artifactId}`}>Artifact</div>
  ),
}))

vi.mock('@/lib/agent-colors', () => ({
  getAgentColorClasses: () => ({ bg: 'bg-blue-500', text: 'text-blue-600', border: 'border-blue-500' }),
  getAgentInitials: (name: string) => name.slice(0, 2).toUpperCase(),
}))

function Wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

// Prevent pending React scheduler work from leaking across files.
afterEach(cleanup)

function makeSlot(overrides: Partial<ContentSlotView> = {}): ContentSlotView {
  return {
    slotId: 'msg-1',
    slotType: 'agent',
    agentId: 'agent-1',
    agentName: 'Test Agent',
    content: 'Hello world',
    artifacts: [],
    status: 'completed',
    ...overrides,
  }
}

describe('AgentContentBlock', () => {
  it('renders agent name and content', () => {
    render(<AgentContentBlock slot={makeSlot()} />, { wrapper: Wrapper })
    expect(screen.getByText('Test Agent')).toBeDefined()
    expect(screen.getByText('Hello world')).toBeDefined()
  })

  it('shows streaming indicator when status is streaming', () => {
    render(<AgentContentBlock slot={makeSlot({ status: 'streaming', content: 'typing...' })} />, { wrapper: Wrapper })
    expect(screen.getByTestId('streaming-indicator')).toBeDefined()
  })

  it('shows error marker when status is failed', () => {
    render(<AgentContentBlock slot={makeSlot({ status: 'failed', error: 'agent crashed' })} />, { wrapper: Wrapper })
    expect(screen.getByText('agent crashed')).toBeDefined()
  })

  it('renders empty content for complete-empty slot', () => {
    const { container } = render(<AgentContentBlock slot={makeSlot({ content: '', status: 'completed' })} />, { wrapper: Wrapper })
    expect(container.textContent).toContain('Test Agent')
    expect(container.querySelector('.prose')).toBeNull()
  })
})
