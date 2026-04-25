import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useMessageStore } from '@/stores/message-store'

// Mock event-log
vi.mock('@/lib/room-timeline/event-log', () => ({
  getEvents: () => [],
  appendEvent: vi.fn(),
  clearRoom: vi.fn(),
}))

vi.mock('@/hooks/useAutoHideScroll', () => ({
  useAutoHideScroll: vi.fn(),
}))

// jsdom doesn't implement scrollIntoView / scrollTo
Element.prototype.scrollIntoView = vi.fn()
HTMLElement.prototype.scrollTo = vi.fn()

function seedStore(messages: Array<{
  id: string
  content: string
  senderName: string
  messageType: 'user' | 'agent'
  timestamp?: string
  agentId?: string
  taskStatus?: string
  taskStatusMessage?: string | null
  taskContent?: string
  isEphemeral?: boolean
}>, hydrated = true) {
  const store = useMessageStore.getState()
  store.setRoom('room-1')

  for (const m of messages) {
    store.upsertMessage({
      id: m.id,
      roomId: 'room-1',
      messageType: m.messageType,
      content: m.content,
      senderName: m.senderName,
      timestamp: m.timestamp || new Date().toISOString(),
      agentId: m.agentId,
      taskStatus: m.taskStatus,
      taskStatusMessage: m.taskStatusMessage,
      taskContent: m.taskContent,
      isEphemeral: m.isEphemeral,
    }, 'db')
  }

  if (hydrated) {
    store.markDbSynced()
  }
}

async function renderMessages() {
  const { RoomMessages } = await import('@/components/room-messages')
  const queryClient = new QueryClient()
  return render(
    <QueryClientProvider client={queryClient}>
      <RoomMessages />
    </QueryClientProvider>
  )
}

describe('RoomMessages', () => {
  beforeEach(() => {
    useMessageStore.getState().clearRoom()
    vi.clearAllMocks()
  })

  afterEach(() => {
    cleanup()
  })

  describe('loading state', () => {
    it('should show loading state when not hydrated', async () => {
      seedStore([], false)
      await renderMessages()
      expect(screen.getByText('Loading messages...')).toBeTruthy()
    })
  })

  describe('empty state', () => {
    it('should show empty state when no messages exist', async () => {
      seedStore([])
      await renderMessages()
      expect(screen.getByText('Start the conversation')).toBeTruthy()
    })
  })

  describe('message rendering', () => {
    it('should render user messages', async () => {
      seedStore([{
        id: 'msg-1',
        content: 'Hello from user',
        senderName: 'Alice',
        messageType: 'user',
      }])
      await renderMessages()
      expect(screen.getByText('Hello from user')).toBeTruthy()
    })

    it('should render multiple messages', async () => {
      seedStore([
        {
          id: 'msg-1',
          content: 'User question',
          senderName: 'User',
          messageType: 'user',
          timestamp: '2024-01-01T00:00:00Z',
        },
        {
          id: 'msg-2',
          content: 'Agent answer',
          senderName: 'Agent',
          messageType: 'agent',
          agentId: 'agent-1',
          timestamp: '2024-01-01T00:00:01Z',
        },
      ])
      await renderMessages()
      expect(screen.getByText('User question')).toBeTruthy()
      expect(screen.getByText('Agent answer')).toBeTruthy()
    })
  })

  describe('sticky user message wrappers', () => {
    it('renders user messages inside a sticky wrapper with data-message-id', async () => {
      seedStore([
        { id: 'u1', content: 'Hello', senderName: 'Alice', messageType: 'user' },
        { id: 'a1', content: 'World', senderName: 'Bot', messageType: 'agent', agentId: 'agent-1' },
      ])
      const { container } = await renderMessages()
      const stickyWrapper = container.querySelector('[data-message-id="u1"]')
      expect(stickyWrapper).not.toBeNull()
      expect(stickyWrapper?.classList.contains('sticky')).toBe(true)
    })

    it('does NOT render sticky wrapper for system-prefix messages', async () => {
      seedStore([
        { id: 'a0', content: 'System welcome', senderName: 'Bot', messageType: 'agent', agentId: 'agent-1' },
        { id: 'u1', content: 'Hello', senderName: 'Alice', messageType: 'user' },
      ])
      const { container } = await renderMessages()
      expect(container.querySelector('[data-message-id="a0"]')).toBeNull()
      expect(container.querySelector('[data-message-id="u1"]')).not.toBeNull()
    })

    it('expand/collapse control does not use sticky positioning', async () => {
      seedStore([
        { id: 'u1', content: 'Hello', senderName: 'Alice', messageType: 'user' },
        { id: 'a1', content: 'World', senderName: 'Bot', messageType: 'agent', agentId: 'agent-1' },
      ])
      const { container } = await renderMessages()
      const expandBtn = container.querySelector('[aria-label*="Collapse"]')
        ?? container.querySelector('[aria-label*="Expand"]')
      const parent = expandBtn?.closest('[class*="absolute"]')
      expect(parent).not.toBeNull()
    })
  })
})
