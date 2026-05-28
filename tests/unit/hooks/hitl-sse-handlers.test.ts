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
import type { SSEMessage } from '@/lib/types/sse'
import { resetPendingTurnBufferForTests } from '@/hooks/room/sse-handlers/pending-turn-buffer'

let capturedOnMessage: ((msg: SSEMessage) => void) | undefined
let mockSseConnected = true

vi.mock('@/hooks/useRoomSSE', () => ({
  useRoomSSE: vi.fn((opts: { onMessage?: (msg: SSEMessage) => void }) => {
    capturedOnMessage = opts.onMessage
    return { connected: mockSseConnected, connecting: false, error: null }
  }),
}))

vi.mock('@clerk/nextjs', () => ({
  useUser: () => ({ user: { id: 'u1', firstName: 'Test' }, isLoaded: true }),
  useAuth: () => ({ getToken: async () => 'token' }),
  useClerk: () => ({ openWaitlist: vi.fn() }),
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
  respondToHitl: vi.fn().mockResolvedValue({ status: 'ok', request_id: 'req-1' }),
  fetchPendingHitlRequests: vi.fn().mockResolvedValue({ requests: [] }),
}))

vi.mock('@/components/ui/banner', () => ({
  banner: { info: vi.fn(), error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}))

function makeSSEMessage(overrides: Partial<SSEMessage>): SSEMessage {
  const type = overrides.type ?? 'heartbeat'
  const data = { ...(overrides.data as Record<string, unknown> | undefined) }
  if (
    (type === 'hitl_input_requested' || type === 'hitl_status_update') &&
    data.client_request_id == null
  ) {
    data.client_request_id = 'req-hitl-test-default'
  }
  return {
    type,
    room_id: 'room-1',
    timestamp: new Date().toISOString(),
    ...overrides,
    data: Object.keys(data).length > 0 ? data : overrides.data,
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
    vi.clearAllMocks()
    capturedOnMessage = undefined
    mockSseConnected = true
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

  describe('hitl_input_requested', () => {
    it('creates a message entity with HITL fields', async () => {
      await mountHook()
      expect(capturedOnMessage).toBeDefined()

      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'hitl_input_requested',
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
          type: 'hitl_input_requested',
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

    it('ignores hitl_input_requested without request_id', async () => {
      await mountHook()
      const countBefore = useMessageStore.getState().orderedIds.length

      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'hitl_input_requested',
          data: {
            message_id: 'msg-no-req',
            content: 'test',
          },
        }))
      })

      expect(useMessageStore.getState().orderedIds.length).toBe(countBefore)
    })
  })

  describe('hitl_status_update', () => {
    async function setupHitlRequest() {
      await mountHook()

      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'hitl_input_requested',
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
          type: 'hitl_status_update',
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
          type: 'hitl_status_update',
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
          type: 'hitl_status_update',
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

    it('ignores hitl_status_update for unknown request_id', async () => {
      await mountHook()

      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'hitl_status_update',
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

    it('keeps form open on "error" status (backend reverts to pending)', async () => {
      await setupHitlRequest()

      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'hitl_status_update',
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
          type: 'hitl_input_requested',
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

      // Step 3: Backend confirms via hitl_status_update
      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'hitl_status_update',
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
          type: 'hitl_input_requested',
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
          type: 'hitl_input_requested',
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
          type: 'hitl_status_update',
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
    it('hitl_status_update expired replaces form with error state', async () => {
      await mountHook()

      // HITL request arrives
      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'hitl_input_requested',
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
          type: 'hitl_status_update',
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
      mockSseConnected = false
      const { rerender } = await mountHook()

      // Simulate SSE reconnecting
      mockSseConnected = true
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

      // Verify hitlRequestIndex was populated by sending a status update
      // for the reconnected request — if the index is missing, this would be a no-op.
      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'hitl_status_update',
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

      mockSseConnected = false
      const { rerender } = await mountHook()

      mockSseConnected = true
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

      mockSseConnected = false
      const { rerender } = await mountHook()

      mockSseConnected = true
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

  describe('respondToHitlRequest optimistic revert', () => {
    it('reverts hitlResolved on API failure', async () => {
      // Override the hitl mock for this test before mounting
      const hitlMod = await import('@/lib/api/hitl')
      const mockRespond = vi.mocked(hitlMod.respondToHitl)

      const hookResult = await mountHook()

      // Seed a HITL entity via SSE
      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'hitl_input_requested',
          data: {
            request_id: 'req-revert',
            message_id: 'msg-revert',
            prompt: 'Pick a date',
            prompt_type: 'text',
            agent_name: 'Agent',
          },
        }))
      })

      const entityBefore = useMessageStore.getState().entities['msg-revert']
      expect(entityBefore.hitlResolved).toBe(false)

      // Make the API call fail
      mockRespond.mockRejectedValueOnce(new Error('Server error'))

      // Call respondToHitlRequest — should set optimistic then revert
      let caughtError: Error | null = null
      await act(async () => {
        try {
          await hookResult.result.current.respondToHitlRequest('req-revert', 'my reply')
        } catch (e) {
          caughtError = e as Error
        }
      })

      expect(caughtError).not.toBeNull()
      expect((caughtError as unknown as Error).message).toBe('Server error')

      // Entity should be reverted to hitlResolved: false
      const entityAfter = useMessageStore.getState().entities['msg-revert']
      expect(entityAfter.hitlResolved).toBe(false)
    })

    it('keeps hitlResolved true on API success', async () => {
      const hookResult = await mountHook()

      // Seed a HITL entity via SSE
      await act(async () => {
        await capturedOnMessage!(makeSSEMessage({
          type: 'hitl_input_requested',
          data: {
            request_id: 'req-success',
            message_id: 'msg-success',
            prompt: 'Confirm?',
            prompt_type: 'confirmation',
            agent_name: 'Agent',
          },
        }))
      })

      // Call respondToHitlRequest — API mock defaults to success
      await act(async () => {
        await hookResult.result.current.respondToHitlRequest('req-success', 'approved')
      })

      const entity = useMessageStore.getState().entities['msg-success']
      expect(entity.hitlResolved).toBe(true)
    })
  })
})
