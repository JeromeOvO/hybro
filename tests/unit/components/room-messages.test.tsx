import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { useMessageStore } from '@/stores/message-store'

vi.mock('@/hooks/useAutoHideScroll', () => ({
  useAutoHideScroll: vi.fn(),
}))

// jsdom doesn't implement scrollIntoView
Element.prototype.scrollIntoView = vi.fn()

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
  return render(<RoomMessages />)
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
      expect(screen.getByText('Alice')).toBeTruthy()
    })

    it('should render agent messages', async () => {
      seedStore([{
        id: 'msg-1',
        content: 'Agent response here',
        senderName: 'Code Agent',
        messageType: 'agent',
        agentId: 'agent-1',
      }])
      await renderMessages()
      expect(screen.getByText('Code Agent')).toBeTruthy()
    })

    it('should render empty working agent tasks as agent bubbles with waiting indicator', async () => {
      seedStore([{
        id: 'msg-1',
        content: '',
        senderName: 'Code Agent',
        messageType: 'agent',
        agentId: 'agent-1',
        taskStatus: 'working',
      }])
      await renderMessages()
      expect(screen.getByText('Code Agent')).toBeTruthy()
      expect(screen.getByText('Working on your request\u2026')).toBeTruthy()
    })

    it('should render ephemeral processing placeholders as agent bubbles with task content', async () => {
      seedStore([{
        id: 'processing-placeholder-room-1',
        content: '',
        senderName: 'HYBRO AI',
        messageType: 'agent',
        taskStatus: 'working',
        taskContent: 'Processing your request...',
        isEphemeral: true,
      }])
      await renderMessages()
      expect(screen.getByText('HYBRO AI')).toBeTruthy()
      expect(screen.getByText('Processing your request...')).toBeTruthy()
    })

    it('should render working task with content as agent bubble showing the content', async () => {
      seedStore([{
        id: 'msg-1',
        content: '**How AI will change software engineering**\n\nArtificial Intelligence',
        senderName: 'Code Agent',
        messageType: 'agent',
        agentId: 'agent-1',
        taskStatus: 'working',
        taskContent: 'Analyzing request...',
      }])
      await renderMessages()
      expect(screen.getByText('Code Agent')).toBeTruthy()
      expect(screen.getByText('How AI will change software engineering')).toBeTruthy()
      expect(screen.getByText('Artificial Intelligence')).toBeTruthy()
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
      expect(screen.getByText('Agent')).toBeTruthy()
    })
  })

  describe('expand/collapse controls', () => {
    it('should show expand/collapse button when agent messages exist', async () => {
      seedStore([{
        id: 'msg-1',
        content: 'Some agent response',
        senderName: 'Agent',
        messageType: 'agent',
        agentId: 'agent-1',
      }])
      await renderMessages()
      const btn = screen.queryByLabelText('Expand all messages') ||
                  screen.queryByLabelText('Collapse all messages')
      expect(btn).toBeTruthy()
    })

    it('should not show expand/collapse button when only user messages', async () => {
      seedStore([{
        id: 'msg-1',
        content: 'User message only',
        senderName: 'User',
        messageType: 'user',
      }])
      await renderMessages()
      const btn = screen.queryByLabelText('Expand all messages') ||
                  screen.queryByLabelText('Collapse all messages')
      expect(btn).toBeNull()
    })
  })
})
