import { beforeEach, describe, expect, it, vi } from 'vitest'
import userEvent from '@testing-library/user-event'
import { render, screen, within } from '../../utils/test-utils'
import { RoomPageShell, type TimelineAdapter } from '@/components/room-page-shell'
import { useMessageStore } from '@/stores/message-store'
import { useRoomUiStore } from '@/stores/room-ui-store'
import { createAgentMessage, createUserMessage, resetCounters } from '../../fixtures'
import { TASK_STATE } from '@/lib/types/sse'

vi.mock('@/components/composer/ComposerShell', () => ({
  ComposerShell: () => <div data-testid="composer-shell" />,
}))

function makeAdapter(roomId = 'room-1'): TimelineAdapter {
  return {
    roomId,
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
      selectedGroup: 'all',
      isOverride: false,
      handleGroupChange: vi.fn(),
      handleClearOverride: vi.fn(),
      handleCreateGroup: vi.fn(),
      handleEditGroup: vi.fn(),
      handleDeleteGroup: vi.fn(),
      onEditRoomAgents: vi.fn(),
    },
    quoteState: {
      quote: null,
      setQuote: vi.fn(),
      clearQuote: vi.fn(),
    },
    chatMode: 'ultimate',
  }
}

describe('RoomPageShell agent detail pane', () => {
  beforeEach(() => {
    resetCounters()
    useRoomUiStore.getState().resetAll()
    const store = useMessageStore.getState()
    store.clearRoom()
    store.setRoom('room-1')
    store.upsertMessage(createUserMessage({
      id: 'user-1',
      roomId: 'room-1',
      content: 'Research a2a agents',
    }), 'db')
    store.upsertMessage(createAgentMessage({
      id: 'agent-1',
      roomId: 'room-1',
      relatedMessageId: 'user-1',
      agentId: 'researcher-1',
      senderName: 'Researcher Alex',
      taskContent: 'Research a2a agents',
      taskStatus: TASK_STATE.COMPLETED,
      content: '# Report\n\nA2A findings.',
    }), 'db')
  })

  it('opens and closes the right-side agent response pane from an agent card', async () => {
    render(<RoomPageShell adapter={makeAdapter()} />)

    expect(screen.queryByTestId('agent-response-detail-pane')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /open researcher alex response/i }))

    const pane = screen.getByTestId('agent-response-detail-pane')
    expect(within(pane).getByText('Researcher Alex')).toBeInTheDocument()
    expect(within(pane).getByText('Research a2a agents')).toBeInTheDocument()
    expect(within(pane).getByRole('heading', { name: 'Report' })).toBeInTheDocument()

    await userEvent.click(within(pane).getByRole('button', { name: /close agent response/i }))

    expect(screen.queryByTestId('agent-response-detail-pane')).not.toBeInTheDocument()
  })
})
