import { useEffect, useRef } from 'react'
import type { MutableRefObject } from 'react'
import type { UseQueryResult } from '@tanstack/react-query'
import type { SSEMessage } from '@/lib/types/sse'
import { fetchPendingHitlRequests } from '@/lib/api/hitl'
import { useRoomSSE } from '../useRoomSSE'
import type { ProcessingLifecycle } from './processing-lifecycle'
import { overlayPendingHitlRequests } from './overlay-pending-hitl'

export function useRoomSSEConnection(
  roomId: string,
  getToken: (() => Promise<string | null>) | undefined,
  sseEnabled: boolean,
  processing: boolean,
  lifecycle: ProcessingLifecycle,
  handleSSEMessage: (message: SSEMessage) => void,
  getAgentName: (agentId: string) => Promise<string>,
  getAgentSource: (agentId: string | undefined) => 'cloud' | 'hub' | undefined,
  hitlRequestIndex: MutableRefObject<Map<string, string>>,
  roomQuery: UseQueryResult<unknown, Error>,
  reconcileWithDb: (roomId: string) => Promise<void>,
  setSseConnected: (v: boolean) => void,
  setSseError: (v: string | null) => void,
) {
  // Initialize SSE connection
  const {
    connected: sseConnected,
    connecting: sseConnecting,
    error: sseError
  } = useRoomSSE({
    roomId,
    enabled: sseEnabled && !!roomId,
    getToken,
    onMessage: handleSSEMessage,
  })

  // Sync SSE state to zustand
  useEffect(() => {
    setSseConnected(!!sseConnected)
    setSseError(sseError ? String(sseError) : null)
  }, [sseConnected, sseError, setSseConnected, setSseError])

  // Track SSE disconnections during active processing.
  // If SSE drops while agents are working, we may have missed events and need
  // to reconcile with DB after processing completes.
  // On reconnect, restore any pending HITL requests and reconcile messages.
  const prevSseConnectedRef = useRef(false)
  const hasBeenConnectedRef = useRef(false)
  useEffect(() => {
    if (!sseConnected && processing) {
      console.log('⚠️ SSE disconnected during processing — will reconcile after completion')
      lifecycle.markSseDisconnection()
    }

    const justReconnected = sseConnected && !prevSseConnectedRef.current && roomId
    const isReconnection = justReconnected && hasBeenConnectedRef.current

    if (sseConnected) {
      hasBeenConnectedRef.current = true
    }

    // HITL reconnect catch-up: restore pending HITL requests after SSE reconnects
    if (justReconnected) {
      fetchPendingHitlRequests(roomId, getToken)
        .then(async (res) => {
          if (res.requests?.length) {
            console.log(`🔔 Restoring ${res.requests.length} pending HITL request(s)`)
            await overlayPendingHitlRequests(roomId, res.requests, {
              getAgentName, getAgentSource, hitlRequestIndex,
            })
          }
        })
        .catch((err) => {
          console.error('[HITL] Failed to fetch pending requests on reconnect:', err)
        })
    }

    // Any reconnection after a gap may have missed messages. Reconcile
    // unconditionally so the UI is fresh, regardless of processing state.
    if (isReconnection) {
      console.log('🔄 SSE reconnected after gap — reconciling with DB')
      reconcileWithDb(roomId)
    }

    // Safety-net: while processing is active, periodically verify backend truth.
    // This covers both reconnect gaps and cases where terminal SSE events are
    // missed without an explicit disconnect marker.
    let safetyTimer: ReturnType<typeof setTimeout> | null = null
    if (sseConnected && processing) {
      safetyTimer = setTimeout(async () => {
        try {
          const result = await roomQuery.refetch()
          const freshRoom = result.data as { processing_message_id?: string | null } | null
          if (freshRoom && !freshRoom.processing_message_id) {
            console.log('🔄 Safety-net: backend confirms processing ended — clearing stuck spinner')
            lifecycle.setProcessing(false)
            lifecycle.clearSseDisconnection()
            reconcileWithDb(roomId)
          }
        } catch {
          // Network error — next reconnect cycle or page refresh will retry
        }
      }, 15_000)
    }

    prevSseConnectedRef.current = sseConnected

    return () => {
      if (safetyTimer) clearTimeout(safetyTimer)
    }
  }, [sseConnected, processing, roomId, getToken, getAgentName, getAgentSource, hitlRequestIndex, roomQuery, lifecycle, reconcileWithDb])

  return { sseConnected, sseConnecting, sseError }
}
