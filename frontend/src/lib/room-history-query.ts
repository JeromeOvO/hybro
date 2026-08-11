export const ROOM_HISTORY_QUERY_KEY = ['room-history'] as const

export function roomHistoryQueryKey(userId: string) {
  return [...ROOM_HISTORY_QUERY_KEY, userId] as const
}
