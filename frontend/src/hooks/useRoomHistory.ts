'use client'

import { useQuery } from '@tanstack/react-query'
import { listRoomHistory, type RoomHistoryResponse, type RoomHistoryStatus } from '@/lib/api/room'
import { roomHistoryQueryKey } from '@/lib/room-history-query'

export const ACTIVE_ROOM_STATUSES: ReadonlySet<RoomHistoryStatus> = new Set(['queued', 'processing', 'awaiting_input'])

export function useRoomHistory({ userId, enabled }: { userId: string; enabled: boolean }) {
  return useQuery<RoomHistoryResponse>({
    queryKey: roomHistoryQueryKey(userId),
    queryFn: ({ signal }) => listRoomHistory(undefined, signal),
    enabled,
    staleTime: 10_000,
    refetchOnWindowFocus: true,
    refetchInterval: (query) =>
      query.state.data?.items.some((item) => ACTIVE_ROOM_STATUSES.has(item.status)) ? 10_000 : false,
  })
}
