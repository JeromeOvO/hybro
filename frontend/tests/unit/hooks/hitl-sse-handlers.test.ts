/**
 * Tests for HITL (Human-in-the-Loop) SSE message handling in useRoomWebhook.
 *
 * Strategy: same as useRoomWebhook.test.ts — mock useRoomSSE to capture the
 * onMessage callback, invoke with HITL SSE messages, verify message store writes.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup } from '@testing-library/react'
import React from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useMessageStore } from '@/stores/message-store'
import { useRoomUiStore } from '@/stores/room-ui-store'
import type { AnySSEFrame } from '@/lib/types/sse'
import { ApiError } from '@/lib/api-client'
import { createInitialProcessingStatusLog } from '@/hooks/room/processing-status-log'

function resolveClientRequestMessageId(clientRequestId: string, messageId: string): void {
  // Post-correlation-buffer: resolution is a store lookup. Mirror the
  // production flow — the optimistic entity exists from send time — but only
  // when a message id was explicitly correlated by the test (a real
  // related_message_id / optimistic entity), not for the synthetic default.
  if (messageId === 'user-hitl-test-default') return
  useMessageStore.getState().upsertMessage({
    id: messageId,
    roomId: 'room-1',
    messageType: 'user',
    content: 'test message',
    senderName: 'Test',
    timestamp: '2026-06-04T00:00:00.000Z',
    clientRequestId,
  }, 'optimistic')
}

const capturedOnMessage = (msg: AnySSEFrame) => {
  const cb = (globalThis as any).capturedOnMessage
  if (!cb) throw new Error('capturedOnMessage is not defined')
  return cb(msg)
}

vi.mock('@/hooks/useRoomSSE', () => ({
  useRoomSSE: vi.fn((opts: { onMessage?: (msg: AnySSEFrame) => void }) => {
    (globalThis as any).capturedOnMessage = opts.onMessage
    const connected = (globalThis as any).mockSseConnected ?? true
    return { connected, connecting: false, error: null }
  }),
}))

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
  respondToHitlBatch: vi.fn().mockResolvedValue({ status: 'applied', request_id: 'req-1' }),
  fetchPendingHitlRequests: vi.fn().mockResolvedValue({ requests: [] }),
}))

vi.mock('@/components/ui/banner', () => ({
  banner: { info: vi.fn(), error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}))

function makeSSEMessage(overrides: Partial<AnySSEFrame>): AnySSEFrame {
  const type = overrides.type ?? 'heartbeat'
  const data = { ...(overrides.data as Record<string, unknown> | undefined) }
  if (
    (type === 'hitl_request' || type === 'hitl_response') &&
    data.client_request_id == null
  ) {
    data.client_request_id = 'req-hitl-test-default'
  }
  if (
    (type === 'hitl_request' || type === 'hitl_response') &&
    typeof data.client_request_id === 'string'
  ) {
    resolveClientRequestMessageId(
      data.client_request_id,
      typeof data.related_message_id === 'string' ? data.related_message_id : 'user-hitl-test-default',
    )
  }
  return {
    type,
    room_id: 'room-1',
    timestamp: new Date().toISOString(),
    ...overrides,
    data: Object.keys(data).length > 0 ? data : (overrides.data ?? {}),
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

describe('useRoomWebhook HITL SSE handling', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (globalThis as any).capturedOnMessage = undefined;
    (globalThis as any).mockSseConnected = true;
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

  describe('hitl_request', () => {
    it('creates a message entity with HITL fields', async () => {
      await mountHook()
      expect(capturedOnMessage).toBeDefined()

      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'hitl_request',
          data: {
            request_id: 'req-1',
            message_id: 'msg-hitl-1',
            agent_id: 'agent-42',
            agent_name: 'Research Agent',
            content: 'Which date range should I search?',
            prompt: 'Which date range should I search?',
            prompt_type: 'text',
            choices: undefined,
            step_number: 2,
            total_steps: 5,
          },
        }))
      })

      const entity = useMessageStore.getState().entities['msg-hitl-1']
      expect(entity).toBeDefined()
      expect(entity.hitlRequestId).toBe('req-1')
      expect(entity.hitlPrompt).toBe('Which date range should I search?')
      expect(entity.hitlPromptType).toBe('text')
      expect(entity.hitlResolved).toBe(false)
      expect(entity.taskStatus).toBe('input-required')
      expect(entity.senderName).toBe('Research Agent')
      expect(entity.stepNumber).toBe(2)
      expect(entity.totalSteps).toBe(5)
    })

    it('handles choice prompt type with choices array', async () => {
      await mountHook()

      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'hitl_request',
          data: {
            request_id: 'req-choice',
            message_id: 'msg-hitl-choice',
            agent_name: 'Agent',
            content: 'Pick one',
            prompt: 'Pick one',
            prompt_type: 'choice',
            choices: ['Option A', 'Option B'],
          },
        }))
      })

      const entity = useMessageStore.getState().entities['msg-hitl-choice']
      expect(entity).toBeDefined()
      expect(entity.hitlPromptType).toBe('choice')
      expect(entity.hitlChoices).toEqual(['Option A', 'Option B'])
    })

    it('applies durable hitl_request events without client_request_id', async () => {
      await mountHook()
      expect(capturedOnMessage).toBeDefined()

      await act(async () => {
        await capturedOnMessage!({
          type: 'hitl_request',
          room_id: 'room-1',
          timestamp: new Date().toISOString(),
          data: {
            request_id: 'req-no-client-id',
            message_id: 'agent-msg-no-client-id',
            prompt: 'Please provide more information',
            prompt_type: 'text',
            source: 'agent',
            agent_id: 'agent-1',
            agent_name: 'Broker',
            related_message_id: 'user-msg-1',
          },
        } as AnySSEFrame)
      })

      const entity = useMessageStore.getState().entities['agent-msg-no-client-id']
      expect(entity).toBeDefined()
      expect(entity?.hitlRequestId).toBe('req-no-client-id')
      expect(entity?.clientRequestId).toBeUndefined()
      expect(entity?.taskStatus).toBe('input-required')
    })

    it('applies durable hitl_request events even when client_request_id is no longer locally resolved', async () => {
      await mountHook()
      expect(capturedOnMessage).toBeDefined()

      await act(async () => {
        await capturedOnMessage!({
          type: 'hitl_request',
          room_id: 'room-1',
          timestamp: new Date().toISOString(),
          data: {
            request_id: 'req-stale-client-id',
            message_id: 'agent-msg-stale-client-id',
            client_request_id: 'cr-already-cleared',
            prompt: 'Please provide more information',
            prompt_type: 'text',
            source: 'agent',
            agent_id: 'agent-1',
            agent_name: 'Broker',
            related_message_id: 'user-msg-1',
          },
        } as AnySSEFrame)
      })

      const entity = useMessageStore.getState().entities['agent-msg-stale-client-id']
      expect(entity).toBeDefined()
      expect(entity?.hitlRequestId).toBe('req-stale-client-id')
      expect(entity?.clientRequestId).toBe('cr-already-cleared')
      expect(entity?.taskStatus).toBe('input-required')
    })

    it('ignores hitl_request without request_id', async () => {
      await mountHook()
      const countBefore = useMessageStore.getState().orderedIds.length

      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'hitl_request',
          data: {
            message_id: 'msg-no-req',
            content: 'test',
          },
        }))
      })

      expect(useMessageStore.getState().orderedIds.length).toBe(countBefore)
    })
  })

  describe('hitl_response', () => {
    async function setupHitlRequest() {
      await mountHook()

      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'hitl_request',
          data: {
            request_id: 'req-status-1',
            message_id: 'msg-status-1',
            agent_name: 'Agent',
            content: 'Need input',
            prompt: 'Need input',
            prompt_type: 'text',
          },
        }))
      })

      const before = useMessageStore.getState().entities['msg-status-1']
      expect(before).toBeDefined()
      expect(before.hitlResolved).toBe(false)
    }

    it('marks entity as resolved on "responded" status', async () => {
      await setupHitlRequest()

      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'hitl_response',
          data: {
            request_id: 'req-status-1',
            status: 'responded',
          },
        }))
      })

      const entity = useMessageStore.getState().entities['msg-status-1']
      expect(entity.hitlResolved).toBe(true)
    })

    it('transitions to failed on "expired" status', async () => {
      await setupHitlRequest()

      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'hitl_response',
          data: {
            request_id: 'req-status-1',
            status: 'expired',
            error_message: 'Request timed out',
          },
        }))
      })

      const entity = useMessageStore.getState().entities['msg-status-1']
      expect(entity.hitlResolved).toBe(true)
      expect(entity.taskStatus).toBe('failed')
      expect(entity.taskError).toBe('Request timed out')
    })

    it('transitions to canceled on "canceled" status', async () => {
      await setupHitlRequest()

      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'hitl_response',
          data: {
            request_id: 'req-status-1',
            status: 'canceled',
          },
        }))
      })

      const entity = useMessageStore.getState().entities['msg-status-1']
      expect(entity.hitlResolved).toBe(true)
      expect(entity.taskStatus).toBe('canceled')
      expect(entity.taskError).toBe('Request canceled')
    })

    it('ignores hitl_response for unknown request_id', async () => {
      await mountHook()

      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'hitl_response',
          data: {
            request_id: 'unknown-req',
            status: 'responded',
          },
        }))
      })

      // No crash, no new entities
      const store = useMessageStore.getState()
      expect(Object.keys(store.entities).length).toBe(0)
    })

    it('applies durable hitl_response by message_id when request index is empty', async () => {
      await mountHook()

      act(() => {
        useMessageStore.getState().upsertMessage({
          id: 'agent-msg-response-fallback',
          roomId: 'room-1',
          messageType: 'agent',
          content: 'Need revenue',
          senderName: 'Broker',
          timestamp: new Date().toISOString(),
          taskStatus: 'input-required',
          hitlRequestId: 'req-response-fallback',
          hitlPrompt: 'Need revenue',
          hitlPromptType: 'text',
          hitlResolved: false,
        }, 'sse')
      })

      await act(async () => {
        await capturedOnMessage!({
          type: 'hitl_response',
          room_id: 'room-1',
          timestamp: new Date().toISOString(),
          data: {
            request_id: 'req-response-fallback',
            message_id: 'agent-msg-response-fallback',
            source: 'agent',
            status: 'responded',
          },
        } as AnySSEFrame)
      })

      const entity = useMessageStore.getState().entities['agent-msg-response-fallback']
      expect(entity?.hitlResolved).toBe(true)
      expect(entity?.taskStatus).toBe('input-required')
    })

    it('keeps form open on "error" status (backend reverts to pending)', async () => {
      await setupHitlRequest()

      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'hitl_response',
          data: {
            request_id: 'req-status-1',
            status: 'error',
            error_message: 'Failed to deliver response to agent',
          },
        }))
      })

      const entity = useMessageStore.getState().entities['msg-status-1']
      expect(entity.hitlResolved).toBe(false)
      expect(entity.taskStatus).toBe('input-required')
      expect(entity.taskError).toBe('Failed to deliver response to agent')
    })
  })

  describe('HITL lifecycle: request → response → status update', () => {
    it('full cycle: request, then respond, then status update removes form state', async () => {
      await mountHook()

      // Step 1: HITL request arrives
      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'hitl_request',
          data: {
            request_id: 'req-full',
            message_id: 'msg-full',
            agent_name: 'Orchestrator',
            content: 'Confirm deployment?',
            prompt: 'Confirm deployment?',
            prompt_type: 'confirmation',
          },
        }))
      })

      let entity = useMessageStore.getState().entities['msg-full']
      expect(entity.hitlRequestId).toBe('req-full')
      expect(entity.hitlPromptType).toBe('confirmation')
      expect(entity.hitlResolved).toBe(false)
      expect(entity.taskStatus).toBe('input-required')

      // Step 2: User responds (optimistic update simulated)
      const store = useMessageStore.getState()
      store.upsertMessage({
        id: 'msg-full',
        roomId: 'room-1',
        messageType: 'agent',
        content: 'Confirm deployment?',
        senderName: 'Orchestrator',
        timestamp: entity.timestamp,
        hitlResolved: true,
      }, 'optimistic')

      entity = useMessageStore.getState().entities['msg-full']
      expect(entity.hitlResolved).toBe(true)

      // Step 3: Backend confirms via hitl_response
      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'hitl_response',
          data: {
            request_id: 'req-full',
            status: 'responded',
          },
        }))
      })

      entity = useMessageStore.getState().entities['msg-full']
      expect(entity.hitlResolved).toBe(true)
    })

    it('skips stale status_update when entity has been claimed by a newer request', async () => {
      await mountHook()

      // 1st HITL request on message_id 'msg-shared'
      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'hitl_request',
          data: {
            request_id: 'req-old',
            message_id: 'msg-shared',
            agent_name: 'Supervisor',
            content: 'First question?',
            prompt: 'First question?',
            prompt_type: 'text',
          },
        }))
      })

      let entity = useMessageStore.getState().entities['msg-shared']
      expect(entity.hitlRequestId).toBe('req-old')
      expect(entity.hitlResolved).toBe(false)

      // 2nd HITL request reuses same message_id with a new request_id
      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'hitl_request',
          data: {
            request_id: 'req-new',
            message_id: 'msg-shared',
            agent_name: 'Supervisor',
            content: 'Follow-up question?',
            prompt: 'Follow-up question?',
            prompt_type: 'text',
          },
        }))
      })

      entity = useMessageStore.getState().entities['msg-shared']
      expect(entity.hitlRequestId).toBe('req-new')
      expect(entity.hitlResolved).toBe(false)
      expect(entity.hitlPrompt).toBe('Follow-up question?')

      // Stale status_update for 1st request arrives — should NOT hide the form
      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'hitl_response',
          data: {
            request_id: 'req-old',
            status: 'responded',
          },
        }))
      })

      entity = useMessageStore.getState().entities['msg-shared']
      expect(entity.hitlResolved).toBe(false)
      expect(entity.hitlRequestId).toBe('req-new')
      expect(entity.hitlPrompt).toBe('Follow-up question?')
    })
  })

  describe('expiry during user typing', () => {
    it('hitl_response expired replaces form with error state', async () => {
      await mountHook()

      // HITL request arrives
      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'hitl_request',
          data: {
            request_id: 'req-expire',
            message_id: 'msg-expire',
            agent_name: 'Agent',
            content: 'Enter data',
            prompt: 'Enter data',
            prompt_type: 'text',
          },
        }))
      })

      // User is "typing" (not yet submitted) when expiry arrives
      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'hitl_response',
          data: {
            request_id: 'req-expire',
            status: 'expired',
            error_message: 'Timed out waiting for input',
          },
        }))
      })

      const entity = useMessageStore.getState().entities['msg-expire']
      expect(entity.hitlResolved).toBe(true)
      expect(entity.taskStatus).toBe('failed')
      expect(entity.taskError).toBe('Timed out waiting for input')
    })
  })

  describe('respondToHitlBatch', () => {
    it('correlates the resumed processing log after an applied batch', async () => {
      const hook = await mountHook()
      useMessageStore.getState().upsertMessage({
        id: 'user-batch-root',
        roomId: 'room-1',
        messageType: 'user',
        content: 'Need details',
        senderName: 'Test',
        timestamp: '2026-06-04T01:00:00.000Z',
        clientRequestId: 'client-batch',
        processingStatusLogs: [
          createInitialProcessingStatusLog('2026-06-04T01:00:00.001Z'),
        ],
      }, 'db')
      for (const [index, requestId] of ['req-batch-1', 'req-batch-2'].entries()) {
        useMessageStore.getState().upsertMessage({
          id: `hitl-batch-${index}`,
          roomId: 'room-1',
          messageType: 'agent',
          content: `Question ${index + 1}`,
          senderName: 'HYBRO AI',
          timestamp: `2026-06-04T01:00:0${index + 1}.000Z`,
          relatedMessageId: 'user-batch-root',
          clientRequestId: 'client-batch',
          hitlRequestId: requestId,
          hitlInteractionId: 'interaction-batch',
          hitlPrompt: `Question ${index + 1}`,
          hitlPromptType: 'text',
          hitlResolved: false,
        }, 'db')
      }

      await act(async () => {
        await hook.result.current.respondToHitlBatch(
          'interaction-batch',
          [
            { requestId: 'req-batch-1', answer: 'First' },
            { requestId: 'req-batch-2', answer: 'Second' },
          ],
          'client-batch',
        )
      })

      const user = useMessageStore.getState().entities['user-batch-root']
      expect(user.processingStatusLogs?.map(entry => entry.message)).toEqual([
        'Applying your answers…',
      ])
      expect(useRoomUiStore.getState().getRoomFlags('room-1').processing).toBe(true)
      expect(useMessageStore.getState().entities['hitl-batch-0'].hitlResolved).toBe(true)
      expect(useMessageStore.getState().entities['hitl-batch-1'].hitlResolved).toBe(true)
    })

    it.each([409, 410])('reconciles batch response status %s', async status => {
      const hook = await mountHook()
      const hitlApi = await import('@/lib/api/hitl')
      vi.mocked(hitlApi.respondToHitlBatch).mockRejectedValueOnce(
        new ApiError(status, 'Batch changed'),
      )
      const roomApi = await import('@/lib/api/room')
      const reconcile = vi.mocked(roomApi.inquiryRoomMessagesByRoomId)
      reconcile.mockClear()

      await expect(hook.result.current.respondToHitlBatch(
        'interaction-batch',
        [{ requestId: 'req-batch-1', answer: 'First' }],
        'client-batch',
      )).rejects.toMatchObject({ status })

      expect(reconcile).toHaveBeenCalled()
    })
  })

  describe('SSE reconnect catch-up', () => {
    it('restores pending HITL requests when SSE reconnects', async () => {
      const { fetchPendingHitlRequests } = await import('@/lib/api/hitl')
      const mockFetch = vi.mocked(fetchPendingHitlRequests)
      mockFetch.mockResolvedValueOnce({
        requests: [
          {
            request_id: 'req-reconnect',
            message_id: 'msg-reconnect',
            source: 'agent' as const,
            agent_id: 'agent-99',
            agent_name: 'Reconnect Agent',
            prompt: 'What year?',
            prompt_type: 'text' as const,
            choices: null,
            status: 'pending' as const,
            expires_at: '2026-12-31T00:00:00Z',
            created_at: '2026-01-01T00:00:00Z',
          },
        ],
      })

      // Start disconnected so the reconnect transition triggers the catch-up
      ;(globalThis as any).mockSseConnected = false
      const { rerender } = await mountHook()

      // Simulate SSE reconnecting
      ;(globalThis as any).mockSseConnected = true
      await act(async () => {
        rerender()
      })

      // Give the async fetch time to resolve
      await act(async () => {
        await new Promise(r => setTimeout(r, 50))
      })

      expect(mockFetch).toHaveBeenCalledWith('room-1', expect.any(Function))

      const entity = useMessageStore.getState().entities['msg-reconnect']
      expect(entity).toBeDefined()
      expect(entity.hitlRequestId).toBe('req-reconnect')
      expect(entity.hitlPrompt).toBe('What year?')
      expect(entity.hitlPromptType).toBe('text')
      expect(entity.hitlResolved).toBe(false)
      expect(entity.taskStatus).toBe('input-required')
      expect(entity.senderName).toBe('Reconnect Agent')
      expect(entity.relatedMessageId).toBeUndefined()
      expect(entity.hitlChoices).toBeNull()

      // Verify hitlRequestIndex was populated by sending a status update
      // for the reconnected request — if the index is missing, this would be a no-op.
      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'hitl_response',
          data: { request_id: 'req-reconnect', status: 'responded' },
        }))
      })

      const updated = useMessageStore.getState().entities['msg-reconnect']
      expect(updated.hitlResolved).toBe(true)
    })

    it('handles empty pending requests gracefully on reconnect', async () => {
      const { fetchPendingHitlRequests } = await import('@/lib/api/hitl')
      const mockFetch = vi.mocked(fetchPendingHitlRequests)
      mockFetch.mockResolvedValueOnce({ requests: [] })

      ;(globalThis as any).mockSseConnected = false
      const { rerender } = await mountHook()

      ;(globalThis as any).mockSseConnected = true
      await act(async () => {
        rerender()
      })

      await act(async () => {
        await new Promise(r => setTimeout(r, 50))
      })

      expect(mockFetch).toHaveBeenCalled()
      expect(Object.keys(useMessageStore.getState().entities).length).toBe(0)
    })

    it('handles fetch failure on reconnect without crashing', async () => {
      const { fetchPendingHitlRequests } = await import('@/lib/api/hitl')
      const mockFetch = vi.mocked(fetchPendingHitlRequests)
      mockFetch.mockRejectedValueOnce(new Error('Network failure'))

      ;(globalThis as any).mockSseConnected = false
      const { rerender } = await mountHook()

      ;(globalThis as any).mockSseConnected = true
      await act(async () => {
        rerender()
      })

      await act(async () => {
        await new Promise(r => setTimeout(r, 50))
      })

      // No crash, no entities created
      expect(Object.keys(useMessageStore.getState().entities).length).toBe(0)
    })
  })

})
