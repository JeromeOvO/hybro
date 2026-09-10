import type { QueryClient } from '@tanstack/react-query'
import type { RoomHistoryItem, RoomHistoryResponse } from '@/lib/api/room'

export const ROOM_HISTORY_QUERY_KEY = ['room-history'] as const

export function roomHistoryQueryKey(userId: string) {
  return [...ROOM_HISTORY_QUERY_KEY, userId] as const
}

export async function optimisticallyUpsertCreatedRoom(
  queryClient: QueryClient,
  userId: string,
  room: RoomHistoryItem,
): Promise<void> {
  const queryKey = roomHistoryQueryKey(userId)
  await queryClient.cancelQueries({ queryKey, exact: true })
  queryClient.setQueryData<RoomHistoryResponse>(queryKey, history => {
    const latestCachedActivity = history?.items.reduce((latest, item) => {
      const activity = Date.parse(item.last_activity_at)
      return Number.isFinite(activity) ? Math.max(latest, activity) : latest
    }, 0) ?? 0
    const serverActivity = Date.parse(room.last_activity_at)
    const optimisticActivity = Math.max(
      Date.now(),
      Number.isFinite(serverActivity) ? serverActivity : 0,
      latestCachedActivity + 1,
    )

    return {
      items: [
        { ...room, last_activity_at: new Date(optimisticActivity).toISOString() },
        ...(history?.items.filter(item => item.room_id !== room.room_id) ?? []),
      ],
    }
  })
}

export async function optimisticallyMarkRoomProcessing(
  queryClient: QueryClient,
  userId: string,
  roomId: string,
  lastActivityAt: string,
): Promise<() => void> {
  const queryKey = roomHistoryQueryKey(userId)
  await queryClient.cancelQueries({ queryKey, exact: true })
  const previousRoom = queryClient
    .getQueryData<RoomHistoryResponse>(queryKey)
    ?.items.find(item => item.room_id === roomId)

  queryClient.setQueryData<RoomHistoryResponse>(queryKey, history => history ? {
    items: history.items.map(item => item.room_id === roomId
      ? { ...item, last_activity_at: lastActivityAt, status: 'processing' }
      : item),
  } : history)

  return () => {
    if (!previousRoom) return
    queryClient.setQueryData<RoomHistoryResponse>(queryKey, history => history ? {
      items: history.items.map(item => item.room_id === roomId
        ? {
            ...item,
            last_activity_at: previousRoom.last_activity_at,
            status: previousRoom.status,
          }
        : item),
    } : history)
  }
}
