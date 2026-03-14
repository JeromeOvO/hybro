import { describe, it, expect } from 'vitest'
import { applyUpsert, isNoOpUpdate, buildSortedIds } from '../upsert'
import type { MessageEntity, IncomingMessage } from '../types'

// ── Helpers ──────────────────────────────────────────────────

function makeEntity(overrides: Partial<MessageEntity> = {}): MessageEntity {
  return {
    id: 'msg-1',
    roomId: 'room-1',
    messageType: 'agent',
    content: 'Hello',
    senderName: 'Agent',
    timestamp: '2026-02-17T10:00:00Z',
    source: 'sse',
    sourceVersion: 1,
    displayType: 'agent-bubble',
    isEphemeral: false,
    createdAt: 1000,
    updatedAt: 1000,
    ...overrides,
  }
}

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

// ── applyUpsert ──────────────────────────────────────────────

describe('applyUpsert', () => {
  describe('new message insertion', () => {
    it('inserts a new message when no existing entity', () => {
      const result = applyUpsert({}, [], makeIncoming(), 'sse')
      expect(result).not.toBeNull()
      expect(result!.idsChanged).toBe(true)
      expect(result!.entities['msg-1']).toBeDefined()
      expect(result!.entities['msg-1'].source).toBe('sse')
      expect(result!.entities['msg-1'].sourceVersion).toBe(1)
    })

    it('sets displayType correctly on insert', () => {
      const result = applyUpsert({}, [], makeIncoming({ messageType: 'user' }), 'sse')
      expect(result!.entities['msg-1'].displayType).toBe('user-bubble')
    })

    it('sets isEphemeral to false by default', () => {
      const result = applyUpsert({}, [], makeIncoming(), 'sse')
      expect(result!.entities['msg-1'].isEphemeral).toBe(false)
    })

    it('sets isEphemeral when specified', () => {
      const result = applyUpsert({}, [], makeIncoming({ isEphemeral: true }), 'optimistic')
      expect(result!.entities['msg-1'].isEphemeral).toBe(true)
    })
  })

  describe('Rule 1: Never downgrade terminal status', () => {
    it('rejects working status when entity is already completed', () => {
      const entities = { 'msg-1': makeEntity({ taskStatus: 'completed' }) }
      const result = applyUpsert(
        entities, ['msg-1'],
        makeIncoming({ taskStatus: 'working' }),
        'sse',
      )
      expect(result).toBeNull()
    })

    it('rejects submitted status when entity is already failed', () => {
      const entities = { 'msg-1': makeEntity({ taskStatus: 'failed' }) }
      const result = applyUpsert(
        entities, ['msg-1'],
        makeIncoming({ taskStatus: 'submitted' }),
        'db',
      )
      expect(result).toBeNull()
    })

    it('rejects working status when entity is already canceled', () => {
      const entities = { 'msg-1': makeEntity({ taskStatus: 'canceled' }) }
      const result = applyUpsert(
        entities, ['msg-1'],
        makeIncoming({ taskStatus: 'working' }),
        'sse',
      )
      expect(result).toBeNull()
    })

    it('allows terminal-to-terminal transitions (e.g., failed → completed from DB)', () => {
      const entities = {
        'msg-1': makeEntity({ taskStatus: 'failed', displayType: 'task-status' }),
      }
      const result = applyUpsert(
        entities, ['msg-1'],
        makeIncoming({ taskStatus: 'completed', content: 'Result' }),
        'db',
      )
      expect(result).not.toBeNull()
      expect(result!.entities['msg-1'].taskStatus).toBe('completed')
    })
  })

  describe('Rule 2: SSE wins over DB for non-terminal states', () => {
    it('rejects DB update when SSE entity is in working state', () => {
      const entities = {
        'msg-1': makeEntity({ source: 'sse', taskStatus: 'working', displayType: 'task-status' }),
      }
      const result = applyUpsert(
        entities, ['msg-1'],
        makeIncoming({ taskStatus: 'working', content: 'DB version' }),
        'db',
      )
      expect(result).toBeNull()
    })

    it('allows DB update when SSE entity has terminal state', () => {
      const entities = {
        'msg-1': makeEntity({
          source: 'sse', taskStatus: 'completed',
          content: 'SSE content', displayType: 'agent-bubble',
        }),
      }
      const result = applyUpsert(
        entities, ['msg-1'],
        makeIncoming({ taskStatus: 'completed', content: 'DB content (canonical)' }),
        'db',
      )
      expect(result).not.toBeNull()
      expect(result!.entities['msg-1'].content).toBe('DB content (canonical)')
    })

    it('allows SSE update when entity was from DB with non-terminal state', () => {
      const entities = {
        'msg-1': makeEntity({ source: 'db', taskStatus: 'working', displayType: 'task-status' }),
      }
      const result = applyUpsert(
        entities, ['msg-1'],
        makeIncoming({ taskStatus: 'working', content: 'SSE update' }),
        'sse',
      )
      expect(result).not.toBeNull()
    })
  })

  describe('Rule 5: Never overwrite ephemeral from DB', () => {
    it('rejects DB update for ephemeral messages', () => {
      const entities = {
        'msg-1': makeEntity({ isEphemeral: true }),
      }
      const result = applyUpsert(
        entities, ['msg-1'],
        makeIncoming({ content: 'DB version' }),
        'db',
      )
      expect(result).toBeNull()
    })

    it('allows SSE update for ephemeral messages', () => {
      const entities = {
        'msg-1': makeEntity({ isEphemeral: true, displayType: 'task-status', taskStatus: 'working' }),
      }
      const result = applyUpsert(
        entities, ['msg-1'],
        makeIncoming({ taskStatus: 'completed', content: 'Done' }),
        'sse',
      )
      expect(result).not.toBeNull()
    })

    it('allows optimistic update for ephemeral messages', () => {
      const entities = {
        'msg-1': makeEntity({ isEphemeral: true, displayType: 'task-status', taskStatus: 'working' }),
      }
      const result = applyUpsert(
        entities, ['msg-1'],
        makeIncoming({ taskStatus: 'canceled' }),
        'optimistic',
      )
      expect(result).not.toBeNull()
    })
  })

  describe('Rule 4: No-op detection', () => {
    it('rejects update that changes nothing visible', () => {
      const entities = {
        'msg-1': makeEntity({
          content: 'Hello',
          taskStatus: undefined,
          senderName: 'Agent',
          displayType: 'agent-bubble',
        }),
      }
      const result = applyUpsert(
        entities, ['msg-1'],
        makeIncoming({ content: 'Hello', senderName: 'Agent' }),
        'db',
      )
      expect(result).toBeNull()
    })

    it('accepts update that changes content', () => {
      const entities = {
        'msg-1': makeEntity({ content: 'Hello', displayType: 'agent-bubble' }),
      }
      const result = applyUpsert(
        entities, ['msg-1'],
        makeIncoming({ content: 'Updated content' }),
        'db',
      )
      expect(result).not.toBeNull()
      expect(result!.entities['msg-1'].content).toBe('Updated content')
    })
  })

  describe('sourceVersion increment', () => {
    it('increments sourceVersion on update', () => {
      const entities = {
        'msg-1': makeEntity({ sourceVersion: 3, content: 'old' }),
      }
      const result = applyUpsert(
        entities, ['msg-1'],
        makeIncoming({ content: 'new' }),
        'sse',
      )
      expect(result!.entities['msg-1'].sourceVersion).toBe(4)
    })
  })

  describe('field merging', () => {
    it('preserves existing optional fields when not provided in incoming', () => {
      const entities = {
        'msg-1': makeEntity({
          stepNumber: 2,
          totalSteps: 5,
          taskContent: 'Doing work',
          displayType: 'task-status',
          taskStatus: 'working',
        }),
      }
      const result = applyUpsert(
        entities, ['msg-1'],
        makeIncoming({ taskStatus: 'completed', content: 'Done' }),
        'sse',
      )
      expect(result).not.toBeNull()
      expect(result!.entities['msg-1'].stepNumber).toBe(2)
      expect(result!.entities['msg-1'].totalSteps).toBe(5)
      expect(result!.entities['msg-1'].taskContent).toBe('Doing work')
    })

    it('allows explicitly setting nullable fields to null', () => {
      const entities = {
        'msg-1': makeEntity({
          taskError: 'Some error',
          taskStatus: 'failed',
          displayType: 'task-status',
        }),
      }
      const result = applyUpsert(
        entities, ['msg-1'],
        makeIncoming({ taskError: null, taskStatus: 'completed', content: 'Fixed' }),
        'sse',
      )
      expect(result).not.toBeNull()
      expect(result!.entities['msg-1'].taskError).toBeNull()
    })
  })
})

// ── isNoOpUpdate ─────────────────────────────────────────────

describe('isNoOpUpdate', () => {
  it('returns true when nothing visible changed', () => {
    const existing = makeEntity({
      content: 'Hello',
      taskStatus: undefined,
      senderName: 'Agent',
      stepNumber: undefined,
      totalSteps: undefined,
      displayType: 'agent-bubble',
    })
    const incoming = makeIncoming({ content: 'Hello', senderName: 'Agent' })
    expect(isNoOpUpdate(existing, incoming, 'db')).toBe(true)
  })

  it('returns false when content changed', () => {
    const existing = makeEntity({ content: 'Hello', displayType: 'agent-bubble' })
    const incoming = makeIncoming({ content: 'Changed' })
    expect(isNoOpUpdate(existing, incoming, 'db')).toBe(false)
  })

  it('returns false when taskStatus changed', () => {
    const existing = makeEntity({
      taskStatus: 'working',
      displayType: 'task-status',
    })
    const incoming = makeIncoming({ taskStatus: 'completed', content: 'Done' })
    expect(isNoOpUpdate(existing, incoming, 'sse')).toBe(false)
  })

  it('returns false when displayType would change', () => {
    const existing = makeEntity({
      taskStatus: 'working',
      displayType: 'task-status',
      content: '',
    })
    const incoming = makeIncoming({
      taskStatus: 'completed',
      content: 'Result here',
    })
    expect(isNoOpUpdate(existing, incoming, 'sse')).toBe(false)
  })

  it('returns false when taskError changes from string to null (Gap 9)', () => {
    const existing = makeEntity({
      taskError: 'Some error',
      taskStatus: 'failed',
      displayType: 'task-status',
    })
    const incoming = makeIncoming({ taskError: null, taskStatus: 'failed' })
    expect(isNoOpUpdate(existing, incoming, 'db')).toBe(false)
  })

  it('returns true when taskError is undefined (not provided) and existing has error', () => {
    const existing = makeEntity({
      taskError: 'Some error',
      taskStatus: 'failed',
      displayType: 'task-status',
    })
    const incoming = makeIncoming({ taskStatus: 'failed' })
    // taskError not in incoming → undefined → coalesces to existing value → no change
    expect(isNoOpUpdate(existing, incoming, 'db')).toBe(true)
  })

  it('returns false when stepNumber changes', () => {
    const existing = makeEntity({
      stepNumber: 1,
      taskStatus: 'working',
      displayType: 'task-status',
    })
    const incoming = makeIncoming({ stepNumber: 2, taskStatus: 'working' })
    expect(isNoOpUpdate(existing, incoming, 'sse')).toBe(false)
  })

  it('detects clientRequestId-only change as non-no-op', () => {
    const existing = makeEntity({
      content: 'Hello',
      displayType: 'agent-bubble',
    })
    const incoming = makeIncoming({ content: 'Hello', clientRequestId: 'cr-new' })
    expect(isNoOpUpdate(existing, incoming, 'sse')).toBe(false)
  })
})

// ── mergeIncoming clientRequestId ─────────────────────────────

describe('applyUpsert clientRequestId merging', () => {
  it('preserves clientRequestId from incoming on new entity', () => {
    const result = applyUpsert(
      {}, [],
      makeIncoming({ clientRequestId: 'cr-123' }),
      'optimistic',
    )
    expect(result).not.toBeNull()
    expect(result!.entities['msg-1'].clientRequestId).toBe('cr-123')
  })

  it('preserves clientRequestId from existing when incoming is undefined', () => {
    const entities = {
      'msg-1': makeEntity({ clientRequestId: 'cr-existing', content: 'old' }),
    }
    const result = applyUpsert(
      entities, ['msg-1'],
      makeIncoming({ content: 'updated' }),
      'sse',
    )
    expect(result).not.toBeNull()
    expect(result!.entities['msg-1'].clientRequestId).toBe('cr-existing')
  })
})

// ── buildSortedIds ───────────────────────────────────────────

describe('buildSortedIds', () => {
  it('sorts by timestamp (primary)', () => {
    const entities: Record<string, MessageEntity> = {
      'b': makeEntity({ id: 'b', timestamp: '2026-02-17T10:01:00Z' }),
      'a': makeEntity({ id: 'a', timestamp: '2026-02-17T10:00:00Z' }),
      'c': makeEntity({ id: 'c', timestamp: '2026-02-17T10:02:00Z' }),
    }
    expect(buildSortedIds(entities)).toEqual(['a', 'b', 'c'])
  })

  it('sorts by stepNumber within same workflow batch (< 60s, same relatedMessageId)', () => {
    const entities: Record<string, MessageEntity> = {
      'step3': makeEntity({
        id: 'step3', timestamp: '2026-02-17T10:00:02Z', stepNumber: 3,
        relatedMessageId: 'user-1',
      }),
      'step1': makeEntity({
        id: 'step1', timestamp: '2026-02-17T10:00:00Z', stepNumber: 1,
        relatedMessageId: 'user-1',
      }),
      'step2': makeEntity({
        id: 'step2', timestamp: '2026-02-17T10:00:01Z', stepNumber: 2,
        relatedMessageId: 'user-1',
      }),
    }
    expect(buildSortedIds(entities)).toEqual(['step1', 'step2', 'step3'])
  })

  it('does not use stepNumber ordering across batches (> 60s apart)', () => {
    const entities: Record<string, MessageEntity> = {
      'late': makeEntity({
        id: 'late', timestamp: '2026-02-17T10:02:00Z', stepNumber: 1,
        relatedMessageId: 'user-1',
      }),
      'early': makeEntity({
        id: 'early', timestamp: '2026-02-17T10:00:00Z', stepNumber: 3,
        relatedMessageId: 'user-1',
      }),
    }
    expect(buildSortedIds(entities)).toEqual(['early', 'late'])
  })

  it('does not use stepNumber ordering across different workflows', () => {
    const entities: Record<string, MessageEntity> = {
      'wf2-step1': makeEntity({
        id: 'wf2-step1', timestamp: '2026-02-17T10:00:30Z', stepNumber: 1,
        relatedMessageId: 'user-2',
      }),
      'wf1-step2': makeEntity({
        id: 'wf1-step2', timestamp: '2026-02-17T10:00:20Z', stepNumber: 2,
        relatedMessageId: 'user-1',
      }),
      'wf1-step1': makeEntity({
        id: 'wf1-step1', timestamp: '2026-02-17T10:00:00Z', stepNumber: 1,
        relatedMessageId: 'user-1',
      }),
    }
    // wf1 steps should be grouped by step, but wf2 should stay in timestamp order
    expect(buildSortedIds(entities)).toEqual(['wf1-step1', 'wf1-step2', 'wf2-step1'])
  })

  it('uses message ID as final tiebreaker', () => {
    const entities: Record<string, MessageEntity> = {
      'bbb': makeEntity({ id: 'bbb', timestamp: '2026-02-17T10:00:00Z' }),
      'aaa': makeEntity({ id: 'aaa', timestamp: '2026-02-17T10:00:00Z' }),
    }
    expect(buildSortedIds(entities)).toEqual(['aaa', 'bbb'])
  })

  it('handles empty entities', () => {
    expect(buildSortedIds({})).toEqual([])
  })

  it('handles mixed messages with and without stepNumbers', () => {
    const entities: Record<string, MessageEntity> = {
      'user-msg': makeEntity({
        id: 'user-msg', messageType: 'user', timestamp: '2026-02-17T10:00:00Z',
      }),
      'step2': makeEntity({
        id: 'step2', timestamp: '2026-02-17T10:00:10Z', stepNumber: 2,
        relatedMessageId: 'user-msg',
      }),
      'step1': makeEntity({
        id: 'step1', timestamp: '2026-02-17T10:00:05Z', stepNumber: 1,
        relatedMessageId: 'user-msg',
      }),
    }
    const sorted = buildSortedIds(entities)
    expect(sorted[0]).toBe('user-msg')
    // step1 and step2 share relatedMessageId, within 60s, so sort by step
    expect(sorted[1]).toBe('step1')
    expect(sorted[2]).toBe('step2')
  })
})
