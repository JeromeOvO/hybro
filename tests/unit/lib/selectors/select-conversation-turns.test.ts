import { describe, it, expect, beforeEach } from 'vitest'
import { selectConversationTurns } from '@/lib/selectors/select-conversation-turns'
import { useMessageStore } from '@/stores/message-store'
import { createUserMessage, createAgentMessage, resetCounters } from '../../../fixtures'
import { TASK_STATE } from '@/lib/types/sse'

function setup(msgs: ReturnType<typeof createUserMessage>[]) {
  const store = useMessageStore.getState()
  store.clearRoom()
  store.setRoom('room-1')
  for (const m of msgs) store.upsertMessage(m, m.id.startsWith('cr:') ? 'optimistic' : 'db')
  const s = useMessageStore.getState()
  return { entities: s.entities, orderedIds: s.orderedIds }
}

describe('selectConversationTurns', () => {
  beforeEach(() => {
    useMessageStore.getState().clearRoom()
    resetCounters()
  })

  it('groups agent under user message via relatedMessageId', () => {
    const { entities, orderedIds } = setup([
      createUserMessage({ id: 'user-1', roomId: 'room-1' }),
      createAgentMessage({
        id: 'agent-1', roomId: 'room-1',
        relatedMessageId: 'user-1',
        taskStatus: TASK_STATE.COMPLETED, content: 'Done',
      }),
    ])
    const turns = selectConversationTurns('room-1', entities, orderedIds)
    expect(turns).toHaveLength(1)
    expect(turns[0].turnId).toBe('user-1')
    expect(turns[0].userMessage!.id).toBe('user-1')
    expect(turns[0].blocks.length).toBeGreaterThanOrEqual(2)
    const card = turns[0].blocks.find(b => b.type === 'agent_card')
    expect(card).toMatchObject({ type: 'agent_card', messageId: 'agent-1' })
  })

  it('optimistic turn uses cr: prefix as temporary turnId', () => {
    const { entities, orderedIds } = setup([
      createUserMessage({ id: 'cr:req-123', roomId: 'room-1', clientRequestId: 'req-123' }),
    ])
    const turns = selectConversationTurns('room-1', entities, orderedIds)
    expect(turns).toHaveLength(1)
    expect(turns[0].turnId).toBe('cr:req-123')
  })

  it('after replaceMessageId, turnId becomes persisted id', () => {
    const store = useMessageStore.getState()
    store.clearRoom()
    store.setRoom('room-1')
    store.upsertMessage(
      createUserMessage({ id: 'cr:req-123', roomId: 'room-1', clientRequestId: 'req-123' }),
      'optimistic',
    )
    store.replaceMessageId('cr:req-123', 'persisted-user-1')
    const s = useMessageStore.getState()
    const turns = selectConversationTurns('room-1', s.entities, s.orderedIds)
    expect(turns).toHaveLength(1)
    expect(turns[0].turnId).toBe('persisted-user-1')
    expect(turns[0].turnId).not.toMatch(/^cr:/)
  })

  it('hydrated history never produces cr: turnId', () => {
    const { entities, orderedIds } = setup([
      createUserMessage({ id: 'db-user-1', roomId: 'room-1', clientRequestId: 'old-req' }),
      createAgentMessage({
        id: 'db-agent-1', roomId: 'room-1',
        relatedMessageId: 'db-user-1', taskStatus: TASK_STATE.COMPLETED,
      }),
    ])
    const turns = selectConversationTurns('room-1', entities, orderedIds)
    for (const t of turns) {
      expect(t.turnId).not.toMatch(/^cr:/)
    }
  })

  it('unresolved agents go to __unresolved__ bucket', () => {
    const { entities, orderedIds } = setup([
      createUserMessage({ id: 'user-1', roomId: 'room-1' }),
      createAgentMessage({ id: 'orphan-1', roomId: 'room-1', taskStatus: TASK_STATE.COMPLETED }),
    ])
    const turns = selectConversationTurns('room-1', entities, orderedIds)
    const unresolved = turns.find(t => t.turnId === '__unresolved__')
    expect(unresolved).toBeDefined()
    expect(unresolved!.userMessage).toBeNull()
  })

  it('unresolved does NOT auto-attach to nearest user turn', () => {
    const { entities, orderedIds } = setup([
      createUserMessage({ id: 'user-1', roomId: 'room-1' }),
      createAgentMessage({ id: 'orphan-1', roomId: 'room-1', agentId: undefined, taskStatus: TASK_STATE.COMPLETED, senderName: 'Orphan Agent' }),
    ])
    const turns = selectConversationTurns('room-1', entities, orderedIds)
    const userTurn = turns.find(t => t.turnId === 'user-1')
    const orphanInUserTurn = userTurn?.blocks.some(
      b => (b.type === 'agent_card' || b.type === 'agent_content') &&
           'agentId' in b && b.agentId === 'orphan-1'
    )
    expect(orphanInUserTurn).toBeFalsy()
    const unresolvedTurn = turns.find(t => t.turnId === '__unresolved__')
    expect(unresolvedTurn).toBeDefined()
    expect(unresolvedTurn!.blocks.some(
      b => b.type === 'agent_card' && b.agentId === 'orphan-1'
    )).toBe(true)
  })

  it('ephemeral placeholder produces synthetic working card', () => {
    const { entities, orderedIds } = setup([
      createUserMessage({ id: 'cr:req-1', roomId: 'room-1', clientRequestId: 'req-1' }),
      createAgentMessage({
        id: 'placeholder-1', roomId: 'room-1',
        isEphemeral: true, clientRequestId: 'req-1',
        taskStatus: TASK_STATE.WORKING, senderName: 'HYBRO AI',
      }),
    ])
    const turns = selectConversationTurns('room-1', entities, orderedIds)
    expect(turns).toHaveLength(1)
    const cards = turns[0].blocks.filter(b => b.type === 'agent_card')
    expect(cards).toHaveLength(1)
    expect(cards[0].type === 'agent_card' && cards[0].display.label).toBe('Working')
  })

  it('deduplicates synthetic card when real agent arrives', () => {
    const { entities, orderedIds } = setup([
      createUserMessage({ id: 'cr:req-1', roomId: 'room-1', clientRequestId: 'req-1' }),
      createAgentMessage({
        id: 'placeholder-1', roomId: 'room-1',
        isEphemeral: true, clientRequestId: 'req-1',
        taskStatus: TASK_STATE.WORKING, senderName: 'HYBRO AI',
      }),
      createAgentMessage({
        id: 'real-agent-1', roomId: 'room-1',
        clientRequestId: 'req-1', relatedMessageId: 'cr:req-1',
        taskStatus: TASK_STATE.WORKING, senderName: 'Security Analyst',
        agentId: 'sa-1',
      }),
    ])
    const turns = selectConversationTurns('room-1', entities, orderedIds)
    expect(turns).toHaveLength(1)
    const cards = turns[0].blocks.filter(b => b.type === 'agent_card')
    expect(cards).toHaveLength(1)
    expect(cards[0].type === 'agent_card' && cards[0].agentName).toBe('Security Analyst')
  })

  it('creates user_answer block for resolved HITL', () => {
    const { entities, orderedIds } = setup([
      createUserMessage({ id: 'user-1', roomId: 'room-1' }),
      createAgentMessage({
        id: 'hitl-1', roomId: 'room-1',
        relatedMessageId: 'user-1',
        hitlRequestId: 'req-1', hitlPrompt: 'Confirm?',
        hitlResolved: true, hitlUserAnswer: 'Yes',
        senderName: 'Analyst', taskStatus: TASK_STATE.COMPLETED,
      }),
    ])
    const turns = selectConversationTurns('room-1', entities, orderedIds)
    const answers = turns[0].blocks.filter(b => b.type === 'user_answer')
    expect(answers).toHaveLength(1)
    expect(answers[0].type === 'user_answer' && answers[0].question).toBe('Confirm?')
    expect(answers[0].type === 'user_answer' && answers[0].answer).toBe('Yes')
  })

  it('adds agent_divider between different agents in same turn', () => {
    const { entities, orderedIds } = setup([
      createUserMessage({ id: 'user-1', roomId: 'room-1' }),
      createAgentMessage({
        id: 'a1', roomId: 'room-1', relatedMessageId: 'user-1',
        agentId: 'agent-a', senderName: 'Agent A',
        taskStatus: TASK_STATE.COMPLETED, content: 'Response A',
      }),
      createAgentMessage({
        id: 'a2', roomId: 'room-1', relatedMessageId: 'user-1',
        agentId: 'agent-b', senderName: 'Agent B',
        taskStatus: TASK_STATE.COMPLETED, content: 'Response B',
      }),
    ])
    const turns = selectConversationTurns('room-1', entities, orderedIds)
    const dividers = turns[0].blocks.filter(b => b.type === 'agent_divider')
    expect(dividers.length).toBeGreaterThanOrEqual(1)
  })

  it('user message with attachments retains attachments on the turn', () => {
    const attachments = [
      { fileId: 'f1', mimeType: 'image/png', fileName: 'screenshot.png', sizeBytes: 1024 },
      { fileId: 'f2', mimeType: 'application/pdf', fileName: 'doc.pdf', sizeBytes: 2048 },
    ]
    const { entities, orderedIds } = setup([
      createUserMessage({ id: 'user-1', roomId: 'room-1', attachments }),
    ])
    const turns = selectConversationTurns('room-1', entities, orderedIds)
    expect(turns).toHaveLength(1)
    expect(turns[0].userMessage?.attachments).toEqual(attachments)
  })

  it('agent message with artifacts creates agent_content block with artifacts', () => {
    const artifacts = [
      { artifactId: 'art-1', name: 'result.json', parts: [{ kind: 'text' as const, text: '{}' }] },
    ]
    const { entities, orderedIds } = setup([
      createUserMessage({ id: 'user-1', roomId: 'room-1' }),
      createAgentMessage({
        id: 'a1', roomId: 'room-1', relatedMessageId: 'user-1',
        agentId: 'agent-a', senderName: 'Agent A',
        taskStatus: TASK_STATE.COMPLETED, content: 'Here are the results',
        artifacts,
      }),
    ])
    const turns = selectConversationTurns('room-1', entities, orderedIds)
    const contentBlocks = turns[0].blocks.filter(b => b.type === 'agent_content')
    expect(contentBlocks).toHaveLength(1)
    expect(contentBlocks[0]).toHaveProperty('artifacts', artifacts)
  })
})
