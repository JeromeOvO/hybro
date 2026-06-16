import { describe, it, expect } from 'vitest'
import { groupMessagesByUserTurn, escapeCssIdent } from './message-groups'
import type { MessageEntity } from '@/stores/message-store'

function entity(id: string, messageType: 'user' | 'agent'): MessageEntity {
  return {
    id,
    messageType,
    displayType: messageType === 'user' ? 'user-bubble' : 'agent-bubble',
    roomId: 'room-1',
    createdAt: Date.now(),
  } as MessageEntity
}

describe('groupMessagesByUserTurn', () => {
  it('returns empty array for empty input', () => {
    expect(groupMessagesByUserTurn([], {})).toEqual([])
  })

  it('groups system prefix (non-user messages before first user message)', () => {
    const entities: Record<string, MessageEntity> = {
      a1: entity('a1', 'agent'),
      a2: entity('a2', 'agent'),
    }
    const result = groupMessagesByUserTurn(['a1', 'a2'], entities)
    expect(result).toEqual([
      { userMsgId: null, childMsgIds: ['a1', 'a2'] },
    ])
  })

  it('groups normal user -> agent sequence', () => {
    const entities: Record<string, MessageEntity> = {
      u1: entity('u1', 'user'),
      a1: entity('a1', 'agent'),
      a2: entity('a2', 'agent'),
    }
    const result = groupMessagesByUserTurn(['u1', 'a1', 'a2'], entities)
    expect(result).toEqual([
      { userMsgId: 'u1', childMsgIds: ['a1', 'a2'] },
    ])
  })

  it('handles consecutive user messages as separate groups', () => {
    const entities: Record<string, MessageEntity> = {
      u1: entity('u1', 'user'),
      u2: entity('u2', 'user'),
      a1: entity('a1', 'agent'),
    }
    const result = groupMessagesByUserTurn(['u1', 'u2', 'a1'], entities)
    expect(result).toEqual([
      { userMsgId: 'u1', childMsgIds: [] },
      { userMsgId: 'u2', childMsgIds: ['a1'] },
    ])
  })

  it('handles system prefix followed by user turn', () => {
    const entities: Record<string, MessageEntity> = {
      a0: entity('a0', 'agent'),
      u1: entity('u1', 'user'),
      a1: entity('a1', 'agent'),
    }
    const result = groupMessagesByUserTurn(['a0', 'u1', 'a1'], entities)
    expect(result).toEqual([
      { userMsgId: null, childMsgIds: ['a0'] },
      { userMsgId: 'u1', childMsgIds: ['a1'] },
    ])
  })

  it('handles multiple user turns', () => {
    const entities: Record<string, MessageEntity> = {
      u1: entity('u1', 'user'),
      a1: entity('a1', 'agent'),
      u2: entity('u2', 'user'),
      a2: entity('a2', 'agent'),
      a3: entity('a3', 'agent'),
    }
    const result = groupMessagesByUserTurn(['u1', 'a1', 'u2', 'a2', 'a3'], entities)
    expect(result).toEqual([
      { userMsgId: 'u1', childMsgIds: ['a1'] },
      { userMsgId: 'u2', childMsgIds: ['a2', 'a3'] },
    ])
  })

  it('groups agent with relatedMessageId by timeline order (documents divergence from build-turns.ts)', () => {
    const entities: Record<string, MessageEntity> = {
      u1: entity('u1', 'user'),
      a1: entity('a1', 'agent'),
      u2: entity('u2', 'user'),
      a2: { ...entity('a2', 'agent'), relatedMessageId: 'u1' } as MessageEntity,
    }
    const result = groupMessagesByUserTurn(['u1', 'a1', 'u2', 'a2'], entities)
    expect(result).toEqual([
      { userMsgId: 'u1', childMsgIds: ['a1'] },
      { userMsgId: 'u2', childMsgIds: ['a2'] },
    ])
  })

  it('skips missing entities gracefully', () => {
    const entities: Record<string, MessageEntity> = {
      u1: entity('u1', 'user'),
    }
    const result = groupMessagesByUserTurn(['u1', 'missing-id'], entities)
    expect(result).toEqual([
      { userMsgId: 'u1', childMsgIds: ['missing-id'] },
    ])
  })
})

describe('escapeCssIdent', () => {
  it('passes through simple strings', () => {
    expect(escapeCssIdent('abc-123')).toBe('abc-123')
  })

  it('escapes double quotes in fallback mode', () => {
    const originalCss = globalThis.CSS
    // @ts-expect-error test override — remove CSS entirely to test fallback
    globalThis.CSS = undefined
    try {
      expect(escapeCssIdent('a"b')).toBe('a\\"b')
    } finally {
      globalThis.CSS = originalCss
    }
  })
})
