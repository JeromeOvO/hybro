import { inquiryRoomMessagesByRoomId } from '@/lib/api/room'
import {
  convertApiMessageToIncoming,
  detectAndMarkStaleTasks,
  filterHydrationMessages,
} from '@/stores/message-store'
import { useMessageStore } from '@/stores/message-store'
import { useRoomUiStore } from '@/stores/room-ui-store'
import { isTerminalState } from '@/lib/types/sse'
import type { IncomingMessage } from '@/stores/message-store/types'
import { applyDbMessages } from './apply-db-messages'
import { overlayHitlForRoom } from './hitl-overlay'
import type { HydrateRoomOptions, HydrateRoomResult } from './types'

function markInitialHydrationComplete(targetRoomId: string): boolean {
  const store = useMessageStore.getState()
  if (store.roomId !== targetRoomId) return false

  store.markDbSynced()
  useRoomUiStore.getState().markInitialHydrated(targetRoomId)
  return true
}

function pruneStaleProcessingPlaceholder(targetRoomId: string): void {
  const store = useMessageStore.getState()
  if (store.roomId !== targetRoomId) return

  const placeholderId = `processing-placeholder-${targetRoomId}`
  if (!store.entities[placeholderId]) return

  const hasActiveTask = Object.values(store.entities).some(
    e => e.roomId === targetRoomId
      && !e.isEphemeral
      && e.taskStatus
      && !isTerminalState(e.taskStatus),
  )
  if (hasActiveTask) return

  store.removeMessage(placeholderId)
}

async function fetchAndNormalizeMessages(
  targetRoomId: string,
  options: HydrateRoomOptions,
): Promise<{ filtered: IncomingMessage[]; rawCount: number } | { fetchFailed: true }> {
  const { getToken, userId, userName, getAgentName, getAgentSource } = options
  const response = await inquiryRoomMessagesByRoomId(targetRoomId, getToken)
  if (!response.success || !response.message_list) {
    return { fetchFailed: true }
  }

  const incomingMessages = await Promise.all(
    response.message_list.map(msg =>
      convertApiMessageToIncoming(msg, { userId, userName, getAgentName, getAgentSource }),
    ),
  )
  const withStaleDetection = detectAndMarkStaleTasks(incomingMessages)
  const filtered = filterHydrationMessages(withStaleDetection)

  return { filtered, rawCount: response.message_list.length }
}

const EMPTY_RESULT: HydrateRoomResult = {
  rawCount: 0,
  filteredCount: 0,
  appliedCount: 0,
  pendingHitlCount: 0,
  fetchFailed: false,
  hitlFetchFailed: false,
}

/**
 * Unified DB hydration / reconcile / HITL overlay orchestrator.
 * React hooks own gates (hydratedFromDb, inflight dedupe, retries).
 */
export async function hydrateRoomFromDb(options: HydrateRoomOptions): Promise<HydrateRoomResult> {
  const { roomId, phase } = options

  if (phase === 'hitl_overlay') {
    if (!options.hitlRequestIndex) {
      console.warn('[room-sync] hitl_overlay skipped — missing hitlRequestIndex')
      return { ...EMPTY_RESULT }
    }
    try {
      const pending = await overlayHitlForRoom({
        roomId,
        getToken: options.getToken,
        hitlRequestIndex: options.hitlRequestIndex,
        getAgentName: options.getAgentName,
        getAgentSource: options.getAgentSource,
      })
      return { ...EMPTY_RESULT, pendingHitlCount: pending.size }
    } catch (err) {
      console.error('[HITL] overlay failed:', err)
      return { ...EMPTY_RESULT, hitlFetchFailed: true }
    }
  }

  try {
    const fetched = await fetchAndNormalizeMessages(roomId, options)
    if ('fetchFailed' in fetched) {
      console.error(`❌ [room-sync] Failed to load messages for room ${roomId}`)
      if (phase === 'initial') {
        markInitialHydrationComplete(roomId)
      }
      return { ...EMPTY_RESULT, fetchFailed: true }
    }

    const { filtered, rawCount } = fetched
    const applyResult = applyDbMessages(roomId, filtered)

    if (!applyResult) {
      return {
        rawCount,
        filteredCount: filtered.length,
        appliedCount: 0,
        pendingHitlCount: 0,
        fetchFailed: false,
        hitlFetchFailed: false,
      }
    }

    if (phase === 'initial') {
      markInitialHydrationComplete(roomId)
    } else {
      useMessageStore.getState().markDbSynced()
      pruneStaleProcessingPlaceholder(roomId)
    }

    let pendingHitlCount = 0
    let hitlFetchFailed = false
    if (phase === 'initial' && options.hitlRequestIndex) {
      try {
        const hydratedIds = new Set(filtered.map(m => m.id))
        const pending = await overlayHitlForRoom({
          roomId,
          getToken: options.getToken,
          hitlRequestIndex: options.hitlRequestIndex,
          getAgentName: options.getAgentName,
          getAgentSource: options.getAgentSource,
          hydratedIdsForResolve: hydratedIds,
        })
        pendingHitlCount = pending.size
      } catch (hitlErr) {
        hitlFetchFailed = true
        console.error('[HITL] Failed to overlay HITL state during hydration:', hitlErr)
      }
    }

    return {
      rawCount,
      filteredCount: filtered.length,
      appliedCount: applyResult.appliedCount,
      pendingHitlCount,
      fetchFailed: false,
      hitlFetchFailed,
    }
  } catch (error) {
    console.error(`❌ [room-sync] ${phase} failed for room ${roomId}:`, error)
    if (phase === 'initial') {
      markInitialHydrationComplete(roomId)
    }
    return { ...EMPTY_RESULT, fetchFailed: true }
  }
}
