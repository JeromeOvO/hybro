import type { MessageEntity } from '@/stores/message-store'

export interface MessageTurnGroup {
  userMsgId: string | null
  childMsgIds: string[]
}

export function groupMessagesByUserTurn(
  orderedIds: string[],
  entities: Record<string, MessageEntity>,
): MessageTurnGroup[] {
  if (orderedIds.length === 0) return []

  const groups: MessageTurnGroup[] = []
  let current: MessageTurnGroup | null = null

  for (const id of orderedIds) {
    const entity = entities[id]
    const isUser = entity?.messageType === 'user'

    if (isUser) {
      current = { userMsgId: id, childMsgIds: [] }
      groups.push(current)
    } else {
      if (!current) {
        current = { userMsgId: null, childMsgIds: [] }
        groups.push(current)
      }
      current.childMsgIds.push(id)
    }
  }

  return groups
}

export function escapeCssIdent(value: string): string {
  return typeof CSS !== 'undefined' && CSS.escape
    ? CSS.escape(value)
    : value.replace(/"/g, '\\"')
}
