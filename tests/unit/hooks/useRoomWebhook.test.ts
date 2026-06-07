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
import { useStreamingStore } from '@/stores/streaming-store'
import { TASK_STATE, type AnySSEFrame } from '@/lib/types/sse'
import {
  flushPendingSseEvents,
  resetPendingTurnBufferForTests,
  resolveClientRequestMessageId,
} from '@/hooks/room/sse-handlers/pending-turn-buffer'

// Capture the onMessage callback passed to useRoomSSE
let capturedOnMessage: ((msg: AnySSEFrame) => void) | undefined

vi.mock('@/hooks/useRoomSSE', () => ({
  useRoomSSE: vi.fn((opts: { onMessage?: (msg: AnySSEFrame) => void }) => {
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

describe('useRoomWebhook SSE message handling', () => {
  beforeEach(async () => {
    const { SendMessage } = await import('@/lib/api/room')

    vi.clearAllMocks()
    vi.mocked(SendMessage).mockReset()
    vi.mocked(SendMessage).mockResolvedValue({ success: true, message_id: 'msg-1' })
    capturedOnMessage = undefined
    resetPendingTurnBufferForTests()
    useMessageStore.getState().clearRoom()
    useMessageStore.getState().setRoom('room-1')
    useMessageStore.getState().markDbSynced()
    useStreamingStore.setState({ buffers: {} })
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

  it.each(['user_message', 'turn_event', 'hitl_input_requested', 'hitl_status_update'])(
    'ignores legacy %s frames under the final room SSE contract',
    async (legacyType) => {
    await mountHook()
    expect(capturedOnMessage).toBeDefined()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: legacyType,
        data: {
          message_id: `msg-${legacyType}`,
          request_id: `req-${legacyType}`,
          content: 'Hello from SSE',
          prompt: 'Input please',
          prompt_type: 'text',
          user_id: 'user-42',
        },
      }))
    })

    expect(useMessageStore.getState().entities[`msg-${legacyType}`]).toBeUndefined()
    },
  )

  it('handles connected frames with final connection_id without message store side effects', async () => {
    await mountHook()
    expect(capturedOnMessage).toBeDefined()
    const countBefore = useMessageStore.getState().orderedIds.length

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'connected',
        data: {
          connection_id: 'conn-room-1',
        },
      }))
    })

    expect(useMessageStore.getState().orderedIds.length).toBe(countBefore)
  })

  it('ignores malformed connected frames without connection_id', async () => {
    await mountHook()
    expect(capturedOnMessage).toBeDefined()
    const countBefore = useMessageStore.getState().orderedIds.length

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'connected',
        data: {
          status: 'connected',
        },
      }))
    })

    expect(useMessageStore.getState().orderedIds.length).toBe(countBefore)
  })

  it('treats cancellation frames as debug-only events', async () => {
    await mountHook()
    expect(capturedOnMessage).toBeDefined()
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    useRoomUiStore.getState().setCancelling('room-1', true)

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'cancellation',
        data: { reason: 'server-notification' },
      }))
    })

    expect(flags().cancelling).toBe(true)
  })

  it('should handle agent_response by finalizing streaming and writing to store', async () => {
    await mountHook()
    resolveClientRequestMessageId('req-agent-response', 'msg-a1')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'agent_response',
        data: {
          message_id: 'msg-a1',
          content: 'Agent reply',
          agent_id: 'agent-1',
          client_request_id: 'req-agent-response',
        },
      }))
    })

    const entity = useMessageStore.getState().entities['msg-a1']
    expect(entity).toBeDefined()
    expect(entity.content).toBe('Agent reply')
    expect(entity.messageType).toBe('agent')
    expect(entity.isEphemeral).toBe(false)
  })

  it('streams agent_response_partial into the transient buffer with correlation metadata', async () => {
    await mountHook()
    resolveClientRequestMessageId('req-partial-response', 'user-partial-1')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'agent_response_partial',
        data: {
          message_id: 'agent-partial-1',
          agent_id: 'agent-1',
          content_delta: 'Partial text',
          client_request_id: 'req-partial-response',
        },
      }))
    })

    const buffer = useStreamingStore.getState().buffers['agent-partial-1']
    expect(buffer.text).toBe('Partial text')
    expect(buffer.clientRequestId).toBe('req-partial-response')
    expect(buffer.userMessageId).toBe('user-partial-1')
    expect(useMessageStore.getState().entities['agent-partial-1']).toBeUndefined()
  })

  it('buffers agent_response_partial until send resolves the client_request_id', async () => {
    await mountHook()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'agent_response_partial',
        data: {
          message_id: 'agent-partial-buffered',
          agent_id: 'agent-1',
          content_delta: 'Buffered text',
          client_request_id: 'req-partial-buffered',
        },
      }))
    })

    expect(useStreamingStore.getState().buffers['req-partial-buffered']).toBeUndefined()

    await act(async () => {
      await flushPendingSseEvents('req-partial-buffered', async (event) => {
        await capturedOnMessage!(event)
      }, 'user-buffered-1')
    })

    const buffer = useStreamingStore.getState().buffers['agent-partial-buffered']
    expect(buffer.text).toBe('Buffered text')
    expect(buffer.clientRequestId).toBe('req-partial-buffered')
    expect(buffer.userMessageId).toBe('user-buffered-1')
  })

  it('clears message-keyed partial buffer when final agent_response arrives for the same message_id', async () => {
    await mountHook()
    resolveClientRequestMessageId('req-partial-final', 'user-partial-final')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'agent_response_partial',
        data: {
          message_id: 'final-msg-1',
          agent_id: 'agent-1',
          content_delta: 'Draft stream',
          client_request_id: 'req-partial-final',
        },
      }))
    })

    expect(useStreamingStore.getState().buffers['final-msg-1']?.text).toBe('Draft stream')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'agent_response',
        data: {
          message_id: 'final-msg-1',
          content: 'Final stream',
          agent_id: 'agent-1',
          client_request_id: 'req-partial-final',
        },
      }))
    })

    expect(useStreamingStore.getState().buffers['final-msg-1']).toBeUndefined()
    expect(useMessageStore.getState().entities['final-msg-1']?.content).toBe('Final stream')
  })

  it('marks an existing working agent as completed when final agent_response repeats streamed content', async () => {
    await mountHook()
    resolveClientRequestMessageId('req-summary-response', 'summary-task-1')

    useMessageStore.getState().upsertMessage({
      id: 'summary-task-1',
      roomId: 'room-1',
      messageType: 'agent',
      content: 'Summary text is already visible.',
      senderName: 'Summary Agent',
      timestamp: new Date().toISOString(),
      agentId: 'summary-agent',
      taskStatus: TASK_STATE.WORKING,
      clientRequestId: 'req-summary-response',
    }, 'sse')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'agent_response',
        data: {
          message_id: 'summary-task-1',
          content: 'Summary text is already visible.',
          agent_id: 'summary-agent',
          client_request_id: 'req-summary-response',
        },
      }))
    })

    const entity = useMessageStore.getState().entities['summary-task-1']
    expect(entity.content).toBe('Summary text is already visible.')
    expect(entity.taskStatus).toBe(TASK_STATE.COMPLETED)
  })

  it('clears only the completed message buffer on terminal task_update (not turn-wide)', async () => {
    await mountHook()
    resolveClientRequestMessageId('req-completed-duplicate-final', 'user-completed-duplicate-final')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'agent_response_partial',
        data: {
          message_id: 'partial-completed-duplicate-final',
          agent_id: 'agent-1',
          content_delta: 'Draft that should not linger',
          client_request_id: 'req-completed-duplicate-final',
        },
      }))
    })

    expect(useStreamingStore.getState().buffers['partial-completed-duplicate-final']?.text)
      .toBe('Draft that should not linger')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'task_update',
        data: {
          message_id: 'completed-duplicate-final',
          agent_id: 'agent-1',
          agent_name: 'Research Agent',
          status: TASK_STATE.COMPLETED,
          content: 'Completed answer',
          client_request_id: 'req-completed-duplicate-final',
        },
      }))
    })

    expect(useMessageStore.getState().entities['completed-duplicate-final']?.taskStatus).toBe(TASK_STATE.COMPLETED)
    expect(useStreamingStore.getState().buffers['completed-duplicate-final']).toBeUndefined()
    expect(useStreamingStore.getState().buffers['partial-completed-duplicate-final']?.text)
      .toBe('Draft that should not linger')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'agent_response_partial',
        data: {
          message_id: 'partial-completed-duplicate-final',
          agent_id: 'agent-1',
          content_delta: 'Late draft that should not linger',
          client_request_id: 'req-completed-duplicate-final',
        },
      }))
    })

    expect(useStreamingStore.getState().buffers['partial-completed-duplicate-final']?.text)
      .toBe('Draft that should not lingerLate draft that should not linger')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'agent_response',
        data: {
          message_id: 'completed-duplicate-final',
          content: 'Completed answer',
          agent_id: 'agent-1',
          client_request_id: 'req-completed-duplicate-final',
        },
      }))
    })

    expect(useMessageStore.getState().entities['completed-duplicate-final']?.content).toBe('Completed answer')
    expect(useStreamingStore.getState().buffers['completed-duplicate-final']).toBeUndefined()
  })

  it('persists a distinct final agent_response after task_submitted from the same agent', async () => {
    await mountHook()
    resolveClientRequestMessageId('req-task-then-response', 'user-task-root')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'task_submitted',
        data: {
          message_id: 'task-1',
          agent_id: 'agent-1',
          agent_name: 'Research Agent',
          status: TASK_STATE.WORKING,
          task_content: 'Searching',
          client_request_id: 'req-task-then-response',
        },
      }))
    })

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'agent_response',
        data: {
          message_id: 'response-1',
          content: 'Final answer',
          agent_id: 'agent-1',
          client_request_id: 'req-task-then-response',
        },
      }))
    })

    const store = useMessageStore.getState()
    expect(store.entities['task-1']).toBeDefined()
    expect(store.entities['response-1']).toMatchObject({
      content: 'Final answer',
      messageType: 'agent',
      agentId: 'agent-1',
      taskStatus: TASK_STATE.COMPLETED,
      isEphemeral: false,
    })
  })

  it('lets final agent_response replace a completed task_update with the same message id', async () => {
    await mountHook()
    resolveClientRequestMessageId('req-task-final-rewrite', 'user-task-root')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'task_update',
        data: {
          message_id: 'agent-msg-1',
          agent_id: 'agent-1',
          agent_name: 'Research Agent',
          status: TASK_STATE.COMPLETED,
          content: 'Draft result',
          client_request_id: 'req-task-final-rewrite',
        },
      }))
    })

    expect(useMessageStore.getState().entities['agent-msg-1']?.content).toBe('Draft result')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'agent_response',
        data: {
          message_id: 'agent-msg-1',
          content: 'Final answer',
          agent_id: 'agent-1',
          client_request_id: 'req-task-final-rewrite',
        },
      }))
    })

    expect(useMessageStore.getState().entities['agent-msg-1']).toMatchObject({
      content: 'Final answer',
      messageType: 'agent',
      agentId: 'agent-1',
      taskStatus: TASK_STATE.COMPLETED,
      isEphemeral: false,
    })
  })

  it('lets same-text final agent_response remove stale task_update artifacts', async () => {
    await mountHook()
    resolveClientRequestMessageId('req-task-final-remove-artifact', 'user-task-root')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'task_update',
        data: {
          message_id: 'agent-msg-remove-artifact',
          agent_id: 'agent-1',
          agent_name: 'Research Agent',
          status: TASK_STATE.COMPLETED,
          content: 'Final answer',
          client_request_id: 'req-task-final-remove-artifact',
          parts: [
            { kind: 'file', file: { uri: 'https://example.test/draft.pdf', mime_type: 'application/pdf', name: 'draft.pdf' } },
          ],
        },
      }))
    })

    expect(useMessageStore.getState().entities['agent-msg-remove-artifact']?.artifacts).toHaveLength(1)
    useMessageStore.getState().upsertMessage({
      ...useMessageStore.getState().entities['agent-msg-remove-artifact'],
      taskContent: '',
    }, 'sse')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'agent_response',
        data: {
          message_id: 'agent-msg-remove-artifact',
          content: 'Final answer',
          agent_id: 'agent-1',
          client_request_id: 'req-task-final-remove-artifact',
        },
      }))
    })

    expect(useMessageStore.getState().entities['agent-msg-remove-artifact']).toMatchObject({
      content: 'Final answer',
      artifacts: [],
      taskStatus: TASK_STATE.COMPLETED,
    })
  })

  it('lets same-text final agent_response replace task_update artifacts', async () => {
    await mountHook()
    resolveClientRequestMessageId('req-task-final-replace-artifact', 'user-task-root')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'task_update',
        data: {
          message_id: 'agent-msg-replace-artifact',
          agent_id: 'agent-1',
          agent_name: 'Research Agent',
          status: TASK_STATE.COMPLETED,
          content: 'Final answer',
          client_request_id: 'req-task-final-replace-artifact',
          parts: [
            { kind: 'file', file: { uri: 'https://example.test/draft.pdf', mime_type: 'application/pdf', name: 'draft.pdf' } },
          ],
        },
      }))
    })

    useMessageStore.getState().upsertMessage({
      ...useMessageStore.getState().entities['agent-msg-replace-artifact'],
      taskContent: '',
    }, 'sse')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'agent_response',
        data: {
          message_id: 'agent-msg-replace-artifact',
          content: 'Final answer',
          agent_id: 'agent-1',
          client_request_id: 'req-task-final-replace-artifact',
          parts: [
            { kind: 'file', file: { uri: 'https://example.test/final.pdf', mime_type: 'application/pdf', name: 'final.pdf' } },
          ],
        },
      }))
    })

    const entity = useMessageStore.getState().entities['agent-msg-replace-artifact']
    expect(entity.content).toBe('Final answer')
    expect(entity.artifacts?.[0]?.parts).toEqual([
      { kind: 'file', file: { uri: 'https://example.test/final.pdf', bytes: undefined, mime_type: 'application/pdf', name: 'final.pdf' }, text: undefined, data: undefined },
    ])
    expect(entity.taskStatus).toBe(TASK_STATE.COMPLETED)
  })

  it('applies parts-only final agent_response after same-id task_update content', async () => {
    await mountHook()
    resolveClientRequestMessageId('req-task-final-parts', 'user-task-root')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'task_update',
        data: {
          message_id: 'agent-msg-parts',
          agent_id: 'agent-1',
          agent_name: 'Research Agent',
          status: TASK_STATE.COMPLETED,
          content: 'Draft result',
          client_request_id: 'req-task-final-parts',
        },
      }))
    })

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'agent_response',
        data: {
          message_id: 'agent-msg-parts',
          agent_id: 'agent-1',
          client_request_id: 'req-task-final-parts',
          parts: [
            { kind: 'file', file: { uri: 'https://example.test/final.pdf', mime_type: 'application/pdf', name: 'final.pdf' } },
          ],
        },
      }))
    })

    const entity = useMessageStore.getState().entities['agent-msg-parts']
    expect(entity.content).toBe('')
    expect(entity.artifacts?.[0]?.parts).toEqual([
      { kind: 'file', file: { uri: 'https://example.test/final.pdf', bytes: undefined, mime_type: 'application/pdf', name: 'final.pdf' }, text: undefined, data: undefined },
    ])
    expect(entity.taskStatus).toBe(TASK_STATE.COMPLETED)
  })

  it('lets shorter final agent_response replace longer same-id task_update content', async () => {
    await mountHook()
    resolveClientRequestMessageId('req-task-final-shorter', 'user-task-root')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'task_update',
        data: {
          message_id: 'agent-msg-shorter',
          agent_id: 'agent-1',
          agent_name: 'Research Agent',
          status: TASK_STATE.COMPLETED,
          content: 'Final answer with draft tail',
          client_request_id: 'req-task-final-shorter',
        },
      }))
    })

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'agent_response',
        data: {
          message_id: 'agent-msg-shorter',
          content: 'Final answer',
          agent_id: 'agent-1',
          client_request_id: 'req-task-final-shorter',
        },
      }))
    })

    expect(useMessageStore.getState().entities['agent-msg-shorter']).toMatchObject({
      content: 'Final answer',
      taskStatus: TASK_STATE.COMPLETED,
    })
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
        data: { status: 'processing', message_id: 'msg-1', client_request_id: 'req-missing-processing', details: null },
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
        data: { status: 'completed', client_request_id: 'req-missing-completed', details: null },
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

  it('drops processing_status with null message_id even when client_request_id is resolved', async () => {
    await mountHook()
    useMessageStore.getState().upsertMessage({
      id: 'msg-null-processing-id',
      roomId: 'room-1',
      messageType: 'user',
      content: 'Null message id should be ignored',
      senderName: 'Test',
      timestamp: '2026-06-04T01:00:00.000Z',
      clientRequestId: 'req-null-processing-id',
      processingStatusLogs: [],
    }, 'optimistic')
    resolveClientRequestMessageId('req-null-processing-id', 'msg-null-processing-id')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'processing',
          message_id: null,
          client_request_id: 'req-null-processing-id',
          details: { message: 'Should not be appended' },
        },
      }))
    })

    expect(useMessageStore.getState().entities['msg-null-processing-id'].processingStatusLogs).toEqual([])
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

  it('concatenates multi-artifact streaming text instead of keeping only the last segment', async () => {
    await mountHook()
    resolveClientRequestMessageId('req-hermes-stream', 'user-hermes-1')

    useMessageStore.getState().upsertMessage({
      id: 'agent-hermes-1',
      roomId: 'room-1',
      messageType: 'agent',
      content: '',
      senderName: 'Hermes Agent',
      timestamp: new Date().toISOString(),
      agentId: 'hermes-agent',
      clientRequestId: 'req-hermes-stream',
      taskStatus: TASK_STATE.WORKING,
    }, 'sse')

    const segments = [
      'Now let me execute the research workflow. ',
      'Good HN data. Let me navigate to the top AI stories now. ',
      'Excellent! Now I have all the URLs.',
    ]

    for (const [index, text] of segments.entries()) {
      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'artifact_update',
          data: {
            client_request_id: 'req-hermes-stream',
            message_id: 'agent-hermes-1',
            agent_id: 'hermes-agent',
            artifact: {
              artifact_id: `segment-${index}`,
              parts: [{ kind: 'text', text }],
            },
            append: false,
            last_chunk: index === segments.length - 1,
          },
        }))
      })
    }

    const buffer = useStreamingStore.getState().buffers['agent-hermes-1']
    expect(buffer?.text).toBe(segments.join(''))
    expect(buffer?.isComplete).toBe(true)

    const { selectAgentResponseDetail } = await import('@/lib/selectors/select-agent-response-detail')
    const { entities, orderedIds } = useMessageStore.getState()
    const detail = selectAgentResponseDetail(
      'room-1',
      'agent-hermes-1',
      entities,
      orderedIds,
      buffer,
    )

    expect(detail?.content).toBe(segments.join(''))
    expect(detail?.isStreaming).toBe(false)
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
          details: null,
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

  it('handles malformed error SSE data without throwing', async () => {
    const { banner } = await import('@/components/ui/banner')
    await mountHook()

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'error',
        data: null,
      }))
    })

    expect(banner.error).toHaveBeenCalledWith('Unknown error')
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
      { description: 'Retry after 2 minutes.', duration: 15000 }
    )
  })

  it('should handle agent_response without agent_id by using fallback sender name', async () => {
    await mountHook()
    resolveClientRequestMessageId('req-no-agent-id', 'msg-no-agent-id')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'agent_response',
        data: {
          message_id: 'msg-no-agent-id',
          content: 'Response without agent id',
          client_request_id: 'req-no-agent-id',
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
      await result.current.sendUserMessage({ userInput: 'Tell me a story', dispatch: { message_target_mode: 'room_default' } })
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
      await result.current.sendUserMessage({ userInput: 'Analyze current project status', dispatch: { message_target_mode: 'room_default' } })
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
          details: { message: 'Dispatching agents' },
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
          details: { message: 'Dispatching agents' },
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
          details: { message: 'Collecting agent results' },
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
          details: { message: 'Dispatching agents' },
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
          details: null,
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
          details: { message: 'Resuming after input' },
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
          details: null,
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
          details: { message: 'Late update after terminal' },
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

  it('normalizes structured processing_status details before appending logs or showing failure banners', async () => {
    const { banner } = await import('@/components/ui/banner')
    const { resolveClientRequestMessageId } = await import('@/hooks/room/sse-handlers/pending-turn-buffer')
    const { result } = await mountHook()
    await waitFor(() => expect(result.current.room).toBeTruthy())

    await act(async () => {
      await result.current.sendUserMessage({ userInput: 'Handle structured processing details', dispatch: { message_target_mode: 'room_default' } })
    })

    const clientRequestId = useMessageStore
      .getState()
      .orderedIds
      .map((id) => useMessageStore.getState().entities[id])
      .filter((entity) => entity?.messageType === 'user')
      .at(-1)?.clientRequestId
    expect(clientRequestId).toBeTruthy()
    resolveClientRequestMessageId(clientRequestId!, 'msg-structured-details')

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'processing',
          message_id: 'msg-structured-details',
          client_request_id: clientRequestId,
          details: { message: 'Planning next action...' },
        },
      }))
    })

    const userEntity = useMessageStore
      .getState()
      .orderedIds
      .map((id) => useMessageStore.getState().entities[id])
      .find((entity) => entity?.messageType === 'user' && entity.clientRequestId === clientRequestId)

    expect(userEntity?.processingStatusLogs?.map((entry) => entry.message)).toEqual([
      'Thinking...',
      'Planning next action...',
    ])

    await act(async () => {
      await capturedOnMessage!(makeSSEMessage({
        type: 'processing_status',
        data: {
          status: 'failed',
          message_id: 'msg-structured-details',
          client_request_id: clientRequestId,
          details: { message: 'Backend failed cleanly' },
        },
      }))
    })

    expect(banner.error).toHaveBeenCalledWith('Processing failed: Backend failed cleanly')
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
          details: null,
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
      await result.current.sendUserMessage({ userInput: 'Old turn', dispatch: { message_target_mode: 'room_default' } })
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
          details: null,
        },
      }))
    })

    expect(useMessageStore.getState().entities['msg-old'].turnTerminalStatus).toBe('completed')

    await act(async () => {
      await result.current.sendUserMessage({ userInput: 'New turn', dispatch: { message_target_mode: 'room_default' } })
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
          details: null,
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
          details: { message: 'Late old processing detail' },
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
          details: null,
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
          details: { message: 'Late old detail' },
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
      await result.current.sendUserMessage({ userInput: 'Run with agent processing updates', dispatch: { message_target_mode: 'room_default' } })
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
          details: { message: 'Agent task is working' },
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
      oldSendPromise = result.current.sendUserMessage({ userInput: 'Fast terminal turn', dispatch: { message_target_mode: 'room_default' } })
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
          details: null,
        },
      }))
    })

    await act(async () => {
      resolveOldSend({ success: true, message_id: 'msg-old' })
      await oldSendPromise
    })

    let newSendPromise!: Promise<boolean>
    await act(async () => {
      newSendPromise = result.current.sendUserMessage({ userInput: 'Next turn with early detail', dispatch: { message_target_mode: 'room_default' } })
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
          details: { message: 'Early next-turn detail' },
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
      sendPromise = result.current.sendUserMessage({ userInput: 'Flush buffered output', dispatch: { message_target_mode: 'room_default' } })
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
      secondSendResult = await result.current.sendUserMessage({ userInput: 'Should still be guarded', dispatch: { message_target_mode: 'room_default' } })
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
      sendPromise = result.current.sendUserMessage({ userInput: 'Need HITL fast', dispatch: { message_target_mode: 'room_default' } })
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
        type: 'hitl_request',
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
      secondSendResult = await result.current.sendUserMessage({ userInput: 'Allowed after HITL', dispatch: { message_target_mode: 'room_default' } })
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
      await result.current.sendUserMessage({ userInput: 'Old turn', dispatch: { message_target_mode: 'room_default' } })
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
          details: null,
        },
      }))
    })

    let sendPromise!: Promise<boolean>
    await act(async () => {
      sendPromise = result.current.sendUserMessage({ userInput: 'New unresolved turn', dispatch: { message_target_mode: 'room_default' } })
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
          details: null,
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
          details: null,
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
      await result.current.sendUserMessage({ userInput: 'Old legacy turn', dispatch: { message_target_mode: 'room_default' } })
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
          details: null,
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
      sendPromise = result.current.sendUserMessage({ userInput: 'New unresolved turn', dispatch: { message_target_mode: 'room_default' } })
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
          details: null,
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
          details: null,
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
      await result.current.sendUserMessage({ userInput: 'Old fast-terminal turn', dispatch: { message_target_mode: 'room_default' } })
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
          details: null,
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
      sendPromise = result.current.sendUserMessage({ userInput: 'New unresolved turn', dispatch: { message_target_mode: 'room_default' } })
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
          details: null,
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
        await result.current.sendUserMessage({ userInput: `Trigger ${terminalStatus}`, dispatch: { message_target_mode: 'room_default' } })
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
            details: { message: 'Processing before terminal' },
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
            details: null,
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
        await result.current.sendUserMessage({ userInput: 'Trigger per-agent terminal', dispatch: { message_target_mode: 'room_default' } })
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
            details: { message: 'Processing before agent terminal' },
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
            related_message_id: userBeforeAgentTerminal!.id,
            client_request_id: clientRequestId,
            details: null,
          },
        }))
      })

      const userAfterAgentTerminal = useMessageStore.getState().entities[userBeforeAgentTerminal!.id]
      expect(userAfterAgentTerminal.processingStatusLogs).toHaveLength(2)
      expect(userAfterAgentTerminal.turnTerminalStatus).toBeUndefined()
      expect(flags().processing).toBe(true)
    },
  )
})
