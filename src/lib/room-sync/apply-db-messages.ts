import type { IncomingMessage } from '@/stores/message-store/types'
import { useMessageStore } from '@/stores/message-store'
import { useStreamingStore } from '@/stores/streaming-store'

export interface ApplyDbMessagesResult {
  appliedIds: ReadonlySet<string>
  appliedCount: number
  filteredCount: number
}

/**
 * Upsert DB-normalized messages and clear streaming buffers only for ids that
 * were actually written (SSE may reject DB overwrites for active streams).
 */
export function applyDbMessages(
  targetRoomId: string,
  filtered: IncomingMessage[],
): ApplyDbMessagesResult | null {
  const store = useMessageStore.getState()
  if (store.roomId !== targetRoomId) return null

  const appliedIds = store.upsertMany(filtered, 'db')
  useStreamingStore.getState().clearByMessageIds(appliedIds)

  return {
    appliedIds,
    appliedCount: appliedIds.size,
    filteredCount: filtered.length,
  }
}
