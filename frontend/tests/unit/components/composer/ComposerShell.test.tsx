import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import {
  ComposerShell,
  type ComposerShellAdapter,
} from '@/components/composer/ComposerShell'
import { useMessageStore } from '@/stores/message-store'

vi.mock('@/components/room-chat-input', () => ({
  RoomChatInput: (props: any) => (
    <div
      data-testid="room-chat-input"
      data-disabled={props.disableSend}
      data-processing={props.processing}
      data-has-top-slot={props.topSlot ? 'true' : 'false'}
      data-target-mode={props.selectedGroupDispatch?.message_target_mode}
    >
      {props.topSlot}
    </div>
  ),
}))

const mockAdapter: ComposerShellAdapter = {
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
    selectedGroup: 'all_agents',
    resolvedTargetMode: { message_target_mode: 'room_default' },
    handleGroupChange: vi.fn(),
    handleCreateGroup: vi.fn(),
    handleEditGroup: vi.fn(),
    handleDeleteGroup: vi.fn(),
  },
  quoteState: { quote: null, clearQuote: vi.fn() },
  chatMode: 'ultimate',
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
    expect(screen.getByTestId('room-chat-input').getAttribute('data-target-mode')).toBe('room_default')
  })

  it('keeps stop mode while the room lifecycle is processing between agent tasks', () => {
    render(<ComposerShell adapter={{ ...mockAdapter, isProcessing: true }} />)

    const input = screen.getByTestId('room-chat-input')
    expect(input.getAttribute('data-processing')).toBe('true')
    expect(input.getAttribute('data-disabled')).toBe('true')
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
