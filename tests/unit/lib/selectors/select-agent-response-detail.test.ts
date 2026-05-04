import { describe, expect, it, beforeEach } from 'vitest'
import { selectAgentResponseDetail } from '@/lib/selectors/select-agent-response-detail'
import { useMessageStore } from '@/stores/message-store'
import { createAgentMessage, createUserMessage, resetCounters } from '../../../fixtures'
import { TASK_STATE } from '@/lib/types/sse'

function setup(msgs: ReturnType<typeof createUserMessage>[]) {
  const store = useMessageStore.getState()
  store.clearRoom()
  store.setRoom('room-1')
  for (const m of msgs) store.upsertMessage(m, 'db')
  const s = useMessageStore.getState()
  return { entities: s.entities, orderedIds: s.orderedIds }
}

describe('selectAgentResponseDetail', () => {
  beforeEach(() => {
    useMessageStore.getState().clearRoom()
    resetCounters()
  })

  it('returns an agent response detail with its related user request', () => {
    const { entities, orderedIds } = setup([
      createUserMessage({
        id: 'user-1',
        roomId: 'room-1',
        content: 'Research a2a agents',
      }),
      createAgentMessage({
        id: 'agent-1',
        roomId: 'room-1',
        relatedMessageId: 'user-1',
        agentId: 'researcher-1',
        senderName: 'Researcher Alex',
        taskContent: 'Research a2a agents',
        taskStatus: TASK_STATE.COMPLETED,
        content: '# Report\n\nA2A findings.',
      }),
    ])

    const detail = selectAgentResponseDetail('room-1', 'agent-1', entities, orderedIds)

    expect(detail).toMatchObject({
      messageId: 'agent-1',
      agentId: 'researcher-1',
      agentName: 'Researcher Alex',
      taskDescription: 'Research a2a agents',
      content: '# Report\n\nA2A findings.',
      isStreaming: false,
    })
    expect(detail?.requestMessage?.id).toBe('user-1')
    expect(detail?.requestMessage?.content).toBe('Research a2a agents')
  })

  it('falls back to clientRequestId when relatedMessageId is absent', () => {
    const { entities, orderedIds } = setup([
      createUserMessage({
        id: 'user-client',
        roomId: 'room-1',
        clientRequestId: 'req-1',
        content: 'Write a report',
      }),
      createAgentMessage({
        id: 'agent-client',
        roomId: 'room-1',
        clientRequestId: 'req-1',
        agentId: 'agent-1',
        senderName: 'Writer',
        taskStatus: TASK_STATE.WORKING,
        content: 'Drafting report',
      }),
    ])

    const detail = selectAgentResponseDetail('room-1', 'agent-client', entities, orderedIds)

    expect(detail?.requestMessage?.id).toBe('user-client')
    expect(detail?.isStreaming).toBe(true)
  })

  it('returns null for non-agent or wrong-room messages', () => {
    const { entities, orderedIds } = setup([
      createUserMessage({ id: 'user-1', roomId: 'room-1' }),
      createAgentMessage({ id: 'agent-1', roomId: 'other-room' }),
    ])

    expect(selectAgentResponseDetail('room-1', 'user-1', entities, orderedIds)).toBeNull()
    expect(selectAgentResponseDetail('room-1', 'agent-1', entities, orderedIds)).toBeNull()
  })
})
