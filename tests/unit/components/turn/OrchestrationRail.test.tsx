import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { OrchestrationRail } from '@/components/turn/OrchestrationRail'
import type { RailItemView } from '@/stores/turn-event-store/types'

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

describe('OrchestrationRail', () => {
  it('renders rail items with labels', () => {
    const items: RailItemView[] = [
      { key: 'p1', icon: 'check', label: 'Planning...', ts: 1000, isActive: false },
      { key: 'p2', icon: 'spinner', label: 'Delegating to Agent A', ts: 2000, isActive: true },
    ]
    render(<OrchestrationRail items={items} />, { wrapper: Wrapper })
    expect(screen.getByText('Planning...')).toBeDefined()
    expect(screen.getByText('Delegating to Agent A')).toBeDefined()
  })

  it('renders nothing when items is empty', () => {
    const { container } = render(<OrchestrationRail items={[]} />, { wrapper: Wrapper })
    expect(container.children).toHaveLength(0)
  })

  it('shows processing placeholder when isProcessing and no items', () => {
    render(<OrchestrationRail items={[]} isProcessing={true} />, { wrapper: Wrapper })
    expect(screen.getByText('Processing...')).toBeDefined()
    expect(screen.queryAllByTestId('rail-spinner').length).toBeGreaterThan(0)
  })

  it('shows spinner animation for active items', () => {
    const items: RailItemView[] = [
      { key: 'p1', icon: 'spinner', label: 'Working...', ts: 1000, isActive: true },
    ]
    const { container } = render(<OrchestrationRail items={items} />, { wrapper: Wrapper })
    const spinner = container.querySelector('[data-testid="rail-spinner"]')
    expect(spinner).toBeDefined()
    expect(spinner?.classList.contains('animate-spin')).toBe(true)
  })
})
