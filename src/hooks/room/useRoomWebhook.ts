import { useEffect, useMemo, useRef } from 'react'
import { useRoomUiStore } from '@/stores/room-ui-store'
import { useAgentCatalog } from './useAgentCatalog'
import { useRoomData } from './useRoomData'
import { createProcessingLifecycle, type ProcessingLifecycle } from './processing-lifecycle'
import { createSSEDispatcher } from './sse-handlers'
import { useRoomReset } from './useRoomReset'
import { useRoomHydration } from './useRoomHydration'
import { useProcessingRestore } from './useProcessingRestore'
import { useRoomSSEConnection } from './useRoomSSEConnection'
import { useSendMessage } from './useSendMessage'
import { useRoomActions } from './useRoomActions'
import type { UseRoomWebhookProps } from './types'

export function useRoomWebhook({ roomId, userId, userName, getToken }: UseRoomWebhookProps) {
  const {
    sending,
    processing,
    cancelling,
    updatingRoom,
    sseEnabled,
    setSending,
    setProcessing,
    setCancelling,
    setUpdatingRoom,
    setSseEnabled,
    setSseConnected,
    setSseError,
  } = useRoomUiStore()

  const {
    availableAgents,
    allAgentsData,
    getAgentName,
    getAgentSource,
    primeAgentNameCache,
    resetAgentNameCache,
  } = useAgentCatalog(userId, getToken)

  const {
    room,
    roomQuery,
    loading,
    getDebateMode,
    getSupervisorMode,
    getAgentList,
    getRoomFormData,
    refreshRoomSetting,
  } = useRoomData(roomId, getToken, primeAgentNameCache, allAgentsData)

  // Processing lifecycle: encapsulates processing refs, send guard, placeholder, cancel timeout, SSE disconnection
  const lifecycleRef = useRef<ProcessingLifecycle | null>(null)
  if (!lifecycleRef.current) {
    lifecycleRef.current = createProcessingLifecycle(setProcessing)
  }
  const lifecycle = lifecycleRef.current

  // Clean up cancel timeout on unmount
  useEffect(() => {
    return () => { lifecycle.dispose() }
  }, [lifecycle])

  // O(1) lookup index: maps HITL request_id → message entity id
  const hitlRequestIndex = useRef(new Map<string, string>())

  // Room reset effect
  useRoomReset(roomId, lifecycle, hitlRequestIndex, resetAgentNameCache, setSending, setCancelling, setSseConnected, setSseError)

  // DB hydration
  const { reconcileWithDb } = useRoomHydration(
    roomId, userId, userName, getToken, room, hitlRequestIndex, getAgentName, getAgentSource,
  )

  // Processing restore
  useProcessingRestore(roomId, room, loading, lifecycle)

  // Handle SSE messages — delegates to pure dispatcher factory
  const handleSSEMessage = useMemo(
    () => createSSEDispatcher({
      roomId, lifecycle, getAgentName, getAgentSource, getSupervisorMode,
      reconcileWithDb, hitlRequestIndex, setCancelling,
    }),
    [roomId, lifecycle, getAgentName, getAgentSource, getSupervisorMode,
     reconcileWithDb, setCancelling]
  )

  // SSE connection
  const { sseConnected, sseConnecting, sseError } = useRoomSSEConnection(
    roomId, getToken, sseEnabled, processing, lifecycle, handleSSEMessage,
    getAgentName, getAgentSource, hitlRequestIndex, roomQuery, reconcileWithDb,
    setSseConnected, setSseError,
  )

  // Send message
  const { sendUserMessage } = useSendMessage(
    roomId, userId, userName, room, getToken, sending, sseConnected,
    lifecycle, setSending, setCancelling, reconcileWithDb,
  )

  // Room actions
  const { cancelProcessing, respondToHitlRequest, updateRoomSettings, refreshMessages, toggleSSE } = useRoomActions(
    roomId, room, getToken, lifecycle, hitlRequestIndex, roomQuery,
    getDebateMode, reconcileWithDb, setCancelling, setUpdatingRoom,
    sseEnabled, setSseEnabled,
  )

  return {
    // State
    room,
    loading,
    sending,
    processing,
    cancelling,
    updatingRoom,

    // SSE State
    sseConnected,
    sseConnecting,
    sseError,
    sseEnabled,

    // Debate Mode
    debateMode: getDebateMode(),

    // Supervisor Mode
    supervisorMode: getSupervisorMode(),

    // Actions
    sendUserMessage,
    cancelProcessing,
    respondToHitlRequest,
    updateRoomSettings,
    refreshMessages,
    refreshRoomSetting,
    getAgentList,
    getRoomFormData,
    toggleSSE,
    availableAgents,
  }
}
