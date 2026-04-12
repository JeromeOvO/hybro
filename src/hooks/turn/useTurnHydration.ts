'use client'

import { useEffect, useRef } from 'react'
import { fetchRecentTurns } from '@/lib/api/turns'
import { inquiryRoomMessagesByRoomId } from '@/lib/api/room'
import { convertLegacyMessagesToTurnEvents } from '@/lib/turn-event-store/legacy-adapter'
import { useTurnEventStore } from '@/stores/turn-event-store'
import { camelCaseEvent } from '@/hooks/turn/useSSEToEventLog'

export function useTurnHydration(
  roomId: string,
  getToken?: () => Promise<string | null>,
) {
  const hydrationRef = useRef<string | null>(null)

  useEffect(() => {
    if (!roomId || hydrationRef.current === roomId) return
    hydrationRef.current = roomId

    async function hydrate() {
      const store = useTurnEventStore.getState()
      store.reset()

      const journals = await fetchRecentTurns(roomId, getToken)

      if (journals && journals.length > 0) {
        for (const journal of journals) {
          for (const wireEvent of journal.events) {
            const event = camelCaseEvent(wireEvent as Record<string, unknown>)
            store.append(event.turnId, event)
          }
        }
        console.log(`[TurnHydration] Hydrated ${journals.length} turns from /turns/recent`)
        return
      }

      try {
        const response = await inquiryRoomMessagesByRoomId(roomId, getToken)
        if (!response.success || !response.message_list?.length) {
          console.log('[TurnHydration] No messages to hydrate')
          return
        }

        const pseudoTurns = convertLegacyMessagesToTurnEvents(response.message_list)
        for (const turn of pseudoTurns) {
          for (const event of turn.events) {
            store.append(event.turnId, event)
          }
        }
        console.log(`[TurnHydration] Hydrated ${pseudoTurns.length} turns via legacy adapter`)
      } catch (err) {
        console.warn('[TurnHydration] Legacy fallback failed:', err)
      }
    }

    hydrate()
  }, [roomId, getToken])
}
