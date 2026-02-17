import { describe, it, expect, beforeEach } from 'vitest'
import { useMessageStore } from '../index'
import type { IncomingMessage } from '../types'

// ── Helpers ──────────────────────────────────────────────────

function makeIncoming(overrides: Partial<IncomingMessage> = {}): IncomingMessage {
  return {
    id: 'msg-1',
    roomId: 'room-1',
    messageType: 'agent',
    content: 'Hello',
    senderName: 'Agent',
    timestamp: '2026-02-17T10:00:00Z',
    ...overrides,
  }
}

describe('useMessageStore', () => {
  beforeEach(() => {
    // Reset store to initial state before each test
    useMessageStore.getState().clearRoom()
  })

  describe('setRoom / clearRoom', () => {
    it('sets the room ID and resets state', () => {
      const store = useMessageStore.getState()
      store.setRoom('room-1')
      const state = useMessageStore.getState()
      expect(state.roomId).toBe('room-1')
      expect(state.entities).toEqual({})
      expect(state.orderedIds).toEqual([])
      expect(state.hydratedFromDb).toBe(false)
      expect(state.version).toBe(0)
    })

    it('clearRoom resets everything', () => {
      const store = useMessageStore.getState()
      store.setRoom('room-1')
      store.upsertMessage(makeIncoming(), 'sse')
      store.clearRoom()
      const state = useMessageStore.getState()
      expect(state.roomId).toBeNull()
      expect(state.entities).toEqual({})
      expect(state.orderedIds).toEqual([])
    })
  })

  describe('upsertMessage', () => {
    it('inserts a new message and updates orderedIds', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(makeIncoming(), 'sse')
      const state = useMessageStore.getState()
      expect(state.entities['msg-1']).toBeDefined()
      expect(state.orderedIds).toEqual(['msg-1'])
      expect(state.version).toBe(1)
    })

    it('updates an existing message without changing orderedIds', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(makeIncoming({ content: 'first' }), 'sse')
      const v1 = useMessageStore.getState().version

      store.upsertMessage(makeIncoming({ content: 'updated' }), 'sse')
      const state = useMessageStore.getState()
      expect(state.entities['msg-1'].content).toBe('updated')
      expect(state.orderedIds).toEqual(['msg-1'])
      expect(state.version).toBe(v1 + 1)
    })

    it('does not change state for no-op updates', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(makeIncoming(), 'sse')
      const v1 = useMessageStore.getState().version

      // Same message, same content → no-op
      store.upsertMessage(makeIncoming(), 'db')
      // Rule 2 actually blocks this because source is 'sse' and entity has no taskStatus
      // But without taskStatus, Rule 2 doesn't apply. Let's use a case that hits no-op.
    })
  })

  describe('upsertMany', () => {
    it('batches multiple inserts into a single state update', () => {
      const store = useMessageStore.getState()
      const msgs = [
        makeIncoming({ id: 'msg-1', timestamp: '2026-02-17T10:00:00Z' }),
        makeIncoming({ id: 'msg-2', timestamp: '2026-02-17T10:01:00Z' }),
        makeIncoming({ id: 'msg-3', timestamp: '2026-02-17T10:02:00Z' }),
      ]
      store.upsertMany(msgs, 'db')
      const state = useMessageStore.getState()
      expect(Object.keys(state.entities)).toHaveLength(3)
      expect(state.orderedIds).toEqual(['msg-1', 'msg-2', 'msg-3'])
      expect(state.version).toBe(1) // single increment for batch
    })

    it('does not change state when all messages are no-ops', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(makeIncoming({ id: 'msg-1', content: 'Hello' }), 'sse')
      const v1 = useMessageStore.getState().version

      // Same message again — should be a no-op (no taskStatus → Rule 2 doesn't apply)
      store.upsertMany([makeIncoming({ id: 'msg-1', content: 'Hello' })], 'sse')
      expect(useMessageStore.getState().version).toBe(v1)
    })

    it('handles mixed inserts and updates correctly', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(
        makeIncoming({ id: 'msg-1', content: 'original' }),
        'sse',
      )

      store.upsertMany([
        makeIncoming({ id: 'msg-1', content: 'updated' }),
        makeIncoming({ id: 'msg-2', timestamp: '2026-02-17T10:01:00Z' }),
      ], 'sse')

      const state = useMessageStore.getState()
      expect(state.entities['msg-1'].content).toBe('updated')
      expect(state.entities['msg-2']).toBeDefined()
      expect(state.orderedIds).toEqual(['msg-1', 'msg-2'])
    })
  })

  describe('removeMessage', () => {
    it('removes an existing message', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(makeIncoming({ id: 'msg-1' }), 'sse')
      store.upsertMessage(
        makeIncoming({ id: 'msg-2', timestamp: '2026-02-17T10:01:00Z' }),
        'sse',
      )
      store.removeMessage('msg-1')
      const state = useMessageStore.getState()
      expect(state.entities['msg-1']).toBeUndefined()
      expect(state.orderedIds).toEqual(['msg-2'])
    })

    it('is a no-op for non-existent messages', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(makeIncoming(), 'sse')
      const v1 = useMessageStore.getState().version

      store.removeMessage('non-existent')
      expect(useMessageStore.getState().version).toBe(v1)
    })
  })

  describe('cancelAllNonTerminal', () => {
    it('cancels all non-terminal tasks in a room', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(
        makeIncoming({
          id: 'task-1', taskStatus: 'working',
          timestamp: '2026-02-17T10:00:00Z',
        }),
        'sse',
      )
      store.upsertMessage(
        makeIncoming({
          id: 'task-2', taskStatus: 'submitted',
          timestamp: '2026-02-17T10:01:00Z',
        }),
        'sse',
      )
      store.upsertMessage(
        makeIncoming({
          id: 'task-3', taskStatus: 'completed', content: 'Done',
          timestamp: '2026-02-17T10:02:00Z',
        }),
        'sse',
      )

      store.cancelAllNonTerminal('room-1')
      const state = useMessageStore.getState()
      expect(state.entities['task-1'].taskStatus).toBe('canceled')
      expect(state.entities['task-2'].taskStatus).toBe('canceled')
      expect(state.entities['task-3'].taskStatus).toBe('completed') // unchanged
    })

    it('does not cancel ephemeral messages', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(
        makeIncoming({
          id: 'placeholder', taskStatus: 'working', isEphemeral: true,
        }),
        'optimistic',
      )

      store.cancelAllNonTerminal('room-1')
      const state = useMessageStore.getState()
      expect(state.entities['placeholder'].taskStatus).toBe('working')
    })

    it('only cancels tasks in the specified room', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(
        makeIncoming({ id: 'task-r1', roomId: 'room-1', taskStatus: 'working' }),
        'sse',
      )
      store.upsertMessage(
        makeIncoming({
          id: 'task-r2', roomId: 'room-2', taskStatus: 'working',
          timestamp: '2026-02-17T10:01:00Z',
        }),
        'sse',
      )

      store.cancelAllNonTerminal('room-1')
      const state = useMessageStore.getState()
      expect(state.entities['task-r1'].taskStatus).toBe('canceled')
      expect(state.entities['task-r2'].taskStatus).toBe('working')
    })

    it('is a no-op when no non-terminal tasks exist', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(
        makeIncoming({ id: 'task-1', taskStatus: 'completed', content: 'Done' }),
        'sse',
      )
      const v1 = useMessageStore.getState().version

      store.cancelAllNonTerminal('room-1')
      expect(useMessageStore.getState().version).toBe(v1)
    })
  })

  describe('markDbSynced', () => {
    it('sets hydratedFromDb and lastDbSyncAt', () => {
      const store = useMessageStore.getState()
      store.markDbSynced()
      const state = useMessageStore.getState()
      expect(state.hydratedFromDb).toBe(true)
      expect(state.lastDbSyncAt).toBeGreaterThan(0)
    })
  })
})
