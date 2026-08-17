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
  clearProcessingStatusLogs,
  ensureInitialProcessingStatusLog,
  findProcessingStatusUserEntity,
} from './processing-status-log'
import { resolveClientRequestMessageId } from './sse-handlers/pending-turn-buffer'

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

  // Respond to a HITL request — inline Q&A display pattern:
  // 1. Mark agent message resolved + embed answer  2. Optionally show processing placeholder (last in group)
  const respondToHitlRequest = useCallback(async (requestId: string, userInput: string) => {
    const entityId = hitlRequestIndex.current.get(requestId)
    const store = useMessageStore.getState()
    const entity = entityId
      ? store.entities[entityId]
      : Object.values(store.entities).find((candidate) =>
          candidate.roomId === roomId &&
          candidate.messageType === 'agent' &&
          candidate.hitlRequestId === requestId
        )
    if (!entityId && entity) {
      hitlRequestIndex.current.set(requestId, entity.id)
    }
    const processingUserEntity = findProcessingStatusUserEntity(roomId, {
      relatedMessageId: entity?.relatedMessageId,
      clientRequestId: entity?.clientRequestId,
      beforeTimestamp: entity?.timestamp,
    })

    // Determine if this is the last unanswered question in its group
    const isGrouped = entity?.hitlGroupId != null
    let isLastInGroup = true
    if (isGrouped && entity?.hitlGroupId) {
      const allEntities = Object.values(store.entities)
      const siblings = allEntities.filter(e => e.hitlGroupId === entity.hitlGroupId && e.id !== entity.id)
      const unresolvedSiblings = siblings.filter(e => !e.hitlResolved && !e.hitlUserAnswer)
      isLastInGroup = unresolvedSiblings.length === 0
    }

    // Optimistic: mark resolved and embed the user's answer inline
    if (entity) {
      store.upsertMessage({
        id: entity.id,
        roomId,
        messageType: 'agent',
        content: entity.content,
        senderName: entity.senderName,
        timestamp: entity.timestamp,
        hitlResolved: true,
        hitlUserAnswer: userInput,
      }, 'optimistic')
    }
    hitlRequestIndex.current.delete(requestId)

    // Only show processing placeholder after the LAST question in a group (or non-grouped)
    if (isLastInGroup) {
      lifecycle.resetPlaceholder()
      lifecycle.resetProcessingResolved()
      lifecycle.setPendingRunEventAck(entity?.clientRequestId ?? null)
      if (entity?.clientRequestId && processingUserEntity?.id) {
        resolveClientRequestMessageId(entity.clientRequestId, processingUserEntity.id)
      }
      store.removeMessage(lifecycle.placeholderId(roomId))
      ensureInitialProcessingStatusLog(roomId, processingUserEntity)
      appendProcessingStatusLog(
        roomId,
        processingUserEntity,
        'Processing your input...',
        new Date(Date.now() + 1).toISOString(),
      )
      lifecycle.startProcessing(processingUserEntity?.id)
    }

    try {
      const { respondToHitl } = await import('@/lib/api/hitl')
      await respondToHitl(roomId, requestId, userInput, getToken)
    } catch (err) {
      // A conflict can mean a different answer, expiry, cancellation, or an
      // in-flight claimant. Reconcile, then surface it; never assume success.
      if (err instanceof ApiError && err.status === 409) {
        await reconcileWithDb(roomId)
      }

      // AbortError (timeout) — the backend is still processing the supervisor
      // resume which can take 60-120s. Keep the optimistic state; the eventual
      // hitl_response SSE will reconcile.
      if (err instanceof Error && err.name === 'AbortError') {
        return
      }

      // Genuine failure — rollback optimistic updates so the HITL form reappears
      if (entity) {
        store.upsertMessage({
          id: entity.id,
          roomId,
          messageType: 'agent',
          content: entity.content,
          senderName: entity.senderName,
          timestamp: entity.timestamp,
          hitlResolved: false,
          hitlUserAnswer: undefined,
        }, 'optimistic')
      }
      if (entityId) {
        hitlRequestIndex.current.set(requestId, entityId)
      }
      if (isLastInGroup) {
        store.removeMessage(lifecycle.placeholderId(roomId))
        clearProcessingStatusLogs(
          roomId,
          findProcessingStatusUserEntity(roomId, {
            messageId: processingUserEntity?.id,
            clientRequestId: entity?.clientRequestId,
            latestWithLogs: true,
          }),
        )
        lifecycle.stopProcessing({ clearMessageId: false })
      }

      throw err
    }
  }, [roomId, getToken, lifecycle, hitlRequestIndex, reconcileWithDb])

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
    const { cancelHitl } = await import('@/lib/api/hitl')
    await cancelHitl(roomId, requestId, getToken)

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
        taskStatus: 'canceled',
        taskError: 'Input request canceled',
      }, 'optimistic')
      if (entity.hitlRequestId) hitlRequestIndex.current.delete(entity.hitlRequestId)
    }
  }, [getToken, hitlRequestIndex, roomId])

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
    respondToHitlRequest,
    respondToHitlBatch,
    cancelHitlRequest,
    refreshMessages,
    toggleSSE,
  }
}
