import { useCallback, useEffect, useRef } from 'react'
import type { MutableRefObject } from 'react'
import { useMessageStore } from '@/stores/message-store'
import { hydrateRoomFromDb } from '@/lib/room-sync/hydrate-room'

export function useRoomHydration(
  roomId: string,
  userId: string | undefined,
  userName: string | undefined,
  getToken: (() => Promise<string | null>) | undefined,
  room: unknown,
  hitlRequestIndex: MutableRefObject<Map<string, string>>,
  getAgentName: (agentId: string) => Promise<string>,
  getAgentSource: (agentId: string | undefined) => 'cloud' | 'local' | 'hub' | undefined,
) {
  const hydrationStartedRef = useRef<string | null>(null)
  const reconcileInflightRef = useRef<string | null>(null)

  const hydrateFromDb = useCallback(async (targetRoomId: string) => {
    const store = useMessageStore.getState()
    if (store.hydratedFromDb && store.roomId === targetRoomId) return

    await hydrateRoomFromDb({
      roomId: targetRoomId,
      phase: 'initial',
      getToken,
      userId,
      userName,
      room,
      getAgentName,
      getAgentSource,
      hitlRequestIndex,
    })
  }, [getToken, userId, userName, getAgentName, getAgentSource, hitlRequestIndex, room])

  const reconcileWithDb = useCallback(async (targetRoomId: string) => {
    if (reconcileInflightRef.current === targetRoomId) return
    reconcileInflightRef.current = targetRoomId
    try {
      let result = await hydrateRoomFromDb({
        roomId: targetRoomId,
        phase: 'reconcile',
        getToken,
        userId,
        userName,
        getAgentName,
        getAgentSource,
      })

      if (!result.fetchFailed && result.filteredCount === 0) {
        await new Promise(r => setTimeout(r, 2000))
        if (reconcileInflightRef.current !== targetRoomId) return
        result = await hydrateRoomFromDb({
          roomId: targetRoomId,
          phase: 'reconcile',
          getToken,
          userId,
          userName,
          getAgentName,
          getAgentSource,
        })
      }
    } catch (error) {
      console.error('[NormalizedStore] Reconciliation failed:', error)
    } finally {
      if (reconcileInflightRef.current === targetRoomId) {
        reconcileInflightRef.current = null
      }
    }
  }, [getToken, userId, userName, getAgentName, getAgentSource])

  useEffect(() => {
    hydrationStartedRef.current = null
    reconcileInflightRef.current = null
  }, [roomId])

  useEffect(() => {
    if (!roomId || !userName || !room) return
    if (hydrationStartedRef.current === roomId) return
    hydrationStartedRef.current = roomId
    hydrateFromDb(roomId)
  }, [roomId, userName, room, hydrateFromDb])

  return { hydrateFromDb, reconcileWithDb }
}
