import type { MessageEntity } from '@/stores/message-store/types'

const MAX_HOPS = 2

export type ClientRequestUserMessageIndex = ReadonlyMap<string, string>

export function buildClientRequestUserMessageIndex(
  userMessageIds: Set<string>,
  entityById: Record<string, MessageEntity>,
): Map<string, string> {
  const index = new Map<string, string>()
  for (const uid of userMessageIds) {
    const clientRequestId = entityById[uid]?.clientRequestId
    if (clientRequestId && !index.has(clientRequestId)) {
      index.set(clientRequestId, uid)
    }
  }
  return index
}

export function routeAgentToTurn(
  entity: MessageEntity,
  userMessageIds: Set<string>,
  entityById: Record<string, MessageEntity>,
  clientRequestUserMessageIdByClientRequestId: ClientRequestUserMessageIndex,
): string | 'unresolved' {
  // Tier 1: relatedMessageId chain (stable path)
  if (entity.relatedMessageId) {
    let current = entity.relatedMessageId
    for (let hop = 0; hop < MAX_HOPS; hop++) {
      if (userMessageIds.has(current)) return current
      const parent = entityById[current]
      if (!parent?.relatedMessageId) break
      current = parent.relatedMessageId
    }
  }

  // Tier 2: clientRequestId (live correlation only)
  if (entity.clientRequestId) {
    const uid = clientRequestUserMessageIdByClientRequestId.get(entity.clientRequestId)
    if (uid && userMessageIds.has(uid)) return uid
  }

  // Tier 3: unresolved
  return 'unresolved'
}
