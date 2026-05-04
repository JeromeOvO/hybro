import { describe, it, expect, beforeEach } from 'vitest'
import { buildClientRequestUserMessageIndex, routeAgentToTurn } from '@/lib/selectors/route-agent'
import type { MessageEntity } from '@/stores/message-store/types'
import { createUserMessage, createAgentMessage, resetCounters } from '../../../fixtures'
import { useMessageStore } from '@/stores/message-store'

function makeEntities(msgs: ReturnType<typeof createUserMessage>[]) {
  const store = useMessageStore.getState()
  store.clearRoom()
  store.setRoom('room-1')
  for (const m of msgs) store.upsertMessage(m, m.id.startsWith('cr:') ? 'optimistic' : 'db')
  const s = useMessageStore.getState()
  return { entities: s.entities, orderedIds: s.orderedIds }
}

function route(
  entity: MessageEntity,
  userMessageIds: Set<string>,
  entities: Record<string, MessageEntity>,
) {
  return routeAgentToTurn(
    entity,
    userMessageIds,
    entities,
    buildClientRequestUserMessageIndex(userMessageIds, entities),
  )
}

describe('routeAgentToTurn', () => {
  beforeEach(() => {
    useMessageStore.getState().clearRoom()
    resetCounters()
  })

  it('routes agent via relatedMessageId to user message (tier 1)', () => {
    const user = createUserMessage({ id: 'user-1', roomId: 'room-1' })
    const agent = createAgentMessage({ id: 'agent-1', roomId: 'room-1', relatedMessageId: 'user-1' })
    const { entities } = makeEntities([user, agent])
    const userMessageIds = new Set(['user-1'])
    const result = route(entities['agent-1'], userMessageIds, entities)
    expect(result).toBe('user-1')
  })

  it('routes via relatedMessageId chain (2 hops max)', () => {
    const user = createUserMessage({ id: 'user-1', roomId: 'room-1' })
    const intermediate = createAgentMessage({ id: 'agent-mid', roomId: 'room-1', relatedMessageId: 'user-1' })
    const leaf = createAgentMessage({ id: 'agent-leaf', roomId: 'room-1', relatedMessageId: 'agent-mid' })
    const { entities } = makeEntities([user, intermediate, leaf])
    const userMessageIds = new Set(['user-1'])
    const result = route(entities['agent-leaf'], userMessageIds, entities)
    expect(result).toBe('user-1')
  })

  it('does NOT follow more than 2 hops', () => {
    const user = createUserMessage({ id: 'user-1', roomId: 'room-1' })
    const hop1 = createAgentMessage({ id: 'hop1', roomId: 'room-1', relatedMessageId: 'user-1' })
    const hop2 = createAgentMessage({ id: 'hop2', roomId: 'room-1', relatedMessageId: 'hop1' })
    const hop3 = createAgentMessage({ id: 'hop3', roomId: 'room-1', relatedMessageId: 'hop2' })
    const { entities } = makeEntities([user, hop1, hop2, hop3])
    const userMessageIds = new Set(['user-1'])
    const result = route(entities['hop3'], userMessageIds, entities)
    expect(result).toBe('unresolved')
  })

  it('routes via clientRequestId to optimistic user (tier 2, live only)', () => {
    const user = createUserMessage({ id: 'cr:req-123', roomId: 'room-1', clientRequestId: 'req-123' })
    const agent = createAgentMessage({ id: 'agent-1', roomId: 'room-1', clientRequestId: 'req-123' })
    const { entities } = makeEntities([user, agent])
    const userMessageIds = new Set(['cr:req-123'])
    const result = route(entities['agent-1'], userMessageIds, entities)
    expect(result).toBe('cr:req-123')
  })

  it('uses a prebuilt clientRequestId index without scanning user ids', () => {
    const user = createUserMessage({ id: 'cr:req-123', roomId: 'room-1', clientRequestId: 'req-123' })
    const inaccessibleUser = createUserMessage({ id: 'cr:req-456', roomId: 'room-1', clientRequestId: 'req-456' })
    const agent = createAgentMessage({ id: 'agent-1', roomId: 'room-1', clientRequestId: 'req-456' })
    const { entities } = makeEntities([user, inaccessibleUser, agent])

    Object.defineProperty(entities, inaccessibleUser.id, {
      configurable: true,
      get() {
        throw new Error('clientRequestId fallback scanned user ids')
      },
    })

    const result = routeAgentToTurn(
      entities['agent-1'],
      new Set([user.id, inaccessibleUser.id]),
      entities,
      new Map([[inaccessibleUser.clientRequestId!, inaccessibleUser.id]]),
    )

    expect(result).toBe(inaccessibleUser.id)
  })

  it('prefers relatedMessageId over clientRequestId when both present', () => {
    const user = createUserMessage({ id: 'user-1', roomId: 'room-1', clientRequestId: 'req-123' })
    const agent = createAgentMessage({ id: 'agent-1', roomId: 'room-1', relatedMessageId: 'user-1', clientRequestId: 'req-123' })
    const { entities } = makeEntities([user, agent])
    const userMessageIds = new Set(['user-1'])
    const result = route(entities['agent-1'], userMessageIds, entities)
    expect(result).toBe('user-1')
  })

  it('returns unresolved for agent without relatedMessageId or clientRequestId', () => {
    const user = createUserMessage({ id: 'user-1', roomId: 'room-1' })
    const orphan = createAgentMessage({ id: 'orphan-1', roomId: 'room-1' })
    const { entities } = makeEntities([user, orphan])
    const userMessageIds = new Set(['user-1'])
    const result = route(entities['orphan-1'], userMessageIds, entities)
    expect(result).toBe('unresolved')
  })

  it('does NOT auto-attach unresolved agent to most recent turn', () => {
    const user1 = createUserMessage({ id: 'user-1', roomId: 'room-1' })
    const user2 = createUserMessage({ id: 'user-2', roomId: 'room-1' })
    const orphan = createAgentMessage({ id: 'orphan-1', roomId: 'room-1' })
    const { entities } = makeEntities([user1, user2, orphan])
    const userMessageIds = new Set(['user-1', 'user-2'])
    const result = route(entities['orphan-1'], userMessageIds, entities)
    expect(result).toBe('unresolved')
  })
})
