import type { MessageEntity } from '@/stores/message-store/types'
import type { ComposerState } from './conversation-types'
import { selectPendingHitls } from './select-hitl'
import { isPendingState } from '@/lib/types/sse'

export function selectComposerState(
  roomId: string,
  entities: Record<string, MessageEntity>,
  orderedIds: string[],
): ComposerState {
  const pendingHitls = selectPendingHitls(roomId, entities, orderedIds)

  const hasActiveTask = orderedIds.some(id => {
    const e = entities[id]
    return e
      && e.roomId === roomId
      && !e.isEphemeral
      && e.messageType === 'agent'
      && e.taskStatus != null
      && isPendingState(e.taskStatus)
  })

  return {
    mode: pendingHitls.length > 0 ? 'hitl_responding' : 'normal',
    isProcessing: hasActiveTask,
    pendingHitls,
  }
}
