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
      // Fetch both sources in parallel. Either may fail independently.
      const [journals, legacyResponse] = await Promise.all([
        fetchRecentTurns(roomId, getToken).catch(() => null),
        inquiryRoomMessagesByRoomId(roomId, getToken).catch(() => null),
      ])
      if (canceled) return

      // Phase 1: Inject turn journals (authoritative source)
      const journalTurnIds = new Set<string>()
      if (journals && journals.length > 0) {
        for (const journal of journals) {
          journalTurnIds.add(journal.turn_id)
          for (const wireEvent of journal.events) {
            const event = camelCaseEvent(wireEvent as Record<string, unknown>)
            store.append(event.turnId, event)
          }
        }
      }

      // Phase 2: Convert legacy messages, but skip any turns already covered
      // by journal data. Legacy turns use the user message_id as turnId.
      if (legacyResponse?.success && legacyResponse.message_list?.length) {
        const pseudoTurns = convertLegacyMessagesToTurnEvents(legacyResponse.message_list)
        for (const turn of pseudoTurns) {
          if (journalTurnIds.has(turn.turnId)) continue
          for (const event of turn.events) {
            store.append(event.turnId, event)
          }
        }
      }
    }

    hydrate()
    return () => { canceled = true }
  }, [roomId, getToken])
}
