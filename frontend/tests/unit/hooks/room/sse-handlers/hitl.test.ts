import { beforeEach, describe, expect, it, vi } from 'vitest'
import { handleHitlRequest, handleHitlResponse } from '@/hooks/room/sse-handlers/handlers/hitl'
import type { ProcessingLifecycle } from '@/hooks/room/processing-lifecycle'
import { useMessageStore } from '@/stores/message-store'

function makeLifecycle(): ProcessingLifecycle {
  return {
    placeholderId: () => 'placeholder-room-1',
    getPendingRunEventAck: () => 'current-cr',
    getMessageId: () => 'current-user',
    dismissPlaceholder: vi.fn(),
    markProcessingResolved: vi.fn(),
    stopProcessing: vi.fn(),
  } as unknown as ProcessingLifecycle
}

describe('handleHitlRequest', () => {
  beforeEach(() => {
    useMessageStore.setState({ entities: {}, orderedIds: [], roomId: 'room-1' })
  })

  it('does not clear the active processing placeholder for a stale durable HITL event', async () => {
    const lifecycle = makeLifecycle()
    const timestamp = new Date().toISOString()

    useMessageStore.getState().upsertMany(
      [
        {
          id: 'placeholder-room-1',
          roomId: 'room-1',
          messageType: 'agent',
          content: 'Working...',
          senderName: 'Hybro',
          timestamp,
        },
        {
          id: 'current-user',
          roomId: 'room-1',
          messageType: 'user',
          content: 'current turn',
          senderName: 'User',
          timestamp,
          clientRequestId: 'current-cr',
          processingStatusLogs: [
            {
              id: 'log-1',
              message: 'Thinking...',
              timestamp,
            },
          ],
        },
      ],
      'sse',
    )

    await handleHitlRequest(
      {
        roomId: 'room-1',
        lifecycle,
        getAgentName: async () => 'Agent',
        getAgentSource: () => undefined,
        reconcileWithDb: async () => {},
        hitlRequestIndex: { current: new Map() },
        setCancelling: () => {},
      },
      {
        type: 'hitl_request',
        room_id: 'room-1',
        timestamp,
        data: {
          request_id: 'old-hitl',
          message_id: 'old-agent-message',
          client_request_id: 'old-cr',
          related_message_id: 'old-user',
          source: 'agent',
          agent_id: 'agent-1',
          agent_name: 'Agent',
          prompt: 'Need stale input',
          prompt_type: 'text',
          question_count: 1,
          question_index: 0,
        },
      },
      'req-1',
    )

    expect(useMessageStore.getState().entities['placeholder-room-1']).toBeDefined()
    expect(lifecycle.dismissPlaceholder).not.toHaveBeenCalled()
    expect(lifecycle.markProcessingResolved).not.toHaveBeenCalled()
    expect(lifecycle.stopProcessing).not.toHaveBeenCalled()
    expect(useMessageStore.getState().entities['old-agent-message']).toMatchObject({
      hitlRequestId: 'old-hitl',
      taskStatus: 'input-required',
    })
  })

  it('preserves auth semantics and same-request nonterminal transitions', async () => {
    const lifecycle = makeLifecycle()
    const timestamp = new Date().toISOString()
    const index = { current: new Map<string, string>() }
    const ctx = {
      roomId: 'room-1',
      lifecycle,
      getAgentName: async () => 'Auth Agent',
      getAgentSource: () => undefined,
      reconcileWithDb: async () => {},
      hitlRequestIndex: index,
      setCancelling: () => {},
    }

    await handleHitlRequest(ctx, {
      type: 'hitl_request',
      room_id: 'room-1',
      timestamp,
      data: {
        request_id: 'auth-hitl',
        message_id: 'auth-message',
        client_request_id: 'client-1',
        source: 'agent',
        prompt: 'Authenticate with the carrier',
        prompt_type: 'authentication',
        interaction_id: 'interaction-1',
        interaction_status: 'open',
        question_count: 1,
        question_index: 0,
      },
    }, 'req-1')

    expect(useMessageStore.getState().entities['auth-message']).toMatchObject({
      hitlPromptType: 'authentication',
      clientRequestId: 'client-1',
      hitlResolved: false,
    })

    handleHitlResponse(ctx, {
      type: 'hitl_response',
      room_id: 'room-1',
      timestamp,
      data: {
        request_id: 'auth-hitl',
        message_id: 'auth-message',
        source: 'agent',
        status: 'answer_recorded',
        interaction_id: 'interaction-1',
        interaction_status: 'applying',
        application_status: 'applying',
        question_count: 1,
        question_index: 0,
        client_request_id: 'client-1',
      },
    }, 'req-1')

    expect(useMessageStore.getState().entities['auth-message']).toMatchObject({
      hitlResolved: false,
      hitlInteractionStatus: 'applying',
      hitlApplicationStatus: 'applying',
      clientRequestId: 'client-1',
    })
    expect(index.current.get('auth-hitl')).toBe('auth-message')

    handleHitlResponse(ctx, {
      type: 'hitl_response',
      room_id: 'room-1',
      timestamp,
      data: {
        request_id: 'auth-hitl',
        message_id: 'auth-message',
        source: 'agent',
        status: 'responded',
        interaction_id: 'interaction-1',
        interaction_status: 'applied',
        application_status: 'applied',
        question_count: 1,
        question_index: 0,
        client_request_id: 'client-1',
      },
    }, 'req-1')

    expect(useMessageStore.getState().entities['auth-message'].hitlResolved).toBe(true)
    expect(index.current.has('auth-hitl')).toBe(false)
  })
})
