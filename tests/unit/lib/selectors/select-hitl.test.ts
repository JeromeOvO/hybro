import { describe, it, expect, beforeEach } from 'vitest'
import { selectPendingHitls, selectAgentHitlState } from '@/lib/selectors/select-hitl'
import { useMessageStore } from '@/stores/message-store'
import { createAgentMessage, resetCounters } from '../../../fixtures'
import { TASK_STATE } from '@/lib/types/sse'

function setup(msgs: ReturnType<typeof createAgentMessage>[]) {
  const store = useMessageStore.getState()
  store.clearRoom()
  store.setRoom('room-1')
  for (const m of msgs) store.upsertMessage(m, 'db')
  const s = useMessageStore.getState()
  return { entities: s.entities, orderedIds: s.orderedIds }
}

describe('selectPendingHitls', () => {
  beforeEach(() => {
    useMessageStore.getState().clearRoom()
    resetCounters()
  })

  it('returns non-grouped unresolved HITL', () => {
    const { entities, orderedIds } = setup([
      createAgentMessage({
        id: 'hitl-1', roomId: 'room-1',
        hitlRequestId: 'req-1', hitlPrompt: 'What is your name?',
        senderName: 'Analyst', taskStatus: TASK_STATE.INPUT_REQUIRED,
      }),
    ])
    const result = selectPendingHitls('room-1', entities, orderedIds)
    expect(result).toHaveLength(1)
    expect(result[0].hitlId).toBe('req-1')
    expect(result[0].question).toBe('What is your name?')
    expect(result[0].isAnswered).toBe(false)
  })

  it('excludes resolved non-grouped HITL', () => {
    const { entities, orderedIds } = setup([
      createAgentMessage({
        id: 'hitl-1', roomId: 'room-1',
        hitlRequestId: 'req-1', hitlResolved: true,
        hitlPrompt: 'Done?', hitlUserAnswer: 'Yes',
        senderName: 'Analyst', taskStatus: TASK_STATE.COMPLETED,
      }),
    ])
    const result = selectPendingHitls('room-1', entities, orderedIds)
    expect(result).toHaveLength(0)
  })

  it('returns entire group when any member is unanswered', () => {
    const { entities, orderedIds } = setup([
      createAgentMessage({
        id: 'g1-q1', roomId: 'room-1',
        hitlRequestId: 'req-1', hitlPrompt: 'Q1?',
        hitlGroupId: 'group-A', hitlGroupTotal: 2, hitlGroupIndex: 0,
        hitlResolved: true, hitlUserAnswer: 'A1',
        senderName: 'Analyst', taskStatus: TASK_STATE.INPUT_REQUIRED,
      }),
      createAgentMessage({
        id: 'g1-q2', roomId: 'room-1',
        hitlRequestId: 'req-2', hitlPrompt: 'Q2?',
        hitlGroupId: 'group-A', hitlGroupTotal: 2, hitlGroupIndex: 1,
        senderName: 'Analyst', taskStatus: TASK_STATE.INPUT_REQUIRED,
      }),
    ])
    const result = selectPendingHitls('room-1', entities, orderedIds)
    expect(result).toHaveLength(2)
    expect(result[0].isAnswered).toBe(true)
    expect(result[1].isAnswered).toBe(false)
    expect(result[0].groupId).toBe('group-A')
  })

  it('passes through choice promptType and choices', () => {
    const { entities, orderedIds } = setup([
      createAgentMessage({
        id: 'hitl-c', roomId: 'room-1',
        hitlRequestId: 'req-c', hitlPrompt: 'Pick one',
        hitlPromptType: 'choice', hitlChoices: ['A', 'B', 'C'],
        senderName: 'Analyst', taskStatus: TASK_STATE.INPUT_REQUIRED,
      }),
    ])
    const result = selectPendingHitls('room-1', entities, orderedIds)
    expect(result).toHaveLength(1)
    expect(result[0].promptType).toBe('choice')
    expect(result[0].choices).toEqual(['A', 'B', 'C'])
  })

  it('passes through confirmation promptType', () => {
    const { entities, orderedIds } = setup([
      createAgentMessage({
        id: 'hitl-cf', roomId: 'room-1',
        hitlRequestId: 'req-cf', hitlPrompt: 'Approve deploy?',
        hitlPromptType: 'confirmation',
        senderName: 'Analyst', taskStatus: TASK_STATE.INPUT_REQUIRED,
      }),
    ])
    const result = selectPendingHitls('room-1', entities, orderedIds)
    expect(result).toHaveLength(1)
    expect(result[0].promptType).toBe('confirmation')
    expect(result[0].choices).toBeUndefined()
  })

  it('defaults promptType to text when hitlPromptType is undefined', () => {
    const { entities, orderedIds } = setup([
      createAgentMessage({
        id: 'hitl-t', roomId: 'room-1',
        hitlRequestId: 'req-t', hitlPrompt: 'What?',
        senderName: 'Analyst', taskStatus: TASK_STATE.INPUT_REQUIRED,
      }),
    ])
    const result = selectPendingHitls('room-1', entities, orderedIds)
    expect(result[0].promptType).toBe('text')
  })

  it('excludes HITL from other rooms', () => {
    const { entities, orderedIds } = setup([
      createAgentMessage({
        id: 'hitl-other', roomId: 'room-2',
        hitlRequestId: 'req-1', hitlPrompt: 'Q?',
        senderName: 'Analyst', taskStatus: TASK_STATE.INPUT_REQUIRED,
      }),
    ])
    const result = selectPendingHitls('room-1', entities, orderedIds)
    expect(result).toHaveLength(0)
  })
})

describe('selectAgentHitlState', () => {
  beforeEach(() => resetCounters())

  it('returns null for entity without hitlRequestId', () => {
    const entity = {
      ...createAgentMessage({ id: 'a1', roomId: 'room-1' }),
      source: 'db' as const, sourceVersion: 1,
      displayType: 'agent-bubble' as const,
      isEphemeral: false, createdAt: Date.now(), updatedAt: Date.now(),
    }
    expect(selectAgentHitlState(entity as any)).toBeNull()
  })

  it('returns HitlState with question from hitlPrompt', () => {
    const entity = {
      ...createAgentMessage({
        id: 'a1', roomId: 'room-1',
        hitlRequestId: 'req-1', hitlPrompt: 'How?', hitlUserAnswer: 'Like this',
        hitlResolved: true,
      }),
      source: 'db' as const, sourceVersion: 1,
      displayType: 'agent-bubble' as const,
      isEphemeral: false, createdAt: Date.now(), updatedAt: Date.now(),
    }
    const result = selectAgentHitlState(entity as any)
    expect(result).toEqual({
      hitlId: 'req-1',
      resolved: true,
      question: 'How?',
      answer: 'Like this',
    })
  })

  it('falls back to content when hitlPrompt is missing', () => {
    const entity = {
      ...createAgentMessage({
        id: 'a1', roomId: 'room-1',
        hitlRequestId: 'req-1', content: 'Agent needs input',
      }),
      source: 'db' as const, sourceVersion: 1,
      displayType: 'agent-bubble' as const,
      isEphemeral: false, createdAt: Date.now(), updatedAt: Date.now(),
    }
    const result = selectAgentHitlState(entity as any)
    expect(result!.question).toBe('Agent needs input')
  })
})
