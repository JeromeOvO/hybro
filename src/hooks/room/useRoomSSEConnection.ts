import { useEffect, useRef } from 'react'
import type { MutableRefObject } from 'react'
import type { SSEMessage } from '@/lib/types/sse'
import { inquiryActiveRuns } from '@/lib/api/room'
import { hydrateRoomFromDb } from '@/lib/room-sync'
import { useRoomSSE } from '../useRoomSSE'
import type { ProcessingLifecycle } from './processing-lifecycle'
import { useRoomUiStore } from '@/stores/room-ui-store'
import { useMessageStore } from '@/stores/message-store'
import { allAgentsTerminalForUserMessage } from '@/lib/room-timeline/turn-agent-terminal'
import { findProcessingStatusUserEntity } from './processing-status-log'

function hasTerminalEvidenceForCurrentTurn(roomId: string, lifecycle: ProcessingLifecycle): boolean {
  const userEntity = findProcessingStatusUserEntity(roomId, {
    messageId: lifecycle.getMessageId(),
    clientRequestId: lifecycle.getPendingRunEventAck(),
    latestWithLogs: true,
  })
  const userMessageId = lifecycle.getMessageId() ?? userEntity?.id
  if (!userMessageId) return false

  const store = useMessageStore.getState()
  if (store.roomId !== roomId) return false

  const latestUser = store.entities[userMessageId] ?? userEntity
  if (latestUser?.roomId === roomId && latestUser.messageType === 'user' && latestUser.turnTerminalStatus) {
    return true
  }

  return allAgentsTerminalForUserMessage(store.entities, roomId, userMessageId)
}

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
    const backendHasActiveLifecycle = async (): Promise<boolean | null> => {
      if (!roomId) return null
      try {
        const result = await inquiryActiveRuns(roomId, getToken)
        if (!result.success) return null
        return (result.active_runs?.length ?? 0) > 0
      } catch {
        return null
      }
    }

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
      hydrateRoomFromDb({
        roomId,
        phase: 'hitl_overlay',
        getToken,
        getAgentName,
        getAgentSource,
        hitlRequestIndex,
      }).catch((err) => {
        console.error('[HITL] Failed to restore pending requests on reconnect:', err)
      })
    }

    // Any reconnection after a gap may have missed messages. Reconcile
    // unconditionally so the UI is fresh, regardless of processing state.
    // Also check whether the backend is still active: if it isn't, clear any
    // stuck spinner even when processing=false (e.g. page refresh during a turn,
    // or terminal SSE events were sent before this reconnect was established).
    if (isReconnection) {
      console.log('🔄 SSE reconnected after gap — reconciling with DB')
      reconcileWithDb(roomId)
      // Capture the current message ID before the async check so we can detect
      // if the user starts a new turn before the response comes back (race guard).
      const messageIdAtReconnect = lifecycle.getMessageId()
      backendHasActiveLifecycle().then(async hasActive => {
        const { sending } = useRoomUiStore.getState().rooms[roomId] ?? {}
        if (hasActive === false && lifecycle.getMessageId() === messageIdAtReconnect && !sending) {
          await reconcileWithDb(roomId)
          if (lifecycle.getMessageId() !== messageIdAtReconnect) return
          if (!hasTerminalEvidenceForCurrentTurn(roomId, lifecycle)) {
            console.log('🔄 Reconnect: backend has no active runs, but DB has no terminal turn yet — keeping processing log')
            return
          }
          console.log('🔄 Reconnect: backend confirms no active runs and DB has terminal turn — stopping processing lifecycle')
          lifecycle.stopProcessing()
          lifecycle.clearSseDisconnection()
        }
      }).catch(() => { /* ignore — safety-net, not critical */ })
    }

    // Safety-net: while processing is active, periodically verify backend truth.
    // This covers both reconnect gaps and cases where terminal SSE events are
    // missed without an explicit disconnect marker.
    let safetyTimer: ReturnType<typeof setTimeout> | null = null
    if (sseConnected && processing) {
      safetyTimer = setTimeout(async () => {
        const hasActiveLifecycle = await backendHasActiveLifecycle()
        if (hasActiveLifecycle === false) {
          await reconcileWithDb(roomId)
          if (!hasTerminalEvidenceForCurrentTurn(roomId, lifecycle)) {
            console.log('🔄 Safety-net: backend has no active runs, but DB has no terminal turn yet — keeping processing log')
            return
          }
          console.log('🔄 Safety-net: backend confirms processing ended and DB has terminal turn — stopping processing lifecycle')
          lifecycle.stopProcessing()
          lifecycle.clearSseDisconnection()
        }
      }, 15_000)
    }

    prevSseConnectedRef.current = sseConnected

    return () => {
      if (safetyTimer) clearTimeout(safetyTimer)
    }
  }, [sseConnected, processing, roomId, getToken, getAgentName, getAgentSource, hitlRequestIndex, lifecycle, reconcileWithDb])

  return { sseConnected, sseConnecting, sseError }
}
