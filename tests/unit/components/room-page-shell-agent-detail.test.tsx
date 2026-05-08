import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import userEvent from '@testing-library/user-event'
import { cleanup, render, screen, within } from '../../utils/test-utils'
import { RoomPageShell, type TimelineAdapter } from '@/components/room-page-shell'
import { useMessageStore } from '@/stores/message-store'
import { useRoomUiStore } from '@/stores/room-ui-store'
import { createAgentMessage, createUserMessage, resetCounters } from '../../fixtures'
import { TASK_STATE } from '@/lib/types/sse'

vi.mock('@/components/composer/ComposerShell', () => ({
  ComposerShell: () => <div data-testid="composer-shell" />,
}))

const originalMatchMedia = window.matchMedia
const originalResizeObserver = window.ResizeObserver
const originalScrollTo = window.HTMLElement.prototype.scrollTo

class MockResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

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
    Object.defineProperty(window, 'ResizeObserver', {
      writable: true,
      configurable: true,
      value: MockResizeObserver,
    })
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

  afterEach(() => {
    cleanup()
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      configurable: true,
      value: originalMatchMedia,
    })
    Object.defineProperty(window, 'ResizeObserver', {
      writable: true,
      configurable: true,
      value: originalResizeObserver,
    })
    Object.defineProperty(window.HTMLElement.prototype, 'scrollTo', {
      writable: true,
      configurable: true,
      value: originalScrollTo,
    })
    vi.unstubAllGlobals()
  })

  it('opens and closes the right-side agent response pane from an agent card', async () => {
    render(<RoomPageShell adapter={makeAdapter()} />)

    expect(screen.queryByTestId('agent-response-detail-pane')).not.toBeInTheDocument()
    expect(screen.queryByTestId('conversation-resizable-workspace')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /open researcher alex response/i }))

    expect(screen.getByTestId('conversation-resizable-workspace')).toBeInTheDocument()
    const primaryPanel = screen.getByTestId('conversation-primary-panel')
    const detailPanel = screen.getByTestId('conversation-detail-panel')
    expect(primaryPanel).toBeInTheDocument()
    expect(screen.getByTestId('conversation-detail-resize-handle')).toBeInTheDocument()
    expect(detailPanel).toBeInTheDocument()
    expect(primaryPanel.getAttribute('style')).toMatch(/flex:\s*66\b/)
    expect(detailPanel.getAttribute('style')).toMatch(/flex:\s*34\b/)

    const pane = screen.getByTestId('agent-response-detail-pane')
    expect(within(pane).getByText('Researcher Alex')).toBeInTheDocument()
    expect(within(pane).getAllByText('Research a2a agents')).toHaveLength(1)
    expect(within(pane).getByRole('heading', { name: 'Report' })).toBeInTheDocument()

    await userEvent.click(within(pane).getByRole('button', { name: /close agent response/i }))

    expect(screen.queryByTestId('agent-response-detail-pane')).not.toBeInTheDocument()
  })

  it('does not scroll the transcript when opening the agent response pane', async () => {
    const scrollTo = vi.fn()
    Object.defineProperty(window.HTMLElement.prototype, 'scrollTo', {
      writable: true,
      configurable: true,
      value: scrollTo,
    })
    Object.defineProperty(window.HTMLElement.prototype, 'scrollHeight', {
      configurable: true,
      get: () => 2400,
    })

    useRoomUiStore.getState().markInitialHydrated('room-1')

    render(<RoomPageShell adapter={makeAdapter()} />)

    expect(scrollTo).toHaveBeenCalledTimes(1)

    await userEvent.click(screen.getByRole('button', { name: /open researcher alex response/i }))

    expect(screen.getByTestId('agent-response-detail-pane')).toBeInTheDocument()
    expect(scrollTo).toHaveBeenCalledTimes(1)
  })

  it('shows agent card buttons at narrow breakpoints but uses a mobile sheet instead of the side pane', async () => {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      configurable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    })

    render(<RoomPageShell adapter={makeAdapter()} />)

    // The card button IS present so users on mobile can tap it to open the sheet
    expect(screen.getByRole('button', { name: /open researcher alex response/i })).toBeInTheDocument()

    // The desktop split-pane layout is NOT used at narrow widths
    expect(screen.queryByTestId('conversation-resizable-workspace')).not.toBeInTheDocument()
    expect(screen.queryByTestId('agent-response-detail-pane')).not.toBeInTheDocument()
  })
})
