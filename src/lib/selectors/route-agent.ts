import type { MessageEntity } from '@/stores/message-store/types'

const MAX_HOPS = 2

export function routeAgentToTurn(
  entity: MessageEntity,
  userMessageIds: Set<string>,
  entityById: Record<string, MessageEntity>,
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
    for (const uid of userMessageIds) {
      const userEntity = entityById[uid]
      if (userEntity?.clientRequestId === entity.clientRequestId) return uid
    }
  }

  // Tier 3: unresolved
  return 'unresolved'
}
