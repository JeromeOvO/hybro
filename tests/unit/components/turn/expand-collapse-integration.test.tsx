/**
 * Integration test: TurnList expand/collapse button -> ExpandCollapseContext -> AgentContentBlock.
 *
 * Verifies the full signal flow:
 * 1. TurnList renders the toggle button
 * 2. Clicking the button increments expandSignal or collapseSignal
 * 3. ExpandCollapseContext propagates signals
 * 4. AgentContentBlock responds by expanding/collapsing long content
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, fireEvent, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { TurnList } from '@/components/turn/TurnList'
import { useTurnEventStore } from '@/stores/turn-event-store'
import type { TurnEvent, UserInputData } from '@/stores/turn-event-store/types'

// Mock only the leaf dependencies, keep the component tree intact
vi.mock('@clerk/nextjs', () => ({
  useUser: () => ({ user: { username: 'testuser', firstName: 'Test', imageUrl: null } }),
}))

vi.mock('@/components/markdown-content', () => ({
  MarkdownContent: ({ content }: { content: string }) => <div data-testid="markdown">{content}</div>,
  LinkifiedContent: ({ content }: { content: string }) => <span>{content}</span>,
}))

vi.mock('@/components/artifact-renderer', () => ({
  ArtifactRenderer: () => <div>Artifact</div>,
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

vi.mock('@/components/message-bubble', () => ({
  UserAttachmentCard: () => <div>Attachment</div>,
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

const userInput: UserInputData = { text: 'What is AI?', attachments: [] }

/** querySelector helpers scoped to container */
function q(container: HTMLElement, selector: string) {
  return container.querySelector(selector)
}

/** Find toggle buttons by text content matching regex */
function findToggles(container: HTMLElement, text: RegExp) {
  const buttons = container.querySelectorAll('button')
  return Array.from(buttons).filter(btn => text.test(btn.textContent ?? ''))
}

function setupTurnWithLongContent() {
  const store = useTurnEventStore.getState()
  const longContent = 'This is a detailed response from the AI agent. '.repeat(20)

  store.append('turn-1', {
    eventId: 'e1', turnId: 'turn-1', seq: 1, ts: Date.now(),
    type: 'turn_started', userInput,
  } as TurnEvent)
  store.append('turn-1', {
    eventId: 'e2', turnId: 'turn-1', seq: 2, ts: Date.now(),
    type: 'slot_opened', slotId: 'msg-1', slotType: 'agent', agentId: 'a1', agentName: 'Agent A',
  } as TurnEvent)
  store.append('turn-1', {
    eventId: 'e3', turnId: 'turn-1', seq: 3, ts: Date.now(),
    type: 'slot_snapshot', slotId: 'msg-1', content: longContent, artifacts: [],
  } as TurnEvent)
  store.append('turn-1', {
    eventId: 'e4', turnId: 'turn-1', seq: 4, ts: Date.now(),
    type: 'slot_terminated', slotId: 'msg-1', status: 'completed',
  } as TurnEvent)
  store.append('turn-1', {
    eventId: 'e5', turnId: 'turn-1', seq: 5, ts: Date.now(),
    type: 'turn_completed', durationMs: 1000,
  } as TurnEvent)
}

describe('Expand/Collapse integration (TurnList -> Context -> AgentContentBlock)', () => {
  beforeEach(() => {
    useTurnEventStore.getState().reset()
  })

  // React 19 scheduler can fire async work after jsdom teardown
  afterEach(cleanup)

  it('renders long content as collapsed with "Show more" toggle', () => {
    setupTurnWithLongContent()
    const { container } = render(<TurnList />, { wrapper: Wrapper })

    const toggleBtns = findToggles(container, /show more/i)
    expect(toggleBtns).toHaveLength(1)
  })

  it('collapse all button collapses long agent content', () => {
    setupTurnWithLongContent()
    const { container } = render(<TurnList />, { wrapper: Wrapper })

    // Expand the agent content manually first
    const showMoreBtn = findToggles(container, /show more/i)[0]
    fireEvent.click(showMoreBtn)
    expect(findToggles(container, /show less/i)).toHaveLength(1)

    // Click "Collapse all responses"
    const collapseAllBtn = q(container, '[aria-label="Collapse all responses"]')!
    fireEvent.click(collapseAllBtn)

    // Agent content should now show "Show more"
    expect(findToggles(container, /show more/i)).toHaveLength(1)
  })

  it('expand all button expands collapsed agent content', () => {
    setupTurnWithLongContent()
    const { container } = render(<TurnList />, { wrapper: Wrapper })

    // Content starts collapsed
    expect(findToggles(container, /show more/i)).toHaveLength(1)

    // Switch toggle button to "Expand all" state
    fireEvent.click(q(container, '[aria-label="Collapse all responses"]')!)
    expect(q(container, '[aria-label="Expand all responses"]')).not.toBeNull()

    // Click "Expand all responses"
    fireEvent.click(q(container, '[aria-label="Expand all responses"]')!)

    // Agent content should now show "Show less"
    expect(findToggles(container, /show less/i)).toHaveLength(1)
  })

  it('full toggle cycle: collapse all -> expand all -> collapse all', () => {
    setupTurnWithLongContent()
    const { container } = render(<TurnList />, { wrapper: Wrapper })

    // Expand manually first
    fireEvent.click(findToggles(container, /show more/i)[0])
    expect(findToggles(container, /show less/i)).toHaveLength(1)

    // Collapse all
    fireEvent.click(q(container, '[aria-label="Collapse all responses"]')!)
    expect(findToggles(container, /show more/i)).toHaveLength(1)

    // Expand all
    fireEvent.click(q(container, '[aria-label="Expand all responses"]')!)
    expect(findToggles(container, /show less/i)).toHaveLength(1)

    // Collapse all again
    fireEvent.click(q(container, '[aria-label="Collapse all responses"]')!)
    expect(findToggles(container, /show more/i)).toHaveLength(1)
  })

  it('multiple agents with long content all respond to collapse/expand signals', () => {
    const store = useTurnEventStore.getState()
    const longContent1 = 'Response from agent one. '.repeat(25)
    const longContent2 = 'Response from agent two. '.repeat(25)

    store.append('turn-1', {
      eventId: 'e1', turnId: 'turn-1', seq: 1, ts: Date.now(),
      type: 'turn_started', userInput,
    } as TurnEvent)
    store.append('turn-1', {
      eventId: 'e2', turnId: 'turn-1', seq: 2, ts: Date.now(),
      type: 'slot_opened', slotId: 'msg-1', slotType: 'agent', agentId: 'a1', agentName: 'Agent A',
    } as TurnEvent)
    store.append('turn-1', {
      eventId: 'e3', turnId: 'turn-1', seq: 3, ts: Date.now(),
      type: 'slot_snapshot', slotId: 'msg-1', content: longContent1, artifacts: [],
    } as TurnEvent)
    store.append('turn-1', {
      eventId: 'e4', turnId: 'turn-1', seq: 4, ts: Date.now(),
      type: 'slot_terminated', slotId: 'msg-1', status: 'completed',
    } as TurnEvent)
    store.append('turn-1', {
      eventId: 'e5', turnId: 'turn-1', seq: 5, ts: Date.now(),
      type: 'slot_opened', slotId: 'msg-2', slotType: 'agent', agentId: 'a2', agentName: 'Agent B',
    } as TurnEvent)
    store.append('turn-1', {
      eventId: 'e6', turnId: 'turn-1', seq: 6, ts: Date.now(),
      type: 'slot_snapshot', slotId: 'msg-2', content: longContent2, artifacts: [],
    } as TurnEvent)
    store.append('turn-1', {
      eventId: 'e7', turnId: 'turn-1', seq: 7, ts: Date.now(),
      type: 'slot_terminated', slotId: 'msg-2', status: 'completed',
    } as TurnEvent)
    store.append('turn-1', {
      eventId: 'e8', turnId: 'turn-1', seq: 8, ts: Date.now(),
      type: 'turn_completed', durationMs: 2000,
    } as TurnEvent)

    const { container } = render(<TurnList />, { wrapper: Wrapper })

    // Both agents should have "Show more" buttons (collapsed by default)
    expect(findToggles(container, /show more/i)).toHaveLength(2)

    // Switch to "Expand all" mode
    fireEvent.click(q(container, '[aria-label="Collapse all responses"]')!)

    // Click "Expand all"
    fireEvent.click(q(container, '[aria-label="Expand all responses"]')!)

    // Both agents should now show "Show less"
    expect(findToggles(container, /show less/i)).toHaveLength(2)

    // Collapse all
    fireEvent.click(q(container, '[aria-label="Collapse all responses"]')!)

    // Both agents should show "Show more" again
    expect(findToggles(container, /show more/i)).toHaveLength(2)
  })
})
