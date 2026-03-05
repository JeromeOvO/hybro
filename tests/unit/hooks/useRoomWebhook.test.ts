/**
 * Tests for useRoomWebhook's SSE message handling logic.
 *
 * Strategy: mock useRoomSSE to capture the onMessage callback, then invoke it
 * directly with various SSE message shapes and verify the resulting writes to
 * useMessageStore (Zustand). React Query is mocked to avoid network calls.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup, waitFor } from '@testing-library/react'
import React from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useMessageStore } from '@/stores/message-store'
import { useRoomUiStore } from '@/stores/room-ui-store'
import { streamingBuffer } from '@/stores/streaming-buffer'
import type { SSEMessage } from '@/lib/types/sse'

// Capture the onMessage callback passed to useRoomSSE
let capturedOnMessage: ((msg: SSEMessage) => void) | undefined

vi.mock('@/hooks/useRoomSSE', () => ({
  useRoomSSE: vi.fn((opts: { onMessage?: (msg: SSEMessage) => void }) => {
    capturedOnMessage = opts.onMessage
    return { connected: true, connecting: false, error: null }
  }),
}))

// Mock Clerk auth
vi.mock('@clerk/nextjs', () => ({
  useUser: () => ({ user: { id: 'u1', firstName: 'Test' }, isLoaded: true }),
  useAuth: () => ({ getToken: async () => 'token' }),
  useClerk: () => ({ openWaitlist: vi.fn() }),
}))

// Mock room API
vi.mock('@/lib/api/room', () => ({
  inquiryRoomSetting: vi.fn().mockResolvedValue({ success: true, room: { room_id: 'room-1', room_name: 'Test', room_agent_set: {} } }),
  SendMessage: vi.fn().mockResolvedValue({ success: true, message_id: 'msg-1' }),
  inquiryRoomMessagesByRoomId: vi.fn().mockResolvedValue({ success: true, message_list: [] }),
  updateRoomAgentSet: vi.fn().mockResolvedValue({ success: true }),
  updateRoomName: vi.fn().mockResolvedValue({ success: true }),
  updateRoomExtendInfo: vi.fn().mockResolvedValue({ success: true }),
}))

vi.mock('@/lib/api/agent', () => ({
  getAllActiveAgents: vi.fn().mockResolvedValue({ success: true, agents: [] }),
}))

vi.mock('@/lib/api/sse', () => ({
  cancelMessage: vi.fn().mockResolvedValue({ success: true }),
  SSEConnection: vi.fn(),
}))

vi.mock('@/components/ui/banner', () => ({
  banner: { info: vi.fn(), error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}))

function makeSSEMessage(overrides: Partial<SSEMessage>): SSEMessage {
  return {
    type: 'heartbeat',
    room_id: 'room-1',
    timestamp: new Date().toISOString(),
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

describe('useRoomWebhook SSE message handling', () => {
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
    return renderHook(
      () => useRoomWebhook({
        roomId: 'room-1',
        userId: 'u1',
        userName: 'Test',
        getToken: async () => 'token',
      }),
      { wrapper: createWrapper() }
    )
  }

  it('should capture onMessage from useRoomSSE', async () => {
    await mountHook()
    expect(capturedOnMessage).toBeDefined()
  })

  it('should handle user_message by writing to message store', async () => {
    await mountHook()
    expect(capturedOnMessage).toBeDefined()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'user_message',
        data: {
          message_id: 'msg-u1',
          content: 'Hello from SSE',
          user_id: 'user-42',
        },
      }))
    })

    const entity = useMessageStore.getState().entities['msg-u1']
    expect(entity).toBeDefined()
    expect(entity.content).toBe('Hello from SSE')
    expect(entity.messageType).toBe('user')
  })

  it('should handle agent_response by finalizing streaming and writing to store', async () => {
    await mountHook()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'agent_response',
        data: {
          message_id: 'msg-a1',
          content: 'Agent reply',
          agent_id: 'agent-1',
        },
      }))
    })

    const entity = useMessageStore.getState().entities['msg-a1']
    expect(entity).toBeDefined()
    expect(entity.content).toBe('Agent reply')
    expect(entity.messageType).toBe('agent')
    expect(entity.isEphemeral).toBe(false)
  })

  it('should handle agent_token by appending to streaming buffer', async () => {
    await mountHook()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'agent_token',
        data: {
          message_id: 'msg-tok-1',
          agent_id: 'agent-1',
          token: 'Hello',
        },
      }))
    })

    // Streaming buffer should have content
    const snapshot = streamingBuffer.get('msg-tok-1')
    expect(snapshot).toBe('Hello')

    // Should have created a placeholder entity
    const entity = useMessageStore.getState().entities['msg-tok-1']
    expect(entity).toBeDefined()
    expect(entity.isEphemeral).toBe(true)
  })

  it('should ignore agent_token without message_id or token', async () => {
    await mountHook()
    const countBefore = useMessageStore.getState().orderedIds.length

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'agent_token',
        data: { message_id: '', token: '' },
      }))
    })

    expect(useMessageStore.getState().orderedIds.length).toBe(countBefore)
  })

  it('should handle heartbeat without side effects', async () => {
    await mountHook()
    const countBefore = useMessageStore.getState().orderedIds.length

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({ type: 'heartbeat' }))
    })

    expect(useMessageStore.getState().orderedIds.length).toBe(countBefore)
  })

  it('should handle processing_status "processing" by setting processing flag', async () => {
    await mountHook()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: { status: 'processing', message_id: 'msg-1' },
      }))
    })

    expect(useRoomUiStore.getState().processing).toBe(true)
  })

  it('should handle processing_status "completed" by clearing processing flag', async () => {
    useRoomUiStore.getState().setProcessing(true)

    await mountHook()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: { status: 'completed' },
      }))
    })

    expect(useRoomUiStore.getState().processing).toBe(false)
    expect(useRoomUiStore.getState().cancelling).toBe(false)
  })

  it('should handle task_submitted by writing task entity to store', async () => {
    await mountHook()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'task_submitted',
        data: {
          message_id: 'task-1',
          task_id: 't-1',
          agent_name: 'Code Agent',
          agent_id: 'agent-1',
          status: 'working',
          task_content: 'Analyzing code...',
          step_number: 1,
          total_steps: 3,
        },
      }))
    })

    const entity = useMessageStore.getState().entities['task-1']
    expect(entity).toBeDefined()
    expect(entity.senderName).toBe('Code Agent')
    expect(entity.taskStatus).toBe('working')
    expect(entity.taskContent).toBe('Analyzing code...')
    expect(entity.stepNumber).toBe(1)
    expect(entity.totalSteps).toBe(3)
  })

  it('should handle task_update with terminal state', async () => {
    await mountHook()

    // First submit a task
    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'task_submitted',
        data: { message_id: 'task-2', agent_name: 'Agent', status: 'working' },
      }))
    })

    // Then complete it — triggers typewriter which finalizes asynchronously
    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'task_update',
        data: {
          message_id: 'task-2',
          status: 'completed',
          content: 'Done! Here is the result.',
          agent_name: 'Agent',
        },
      }))
    })

    // Typewriter callback fires after setInterval ticks; wait for it to land
    await waitFor(() => {
      const entity = useMessageStore.getState().entities['task-2']
      expect(entity.taskStatus).toBe('completed')
      expect(entity.content).toBe('Done! Here is the result.')
      expect(entity.isEphemeral).toBe(false)
    })
  })

  it('should render completed task with parts but no content as agent-bubble', async () => {
    await mountHook()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'task_update',
        data: {
          message_id: 'task-parts-only',
          status: 'completed',
          content: '',
          agent_name: 'Agent',
          parts: [
            { kind: 'file', file: { uri: 'https://s3/image.png', mime_type: 'image/png', name: 'result.png' } },
          ],
        },
      }))
    })

    const entity = useMessageStore.getState().entities['task-parts-only']
    expect(entity).toBeTruthy()
    expect(entity.taskStatus).toBe('completed')
    expect(entity.displayType).toBe('agent-bubble')
    expect(entity.artifacts).toBeDefined()
    expect(entity.artifacts!.length).toBeGreaterThan(0)
  })

  it('should handle error SSE message', async () => {
    const { banner } = await import('@/components/ui/banner')
    await mountHook()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'error',
        data: { error: 'Something went wrong' },
      }))
    })

    expect(banner.error).toHaveBeenCalledWith('Something went wrong')
  })

  it('should handle rate_limit_exceeded error with retry info', async () => {
    const { banner } = await import('@/components/ui/banner')
    await mountHook()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'error',
        data: {
          error_type: 'rate_limit_exceeded',
          error: 'Rate limit exceeded',
          retry_after_seconds: 120,
        },
      }))
    })

    expect(banner.error).toHaveBeenCalledWith(
      'Rate limit exceeded',
      { duration: 15000 }
    )
  })
})
