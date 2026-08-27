import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createSSEDispatcher } from '@/hooks/room/sse-handlers/dispatch'
import { createProcessingLifecycle } from '@/hooks/room/processing-lifecycle'
import { useMessageStore } from '@/stores/message-store'
import { useTurnStore } from '@/stores/turn-store'
import type { AnySSEFrame } from '@/lib/types/sse'

const timestamp = '2030-01-01T00:00:00.000Z'

function dispatcher() {
  return createSSEDispatcher({
    roomId: 'room-1',
    lifecycle: createProcessingLifecycle(() => {}),
    getAgentName: vi.fn().mockResolvedValue('Weather Agent'),
    getAgentSource: vi.fn(),
    reconcileWithDb: vi.fn(),
    hitlRequestIndex: { current: new Map<string, string>() },
    setCancelling: vi.fn(),
    requestSnapshotRef: { current: vi.fn() },
  })
}

describe('canonical dispatcher exact-root boundary', () => {
  beforeEach(() => {
    useMessageStore.getState().clearRoom()
    useMessageStore.getState().setRoom('room-1')
    useTurnStore.getState().clear()
  })

  it('does not suppress an unrelated legacy task merely because the room has canonical capability', async () => {
    const dispatch = dispatcher()
    await dispatch({
      type: 'connected', room_id: 'room-1', timestamp,
      data: { connection_id: 'connection-1', room_seq: 0 },
    } as AnySSEFrame)
    await dispatch({
      type: 'snapshot', room_id: 'room-1', timestamp,
      data: {
        room_seq: 0, messages: [], tasks: [], runs: [], streaming: {}, trace: {},
        hitl: { requests: [], resolved: [] }, turn_lifecycle_schema: 1, turns: [],
      },
    } as AnySSEFrame)
    await dispatch({
      type: 'task_submitted', room_id: 'room-1', timestamp,
      data: {
        room_seq: 1, room_event_id: 'legacy-task-1', message_id: 'legacy-agent-1',
        task_id: 'legacy-task', agent_name: 'Weather Agent', agent_id: 'agent-1',
        status: 'working', related_message_id: 'legacy-user-1',
        client_request_id: 'legacy-client-1',
      },
    } as AnySSEFrame)

    expect(useMessageStore.getState().entities['legacy-agent-1']).toMatchObject({
      senderName: 'Weather Agent', taskStatus: 'working',
      relatedMessageId: 'legacy-user-1', clientRequestId: 'legacy-client-1',
    })
  })
})
