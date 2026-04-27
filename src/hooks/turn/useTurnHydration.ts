'use client'

import { useEffect, useRef } from 'react'
import { fetchPendingHitlRequests, type HitlPendingRequest } from '@/lib/api/hitl'
import { useMessageStore } from '@/stores/message-store'
import { useTurnEventStore } from '@/stores/turn-event-store'
import type { TurnEvent } from '@/stores/turn-event-store/types'

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
    useTurnEventStore.getState().reset()

    async function hydrate() {
      // Single source of truth: turn-event-store is derived only from message-store.
      // Trigger the bridge to rebuild immediately when messages already exist.
      const msgStore = useMessageStore.getState()
      if (msgStore.roomId === roomId && msgStore.orderedIds.length > 0) {
        msgStore.nudgeSyncBridge()
      }

      // Hydration is complete when either:
      // 1) turn store has at least one turn, or
      // 2) message store is fully hydrated for this room and empty.
      const maybeMarkHydrated = () => {
        const turnStore = useTurnEventStore.getState()
        const m = useMessageStore.getState()
        if (turnStore.orderedTurnIds.length > 0) {
          turnStore.markHydrated()
          return true
        }
        if (m.hydratedFromDb && m.roomId === roomId && m.orderedIds.length === 0) {
          turnStore.markHydrated()
          return true
        }
        return false
      }

      if (!maybeMarkHydrated()) {
        // Wait for message-store hydration/updates then mark turn-store hydrated.
        const unsub = useMessageStore.subscribe(
          s => [s.version, s.hydratedFromDb, s.roomId] as const,
          () => {
            if (canceled) {
              unsub()
              return
            }
            const m = useMessageStore.getState()
            if (m.roomId !== roomId) return
            if (maybeMarkHydrated()) {
              unsub()
            }
          },
        )
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
