import { beforeEach, describe, expect, it } from 'vitest'
import type { IncomingMessage } from '../types'
import { useMessageStore } from '../index'
import {
  collectActiveRunTriggerMessageIds,
  stampInferredTurnTerminalStatus,
} from '../infer-turn-terminal-status'

function makeUser(id: string, overrides: Partial<IncomingMessage> = {}): IncomingMessage {
  return {
    id,
    roomId: 'room-1',
    messageType: 'user',
    content: 'question',
    senderName: 'User',
    timestamp: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function makeAgent(
  id: string,
  relatedMessageId: string,
  overrides: Partial<IncomingMessage> = {},
): IncomingMessage {
  return {
    id,
    roomId: 'room-1',
    messageType: 'agent',
    content: 'answer',
    senderName: 'Agent',
    timestamp: '2026-01-01T00:01:00Z',
    agentId: `agent-${id}`,
    relatedMessageId,
    taskStatus: 'completed',
    ...overrides,
  }
}

describe('collectActiveRunTriggerMessageIds', () => {
  it('returns trigger message ids from room.active_runs', () => {
    const ids = collectActiveRunTriggerMessageIds({
      active_runs: [
        { trigger_message_id: 'u1' },
        { trigger_message_id: null },
        { trigger_message_id: 'u2' },
      ],
    })
    expect(ids).toEqual(new Set(['u1', 'u2']))
  })

  it('returns empty set when room has no active runs', () => {
    expect(collectActiveRunTriggerMessageIds({ active_runs: [] })).toEqual(new Set())
    expect(collectActiveRunTriggerMessageIds(null)).toEqual(new Set())
  })
})

describe('stampInferredTurnTerminalStatus', () => {
  beforeEach(() => {
    useMessageStore.getState().clearRoom()
    useMessageStore.getState().setRoom('room-1')
  })

  it('stamps canceled when all agent tasks were canceled', () => {
    const u1 = makeUser('u1')
    const agents = ['a1', 'a2', 'a3', 'a4'].map((id, i) =>
      makeAgent(id, 'u1', {
        agentId: `agent-${i}`,
        taskStatus: 'canceled',
        content: 'Task was canceled',
      }),
    )

    useMessageStore.getState().upsertMany([u1, ...agents], 'db')

    stampInferredTurnTerminalStatus('room-1')

    expect(useMessageStore.getState().entities.u1?.turnTerminalStatus).toBe('canceled')
  })

  it('stamps completed multi-agent turn without summary entity', () => {
    const u1 = makeUser('u1')
    const a1 = makeAgent('a1', 'u1', { agentId: 'agent-a' })
    const a2 = makeAgent('a2', 'u1', { agentId: 'agent-b' })

    useMessageStore.getState().upsertMany([u1, a1, a2], 'db')

    stampInferredTurnTerminalStatus('room-1')

    expect(useMessageStore.getState().entities.u1?.turnTerminalStatus).toBe('completed')
  })

  it('stamps failed when active_runs stale but all agents failed', () => {
    const u1 = makeUser('u1')
    const agents = ['a1', 'a2'].map((id, i) =>
      makeAgent(id, 'u1', {
        agentId: `agent-${i}`,
        taskStatus: 'failed',
        content: 'Task failed due to timeout',
      }),
    )

    useMessageStore.getState().upsertMany([u1, ...agents], 'db')

    stampInferredTurnTerminalStatus('room-1', {
      activeRunTriggerMessageIds: new Set(['u1']),
    })

    expect(useMessageStore.getState().entities.u1?.turnTerminalStatus).toBe('failed')
  })

  it('skips turn tied to an active room run (reload mid-synthesis)', () => {
    const u1 = makeUser('u1')
    const a1 = makeAgent('a1', 'u1', { agentId: 'agent-a' })
    const a2 = makeAgent('a2', 'u1', { agentId: 'agent-b' })

    useMessageStore.getState().upsertMany([u1, a1, a2], 'db')

    stampInferredTurnTerminalStatus('room-1', {
      activeRunTriggerMessageIds: new Set(['u1']),
    })

    expect(useMessageStore.getState().entities.u1?.turnTerminalStatus).toBeUndefined()
  })

  it('does not stamp active turns', () => {
    const u1 = makeUser('u1')
    const a1 = makeAgent('a1', 'u1', { taskStatus: 'working', content: '' })

    useMessageStore.getState().upsertMany([u1, a1], 'db')

    stampInferredTurnTerminalStatus('room-1')

    expect(useMessageStore.getState().entities.u1?.turnTerminalStatus).toBeUndefined()
  })

  it('stamps when deterministic summary entity exists in store', () => {
    const u1 = makeUser('u1')
    const a1 = makeAgent('a1', 'u1', { agentId: 'agent-a' })
    const a2 = makeAgent('a2', 'u1', { agentId: 'agent-b' })
    const summary = makeAgent('s1', 'u1', {
      agentId: 'summary',
      content: '2 agents responded.',
      summaryOrigin: 'deterministic',
    })

    useMessageStore.getState().upsertMany([u1, a1, a2, summary], 'db')

    stampInferredTurnTerminalStatus('room-1')

    expect(useMessageStore.getState().entities.u1?.turnTerminalStatus).toBe('completed')
  })
})
