'use client'

import { useEffect, useRef } from 'react'
import { fetchRecentTurns } from '@/lib/api/turns'
import { fetchPendingHitlRequests, type HitlPendingRequest } from '@/lib/api/hitl'
import { useMessageStore } from '@/stores/message-store'
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
  // Ref-ify getToken so the effect only re-runs when roomId changes.
  // Clerk's useAuth().getToken is not referentially stable — including it in
  // the deps array caused store.reset() on every parent re-render, wiping
  // all turns mid-session (the "No messages yet" bug).
  const getTokenRef = useRef(getToken)
  getTokenRef.current = getToken

  useEffect(() => {
    if (!roomId) return

    let canceled = false
    const store = useTurnEventStore.getState()
    store.reset()

    async function hydrate() {
      // Fetch journal turns (the native turn-event source).
      // Legacy messages are NOT fetched here — useRoomHydration populates
      // the message store and useMessageStoreSync bridges them into the
      // turn-event-store. Having two independent paths for the same data
      // caused duplicate slot rendering due to race conditions.
      const journals = await fetchRecentTurns(roomId, getTokenRef.current).catch(() => null)
      if (canceled) return

      // Convert journal turns
      const journalTurns: HydrationTurn[] = []
      if (journals && journals.length > 0) {
        for (const journal of journals) {
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

      // Sort chronologically and append.
      journalTurns.sort((a, b) => a.startTs - b.startTs)
      for (const turn of journalTurns) {
        for (const event of turn.events) {
          store.append(event.turnId, event)
        }
      }

      store.markHydrated()

      // If journal hydration yielded no turns but the message store has data,
      // bump message-store version to trigger useMessageStoreSync bridge.
      if (store.orderedTurnIds.length === 0) {
        const msgStore = useMessageStore.getState()
        if (msgStore.orderedIds.length > 0 && msgStore.roomId === roomId) {
          msgStore.nudgeSyncBridge()
        }
      }

      // Inject pending HITL requests into the turn store.
      // Turns may not be populated yet (useMessageStoreSync creates them
      // asynchronously after useRoomHydration loads the message store).
      // Retry a few times to allow the sync bridge to finish.
      if (canceled) return
      try {
        const hitlRes = await fetchPendingHitlRequests(roomId, getTokenRef.current)
        if (canceled) return
        if (hitlRes.requests?.length) {
          await injectHitlRequests(hitlRes.requests, canceled)
        }
      } catch {
        // HITL fetch failure is non-fatal — SSE reconnect will retry
      }
    }

    hydrate()
    return () => { canceled = true }
  }, [roomId])
}

/**
 * Inject pending HITL requests into the most recent active turn.
 * Retries up to 5 times (250ms apart) to wait for useMessageStoreSync
 * to create turns from the message store.
 */
async function injectHitlRequests(
  requests: HitlPendingRequest[],
  canceled: boolean,
): Promise<void> {
  for (let attempt = 0; attempt < 5; attempt++) {
    if (canceled) return

    const turnStore = useTurnEventStore.getState()
    let activeTurnId: string | undefined
    for (let i = turnStore.orderedTurnIds.length - 1; i >= 0; i--) {
      const id = turnStore.orderedTurnIds[i]
      const log = turnStore.turnLogs.get(id)
      if (log && !log.isTerminal()) { activeTurnId = id; break }
    }

    if (activeTurnId) {
      const log = turnStore.turnLogs.get(activeTurnId)
      const logEvents = log?.getEvents() ?? []
      let nextSeq = logEvents.length > 0 ? logEvents[logEvents.length - 1].seq + 1 : 1

      for (const req of requests) {
        // Deterministic eventId so TurnEventLog deduplicates if
        // overlayPendingHitlRequests already injected the same request.
        turnStore.append(activeTurnId, {
          eventId: `hitl-pending-${req.request_id}`,
          turnId: activeTurnId,
          seq: nextSeq++,
          ts: new Date(req.created_at).getTime(),
          type: 'hitl_requested',
          hitlId: req.request_id,
          source: req.source,
          agentName: req.agent_name ?? undefined,
          prompt: req.prompt,
          promptType: req.prompt_type,
          choices: req.choices ?? undefined,
          groupId: req.group_id ?? undefined,
          groupTotal: req.group_total ?? undefined,
          groupIndex: req.group_index ?? undefined,
        } as TurnEvent)
      }
      return
    }

    // Turns not yet available — wait for useMessageStoreSync
    await new Promise(resolve => setTimeout(resolve, 250))
  }
}
