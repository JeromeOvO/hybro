/**
 * Regression tests for MessageEntity type conformance.
 *
 * Ensures the makeEntity test helper and MessageEntity type
 * stay aligned after removing stale fields (taskId, type, parentId).
 */
import { describe, it, expect } from 'vitest'
import type { MessageEntity } from '@/stores/message-store/types'

function makeEntity(overrides: Partial<MessageEntity> = {}): MessageEntity {
  return {
    id: 'msg-1',
    content: 'Hello, world!',
    senderName: 'Test User',
    timestamp: new Date().toISOString(),
    messageType: 'user',
    source: 'db',
    sourceVersion: 1,
    displayType: 'user-bubble',
    isEphemeral: false,
    createdAt: Date.now(),
    updatedAt: Date.now(),
    agentId: undefined,
    taskStatus: undefined,
    roomId: 'room-1',
    ...overrides,
  }
}

const ENTITY_REQUIRED_KEYS: (keyof MessageEntity)[] = [
  'id',
  'roomId',
  'messageType',
  'content',
  'senderName',
  'timestamp',
  'source',
  'sourceVersion',
  'displayType',
  'isEphemeral',
  'createdAt',
  'updatedAt',
]

describe('MessageEntity type conformance', () => {
  it('makeEntity returns all required fields', () => {
    const entity = makeEntity()
    for (const key of ENTITY_REQUIRED_KEYS) {
      expect(entity).toHaveProperty(key)
      expect(entity[key]).toBeDefined()
    }
  })

  it('does not include stale fields (taskId, type, parentId)', () => {
    const entity = makeEntity()
    expect(entity).not.toHaveProperty('taskId')
    expect(entity).not.toHaveProperty('type')
    expect(entity).not.toHaveProperty('parentId')
  })

  it('uses messageType instead of type', () => {
    const entity = makeEntity()
    expect(entity.messageType).toBe('user')
    expect((entity as Record<string, unknown>)['type']).toBeUndefined()
  })

  it('applies overrides correctly', () => {
    const entity = makeEntity({
      messageType: 'agent',
      agentId: 'agent-1',
      content: 'Agent reply',
    })
    expect(entity.messageType).toBe('agent')
    expect(entity.agentId).toBe('agent-1')
    expect(entity.content).toBe('Agent reply')
  })

  it('includes provenance fields', () => {
    const entity = makeEntity()
    expect(entity.source).toBe('db')
    expect(typeof entity.sourceVersion).toBe('number')
    expect(entity.displayType).toBe('user-bubble')
    expect(entity.isEphemeral).toBe(false)
    expect(typeof entity.createdAt).toBe('number')
    expect(typeof entity.updatedAt).toBe('number')
  })
})
