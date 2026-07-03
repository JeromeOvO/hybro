import type { MessageEntity } from '@/stores/message-store/types'
import type { PendingHitl, HitlState } from './conversation-types'

export function selectPendingHitls(
  roomId: string,
  entities: Record<string, MessageEntity>,
  orderedIds: string[],
): PendingHitl[] {
  const hitlEntities: MessageEntity[] = []
  for (const id of orderedIds) {
    const e = entities[id]
    if (e && e.roomId === roomId && e.hitlRequestId) hitlEntities.push(e)
  }

  const activeGroupIds = new Set<string>()
  for (const e of hitlEntities) {
    if (e.hitlGroupId && !e.hitlResolved && !e.hitlUserAnswer) {
      activeGroupIds.add(e.hitlGroupId)
    }
  }

  return hitlEntities
    .filter(e => {
      if (!e.hitlGroupId) return !e.hitlResolved
      return activeGroupIds.has(e.hitlGroupId)
    })
    .map(e => ({
      hitlId: e.hitlRequestId!,
      agentName: e.senderName,
      question: e.hitlPrompt ?? e.content ?? e.taskStatusMessage ?? '',
      promptType: e.hitlPromptType ?? 'text',
      choices: e.hitlChoices ?? undefined,
      messageId: e.id,
      groupId: e.hitlGroupId ?? undefined,
      groupTotal: e.hitlGroupTotal ?? undefined,
      groupIndex: e.hitlGroupIndex ?? undefined,
      isAnswered: e.hitlResolved === true || !!e.hitlUserAnswer,
    }))
}

export function selectAgentHitlState(entity: MessageEntity): HitlState | null {
  if (!entity.hitlRequestId) return null
  return {
    hitlId: entity.hitlRequestId,
    resolved: entity.hitlResolved === true,
    question: entity.hitlPrompt ?? entity.content ?? entity.taskStatusMessage ?? '',
    answer: entity.hitlUserAnswer ?? null,
  }
}
