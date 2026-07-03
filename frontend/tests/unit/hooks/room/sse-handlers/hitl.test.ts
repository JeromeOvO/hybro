import { beforeEach, describe, expect, it, vi } from 'vitest'
import { handleHitlRequest } from '@/hooks/room/sse-handlers/handlers/hitl'
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
        },
      },
      { shouldDrop: false, shouldBuffer: false },
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
})
