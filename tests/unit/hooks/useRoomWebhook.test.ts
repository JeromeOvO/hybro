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
import { TASK_STATE, type SSEMessage } from '@/lib/types/sse'

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

const flags = (roomId = 'room-1') => useRoomUiStore.getState().getRoomFlags(roomId)

describe('useRoomWebhook SSE message handling', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    capturedOnMessage = undefined
    useMessageStore.getState().clearRoom()
    useMessageStore.getState().setRoom('room-1')
    useMessageStore.getState().markDbSynced()
    useRoomUiStore.getState().resetAll()
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

  it('should preserve clientRequestId on user_message when provided', async () => {
    await mountHook()
    expect(capturedOnMessage).toBeDefined()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'user_message',
        data: {
          message_id: 'msg-u2',
          content: 'Hello with correlation',
          user_id: 'user-99',
          client_request_id: 'req-user-msg-1',
        },
      }))
    })

    const entity = useMessageStore.getState().entities['msg-u2']
    expect(entity).toBeDefined()
    expect(entity.clientRequestId).toBe('req-user-msg-1')
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

  it('marks an existing working agent as completed when final agent_response repeats streamed content', async () => {
    await mountHook()

    useMessageStore.getState().upsertMessage({
      id: 'summary-task-1',
      roomId: 'room-1',
      messageType: 'agent',
      content: 'Summary text is already visible.',
      senderName: 'Summary Agent',
      timestamp: new Date().toISOString(),
      agentId: 'summary-agent',
      taskStatus: TASK_STATE.WORKING,
    }, 'sse')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'agent_response',
        data: {
          message_id: 'summary-task-1',
          content: 'Summary text is already visible.',
          agent_id: 'summary-agent',
        },
      }))
    })

    const entity = useMessageStore.getState().entities['summary-task-1']
    expect(entity.content).toBe('Summary text is already visible.')
    expect(entity.taskStatus).toBe(TASK_STATE.COMPLETED)
  })

  it('should handle heartbeat without side effects', async () => {
    await mountHook()
    const countBefore = useMessageStore.getState().orderedIds.length

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({ type: 'heartbeat' }))
    })

    expect(useMessageStore.getState().orderedIds.length).toBe(countBefore)
  })

  it('ignores processing_status "processing" without resolvable correlation', async () => {
    await mountHook()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: { status: 'processing', message_id: 'msg-1', client_request_id: 'req-missing-processing' },
      }))
    })

    expect(flags().processing).toBe(false)
  })

  it('ignores processing_status "completed" without resolvable correlation', async () => {
    useRoomUiStore.getState().setProcessing('room-1', true)

    await mountHook()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: { status: 'completed', client_request_id: 'req-missing-completed' },
      }))
    })

    expect(flags().processing).toBe(false)
  })

  it('drops processing_status without client_request_id', async () => {
    useRoomUiStore.getState().setProcessing('room-1', false)
    await mountHook()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: { status: 'processing', message_id: 'msg-uncorrelated-processing' },
      }))
    })

    expect(flags().processing).toBe(false)
  })

  it('buffers task_submitted when correlation is unresolved', async () => {
    await mountHook()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'task_submitted',
        data: {
          client_request_id: 'req-missing-task-submitted',
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

    expect(useMessageStore.getState().entities['task-1']).toBeUndefined()
  })

  it('buffers task_submitted with unresolved client_request_id instead of writing immediately', async () => {
    await mountHook()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'task_submitted',
        data: {
          client_request_id: 'req-buffer-task-submitted',
          message_id: 'task-buffered-1',
          task_id: 't-buffered-1',
          agent_name: 'Buffered Agent',
          status: 'working',
        },
      }))
    })

    expect(useMessageStore.getState().entities['task-buffered-1']).toBeUndefined()
  })

  it('buffers task_update with terminal state when correlation is unresolved', async () => {
    await mountHook()

    // First submit a task
    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'task_submitted',
        data: { client_request_id: 'req-missing-task-update', message_id: 'task-2', agent_name: 'Agent', status: 'working' },
      }))
    })

    // Then complete it
    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'task_update',
        data: {
          client_request_id: 'req-missing-task-update',
          message_id: 'task-2',
          status: 'completed',
          content: 'Done! Here is the result.',
          agent_name: 'Agent',
        },
      }))
    })

    expect(useMessageStore.getState().entities['task-2']).toBeUndefined()
  })

  it('buffers task_update with unresolved client_request_id instead of writing immediately', async () => {
    await mountHook()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'task_update',
        data: {
          client_request_id: 'req-buffer-task-update',
          message_id: 'task-buffered-2',
          status: 'working',
          content: 'Should be buffered',
          agent_name: 'Buffered Agent',
        },
      }))
    })

    expect(useMessageStore.getState().entities['task-buffered-2']).toBeUndefined()
  })

  it('buffers non-terminal task_update when correlation is unresolved', async () => {
    await mountHook()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'task_update',
        data: {
          client_request_id: 'req-missing-no-rewrite-non-terminal',
          message_id: 'task-no-rewrite-non-terminal',
          status: 'working',
          content: 'First visible answer chunk.',
          agent_name: 'Agent',
          task_content: 'Planning...',
        },
      }))
    })

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'task_update',
        data: {
          client_request_id: 'req-missing-no-rewrite-non-terminal',
          message_id: 'task-no-rewrite-non-terminal',
          status: 'working',
          content: 'Rewritten draft that should not replace visible content.',
          agent_name: 'Agent',
          task_content: 'Evaluating...',
          step_number: 2,
          total_steps: 4,
        },
      }))
    })

    expect(useMessageStore.getState().entities['task-no-rewrite-non-terminal']).toBeUndefined()
  })

  it('buffers terminal task_update when correlation is unresolved', async () => {
    await mountHook()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'task_update',
        data: {
          client_request_id: 'req-missing-no-rewrite-terminal',
          message_id: 'task-no-rewrite-terminal',
          status: 'working',
          content: 'Locked answer body.',
          agent_name: 'Agent',
        },
      }))
    })

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'task_update',
        data: {
          client_request_id: 'req-missing-no-rewrite-terminal',
          message_id: 'task-no-rewrite-terminal',
          status: 'completed',
          content: 'Final rewritten variant that should be dropped.',
          agent_name: 'Agent',
          status_message: 'Completed',
        },
      }))
    })

    expect(useMessageStore.getState().entities['task-no-rewrite-terminal']).toBeUndefined()
  })

  it('buffers completed task with parts when correlation is unresolved', async () => {
    await mountHook()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'task_update',
        data: {
          client_request_id: 'req-missing-parts-only',
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

    expect(useMessageStore.getState().entities['task-parts-only']).toBeUndefined()
  })

  it('does not store empty artifact_update payloads as renderable artifacts', async () => {
    const { resolveClientRequestMessageId } = await import('@/hooks/room/sse-handlers/pending-turn-buffer')
    await mountHook()

    resolveClientRequestMessageId('req-empty-artifact', 'task-empty-artifact')
    useMessageStore.getState().upsertMessage({
      id: 'task-empty-artifact',
      roomId: 'room-1',
      messageType: 'agent',
      content: 'Here is the employee CSV report.',
      senderName: 'CSV File Mock Agent',
      timestamp: new Date().toISOString(),
      agentId: 'agent-csv',
      clientRequestId: 'req-empty-artifact',
    }, 'sse')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'artifact_update',
        data: {
          client_request_id: 'req-empty-artifact',
          message_id: 'task-empty-artifact',
          agent_id: 'agent-csv',
          artifact: {
            artifact_id: 'empty-artifact',
            name: 'Response files',
            parts: [],
          },
          append: false,
          last_chunk: true,
        },
      }))
    })

    const entity = useMessageStore.getState().entities['task-empty-artifact']
    expect(entity.content).toBe('Here is the employee CSV report.')
    // artifact_update goes to streamingStore only; messageStore entity is unchanged
    expect(entity.artifacts).toBeUndefined()
  })

  it('normalizes root-wrapped file parts from task_update before storing artifacts', async () => {
    const { resolveClientRequestMessageId } = await import('@/hooks/room/sse-handlers/pending-turn-buffer')
    await mountHook()

    resolveClientRequestMessageId('req-root-file', 'task-root-file')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'task_update',
        data: {
          client_request_id: 'req-root-file',
          message_id: 'task-root-file',
          status: 'completed',
          content: 'Here is the PDF report.',
          agent_name: 'PDF File Mock Agent',
          agent_id: 'agent-pdf',
          parts: [
            {
              root: {
                kind: 'file',
                file: {
                  bytes: 'JVBERi0xLjQK',
                  mime_type: 'application/pdf',
                  name: 'mock_report.pdf',
                },
              },
            },
          ],
        },
      }))
    })

    const entity = useMessageStore.getState().entities['task-root-file']
    expect(entity.content).toBe('Here is the PDF report.')
    expect(entity.artifacts?.[0].parts).toEqual([
      {
        kind: 'file',
        file: {
          bytes: 'JVBERi0xLjQK',
          mime_type: 'application/pdf',
          name: 'mock_report.pdf',
          uri: undefined,
        },
        data: undefined,
        text: undefined,
      },
    ])
  })

  it('drops uncorrelated task_update even when lifecycle already has active message id', async () => {
    const { resolveClientRequestMessageId } = await import('@/hooks/room/sse-handlers/pending-turn-buffer')
    await mountHook()

    // Resolve correlation so processing_status can set lifecycle message id.
    resolveClientRequestMessageId('req-active-lifecycle', 'msg-active-lifecycle')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'processing',
          message_id: 'msg-active-lifecycle',
          client_request_id: 'req-active-lifecycle',
        },
      }))
    })

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'task_update',
        data: {
          // intentionally omit client_request_id to validate strict behavior
          message_id: 'task-active-lifecycle',
          status: 'working',
          content: 'Should still apply with active lifecycle context',
          agent_name: 'Fallback Agent',
        },
      }))
    })

    const entity = useMessageStore.getState().entities['task-active-lifecycle']
    expect(entity).toBeUndefined()
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

  it('should handle agent_response without agent_id by using fallback sender name', async () => {
    await mountHook()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'agent_response',
        data: {
          message_id: 'msg-no-agent-id',
          content: 'Response without agent id',
          // agent_id intentionally absent — simulates legacy hub or missing field
        },
      }))
    })

    const entity = useMessageStore.getState().entities['msg-no-agent-id']
    expect(entity).toBeDefined()
    expect(entity.content).toBe('Response without agent id')
    expect(entity.messageType).toBe('agent')
    expect(entity.isEphemeral).toBe(false)
    // agent_id is absent — senderName should be the fallback, not undefined
    expect(entity.senderName).toBeTruthy()
    expect(entity.agentId).toBeUndefined()
  })
})
