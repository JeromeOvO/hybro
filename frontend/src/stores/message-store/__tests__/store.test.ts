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

  describe('markDbSynced', () => {
    it('sets hydratedFromDb and lastDbSyncAt', () => {
      const store = useMessageStore.getState()
      store.markDbSynced()
      const state = useMessageStore.getState()
      expect(state.hydratedFromDb).toBe(true)
      expect(state.lastDbSyncAt).toBeGreaterThan(0)
    })
  })

  describe('replaceMessageId', () => {
    it('renames an optimistic id to the real server id', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(makeIncoming({ id: 'cr:opt-1', messageType: 'user' }), 'optimistic')

      store.replaceMessageId('cr:opt-1', 'srv-1')
      const state = useMessageStore.getState()
      expect(state.entities['cr:opt-1']).toBeUndefined()
      expect(state.entities['srv-1']).toBeDefined()
      expect(state.entities['srv-1'].id).toBe('srv-1')
      expect(state.orderedIds).toEqual(['srv-1'])
    })

    it('merges with an SSE entity already at the new id (SSE wins)', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(
        makeIncoming({ id: 'cr:opt-1', messageType: 'user', content: 'optimistic' }),
        'optimistic',
      )
      store.upsertMessage(
        makeIncoming({ id: 'srv-1', messageType: 'user', content: 'sse-version' }),
        'sse',
      )

      store.replaceMessageId('cr:opt-1', 'srv-1')
      const state = useMessageStore.getState()
      expect(state.entities['cr:opt-1']).toBeUndefined()
      expect(state.entities['srv-1'].content).toBe('sse-version')
      // No duplicate id in orderedIds
      expect(state.orderedIds.filter(id => id === 'srv-1')).toHaveLength(1)
    })

    it('is a no-op when old id does not exist', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(makeIncoming({ id: 'msg-1' }), 'sse')
      const v1 = useMessageStore.getState().version

      store.replaceMessageId('does-not-exist', 'srv-1')
      expect(useMessageStore.getState().version).toBe(v1)
    })

    it('is a no-op when old id equals new id', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(makeIncoming({ id: 'msg-1' }), 'sse')
      const v1 = useMessageStore.getState().version

      store.replaceMessageId('msg-1', 'msg-1')
      expect(useMessageStore.getState().version).toBe(v1)
    })

    it('rewires relatedMessageId links pointing at the old id', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(makeIncoming({ id: 'cr:opt-1', messageType: 'user' }), 'optimistic')
      store.upsertMessage(
        makeIncoming({
          id: 'agent-reply', messageType: 'agent',
          timestamp: '2026-02-17T10:01:00Z',
        }),
        'sse',
      )
      // Manually set the relation to simulate a processing placeholder
      const s = useMessageStore.getState()
      s.entities['agent-reply'] = { ...s.entities['agent-reply'], relatedMessageId: 'cr:opt-1' }

      store.replaceMessageId('cr:opt-1', 'srv-1')
      const state = useMessageStore.getState()
      expect(state.entities['agent-reply'].relatedMessageId).toBe('srv-1')
    })
  })

  describe('replaceAndPatchMessageId', () => {
    it('replaces id and applies patch in a single version bump', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(
        makeIncoming({ id: 'cr:opt-1', messageType: 'user', content: 'hello' }),
        'optimistic',
      )
      const vBefore = useMessageStore.getState().version

      store.replaceAndPatchMessageId('cr:opt-1', 'srv-1', {
        content: 'hello',
        userId: 'user-42',
      })

      const state = useMessageStore.getState()
      expect(state.entities['cr:opt-1']).toBeUndefined()
      expect(state.entities['srv-1']).toBeDefined()
      expect(state.entities['srv-1'].id).toBe('srv-1')
      expect(state.entities['srv-1'].userId).toBe('user-42')
      expect(state.orderedIds).toEqual(['srv-1'])
      // Exactly one version bump (atomic — not two)
      expect(state.version).toBe(vBefore + 1)
    })

    it('merges attachment patch with server-resolved URLs', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(
        makeIncoming({
          id: 'cr:opt-1', messageType: 'user',
          attachments: [{ fileId: 'blob-id', fileUrl: 'blob:preview', mimeType: 'image/png', fileName: 'pic.png', sizeBytes: 1024 }],
        }),
        'optimistic',
      )

      store.replaceAndPatchMessageId('cr:opt-1', 'srv-1', {
        attachments: [{ fileId: 'server-id', fileUrl: 'https://cdn.example.com/pic.png', mimeType: 'image/png', fileName: 'pic.png', sizeBytes: 1024 }],
      })

      const state = useMessageStore.getState()
      const att = state.entities['srv-1'].attachments?.[0]
      expect(att?.fileId).toBe('server-id')
      expect(att?.fileUrl).toBe('https://cdn.example.com/pic.png')
    })

    it('SSE race: when srv id already exists, SSE entity wins and no duplicate in orderedIds', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(
        makeIncoming({ id: 'cr:opt-1', messageType: 'user', content: 'optimistic' }),
        'optimistic',
      )
      store.upsertMessage(
        makeIncoming({ id: 'srv-1', messageType: 'user', content: 'sse-wins' }),
        'sse',
      )

      store.replaceAndPatchMessageId('cr:opt-1', 'srv-1', { content: 'http-patch' })

      const state = useMessageStore.getState()
      expect(state.entities['cr:opt-1']).toBeUndefined()
      // SSE content preserved, not overwritten by http-patch
      expect(state.entities['srv-1'].content).toBe('sse-wins')
      expect(state.orderedIds.filter(id => id === 'srv-1')).toHaveLength(1)
    })

    it('preserves clientRequestId from optimistic entity when SSE omits it', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(
        makeIncoming({ id: 'cr:opt-1', messageType: 'user', clientRequestId: 'crid-xyz' }),
        'optimistic',
      )
      // SSE entity has no clientRequestId
      store.upsertMessage(
        makeIncoming({ id: 'srv-1', messageType: 'user' }),
        'sse',
      )

      store.replaceAndPatchMessageId('cr:opt-1', 'srv-1', {})

      const state = useMessageStore.getState()
      expect(state.entities['srv-1'].clientRequestId).toBe('crid-xyz')
    })

    it('is a no-op when old id does not exist', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(makeIncoming({ id: 'msg-1' }), 'sse')
      const v1 = useMessageStore.getState().version

      store.replaceAndPatchMessageId('ghost', 'srv-1', {})
      expect(useMessageStore.getState().version).toBe(v1)
    })

    it('is a no-op when old id equals new id', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(makeIncoming({ id: 'msg-1' }), 'sse')
      const v1 = useMessageStore.getState().version

      store.replaceAndPatchMessageId('msg-1', 'msg-1', {})
      expect(useMessageStore.getState().version).toBe(v1)
    })

    it('rewires relatedMessageId links pointing at the old id', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(
        makeIncoming({ id: 'cr:opt-1', messageType: 'user' }),
        'optimistic',
      )
      store.upsertMessage(
        makeIncoming({ id: 'agent-reply', messageType: 'agent', timestamp: '2026-02-17T10:01:00Z' }),
        'sse',
      )
      useMessageStore.getState().entities['agent-reply'] = {
        ...useMessageStore.getState().entities['agent-reply'],
        relatedMessageId: 'cr:opt-1',
      }

      store.replaceAndPatchMessageId('cr:opt-1', 'srv-1', {})
      const state = useMessageStore.getState()
      expect(state.entities['agent-reply'].relatedMessageId).toBe('srv-1')
    })
  })

  describe('multi-round HITL upsert', () => {
    it('allows transitioning completed entity back to input-required on chronologically newer follow-up', () => {
      const store = useMessageStore.getState()
      // Round 1 arrives as input-required
      store.upsertMessage(
        makeIncoming({
          id: 'agent-msg-1',
          taskStatus: 'input-required',
          hitlRequestId: 'req-1',
          hitlInteractionId: 'interaction-1',
          hitlPrompt: 'Round 1 Question',
          hitlResolved: false,
          taskUpdatedAt: '2026-08-30T10:00:00Z',
        }),
        'sse',
      )

      // Round 1 is answered and completed
      store.upsertMessage(
        makeIncoming({
          id: 'agent-msg-1',
          taskStatus: 'completed',
          hitlRequestId: 'req-1',
          hitlInteractionId: 'interaction-1',
          hitlResolved: true,
          hitlUserAnswer: 'Answer 1',
          taskUpdatedAt: '2026-08-30T10:05:00Z',
        }),
        'sse',
      )

      expect(useMessageStore.getState().entities['agent-msg-1'].taskStatus).toBe('completed')
      expect(useMessageStore.getState().entities['agent-msg-1'].hitlResolved).toBe(true)

      // Round 2 arrives with newer timestamp
      store.upsertMessage(
        makeIncoming({
          id: 'agent-msg-1',
          taskStatus: 'input-required',
          hitlRequestId: 'req-2',
          hitlInteractionId: 'interaction-2',
          hitlPrompt: 'Round 2 Question',
          hitlResolved: false,
          hitlUserAnswer: '',
          taskUpdatedAt: '2026-08-30T10:10:00Z',
        }),
        'sse',
      )

      const state = useMessageStore.getState()
      expect(state.entities['agent-msg-1'].taskStatus).toBe('input-required')
      expect(state.entities['agent-msg-1'].hitlRequestId).toBe('req-2')
      expect(state.entities['agent-msg-1'].hitlInteractionId).toBe('interaction-2')
      expect(state.entities['agent-msg-1'].hitlPrompt).toBe('Round 2 Question')
      expect(state.entities['agent-msg-1'].hitlResolved).toBe(false)
      expect(state.entities['agent-msg-1'].hitlUserAnswer).toBe('')
    })

    it('rejects out-of-order delayed input-required events from previous rounds', () => {
      const store = useMessageStore.getState()
      // Currently on Round 2 completed
      store.upsertMessage(
        makeIncoming({
          id: 'agent-msg-late',
          taskStatus: 'completed',
          hitlRequestId: 'req-2',
          taskUpdatedAt: '2026-08-30T10:20:00Z',
        }),
        'sse',
      )

      // A delayed event from Round 1 arrives
      store.upsertMessage(
        makeIncoming({
          id: 'agent-msg-late',
          taskStatus: 'input-required',
          hitlRequestId: 'req-1',
          taskUpdatedAt: '2026-08-30T10:15:00Z',
        }),
        'sse',
      )

      const state = useMessageStore.getState()
      expect(state.entities['agent-msg-late'].taskStatus).toBe('completed')
      expect(state.entities['agent-msg-late'].hitlRequestId).toBe('req-2')
    })

    it('rejects reopening failed or canceled states', () => {
      const store = useMessageStore.getState()
      store.upsertMessage(
        makeIncoming({
          id: 'agent-msg-fail',
          taskStatus: 'failed',
          taskUpdatedAt: '2026-08-30T10:30:00Z',
        }),
        'sse',
      )

      // Attempt to reopen with newer timestamp
      store.upsertMessage(
        makeIncoming({
          id: 'agent-msg-fail',
          taskStatus: 'input-required',
          hitlRequestId: 'req-new',
          taskUpdatedAt: '2026-08-30T10:35:00Z',
        }),
        'sse',
      )

      const state = useMessageStore.getState()
      expect(state.entities['agent-msg-fail'].taskStatus).toBe('failed')
      expect(state.entities['agent-msg-fail'].hitlRequestId).toBeUndefined()
    })
  })
})
