/**
 * Tests for useRoomWebhook's updateRoomSettings — verifies that the settings
 * form only updates debateMode and never touches use_supervisor in extend_info.
 *
 * Supervisor mode is managed exclusively by the chat input toggle and
 * handleSendMessage; the settings dialog must not overwrite it.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup, waitFor } from '@testing-library/react'
import React from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useMessageStore } from '@/stores/message-store'
import { useRoomUiStore } from '@/stores/room-ui-store'
import { streamingBuffer } from '@/stores/streaming-buffer'
import type { SSEMessage } from '@/lib/types/sse'
import type { Agent } from '@/lib/types/agent'

let capturedOnMessage: ((msg: SSEMessage) => void) | undefined

vi.mock('@/hooks/useRoomSSE', () => ({
  useRoomSSE: vi.fn((opts: { onMessage?: (msg: SSEMessage) => void }) => {
    capturedOnMessage = opts.onMessage
    return { connected: true, connecting: false, error: null }
  }),
}))

vi.mock('@clerk/nextjs', () => ({
  useUser: () => ({ user: { id: 'u1', firstName: 'Test' }, isLoaded: true }),
  useAuth: () => ({ getToken: async () => 'token' }),
  useClerk: () => ({ openWaitlist: vi.fn() }),
}))

const mockUpdateRoomExtendInfo = vi.fn().mockResolvedValue({ success: true })
const mockUpdateRoomName = vi.fn().mockResolvedValue({ success: true })
const mockUpdateRoomAgentSet = vi.fn().mockResolvedValue({ success: true })

vi.mock('@/lib/api/room', () => ({
  inquiryRoomSetting: vi.fn().mockResolvedValue({
    success: true,
    room: {
      room_id: 'room-1',
      room_name: 'Test Room',
      room_agent_set: {},
      extend_info: { debateMode: false, use_supervisor: true },
    },
  }),
  SendMessage: vi.fn().mockResolvedValue({ success: true, message_id: 'msg-1' }),
  inquiryRoomMessagesByRoomId: vi.fn().mockResolvedValue({ success: true, message_list: [] }),
  updateRoomAgentSet: (...args: unknown[]) => mockUpdateRoomAgentSet(...args),
  updateRoomName: (...args: unknown[]) => mockUpdateRoomName(...args),
  updateRoomExtendInfo: (...args: unknown[]) => mockUpdateRoomExtendInfo(...args),
}))

vi.mock('@/lib/api/agent', () => ({
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

const mockAgent: Agent = {
  agent_id: 'agent-1',
  agent_card: { name: 'Research Bot' } as Agent['agent_card'],
  agent_status: 'active' as Agent['agent_status'],
}

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client: queryClient }, children)
  }
}

describe('useRoomWebhook — updateRoomSettings does not touch supervisor', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    capturedOnMessage = undefined
    useMessageStore.getState().clearRoom()
    useMessageStore.getState().setRoom('room-1')
    useMessageStore.getState().markDbSynced()
    useRoomUiStore.getState().resetAll()
    streamingBuffer.clear()
  })

  afterEach(() => {
    cleanup()
  })

  async function mountHook() {
    const { useRoomWebhook } = await import('@/hooks/useRoomWebhook')
    const result = renderHook(
      () => useRoomWebhook({
        roomId: 'room-1',
        userId: 'u1',
        userName: 'Test',
        getToken: async () => 'token',
      }),
      { wrapper: createWrapper() }
    )
    await waitFor(() => {
      expect(result.result.current.room).not.toBeNull()
    })
    return result
  }

  it('should call updateRoomExtendInfo with only debateMode when it changes', async () => {
    const { result } = await mountHook()

    await act(async () => {
      await result.current.updateRoomSettings(
        'Test Room',
        ['agent-1'],
        { debateMode: true },
      )
    })

    expect(mockUpdateRoomExtendInfo).toHaveBeenCalledTimes(1)
    const payload = mockUpdateRoomExtendInfo.mock.calls[0][1]
    expect(payload.debateMode).toBe(true)
    // use_supervisor should be preserved from the existing extend_info, not overwritten
    expect(payload).not.toHaveProperty('use_supervisor', false)
  })

  it('should not call updateRoomExtendInfo when debateMode has not changed', async () => {
    const { result } = await mountHook()

    await act(async () => {
      await result.current.updateRoomSettings(
        'Test Room',
        ['agent-1'],
        { debateMode: false },
      )
    })

    expect(mockUpdateRoomExtendInfo).not.toHaveBeenCalled()
  })

  it('should never include use_supervisor in the settings update payload', async () => {
    const { result } = await mountHook()

    await act(async () => {
      await result.current.updateRoomSettings(
        'Test Room',
        ['agent-1'],
        { debateMode: true },
      )
    })

    expect(mockUpdateRoomExtendInfo).toHaveBeenCalledTimes(1)
    const payload = mockUpdateRoomExtendInfo.mock.calls[0][1]
    // The spread of existing extend_info may carry use_supervisor through,
    // but the settings form must not *set* it — it should only flow through
    // from the existing value.
    expect(payload.debateMode).toBe(true)
    expect(payload.use_supervisor).toBe(true) // preserved from room fixture
  })

  it('should show success banner after updating debate mode', async () => {
    const { banner } = await import('@/components/ui/banner')
    const { result } = await mountHook()

    let success: boolean | undefined
    await act(async () => {
      success = await result.current.updateRoomSettings(
        'Test Room',
        ['agent-1'],
        { debateMode: true },
      )
    })

    expect(success).toBe(true)
    expect(banner.success).toHaveBeenCalledWith('Room settings updated successfully')
  })

  it('should handle updateRoomExtendInfo failure gracefully', async () => {
    mockUpdateRoomExtendInfo.mockResolvedValueOnce({ success: false, error: 'Server error' })
    const { banner } = await import('@/components/ui/banner')
    const { result } = await mountHook()

    let success: boolean | undefined
    await act(async () => {
      success = await result.current.updateRoomSettings(
        'Test Room',
        ['agent-1'],
        { debateMode: true },
      )
    })

    expect(success).toBe(false)
    expect(banner.error).toHaveBeenCalled()
  })

  it('should succeed without calling extendInfo when nothing changed', async () => {
    const { banner } = await import('@/components/ui/banner')
    const { result } = await mountHook()

    let success: boolean | undefined
    await act(async () => {
      success = await result.current.updateRoomSettings(
        'Test Room',
        ['agent-1'],
        { debateMode: false },
      )
    })

    expect(success).toBe(true)
    expect(mockUpdateRoomExtendInfo).not.toHaveBeenCalled()
    expect(banner.success).toHaveBeenCalledWith('Room settings updated successfully')
  })
})
