import { useEffect, useRef } from 'react'
import type { MutableRefObject } from 'react'
import type { UseQueryResult } from '@tanstack/react-query'
import type { SSEMessage } from '@/lib/types/sse'
import { fetchPendingHitlRequests } from '@/lib/api/hitl'
import { useRoomSSE } from '../useRoomSSE'
import type { ProcessingLifecycle } from './processing-lifecycle'
import { useMessageStore } from '@/stores/message-store'
import { streamingBuffer } from '@/stores/streaming-buffer'
import { typewriterManager } from './sse-handlers'
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
  // Also promote any partial streaming content to entity content.
  // On reconnect, restore any pending HITL requests.
  const prevSseConnectedRef = useRef(false)
  useEffect(() => {
    if (!sseConnected && processing) {
      console.log('⚠️ SSE disconnected during processing — will reconcile after completion')
      lifecycle.markSseDisconnection()

      // Finish any active typewriters so their content is committed
      typewriterManager.finishAll()

      // Promote partial streaming content to entity content on disconnect.
      // Use 'optimistic' source so Rule 2 (SSE wins over DB for non-terminal)
      // doesn't block DB reconciliation later. Clear task fields so
      // cancelAllNonTerminal doesn't sweep these fallback messages.
      const store = useMessageStore.getState()
      for (const [messageId, partial] of streamingBuffer.entries()) {
        if (partial) {
          console.log(`📝 Promoting partial streaming content for message ${messageId}`)
          const existing = store.entities[messageId]
          store.upsertMessage({
            id: messageId,
            roomId,
            messageType: 'agent',
            content: partial,
            senderName: existing?.senderName || 'Agent',
            agentId: existing?.agentId,
            agentSource: existing?.agentSource,
            timestamp: existing?.timestamp || new Date().toISOString(),
            isEphemeral: false,
            taskStatus: null,
          }, 'optimistic')
        }
      }
      streamingBuffer.clear()
    }

    // HITL reconnect catch-up: restore pending HITL requests after SSE reconnects
    if (sseConnected && !prevSseConnectedRef.current && roomId) {
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

    // Safety-net: if SSE reconnected after a gap during processing, the
    // terminal processing_status SSE may have been the event that was lost.
    // Schedule a deferred check against the room's persisted state. If the
    // backend already cleared processing_message_id (it writes to DB before
    // broadcasting), we know the terminal event was lost and can recover.
    let safetyTimer: ReturnType<typeof setTimeout> | null = null
    if (sseConnected && processing && lifecycle.hadSseDisconnection()) {
      safetyTimer = setTimeout(async () => {
        if (!lifecycle.hadSseDisconnection()) return
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
