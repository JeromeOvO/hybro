// tests/unit/components/conversation-timeline.test.tsx
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { useMessageStore } from '@/stores/message-store'
import { resetCounters, createUserMessage, createAgentMessage } from '../../fixtures'
import { TASK_STATE } from '@/lib/types/sse'

// Mock event-log
vi.mock('@/lib/room-timeline/event-log', () => ({
  getEvents: () => [],
  appendEvent: vi.fn(),
  clearRoom: vi.fn(),
}))

// Mock useAutoHideScroll since it accesses DOM APIs
vi.mock('@/hooks/useAutoHideScroll', () => ({
  useAutoHideScroll: () => {},
}))

// jsdom doesn't implement scrollIntoView
Element.prototype.scrollIntoView = vi.fn()

// Import after mocks
import { ConversationTimeline, TimelineErrorBoundary } from '@/components/conversation-timeline'

describe('ConversationTimeline', () => {
  beforeEach(() => {
    resetCounters()
    useMessageStore.setState({
      entities: {},
      orderedIds: [],
      roomId: 'room-1',
      hydratedFromDb: true,
      version: 0,
    })
  })

  afterEach(() => {
    cleanup()
  })

  it('renders empty state when no messages', () => {
    render(<ConversationTimeline />)
    expect(screen.getByText('No messages yet')).toBeTruthy()
  })

  it('renders loading state when not hydrated', () => {
    useMessageStore.setState({ hydratedFromDb: false })
    render(<ConversationTimeline />)
    expect(screen.getByText('Loading messages...')).toBeTruthy()
  })

  it('renders turns when messages exist', () => {
    const store = useMessageStore.getState()
    store.upsertMessage(
      createUserMessage({ id: 'u1', content: 'Hello world' }),
      'db',
    )
    store.upsertMessage(
      createAgentMessage({
        id: 'a1',
        content: 'Reply',
        taskStatus: TASK_STATE.COMPLETED,
      }),
      'db',
    )

    render(<ConversationTimeline />)
    // MemoizedTurn renders as <article aria-label="Turn N: ...">
    const turns = screen.getAllByRole('article')
    expect(turns.length).toBeGreaterThanOrEqual(1)
    expect(turns[0].getAttribute('aria-label')).toMatch(/Turn 1/)
  })

  it('error boundary falls back to flat message list', () => {
    // Simulate an error by rendering a component that throws
    const ThrowingChild = () => {
      throw new Error('Test error')
    }

    // Suppress console.error from React error boundary
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

    const store = useMessageStore.getState()
    store.upsertMessage(
      createUserMessage({ id: 'u1', content: 'Test message' }),
      'db',
    )

    render(
      <TimelineErrorBoundary>
        <ThrowingChild />
      </TimelineErrorBoundary>,
    )

    // The fallback should render the flat message list
    // Since the ErrorBoundary caught the error, it renders FallbackMessageList
    // which iterates orderedIds and renders messages
    expect(screen.getByText('Test message')).toBeTruthy()

    consoleSpy.mockRestore()
  })
})
