import { buildTurns } from '@/lib/room-timeline/build-turns'
import { isCanceledMultiAgentTurn, isFailedMultiAgentTurn } from '@/lib/room-timeline/derive-final-answer'
import { getStripSourceResults } from '@/lib/room-timeline/turn-live-shell'
import {
  canStampTurnTerminalFromEntityState,
  terminalStatusForTurn,
} from '@/lib/room-timeline/turn-terminal-stamp'
import type { TurnViewModel } from '@/lib/room-timeline/types'
import { useMessageStore } from './index'

export interface StampInferredTurnTerminalOptions {
  /** User message IDs with an in-flight room run — do not infer terminal status. */
  activeRunTriggerMessageIds?: ReadonlySet<string>
}

function shouldStampTurnTerminal(
  turn: TurnViewModel,
  activeRunTriggerMessageIds: ReadonlySet<string>,
): boolean {
  if (!turn.userMessageId) return false

  const real = getStripSourceResults(turn)
  if (isFailedMultiAgentTurn(turn, real) || isCanceledMultiAgentTurn(turn, real)) {
    return true
  }

  const backendRunActive = activeRunTriggerMessageIds.has(turn.userMessageId)
  return canStampTurnTerminalFromEntityState(turn, real, backendRunActive)
}

/**
 * After DB hydration, infer turnTerminalStatus on user entities for historical turns.
 * Live sessions still rely on processing_status SSE for the active last turn.
 */
export function stampInferredTurnTerminalStatus(
  roomId: string,
  options: StampInferredTurnTerminalOptions = {},
): void {
  const activeRunTriggerMessageIds = options.activeRunTriggerMessageIds ?? new Set<string>()
  const store = useMessageStore.getState()
  if (store.roomId !== roomId) return

  const roomOrderedIds = store.orderedIds.filter(id => store.entities[id]?.roomId === roomId)
  const turns = buildTurns(store.entities, roomOrderedIds, [])

  for (const turn of turns) {
    const userId = turn.userMessageId
    if (!userId) continue

    const user = store.entities[userId]
    if (!user || user.turnTerminalStatus) continue
    if (!shouldStampTurnTerminal(turn, activeRunTriggerMessageIds)) continue

    store.upsertMessage({
      id: userId,
      roomId,
      messageType: 'user',
      content: user.content,
      senderName: user.senderName,
      timestamp: user.timestamp,
      turnTerminalStatus: terminalStatusForTurn(turn),
    }, 'db')
  }
}

export function collectActiveRunTriggerMessageIds(
  room: unknown,
): Set<string> {
  if (!room || typeof room !== 'object') return new Set()
  const runs = (room as { active_runs?: Array<{ trigger_message_id?: string | null }> | null }).active_runs
  if (!runs?.length) return new Set()
  const ids = runs
    .map(r => r.trigger_message_id)
    .filter((id): id is string => typeof id === 'string' && id.length > 0)
  return new Set(ids)
}
