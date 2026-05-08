import { describe, it, expect } from 'vitest'
import { applyUpsert, isNoOpUpdate, buildSortedIds, mergeArtifacts, extractTextFromArtifacts } from '../upsert'
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
        'msg-1': makeEntity({ taskStatus: 'failed', displayType: 'agent-bubble' }),
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
    it('rejects DB update when SSE entity is in working state and DB is also non-terminal', () => {
      const entities = {
        'msg-1': makeEntity({ source: 'sse', taskStatus: 'working', displayType: 'agent-bubble' }),
      }
      const result = applyUpsert(
        entities, ['msg-1'],
        makeIncoming({ taskStatus: 'working', content: 'DB version' }),
        'db',
      )
      expect(result).toBeNull()
    })

    it('allows DB terminal upgrade when SSE entity is stuck at non-terminal', () => {
      const entities = {
        'msg-1': makeEntity({
          source: 'sse', taskStatus: 'working', content: '',
          displayType: 'agent-bubble',
        }),
      }
      const result = applyUpsert(
        entities, ['msg-1'],
        makeIncoming({ taskStatus: 'completed', content: 'Final answer from DB' }),
        'db',
      )
      expect(result).not.toBeNull()
      expect(result!.entities['msg-1'].taskStatus).toBe('completed')
      expect(result!.entities['msg-1'].content).toBe('Final answer from DB')
    })

    it('blocks DB update when SSE agent entity is completed with content (Rule 2b: prevents post-stream duplicate re-render)', () => {
      // Rule 2b: DB reconcile must not overwrite already-completed SSE agent content
      // unless the DB body is materially longer (truncated stream). This prevents
      // a re-render after streaming finishes that looks like a duplicate message.
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
      expect(result).toBeNull()
    })

    it('blocks DB to non-materially extend SSE agent body on reconcile (Rule 2b)', () => {
      // DB body is only slightly longer than SSE (< DB_RECONCILE_MATERIAL_LENGTH_DELTA chars)
      // → treat as cosmetic/normalization difference, skip to avoid re-render.
      const entities = {
        'msg-1': makeEntity({
          source: 'sse', taskStatus: 'completed',
          content: 'Hello', displayType: 'agent-bubble',
        }),
      }
      const result = applyUpsert(
        entities, ['msg-1'],
        makeIncoming({ taskStatus: 'completed', content: 'Hello world' }),
        'db',
      )
      expect(result).toBeNull()
    })

    it('blocks DB update when SSE agent entity is completed with artifacts only (Rule 2b: Hermes-style agent)', () => {
      // Hermes-style agents stream entirely via artifacts with empty content field.
      // Rule 2b must use artifact text length as the "renderable" measure, not
      // just content length, otherwise the rule is bypassed and DB reconcile
      // re-renders a duplicate message after streaming finishes.
      const entities = {
        'msg-1': makeEntity({
          source: 'sse', taskStatus: 'completed',
          content: '', // empty — Hermes delivers via artifacts
          artifacts: [{ artifactId: 'a1', name: 'result', parts: [{ kind: 'text' as const, text: 'Long Hermes response text here' }] }],
          displayType: 'agent-bubble',
        }),
      }
      const result = applyUpsert(
        entities, ['msg-1'],
        makeIncoming({ taskStatus: 'completed', content: 'Long Hermes response text here' }),
        'db',
      )
      expect(result).toBeNull()
    })

    it('allows DB failed→completed upgrade even if SSE already has content (Rule 2b exception)', () => {
      // Rule 2b does NOT block when existing is 'failed' — DB may carry the real result.
      const entities = {
        'msg-1': makeEntity({
          source: 'sse', taskStatus: 'failed',
          content: 'error text', displayType: 'agent-bubble',
        }),
      }
      const result = applyUpsert(
        entities, ['msg-1'],
        makeIncoming({ taskStatus: 'completed', content: 'Result' }),
        'db',
      )
      expect(result).not.toBeNull()
      expect(result!.entities['msg-1'].taskStatus).toBe('completed')
    })

    it('prefers DB body when materially longer than SSE (truncated stream)', () => {
      const longDb = `${'x'.repeat(60)} full ending`
      const entities = {
        'msg-1': makeEntity({
          source: 'sse', taskStatus: 'completed',
          content: 'short partial',
          displayType: 'agent-bubble',
        }),
      }
      const result = applyUpsert(
        entities, ['msg-1'],
        makeIncoming({ taskStatus: 'completed', content: longDb }),
        'db',
      )
      expect(result).not.toBeNull()
      expect(result!.entities['msg-1'].content).toBe(longDb)
    })

    it('allows SSE update when entity was from DB with non-terminal state', () => {
      const entities = {
        'msg-1': makeEntity({ source: 'db', taskStatus: 'working', displayType: 'agent-bubble' }),
      }
      const result = applyUpsert(
        entities, ['msg-1'],
        makeIncoming({ taskStatus: 'working', content: 'SSE update' }),
        'sse',
      )
      expect(result).not.toBeNull()
    })

    it('keeps working task with content as agent-bubble', () => {
      const entities = {
        'msg-1': makeEntity({ source: 'db', taskStatus: 'working', displayType: 'agent-bubble', content: '' }),
      }
      const result = applyUpsert(
        entities, ['msg-1'],
        makeIncoming({ taskStatus: 'working', content: 'SSE update' }),
        'sse',
      )
      expect(result).not.toBeNull()
      expect(result!.entities['msg-1'].displayType).toBe('agent-bubble')
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
        'msg-1': makeEntity({ isEphemeral: true, displayType: 'agent-bubble', taskStatus: 'working' }),
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
        'msg-1': makeEntity({ isEphemeral: true, displayType: 'agent-bubble', taskStatus: 'working' }),
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
        'msg-1': makeEntity({
          content: 'Hello',
          displayType: 'agent-bubble',
          source: 'db',
        }),
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
          displayType: 'agent-bubble',
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
          displayType: 'agent-bubble',
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
      displayType: 'agent-bubble',
    })
    const incoming = makeIncoming({ taskStatus: 'completed', content: 'Done' })
    expect(isNoOpUpdate(existing, incoming, 'sse')).toBe(false)
  })

  it('returns false when taskError changes from string to null (Gap 9)', () => {
    const existing = makeEntity({
      taskError: 'Some error',
      taskStatus: 'failed',
      displayType: 'agent-bubble',
    })
    const incoming = makeIncoming({ taskError: null, taskStatus: 'failed' })
    expect(isNoOpUpdate(existing, incoming, 'db')).toBe(false)
  })

  it('returns true when taskError is undefined (not provided) and existing has error', () => {
    const existing = makeEntity({
      taskError: 'Some error',
      taskStatus: 'failed',
      displayType: 'agent-bubble',
    })
    const incoming = makeIncoming({ taskStatus: 'failed' })
    // taskError not in incoming → undefined → coalesces to existing value → no change
    expect(isNoOpUpdate(existing, incoming, 'db')).toBe(true)
  })

  it('returns false when stepNumber changes', () => {
    const existing = makeEntity({
      stepNumber: 1,
      taskStatus: 'working',
      displayType: 'agent-bubble',
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

  it('returns false when agentSource changes', () => {
    const existing = makeEntity({
      content: 'Hello',
      agentSource: undefined,
      displayType: 'agent-bubble',
    })
    const incoming = makeIncoming({ content: 'Hello', agentSource: 'hub' })
    expect(isNoOpUpdate(existing, incoming, 'db')).toBe(false)
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

describe('mergeArtifacts — same-name text-only dedup', () => {
  it('replaces existing text-only artifact with same name but different ID', () => {
    const existing = [
      { artifactId: 'id-1', name: 'current_result', parts: [{ kind: 'text' as const, text: 'Hello' }] },
    ]
    const incoming = { artifactId: 'id-2', name: 'current_result', parts: [{ kind: 'text' as const, text: 'Hello world' }] }
    const result = mergeArtifacts(existing, incoming, false)
    expect(result).toHaveLength(1)
    expect(result[0].artifactId).toBe('id-2')
    expect(result[0].parts[0].text).toBe('Hello world')
  })

  it('does not dedup if artifact has non-text parts', () => {
    const existing = [
      { artifactId: 'id-1', name: 'output', parts: [{ kind: 'text' as const, text: 'Hello' }] },
    ]
    const incoming = { artifactId: 'id-2', name: 'output', parts: [{ kind: 'file' as const, file: { uri: 'x' } }] }
    const result = mergeArtifacts(existing, incoming, false)
    expect(result).toHaveLength(2)
  })

  it('does not dedup if names differ', () => {
    const existing = [
      { artifactId: 'id-1', name: 'result-a', parts: [{ kind: 'text' as const, text: 'Hello' }] },
    ]
    const incoming = { artifactId: 'id-2', name: 'result-b', parts: [{ kind: 'text' as const, text: 'World' }] }
    const result = mergeArtifacts(existing, incoming, false)
    expect(result).toHaveLength(2)
  })

  it('still uses normal ID-based merge when IDs match', () => {
    const existing = [
      { artifactId: 'same-id', name: 'result', parts: [{ kind: 'text' as const, text: 'old' }] },
    ]
    const incoming = { artifactId: 'same-id', name: 'result', parts: [{ kind: 'text' as const, text: 'new' }] }
    const result = mergeArtifacts(existing, incoming, false)
    expect(result).toHaveLength(1)
    expect(result[0].parts[0].text).toBe('new')
  })
})

describe('extractTextFromArtifacts', () => {
  it('extracts text from the longest text-only artifact', () => {
    const artifacts = [
      { artifactId: 'a1', parts: [{ kind: 'text' as const, text: 'short' }] },
      { artifactId: 'a2', parts: [{ kind: 'text' as const, text: 'this is much longer text' }] },
    ]
    expect(extractTextFromArtifacts(artifacts as any)).toBe('this is much longer text')
  })

  it('returns empty string when no text-only artifacts exist', () => {
    const artifacts = [
      { artifactId: 'a1', parts: [{ kind: 'file' as const, file: { uri: 'x' } }] },
    ]
    expect(extractTextFromArtifacts(artifacts as any)).toBe('')
  })

  it('skips mixed artifacts (not all-text)', () => {
    const artifacts = [
      { artifactId: 'a1', parts: [{ kind: 'text' as const, text: 'text' }, { kind: 'file' as const, file: { uri: 'x' } }] },
    ]
    expect(extractTextFromArtifacts(artifacts as any)).toBe('')
  })
})
