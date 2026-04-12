'use client'

import { useEffect } from 'react'
import { fetchRecentTurns } from '@/lib/api/turns'
import { inquiryRoomMessagesByRoomId } from '@/lib/api/room'
import { convertLegacyMessagesToTurnEvents } from '@/lib/turn-event-store/legacy-adapter'
import { useTurnEventStore } from '@/stores/turn-event-store'
import { camelCaseEvent } from '@/hooks/turn/useSSEToEventLog'

export function useTurnHydration(
  roomId: string,
  getToken?: () => Promise<string | null>,
) {
  useEffect(() => {
    if (!roomId) return

    let canceled = false
    const store = useTurnEventStore.getState()
    store.reset()

    async function hydrate() {
      const journals = await fetchRecentTurns(roomId, getToken)
      if (canceled) return

      if (journals && journals.length > 0) {
        for (const journal of journals) {
          for (const wireEvent of journal.events) {
            const event = camelCaseEvent(wireEvent as Record<string, unknown>)
            store.append(event.turnId, event)
          }
        }
        return
      }

      try {
        const response = await inquiryRoomMessagesByRoomId(roomId, getToken)
        if (canceled) return
        if (!response.success || !response.message_list?.length) return

        const pseudoTurns = convertLegacyMessagesToTurnEvents(response.message_list)
        for (const turn of pseudoTurns) {
          for (const event of turn.events) {
            store.append(event.turnId, event)
          }
        }
      } catch {
        // Legacy fallback failed — store stays empty, SSE will populate on next events
      }
    }

    hydrate()
    return () => { canceled = true }
  }, [roomId, getToken])
}
