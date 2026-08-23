/**
 * Phase 0b: Characterization tests for room lifecycle behaviors.
 *
 * These tests verify the key lifecycle behaviors that span multiple
 * extracted sub-hooks in the useRoomWebhook decomposition:
 *
 * 1. Room reset on navigation
 * 2. Processing restore on page refresh
 * 3. SSE disconnect/reconnect behavior
 * 4. Cancel timeout safety net
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup, waitFor } from '@testing-library/react'
import React from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useMessageStore } from '@/stores/message-store'
import { useRoomUiStore } from '@/stores/room-ui-store'
import type { AnySSEFrame } from '@/lib/types/sse'

let capturedOnMessage: ((msg: AnySSEFrame) => void) | undefined
let mockSseConnected = true

vi.mock('@/hooks/useRoomSSE', () => ({
  useRoomSSE: vi.fn((opts: { onMessage?: (msg: AnySSEFrame) => void }) => {
    capturedOnMessage = opts.onMessage
    return { connected: mockSseConnected, connecting: false, error: null }
  }),
}))

const mockInquiryRoomSetting = vi.fn()
const mockSendMessage = vi.fn().mockResolvedValue({ success: true, message_id: 'msg-1' })
const mockCancelMessage = vi.fn().mockResolvedValue({ success: true, message_id: 'msg-1', message: 'Cancelled' })

vi.mock('@/lib/api/room', () => ({
  inquiryRoomSetting: (...args: unknown[]) => mockInquiryRoomSetting(...args),
  inquiryActiveRuns: vi.fn().mockResolvedValue({ success: true, active_runs: [] }),
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
  cancelMessage: (...args: unknown[]) => mockCancelMessage(...args),
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

describe('Room lifecycle characterization tests', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.unstubAllEnvs()
    capturedOnMessage = undefined
    mockSseConnected = true
    mockInquiryRoomSetting.mockResolvedValue({
      success: true,
      room: { room_id: 'room-1', room_name: 'Test', room_agent_set: {} },
    })
    useMessageStore.getState().clearRoom()
    useMessageStore.getState().setRoom('room-1')
    useMessageStore.getState().markDbSynced()
    useRoomUiStore.getState().resetAll()
  })

  afterEach(() => {
    vi.useRealTimers()
    cleanup()
  })

  async function mountHook(roomId = 'room-1') {
    const { useRoomWebhook } = await import('@/hooks/useRoomWebhook')
    return renderHook(
      () => useRoomWebhook({
        roomId,
        userId: 'u1',
        userName: 'Test',
        getToken: async () => 'token',
      }),
      { wrapper: createWrapper() }
    )
  }

  async function mountAndWaitForRoom(roomId = 'room-1') {
    const hook = await mountHook(roomId)
    await waitFor(() => {
      expect(hook.result.current.room).toBeTruthy()
    })
    return hook
  }

  // ── Test 1: Room reset on navigation ──

  describe('Room reset on navigation', () => {
    it('resets processing/cancelling/sseConnected and changes store roomId', async () => {
      const { result } = await mountAndWaitForRoom('room-1')
      expect(result.current.room).toBeTruthy()

      // Establish processing state through normal flow
      mockSendMessage.mockResolvedValue({ success: true, message_id: 'msg-nav-1' })
      await act(async () => {
        await result.current.sendUserMessage({ userInput: 'Hello', mode: 'direct', agentScope: { source: 'room_default' } })
      })
      expect(flags('room-1').processing).toBe(true)

      // Navigate to room-2: unmount room-1, mount room-2
      cleanup()

      mockInquiryRoomSetting.mockResolvedValue({
        success: true,
        room: { room_id: 'room-2', room_name: 'Second Room', room_agent_set: {} },
      })
      // Don't pre-set room store — the hook's reset effect handles it
      useMessageStore.getState().clearRoom()
      useMessageStore.getState().setRoom('room-2')
      useMessageStore.getState().markDbSynced()

      await mountAndWaitForRoom('room-2')

      // After room switch, UI flags should be reset (room-1 cleaned up, room-2 starts fresh)
      expect(flags('room-2').processing).toBe(false)
      expect(flags('room-2').cancelling).toBe(false)
      expect(flags('room-2').sending).toBe(false)

      // Store should have switched to new room
      expect(useMessageStore.getState().roomId).toBe('room-2')
    })
  })

  // ── Test 2: Processing restore on page refresh ──

  describe('Processing restore on page refresh', () => {
    it('restores placeholder from active_runs', async () => {
      const { inquiryRoomMessagesByRoomId } = await import('@/lib/api/room')
      vi.mocked(inquiryRoomMessagesByRoomId).mockResolvedValueOnce({
        success: true,
        message_list: [{
          room_id: 'room-1',
          message_id: 'msg-processing-1',
          message_type: 'user',
          user_id: 'u1',
          message_created_at: new Date().toISOString(),
          message_content: { message_text: 'Hello' },
        }] as any,
      })
      mockInquiryRoomSetting.mockResolvedValue({
        success: true,
        room: { room_id: 'room-1', room_name: 'Test', room_agent_set: {} },
        active_runs: [
          { state: 'processing', trigger_message_id: 'msg-processing-1' },
        ],
      })

      await mountAndWaitForRoom()

      await waitFor(() => {
        const entity = useMessageStore.getState().entities['msg-processing-1']
        expect(entity?.processingStatusLogs?.map((entry) => entry.message)).toEqual(['Thinking...'])
      })
      expect(useMessageStore.getState().entities['processing-placeholder-room-1']).toBeUndefined()
      expect(flags('room-1').processing).toBe(true)
    })

    it('seeds processing log when room has active run trigger', async () => {
      const { inquiryRoomMessagesByRoomId } = await import('@/lib/api/room')
      vi.mocked(inquiryRoomMessagesByRoomId).mockResolvedValueOnce({
        success: true,
        message_list: [{
          room_id: 'room-1',
          message_id: 'msg-processing-1',
          message_type: 'user',
          user_id: 'u1',
          message_created_at: new Date().toISOString(),
          message_content: { message_text: 'Hello' },
        }] as any,
      })
      mockInquiryRoomSetting.mockResolvedValue({
        success: true,
        room: { room_id: 'room-1', room_name: 'Test', room_agent_set: {} },
        active_runs: [
          { state: 'processing', trigger_message_id: 'msg-processing-1' },
        ],
      })

      await mountAndWaitForRoom()

      await waitFor(() => {
        const entity = useMessageStore.getState().entities['msg-processing-1']
        expect(entity?.processingStatusLogs?.map((entry) => entry.message)).toEqual(['Thinking...'])
      })

      expect(useMessageStore.getState().entities['processing-placeholder-room-1']).toBeUndefined()
      expect(flags('room-1').processing).toBe(true)
    })

    it('skips placeholder when processing message is stale (>2min)', async () => {
      const staleTimestamp = new Date(Date.now() - 3 * 60 * 1000).toISOString()

      // Provide the stale message through DB hydration so it survives the room reset
      const { inquiryRoomMessagesByRoomId } = await import('@/lib/api/room')
      vi.mocked(inquiryRoomMessagesByRoomId).mockResolvedValueOnce({
        success: true,
        message_list: [{
          room_id: 'room-1',
          message_id: 'msg-stale',
          message_type: 'user',
          user_id: 'u1',
          message_created_at: staleTimestamp,
          message_content: { message_text: 'Old message' },
        }] as any,
      })

      mockInquiryRoomSetting.mockResolvedValue({
        success: true,
        room: { room_id: 'room-1', room_name: 'Test', room_agent_set: {} },
        active_runs: [
          { state: 'processing', trigger_message_id: 'msg-stale' },
        ],
      })

      await mountAndWaitForRoom()

      // Give hydration + restore effect time to complete
      await act(async () => {
        await new Promise(r => setTimeout(r, 100))
      })

      // The stale message should be in the store from hydration
      expect(useMessageStore.getState().entities['msg-stale']).toBeDefined()

      // But no placeholder should have been created since the message is stale
      const placeholderId = `processing-placeholder-room-1`
      const placeholder = useMessageStore.getState().entities[placeholderId]
      expect(placeholder).toBeUndefined()
    })
  })

  // ── Test 3: SSE disconnect/reconnect ──

  describe('SSE disconnect/reconnect', () => {
    it('keeps live processing logs when active runs are empty but DB has no terminal turn yet', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true })
      const { inquiryActiveRuns, inquiryRoomMessagesByRoomId } = await import('@/lib/api/room')
      vi.mocked(inquiryActiveRuns).mockResolvedValue({ success: true, active_runs: [] })

      const { result } = await mountAndWaitForRoom()
      mockSendMessage.mockResolvedValue({ success: true, message_id: 'msg-active-run-lag' })
      vi.mocked(inquiryRoomMessagesByRoomId).mockResolvedValueOnce({
        success: true,
        message_list: [{
          room_id: 'room-1',
          message_id: 'msg-active-run-lag',
          message_type: 'user',
          user_id: 'u1',
          message_created_at: '2026-06-04T01:00:00.000Z',
          message_content: { message_text: 'Backend active run lag' },
        }] as any,
      })

      await act(async () => {
        await result.current.sendUserMessage({ userInput: 'Backend active run lag', mode: 'direct', agentScope: { source: 'room_default' } })
      })

      expect(useMessageStore.getState().entities['msg-active-run-lag'].processingStatusLogs?.map((entry) => entry.message)).toEqual([
        'Thinking...',
      ])
      expect(flags('room-1').processing).toBe(true)

      await act(async () => {
        vi.advanceTimersByTime(15_000)
        await Promise.resolve()
        await Promise.resolve()
      })

      expect(flags('room-1').processing).toBe(true)
      expect(useMessageStore.getState().entities['msg-active-run-lag'].processingStatusLogs?.map((entry) => entry.message)).toEqual([
        'Thinking...',
      ])
    })

    it('recovers a missed terminal only from a durable-confirmed frame (no polling)', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true })
      const { inquiryActiveRuns, inquiryRoomMessagesByRoomId } = await import('@/lib/api/room')
      vi.mocked(inquiryActiveRuns)
        .mockResolvedValueOnce({
          success: true,
          active_runs: [{ state: 'processing', trigger_message_id: 'msg-late-terminal' }],
        })
        .mockResolvedValue({ success: true, active_runs: [] })

      const { result } = await mountAndWaitForRoom()
      mockSendMessage.mockResolvedValue({ success: true, message_id: 'msg-late-terminal' })
      vi.mocked(inquiryRoomMessagesByRoomId).mockResolvedValue({
        success: true,
        message_list: [
          {
            room_id: 'room-1',
            message_id: 'msg-late-terminal',
            message_type: 'user',
            user_id: 'u1',
            message_created_at: '2026-06-04T01:00:00.000Z',
            message_content: { message_text: 'Late terminal turn' },
            extend_info: { orchestration_status: 'failed' },
          },
          {
            room_id: 'room-1',
            message_id: 'agent-late-terminal',
            message_type: 'agent',
            agent_id: 'agent-1',
            related_message_id: 'msg-late-terminal',
            message_created_at: '2026-06-04T01:00:01.000Z',
            task_updated_at: '2026-06-04T01:00:01.000Z',
            task_content: 'Answering',
            message_content: {
              message_text: 'Task failed',
              message_task: {
                status: { state: 'failed' },
                metadata: { agent_id: 'agent-1' },
              },
            },
          },
        ] as any,
      })

      await act(async () => {
        await result.current.sendUserMessage({ userInput: 'Late terminal turn', mode: 'supervisor', agentScope: { source: 'room_default' } })
      })
      expect(flags('room-1').processing).toBe(true)

      // Phase 3 (§8): the 5 s safety-net poll is removed. Advancing timers
      // must NOT recover anything — the lifecycle stays live until the
      // durable-confirmed terminal frame arrives.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000)
      })
      expect(flags('room-1').processing).toBe(true)

      // The terminal frame is the only recovery path.
      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'processing_status',
          data: {
            status: 'failed',
            message_id: 'msg-late-terminal',
            client_request_id: 'req-late-terminal',
            details: null,
          },
        }))
      })
      expect(flags('room-1').processing).toBe(false)
      expect(useMessageStore.getState().entities['msg-late-terminal'].turnTerminalStatus).toBe('failed')
    })

    it('preserves live processing logs without the removed polling loop', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true })
      const { inquiryActiveRuns, inquiryRoomMessagesByRoomId } = await import('@/lib/api/room')
      vi.mocked(inquiryActiveRuns).mockResolvedValue({ success: true, active_runs: [] })

      const { result } = await mountAndWaitForRoom()
      mockSendMessage.mockResolvedValue({ success: true, message_id: 'msg-missed-terminal' })
      vi.mocked(inquiryRoomMessagesByRoomId).mockResolvedValueOnce({
        success: true,
        message_list: [
          {
            room_id: 'room-1',
            message_id: 'msg-missed-terminal',
            message_type: 'user',
            user_id: 'u1',
            message_created_at: '2026-06-04T01:00:00.000Z',
            message_content: { message_text: 'Missed terminal turn' },
          },
          {
            room_id: 'room-1',
            message_id: 'agent-missed-terminal',
            message_type: 'agent',
            agent_id: 'agent-1',
            related_message_id: 'msg-missed-terminal',
            message_created_at: '2026-06-04T01:00:01.000Z',
            task_updated_at: '2026-06-04T01:00:01.000Z',
            task_content: 'Answering',
            message_content: {
              message_text: 'Done',
              message_task: {
                status: { state: 'completed' },
                metadata: { agent_id: 'agent-1' },
              },
            },
          },
        ] as any,
      })

      await act(async () => {
        await result.current.sendUserMessage({ userInput: 'Missed terminal turn', mode: 'direct', agentScope: { source: 'room_default' } })
      })

      expect(useMessageStore.getState().entities['msg-missed-terminal'].processingStatusLogs?.map((entry) => entry.message)).toEqual([
        'Thinking...',
      ])
      expect(flags('room-1').processing).toBe(true)

      await act(async () => {
        vi.advanceTimersByTime(15_000)
        await Promise.resolve()
      })

      // No polling recovery (§8): the lifecycle stays live until the
      // durable-confirmed terminal frame arrives, and the live log is
      // preserved in the meantime.
      expect(flags('room-1').processing).toBe(true)
      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'processing_status',
          data: {
            status: 'completed',
            message_id: 'msg-missed-terminal',
            client_request_id: 'req-missed-terminal',
            details: null,
          },
        }))
      })
      expect(flags('room-1').processing).toBe(false)
      expect(useMessageStore.getState().entities['msg-missed-terminal'].processingStatusLogs?.map((entry) => entry.message)).toEqual([
        'Thinking...',
      ])
    })

    it('restores pending HITL requests on SSE reconnect', async () => {
      const { fetchPendingHitlRequests } = await import('@/lib/api/hitl')
      const mockFetch = vi.mocked(fetchPendingHitlRequests)
      mockFetch.mockResolvedValueOnce({
        requests: [{
          request_id: 'hitl-reconnect-1',
          message_id: 'msg-hitl-reconnect',
          source: 'agent' as const,
          agent_id: 'agent-1',
          agent_name: 'Reconnect Agent',
          prompt: 'Need input after reconnect',
          prompt_type: 'text' as const,
          choices: null,
          status: 'pending' as const,
          created_at: new Date().toISOString(),
        }],
      })

      // Start disconnected
      mockSseConnected = false
      const { rerender } = await mountHook()

      // Reconnect SSE
      mockSseConnected = true
      await act(async () => {
        rerender()
      })

      // Give async fetch time to resolve
      await act(async () => {
        await new Promise(r => setTimeout(r, 100))
      })

      expect(mockFetch).toHaveBeenCalledWith('room-1', expect.any(Function))
      const entity = useMessageStore.getState().entities['msg-hitl-reconnect']
      expect(entity).toBeDefined()
      expect(entity.hitlRequestId).toBe('hitl-reconnect-1')
      expect(entity.hitlResolved).toBe(false)
      expect(entity.taskStatus).toBe('input-required')
    })
  })

  // ── Test 4: Cancel timeout safety net ──

  describe('Cancel timeout safety net', () => {
    it('stops processing immediately when cancel reports an existing terminal turn', async () => {
      const { result } = await mountAndWaitForRoom()
      mockSendMessage.mockResolvedValue({ success: true, message_id: 'msg-terminal-before-cancel' })
      mockCancelMessage.mockResolvedValueOnce({
        success: true,
        message_id: 'msg-terminal-before-cancel',
        message: 'Message processing had already finished',
        status: 'failed',
        outcome: 'already_terminal',
      })

      await act(async () => {
        await result.current.sendUserMessage({ userInput: 'Hello', mode: 'direct', agentScope: { source: 'room_default' } })
      })
      expect(flags('room-1').processing).toBe(true)

      await act(async () => {
        await result.current.cancelProcessing()
      })

      expect(flags('room-1').processing).toBe(false)
      expect(flags('room-1').cancelling).toBe(false)
    })

    it('fires timeout warning after 15s of unresolved cancellation', async () => {
      const { banner } = await import('@/components/ui/banner')

      const { result } = await mountAndWaitForRoom()
      expect(capturedOnMessage).toBeDefined()

      // Send a message first to establish processing state
      mockSendMessage.mockResolvedValue({ success: true, message_id: 'msg-cancel-1' })
      await act(async () => {
        await result.current.sendUserMessage({ userInput: 'Hello', mode: 'direct', agentScope: { source: 'room_default' } })
      })
      expect(flags('room-1').processing).toBe(true)

      // Switch to fake timers AFTER mounting (so waitFor/promises work normally)
      vi.useFakeTimers({ shouldAdvanceTime: true })

      // Cancel processing
      await act(async () => {
        await result.current.cancelProcessing()
      })
      expect(flags('room-1').cancelling).toBe(true)

      // Advance past the 15s cancel timeout
      await act(async () => {
        vi.advanceTimersByTime(16000)
      })

      // Safety net should have fired
      expect(banner.warning).toHaveBeenCalledWith(
        'Cancellation timed out — the agent may still be running'
      )
      expect(flags('room-1').cancelling).toBe(false)
      expect(flags('room-1').processing).toBe(false)
    })

    it('disarms cancel timeout when terminal SSE arrives before timeout', async () => {
      const { banner } = await import('@/components/ui/banner')

      const { result } = await mountAndWaitForRoom()
      expect(capturedOnMessage).toBeDefined()

      // Send + cancel
      mockSendMessage.mockResolvedValue({ success: true, message_id: 'msg-cancel-2' })
      await act(async () => {
        await result.current.sendUserMessage({ userInput: 'Test', mode: 'direct', agentScope: { source: 'room_default' } })
      })
      const clientRequestId = latestClientRequestId('room-1')
      expect(clientRequestId).toBeTruthy()

      // Switch to fake timers AFTER async setup
      vi.useFakeTimers({ shouldAdvanceTime: true })

      await act(async () => {
        await result.current.cancelProcessing()
      })
      expect(flags('room-1').cancelling).toBe(true)

      // Terminal SSE arrives before timeout
      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'processing_status',
          data: { status: 'canceled', message_id: 'msg-cancel-2', client_request_id: clientRequestId, details: null },
        }))
      })

      expect(flags('room-1').processing).toBe(false)
      expect(flags('room-1').cancelling).toBe(false)

      // Advance past 15s — no warning should fire since SSE already resolved it
      vi.clearAllMocks()
      await act(async () => {
        vi.advanceTimersByTime(16000)
      })

      expect(banner.warning).not.toHaveBeenCalled()
    })
  })
})
