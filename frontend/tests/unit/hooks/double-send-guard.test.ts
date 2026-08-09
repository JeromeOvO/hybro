/**
 * Tests for the isProcessingRef double-send guard in useRoomWebhook.
 *
 * Verifies that:
 * 1. A second sendUserMessage call is rejected while processing
 * 2. The ref guard is cleared when a terminal SSE processing_status event arrives
 * 3. After clearing, sending is allowed again
 *
 * NOTE: task_update terminal events do NOT clear processing — only
 * processing_status does (see sse-handlers/index.ts comment block).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor, cleanup } from '@testing-library/react'
import React from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useMessageStore } from '@/stores/message-store'
import { useRoomUiStore } from '@/stores/room-ui-store'
import type { AnySSEFrame } from '@/lib/types/sse'

let capturedOnMessage: ((msg: AnySSEFrame) => void) | undefined

vi.mock('@/hooks/useRoomSSE', () => ({
  useRoomSSE: vi.fn((opts: { onMessage?: (msg: AnySSEFrame) => void }) => {
    capturedOnMessage = opts.onMessage
    return { connected: true, connecting: false, error: null }
  }),
}))

vi.mock('@clerk/nextjs', () => ({
  useUser: () => ({ user: { id: 'u1', firstName: 'Test' }, isLoaded: true }),
  useAuth: () => ({ getToken: async () => 'token' }),
  useClerk: () => ({ openWaitlist: vi.fn() }),
}))

const mockSendMessage = vi.fn().mockResolvedValue({ success: true, message_id: 'msg-1' })

vi.mock('@/lib/api/room', () => ({
  inquiryRoomSetting: vi.fn().mockResolvedValue({
    success: true,
    room: { room_id: 'room-1', room_name: 'Test', room_agent_set: {} },
  }),
  SendMessage: (...args: unknown[]) => mockSendMessage(...args),
  inquiryRoomMessagesByRoomId: vi.fn().mockResolvedValue({ success: true, message_list: [] }),
  updateRoomAgentSet: vi.fn().mockResolvedValue({ success: true }),
  updateRoomName: vi.fn().mockResolvedValue({ success: true }),
  updateRoomExtendInfo: vi.fn().mockResolvedValue({ success: true }),
}))

vi.mock('@/lib/api/agent', () => ({
  getAllAgents: vi.fn().mockResolvedValue({ success: true, agents: [] }),
  getAllActiveAgents: vi.fn().mockResolvedValue({ success: true, agents: [] }),
}))

vi.mock('@/lib/api/sse', () => ({
  cancelMessage: vi.fn().mockResolvedValue({ success: true }),
  SSEConnection: vi.fn(),
}))

vi.mock('@/lib/api/hitl', () => ({
  respondToHitl: vi.fn().mockResolvedValue({ status: 'ok', request_id: 'req-1' }),
  fetchPendingHitlRequests: vi.fn().mockResolvedValue({ requests: [] }),
}))

vi.mock('@/components/ui/banner', () => ({
  banner: { info: vi.fn(), error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}))

function makeSSEMessage(overrides: Partial<AnySSEFrame>): AnySSEFrame {
  return {
    type: 'heartbeat',
    room_id: 'room-1',
    timestamp: new Date().toISOString(),
    data: {},
    ...overrides,
  }
}

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client: queryClient }, children)
  }
}

const flags = (roomId = 'room-1') => useRoomUiStore.getState().getRoomFlags(roomId)
const latestClientRequestId = (roomId = 'room-1') =>
  useMessageStore
    .getState()
    .orderedIds
    .map((id) => useMessageStore.getState().entities[id])
    .filter((m) => m?.roomId === roomId && m?.messageType === 'user')
    .at(-1)?.clientRequestId

describe('useRoomWebhook double-send guard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    capturedOnMessage = undefined
    mockSendMessage.mockResolvedValue({ success: true, message_id: 'msg-1' })
    useMessageStore.getState().clearRoom()
    useMessageStore.getState().setRoom('room-1')
    useMessageStore.getState().markDbSynced()
    useRoomUiStore.getState().resetAll()
  })

  afterEach(() => {
    cleanup()
  })

  async function mountAndWaitForRoom() {
    const { useRoomWebhook } = await import('@/hooks/useRoomWebhook')
    const hook = renderHook(
      () => useRoomWebhook({
        roomId: 'room-1',
        userId: 'u1',
        userName: 'Test',
        getToken: async () => 'token',
      }),
      { wrapper: createWrapper() }
    )
    await waitFor(() => {
      expect(hook.result.current.room).toBeTruthy()
    })
    return hook
  }

  it('rejects a second sendUserMessage while the first is still processing', async () => {
    const { result } = await mountAndWaitForRoom()

    let firstResult: boolean | undefined
    await act(async () => {
      firstResult = await result.current.sendUserMessage({ userInput: 'Hello', mode: 'direct', agentScope: { source: 'room_default' } })
    })
    expect(firstResult).toBe(true)
    expect(mockSendMessage).toHaveBeenCalledTimes(1)
    expect(flags().processing).toBe(true)

    let secondResult: boolean | undefined
    await act(async () => {
      secondResult = await result.current.sendUserMessage({ userInput: 'Second message', mode: 'direct', agentScope: { source: 'room_default' } })
    })
    expect(secondResult).toBe(false)
    expect(mockSendMessage).toHaveBeenCalledTimes(1)
  })

  it('allows sending again after a terminal processing_status SSE clears the guard', async () => {
    const { result } = await mountAndWaitForRoom()
    expect(capturedOnMessage).toBeDefined()

    await act(async () => {
      await result.current.sendUserMessage({ userInput: 'Hello', mode: 'direct', agentScope: { source: 'room_default' } })
    })
    expect(flags().processing).toBe(true)
    const clientRequestId = latestClientRequestId()
    expect(clientRequestId).toBeTruthy()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: { status: 'completed', message_id: 'msg-1', client_request_id: clientRequestId, details: null },
      }))
    })
    expect(flags().processing).toBe(false)

    mockSendMessage.mockResolvedValue({ success: true, message_id: 'msg-2' })

    let secondResult: boolean | undefined
    await act(async () => {
      secondResult = await result.current.sendUserMessage({ userInput: 'Follow up', mode: 'direct', agentScope: { source: 'room_default' } })
    })
    expect(secondResult).toBe(true)
    expect(mockSendMessage).toHaveBeenCalledTimes(2)
  })

  it('does NOT clear the guard on terminal task_update (only processing_status clears it)', async () => {
    const { result } = await mountAndWaitForRoom()
    expect(capturedOnMessage).toBeDefined()

    await act(async () => {
      await result.current.sendUserMessage({ userInput: 'Hello', mode: 'direct', agentScope: { source: 'room_default' } })
    })
    expect(flags().processing).toBe(true)
    const clientRequestId = latestClientRequestId()
    expect(clientRequestId).toBeTruthy()

    // task_update with terminal status does NOT clear processing —
    // it means one agent finished, but room-level processing continues
    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'task_update',
        data: {
          message_id: 'task-msg-1',
          client_request_id: clientRequestId,
          status: 'completed',
          content: 'Done!',
          agent_name: 'Agent',
          agent_id: 'a1',
        },
      }))
    })
    expect(flags().processing).toBe(true)

    // The authoritative signal is processing_status — that clears it
    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: { status: 'completed', message_id: 'msg-1', client_request_id: clientRequestId, details: null },
      }))
    })
    expect(flags().processing).toBe(false)

    mockSendMessage.mockResolvedValue({ success: true, message_id: 'msg-2' })

    let sendResult: boolean | undefined
    await act(async () => {
      sendResult = await result.current.sendUserMessage({ userInput: 'Another message', mode: 'direct', agentScope: { source: 'room_default' } })
    })
    expect(sendResult).toBe(true)
  })
})
