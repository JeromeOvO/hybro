import { describe, it, expect, beforeEach } from 'vitest'
import { useMessageStore } from '../index'
import type { IncomingMessage } from '../types'
import { TASK_STATE } from '@/lib/types/sse'

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
      const versionAfterFirst = useMessageStore.getState().version

      // Same message, same content → no-op
      store.upsertMessage(makeIncoming(), 'db')
      // Rule 2 actually blocks this because source is 'sse' and entity has no taskStatus
      // But without taskStatus, Rule 2 doesn't apply. Let's use a case that hits no-op.
      expect(useMessageStore.getState().version).toBe(versionAfterFirst)
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
          id: 'task-1', taskStatus: TASK_STATE.WORKING,
          timestamp: '2026-02-17T10:00:00Z',
        }),
        'sse',
      )
      store.upsertMessage(
        makeIncoming({
          id: 'task-2', taskStatus: TASK_STATE.SUBMITTED,
          timestamp: '2026-02-17T10:01:00Z',
        }),
        'sse',
      )
      store.upsertMessage(
        makeIncoming({
          id: 'task-3', taskStatus: TASK_STATE.COMPLETED, content: 'Done',
          timestamp: '2026-02-17T10:02:00Z',
        }),
        'sse',
      )

      store.cancelAllNonTerminal('room-1')
      const state = useMessageStore.getState()
      expect(state.entities['task-1'].taskStatus).toBe(TASK_STATE.CANCELED)
      expect(state.entities['task-2'].taskStatus).toBe(TASK_STATE.CANCELED)
      expect(state.entities['task-3'].taskStatus).toBe(TASK_STATE.COMPLETED) // unchanged
    })

    it('does not cancel ephemeral messages', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(
        makeIncoming({
          id: 'placeholder', taskStatus: TASK_STATE.WORKING, isEphemeral: true,
        }),
        'optimistic',
      )

      store.cancelAllNonTerminal('room-1')
      const state = useMessageStore.getState()
      expect(state.entities['placeholder'].taskStatus).toBe(TASK_STATE.WORKING)
    })

    it('only cancels tasks in the specified room', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(
        makeIncoming({ id: 'task-r1', roomId: 'room-1', taskStatus: TASK_STATE.WORKING }),
        'sse',
      )
      store.upsertMessage(
        makeIncoming({
          id: 'task-r2', roomId: 'room-2', taskStatus: TASK_STATE.WORKING,
          timestamp: '2026-02-17T10:01:00Z',
        }),
        'sse',
      )

      store.cancelAllNonTerminal('room-1')
      const state = useMessageStore.getState()
      expect(state.entities['task-r1'].taskStatus).toBe(TASK_STATE.CANCELED)
      expect(state.entities['task-r2'].taskStatus).toBe(TASK_STATE.WORKING)
    })

    it('is a no-op when no non-terminal tasks exist', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(
        makeIncoming({ id: 'task-1', taskStatus: TASK_STATE.COMPLETED, content: 'Done' }),
        'sse',
      )
      const v1 = useMessageStore.getState().version

      store.cancelAllNonTerminal('room-1')
      expect(useMessageStore.getState().version).toBe(v1)
    })
  })

  describe('replaceMessageId', () => {
    it('Case A: HTTP first — swaps temp to real when real does not exist', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(makeIncoming({
        id: 'temp-123',
        messageType: 'user',
        content: 'Hello',
        clientRequestId: 'cr-1',
      }), 'optimistic')

      store.replaceMessageId('temp-123', 'real-456', {
        id: 'real-456',
        roomId: 'room-1',
        messageType: 'user',
        content: 'Hello',
        senderName: 'Agent',
        timestamp: '2026-02-17T10:00:00Z',
        clientRequestId: 'cr-1',
      })

      const state = useMessageStore.getState()
      expect(state.entities['temp-123']).toBeUndefined()
      expect(state.entities['real-456']).toBeDefined()
      expect(state.entities['real-456'].content).toBe('Hello')
      expect(state.entities['real-456'].clientRequestId).toBe('cr-1')
    })

    it('Case B: SSE first — merges temp data into existing real entity, removes temp', () => {
      const store = useMessageStore.getState()
      // Temp from optimistic
      store.upsertMessage(makeIncoming({
        id: 'temp-123',
        messageType: 'user',
        content: 'Hello',
        clientRequestId: 'cr-1',
      }), 'optimistic')
      // SSE already created the real entity
      store.upsertMessage(makeIncoming({
        id: 'real-456',
        messageType: 'user',
        content: '',
        timestamp: '2026-02-17T10:01:00Z',
      }), 'sse')

      store.replaceMessageId('temp-123', 'real-456', {
        id: 'real-456',
        roomId: 'room-1',
        messageType: 'user',
        content: 'Hello',
        senderName: 'Agent',
        timestamp: '2026-02-17T10:00:00Z',
        clientRequestId: 'cr-1',
      })

      const state = useMessageStore.getState()
      expect(state.entities['temp-123']).toBeUndefined()
      expect(state.entities['real-456']).toBeDefined()
      expect(state.entities['real-456'].content).toBe('Hello')
      expect(state.entities['real-456'].clientRequestId).toBe('cr-1')
    })

    it('Case C: SSE swapped first — temp gone, applies updates to real', () => {
      const store = useMessageStore.getState()
      // Only the real entity exists (SSE handler already swapped)
      store.upsertMessage(makeIncoming({
        id: 'real-456',
        messageType: 'user',
        content: 'Hello',
        clientRequestId: 'cr-1',
      }), 'sse')

      store.replaceMessageId('temp-123', 'real-456', {
        id: 'real-456',
        roomId: 'room-1',
        messageType: 'user',
        content: 'Hello',
        senderName: 'Agent',
        timestamp: '2026-02-17T10:00:00Z',
        clientRequestId: 'cr-1',
        attachments: [{ fileId: 'f1', fileName: 'test.png', mimeType: 'image/png', sizeBytes: 1024 }],
      })

      const state = useMessageStore.getState()
      expect(state.entities['real-456'].attachments).toHaveLength(1)
      expect(state.entities['real-456'].attachments![0].fileId).toBe('f1')
    })

    it('Case D: neither exists — returns unchanged state', () => {
      const store = useMessageStore.getState()
      const vBefore = useMessageStore.getState().version

      store.replaceMessageId('temp-123', 'real-456')

      expect(useMessageStore.getState().version).toBe(vBefore)
    })

    it('preserves clientRequestId through all swap cases', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(makeIncoming({
        id: 'temp-aaa',
        messageType: 'user',
        content: 'Hi',
        clientRequestId: 'cr-uuid',
      }), 'optimistic')

      store.replaceMessageId('temp-aaa', 'real-bbb')

      const state = useMessageStore.getState()
      expect(state.entities['real-bbb'].clientRequestId).toBe('cr-uuid')
    })

    it('updates orderedIds after swap', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(makeIncoming({
        id: 'temp-111',
        messageType: 'user',
        content: 'First',
        timestamp: '2026-02-17T10:00:00Z',
      }), 'optimistic')
      store.upsertMessage(makeIncoming({
        id: 'msg-2',
        timestamp: '2026-02-17T10:01:00Z',
      }), 'sse')

      store.replaceMessageId('temp-111', 'real-111')

      const state = useMessageStore.getState()
      expect(state.orderedIds).toContain('real-111')
      expect(state.orderedIds).not.toContain('temp-111')
    })
  })

  describe('findByClientRequestId', () => {
    it('finds entity by clientRequestId', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(makeIncoming({
        id: 'msg-1',
        messageType: 'user',
        clientRequestId: 'cr-find-me',
      }), 'optimistic')

      const found = store.findByClientRequestId('cr-find-me')
      expect(found).toBeDefined()
      expect(found!.id).toBe('msg-1')
    })

    it('returns undefined when no match', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(makeIncoming({ id: 'msg-1' }), 'sse')

      const found = store.findByClientRequestId('nonexistent')
      expect(found).toBeUndefined()
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
