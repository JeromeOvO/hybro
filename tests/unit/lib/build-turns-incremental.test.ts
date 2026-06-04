// tests/unit/lib/build-turns-incremental.test.ts
import { describe, it, expect, beforeEach } from 'vitest'
import { buildTurns, buildTurnsIncremental } from '@/lib/room-timeline/build-turns'
import type { MessageEntity } from '@/stores/message-store/types'

// ── Helpers ──────────────────────────────────────────────────

let counter = 0

function makeEntity(overrides: Partial<MessageEntity> = {}): MessageEntity {
  counter++
  return {
    id: `msg-${counter}`,
    roomId: 'room-1',
    messageType: 'agent',
    content: `Content ${counter}`,
    senderName: 'Agent',
    timestamp: new Date(2026, 0, 1, 0, 0, counter).toISOString(),
    source: 'db',
    sourceVersion: 1,
    displayType: 'agent-bubble',
    isEphemeral: false,
    createdAt: Date.now(),
    updatedAt: Date.now(),
    ...overrides,
  }
}

function makeUserEntity(overrides: Partial<MessageEntity> = {}): MessageEntity {
  return makeEntity({
    messageType: 'user',
    senderName: 'User',
    displayType: 'user-bubble',
    ...overrides,
  })
}

function makeAgentEntity(overrides: Partial<MessageEntity> = {}): MessageEntity {
  return makeEntity({
    messageType: 'agent',
    senderName: 'Test Agent',
    agentId: 'agent-1',
    displayType: 'agent-bubble',
    ...overrides,
  })
}

function entitiesToMap(entities: MessageEntity[]): Record<string, MessageEntity> {
  const map: Record<string, MessageEntity> = {}
  for (const e of entities) map[e.id] = e
  return map
}

beforeEach(() => { counter = 0 })

// ── Tests ───────────────────────────────────────────────────

describe('buildTurnsIncremental', () => {
  it('1. new agent message only rebuilds active turn', () => {
    // Build initial state: one completed turn
    const u1 = makeUserEntity({ id: 'u1' })
    const a1 = makeAgentEntity({ id: 'a1', taskStatus: 'completed', content: 'Done' })
    const u2 = makeUserEntity({ id: 'u2' })

    const entitiesV1 = entitiesToMap([u1, a1, u2])
    const turnsV1 = buildTurns(entitiesV1, ['u1', 'a1', 'u2'], [])

    expect(turnsV1).toHaveLength(2)

    // Add a new agent message to the active turn
    const a2 = makeAgentEntity({ id: 'a2', taskStatus: 'completed', content: 'New reply' })
    const entitiesV2 = { ...entitiesV1, 'a2': a2 }

    const turnsV2 = buildTurnsIncremental(turnsV1, entitiesV2, ['u1', 'a1', 'u2', 'a2'], [])

    expect(turnsV2).toHaveLength(2)
    // Active turn (u2) should have the new agent result
    expect(turnsV2[1].agentResults).toHaveLength(1)
    expect(turnsV2[1].agentResults[0].messageId).toBe('a2')
  })

  it('2. older turns maintain referential identity (===)', () => {
    const u1 = makeUserEntity({ id: 'u1' })
    const a1 = makeAgentEntity({ id: 'a1', taskStatus: 'completed', content: 'Done' })
    const u2 = makeUserEntity({ id: 'u2' })

    const entitiesV1 = entitiesToMap([u1, a1, u2])
    const turnsV1 = buildTurns(entitiesV1, ['u1', 'a1', 'u2'], [])

    // Add agent to active turn — older turn should be the same reference
    const a2 = makeAgentEntity({ id: 'a2', taskStatus: 'completed', content: 'New' })
    const entitiesV2 = { ...entitiesV1, 'a2': a2 }

    const turnsV2 = buildTurnsIncremental(turnsV1, entitiesV2, ['u1', 'a1', 'u2', 'a2'], [])

    // First turn (u1) should be referentially identical (===)
    expect(turnsV2[0]).toBe(turnsV1[0])
    // Second turn (u2) is rebuilt — different reference
    expect(turnsV2[1]).not.toBe(turnsV1[1])
  })

  it('3. relatedMessageId to old turn rebuilds that turn', () => {
    const u1 = makeUserEntity({ id: 'u1' })
    const a1 = makeAgentEntity({ id: 'a1', taskStatus: 'completed', content: 'V1' })
    const u2 = makeUserEntity({ id: 'u2' })

    const entitiesV1 = entitiesToMap([u1, a1, u2])
    const turnsV1 = buildTurns(entitiesV1, ['u1', 'a1', 'u2'], [])

    // Late agent arrives pointing back to u1
    const aLate = makeAgentEntity({
      id: 'a-late',
      relatedMessageId: 'u1',
      taskStatus: 'completed',
      content: 'Late reply to first question',
    })
    const entitiesV2 = { ...entitiesV1, 'a-late': aLate }

    const turnsV2 = buildTurnsIncremental(turnsV1, entitiesV2, ['u1', 'a1', 'u2', 'a-late'], [])

    expect(turnsV2).toHaveLength(2)
    // First turn is rebuilt (different reference) because it got a new agent
    expect(turnsV2[0]).not.toBe(turnsV1[0])
    expect(turnsV2[0].agentResults).toHaveLength(2)
    expect(turnsV2[0].agentResults.some(r => r.messageId === 'a-late')).toBe(true)
  })

  it('4. new user message creates new active turn', () => {
    const u1 = makeUserEntity({ id: 'u1' })
    const a1 = makeAgentEntity({ id: 'a1', taskStatus: 'completed', content: 'Done' })

    const entitiesV1 = entitiesToMap([u1, a1])
    const turnsV1 = buildTurns(entitiesV1, ['u1', 'a1'], [])

    expect(turnsV1).toHaveLength(1)

    // New user message
    const u2 = makeUserEntity({ id: 'u2' })
    const entitiesV2 = { ...entitiesV1, 'u2': u2 }

    const turnsV2 = buildTurnsIncremental(turnsV1, entitiesV2, ['u1', 'a1', 'u2'], [])

    expect(turnsV2).toHaveLength(2)
    // First turn preserved
    expect(turnsV2[0]).toBe(turnsV1[0])
    // New turn created
    expect(turnsV2[1].userMessageId).toBe('u2')
    expect(turnsV2[1].agentResults).toHaveLength(0)
  })

  it('5. empty prev turns delegates to full buildTurns', () => {
    const u1 = makeUserEntity({ id: 'u1' })
    const a1 = makeAgentEntity({ id: 'a1', taskStatus: 'completed', content: 'Reply' })

    const entities = entitiesToMap([u1, a1])
    const turnsFromIncremental = buildTurnsIncremental([], entities, ['u1', 'a1'], [])
    const turnsFromFull = buildTurns(entities, ['u1', 'a1'], [])

    expect(turnsFromIncremental).toHaveLength(turnsFromFull.length)
    expect(turnsFromIncremental[0].id).toBe(turnsFromFull[0].id)
    expect(turnsFromIncremental[0].agentResults.length).toBe(turnsFromFull[0].agentResults.length)
    expect(turnsFromIncremental[0].status).toBe(turnsFromFull[0].status)
  })

  it('rebuilds a turn when processing status logs change', () => {
    const userV1 = makeUserEntity({
      id: 'u1',
      clientRequestId: 'cr-1',
    })
    const turnsV1 = buildTurns(entitiesToMap([userV1]), ['u1'], [])

    const userV2 = {
      ...userV1,
      processingStatusLogs: [
        {
          id: 'log-1',
          message: 'Dispatching agents',
          timestamp: '2026-06-03T12:00:01.000Z',
        },
      ],
    }

    const turnsV2 = buildTurnsIncremental(
      turnsV1,
      entitiesToMap([userV2]),
      ['u1'],
      [],
    )

    expect(turnsV2[0]).not.toBe(turnsV1[0])
    expect(turnsV2[0].processingStatusLogs).toHaveLength(1)
  })
})
