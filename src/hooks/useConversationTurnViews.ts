import { useShallow } from 'zustand/react/shallow'
import { useMessageStore } from '@/stores/message-store'
import { selectConversationTurns } from '@/lib/selectors'
import type { ConversationTurnView } from '@/lib/selectors/conversation-types'

export function useConversationTurnViews(roomId: string): ConversationTurnView[] {
  return useMessageStore(
    useShallow(s => selectConversationTurns(roomId, s.entities, s.orderedIds))
  )
}
