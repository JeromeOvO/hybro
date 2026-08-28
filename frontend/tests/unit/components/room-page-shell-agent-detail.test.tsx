import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import userEvent from '@testing-library/user-event'
import { act, cleanup, render, screen, within } from '../../utils/test-utils'
import { RoomPageShell, type TimelineAdapter } from '@/components/room-page-shell'
import { useMessageStore } from '@/stores/message-store'
import { useRoomUiStore } from '@/stores/room-ui-store'
import { useTurnStore } from '@/stores/turn-store'
import { createAgentMessage, createUserMessage, resetCounters } from '../../fixtures'
import { TASK_STATE } from '@/lib/types/sse'

vi.mock('@/components/composer/ComposerShell', () => ({
  ComposerShell: () => <div data-testid="composer-shell" />,
}))

const setSidebarOpen = vi.fn()
const setSidebarOpenMobile = vi.fn()

vi.mock('@/components/ui/sidebar', () => ({
  useSidebar: () => ({
    state: 'expanded',
    open: true,
    setOpen: setSidebarOpen,
    openMobile: false,
    setOpenMobile: setSidebarOpenMobile,
    isMobile: false,
    toggleSidebar: vi.fn(),
  }),
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
      onRespondToHitlBatch: vi.fn(),
    onCancelHitl: vi.fn(),
    onRefreshHitl: vi.fn(),
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
      resolvedTargetMode: { message_target_mode: 'all_agents' as const },
      handleGroupChange: vi.fn(),
      handleCreateGroup: vi.fn(),
      handleEditGroup: vi.fn(),
      handleDeleteGroup: vi.fn(),
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
    setSidebarOpen.mockClear()
    setSidebarOpenMobile.mockClear()
    Object.defineProperty(window, 'ResizeObserver', {
      writable: true,
      configurable: true,
      value: MockResizeObserver,
    })
    resetCounters()
    useRoomUiStore.getState().resetAll()
    useTurnStore.getState().clear()
    const store = useMessageStore.getState()
    store.clearRoom()
    store.setRoom('room-1')
    store.upsertMessage(createUserMessage({
      id: 'user-1',
      roomId: 'room-1',
      content: 'Research a2a agents',
    }), 'db')
    const messageId = 'orchestrator:run-research:inv_research_0001'
    store.upsertMessage(createAgentMessage({
      id: messageId,
      roomId: 'room-1',
      relatedMessageId: 'user-1',
      agentId: undefined,
      senderName: 'Researcher Alex',
      dispatchText: 'Research a2a agents',
      taskStatus: TASK_STATE.COMPLETED,
      content: '',
    }), 'db')
    useTurnStore.getState().replaceSnapshot('room-1', [{
      hybro_turn_id: 'run-research', run_id: 'run-research', user_message_id: 'user-1',
      client_request_id: 'client-research', state: 'active',
      started_at: '2030-01-01T00:00:00.000Z', settled_at: null,
      duration_ms: null, terminal_code: null, terminal_summary: null,
      internal_turns: [{
        internal_turn_id: 'turn-1', attempt: 1, message_ids: [],
        tool_call_ids: ['inv_research_0001'], status: 'active',
      }],
      activity: [{
        kind: 'tool', id: 'inv_research_0001', internal_turn_id: 'turn-1',
        tool_call_id: 'inv_research_0001', label: 'Researcher Alex', input: {},
        partial_result: '', result: '', is_error: false, duration_ms: 100,
        status: 'completed', update_index: 0, execution_kind: 'agent',
        target_name: 'Researcher Alex', request_summary: 'Research a2a agents',
        detail_available: true, order: 1,
      }],
      current_assistant: null, final_answer: null, final_committed: false,
      hitl_interactions: [], active_interaction_id: null,
      agent_call_message_ids: [messageId],
    }])
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      run_id: 'run-research', public_call_id: 'inv_research_0001', status: 'completed',
      output: '# Report\n\nA2A findings.', artifacts: [],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
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
    expect(primaryPanel.getAttribute('style')).toMatch(/flex:\s*50\b/)
    expect(detailPanel.getAttribute('style')).toMatch(/flex:\s*50\b/)
    expect(screen.queryByRole('button', { name: /enter fullscreen/i })).not.toBeInTheDocument()
    expect(setSidebarOpen).toHaveBeenCalledWith(false)

    const pane = screen.getByTestId('agent-response-detail-pane')
    expect(within(pane).getByText('Researcher Alex')).toBeInTheDocument()
    expect(within(pane).getAllByText('Research a2a agents')).toHaveLength(1)
    expect(within(pane).getByRole('heading', { name: 'Report' })).toBeInTheDocument()

    await userEvent.click(within(pane).getByRole('button', { name: /close agent response/i }))

    expect(setSidebarOpen).toHaveBeenLastCalledWith(true)
    expect(screen.queryByTestId('agent-response-detail-pane')).not.toBeInTheDocument()
  })

  it('loads canonical private output by opaque card identity', async () => {
    const messageId = 'orchestrator:run-1:inv_weather_0001'
    useMessageStore.getState().upsertMessage(createAgentMessage({
      id: messageId,
      roomId: 'room-1',
      relatedMessageId: 'user-1',
      agentId: undefined,
      senderName: 'Weather Agent',
      taskStatus: TASK_STATE.COMPLETED,
      content: '',
    }), 'sse')
    useTurnStore.getState().replaceSnapshot('room-1', [{
      hybro_turn_id: 'run-1', run_id: 'run-1', user_message_id: 'user-1',
      client_request_id: 'client-1', state: 'active',
      started_at: '2030-01-01T00:00:00.000Z', settled_at: null,
      duration_ms: null, terminal_code: null, terminal_summary: null,
      internal_turns: [], activity: [], current_assistant: null, final_answer: null,
      final_committed: false, hitl_interactions: [], active_interaction_id: null,
      agent_call_message_ids: [messageId],
    }])
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      run_id: 'run-1', public_call_id: 'inv_weather_0001', status: 'completed',
      output: 'FLATTENED OUTPUT MUST NOT RENDER',
      parts: [
        { kind: 'data', data: { temperature: 27, unit: 'C' } },
        { kind: 'text', text: '# Private weather output\n\nSunny.' },
      ],
      artifacts: [],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    render(<RoomPageShell adapter={{ ...makeAdapter(), getToken: async () => 'token' }} />)
    act(() => useRoomUiStore.getState().openAgentDetail('room-1', messageId))

    expect(await screen.findByRole('heading', { name: 'Private weather output' })).toBeInTheDocument()
    expect(screen.queryByText('FLATTENED OUTPUT MUST NOT RENDER')).not.toBeInTheDocument()
    const jsonToggle = screen.getByRole('button', { name: /json.*lines/i })
    expect(jsonToggle).toHaveAttribute('aria-expanded', 'false')
    await userEvent.click(jsonToggle)
    expect(screen.getByTestId('agent-response-detail-pane').querySelector('pre code'))
      .toHaveTextContent('"temperature": 27')
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/rooms/room-1/agent-calls/run-1/inv_weather_0001/detail'),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
    expect(screen.queryByText('inv_weather_0001')).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Weather Agent' })).not.toBeInTheDocument()
    expect(screen.getAllByText('Weather Agent').length).toBeGreaterThan(0)
  })

  it('shows a private-detail loading state without falling back to public content', () => {
    const messageId = 'orchestrator:run-1:inv_private_0001'
    useMessageStore.getState().upsertMessage(createAgentMessage({
      id: messageId,
      roomId: 'room-1',
      relatedMessageId: 'user-1',
      agentId: undefined,
      senderName: 'Private Agent',
      taskStatus: TASK_STATE.COMPLETED,
      content: 'PUBLIC SENTINEL MUST NOT RENDER',
    }), 'sse')
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})))

    render(<RoomPageShell adapter={{ ...makeAdapter(), getToken: async () => 'token' }} />)
    act(() => useRoomUiStore.getState().openAgentDetail('room-1', messageId))

    expect(screen.getByText('Loading response…')).toBeInTheDocument()
    expect(screen.queryByText('PUBLIC SENTINEL MUST NOT RENDER')).not.toBeInTheDocument()
  })

  it('keeps a working canonical detail pending without fetching unavailable output', () => {
    const messageId = 'orchestrator:run-1:inv_working_0001'
    useMessageStore.getState().upsertMessage(createAgentMessage({
      id: messageId,
      roomId: 'room-1',
      relatedMessageId: 'user-1',
      agentId: undefined,
      senderName: 'Working Agent',
      taskStatus: TASK_STATE.WORKING,
      content: '',
    }), 'sse')
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    render(<RoomPageShell adapter={{ ...makeAdapter(), getToken: async () => 'token' }} />)
    act(() => useRoomUiStore.getState().openAgentDetail('room-1', messageId))

    expect(screen.getByTestId('agent-response-detail-pane')).toBeInTheDocument()
    expect(screen.getByText('Working on your request…')).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.queryByText(/HTTP error/i)).not.toBeInTheDocument()
  })

  it('retries a transient terminal canonical 404 until private detail is durable', async () => {
    const messageId = 'orchestrator:run-1:inv_pending_0001'
    useMessageStore.getState().upsertMessage(createAgentMessage({
      id: messageId,
      roomId: 'room-1',
      relatedMessageId: 'user-1',
      agentId: undefined,
      senderName: 'Pending Agent',
      taskStatus: TASK_STATE.COMPLETED,
      content: '',
    }), 'sse')
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(
        JSON.stringify({ detail: 'Agent call output not found' }),
        { status: 404, headers: { 'Content-Type': 'application/json' } },
      ))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        run_id: 'run-1', public_call_id: 'inv_pending_0001', status: 'completed',
        output: 'Projection is ready.', artifacts: [],
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    render(<RoomPageShell adapter={{ ...makeAdapter(), getToken: async () => 'token' }} />)
    act(() => useRoomUiStore.getState().openAgentDetail('room-1', messageId))

    expect(await screen.findByText('Projection is ready.', {}, { timeout: 1500 })).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(screen.queryByText(/HTTP error/i)).not.toBeInTheDocument()
  })

  it('renders authenticated private artifacts when the response has no text', async () => {
    const messageId = 'orchestrator:run-1:inv_artifact_0001'
    useMessageStore.getState().upsertMessage(createAgentMessage({
      id: messageId,
      roomId: 'room-1',
      relatedMessageId: 'user-1',
      agentId: undefined,
      senderName: 'Artifact Agent',
      taskStatus: TASK_STATE.COMPLETED,
      content: '',
    }), 'sse')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      run_id: 'run-1', public_call_id: 'inv_artifact_0001', status: 'completed',
      output: '',
      artifacts: [{
        artifact_ref: '/api/v1/files/af011190aaba4f97b459e7656bba7f7e/content',
        file_id: 'af011190aaba4f97b459e7656bba7f7e',
        name: 'report.pdf',
        mime_type: 'application/pdf',
        size_bytes: 1024,
      }],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })))

    render(<RoomPageShell adapter={{ ...makeAdapter(), getToken: async () => 'token' }} />)
    act(() => useRoomUiStore.getState().openAgentDetail('room-1', messageId))

    expect((await screen.findAllByText('report.pdf')).length).toBeGreaterThan(0)
    expect(screen.queryByText('No response content yet.')).not.toBeInTheDocument()
  })

  it('shares canonical detail and file loads between the final body and Agent detail', async () => {
    const fileId = '22222222222222222222222222222222'
    const messageId = 'orchestrator:run-image:inv_image_0001'
    useTurnStore.getState().replaceSnapshot('room-1', [{
      hybro_turn_id: 'run-image', run_id: 'run-image', user_message_id: 'user-1',
      client_request_id: 'client-image', state: 'completed',
      started_at: '2030-01-01T00:00:00.000Z', settled_at: '2030-01-01T00:00:01.000Z',
      duration_ms: 1000, terminal_code: null, terminal_summary: null,
      internal_turns: [{
        internal_turn_id: 'turn-image', attempt: 1, message_ids: ['assistant-image'],
        tool_call_ids: ['inv_image_0001'], status: 'completed',
      }],
      activity: [{
        kind: 'tool', id: 'inv_image_0001', internal_turn_id: 'turn-image',
        tool_call_id: 'inv_image_0001', label: 'Image Generator Agent', input: {},
        partial_result: '', result: '', is_error: false, duration_ms: 100,
        status: 'completed', update_index: 0, execution_kind: 'agent',
        target_name: 'Image Generator Agent', request_summary: 'Generate an image',
        detail_available: true, order: 1,
      }],
      current_assistant: null,
      final_answer: {
        message_id: 'assistant-image', internal_turn_id: 'turn-image',
        text: 'Here is the generated image.', status: 'completed',
        content_index: 0, next_delta_index: 0, end_offset: 28, order: 2,
      },
      final_committed: true, hitl_interactions: [], active_interaction_id: null,
      agent_call_message_ids: [messageId],
    }])
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input)
      if (url.includes('/agent-calls/')) {
        return new Response(JSON.stringify({
          run_id: 'run-image', public_call_id: 'inv_image_0001', status: 'completed',
          output: 'FLATTENED IMAGE OUTPUT MUST NOT RENDER',
          parts: [{ kind: 'text', text: 'Generated image response.' }],
          artifacts: [{
            artifact_ref: `/api/v1/files/${fileId}/content`,
            file_id: fileId,
            name: 'shared-image.png',
            mime_type: 'image/png',
            size_bytes: 5,
          }],
        }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.includes(`/files/${fileId}/content`)) {
        return new Response('image', {
          status: 200,
          headers: { 'Content-Type': 'image/png' },
        })
      }
      throw new Error(`Unexpected fetch: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => 'blob:shared-image'),
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: vi.fn(),
    })

    render(<RoomPageShell adapter={{ ...makeAdapter(), getToken: async () => 'token' }} />)
    expect(await screen.findByRole('img', { name: 'shared-image.png' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /open image generator agent response/i }))
    expect(await screen.findByTestId('agent-response-detail-pane')).toBeInTheDocument()
    expect((await screen.findAllByRole('img', { name: 'shared-image.png' }))).toHaveLength(2)
    expect(screen.getByText('Generated image response.')).toBeInTheDocument()
    expect(screen.queryByText('FLATTENED IMAGE OUTPUT MUST NOT RENDER')).not.toBeInTheDocument()

    const urls = fetchMock.mock.calls.map(([input]) => String(input))
    expect(urls.filter((url) => url.includes('/agent-calls/'))).toHaveLength(1)
    expect(urls.filter((url) => url.includes(`/files/${fileId}/content`))).toHaveLength(1)
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
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    })

    render(<RoomPageShell adapter={makeAdapter()} />)

    expect(screen.getByRole('button', { name: /open researcher alex response/i })).toBeInTheDocument()
    expect(screen.queryByTestId('conversation-resizable-workspace')).not.toBeInTheDocument()
    expect(screen.queryByTestId('agent-response-detail-pane')).not.toBeInTheDocument()
  })
})
