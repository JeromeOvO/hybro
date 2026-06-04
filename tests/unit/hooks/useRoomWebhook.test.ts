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
import {
  resetPendingTurnBufferForTests,
  resolveClientRequestMessageId,
} from '@/hooks/room/sse-handlers/pending-turn-buffer'

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
    resetPendingTurnBufferForTests()
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

  it('seeds the live user turn with Thinking log instead of creating an agent placeholder', async () => {
    const { result } = await mountHook()
    await waitFor(() => expect(result.current.room).toBeTruthy())

    await act(async () => {
      await result.current.sendUserMessage('Tell me a story')
    })

    const userEntity = useMessageStore
      .getState()
      .orderedIds
      .map((id) => useMessageStore.getState().entities[id])
      .find((entity) => entity?.messageType === 'user' && entity.content === 'Tell me a story')

    expect(userEntity?.processingStatusLogs?.map((entry) => entry.message)).toEqual([
      'Thinking...',
    ])
    expect(useMessageStore.getState().entities['processing-placeholder-room-1']).toBeUndefined()
  })

  it('records processing_status details on the user message and preserves them after terminal status', async () => {
    const { resolveClientRequestMessageId } = await import('@/hooks/room/sse-handlers/pending-turn-buffer')
    const { result } = await mountHook()
    await waitFor(() => expect(result.current.room).toBeTruthy())

    const latestClientRequestId = () =>
      useMessageStore
        .getState()
        .orderedIds
        .map((id) => useMessageStore.getState().entities[id])
        .filter((entity) => entity?.messageType === 'user')
        .at(-1)?.clientRequestId

    await act(async () => {
      await result.current.sendUserMessage('Analyze current project status')
    })

    const clientRequestId = latestClientRequestId()
    expect(clientRequestId).toBeTruthy()
    resolveClientRequestMessageId(clientRequestId!, 'msg-1')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'processing',
          message_id: 'msg-1',
          client_request_id: clientRequestId,
          details: 'Dispatching agents',
        },
      }))
    })

    const userAfterFirstDetail = useMessageStore
      .getState()
      .orderedIds
      .map((id) => useMessageStore.getState().entities[id])
      .find((entity) => entity?.messageType === 'user' && entity.clientRequestId === clientRequestId)

    expect(userAfterFirstDetail?.processingStatusLogs?.map((entry) => entry.message)).toEqual([
      'Thinking...',
      'Dispatching agents',
    ])

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'processing',
          message_id: 'msg-1',
          client_request_id: clientRequestId,
          details: 'Dispatching agents',
        },
      }))
    })

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'processing',
          message_id: 'msg-1',
          client_request_id: clientRequestId,
          details: 'Collecting agent results',
        },
      }))
    })

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'processing',
          message_id: 'msg-1',
          client_request_id: clientRequestId,
          details: 'Dispatching agents',
        },
      }))
    })

    const userBeforeTerminal = useMessageStore
      .getState()
      .orderedIds
      .map((id) => useMessageStore.getState().entities[id])
      .find((entity) => entity?.messageType === 'user' && entity.clientRequestId === clientRequestId)

    expect(userBeforeTerminal?.processingStatusLogs?.map((entry) => entry.message)).toEqual([
      'Thinking...',
      'Dispatching agents',
      'Collecting agent results',
    ])

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'awaiting_input',
          message_id: 'msg-1',
          client_request_id: clientRequestId,
        },
      }))
    })

    expect(useMessageStore.getState().entities[userBeforeTerminal!.id].processingStatusLogs?.map((entry) => entry.message)).toEqual([
      'Thinking...',
      'Dispatching agents',
      'Collecting agent results',
    ])

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'processing',
          message_id: 'msg-1',
          client_request_id: clientRequestId,
          details: 'Resuming after input',
        },
      }))
    })

    expect(useMessageStore.getState().entities[userBeforeTerminal!.id].processingStatusLogs?.map((entry) => entry.message)).toEqual([
      'Thinking...',
      'Dispatching agents',
      'Collecting agent results',
      'Resuming after input',
    ])

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'completed',
          message_id: 'msg-1',
          client_request_id: clientRequestId,
        },
      }))
    })

    const userAfterTerminal = useMessageStore.getState().entities[userBeforeTerminal!.id]
    expect(userAfterTerminal.processingStatusLogs?.map((entry) => entry.message)).toEqual([
      'Thinking...',
      'Dispatching agents',
      'Collecting agent results',
      'Resuming after input',
    ])
    expect(userAfterTerminal.turnTerminalStatus).toBe('completed')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'processing',
          message_id: 'msg-1',
          client_request_id: clientRequestId,
          details: 'Late update after terminal',
        },
      }))
    })

    expect(useMessageStore.getState().entities[userBeforeTerminal!.id].processingStatusLogs?.map((entry) => entry.message)).toEqual([
      'Thinking...',
      'Dispatching agents',
      'Collecting agent results',
      'Resuming after input',
    ])
    expect(flags().processing).toBe(false)
    expect(useMessageStore.getState().entities['processing-placeholder-room-1']).toBeUndefined()
  })

  it('preserves logs for a room-level terminal status when the user entity still has its optimistic id', async () => {
    const { resolveClientRequestMessageId } = await import('@/hooks/room/sse-handlers/pending-turn-buffer')
    const { result } = await mountHook()
    await waitFor(() => expect(result.current.room).toBeTruthy())

    const clientRequestId = 'req-fast-terminal'
    const optimisticUserId = `cr:${clientRequestId}`
    resolveClientRequestMessageId(clientRequestId, 'msg-fast-terminal')
    useRoomUiStore.getState().setProcessing('room-1', true)
    useMessageStore.getState().upsertMessage({
      id: optimisticUserId,
      roomId: 'room-1',
      messageType: 'user',
      content: 'Fast terminal race',
      senderName: 'User',
      timestamp: '2026-06-03T12:00:00.000Z',
      clientRequestId,
      processingStatusLogs: [
        {
          id: 'processing-log-1',
          message: 'Dispatching agents',
          timestamp: '2026-06-03T12:00:01.000Z',
        },
      ],
    }, 'optimistic')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'completed',
          message_id: 'msg-fast-terminal',
          client_request_id: clientRequestId,
        },
      }))
    })

    const userAfterTerminal = useMessageStore.getState().entities[optimisticUserId]
    expect(userAfterTerminal.processingStatusLogs?.map((entry) => entry.message)).toEqual([
      'Dispatching agents',
    ])
    expect(userAfterTerminal.turnTerminalStatus).toBe('completed')
    expect(flags().processing).toBe(false)
  })

  it('does not apply stale processing_status events to the newer active turn', async () => {
    const { SendMessage } = await import('@/lib/api/room')
    vi.mocked(SendMessage)
      .mockResolvedValueOnce({ success: true, message_id: 'msg-old' })
      .mockResolvedValueOnce({ success: true, message_id: 'msg-new' })

    const { result } = await mountHook()
    await waitFor(() => expect(result.current.room).toBeTruthy())

    const latestUser = () =>
      useMessageStore
        .getState()
        .orderedIds
        .map((id) => useMessageStore.getState().entities[id])
        .filter((entity) => entity?.messageType === 'user')
        .at(-1)

    await act(async () => {
      await result.current.sendUserMessage('Old turn')
    })
    const oldUser = latestUser()
    const oldClientRequestId = oldUser?.clientRequestId
    expect(oldClientRequestId).toBeTruthy()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'completed',
          message_id: 'msg-old',
          client_request_id: oldClientRequestId,
        },
      }))
    })

    expect(useMessageStore.getState().entities['msg-old'].turnTerminalStatus).toBe('completed')

    await act(async () => {
      await result.current.sendUserMessage('New turn')
    })
    const newUser = latestUser()
    const newClientRequestId = newUser?.clientRequestId
    expect(newClientRequestId).toBeTruthy()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'run_event',
        data: {
          type: 'run_started',
          correlation_id: newClientRequestId,
        },
      }))
    })

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'completed',
          message_id: 'msg-old',
          client_request_id: oldClientRequestId,
        },
      }))
    })

    expect(useMessageStore.getState().entities['msg-new'].turnTerminalStatus).toBeUndefined()
    expect(flags().processing).toBe(true)

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'processing',
          message_id: 'msg-old',
          client_request_id: oldClientRequestId,
          details: 'Late old processing detail',
        },
      }))
    })

    expect(useMessageStore.getState().entities['msg-old'].processingStatusLogs?.map((entry) => entry.message)).toEqual([
      'Thinking...',
    ])
    expect(useMessageStore.getState().entities['msg-new'].processingStatusLogs?.map((entry) => entry.message)).toEqual([
      'Thinking...',
    ])
  })

  it('does not append stale processing details to an inactive old turn', async () => {
    const { result } = await mountHook()
    await waitFor(() => expect(result.current.room).toBeTruthy())

    useMessageStore.getState().upsertMessage({
      id: 'msg-old',
      roomId: 'room-1',
      messageType: 'user',
      content: 'Old turn',
      senderName: 'Test',
      timestamp: '2026-06-04T01:00:00.000Z',
      clientRequestId: 'req-old-stale-detail',
      processingStatusLogs: [],
    }, 'optimistic')
    useMessageStore.getState().upsertMessage({
      id: 'msg-new',
      roomId: 'room-1',
      messageType: 'user',
      content: 'New turn',
      senderName: 'Test',
      timestamp: '2026-06-04T01:00:01.000Z',
      clientRequestId: 'req-new-active-detail',
      processingStatusLogs: [
        {
          id: 'processing-log-new-0',
          message: 'Thinking...',
          timestamp: '2026-06-04T01:00:01.000Z',
        },
      ],
    }, 'optimistic')
    resolveClientRequestMessageId('req-new-active-detail', 'msg-new')
    resolveClientRequestMessageId('req-old-stale-detail', 'msg-old')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'processing',
          message_id: 'msg-new',
          client_request_id: 'req-new-active-detail',
        },
      }))
    })

    expect(flags().processing).toBe(true)

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'processing',
          message_id: 'msg-old',
          client_request_id: 'req-old-stale-detail',
          details: 'Late old detail',
        },
      }))
    })

    expect(useMessageStore.getState().entities['msg-old'].processingStatusLogs).toEqual([])
    expect(useMessageStore.getState().entities['msg-new'].processingStatusLogs?.map((entry) => entry.message)).toEqual([
      'Thinking...',
    ])
  })

  it('does not let per-agent processing_status overwrite the turn cancel target', async () => {
    const { cancelMessage } = await import('@/lib/api/sse')
    const { result } = await mountHook()
    await waitFor(() => expect(result.current.room).toBeTruthy())

    await act(async () => {
      await result.current.sendUserMessage('Run with agent processing updates')
    })
    const userEntity = useMessageStore
      .getState()
      .orderedIds
      .map((id) => useMessageStore.getState().entities[id])
      .find((entity) => entity?.messageType === 'user')
    const clientRequestId = userEntity?.clientRequestId
    expect(clientRequestId).toBeTruthy()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'processing',
          message_id: 'agent-task-1',
          client_request_id: clientRequestId,
          details: 'Agent task is working',
        },
      }))
    })

    expect(useMessageStore.getState().entities[userEntity!.id].processingStatusLogs?.map((entry) => entry.message)).toEqual([
      'Thinking...',
      'Agent task is working',
    ])

    await act(async () => {
      await result.current.cancelProcessing()
    })

    expect(cancelMessage).toHaveBeenCalledWith('msg-1', expect.any(Function))
  })

  it('does not drop early processing logs for a new turn after a fast-terminal previous turn', async () => {
    const { SendMessage } = await import('@/lib/api/room')
    let resolveOldSend!: (value: { success: true; message_id: string }) => void
    let resolveNewSend!: (value: { success: true; message_id: string }) => void
    vi.mocked(SendMessage)
      .mockImplementationOnce(() => new Promise((resolve) => {
        resolveOldSend = resolve
      }))
      .mockImplementationOnce(() => new Promise((resolve) => {
        resolveNewSend = resolve
      }))

    const { result } = await mountHook()
    await waitFor(() => expect(result.current.room).toBeTruthy())

    const latestUser = () =>
      useMessageStore
        .getState()
        .orderedIds
        .map((id) => useMessageStore.getState().entities[id])
        .filter((entity) => entity?.messageType === 'user')
        .at(-1)

    let oldSendPromise!: Promise<boolean>
    await act(async () => {
      oldSendPromise = result.current.sendUserMessage('Fast terminal turn')
      await Promise.resolve()
    })
    const oldClientRequestId = latestUser()?.clientRequestId
    expect(oldClientRequestId).toBeTruthy()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'completed',
          message_id: 'msg-old',
          client_request_id: oldClientRequestId,
        },
      }))
    })

    await act(async () => {
      resolveOldSend({ success: true, message_id: 'msg-old' })
      await oldSendPromise
    })

    let newSendPromise!: Promise<boolean>
    await act(async () => {
      newSendPromise = result.current.sendUserMessage('Next turn with early detail')
      await Promise.resolve()
    })
    const newClientRequestId = latestUser()?.clientRequestId
    expect(newClientRequestId).toBeTruthy()
    expect(newClientRequestId).not.toBe(oldClientRequestId)

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'processing',
          message_id: 'msg-new',
          client_request_id: newClientRequestId,
          details: 'Early next-turn detail',
        },
      }))
    })

    await act(async () => {
      resolveNewSend({ success: true, message_id: 'msg-new' })
      await newSendPromise
    })

    expect(useMessageStore.getState().entities['msg-new'].processingStatusLogs?.map((entry) => entry.message)).toEqual([
      'Thinking...',
      'Early next-turn detail',
    ])
  })

  it('keeps the send guard active when buffered task output flushes before room terminal status', async () => {
    const { SendMessage } = await import('@/lib/api/room')
    let resolveSend!: (value: { success: true; message_id: string }) => void
    vi.mocked(SendMessage).mockImplementationOnce(() => new Promise((resolve) => {
      resolveSend = resolve
    }))

    const { result } = await mountHook()
    await waitFor(() => expect(result.current.room).toBeTruthy())

    let sendPromise!: Promise<boolean>
    await act(async () => {
      sendPromise = result.current.sendUserMessage('Flush buffered output')
      await Promise.resolve()
    })

    const userEntity = useMessageStore
      .getState()
      .orderedIds
      .map((id) => useMessageStore.getState().entities[id])
      .find((entity) => entity?.messageType === 'user' && entity.content === 'Flush buffered output')
    const clientRequestId = userEntity?.clientRequestId
    expect(clientRequestId).toBeTruthy()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'task_update',
        data: {
          status: TASK_STATE.COMPLETED,
          message_id: 'agent-buffered-1',
          client_request_id: clientRequestId,
          agent_id: 'agent-1',
          agent_name: 'Agent',
          content: 'Completed agent output before send resolves',
        },
      }))
    })

    await act(async () => {
      resolveSend({ success: true, message_id: 'msg-flush' })
      await sendPromise
    })

    expect(flags().processing).toBe(true)
    expect(useRoomUiStore.getState().rooms['room-1']?.sending).toBe(false)
    expect(useMessageStore.getState().entities['msg-flush'].processingStatusLogs?.map((entry) => entry.message)).toEqual([
      'Thinking...',
    ])

    let secondSendResult: boolean | undefined
    await act(async () => {
      secondSendResult = await result.current.sendUserMessage('Should still be guarded')
    })

    expect(secondSendResult).toBe(false)
    expect(vi.mocked(SendMessage)).toHaveBeenCalledTimes(1)
  })

  it('preserves the live processing log when buffered HITL input arrives before send resolves', async () => {
    const { SendMessage } = await import('@/lib/api/room')
    let resolveSend!: (value: { success: true; message_id: string }) => void
    vi.mocked(SendMessage).mockImplementationOnce(() => new Promise((resolve) => {
      resolveSend = resolve
    }))

    const { result } = await mountHook()
    await waitFor(() => expect(result.current.room).toBeTruthy())

    let sendPromise!: Promise<boolean>
    await act(async () => {
      sendPromise = result.current.sendUserMessage('Need HITL fast')
      await Promise.resolve()
    })

    const userEntity = useMessageStore
      .getState()
      .orderedIds
      .map((id) => useMessageStore.getState().entities[id])
      .find((entity) => entity?.messageType === 'user' && entity.content === 'Need HITL fast')
    const clientRequestId = userEntity?.clientRequestId
    expect(clientRequestId).toBeTruthy()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'hitl_input_requested',
        data: {
          request_id: 'req-fast-hitl',
          message_id: 'hitl-fast-1',
          prompt: 'Which option?',
          prompt_type: 'text',
          agent_name: 'Agent',
          related_message_id: userEntity!.id,
          client_request_id: clientRequestId,
        },
      }))
    })

    await act(async () => {
      resolveSend({ success: true, message_id: 'msg-fast-hitl' })
      await sendPromise
    })

    expect(flags().processing).toBe(false)
    expect(useMessageStore.getState().entities['msg-fast-hitl'].processingStatusLogs?.map((entry) => entry.message)).toEqual([
      'Thinking...',
    ])

    let secondSendResult: boolean | undefined
    await act(async () => {
      secondSendResult = await result.current.sendUserMessage('Allowed after HITL')
    })
    expect(secondSendResult).toBe(true)
    expect(vi.mocked(SendMessage)).toHaveBeenCalledTimes(2)
  })

  it('does not let stale terminal processing_status clear a newer unresolved send log', async () => {
    const { SendMessage } = await import('@/lib/api/room')
    vi.mocked(SendMessage)
      .mockResolvedValueOnce({ success: true, message_id: 'msg-old' })

    let resolveNewSend!: (value: { success: true; message_id: string }) => void
    vi.mocked(SendMessage).mockImplementationOnce(() => new Promise((resolve) => {
      resolveNewSend = resolve
    }))

    const { result } = await mountHook()
    await waitFor(() => expect(result.current.room).toBeTruthy())

    const latestUser = () =>
      useMessageStore
        .getState()
        .orderedIds
        .map((id) => useMessageStore.getState().entities[id])
        .filter((entity) => entity?.messageType === 'user')
        .at(-1)

    await act(async () => {
      await result.current.sendUserMessage('Old turn')
    })
    const oldClientRequestId = latestUser()?.clientRequestId
    expect(oldClientRequestId).toBeTruthy()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'completed',
          message_id: 'msg-old',
          client_request_id: oldClientRequestId,
        },
      }))
    })

    let sendPromise!: Promise<boolean>
    await act(async () => {
      sendPromise = result.current.sendUserMessage('New unresolved turn')
      await Promise.resolve()
    })

    const newOptimisticUser = latestUser()
    expect(newOptimisticUser?.clientRequestId).toBeTruthy()
    expect(newOptimisticUser?.processingStatusLogs?.map((entry) => entry.message)).toEqual(['Thinking...'])
    expect(useMessageStore.getState().entities['processing-placeholder-room-1']).toBeUndefined()

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 200))
    })

    expect(useMessageStore.getState().entities[newOptimisticUser!.id].processingStatusLogs?.map((entry) => entry.message)).toEqual(['Thinking...'])

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'awaiting_input',
          message_id: 'msg-old',
          client_request_id: oldClientRequestId,
        },
      }))
    })

    expect(useMessageStore.getState().entities[newOptimisticUser!.id].processingStatusLogs?.map((entry) => entry.message)).toEqual(['Thinking...'])
    expect(flags().processing).toBe(true)

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'completed',
          message_id: 'msg-old',
          client_request_id: oldClientRequestId,
        },
      }))
    })

    expect(useMessageStore.getState().entities[newOptimisticUser!.id].processingStatusLogs?.map((entry) => entry.message)).toEqual(['Thinking...'])
    expect(flags().processing).toBe(true)

    await act(async () => {
      resolveNewSend({ success: true, message_id: 'msg-new' })
      await sendPromise
    })

    expect(useMessageStore.getState().entities['msg-new']).toBeDefined()
    expect(useMessageStore.getState().entities['msg-new'].processingStatusLogs?.map((entry) => entry.message)).toEqual(['Thinking...'])
  })

  it('does not let stale processing_status clear a newer unresolved log when the old user lacks clientRequestId', async () => {
    const { SendMessage } = await import('@/lib/api/room')
    vi.mocked(SendMessage)
      .mockResolvedValueOnce({ success: true, message_id: 'msg-old' })

    let resolveNewSend!: (value: { success: true; message_id: string }) => void
    vi.mocked(SendMessage).mockImplementationOnce(() => new Promise((resolve) => {
      resolveNewSend = resolve
    }))

    const { result } = await mountHook()
    await waitFor(() => expect(result.current.room).toBeTruthy())

    const latestUser = () =>
      useMessageStore
        .getState()
        .orderedIds
        .map((id) => useMessageStore.getState().entities[id])
        .filter((entity) => entity?.messageType === 'user')
        .at(-1)

    await act(async () => {
      await result.current.sendUserMessage('Old legacy turn')
    })
    const oldUser = latestUser()
    const oldClientRequestId = oldUser?.clientRequestId
    expect(oldClientRequestId).toBeTruthy()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'completed',
          message_id: 'msg-old',
          client_request_id: oldClientRequestId,
        },
      }))
    })

    useMessageStore.getState().upsertMessage({
      id: 'msg-old',
      roomId: 'room-1',
      messageType: 'user',
      content: 'Old legacy turn',
      senderName: 'User',
      timestamp: useMessageStore.getState().entities['msg-old'].timestamp,
      clientRequestId: null as unknown as string,
    }, 'db')
    expect(useMessageStore.getState().entities['msg-old'].clientRequestId).toBeNull()

    let sendPromise!: Promise<boolean>
    await act(async () => {
      sendPromise = result.current.sendUserMessage('New unresolved turn')
      await Promise.resolve()
    })

    const newOptimisticUser = latestUser()
    expect(newOptimisticUser?.clientRequestId).toBeTruthy()
    expect(newOptimisticUser?.processingStatusLogs?.map((entry) => entry.message)).toEqual(['Thinking...'])
    expect(useMessageStore.getState().entities['processing-placeholder-room-1']).toBeUndefined()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'awaiting_input',
          message_id: 'msg-old',
          client_request_id: oldClientRequestId,
        },
      }))
    })

    expect(useMessageStore.getState().entities[newOptimisticUser!.id].processingStatusLogs?.map((entry) => entry.message)).toEqual(['Thinking...'])
    expect(flags().processing).toBe(true)

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'completed',
          message_id: 'msg-old',
          client_request_id: oldClientRequestId,
        },
      }))
    })

    expect(useMessageStore.getState().entities[newOptimisticUser!.id].processingStatusLogs?.map((entry) => entry.message)).toEqual(['Thinking...'])
    expect(flags().processing).toBe(true)

    await act(async () => {
      resolveNewSend({ success: true, message_id: 'msg-new' })
      await sendPromise
    })

    expect(useMessageStore.getState().entities['msg-new']).toBeDefined()
    expect(useMessageStore.getState().entities['msg-new'].processingStatusLogs?.map((entry) => entry.message)).toEqual(['Thinking...'])
  })

  it('does not let stale terminal processing_status with stale lifecycle id clear a newer unresolved log', async () => {
    const { SendMessage } = await import('@/lib/api/room')
    vi.mocked(SendMessage)
      .mockResolvedValueOnce({ success: true, message_id: 'msg-old' })

    let resolveNewSend!: (value: { success: true; message_id: string }) => void
    vi.mocked(SendMessage).mockImplementationOnce(() => new Promise((resolve) => {
      resolveNewSend = resolve
    }))

    const { result } = await mountHook()
    await waitFor(() => expect(result.current.room).toBeTruthy())

    const latestUser = () =>
      useMessageStore
        .getState()
        .orderedIds
        .map((id) => useMessageStore.getState().entities[id])
        .filter((entity) => entity?.messageType === 'user')
        .at(-1)

    await act(async () => {
      await result.current.sendUserMessage('Old fast-terminal turn')
    })
    const oldClientRequestId = latestUser()?.clientRequestId
    expect(oldClientRequestId).toBeTruthy()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'completed',
          message_id: 'msg-old',
          client_request_id: oldClientRequestId,
        },
      }))
    })

    useRoomUiStore.getState().setProcessing('room-1', true)
    useMessageStore.getState().upsertMessage({
      id: 'processing-placeholder-room-1',
      roomId: 'room-1',
      messageType: 'agent',
      content: '',
      senderName: 'HYBRO AI',
      taskStatus: TASK_STATE.WORKING,
      taskContent: 'Processing your request...',
      timestamp: new Date().toISOString(),
      isEphemeral: true,
      clientRequestId: oldClientRequestId,
    }, 'optimistic')

    let sendPromise!: Promise<boolean>
    await act(async () => {
      sendPromise = result.current.sendUserMessage('New unresolved turn')
      await Promise.resolve()
    })

    const newOptimisticUser = latestUser()
    expect(newOptimisticUser?.processingStatusLogs?.map((entry) => entry.message)).toEqual(['Thinking...'])
    expect(useMessageStore.getState().entities['processing-placeholder-room-1']).toBeUndefined()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'completed',
          message_id: 'msg-old',
          client_request_id: oldClientRequestId,
        },
      }))
    })

    expect(useMessageStore.getState().entities[newOptimisticUser!.id].processingStatusLogs?.map((entry) => entry.message)).toEqual(['Thinking...'])
    expect(flags().processing).toBe(true)

    await act(async () => {
      resolveNewSend({ success: true, message_id: 'msg-new' })
      await sendPromise
    })

    expect(useMessageStore.getState().entities['msg-new']).toBeDefined()
    expect(useMessageStore.getState().entities['msg-new'].processingStatusLogs?.map((entry) => entry.message)).toEqual(['Thinking...'])
  })

  it.each(['failed', 'canceled', 'rejected', 'error', 'rate_limited'] as const)(
    'preserves processing status logs on %s processing_status',
    async (terminalStatus) => {
      const { resolveClientRequestMessageId } = await import('@/hooks/room/sse-handlers/pending-turn-buffer')
      const { result } = await mountHook()
      await waitFor(() => expect(result.current.room).toBeTruthy())

      const latestClientRequestId = () =>
        useMessageStore
          .getState()
          .orderedIds
          .map((id) => useMessageStore.getState().entities[id])
          .filter((entity) => entity?.messageType === 'user')
          .at(-1)?.clientRequestId

      await act(async () => {
        await result.current.sendUserMessage(`Trigger ${terminalStatus}`)
      })

      const clientRequestId = latestClientRequestId()
      expect(clientRequestId).toBeTruthy()
      resolveClientRequestMessageId(clientRequestId!, 'msg-1')

      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'processing_status',
          data: {
            status: 'processing',
            message_id: 'msg-1',
            client_request_id: clientRequestId,
            details: 'Processing before terminal',
          },
        }))
      })

      const userBeforeTerminal = useMessageStore
        .getState()
        .orderedIds
        .map((id) => useMessageStore.getState().entities[id])
        .find((entity) => entity?.messageType === 'user' && entity.clientRequestId === clientRequestId)
      expect(userBeforeTerminal?.processingStatusLogs).toHaveLength(2)
      const userMessageId = userBeforeTerminal!.id

      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'processing_status',
          data: {
            status: terminalStatus,
            message_id: userMessageId,
            client_request_id: clientRequestId,
          },
        }))
      })

      const terminalUser = useMessageStore.getState().entities[userBeforeTerminal!.id]
      expect(terminalUser.processingStatusLogs?.map((entry) => entry.message)).toEqual([
        'Thinking...',
        'Processing before terminal',
      ])
      expect(terminalUser.turnTerminalStatus).toBe(
        terminalStatus === 'canceled' ? 'canceled' : 'failed',
      )
    },
  )

  it.each(['completed', 'failed', 'canceled', 'rejected', 'error', 'rate_limited'] as const)(
    'does not clear processing status logs for per-agent %s processing_status events',
    async (terminalStatus) => {
      const { resolveClientRequestMessageId } = await import('@/hooks/room/sse-handlers/pending-turn-buffer')
      const { result } = await mountHook()
      await waitFor(() => expect(result.current.room).toBeTruthy())

      const latestClientRequestId = () =>
        useMessageStore
          .getState()
          .orderedIds
          .map((id) => useMessageStore.getState().entities[id])
          .filter((entity) => entity?.messageType === 'user')
          .at(-1)?.clientRequestId

      await act(async () => {
        await result.current.sendUserMessage('Trigger per-agent terminal')
      })

      const clientRequestId = latestClientRequestId()
      expect(clientRequestId).toBeTruthy()
      resolveClientRequestMessageId(clientRequestId!, 'msg-1')

      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'processing_status',
          data: {
            status: 'processing',
            message_id: 'msg-1',
            client_request_id: clientRequestId,
            details: 'Processing before agent terminal',
          },
        }))
      })

      const userBeforeAgentTerminal = useMessageStore
        .getState()
        .orderedIds
        .map((id) => useMessageStore.getState().entities[id])
        .find((entity) => entity?.messageType === 'user' && entity.clientRequestId === clientRequestId)
      expect(userBeforeAgentTerminal?.processingStatusLogs).toHaveLength(2)

      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'processing_status',
          data: {
            status: terminalStatus,
            message_id: 'agent-task-1',
            client_request_id: clientRequestId,
          },
        }))
      })

      expect(useMessageStore.getState().entities[userBeforeAgentTerminal!.id].processingStatusLogs).toHaveLength(2)
    },
  )
})
