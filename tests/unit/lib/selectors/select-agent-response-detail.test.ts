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
    expect(detail?.isStreaming).toBe(false)
  })

  it('uses stream buffer text and streaming flag when provided', () => {
    const { entities, orderedIds } = setup([
      createUserMessage({ id: 'user-1', roomId: 'room-1' }),
      createAgentMessage({
        id: 'agent-1',
        roomId: 'room-1',
        relatedMessageId: 'user-1',
        taskStatus: TASK_STATE.WORKING,
        content: 'final from db',
      }),
    ])

    const detail = selectAgentResponseDetail('room-1', 'agent-1', entities, orderedIds, {
      text: 'live tokens',
      artifacts: [],
      isComplete: false,
      roomId: 'room-1',
      lastUpdatedAt: Date.now(),
    })

    expect(detail?.content).toBe('live tokens')
    expect(detail?.isStreaming).toBe(true)
    expect(detail?.artifacts).toBeUndefined()
  })

  it('shows non-text entity artifacts while streaming text from buffer', () => {
    const entityArtifacts = [{ artifactId: 'file-1', parts: [{ kind: 'file' as const, file: { uri: 's3://x' } }] }]
    const { entities, orderedIds } = setup([
      createUserMessage({ id: 'user-1', roomId: 'room-1' }),
      createAgentMessage({
        id: 'agent-1',
        roomId: 'room-1',
        relatedMessageId: 'user-1',
        taskStatus: TASK_STATE.WORKING,
        content: '',
        artifacts: entityArtifacts,
      }),
    ])

    const detail = selectAgentResponseDetail('room-1', 'agent-1', entities, orderedIds, {
      text: 'streaming report body',
      artifacts: [],
      isComplete: false,
      roomId: 'room-1',
      lastUpdatedAt: Date.now(),
    })

    expect(detail?.content).toBe('streaming report body')
    expect(detail?.isStreaming).toBe(true)
    expect(detail?.artifacts).toEqual(entityArtifacts)
  })

  it('ignores live buffer when entity taskStatus is terminal (strict terminal guard)', () => {
    const { entities, orderedIds } = setup([
      createUserMessage({ id: 'user-1', roomId: 'room-1' }),
      createAgentMessage({
        id: 'agent-1',
        roomId: 'room-1',
        relatedMessageId: 'user-1',
        taskStatus: TASK_STATE.COMPLETED,
        content: 'Hermes final answer',
      }),
    ])

    const detail = selectAgentResponseDetail('room-1', 'agent-1', entities, orderedIds, {
      text: 'Synthesis text that must not appear',
      artifacts: [],
      isComplete: false,
      roomId: 'room-1',
      lastUpdatedAt: Date.now(),
    })

    expect(detail?.content).toBe('Hermes final answer')
    expect(detail?.isStreaming).toBe(false)
    expect(detail?.display.label).not.toBe('Streaming')
  })

  it('ignores live buffer when entity is canceled mid-stream', () => {
    const { entities, orderedIds } = setup([
      createUserMessage({ id: 'user-1', roomId: 'room-1' }),
      createAgentMessage({
        id: 'agent-1',
        roomId: 'room-1',
        relatedMessageId: 'user-1',
        taskStatus: TASK_STATE.CANCELED,
        content: '',
      }),
    ])

    const detail = selectAgentResponseDetail('room-1', 'agent-1', entities, orderedIds, {
      text: 'partial stream',
      artifacts: [],
      isComplete: false,
      roomId: 'room-1',
      lastUpdatedAt: Date.now(),
    })

    expect(detail?.content).toBe('')
    expect(detail?.isStreaming).toBe(false)
  })

  it('shows entity artifacts when buffer is complete but not yet cleared', () => {
    const entityArtifacts = [{ artifactId: 'file-1', parts: [{ kind: 'file' as const, file: { uri: 's3://x' } }] }]
    const { entities, orderedIds } = setup([
      createUserMessage({ id: 'user-1', roomId: 'room-1' }),
      createAgentMessage({
        id: 'agent-1',
        roomId: 'room-1',
        relatedMessageId: 'user-1',
        taskStatus: TASK_STATE.COMPLETED,
        content: 'done',
        artifacts: entityArtifacts,
      }),
    ])

    const detail = selectAgentResponseDetail('room-1', 'agent-1', entities, orderedIds, {
      text: 'done',
      artifacts: [],
      isComplete: true,
      roomId: 'room-1',
      lastUpdatedAt: Date.now(),
    })

    expect(detail?.isStreaming).toBe(false)
    expect(detail?.artifacts).toEqual(entityArtifacts)
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
