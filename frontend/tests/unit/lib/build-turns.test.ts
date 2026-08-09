// tests/unit/lib/build-turns.test.ts
import { describe, it, expect } from 'vitest'
import { buildTurns, selectSummary, buildTurnsIncremental, deriveTurnPhase } from '@/lib/room-timeline/build-turns'
import { derivePrimaryStreamFromFinalAnswer } from '@/lib/room-timeline/derive-final-answer'
import { convertApiMessageToIncoming } from '@/stores/message-store/convert-api-message'
import type { MessageEntity } from '@/stores/message-store/types'
import type { RoomMessage } from '@/lib/types/response'

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
    expect(turns[0].status).toBe('awaiting_input')
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
        agentId: 'system:hybro',
        senderName: 'Summary Agent',
        taskStatus: 'completed',
        content: '# Summary\nThe team has completed the analysis.',
      })

      const entities = entitiesToMap([user, normalAgent, supervisorAgent])
      const turns = buildTurns(entities, ['u1', 'a1', 'a2'], [])

      expect(turns[0].summary).not.toBeNull()
      expect(turns[0].summary!.sourceAgentName).toBe('Summary Agent')
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

describe('buildTurns – V2 data model', () => {
  // ── Ephemeral placeholder handling ───────────────────────

  it('does not render arbitrary taskContent as agent reply detail', () => {
    const user = makeUserEntity({
      id: 'u1',
      timestamp: '2026-01-01T00:00:00Z',
      content: 'Get a quote',
    })
    const privateTaskContent = 'Evaluate the confidential renewal file and include the internal premium ceiling'
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
      relatedMessageId: 'u1',
      agentId: 'agent-1',
      senderName: 'Insurer Agent',
      content: '',
      taskStatus: 'working' as any,
      taskContent: privateTaskContent,
    })

    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])

    const serialized = JSON.stringify(turns[0])
    expect(serialized).not.toContain(privateTaskContent)
    expect(turns[0].agentResults[0].taskStatusMessage).toBe('Working')
  })

  it('renders explicit public taskStatusMessage as active agent status', () => {
    const user = makeUserEntity({
      id: 'u1',
      timestamp: '2026-01-01T00:00:00Z',
      content: 'Get a quote',
    })
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
      relatedMessageId: 'u1',
      agentId: 'agent-1',
      senderName: 'Insurer Agent',
      content: '',
      taskStatus: 'working' as any,
      taskStatusMessage: 'Requesting Insurer Agent',
      taskContent: 'Evaluate the confidential renewal file and include the internal premium ceiling',
    })

    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    const serialized = JSON.stringify(turns[0])

    expect(turns[0].agentResults[0].taskStatusMessage).toBe('Requesting Insurer Agent')
    expect(serialized).toContain('Requesting Insurer Agent')
    expect(serialized).not.toContain('confidential renewal file')
  })

  it('suppresses ephemeral placeholder when real agent shares clientRequestId', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z', clientRequestId: 'cr-1' })
    const placeholder = makeEntity({
      id: 'placeholder-1',
      messageType: 'agent',
      senderName: 'HYBRO AI',
      isEphemeral: true,
      clientRequestId: 'cr-1',
      taskStatus: 'working' as any,
      taskContent: '',
      timestamp: '2026-01-01T00:00:01Z',
    })
    const realAgent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:02Z',
      agentId: 'agent-real-1',
      clientRequestId: 'cr-1',
    })
    const turns = buildTurns(
      entitiesToMap([user, placeholder, realAgent]),
      ['u1', 'placeholder-1', 'a1'],
      [],
    )
    expect(turns[0].agentResults).toHaveLength(1)
    expect(turns[0].agentResults[0].agentId).toBe('agent-real-1')
  })

  it('suppresses Planning ephemeral when all real agents completed (DONE path)', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z', clientRequestId: 'cr-1' })
    const placeholder = makeEntity({
      id: 'placeholder-1',
      messageType: 'agent',
      senderName: 'HYBRO AI',
      isEphemeral: true,
      clientRequestId: 'cr-1',
      taskStatus: 'working' as any,
      taskContent: 'Planning next action...',
      timestamp: '2026-01-01T00:00:03Z',
    })
    const realAgent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:02Z',
      agentId: 'agent-real-1',
      clientRequestId: 'cr-1',
      taskStatus: 'completed',
      content: 'Real response',
    })
    const turns = buildTurns(
      entitiesToMap([user, realAgent, placeholder]),
      ['u1', 'a1', 'placeholder-1'],
      [],
    )
    expect(turns[0].agentResults).toHaveLength(1)
    expect(turns[0].agentResults[0].agentId).toBe('agent-real-1')
    expect(turns[0].displayMode).toBe('single_agent')
  })

  it('suppresses Planning ephemeral without clientRequestId when agent completed', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z', clientRequestId: 'cr-1' })
    const placeholder = makeEntity({
      id: 'placeholder-1',
      messageType: 'agent',
      senderName: 'HYBRO AI',
      isEphemeral: true,
      taskStatus: 'working' as any,
      taskContent: 'Planning next action...',
      timestamp: '2026-01-01T00:00:03Z',
    })
    const realAgent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:02Z',
      agentId: 'agent-real-1',
      clientRequestId: 'cr-1',
      taskStatus: 'completed',
      content: 'Real response',
    })
    const turns = buildTurns(
      entitiesToMap([user, realAgent, placeholder]),
      ['u1', 'a1', 'placeholder-1'],
      [],
    )
    expect(turns[0].agentResults).toHaveLength(1)
    expect(turns[0].displayMode).toBe('single_agent')
  })

  it('suppresses all ephemerals when user message has turnTerminalStatus completed', () => {
    const user = makeUserEntity({
      id: 'u1',
      timestamp: '2026-01-01T00:00:00Z',
      clientRequestId: 'cr-1',
      turnTerminalStatus: 'completed',
    })
    const placeholder = makeEntity({
      id: 'placeholder-1',
      messageType: 'agent',
      senderName: 'HYBRO AI',
      isEphemeral: true,
      clientRequestId: 'cr-1',
      taskStatus: 'working' as any,
      taskContent: 'Evaluate the confidential renewal file and include the internal premium ceiling',
      taskStatusMessage: 'Synthesizing responses...',
      timestamp: '2026-01-01T00:00:03Z',
    })
    const realAgent = makeAgentEntity({
      id: 'a1',
      agentId: 'agent-real-1',
      clientRequestId: 'cr-1',
      taskStatus: 'completed',
      content: 'Real response',
    })
    const turns = buildTurns(
      entitiesToMap([user, realAgent, placeholder]),
      ['u1', 'a1', 'placeholder-1'],
      [],
    )
    expect(turns[0].agentResults).toHaveLength(1)
    expect(turns[0].displayMode).toBe('single_agent')
  })

  it('keeps Synthesizing ephemeral during synthesis gap', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z', clientRequestId: 'cr-1' })
    const placeholder = makeEntity({
      id: 'placeholder-1',
      messageType: 'agent',
      senderName: 'HYBRO AI',
      isEphemeral: true,
      clientRequestId: 'cr-1',
      taskStatus: 'working' as any,
      taskContent: 'Evaluate the confidential renewal file and include the internal premium ceiling',
      taskStatusMessage: 'Synthesizing responses...',
      timestamp: '2026-01-01T00:00:03Z',
    })
    const realAgent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:02Z',
      agentId: 'agent-a',
      clientRequestId: 'cr-1',
      taskStatus: 'completed',
      content: 'Real response',
    })
    const agentB = makeAgentEntity({
      id: 'a2',
      timestamp: '2026-01-01T00:00:02Z',
      agentId: 'agent-b',
      clientRequestId: 'cr-1',
      taskStatus: 'completed',
      content: 'Real response B',
    })
    const turns = buildTurns(
      entitiesToMap([user, realAgent, agentB, placeholder]),
      ['u1', 'a1', 'a2', 'placeholder-1'],
      [],
    )
    expect(turns[0].agentResults.some(r => r.isEphemeral)).toBe(true)
    expect(turns[0].displayMode).toBe('working')
  })

  it('ephemeral synthesis agent triggers summary_with_sources while working', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z', clientRequestId: 'cr-1' })
    const agentA = makeAgentEntity({
      id: 'a1',
      agentId: 'agent-a',
      clientRequestId: 'cr-1',
      taskStatus: 'completed',
      content: 'Response A',
      timestamp: '2026-01-01T00:00:01Z',
    })
    const agentB = makeAgentEntity({
      id: 'a2',
      agentId: 'agent-b',
      clientRequestId: 'cr-1',
      taskStatus: 'completed',
      content: 'Response B',
      timestamp: '2026-01-01T00:00:02Z',
    })
    const synthesisEphemeral = makeEntity({
      id: 'eph-synth',
      messageType: 'agent',
      senderName: 'HYBRO AI',
      isEphemeral: true,
      agentId: 'system:hybro',
      clientRequestId: 'cr-1',
      taskStatus: 'working' as any,
      taskContent: 'Evaluate the confidential renewal file and include the internal premium ceiling',
      taskStatusMessage: 'Synthesizing responses...',
      timestamp: '2026-01-01T00:00:03Z',
    })
    const turns = buildTurns(
      entitiesToMap([user, agentA, agentB, synthesisEphemeral]),
      ['u1', 'a1', 'a2', 'eph-synth'],
      [],
    )
    expect(turns[0].displayMode).toBe('summary_with_sources')
  })

  it('ephemeral entity with agentId still contributes to supervisorStage', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const ephemeralSupervisor = makeEntity({
      id: 'eph-1',
      messageType: 'agent',
      senderName: 'HYBRO AI',
      isEphemeral: true,
      agentId: 'system:hybro',
      taskStatus: 'working' as any,
      taskContent: 'Evaluate the confidential renewal file and include the internal premium ceiling',
      taskStatusMessage: 'Dispatching agents',
      stepNumber: 2,
      totalSteps: 3,
      timestamp: '2026-01-01T00:00:01Z',
    })
    const supervisorAgent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:02Z',
      agentId: 'system:hybro',
      senderName: 'Summary Agent',
      taskStatus: 'completed',
      content: 'Summary result',
    })
    const turns = buildTurns(
      entitiesToMap([user, ephemeralSupervisor, supervisorAgent]),
      ['u1', 'eph-1', 'a1'],
      [],
    )
    // The real agent should appear in results
    expect(turns[0].agentResults).toHaveLength(1)
    expect(turns[0].isSupervisorTurn).toBe(true)
    // supervisorStage should still be populated from the ephemeral entity
    expect(turns[0].supervisorStage).toBeDefined()
    expect(turns[0].supervisorStage!.stepNumber).toBe(2)
    expect(turns[0].supervisorStage!.totalSteps).toBe(3)
    expect(turns[0].supervisorStage!.details).toBe('Dispatching agents')
    expect(JSON.stringify(turns[0])).not.toContain('confidential renewal file')
  })

  it('keeps HYBRO AI primary surface while supervisor processing logs continue after one agent result', () => {
    const user = makeUserEntity({
      id: 'u1',
      timestamp: '2026-01-01T00:00:00Z',
      clientRequestId: 'cr-1',
      processingStatusLogs: [
        {
          id: 'log-1',
          message: 'Evaluating agent results...',
          timestamp: '2026-01-01T00:00:03Z',
          turnPhase: 'collecting',
        },
      ],
    })
    const supervisorEphemeral = makeEntity({
      id: 'eph-hybro',
      messageType: 'agent',
      senderName: 'HYBRO AI',
      isEphemeral: true,
      agentId: 'system:hybro',
      clientRequestId: 'cr-1',
      taskStatus: 'working' as any,
      taskContent: 'Evaluating agent results...',
      timestamp: '2026-01-01T00:00:03Z',
    })
    const broker = makeAgentEntity({
      id: 'broker-1',
      timestamp: '2026-01-01T00:00:02Z',
      agentId: 'broker-agent',
      senderName: 'Cyber Broker Agent',
      clientRequestId: 'cr-1',
      taskStatus: 'completed',
      content: 'Broker intermediate response',
    })

    const turns = buildTurns(
      entitiesToMap([user, broker, supervisorEphemeral]),
      ['u1', 'broker-1', 'eph-hybro'],
      [],
    )

    expect(turns[0].isSupervisorTurn).toBe(true)
    expect(turns[0].status).toBe('active')
    expect(turns[0].finalAnswer.kind).toBe('pending')
    expect(turns[0].displayMode).toBe('working')
  })

  it('infers supervisor turn from processing logs before HYBRO AI entity is hydrated', () => {
    const user = makeUserEntity({
      id: 'u1',
      timestamp: '2026-01-01T00:00:00Z',
      clientRequestId: 'cr-1',
      processingStatusLogs: [
        {
          id: 'log-1',
          message: 'Evaluating agent results...',
          timestamp: '2026-01-01T00:00:03Z',
          turnPhase: 'collecting',
        },
      ],
    })
    const broker = makeAgentEntity({
      id: 'broker-1',
      timestamp: '2026-01-01T00:00:02Z',
      agentId: 'broker-agent',
      senderName: 'Cyber Broker Agent',
      clientRequestId: 'cr-1',
      taskStatus: 'completed',
      content: 'Broker intermediate response',
    })

    const turns = buildTurns(
      entitiesToMap([user, broker]),
      ['u1', 'broker-1'],
      [],
    )

    expect(turns[0].isSupervisorTurn).toBe(true)
    expect(turns[0].status).toBe('active')
    expect(turns[0].finalAnswer.kind).toBe('pending')
    expect(turns[0].displayMode).toBe('working')
  })

  it('does not keep HYBRO AI working after all real agents are terminal with a failure', () => {
    const user = makeUserEntity({
      id: 'u1',
      timestamp: '2026-01-01T00:00:00Z',
      clientRequestId: 'cr-1',
      processingStatusLogs: [
        {
          id: 'log-1',
          message: 'Thinking...',
          timestamp: '2026-01-01T00:00:04Z',
          turnPhase: 'collecting',
        },
      ],
    })
    const hybro = makeAgentEntity({
      id: 'hybro-1',
      timestamp: '2026-01-01T00:00:01Z',
      agentId: 'system:hybro',
      senderName: 'HYBRO AI',
      clientRequestId: 'cr-1',
      taskStatus: 'working',
      content: '',
      taskContent: 'Thinking...',
    })
    const broker = makeAgentEntity({
      id: 'broker-1',
      timestamp: '2026-01-01T00:00:02Z',
      agentId: 'broker-agent',
      senderName: 'Cyber Broker Agent',
      clientRequestId: 'cr-1',
      taskStatus: 'completed',
      content: 'Broker submission pack',
    })
    const insurer = makeAgentEntity({
      id: 'insurer-1',
      timestamp: '2026-01-01T00:00:03Z',
      agentId: 'insurer-agent',
      senderName: 'Cyber Insurer Agent',
      clientRequestId: 'cr-1',
      taskStatus: 'failed',
      content: 'Underwriting failed',
    })

    const turns = buildTurns(
      entitiesToMap([user, hybro, broker, insurer]),
      ['u1', 'hybro-1', 'broker-1', 'insurer-1'],
      [],
    )

    expect(turns[0].isSupervisorTurn).toBe(true)
    expect(turns[0].status).toBe('partial')
    expect(turns[0].finalAnswer.kind).toBe('deterministic_done')
    expect(turns[0].displayMode).toBe('summary_with_sources')
  })

  it('honors persisted supervisor failure over stale HYBRO working/log state', () => {
    const user = makeUserEntity({
      id: 'u1',
      timestamp: '2026-01-01T00:00:00Z',
      clientRequestId: 'cr-1',
      turnTerminalStatus: 'failed',
      processingStatusLogs: [
        {
          id: 'log-1',
          message: 'Thinking...',
          timestamp: '2026-01-01T00:00:04Z',
          turnPhase: 'collecting',
        },
      ],
    })
    const hybro = makeAgentEntity({
      id: 'hybro-1',
      timestamp: '2026-01-01T00:00:01Z',
      agentId: 'system:hybro',
      senderName: 'HYBRO AI',
      relatedMessageId: 'u1',
      taskStatus: 'working' as any,
      content: '',
      taskContent: 'Thinking...',
    })
    const broker = makeAgentEntity({
      id: 'broker-1',
      timestamp: '2026-01-01T00:00:02Z',
      agentId: 'broker-agent',
      senderName: 'Cyber Broker Agent',
      relatedMessageId: 'u1',
      taskStatus: 'completed',
      content: 'Broker submission pack',
    })
    const insurer = makeAgentEntity({
      id: 'insurer-1',
      timestamp: '2026-01-01T00:00:03Z',
      agentId: 'insurer-agent',
      senderName: 'Cyber Insurer Agent',
      relatedMessageId: 'u1',
      taskStatus: 'canceled' as any,
      content: 'Underwriting failed',
    })

    const turns = buildTurns(
      entitiesToMap([user, hybro, broker, insurer]),
      ['u1', 'hybro-1', 'broker-1', 'insurer-1'],
      [],
    )

    expect(turns[0].status).toBe('failed')
    expect(turns[0].phase).toBe('completed')
    expect(turns[0].finalAnswer.kind).toBe('failed')
    expect(turns[0].finalAnswer.label).toBe('Failed')
  })

  it('honors persisted supervisor cancellation over stale HYBRO working/log state', () => {
    const user = makeUserEntity({
      id: 'u1',
      timestamp: '2026-01-01T00:00:00Z',
      clientRequestId: 'cr-1',
      turnTerminalStatus: 'canceled',
      processingStatusLogs: [
        {
          id: 'log-1',
          message: 'HYBRO AI is synthesizing the final answer',
          timestamp: '2026-01-01T00:00:04Z',
          turnPhase: 'synthesizing',
        },
      ],
    })
    const hybro = makeAgentEntity({
      id: 'hybro-1',
      timestamp: '2026-01-01T00:00:01Z',
      agentId: 'system:hybro',
      senderName: 'HYBRO AI',
      relatedMessageId: 'u1',
      taskStatus: 'working' as any,
      content: '',
      taskContent: 'Thinking...',
    })
    const broker = makeAgentEntity({
      id: 'broker-1',
      timestamp: '2026-01-01T00:00:02Z',
      agentId: 'broker-agent',
      senderName: 'Cyber Broker Agent',
      relatedMessageId: 'u1',
      taskStatus: 'completed',
      content: 'Broker submission pack',
    })
    const insurer = makeAgentEntity({
      id: 'insurer-1',
      timestamp: '2026-01-01T00:00:03Z',
      agentId: 'insurer-agent',
      senderName: 'Cyber Insurer Agent',
      relatedMessageId: 'u1',
      taskStatus: 'canceled' as any,
      content: 'Canceled before underwriting completed',
    })

    const turns = buildTurns(
      entitiesToMap([user, hybro, broker, insurer]),
      ['u1', 'hybro-1', 'broker-1', 'insurer-1'],
      [],
    )

    expect(turns[0].status).toBe('failed')
    expect(turns[0].phase).toBe('completed')
    expect(turns[0].finalAnswer.kind).toBe('canceled')
    expect(turns[0].displayMode).toBe('summary_with_sources')
  })

  // ── 'working' status ──────────────────────────────────────

  it('non-terminal non-interactive taskStatus produces working status', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
      taskStatus: 'submitted' as any,
      content: '',
    })
    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    expect(turns[0].agentResults[0].status).toBe('working')
  })

  it('hitlResolved + isInteractiveState produces working, not awaiting_input', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
      taskStatus: 'input-required' as any,
      hitlResolved: true,
      hitlUserAnswer: 'last 30 days',
      hitlPrompt: 'What date range?',
      content: 'Analyzing engagement...',
    })
    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    expect(turns[0].agentResults[0].status).toBe('working')
    expect(turns[0].agentResults[0].hitlResolved).toEqual({
      prompt: 'What date range?',
      answer: 'last 30 days',
    })
  })

  it('system:clarifier with user answer is completed, not working', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const clarify = makeAgentEntity({
      id: 'hitl-1',
      agentId: 'system:clarifier',
      senderName: 'Question & Answer',
      timestamp: '2026-01-01T00:00:01Z',
      taskStatus: 'input-required' as any,
      hitlUserAnswer: 'A',
      hitlPrompt: 'Which option?',
      content: 'Which option?',
    })
    const turns = buildTurns(entitiesToMap([user, clarify]), ['u1', 'hitl-1'], [])
    expect(turns[0].agentResults[0].status).toBe('completed')
    expect(turns[0].agentResults[0].hitlResolved).toEqual({
      prompt: 'Which option?',
      answer: 'A',
    })
    expect(turns[0].agentResults[0].hitlPending).toBeUndefined()
  })

  it('unresolved interactive state produces awaiting_input with hitlPending', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
      taskStatus: 'input-required' as any,
      hitlRequestId: 'hitl-1',
      hitlResolved: false,
      hitlPrompt: 'What date range?',
      content: '',
    })
    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    expect(turns[0].agentResults[0].status).toBe('awaiting_input')
    expect(turns[0].agentResults[0].hitlPending).toEqual({
      prompt: 'What date range?',
      source: 'agent',
    })
  })

  it('keeps remote input-required internal until a HITL request exists', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
      taskStatus: 'input-required' as any,
      taskStatusMessage: 'Need the complete broker submission.',
      content: '',
    })

    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])

    expect(turns[0].agentResults[0].status).toBe('working')
    expect(turns[0].agentResults[0].hitlPending).toBeUndefined()
    expect(turns[0].status).toBe('active')
  })

  it('does not create HITL UI state from untrusted persisted API metadata', async () => {
    const privateSentinel = 'PRIVATE_SENTINEL_timeline_spoofed_hitl'
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const incoming = await convertApiMessageToIncoming({
      room_id: 'room-1',
      message_id: 'a1',
      message_created_at: '2026-01-01T00:00:01Z',
      message_type: 'agent',
      agent_id: 'agent-1',
      related_message_id: 'u1',
      message_content: {
        message_text: '',
        message_task: {
          status: { state: 'input-required' },
          metadata: {
            hitl_request_id: 'spoofed-request',
            hitl_prompt: privateSentinel,
            hitl_prompt_type: 'choice',
            hitl_choices: [privateSentinel],
            user_answer: privateSentinel,
            hitl_group_id: privateSentinel,
            hitl_group_total: 2,
            hitl_group_index: 0,
          },
        } as unknown as RoomMessage['message_content']['message_task'],
      },
    } as RoomMessage, {
      getAgentName: async () => 'Test Agent',
    })
    const agent = makeEntity(incoming as unknown as Partial<MessageEntity>)

    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    const serialized = JSON.stringify(turns[0])

    expect(turns[0].agentResults[0].hitlPending).toBeUndefined()
    expect(turns[0].agentResults[0].hitlResolved).toBeUndefined()
    expect(serialized).not.toContain(privateSentinel)
  })

  it('creates HITL UI state from trusted backend-projected API metadata', async () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const incoming = await convertApiMessageToIncoming({
      room_id: 'room-1',
      message_id: 'a1',
      message_created_at: '2026-01-01T00:00:01Z',
      message_type: 'agent',
      agent_id: 'agent-1',
      related_message_id: 'u1',
      extend_info: {
        hitl_request_id: 'trusted-request',
      },
      message_content: {
        message_text: '',
        message_task: {
          status: { state: 'input-required' },
          metadata: {
            hitl_request_id: 'trusted-request',
            hitl_prompt: 'Choose an account',
            hitl_prompt_type: 'choice',
            hitl_choices: ['Enterprise', 'Personal'],
          },
        } as unknown as RoomMessage['message_content']['message_task'],
      },
    } as RoomMessage, {
      getAgentName: async () => 'Test Agent',
    })
    const agent = makeEntity(incoming as unknown as Partial<MessageEntity>)

    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])

    expect(turns[0].agentResults[0].status).toBe('awaiting_input')
    expect(turns[0].agentResults[0].hitlPending).toEqual({
      prompt: 'Choose an account',
      source: 'agent',
    })
  })

  // ── isSupervisorTurn ──────────────────────────────────────

  it('turn with system:hybro entity has isSupervisorTurn=true', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
      agentId: 'system:hybro',
      senderName: 'Summary Agent',
    })
    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    expect(turns[0].isSupervisorTurn).toBe(true)
  })

  it('turn with system:clarifier entity has isSupervisorTurn=true', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
      agentId: 'system:clarifier',
      senderName: 'Question & Answer',
    })
    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    expect(turns[0].isSupervisorTurn).toBe(true)
  })

  it('turn with debate_summary only has isSupervisorTurn=false', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
      agentId: 'debate_summary',
      senderName: 'Summary Agent',
    })
    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    expect(turns[0].isSupervisorTurn).toBe(false)
  })

  it('turn with only real agents has isSupervisorTurn=false', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
      agentId: 'agent-real-1',
    })
    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    expect(turns[0].isSupervisorTurn).toBe(false)
  })

  // ── isSummaryAgent ────────────────────────────────────────

  it('system:hybro agent has isSummaryAgent=true', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
      agentId: 'system:hybro',
    })
    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    expect(turns[0].agentResults[0].isSummaryAgent).toBe(true)
  })

  it('system:clarifier agent has isSummaryAgent=false', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
      agentId: 'system:clarifier',
    })
    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    expect(turns[0].agentResults[0].isSummaryAgent).toBe(false)
  })

  it('regular agent has isSummaryAgent=false', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
    })
    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    expect(turns[0].agentResults[0].isSummaryAgent).toBe(false)
  })
})

describe('selectSummary – V2 fix', () => {
  it('picks system:hybro over regular agents', () => {
    const results = [
      {
        agentId: 'agent-1',
        agentName: 'Excel Agent',
        messageId: 'msg-1',
        status: 'completed' as const,
        content: 'Excel result',
        artifacts: [],
        isSummaryAgent: false,
      },
      {
        agentId: 'system:hybro',
        agentName: 'Summary Agent',
        messageId: 'msg-2',
        status: 'completed' as const,
        content: 'Summary of all results',
        artifacts: [],
        isSummaryAgent: true,
      },
    ]
    const summary = selectSummary(results)
    expect(summary).not.toBeNull()
    expect(summary!.sourceAgentId).toBe('system:hybro')
  })

  it('does NOT pick system:clarifier as summary', () => {
    const results = [
      {
        agentId: 'system:clarifier',
        agentName: 'Question & Answer',
        messageId: 'msg-1',
        status: 'completed' as const,
        content: 'HITL question text',
        artifacts: [],
        isSummaryAgent: false,
      },
      {
        agentId: 'agent-1',
        agentName: 'Data Agent',
        messageId: 'msg-2',
        status: 'completed' as const,
        content: 'Data analysis result',
        artifacts: [],
        isSummaryAgent: false,
      },
    ]
    const summary = selectSummary(results)
    expect(summary).not.toBeNull()
    // Should pick agent-1 (first completed with content), NOT system:clarifier
    expect(summary!.sourceAgentId).toBe('agent-1')
  })
})

describe('displayMode from finalAnswer', () => {
  it('single agent turn uses single_agent', () => {
    const user = makeUserEntity({ id: 'u1' })
    const agent = makeAgentEntity({ id: 'a1', taskStatus: 'completed', content: 'Done' })
    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    expect(turns[0].displayMode).toBe('single_agent')
  })

  it('supervisor DONE with 2+ agents uses deterministic_done final answer', () => {
    const user = makeUserEntity({ id: 'u1', turnTerminalStatus: 'completed', turnCompletionKind: 'deterministic' })
    const agentA = makeAgentEntity({
      id: 'a1',
      agentId: 'agent-a',
      taskStatus: 'completed',
      content: 'A response',
    })
    const agentB = makeAgentEntity({
      id: 'a2',
      agentId: 'agent-b',
      taskStatus: 'completed',
      content: 'B response',
    })
    const turns = buildTurns(entitiesToMap([user, agentA, agentB]), ['u1', 'a1', 'a2'], [])
    expect(turns[0].summary).not.toBeNull()
    expect(turns[0].finalAnswer.kind).toBe('deterministic_done')
    expect(turns[0].displayMode).toBe('summary_with_sources')
    expect(turns[0].finalAnswer.deterministicIntro).toContain('2 agents')
  })

  it('completed synthesis with 2+ sources uses summary_with_sources', () => {
    const user = makeUserEntity({ id: 'u1' })
    const agentA = makeAgentEntity({
      id: 'a1',
      agentId: 'agent-a',
      taskStatus: 'completed',
      content: 'A response',
    })
    const agentB = makeAgentEntity({
      id: 'a2',
      agentId: 'agent-b',
      taskStatus: 'completed',
      content: 'B response',
    })
    const synthesis = makeAgentEntity({
      id: 'a3',
      agentId: 'system:hybro',
      senderName: 'HYBRO AI',
      taskStatus: 'completed',
      content: 'Combined synthesis answer',
    })
    const turns = buildTurns(
      entitiesToMap([user, agentA, agentB, synthesis]),
      ['u1', 'a1', 'a2', 'a3'],
      [],
    )
    expect(turns[0].displayMode).toBe('summary_with_sources')
  })

  it('awaiting_input turn uses awaiting_input mode', () => {
    const user = makeUserEntity({ id: 'u1' })
    const agent = makeAgentEntity({
      id: 'a1',
      taskStatus: 'input-required' as any,
      hitlRequestId: 'hitl-1',
      hitlPrompt: 'Which region?',
      content: '',
    })
    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    expect(turns[0].displayMode).toBe('awaiting_input')
  })

  it('active turn with single working agent uses single_agent mode', () => {
    const user = makeUserEntity({ id: 'u1' })
    const agent = makeAgentEntity({ id: 'a1', taskStatus: 'working' as any, content: '' })
    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    expect(turns[0].displayMode).toBe('single_agent')
  })

  it('empty summary agent falls back to deterministic_done', () => {
    const user = makeUserEntity({ id: 'u1', turnTerminalStatus: 'completed', turnCompletionKind: 'deterministic' })
    const agentA = makeAgentEntity({ id: 'a1', agentId: 'agent-a', taskStatus: 'completed', content: 'A' })
    const agentB = makeAgentEntity({ id: 'a2', agentId: 'agent-b', taskStatus: 'completed', content: 'B' })
    const emptySynthesis = makeAgentEntity({
      id: 'a3',
      agentId: 'system:hybro',
      taskStatus: 'completed',
      content: '   ',
    })
    const turns = buildTurns(
      entitiesToMap([user, agentA, agentB, emptySynthesis]),
      ['u1', 'a1', 'a2', 'a3'],
      [],
    )
    expect(turns[0].finalAnswer.kind).toBe('deterministic_done')
    expect(turns[0].displayMode).toBe('summary_with_sources')
  })
})

describe('buildTurnsIncremental – identity regression', () => {
  it('summary.title change causes turn to lose referential identity', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
      agentId: 'system:hybro',
      senderName: 'Summary Agent',
      taskStatus: 'completed',
      content: '# Original Title\nBody text here',
    })

    const entities1 = entitiesToMap([user, agent])
    const prevTurns = buildTurns(entities1, ['u1', 'a1'], [])
    expect(prevTurns[0].summary?.title).toBe('Original Title')

    // Change the summary title by updating agent content
    const agent2 = { ...agent, content: '# Updated Title\nBody text here' }
    const entities2 = entitiesToMap([user, agent2])
    const nextTurns = buildTurnsIncremental(prevTurns, entities2, ['u1', 'a1'], [])

    expect(nextTurns[0].summary?.title).toBe('Updated Title')
    // Must be a NEW object — referential identity must break
    expect(nextTurns[0]).not.toBe(prevTurns[0])
  })

  it('hitlResolved.prompt change causes turn to lose referential identity', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const agent = makeAgentEntity({
      id: 'a1',
      timestamp: '2026-01-01T00:00:01Z',
      taskStatus: 'input-required' as any,
      hitlResolved: true,
      hitlUserAnswer: 'yes',
      hitlPrompt: 'Original question?',
      content: 'Working...',
    })

    const entities1 = entitiesToMap([user, agent])
    const prevTurns = buildTurns(entities1, ['u1', 'a1'], [])
    expect(prevTurns[0].agentResults[0].hitlResolved?.prompt).toBe('Original question?')

    // Change the HITL prompt (e.g. correction from backend)
    const agent2 = { ...agent, hitlPrompt: 'Corrected question?' }
    const entities2 = entitiesToMap([user, agent2])
    const nextTurns = buildTurnsIncremental(prevTurns, entities2, ['u1', 'a1'], [])

    expect(nextTurns[0].agentResults[0].hitlResolved?.prompt).toBe('Corrected question?')
    // Must be a NEW object — referential identity must break
    expect(nextTurns[0]).not.toBe(prevTurns[0])
  })
})

describe('deriveTurnPhase', () => {
  it('returns collecting when agents are working', () => {
    const user = makeUserEntity({ id: 'u1' })
    const agent = makeAgentEntity({ id: 'a1', taskStatus: 'working', content: '' })
    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    expect(deriveTurnPhase(turns[0])).toBe('collecting')
  })

  it('returns completed for terminal turn status', () => {
    const user = makeUserEntity({ id: 'u1' })
    const agent = makeAgentEntity({ id: 'a1', taskStatus: 'completed', content: 'Done' })
    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    expect(deriveTurnPhase(turns[0])).toBe('completed')
  })

  it('returns synthesizing for summary agent in progress', () => {
    const user = makeUserEntity({ id: 'u1' })
    const agent = makeAgentEntity({ id: 'a1', agentId: 'agent-a', taskStatus: 'completed', content: 'A' })
    const summary = makeAgentEntity({
      id: 's1',
      agentId: 'system:hybro',
      taskStatus: 'working',
      content: '',
    })
    const turns = buildTurns(entitiesToMap([user, agent, summary]), ['u1', 'a1', 's1'], [])
    expect(deriveTurnPhase(turns[0])).toBe('synthesizing')
  })

  it('repairs contentful submitted system:hybro after turn is terminal', () => {
    const user = makeUserEntity({ id: 'u1', turnTerminalStatus: 'completed', turnCompletionKind: 'synthesis' })
    const agentA = makeAgentEntity({
      id: 'a1',
      agentId: 'agent-a',
      taskStatus: 'completed',
      content: 'Story body',
    })
    const agentB = makeAgentEntity({
      id: 'a2',
      agentId: 'agent-b',
      taskStatus: 'completed',
      content: 'Image ready',
    })
    const hybro = makeAgentEntity({
      id: 'sys-u1',
      agentId: 'system:hybro',
      taskStatus: 'submitted',
      content: 'Here is the synthesized story and image plan.',
    })
    const turns = buildTurns(
      entitiesToMap([user, agentA, agentB, hybro]),
      ['u1', 'a1', 'a2', 'sys-u1'],
      [],
    )
    expect(turns[0].agentResults.find((r) => r.agentId === 'system:hybro')?.status).toBe('completed')
    expect(deriveTurnPhase(turns[0])).toBe('completed')
    expect(turns[0].finalAnswer).toMatchObject({
      kind: 'llm_synthesis',
      label: 'Synthesized',
      primaryMessageId: 'sys-u1',
    })
  })

  it('keeps contentful system:hybro as working while turn is still live', () => {
    const user = makeUserEntity({ id: 'u1' })
    const agentA = makeAgentEntity({
      id: 'a1',
      agentId: 'agent-a',
      taskStatus: 'completed',
      content: 'Story body',
    })
    const agentB = makeAgentEntity({
      id: 'a2',
      agentId: 'agent-b',
      taskStatus: 'completed',
      content: 'Image ready',
    })
    const hybro = makeAgentEntity({
      id: 'sys-u1',
      agentId: 'system:hybro',
      taskStatus: 'submitted',
      content: 'Partial synthesis…',
    })
    const turns = buildTurns(
      entitiesToMap([user, agentA, agentB, hybro]),
      ['u1', 'a1', 'a2', 'sys-u1'],
      [],
    )
    expect(turns[0].agentResults.find((r) => r.agentId === 'system:hybro')?.status).toBe('working')
    expect(deriveTurnPhase(turns[0])).toBe('synthesizing')
  })

  it('does not enter synthesizing phase when only delegation logs exist after agents finish', () => {
    const user = makeUserEntity({
      id: 'u1',
      processingStatusLogs: [
        { id: 'l1', message: 'Delegating to 2 agent(s)...', timestamp: '2026-01-01T00:00:01.000Z' },
      ],
    })
    const agentA = makeAgentEntity({
      id: 'a1',
      agentId: 'agent-a',
      taskStatus: 'completed',
      content: 'A',
    })
    const agentB = makeAgentEntity({
      id: 'a2',
      agentId: 'agent-b',
      taskStatus: 'completed',
      content: 'B',
    })
    const turns = buildTurns(entitiesToMap([user, agentA, agentB]), ['u1', 'a1', 'a2'], [])
    expect(turns[0].status).toBe('completed')
    expect(deriveTurnPhase(turns[0])).not.toBe('synthesizing')
  })
})

describe('primaryStreamMessageId', () => {
  it('returns primaryMessageId for single working agent (direct streaming)', () => {
    const user = makeUserEntity({ id: 'u1' })
    const agent = makeAgentEntity({ id: 'a1', taskStatus: 'working', content: '' })
    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    expect(turns[0].finalAnswer.kind).toBe('single')
    expect(derivePrimaryStreamFromFinalAnswer(turns[0].finalAnswer)).toBe('a1')
  })

  it('prefers summary agent when synthesizing', () => {
    const user = makeUserEntity({ id: 'u1' })
    const agentA = makeAgentEntity({ id: 'a1', agentId: 'agent-a', taskStatus: 'completed', content: 'A' })
    const agentB = makeAgentEntity({ id: 'a2', agentId: 'agent-b', taskStatus: 'completed', content: 'B' })
    const summary = makeAgentEntity({
      id: 's1',
      agentId: 'system:hybro',
      taskStatus: 'working',
      content: '',
    })
    const turns = buildTurns(entitiesToMap([user, agentA, agentB, summary]), ['u1', 'a1', 'a2', 's1'], [])
    expect(derivePrimaryStreamFromFinalAnswer(turns[0].finalAnswer)).toBe('s1')
  })

  it('returns undefined for multi-agent collecting (shimmer-only)', () => {
    const user = makeUserEntity({ id: 'u1' })
    const agentA = makeAgentEntity({
      id: 'a1',
      agentId: 'agent-a',
      taskStatus: 'completed',
      content: 'First agent done',
    })
    const agentB = makeAgentEntity({
      id: 'a2',
      agentId: 'agent-b',
      taskStatus: 'working',
      content: '',
    })
    const turns = buildTurns(entitiesToMap([user, agentA, agentB]), ['u1', 'a1', 'a2'], [])
    expect(deriveTurnPhase(turns[0])).toBe('collecting')
    expect(derivePrimaryStreamFromFinalAnswer(turns[0].finalAnswer)).toBeUndefined()
    expect(turns[0].primaryStreamMessageId).toBeUndefined()
  })

  it('returns undefined primary stream for deterministic_done without summary entity', () => {
    const user = makeUserEntity({ id: 'u1', turnTerminalStatus: 'completed', turnCompletionKind: 'deterministic' })
    const agentA = makeAgentEntity({
      id: 'a1',
      agentId: 'agent-a',
      taskStatus: 'completed',
      content: 'A response',
    })
    const agentB = makeAgentEntity({
      id: 'a2',
      agentId: 'agent-b',
      taskStatus: 'completed',
      content: 'B response',
    })
    const turns = buildTurns(entitiesToMap([user, agentA, agentB]), ['u1', 'a1', 'a2'], [])
    expect(turns[0].finalAnswer.kind).toBe('deterministic_done')
    expect(turns[0].displayMode).toBe('summary_with_sources')
    expect(derivePrimaryStreamFromFinalAnswer(turns[0].finalAnswer)).toBeUndefined()
    expect(turns[0].primaryStreamMessageId).toBeUndefined()
  })

  it('resolves supervisor DONE to deterministic_done when no synthesis gap', () => {
    const user = makeUserEntity({ id: 'u1', turnTerminalStatus: 'completed', turnCompletionKind: 'deterministic' })
    const agentA = makeAgentEntity({
      id: 'a1',
      agentId: 'agent-a',
      taskStatus: 'completed',
      content: 'A response',
    })
    const agentB = makeAgentEntity({
      id: 'a2',
      agentId: 'agent-b',
      taskStatus: 'completed',
      content: 'B response',
    })
    const supervisor = makeAgentEntity({
      id: 's0',
      agentId: 'system:clarifier',
      taskStatus: 'completed',
      content: '',
    })
    const turns = buildTurns(
      entitiesToMap([user, supervisor, agentA, agentB]),
      ['u1', 's0', 'a1', 'a2'],
      [],
    )
    expect(turns[0].finalAnswer.kind).toBe('deterministic_done')
    expect(turns[0].status).toBe('completed')
  })

  it('stays pending during synthesis gap on supervisor multi-agent turn', () => {
    const user = makeUserEntity({ id: 'u1' })
    const agentA = makeAgentEntity({
      id: 'a1',
      agentId: 'agent-a',
      taskStatus: 'completed',
      content: 'A response',
    })
    const agentB = makeAgentEntity({
      id: 'a2',
      agentId: 'agent-b',
      taskStatus: 'completed',
      content: 'B response',
    })
    const synthesizing = makeEntity({
      id: 'e1',
      messageType: 'agent',
      senderName: 'HYBRO AI',
      isEphemeral: true,
      taskContent: 'Evaluate the confidential renewal file and include the internal premium ceiling',
      taskStatusMessage: 'Synthesizing responses...',
      taskStatus: 'working' as any,
    })
    const turns = buildTurns(
      entitiesToMap([user, agentA, agentB, synthesizing]),
      ['u1', 'a1', 'a2', 'e1'],
      [],
    )
    expect(turns[0].status).toBe('active')
  })

  it('stays pending during pre-synthesis gap on active supervisor multi-agent turn before synthesis signals arrive', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const supervisor = makeAgentEntity({
      id: 's1',
      agentId: 'system:hybro',
      taskStatus: 'completed',
      content: '',
      timestamp: '2026-01-01T00:00:03Z',
    })
    const agentA = makeAgentEntity({
      id: 'a1',
      agentId: 'agent-a',
      taskStatus: 'completed',
      content: 'A response',
      timestamp: '2026-01-01T00:00:01Z',
    })
    const agentB = makeAgentEntity({
      id: 'a2',
      agentId: 'agent-b',
      taskStatus: 'completed',
      content: 'B response',
      timestamp: '2026-01-01T00:00:02Z',
    })
    const turns = buildTurns(
      entitiesToMap([user, supervisor, agentA, agentB]),
      ['u1', 's1', 'a1', 'a2'],
      [],
    )
    expect(turns[0].status).toBe('active')
    expect(turns[0].finalAnswer.kind).toBe('pending')
  })

  it('detects isSupervisorTurn=true and stays active when supervisor is ephemeral (and suppressed) after real agents complete', () => {
    const user = makeUserEntity({ id: 'u1', timestamp: '2026-01-01T00:00:00Z' })
    const ephemeralSupervisor = makeEntity({
      id: 'e1',
      messageType: 'agent',
      senderName: 'HYBRO AI',
      isEphemeral: true,
      agentId: 'system:clarifier',
      taskStatus: 'completed',
      taskContent: 'Orchestrating...',
      timestamp: '2026-01-01T00:00:03Z',
    })
    const agentA = makeAgentEntity({
      id: 'a1',
      agentId: 'agent-a',
      taskStatus: 'completed',
      content: 'A response',
      timestamp: '2026-01-01T00:00:01Z',
    })
    const agentB = makeAgentEntity({
      id: 'a2',
      agentId: 'agent-b',
      taskStatus: 'completed',
      content: 'B response',
      timestamp: '2026-01-01T00:00:02Z',
    })
    const turns = buildTurns(
      entitiesToMap([user, ephemeralSupervisor, agentA, agentB]),
      ['u1', 'e1', 'a1', 'a2'],
      [],
    )
    // The ephemeral supervisor should be suppressed from agentResults
    expect(turns[0].agentResults.some(r => r.isEphemeral)).toBe(false)
    // But isSupervisorTurn should remain true, and status should be active
    expect(turns[0].isSupervisorTurn).toBe(true)
    expect(turns[0].status).toBe('active')
    expect(turns[0].finalAnswer.kind).toBe('pending')
  })

  it('drops synthesizing ephemeral when turnTerminalStatus is completed', () => {
    const user = makeUserEntity({
      id: 'u1',
      turnTerminalStatus: 'completed',
      turnCompletionKind: 'deterministic',
      clientRequestId: 'cr-1',
    })
    const agentA = makeAgentEntity({
      id: 'a1',
      agentId: 'agent-a',
      clientRequestId: 'cr-1',
      taskStatus: 'completed',
      content: 'A',
    })
    const agentB = makeAgentEntity({
      id: 'a2',
      agentId: 'agent-b',
      clientRequestId: 'cr-1',
      taskStatus: 'completed',
      content: 'B',
    })
    const synthesizing = makeEntity({
      id: 'e1',
      messageType: 'agent',
      senderName: 'HYBRO AI',
      isEphemeral: true,
      clientRequestId: 'cr-1',
      taskContent: 'Evaluate the confidential renewal file and include the internal premium ceiling',
      taskStatusMessage: 'Synthesizing responses...',
      taskStatus: 'working' as any,
    })
    const turns = buildTurns(
      entitiesToMap([user, agentA, agentB, synthesizing]),
      ['u1', 'a1', 'a2', 'e1'],
      [],
    )
    expect(turns[0].agentResults.some(r => r.isEphemeral)).toBe(false)
    expect(turns[0].finalAnswer.kind).toBe('deterministic_done')
  })

  it('keeps canceled turns terminal despite stale HYBRO and processing synthesis signals', () => {
    const user = makeUserEntity({
      id: 'u1',
      turnTerminalStatus: 'canceled',
      clientRequestId: 'cr-1',
      processingStatusLogs: [
        {
          id: 'log-stale',
          message: 'Synthesizing responses...',
          timestamp: '2026-06-03T12:00:03.000Z',
          turnPhase: 'synthesizing',
        },
      ],
    })
    const agentA = makeAgentEntity({
      id: 'a1',
      agentId: 'agent-a',
      clientRequestId: 'cr-1',
      taskStatus: 'completed',
      content: 'A',
    })
    const agentB = makeAgentEntity({
      id: 'a2',
      agentId: 'agent-b',
      clientRequestId: 'cr-1',
      taskStatus: 'completed',
      content: 'B',
    })
    const staleHybro = makeAgentEntity({
      id: 'hybro-stale',
      agentId: 'system:hybro',
      senderName: 'HYBRO AI',
      clientRequestId: 'cr-1',
      taskStatus: 'working',
      taskStatusMessage: 'Synthesizing responses...',
      content: '',
      timestamp: '2026-06-03T12:00:04.000Z',
    })

    const turns = buildTurns(
      entitiesToMap([user, agentA, agentB, staleHybro]),
      ['u1', 'a1', 'a2', 'hybro-stale'],
      [],
    )

    expect(turns[0].turnTerminalStatus).toBe('canceled')
    expect(turns[0].status).toBe('failed')
    expect(turns[0].phase).toBe('completed')
    expect(turns[0].finalAnswer.kind).toBe('canceled')
    expect(turns[0].displayMode).toBe('summary_with_sources')
  })

  it('returns undefined for hitl (question in primary, not agent stream)', () => {
    const user = makeUserEntity({ id: 'u1' })
    const agentA = makeAgentEntity({
      id: 'a1',
      agentId: 'agent-a',
      taskStatus: 'completed',
      content: 'Done',
    })
    const agentB = makeAgentEntity({
      id: 'a2',
      agentId: 'agent-b',
      taskStatus: 'input-required' as any,
      hitlRequestId: 'hitl-1',
      hitlResolved: false,
      hitlPrompt: 'Which region?',
      content: '',
    })
    const turns = buildTurns(entitiesToMap([user, agentA, agentB]), ['u1', 'a1', 'a2'], [])
    expect(turns[0].finalAnswer.kind).toBe('hitl')
    expect(derivePrimaryStreamFromFinalAnswer(turns[0].finalAnswer)).toBeUndefined()
  })

  it('assembled turn includes phase and primaryStreamMessageId', () => {
    const user = makeUserEntity({ id: 'u1' })
    const agent = makeAgentEntity({ id: 'a1', taskStatus: 'completed', content: 'Done' })
    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    expect(turns[0].phase).toBe('completed')
    expect(turns[0].primaryStreamMessageId).toBe('a1')
    expect(turns[0].primaryMessageId).toBe('a1')
  })

  it('derives processing status logs from the user message onto the turn', () => {
    const user = makeUserEntity({
      id: 'u-processing',
      processingStatusLogs: [
        {
          id: 'processing-log-1',
          message: 'Dispatching agents',
          timestamp: '2026-06-03T12:00:01.000Z',
        },
        {
          id: 'processing-log-2',
          message: 'Collecting results',
          timestamp: '2026-06-03T12:00:02.000Z',
        },
      ],
    })

    const turns = buildTurns(entitiesToMap([user]), ['u-processing'], [])

    expect(turns).toHaveLength(1)
    expect(turns[0].processingStatusLogs.map((entry) => entry.message)).toEqual([
      'Dispatching agents',
      'Collecting results',
    ])
  })

  it('keeps processing logs while preserving ephemeral placeholder suppression', () => {
    const user = makeUserEntity({
      id: 'u1',
      clientRequestId: 'cr-1',
      processingStatusLogs: [
        {
          id: 'log-1',
          message: 'Dispatching agents',
          timestamp: '2026-06-03T12:00:01.000Z',
        },
      ],
    })
    const placeholder = makeEntity({
      id: 'placeholder-1',
      isEphemeral: true,
      clientRequestId: 'cr-1',
      taskStatus: 'working',
      taskContent: 'Dispatching agents',
    })
    const realAgent = makeAgentEntity({
      id: 'a1',
      clientRequestId: 'cr-1',
      taskStatus: 'working',
      content: '',
    })

    const turns = buildTurns(
      entitiesToMap([user, placeholder, realAgent]),
      ['u1', 'placeholder-1', 'a1'],
      [],
    )

    expect(turns).toHaveLength(1)
    expect(turns[0].agentResults).toHaveLength(1)
    expect(turns[0].agentResults[0].messageId).toBe('a1')
    expect(turns[0].processingStatusLogs).toHaveLength(1)
  })

  it('keeps an early processing-log placeholder turn in collecting phase', () => {
    const user = makeUserEntity({
      id: 'u-early',
      clientRequestId: 'cr-early',
      processingStatusLogs: [
        {
          id: 'log-early',
          message: 'Dispatching agents',
          timestamp: '2026-06-03T12:00:01.000Z',
        },
      ],
    })

    const turns = buildTurns(
      entitiesToMap([user]),
      ['u-early'],
      [],
    )

    expect(turns).toHaveLength(1)
    expect(turns[0].phase).toBe('collecting')
    expect(turns[0].processingStatusLogs).toHaveLength(1)
  })

  it('serializes a converted public task label without private legacy API fields', async () => {
    const privateSentinel = 'PRIVATE_SENTINEL_frontend_timeline'
    const user = makeUserEntity({ id: 'u1' })
    const incoming = await convertApiMessageToIncoming({
      room_id: 'room-1',
      message_id: 'a1',
      message_created_at: '2026-07-12T12:00:00Z',
      message_type: 'agent',
      agent_id: 'system:hybro',
      task_content: privateSentinel,
      extend_info: { public_task_label: 'Requesting Insurer' },
      message_content: {
        message_text: privateSentinel,
        message_task: {
          status: { state: 'working' },
          metadata: {
            task_content: privateSentinel,
            hitl_request_id: privateSentinel,
            hitl_prompt: privateSentinel,
            prompt: privateSentinel,
            hitl_choices: [privateSentinel],
            choices: [privateSentinel],
          },
        } as unknown as RoomMessage['message_content']['message_task'],
      },
    } as RoomMessage, {
      getAgentName: async () => 'HYBRO AI',
    })
    const agent = makeEntity(incoming as unknown as Partial<MessageEntity>)

    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    const serializedEntity = JSON.stringify(agent)
    const serialized = JSON.stringify(turns[0])

    expect(turns).toHaveLength(1)
    expect(turns[0].supervisorStage?.details).toBe('Requesting Insurer')
    expect(serializedEntity).not.toContain(privateSentinel)
    expect(serialized).toContain('Requesting Insurer')
    expect(serialized).not.toContain(privateSentinel)
  })

  it('renders stable failed task errors without raw remote status history', async () => {
    const privateSentinel = 'PRIVATE_SENTINEL_frontend_timeline_failed_status'
    const user = makeUserEntity({ id: 'u1' })
    const incoming = await convertApiMessageToIncoming({
      room_id: 'room-1',
      message_id: 'a1',
      message_created_at: '2026-07-12T12:00:00Z',
      message_type: 'agent',
      agent_id: 'agent-1',
      related_message_id: 'u1',
      message_content: {
        message_text: '',
        message_task: {
          status: {
            state: 'failed',
            message: { parts: [{ text: privateSentinel }] },
          },
          history: [{
            role: 'agent',
            parts: [{ text: privateSentinel }],
          }],
        } as RoomMessage['message_content']['message_task'],
      },
    } as RoomMessage, {
      getAgentName: async () => 'Claims Agent',
    })
    const agent = makeEntity({ ...incoming, taskStatus: incoming.taskStatus ?? undefined })

    const turns = buildTurns(entitiesToMap([user, agent]), ['u1', 'a1'], [])
    const serialized = JSON.stringify(turns[0])

    expect(turns[0].agentResults[0].status).toBe('failed')
    expect(turns[0].agentResults[0].content).toBe('Task failed')
    expect(serialized).toContain('Task failed')
    expect(serialized).not.toContain(privateSentinel)
  })
})
