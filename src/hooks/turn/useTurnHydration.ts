'use client'

import { useEffect } from 'react'
import { fetchRecentTurns } from '@/lib/api/turns'
import { inquiryRoomMessagesByRoomId } from '@/lib/api/room'
import { fetchPendingHitlRequests } from '@/lib/api/hitl'
import { convertLegacyMessagesToTurnEvents } from '@/lib/turn-event-store/legacy-adapter'
import { useTurnEventStore } from '@/stores/turn-event-store'
import type { TurnEvent } from '@/stores/turn-event-store/types'
import { camelCaseEvent } from '@/hooks/turn/useSSEToEventLog'

interface HydrationTurn {
  turnId: string
  startTs: number
  events: TurnEvent[]
}

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

      // Phase 1: Convert journal turns
      const journalTurnIds = new Set<string>()
      const journalTurns: HydrationTurn[] = []
      if (journals && journals.length > 0) {
        for (const journal of journals) {
          journalTurnIds.add(journal.turn_id)
          const events = journal.events.map(we =>
            camelCaseEvent(we as Record<string, unknown>),
          )
          journalTurns.push({
            turnId: journal.turn_id,
            startTs: events[0]?.ts ?? 0,
            events,
          })
        }
      }

      // Phase 2: Convert legacy messages, skip turns already covered by journals
      const legacyTurns: HydrationTurn[] = []
      if (legacyResponse?.success && legacyResponse.message_list?.length) {
        const pseudoTurns = convertLegacyMessagesToTurnEvents(legacyResponse.message_list)
        for (const turn of pseudoTurns) {
          if (journalTurnIds.has(turn.turnId)) continue
          legacyTurns.push({
            turnId: turn.turnId,
            startTs: turn.events[0]?.ts ?? 0,
            events: turn.events,
          })
        }
      }

      // Phase 3: Merge and sort chronologically, then append.
      // Store orders turns by insertion, so we must append in timestamp order.
      const allTurns = [...journalTurns, ...legacyTurns]
      allTurns.sort((a, b) => a.startTs - b.startTs)

      for (const turn of allTurns) {
        for (const event of turn.events) {
          store.append(event.turnId, event)
        }
      }

      // Phase 4: Inject pending HITL requests into the turn store.
      // This runs after turns are loaded so the store is populated.
      // (overlayPendingHitlRequests in useRoomHydration may race ahead
      // of this hook, seeing an empty store and skipping injection.)
      if (canceled) return
      try {
        const hitlRes = await fetchPendingHitlRequests(roomId, getToken)
        if (canceled) return
        if (hitlRes.requests?.length) {
          const turnStore = useTurnEventStore.getState()
          let activeTurnId: string | undefined
          for (let i = turnStore.orderedTurnIds.length - 1; i >= 0; i--) {
            const id = turnStore.orderedTurnIds[i]
            const log = turnStore.turnLogs.get(id)
            if (log && !log.isTerminal()) { activeTurnId = id; break }
          }
          if (activeTurnId) {
            // Derive seq from the turn's current max so events appear at the
            // end of the log, preserving correct rail order.
            const log = turnStore.turnLogs.get(activeTurnId)
            const logEvents = log?.getEvents() ?? []
            let nextSeq = logEvents.length > 0 ? logEvents[logEvents.length - 1].seq + 1 : 1

            for (const req of hitlRes.requests) {
              // Deterministic eventId so TurnEventLog deduplicates if
              // overlayPendingHitlRequests already injected the same request.
              turnStore.append(activeTurnId, {
                eventId: `hitl-pending-${req.request_id}`,
                turnId: activeTurnId,
                seq: nextSeq++,
                ts: req.created_at ? new Date(req.created_at).getTime() : Date.now(),
                type: 'hitl_requested',
                hitlId: req.request_id,
                source: req.source,
                agentName: req.agent_name || undefined,
                prompt: req.prompt,
                promptType: req.prompt_type,
                choices: req.choices ?? undefined,
                groupId: req.group_id ?? undefined,
                groupTotal: req.group_total ?? undefined,
                groupIndex: req.group_index ?? undefined,
              } as TurnEvent)
            }
          }
        }
      } catch {
        // HITL fetch failure is non-fatal — SSE reconnect will retry
      }
    }

    hydrate()
    return () => { canceled = true }
  }, [roomId, getToken])
}
