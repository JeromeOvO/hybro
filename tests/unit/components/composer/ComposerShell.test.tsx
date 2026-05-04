import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { ComposerShell } from '@/components/composer/ComposerShell'
import { useMessageStore } from '@/stores/message-store'

vi.mock('@/components/room-chat-input', () => ({
  RoomChatInput: (props: any) => (
    <div data-testid="room-chat-input" data-disabled={props.disableSend} data-has-top-slot={props.topSlot ? 'true' : 'false'}>
      {props.topSlot}
    </div>
  ),
}))

const mockAdapter = {
  roomId: 'test-room',
  onSendMessage: vi.fn(),
  onCancelProcessing: vi.fn(),
  onRespondToHitl: vi.fn(),
  onChatModeChange: vi.fn(),
  isSending: false,
  isProcessing: false,
  isCancelling: false,
  agents: [],
  roomAgentIds: [],
  groupManagement: {
    groups: [],
    loadingGroups: false,
    selectedGroup: 'room_default',
    isOverride: false,
    handleGroupChange: vi.fn(),
    handleClearOverride: vi.fn(),
    handleCreateGroup: vi.fn(),
    handleEditGroup: vi.fn(),
    handleDeleteGroup: vi.fn(),
    onEditRoomAgents: vi.fn(),
  },
  quoteState: { quote: null, setQuote: vi.fn(), clearQuote: vi.fn() },
  chatMode: 'direct',
}

describe('ComposerShell', () => {
  beforeEach(() => {
    cleanup()
    const store = useMessageStore.getState()
    store.clearRoom()
    store.setRoom('test-room')
  })

  it('renders in normal mode', () => {
    render(<ComposerShell adapter={mockAdapter} />)
    expect(screen.getByTestId('room-chat-input')).toBeDefined()
  })

  it('shows HitlResponseBar when there are pending HITLs', () => {
    const store = useMessageStore.getState()
    store.upsertMessage({
      id: 'user-1',
      roomId: 'test-room',
      messageType: 'user',
      content: 'hi',
      senderName: 'User',
      timestamp: new Date().toISOString(),
    }, 'db')
    store.upsertMessage({
      id: 'hitl-1',
      roomId: 'test-room',
      messageType: 'agent',
      content: '',
      senderName: 'Agent A',
      timestamp: new Date().toISOString(),
      relatedMessageId: 'user-1',
      taskStatus: 'input-required' as any,
      hitlRequestId: 'h1',
      hitlPrompt: 'What color?',
      hitlPromptType: 'text',
      hitlResolved: false,
    }, 'db')

    render(<ComposerShell adapter={mockAdapter} />)
    expect(screen.getByText('What color?')).toBeDefined()
    expect(screen.getByTestId('hitl-response-frame')).toBeDefined()
    expect(screen.getByTestId('room-chat-input').getAttribute('data-has-top-slot')).toBe('false')
  })
})
