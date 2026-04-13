/**
 * Tests for TurnList expand/collapse all button.
 *
 * Covers:
 * - Button not rendered when there are no turns
 * - Button rendered when turns exist
 * - Toggle state: allExpanded -> collapse signal -> allCollapsed -> expand signal
 * - Correct aria-labels
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { TurnList } from '@/components/turn/TurnList'
import { useTurnEventStore } from '@/stores/turn-event-store'
import type { TurnEvent, UserInputData } from '@/stores/turn-event-store/types'

// Mock child components to avoid deep rendering
vi.mock('@/components/turn/OrchestraTurn', () => ({
  OrchestraTurn: ({ turnLog }: { turnLog: { turnId: string } }) => (
    <div data-testid={`turn-mock-${turnLog.turnId}`}>Turn mock</div>
  ),
}))

vi.mock('@/hooks/useAutoHideScroll', () => ({
  useAutoHideScroll: () => {},
}))

vi.mock('@/hooks/turn/useTurnScroll', () => ({
  useTurnScroll: () => ({
    messagesEndRef: { current: null },
    shouldAutoScroll: true,
    handleScroll: () => {},
    scrollToBottom: () => {},
  }),
}))

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

const userInput: UserInputData = { text: 'hello', attachments: [] }

/** querySelector shorthand scoped to rendered container */
function q(container: HTMLElement, selector: string) {
  return container.querySelector(selector)
}
function qa(container: HTMLElement, selector: string) {
  return container.querySelectorAll(selector)
}

describe('TurnList', () => {
  beforeEach(() => {
    useTurnEventStore.getState().reset()
  })

  it('shows empty state when there are no turns', () => {
    render(<TurnList />, { wrapper: Wrapper })
    expect(screen.getByText('No messages yet')).toBeDefined()
  })

  it('does not show expand/collapse button when there are no turns', () => {
    const { container } = render(<TurnList />, { wrapper: Wrapper })
    expect(q(container, '[aria-label="Collapse all responses"]')).toBeNull()
    expect(q(container, '[aria-label="Expand all responses"]')).toBeNull()
  })

  it('shows collapse-all button when turns exist (default state is expanded)', () => {
    const store = useTurnEventStore.getState()
    store.append('turn-1', {
      eventId: 'e1', turnId: 'turn-1', seq: 1, ts: Date.now(),
      type: 'turn_started', userInput,
    } as TurnEvent)

    const { container } = render(<TurnList />, { wrapper: Wrapper })
    const btn = q(container, '[aria-label="Collapse all responses"]')
    expect(btn).not.toBeNull()
  })

  it('toggles aria-label between collapse and expand on click', () => {
    const store = useTurnEventStore.getState()
    store.append('turn-1', {
      eventId: 'e1', turnId: 'turn-1', seq: 1, ts: Date.now(),
      type: 'turn_started', userInput,
    } as TurnEvent)

    const { container } = render(<TurnList />, { wrapper: Wrapper })

    // Initially: "Collapse all responses" (allExpanded=true)
    const collapseBtn = q(container, '[aria-label="Collapse all responses"]')!
    expect(collapseBtn).not.toBeNull()

    // Click to collapse all
    fireEvent.click(collapseBtn)

    // Now: "Expand all responses" (allExpanded=false)
    expect(q(container, '[aria-label="Expand all responses"]')).not.toBeNull()
    expect(q(container, '[aria-label="Collapse all responses"]')).toBeNull()

    // Click to expand all
    fireEvent.click(q(container, '[aria-label="Expand all responses"]')!)

    // Back to: "Collapse all responses"
    expect(q(container, '[aria-label="Collapse all responses"]')).not.toBeNull()
  })

  it('renders mock OrchestraTurn for each turn', () => {
    const store = useTurnEventStore.getState()
    store.append('turn-1', {
      eventId: 'e1', turnId: 'turn-1', seq: 1, ts: Date.now(),
      type: 'turn_started', userInput,
    } as TurnEvent)
    store.append('turn-2', {
      eventId: 'e2', turnId: 'turn-2', seq: 1, ts: Date.now(),
      type: 'turn_started', userInput,
    } as TurnEvent)

    const { container } = render(<TurnList />, { wrapper: Wrapper })
    const turns = qa(container, '[data-testid^="turn-mock-"]')
    expect(turns).toHaveLength(2)
  })
})
