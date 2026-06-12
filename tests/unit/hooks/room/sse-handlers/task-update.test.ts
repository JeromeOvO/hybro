import { beforeEach, describe, expect, it, vi } from 'vitest'
import { handleTaskUpdate } from '@/hooks/room/sse-handlers/handlers/task-update'
import { useMessageStore } from '@/stores/message-store'
import { TASK_STATE } from '@/lib/types/sse'
import type { ProcessingLifecycle } from '@/hooks/room/processing-lifecycle'

vi.mock('@/lib/room-timeline/stamp-live-turn-terminal', () => ({
  stampLiveTurnTerminalIfInferable: vi.fn(() => false),
}))

vi.mock('@/lib/room-timeline/turn-terminal-stamp', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/room-timeline/turn-terminal-stamp')>()
  return {
    ...actual,
    scheduleTurnTerminalBackendTruthCheck: vi.fn(),
  }
})

import { scheduleTurnTerminalBackendTruthCheck } from '@/lib/room-timeline/turn-terminal-stamp'

function makeLifecycle(): ProcessingLifecycle {
  return {
    placeholderId: () => 'placeholder-room-1',
    isPlaceholderDismissed: () => true,
    dismissPlaceholder: () => {},
    disarmCancelTimeout: () => {},
    hasCancelTimedOut: () => false,
  } as ProcessingLifecycle
}

describe('handleTaskUpdate terminal recovery', () => {
  beforeEach(() => {
    vi.mocked(scheduleTurnTerminalBackendTruthCheck).mockClear()
    useMessageStore.setState({ entities: {}, orderedIds: [], roomId: 'room-1' })
  })

  it('schedules backend truth check when agent task fails and stamp is not inferable', async () => {
    await handleTaskUpdate(
      {
        roomId: 'room-1',
        lifecycle: makeLifecycle(),
        getAgentName: async () => 'Agent',
        getAgentSource: () => undefined,
        setCancelling: () => {},
        getToken: async () => 'token',
      },
      {
        type: 'task_update',
        room_id: 'room-1',
        timestamp: new Date().toISOString(),
        data: {
          message_id: 'agent-1',
          client_request_id: 'cr-1',
          related_message_id: 'user-1',
          agent_id: 'agent-a',
          status: TASK_STATE.FAILED,
          error: 'Task failed',
        },
      },
      { shouldDrop: false, shouldBuffer: false, clientReqId: 'cr-1' },
    )

    expect(scheduleTurnTerminalBackendTruthCheck).toHaveBeenCalledWith(
      'room-1',
      expect.anything(),
      {
        clientRequestId: 'cr-1',
        relatedMessageId: 'user-1',
      },
      expect.any(Function),
    )
  })

  it('schedules backend truth check when all agents complete successfully', async () => {
    useMessageStore.getState().upsertMany([
      {
        id: 'user-1',
        roomId: 'room-1',
        messageType: 'user',
        content: 'hello',
        senderName: 'User',
        timestamp: '2026-01-01T00:00:00.000Z',
        clientRequestId: 'cr-1',
      },
      {
        id: 'agent-1',
        roomId: 'room-1',
        messageType: 'agent',
        content: 'A',
        senderName: 'Agent A',
        agentId: 'agent-a',
        relatedMessageId: 'user-1',
        clientRequestId: 'cr-1',
        taskStatus: 'completed',
        timestamp: '2026-01-01T00:00:01.000Z',
      },
      {
        id: 'agent-2',
        roomId: 'room-1',
        messageType: 'agent',
        content: 'B',
        senderName: 'Agent B',
        agentId: 'agent-b',
        relatedMessageId: 'user-1',
        clientRequestId: 'cr-1',
        taskStatus: 'completed',
        timestamp: '2026-01-01T00:00:02.000Z',
      },
    ])

    await handleTaskUpdate(
      {
        roomId: 'room-1',
        lifecycle: makeLifecycle(),
        getAgentName: async () => 'Agent',
        getAgentSource: () => undefined,
        setCancelling: () => {},
        getToken: async () => 'token',
      },
      {
        type: 'task_update',
        room_id: 'room-1',
        timestamp: new Date().toISOString(),
        data: {
          message_id: 'agent-2',
          client_request_id: 'cr-1',
          related_message_id: 'user-1',
          agent_id: 'agent-b',
          status: TASK_STATE.COMPLETED,
        },
      },
      { shouldDrop: false, shouldBuffer: false, clientReqId: 'cr-1' },
    )

    expect(scheduleTurnTerminalBackendTruthCheck).toHaveBeenCalledWith(
      'room-1',
      expect.anything(),
      {
        clientRequestId: 'cr-1',
        relatedMessageId: 'user-1',
      },
      expect.any(Function),
    )
  })
})
