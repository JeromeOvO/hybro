import { useCallback, useEffect, useRef } from 'react'
import type { MutableRefObject } from 'react'
import { inquiryRoomMessagesByRoomId } from '@/lib/api/room'
import { fetchPendingHitlRequests } from '@/lib/api/hitl'
import { useMessageStore, detectAndMarkStaleTasks, filterHydrationMessages, convertApiMessageToIncoming } from '@/stores/message-store'
import { useStreamingStore } from '@/stores/streaming-store'
import { useRoomUiStore } from '@/stores/room-ui-store'
import { overlayPendingHitlRequests } from './overlay-pending-hitl'

function markInitialHydrationComplete(targetRoomId: string): boolean {
  const store = useMessageStore.getState()
  if (store.roomId !== targetRoomId) return false

  store.markDbSynced()
  useRoomUiStore.getState().markInitialHydrated(targetRoomId)
  return true
}

export function useRoomHydration(
  roomId: string,
  userId: string | undefined,
  userName: string | undefined,
  getToken: (() => Promise<string | null>) | undefined,
  room: unknown,
  hitlRequestIndex: MutableRefObject<Map<string, string>>,
  getAgentName: (agentId: string) => Promise<string>,
  getAgentSource: (agentId: string | undefined) => 'cloud' | 'hub' | undefined,
) {
  const hydrationStartedRef = useRef<string | null>(null)

  const hydrateFromDb = useCallback(async (targetRoomId: string) => {
    const store = useMessageStore.getState()
    if (store.hydratedFromDb && store.roomId === targetRoomId) return

    console.log(`🔍 Loading messages for room: ${targetRoomId}`)
    const startTime = Date.now()

    try {
      const response = await inquiryRoomMessagesByRoomId(targetRoomId, getToken)
      if (!response.success || !response.message_list) {
        console.error(`❌ Failed to load messages for room ${targetRoomId}`)
        // Mark as hydrated even on failure so we don't stay in loading forever
        markInitialHydrationComplete(targetRoomId)
        return
      }

      console.log(`✅ Loaded ${response.message_list.length} messages in ${Date.now() - startTime}ms`)

      const incomingMessages = await Promise.all(
        response.message_list.map(msg =>
          convertApiMessageToIncoming(msg, { userId, userName, getAgentName, getAgentSource })
        )
      )
      const withStaleDetection = detectAndMarkStaleTasks(incomingMessages)
      const filtered = filterHydrationMessages(withStaleDetection)

      const msgStore = useMessageStore.getState()
      if (msgStore.roomId === targetRoomId) {
        const appliedIds = msgStore.upsertMany(filtered, 'db')
        // Clear streaming buffers only for messages that were actually written.
        // applyUpsert rejects writes for actively-streaming entities (Rule 2:
        // SSE wins over DB for non-terminal state), so using filtered.map(m=>m.id)
        // would clear live buffers for messages where the DB write was a no-op.
        useStreamingStore.getState().clearByMessageIds(appliedIds)
        markInitialHydrationComplete(targetRoomId)
        console.log(
          `[NormalizedStore] DB hydration: ${appliedIds.size}/${filtered.length} messages written ` +
          `(${response.message_list.length} raw, ${incomingMessages.length} converted, ` +
          `${incomingMessages.length - filtered.length} filtered, ` +
          `${filtered.length - appliedIds.size} rejected by upsert rules)`
        )
      }

      // Overlay HITL state after DB hydration to avoid race with SSE reconnect.
      // Pending requests get hitlResolved=false; input-required messages NOT in
      // the pending set are already resolved and get hitlResolved=true.
      try {
        const hitlRes = await fetchPendingHitlRequests(targetRoomId, getToken)
        const hitlStore = useMessageStore.getState()
        if (hitlStore.roomId !== targetRoomId) return

        let pendingMessageIds = new Set<string>()
        if (hitlRes.requests?.length) {
          console.log(`🔔 Hydration: overlaying ${hitlRes.requests.length} pending HITL request(s)`)
          pendingMessageIds = await overlayPendingHitlRequests(
            targetRoomId, hitlRes.requests,
            { getAgentName, getAgentSource, hitlRequestIndex },
          )
        }

        // Mark input-required messages from DB hydration that are NOT pending as already resolved.
        // Only check messages that came from this hydration batch, not SSE-created entities.
        const hydratedIds = new Set(filtered.map(m => m.id))
        for (const entity of Object.values(hitlStore.entities)) {
          if (
            entity.roomId === targetRoomId &&
            entity.taskStatus === 'input-required' &&
            hydratedIds.has(entity.id) &&
            !pendingMessageIds.has(entity.id)
          ) {
            hitlStore.upsertMessage({
              id: entity.id,
              roomId: targetRoomId,
              messageType: 'agent',
              content: entity.content,
              senderName: entity.senderName,
              timestamp: entity.timestamp,
              hitlResolved: true,
            }, 'sse')
          }
        }
      } catch (hitlErr) {
        console.error('[HITL] Failed to overlay HITL state during hydration:', hitlErr)
      }
    } catch (error) {
      console.error(`❌ Failed to load messages for room ${targetRoomId}:`, error)
      // Mark as hydrated on error to avoid infinite loading
      markInitialHydrationComplete(targetRoomId)
    }
  }, [getToken, userId, userName, getAgentName, getAgentSource, hitlRequestIndex])

  const reconcileInflightRef = useRef<string | null>(null)

  const reconcileOnce = useCallback(async (targetRoomId: string): Promise<number> => {
    const response = await inquiryRoomMessagesByRoomId(targetRoomId, getToken)
    if (!response.success || !response.message_list) return 0

    const incomingMessages = await Promise.all(
      response.message_list.map(msg =>
        convertApiMessageToIncoming(msg, { userId, userName, getAgentName, getAgentSource })
      )
    )
    const withStaleDetection = detectAndMarkStaleTasks(incomingMessages)
    const filtered = filterHydrationMessages(withStaleDetection)

    const store = useMessageStore.getState()
    if (store.roomId === targetRoomId) {
      const appliedIds = store.upsertMany(filtered, 'db')
      store.markDbSynced()
      // Clear streaming buffers only for messages that were actually written.
      // See hydrateFromDb for rationale — same rule applies here.
      useStreamingStore.getState().clearByMessageIds(appliedIds)
    }
    return filtered.length
  }, [getToken, userId, userName, getAgentName, getAgentSource])

  const reconcileWithDb = useCallback(async (targetRoomId: string) => {
    if (reconcileInflightRef.current === targetRoomId) return
    reconcileInflightRef.current = targetRoomId
    try {
      const count = await reconcileOnce(targetRoomId)

      // If the API returned zero messages the backend may not have persisted
      // yet.  Do a single retry after a short delay to cover write-latency.
      if (count === 0) {
        await new Promise(r => setTimeout(r, 2000))
        if (reconcileInflightRef.current !== targetRoomId) return
        await reconcileOnce(targetRoomId)
      }
    } catch (error) {
      console.error('[NormalizedStore] Reconciliation failed:', error)
    } finally {
      if (reconcileInflightRef.current === targetRoomId) {
        reconcileInflightRef.current = null
      }
    }
  }, [reconcileOnce])

  // Reset hydration gate + inflight reconcile on room switch
  useEffect(() => {
    hydrationStartedRef.current = null
    reconcileInflightRef.current = null
  }, [roomId])

  // Hydrate from DB once room data is available.
  // Gating on `room` ensures the room query has completed and pre-populated
  // agentNameCache from room_agent_set, so agent names resolve correctly
  // instead of falling back to "Agent <id>" on page refresh.
  useEffect(() => {
    if (!roomId || !userName || !room) return
    if (hydrationStartedRef.current === roomId) return
    hydrationStartedRef.current = roomId
    hydrateFromDb(roomId)
  }, [roomId, userName, room, hydrateFromDb])

  return { hydrateFromDb, reconcileWithDb }
}
