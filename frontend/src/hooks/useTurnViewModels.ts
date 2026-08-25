import React from 'react'
import { useMessageStore } from '@/stores/message-store'
import { useRoomUiStore } from '@/stores/room-ui-store'
import { buildTurnsIncremental } from '@/lib/room-timeline/build-turns'
import type { TurnViewModel } from '@/lib/room-timeline/types'
import type { RawTimelineEvent } from '@/lib/room-timeline/types'

const EMPTY_EVENTS: readonly RawTimelineEvent[] = []

function filterRoomMessages(
  roomId: string,
  entities: Record<string, import('@/stores/message-store/types').MessageEntity>,
  orderedIds: string[],
): string[] {
  return orderedIds.filter(id => entities[id]?.roomId === roomId)
}

/**
 * Derives TurnViewModel[] from the message store for a room.
 * Streaming overlays are applied in leaf components via useStreamingStore.
 */
export function useTurnViewModels(roomId: string): TurnViewModel[] {
  const version = useMessageStore(s => s.version)
  const roomProcessingActive = useRoomUiStore(
    s => s.rooms[roomId]?.processing ?? false,
  )
  const activeRunTriggerMessageIds = useRoomUiStore(
    s => s.rooms[roomId]?.activeRunTriggerMessageIds ?? [],
  )
  const prev = React.useRef<TurnViewModel[]>([])

  const next = React.useMemo(() => {
    const { entities, orderedIds } = useMessageStore.getState()
    const roomOrderedIds = filterRoomMessages(roomId, entities, orderedIds)
    return buildTurnsIncremental(prev.current, entities, roomOrderedIds, EMPTY_EVENTS, {
      roomProcessingActive,
      activeRunTriggerMessageIds: new Set(activeRunTriggerMessageIds),
    })
  }, [roomId, version, roomProcessingActive, activeRunTriggerMessageIds])

  prev.current = next
  return next
}
