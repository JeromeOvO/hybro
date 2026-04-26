import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { SSEMessage } from '@/lib/types/sse'
import {
  clearPendingSseForClientRequest,
  enqueuePendingSseEvent,
  flushPendingSseEvents,
  getResolvedMessageId,
} from '@/hooks/room/sse-handlers/pending-turn-buffer'

function mkEvent(type: SSEMessage['type'], data: Record<string, unknown> = {}): SSEMessage {
  return {
    type,
    room_id: 'room-1',
    timestamp: new Date().toISOString(),
    data,
  }
}

describe('pending-turn-buffer', () => {
  beforeEach(() => {
    clearPendingSseForClientRequest('req-order')
    clearPendingSseForClientRequest('req-replay')
    clearPendingSseForClientRequest('req-rollback')
  })

  it('flushes buffered events in arrival order', async () => {
    enqueuePendingSseEvent('req-order', mkEvent('task_submitted', { n: 1 }))
    enqueuePendingSseEvent('req-order', mkEvent('task_update', { n: 2 }))
    enqueuePendingSseEvent('req-order', mkEvent('artifact_update', { n: 3 }))

    const calls: Array<{ type: string }> = []
    const dispatch = vi.fn(async (event: SSEMessage) => {
      calls.push({ type: event.type })
    })

    await flushPendingSseEvents('req-order', dispatch, 'msg-real-order')

    expect(calls).toEqual([
      { type: 'task_submitted' },
      { type: 'task_update' },
      { type: 'artifact_update' },
    ])
    expect(getResolvedMessageId('req-order')).toBe('msg-real-order')
  })

  it('is replay-idempotent: second flush after drain dispatches nothing', async () => {
    enqueuePendingSseEvent('req-replay', mkEvent('task_submitted', { n: 1 }))

    const dispatch = vi.fn(async () => {})
    await flushPendingSseEvents('req-replay', dispatch, 'msg-real-replay')
    await flushPendingSseEvents('req-replay', dispatch, 'msg-real-replay')

    expect(dispatch).toHaveBeenCalledTimes(1)
  })

  it('rollback clear drops buffered events before flush', async () => {
    enqueuePendingSseEvent('req-rollback', mkEvent('task_submitted', { n: 1 }))
    enqueuePendingSseEvent('req-rollback', mkEvent('task_update', { n: 2 }))

    // Simulate send failure rollback path.
    clearPendingSseForClientRequest('req-rollback')

    const dispatch = vi.fn(async () => {})
    await flushPendingSseEvents('req-rollback', dispatch, 'msg-never-used')

    expect(dispatch).not.toHaveBeenCalled()
    // Resolution map is still set by flush, but no buffered side effects occur.
    expect(getResolvedMessageId('req-rollback')).toBe('msg-never-used')
  })
})
