/**
 * Tests for AgentContentBlock collapsible content feature.
 *
 * Covers:
 * - COLLAPSE_THRESHOLD (500 chars): short messages not collapsible, long messages collapsible
 * - Show more / Show less toggle button
 * - Expand / collapse signal response via ExpandCollapseContext
 * - No toggle button during streaming
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, fireEvent, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AgentContentBlock } from '@/components/turn/AgentContentBlock'
import { ExpandCollapseContext } from '@/components/turn/expand-collapse-context'
import type { ContentSlotView } from '@/stores/turn-event-store/types'

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

vi.mock('@/lib/agent-avatar', () => ({
  getAgentAvatarUri: () => undefined,
}))

vi.mock('@/lib/system-agents', () => ({
  SYSTEM_AGENTS: {},
}))

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

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

function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

/** Find the toggle button by matching its text content against a regex */
function findToggle(container: HTMLElement, text: RegExp): HTMLButtonElement | null {
  const buttons = container.querySelectorAll('button')
  for (const btn of buttons) {
    if (text.test(btn.textContent ?? '')) return btn
  }
  return null
}

// React 19 scheduler can fire async work after jsdom teardown; explicit cleanup prevents this.
afterEach(cleanup)

describe('AgentContentBlock — collapsible content', () => {
  it('does not show toggle button for short content', () => {
    const { container } = render(
      <AgentContentBlock slot={makeSlot({ content: 'Short message' })} />,
      { wrapper: Wrapper },
    )
    expect(findToggle(container, /show more/i)).toBeNull()
    expect(findToggle(container, /show less/i)).toBeNull()
  })

  it('shows "Show more" button for long content (>500 chars)', () => {
    const longContent = 'A'.repeat(501)
    const { container } = render(
      <AgentContentBlock slot={makeSlot({ content: longContent })} />,
      { wrapper: Wrapper },
    )
    expect(findToggle(container, /show more/i)).not.toBeNull()
  })

  it('starts collapsed for long messages', () => {
    const longContent = 'A'.repeat(600)
    const { container } = render(
      <AgentContentBlock slot={makeSlot({ content: longContent })} />,
      { wrapper: Wrapper },
    )
    const proseDiv = container.querySelector('.prose')
    expect(proseDiv?.className).toContain('max-h-[5lh]')
  })

  it('starts expanded for short messages', () => {
    const { container } = render(
      <AgentContentBlock slot={makeSlot({ content: 'Short' })} />,
      { wrapper: Wrapper },
    )
    const proseDiv = container.querySelector('.prose')
    expect(proseDiv?.className).not.toContain('max-h-[5lh]')
  })

  it('toggles between "Show more" and "Show less" on click', () => {
    const longContent = 'A'.repeat(600)
    const { container } = render(
      <AgentContentBlock slot={makeSlot({ content: longContent })} />,
      { wrapper: Wrapper },
    )

    const showMoreBtn = findToggle(container, /show more/i)!
    expect(showMoreBtn).not.toBeNull()

    // Click to expand
    fireEvent.click(showMoreBtn)
    expect(findToggle(container, /show less/i)).not.toBeNull()
    expect(findToggle(container, /show more/i)).toBeNull()

    // Click to collapse again
    fireEvent.click(findToggle(container, /show less/i)!)
    expect(findToggle(container, /show more/i)).not.toBeNull()
  })

  it('does not show toggle button while streaming', () => {
    const longContent = 'A'.repeat(600)
    const { container } = render(
      <AgentContentBlock slot={makeSlot({ content: longContent, status: 'streaming' })} />,
      { wrapper: Wrapper },
    )
    expect(findToggle(container, /show more/i)).toBeNull()
    expect(findToggle(container, /show less/i)).toBeNull()
  })

  it('shows estimated line count in "Show more" label', () => {
    const longContent = 'A'.repeat(800)
    const { container } = render(
      <AgentContentBlock slot={makeSlot({ content: longContent })} />,
      { wrapper: Wrapper },
    )
    const btn = findToggle(container, /show more/i)
    expect(btn?.textContent).toContain('lines')
  })

  it('shows gradient fade overlay when collapsed with long content', () => {
    const longContent = 'A'.repeat(600)
    const { container } = render(
      <AgentContentBlock slot={makeSlot({ content: longContent })} />,
      { wrapper: Wrapper },
    )
    expect(container.querySelector('.bg-linear-to-t')).not.toBeNull()
  })

  it('hides gradient fade overlay when expanded', () => {
    const longContent = 'A'.repeat(600)
    const { container } = render(
      <AgentContentBlock slot={makeSlot({ content: longContent })} />,
      { wrapper: Wrapper },
    )

    fireEvent.click(findToggle(container, /show more/i)!)
    expect(container.querySelector('.bg-linear-to-t')).toBeNull()
  })
})

describe('AgentContentBlock — expand/collapse signals', () => {
  function renderWithSignals(slot: ContentSlotView, expandSignal: number, collapseSignal: number) {
    return render(
      <ExpandCollapseContext.Provider value={{ expandSignal, collapseSignal }}>
        <QueryClientProvider client={queryClient}>
          <AgentContentBlock slot={slot} />
        </QueryClientProvider>
      </ExpandCollapseContext.Provider>,
    )
  }

  it('responds to expand signal from context', () => {
    const longContent = 'A'.repeat(600)
    const slot = makeSlot({ content: longContent })

    const { container, rerender } = renderWithSignals(slot, 0, 0)
    expect(findToggle(container, /show more/i)).not.toBeNull()

    rerender(
      <ExpandCollapseContext.Provider value={{ expandSignal: 1, collapseSignal: 0 }}>
        <QueryClientProvider client={queryClient}>
          <AgentContentBlock slot={slot} />
        </QueryClientProvider>
      </ExpandCollapseContext.Provider>,
    )
    expect(findToggle(container, /show less/i)).not.toBeNull()
  })

  it('responds to collapse signal from context', () => {
    const longContent = 'A'.repeat(600)
    const slot = makeSlot({ content: longContent })

    const { container, rerender } = renderWithSignals(slot, 1, 0)
    expect(findToggle(container, /show less/i)).not.toBeNull()

    rerender(
      <ExpandCollapseContext.Provider value={{ expandSignal: 1, collapseSignal: 1 }}>
        <QueryClientProvider client={queryClient}>
          <AgentContentBlock slot={slot} />
        </QueryClientProvider>
      </ExpandCollapseContext.Provider>,
    )
    expect(findToggle(container, /show more/i)).not.toBeNull()
  })

  it('signals do not affect short messages', () => {
    const slot = makeSlot({ content: 'Short' })

    const { container, rerender } = renderWithSignals(slot, 0, 0)
    expect(findToggle(container, /show more/i)).toBeNull()
    expect(findToggle(container, /show less/i)).toBeNull()

    rerender(
      <ExpandCollapseContext.Provider value={{ expandSignal: 1, collapseSignal: 0 }}>
        <QueryClientProvider client={queryClient}>
          <AgentContentBlock slot={slot} />
        </QueryClientProvider>
      </ExpandCollapseContext.Provider>,
    )
    expect(findToggle(container, /show more/i)).toBeNull()
    expect(findToggle(container, /show less/i)).toBeNull()
  })

  it('responds to multiple sequential signal increments', () => {
    const longContent = 'A'.repeat(600)
    const slot = makeSlot({ content: longContent })

    const { container, rerender } = renderWithSignals(slot, 0, 0)
    expect(findToggle(container, /show more/i)).not.toBeNull()

    // Expand
    rerender(
      <ExpandCollapseContext.Provider value={{ expandSignal: 1, collapseSignal: 0 }}>
        <QueryClientProvider client={queryClient}>
          <AgentContentBlock slot={slot} />
        </QueryClientProvider>
      </ExpandCollapseContext.Provider>,
    )
    expect(findToggle(container, /show less/i)).not.toBeNull()

    // Collapse
    rerender(
      <ExpandCollapseContext.Provider value={{ expandSignal: 1, collapseSignal: 1 }}>
        <QueryClientProvider client={queryClient}>
          <AgentContentBlock slot={slot} />
        </QueryClientProvider>
      </ExpandCollapseContext.Provider>,
    )
    expect(findToggle(container, /show more/i)).not.toBeNull()

    // Expand again
    rerender(
      <ExpandCollapseContext.Provider value={{ expandSignal: 2, collapseSignal: 1 }}>
        <QueryClientProvider client={queryClient}>
          <AgentContentBlock slot={slot} />
        </QueryClientProvider>
      </ExpandCollapseContext.Provider>,
    )
    expect(findToggle(container, /show less/i)).not.toBeNull()
  })
})
