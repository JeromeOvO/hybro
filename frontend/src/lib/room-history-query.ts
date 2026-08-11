import type { QueryClient } from '@tanstack/react-query'
import type { RoomHistoryResponse } from '@/lib/api/room'

export const ROOM_HISTORY_QUERY_KEY = ['room-history'] as const

export function roomHistoryQueryKey(userId: string) {
  return [...ROOM_HISTORY_QUERY_KEY, userId] as const
}

export function optimisticallyMarkRoomProcessing(
  queryClient: QueryClient,
  userId: string,
  roomId: string,
  lastActivityAt: string,
): () => void {
  const queryKey = roomHistoryQueryKey(userId)
  const previousHistory = queryClient.getQueryData<RoomHistoryResponse>(queryKey)

  queryClient.setQueryData<RoomHistoryResponse>(queryKey, history => history ? {
    items: history.items.map(item => item.room_id === roomId
      ? { ...item, last_activity_at: lastActivityAt, status: 'processing' }
      : item),
  } : history)

  return () => {
    if (previousHistory !== undefined) {
      queryClient.setQueryData(queryKey, previousHistory)
      return
    }
    queryClient.removeQueries({ queryKey, exact: true })
  }
}
