import { beforeEach, describe, expect, it, vi } from 'vitest'
import { handleTaskUpdate } from '../handlers/task-update'
import { createProcessingLifecycle } from '../../processing-lifecycle'
import { TASK_STATE } from '@/lib/types/sse'
import { useMessageStore } from '@/stores/message-store'
import type { RoomSSEFrameMap } from '@/lib/types/sse'
import type { SSEHandlerDeps } from '../types'

vi.mock('@/components/ui/banner', () => ({
  banner: {
    error: vi.fn(),
  },
}))

vi.mock('@/lib/room-timeline/event-log', () => ({
  appendEvent: vi.fn(),
}))

vi.mock('@/lib/room-timeline/stamp-live-turn-terminal', () => ({
  stampLiveTurnTerminalIfInferable: vi.fn(() => true),
}))

vi.mock('@/lib/room-timeline/turn-terminal-stamp', () => ({
  buildTurnForRecoveryHint: vi.fn(),
  scheduleTurnTerminalBackendTruthCheck: vi.fn(),
  shouldScheduleTurnTerminalRecovery: vi.fn(() => false),
}))

function makeDeps(): SSEHandlerDeps {
  const lifecycle = createProcessingLifecycle(() => {})
  return {
    roomId: 'room-1',
    lifecycle,
    getAgentName: vi.fn().mockResolvedValue('Agent One'),
    getAgentSource: vi.fn(() => 'cloud'),
    reconcileWithDb: vi.fn(),
    hitlRequestIndex: { current: new Map() },
    setCancelling: vi.fn(),
  }
}

function makeTaskUpdate(
  data: Partial<RoomSSEFrameMap['task_update']['data']>,
): RoomSSEFrameMap['task_update'] {
  return {
    type: 'task_update',
    timestamp: '2026-02-17T10:00:00Z',
    room_id: 'room-1',
    data: {
      message_id: 'agent-message-1',
      status: TASK_STATE.COMPLETED,
      client_request_id: 'client-request-1',
      ...data,
    },
  }
}

describe('handleTaskUpdate', () => {
  beforeEach(() => {
    useMessageStore.getState().clearRoom()
    useMessageStore.getState().setRoom('room-1')
  })

  it('does not store arbitrary completed status_message as taskStatusMessage', async () => {
    const privateSentinel = 'PRIVATE_SENTINEL_completed_sse_status_message'

    await handleTaskUpdate(
      makeDeps(),
      makeTaskUpdate({
        status_message: privateSentinel,
        content: '',
      }),
      { shouldDrop: false, shouldBuffer: false },
    )

    const entity = useMessageStore.getState().entities['agent-message-1']
    expect(entity.taskStatus).toBe(TASK_STATE.COMPLETED)
    expect(entity.taskStatusMessage).toBeUndefined()
    expect(JSON.stringify(entity)).not.toContain(privateSentinel)
  })

  it('keeps known safe local status_message labels for non-terminal tasks', async () => {
    await handleTaskUpdate(
      makeDeps(),
      makeTaskUpdate({
        status: TASK_STATE.AUTH_REQUIRED,
        status_message: 'Authentication required',
      }),
      { shouldDrop: false, shouldBuffer: false },
    )

    const entity = useMessageStore.getState().entities['agent-message-1']
    expect(entity.taskStatus).toBe(TASK_STATE.AUTH_REQUIRED)
    expect(entity.taskStatusMessage).toBe('Authentication required')
  })
})
