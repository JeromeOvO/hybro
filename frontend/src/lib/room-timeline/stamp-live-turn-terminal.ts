import { buildTurns } from './build-turns'
import { isMultiAgentTurnReadyForDeterministicDone } from './multi-agent-turn-complete'
import { getStripSourceResults } from './turn-live-shell'
import { stampTurnTerminalFromBackendTruth } from './turn-terminal-stamp'
import { findProcessingStatusUserEntity } from '@/hooks/room/processing-status-log'
import type { ProcessingLifecycle } from '@/hooks/room/processing-lifecycle'
import { useMessageStore } from '@/stores/message-store'

/**
 * When all mentioned agents finish but turn-level processing_status was missed,
 * infer turnTerminalStatus from agent results and clear the live processing lifecycle.
 */
export function stampLiveTurnTerminalIfInferable(
  conversationId: string,
  lifecycle: ProcessingLifecycle,
  hint: {
    clientRequestId?: string | null
    relatedMessageId?: string | null
  },
): boolean {
  const store = useMessageStore.getState()
  if (store.conversationId !== conversationId) return false

  const user = findProcessingStatusUserEntity(conversationId, {
    messageId: hint.relatedMessageId,
    clientRequestId: hint.clientRequestId,
    preferClientRequestId: true,
  })
  if (!user || user.turnTerminalStatus) return false

  const roomOrderedIds = store.orderedIds.filter(id => store.entities[id]?.conversationId === conversationId)
  const turn = buildTurns(store.entities, roomOrderedIds, []).find(t => t.userMessageId === user.id)
  if (!turn) return false

  const real = getStripSourceResults(turn)
  if (!isMultiAgentTurnReadyForDeterministicDone(turn, real)) {
    return stampTurnTerminalFromBackendTruth(conversationId, lifecycle, hint, null)
  }

  return stampTurnTerminalFromBackendTruth(conversationId, lifecycle, hint, false)
}
