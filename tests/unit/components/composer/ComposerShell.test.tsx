import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { ComposerShell } from '@/components/composer/ComposerShell'
import { useTurnEventStore } from '@/stores/turn-event-store'

// Mock the RoomChatInput to avoid pulling in all dependencies
vi.mock('@/components/room-chat-input', () => ({
  RoomChatInput: (props: any) => (
    <div data-testid="room-chat-input" data-disabled={props.disabled}>
      {props.topSlot}
    </div>
  ),
}))

const mockAdapter = {
  roomId: 'test-room-id',
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
    useTurnEventStore.getState().reset()
  })

  it('renders in normal mode', () => {
    render(<ComposerShell adapter={mockAdapter} />)
    expect(screen.getByTestId('room-chat-input')).toBeDefined()
  })

  it('shows HitlResponseBar when there are pending HITLs', () => {
    const store = useTurnEventStore.getState()
    store.append('turn-1', {
      eventId: 'e1', turnId: 'turn-1', seq: 1, ts: 1000,
      type: 'turn_started', userInput: { text: 'hi', attachments: [] },
    })
    store.append('turn-1', {
      eventId: 'e2', turnId: 'turn-1', seq: 2, ts: 2000,
      type: 'hitl_requested', hitlId: 'h1', source: 'agent',
      agentName: 'Agent A', prompt: 'What color?', promptType: 'text',
    } as any)

    const { container } = render(<ComposerShell adapter={mockAdapter} />)
    // The HitlResponseBar is passed as topSlot to RoomChatInput, so it appears in the DOM
    expect(container.querySelector('[data-testid="hitl-response-bar"]')).toBeDefined()
    expect(screen.getAllByText('What color?').length).toBeGreaterThan(0)
  })
})
