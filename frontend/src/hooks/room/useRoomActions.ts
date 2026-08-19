import { useCallback } from 'react'
import type { MutableRefObject } from 'react'
import type { UseQueryResult } from '@tanstack/react-query'
import { cancelMessage } from '@/lib/api/sse'
import { updateRoomAgentSet, updateRoomName } from '@/lib/api/room'
import { ApiError } from '@/lib/api-client'
import { banner } from '@/components/ui/banner'
import { useRoomUiStore } from '@/stores/room-ui-store'
import { useMessageStore } from '@/stores/message-store'
import type { ProcessingLifecycle } from './processing-lifecycle'
import {
  appendProcessingStatusLog,
  ensureInitialProcessingStatusLog,
  findProcessingStatusUserEntity,
} from './processing-status-log'

export function useRoomActions(
  roomId: string,
  room: { room_name?: string; room_agent_set?: Record<string, string> | null; extend_info?: unknown } | null,
  getToken: (() => Promise<string | null>) | undefined,
  lifecycle: ProcessingLifecycle,
  hitlRequestIndex: MutableRefObject<Map<string, string>>,
  roomQuery: UseQueryResult<unknown, Error>,
  reconcileWithDb: (roomId: string) => Promise<void>,
  setCancelling: (v: boolean) => void,
  setUpdatingRoom: (v: boolean) => void,
  sseEnabled: boolean,
  setSseEnabled: (v: boolean) => void,
) {
  // Update room name and membership. Execution mode is request-scoped.
  const updateRoomSettings = useCallback(async (
    roomName: string,
    membershipAgentIds: string[],
  ) => {
    if (!room) {
      banner.error('Room data not available')
      return false
    }

    try {
      setUpdatingRoom(true)

      // Update room name if changed
      if (roomName !== room.room_name) {
        const nameResponse = await updateRoomName(roomId, roomName)
        if (!nameResponse.success) {
          throw new Error(`Failed to update room name: ${nameResponse.error}`)
        }
      }

      // Only update membership when the set actually changed.
      // This avoids backend rejection of deleted stale members when the user
      // only changed the room name.
      const currentAgentIds = new Set(Object.keys(room.room_agent_set || {}))
      const newAgentIds = new Set(membershipAgentIds)
      const membershipChanged = currentAgentIds.size !== newAgentIds.size
        || [...currentAgentIds].some(id => !newAgentIds.has(id))

      if (membershipChanged) {
        const agentResponse = await updateRoomAgentSet(
          roomId, {}, getToken,
          { membership_seed_input: "manual", room_agent_ids: membershipAgentIds },
        )
        if (!agentResponse.success) {
          const errMsg = agentResponse.error || 'Unknown error'
          if (errMsg.includes('Unknown or deleted agent')) {
            banner.error('Some agents have been deleted. Remove them before saving membership changes.')
            return false
          }
          throw new Error(`Failed to update room agents: ${errMsg}`)
        }
      }

      // Reload room settings to get updated data from backend
      await roomQuery.refetch()

      banner.success('Room settings updated successfully')
      return true

    } catch (error) {
      console.error('Error updating room settings:', error)
      banner.error(`Failed to update room settings: ${error instanceof Error ? error.message : 'Unknown error'}`)
      return false
    } finally {
      setUpdatingRoom(false)
    }
  }, [room, roomId, roomQuery, setUpdatingRoom, getToken])

  // Cancel ongoing message processing
  const cancelProcessing = useCallback(async () => {
    const messageId = lifecycle.getMessageId()
    if (!messageId) {
      banner.warning('Unable to cancel — no active task found')
      return false
    }

    try {
      setCancelling(true)
      lifecycle.setCancelTimedOut(false)
      const cancellation = await cancelMessage(messageId, getToken)

      // Batch cancel all non-terminal tasks in the normalized store
      useMessageStore.getState().cancelAllNonTerminal(roomId)

      if (cancellation.status) {
        const store = useMessageStore.getState()
        const userMessage = store.entities[messageId]
        if (userMessage?.messageType === 'user') {
          store.upsertMessage({
            id: userMessage.id,
            roomId,
            messageType: 'user',
            content: userMessage.content,
            senderName: userMessage.senderName,
            timestamp: userMessage.timestamp,
            turnTerminalStatus: cancellation.status,
          }, 'sse')
        }
        lifecycle.markProcessingResolved()
        lifecycle.stopProcessing()
        lifecycle.disarmCancelTimeout()
        setCancelling(false)
        store.removeMessage(lifecycle.placeholderId(roomId))
        await reconcileWithDb(roomId)
        return true
      }

      // Start cancellation timeout safety net (Gap 11)
      lifecycle.armCancelTimeout(() => {
        const cancelling = useRoomUiStore.getState().getRoomFlags(roomId).cancelling
        if (cancelling) {
          lifecycle.setCancelTimedOut(true)
          setCancelling(false)
          lifecycle.stopProcessing({ clearMessageId: false })
          banner.warning('Cancellation timed out — the agent may still be running')
        }
      })

      return true
    } catch (error) {
      console.error('Error cancelling message:', error)
      setCancelling(false)
      banner.error(`Failed to stop processing: ${error instanceof Error ? error.message : 'Unknown error'}`)
      return false
    }
  }, [getToken, setCancelling, lifecycle, roomId, reconcileWithDb])

  const respondToHitlBatch = useCallback(async (
    interactionId: string,
    answers: Array<{ requestId: string; answer: string }>,
    clientRequestId?: string,
  ) => {
    const answerById = new Map(answers.map(answer => [answer.requestId, answer.answer]))
    const store = useMessageStore.getState()
    const entities = Object.values(store.entities).filter(entity =>
      entity.roomId === roomId
      && entity.hitlRequestId
      && answerById.has(entity.hitlRequestId)
      && (entity.hitlInteractionId ?? entity.hitlGroupId ?? entity.hitlRequestId) === interactionId
    )

    const { respondToHitlBatch: submitBatch } = await import('@/lib/api/hitl')
    let response
    try {
      response = await submitBatch(
        roomId,
        interactionId,
        answers,
        clientRequestId,
        getToken,
      )
    } catch (error) {
      if (error instanceof ApiError && (error.status === 409 || error.status === 410)) {
        await reconcileWithDb(roomId)
      }
      throw error
    }

    const applied = response.status === 'applied' || response.status === 'responded'
    for (const entity of entities) {
      const requestId = entity.hitlRequestId
      if (!requestId) continue
      store.upsertMessage({
        id: entity.id,
        roomId,
        messageType: 'agent',
        content: entity.content,
        senderName: entity.senderName,
        timestamp: entity.timestamp,
        hitlResolved: applied,
        hitlUserAnswer: answerById.get(requestId),
        hitlInteractionStatus: applied ? 'applied' : 'applying',
        hitlApplicationStatus: applied ? 'applied' : 'applying',
      }, 'optimistic')
      if (applied) hitlRequestIndex.current.delete(requestId)
    }

    if (applied) {
      const first = entities[0]
      lifecycle.resetPlaceholder()
      lifecycle.resetProcessingResolved()
      lifecycle.setPendingRunEventAck(clientRequestId ?? first?.clientRequestId ?? null)
      const processingUserEntity = findProcessingStatusUserEntity(roomId, {
        relatedMessageId: first?.relatedMessageId,
        clientRequestId: clientRequestId ?? first?.clientRequestId,
        beforeTimestamp: first?.timestamp,
      })
      store.removeMessage(lifecycle.placeholderId(roomId))
      ensureInitialProcessingStatusLog(roomId, processingUserEntity)
      appendProcessingStatusLog(
        roomId,
        processingUserEntity,
        'Applying your answers…',
        new Date(Date.now() + 1).toISOString(),
      )
      lifecycle.startProcessing(processingUserEntity?.id)
    }
  }, [getToken, hitlRequestIndex, lifecycle, reconcileWithDb, roomId])

  const cancelHitlRequest = useCallback(async (requestId: string) => {
    const store = useMessageStore.getState()
    const target = Object.values(store.entities).find(entity =>
      entity.roomId === roomId && entity.hitlRequestId === requestId
    )
    const interactionId = target
      ? (target.hitlInteractionId ?? target.hitlGroupId ?? target.hitlRequestId)
      : undefined
    if (!interactionId || !target?.hitlInteractionVersion) {
      throw new Error('The interaction changed before it could be canceled.')
    }
    const { cancelHitl } = await import('@/lib/api/hitl')
    let result
    try {
      result = await cancelHitl(
        roomId,
        interactionId,
        target.hitlInteractionVersion,
        target.clientRequestId ?? crypto.randomUUID(),
        getToken,
      )
    } catch (error) {
      if (error instanceof ApiError && (error.status === 404 || error.status === 409 || error.status === 410)) {
        await reconcileWithDb(roomId)
      }
      throw error
    }

    for (const entity of Object.values(store.entities)) {
      const entityInteractionId = entity.hitlInteractionId ?? entity.hitlGroupId ?? entity.hitlRequestId
      if (entity.roomId !== roomId || entityInteractionId !== interactionId) continue
      store.upsertMessage({
        id: entity.id,
        roomId,
        messageType: 'agent',
        content: entity.content,
        senderName: entity.senderName,
        timestamp: entity.timestamp,
        hitlResolved: true,
        hitlInteractionStatus: 'canceled',
        hitlInteractionVersion: result.interaction_version,
        taskStatus: 'canceled',
        taskError: 'Input request canceled',
      }, 'optimistic')
      if (entity.hitlRequestId) hitlRequestIndex.current.delete(entity.hitlRequestId)
    }
  }, [getToken, hitlRequestIndex, reconcileWithDb, roomId])

  // Manually refresh messages — delegates to reconcileWithDb (Gap 14)
  const refreshMessages = useCallback(async () => {
    await reconcileWithDb(roomId)
  }, [roomId, reconcileWithDb])

  // Toggle SSE connection
  const toggleSSE = useCallback(() => {
    setSseEnabled(!sseEnabled)
  }, [setSseEnabled, sseEnabled])

  return {
    updateRoomSettings,
    cancelProcessing,
    respondToHitlBatch,
    cancelHitlRequest,
    refreshMessages,
    toggleSSE,
  }
}
