// tests/unit/lib/build-turns.test.ts
import { describe, it, expect } from 'vitest'
import { buildTurns } from '@/lib/room-timeline/build-turns'
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
    timestamp: new Date(Date.now() + counter * 1000).toISOString(),
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

// Reset counter before each test
import { beforeEach } from 'vitest'
beforeEach(() => { counter = 0 })

// ── Tests ───────────────────────────────────────────────────

describe('buildTurns – core construction', () => {
  it('1. empty messages returns empty turns', () => {
    const turns = buildTurns({}, [], [])
    expect(turns).toEqual([])
  })

  it('2. single user message produces one turn with no agent results', () => {
    const user = makeUserEntity({ id: 'u1' })
    const turns = buildTurns(entitiesToMap([user]), ['u1'], [])

    expect(turns).toHaveLength(1)
    expect(turns[0].userMessageId).toBe('u1')
    expect(turns[0].userContent).toBe(user.content)
    expect(turns[0].agentResults).toHaveLength(0)
    expect(turns[0].status).toBe('active')
  })

  it('3. user + agent produces one turn with one agent result', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
      content: 'Agent reply',
      taskStatus: 'completed',
    })
    const entities = entitiesToMap([user, agent])
    const turns = buildTurns(entities, ['u1', 'a1'], [])

    expect(turns).toHaveLength(1)
    expect(turns[0].userMessageId).toBe('u1')
    expect(turns[0].agentResults).toHaveLength(1)
    expect(turns[0].agentResults[0].messageId).toBe('a1')
    expect(turns[0].agentResults[0].content).toBe('Agent reply')
    expect(turns[0].status).toBe('completed')
  })

  it('4. two user messages produce two turns', () => {
    const u1 = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const a1 = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
      taskStatus: 'completed',
    })
    const u2 = makeUserEntity({ id: 'u2', timestamp: '2026-01-01T00:00:02Z' })
    const a2 = makeAgentEntity({
      id: 'a2',
      timestamp: '2026-01-01T00:00:03Z',
      taskStatus: 'completed',
    })

    const entities = entitiesToMap([u1, a1, u2, a2])
    const turns = buildTurns(entities, ['u1', 'a1', 'u2', 'a2'], [])

    expect(turns).toHaveLength(2)
    expect(turns[0].userMessageId).toBe('u1')
    expect(turns[0].agentResults).toHaveLength(1)
    expect(turns[0].agentResults[0].messageId).toBe('a1')
    expect(turns[1].userMessageId).toBe('u2')
    expect(turns[1].agentResults).toHaveLength(1)
    expect(turns[1].agentResults[0].messageId).toBe('a2')
  })

  it('5. agent messages before first user message go to synthetic system turn', () => {
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:00Z',
      taskStatus: 'completed',
      content: 'System greeting',
    })
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:01Z' })

    const entities = entitiesToMap([agent, user])
    const turns = buildTurns(entities, ['a1', 'u1'], [])

    expect(turns).toHaveLength(2)
    // First turn is the synthetic system turn
    expect(turns[0].id).toBe('system-turn')
    expect(turns[0].userMessageId).toBeNull()
    expect(turns[0].userContent).toBe('')
    expect(turns[0].agentResults).toHaveLength(1)
    expect(turns[0].agentResults[0].content).toBe('System greeting')
    // Second turn is the user turn
    expect(turns[1].userMessageId).toBe('u1')
  })

  it('6. relatedMessageId routes agent to the correct turn', () => {
    const u1 = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const u2 = makeUserEntity({ id: 'u2', timestamp: '2026-01-01T00:00:02Z' })
    // This agent arrives late but is related to u1
    const lateComer = makeAgentEntity({
      id: 'a-late',
      timestamp: '2026-01-01T00:00:03Z',
      relatedMessageId: 'u1',
      taskStatus: 'completed',
      content: 'Late response to first question',
    })

    const entities = entitiesToMap([u1, u2, lateComer])
    const turns = buildTurns(entities, ['u1', 'u2', 'a-late'], [])

    expect(turns).toHaveLength(2)
    // Late-comer should be routed to u1's turn, not u2's
    expect(turns[0].userMessageId).toBe('u1')
    expect(turns[0].agentResults).toHaveLength(1)
    expect(turns[0].agentResults[0].messageId).toBe('a-late')
    expect(turns[1].userMessageId).toBe('u2')
    expect(turns[1].agentResults).toHaveLength(0)
  })

  it('7. multiple agents in one turn', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const agentA = makeAgentEntity({
      id: 'a1',
      agentId: 'agent-a',
      senderName: 'Agent A',
      timestamp: '2026-01-01T00:00:01Z',
      taskStatus: 'completed',
      content: 'Response from A',
    })
    const agentB = makeAgentEntity({
      id: 'a2',
      agentId: 'agent-b',
      senderName: 'Agent B',
      timestamp: '2026-01-01T00:00:02Z',
      taskStatus: 'completed',
      content: 'Response from B',
    })

    const entities = entitiesToMap([user, agentA, agentB])
    const turns = buildTurns(entities, ['u1', 'a1', 'a2'], [])

    expect(turns).toHaveLength(1)
    expect(turns[0].agentResults).toHaveLength(2)
    expect(turns[0].agentResults[0].agentName).toBe('Agent A')
    expect(turns[0].agentResults[1].agentName).toBe('Agent B')
    expect(turns[0].status).toBe('completed')
  })

  it('8. failed agent in turn produces failed status when all agents fail', () => {
    const user = makeUserEntity({ id: 'u1' })
    const agent = makeAgentEntity({
      id: 'a1',
      taskStatus: 'failed',
      content: 'Something went wrong',
    })

    const entities = entitiesToMap([user, agent])
    const turns = buildTurns(entities, ['u1', 'a1'], [])

    expect(turns).toHaveLength(1)
    expect(turns[0].agentResults[0].status).toBe('failed')
    expect(turns[0].status).toBe('failed')
  })

  it('9. HITL agent in turn is detected', () => {
    const user = makeUserEntity({ id: 'u1' })
    const agent = makeAgentEntity({
      id: 'a1',
      taskStatus: 'input-required',
      hitlRequestId: 'hitl-1',
      hitlPrompt: 'Please confirm',
      content: '',
    })

    const entities = entitiesToMap([user, agent])
    const turns = buildTurns(entities, ['u1', 'a1'], [])

    expect(turns).toHaveLength(1)
    expect(turns[0].agentResults[0].status).toBe('awaiting_input')
    expect(turns[0].status).toBe('active')
  })

  it('10. turn status derives correctly for mixed results', () => {
    const user = makeUserEntity({ id: 'u1' })
    const successAgent = makeAgentEntity({
      id: 'a1',
      agentId: 'agent-a',
      taskStatus: 'completed',
      content: 'Success',
    })
    const failedAgent = makeAgentEntity({
      id: 'a2',
      agentId: 'agent-b',
      taskStatus: 'failed',
      content: 'Error',
    })

    const entities = entitiesToMap([user, successAgent, failedAgent])
    const turns = buildTurns(entities, ['u1', 'a1', 'a2'], [])

    expect(turns).toHaveLength(1)
    expect(turns[0].status).toBe('partial')
    expect(turns[0].agentResults).toHaveLength(2)
  })

  describe('buildTurns – summary selection', () => {
    it('11. supervisor result selected as summary', () => {
      const user = makeUserEntity({ id: 'u1' })
      const normalAgent = makeAgentEntity({
        id: 'a1',
        agentId: 'agent-normal',
        senderName: 'Code Agent',
        taskStatus: 'completed',
        content: 'Normal agent response',
      })
      const supervisorAgent = makeAgentEntity({
        id: 'a2',
        agentId: 'agent-sup',
        senderName: 'Supervisor Agent',
        taskStatus: 'completed',
        content: '# Summary\nThe team has completed the analysis.',
      })

      const entities = entitiesToMap([user, normalAgent, supervisorAgent])
      const turns = buildTurns(entities, ['u1', 'a1', 'a2'], [])

      expect(turns[0].summary).not.toBeNull()
      expect(turns[0].summary!.sourceAgentName).toBe('Supervisor Agent')
      expect(turns[0].summary!.title).toBe('Summary')
      expect(turns[0].summary!.body).toContain('The team has completed the analysis.')
    })

    it('12. fallback to first completed agent when no supervisor', () => {
      const user = makeUserEntity({ id: 'u1' })
      const agentA = makeAgentEntity({
        id: 'a1',
        agentId: 'agent-a',
        senderName: 'Agent Alpha',
        taskStatus: 'completed',
        content: 'First response with content',
      })
      const agentB = makeAgentEntity({
        id: 'a2',
        agentId: 'agent-b',
        senderName: 'Agent Beta',
        taskStatus: 'completed',
        content: 'Second response with content',
      })

      const entities = entitiesToMap([user, agentA, agentB])
      const turns = buildTurns(entities, ['u1', 'a1', 'a2'], [])

      expect(turns[0].summary).not.toBeNull()
      // First completed agent in ordering wins
      expect(turns[0].summary!.sourceAgentName).toBe('Agent Alpha')
    })

    it('13. no completed agents returns null summary', () => {
      const user = makeUserEntity({ id: 'u1' })
      const agent = makeAgentEntity({
        id: 'a1',
        taskStatus: 'working',
        content: '',
      })

      const entities = entitiesToMap([user, agent])
      const turns = buildTurns(entities, ['u1', 'a1'], [])

      expect(turns[0].summary).toBeNull()
    })

    it('14. completed agent with empty content is skipped for summary', () => {
      const user = makeUserEntity({ id: 'u1' })
      const emptyAgent = makeAgentEntity({
        id: 'a1',
        agentId: 'agent-empty',
        senderName: 'Empty Agent',
        taskStatus: 'completed',
        content: '',
      })
      const contentAgent = makeAgentEntity({
        id: 'a2',
        agentId: 'agent-content',
        senderName: 'Content Agent',
        taskStatus: 'completed',
        content: 'Actual meaningful response',
      })

      const entities = entitiesToMap([user, emptyAgent, contentAgent])
      const turns = buildTurns(entities, ['u1', 'a1', 'a2'], [])

      expect(turns[0].summary).not.toBeNull()
      // Empty agent skipped, Content Agent selected
      expect(turns[0].summary!.sourceAgentName).toBe('Content Agent')
    })

    it('15. failed agents are excluded from summary selection', () => {
      const user = makeUserEntity({ id: 'u1' })
      const failedAgent = makeAgentEntity({
        id: 'a1',
        agentId: 'agent-fail',
        senderName: 'Failed Agent',
        taskStatus: 'failed',
        content: 'Error: something broke',
      })
      const successAgent = makeAgentEntity({
        id: 'a2',
        agentId: 'agent-ok',
        senderName: 'Success Agent',
        taskStatus: 'completed',
        content: 'Valid response here',
      })

      const entities = entitiesToMap([user, failedAgent, successAgent])
      const turns = buildTurns(entities, ['u1', 'a1', 'a2'], [])

      expect(turns[0].summary).not.toBeNull()
      // Failed agent must not be selected
      expect(turns[0].summary!.sourceAgentName).toBe('Success Agent')
    })
  })
})
