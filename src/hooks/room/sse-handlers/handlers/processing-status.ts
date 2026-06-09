import { banner } from '@/components/ui/banner'
import type { ProcessingStatus, ProcessingStatusData, RoomSSEFrameMap } from '@/lib/types/sse'
import { PROCESSING_STATUS, isProcessingDone, TASK_STATE } from '@/lib/types/sse'
import { useMessageStore } from '@/stores/message-store'
import type { MessageEntity } from '@/stores/message-store/types'
import {
  appendProcessingStatusLog,
  findProcessingStatusUserEntity,
  processingDetailsToLogMessage,
} from '../../processing-status-log'
import { getResolvedMessageId } from '../pending-turn-buffer'
import { applyRoomCommands } from '../apply-commands'
import type { CorrelationResult } from '../correlation'
import type { SSEHandlerDeps } from '../types'

function isTurnLevelTerminalProcessingStatus(
  sseMessageId: string | undefined,
  lifecycleMessageId: string | null,
  resolvedMessageId: string | undefined,
  userEntity: MessageEntity | undefined,
  relatedMessageId: string | null | undefined,
  clientRequestId: string | null | undefined,
): boolean {
  if (!sseMessageId) return true
  if (lifecycleMessageId && sseMessageId === lifecycleMessageId) return true
  if (resolvedMessageId && sseMessageId === resolvedMessageId) return true
  if (userEntity?.id === sseMessageId) return true
  // HITL resume can introduce a new backend request id while pointing back to the original user turn.
  if (
    relatedMessageId &&
    userEntity?.id === relatedMessageId &&
    resolvedMessageId === userEntity.id &&
    userEntity.clientRequestId !== clientRequestId
  ) {
    return true
  }
  return false
}

function isCurrentProcessingUser(
  roomId: string,
  lifecycleMessageId: string | null,
  userEntity: MessageEntity | undefined,
  lifecycle: SSEHandlerDeps['lifecycle'],
  clientRequestId: string | null | undefined,
  relatedMessageId?: string | null,
): boolean {
  if (lifecycleMessageId && relatedMessageId === lifecycleMessageId) return true
  if (lifecycleMessageId && userEntity?.id === lifecycleMessageId) return true

  const activeClientRequestId = lifecycle.getPendingRunEventAck()
  if (activeClientRequestId && clientRequestId) {
    return activeClientRequestId === clientRequestId
  }

  if (activeClientRequestId && userEntity?.clientRequestId) {
    return activeClientRequestId === userEntity.clientRequestId
  }

  const placeholderClientRequestId =
    useMessageStore.getState().entities[lifecycle.placeholderId(roomId)]?.clientRequestId
  if (placeholderClientRequestId && clientRequestId) {
    return placeholderClientRequestId === clientRequestId
  }

  if (placeholderClientRequestId && userEntity?.clientRequestId) {
    return placeholderClientRequestId === userEntity.clientRequestId
  }

  if (lifecycleMessageId) return false

  return true
}

function scheduleTerminalReconcile(
  ctx: SSEHandlerDeps,
  roomId: string,
  lifecycle: SSEHandlerDeps['lifecycle'],
  clientRequestId: string | null | undefined,
  delayMs: number,
): void {
  setTimeout(() => {
    const placeholderClientRequestId =
      useMessageStore.getState().entities[lifecycle.placeholderId(roomId)]?.clientRequestId
    if (
      placeholderClientRequestId &&
      clientRequestId &&
      placeholderClientRequestId !== clientRequestId
    ) {
      return
    }
    void ctx.reconcileWithDb(roomId)
  }, delayMs)
}

const PROCESSING_STATUS_VALUES = new Set<string>(Object.values(PROCESSING_STATUS))

function hasValidProcessingDetails(
  details: unknown,
): details is Record<string, unknown> | null {
  return details === null || (typeof details === 'object' && !Array.isArray(details))
}

function isProcessingStatusData(data: unknown): data is ProcessingStatusData {
  if (!data || typeof data !== 'object') return false
  const value = data as Record<string, unknown>
  if (!Object.prototype.hasOwnProperty.call(value, 'message_id')) return false
  if (typeof value.message_id !== 'string' || value.message_id.length === 0) return false
  if (typeof value.client_request_id !== 'string' || value.client_request_id.length === 0) return false
  if (typeof value.status !== 'string' || !PROCESSING_STATUS_VALUES.has(value.status)) return false
  if (!Object.prototype.hasOwnProperty.call(value, 'details')) return false
  return hasValidProcessingDetails(value.details)
}

export function handleProcessingStatus(
  ctx: SSEHandlerDeps,
  sseMessage: RoomSSEFrameMap['processing_status'],
  correlation: CorrelationResult,
): void {
  if (!isProcessingStatusData(sseMessage.data)) {
    const details = sseMessage.data && typeof sseMessage.data === 'object'
      ? (sseMessage.data as Record<string, unknown>).details
      : undefined
    const message = details === undefined
      ? 'Ignoring processing_status without required object/null details:'
      : 'Ignoring invalid processing_status data:'
    console.debug(message, details ?? sseMessage.data)
    return
  }

  const status = sseMessage.data.status
  const store = useMessageStore.getState()
  const { roomId, lifecycle } = ctx

  if (
    status === PROCESSING_STATUS.QUEUED
    || status === PROCESSING_STATUS.PROCESSING
    || status === PROCESSING_STATUS.AWAITING_INPUT
  ) {
    const pendingAckClientRequestId = lifecycle.getPendingRunEventAck()
    if (
      pendingAckClientRequestId
      && correlation.clientReqId
      && correlation.clientReqId !== pendingAckClientRequestId
    ) {
      return
    }

    const lifecycleMessageId = lifecycle.getMessageId()
    const realMessageId = sseMessage.data.message_id as string | undefined
    const relatedMessageId = (sseMessage.data as { related_message_id?: string | null }).related_message_id ?? undefined
    const existingUserForEventId = findProcessingStatusUserEntity(roomId, {
      messageId: realMessageId,
      relatedMessageId,
    })

    const resolvedClientMessageId = correlation.clientReqId
      ? getResolvedMessageId(correlation.clientReqId)
      : undefined
    const userMsgId =
      resolvedClientMessageId ??
      relatedMessageId ??
      (sseMessage.data.message_id as string | undefined) ??
      lifecycleMessageId
    const processingUserEntity = findProcessingStatusUserEntity(roomId, {
      messageId: userMsgId,
      clientRequestId: correlation.clientReqId,
      relatedMessageId,
      preferClientRequestId: true,
    })
    if (processingUserEntity?.turnTerminalStatus) {
      const stageMessage = processingDetailsToLogMessage(sseMessage.data.details)?.toLowerCase() ?? ''
      const isOrchestrationStage =
        stageMessage.includes('synthesiz')
        || stageMessage.includes('evaluat')
        || stageMessage.includes('planning')
        || stageMessage.includes('delegat')
      const holdForSynthesis =
        processingUserEntity.turnCompletionKind === 'synthesis'
        || isOrchestrationStage
      if (!holdForSynthesis) {
        return
      }
    }

    if (!isCurrentProcessingUser(
      roomId,
      lifecycleMessageId,
      processingUserEntity,
      lifecycle,
      correlation.clientReqId,
      relatedMessageId,
    )) {
      return
    }

    appendProcessingStatusLog(
      roomId,
      processingUserEntity,
      processingDetailsToLogMessage(sseMessage.data.details),
      sseMessage.timestamp,
      'sse',
    )

    lifecycle.startProcessing(processingUserEntity?.id ?? lifecycleMessageId ?? userMsgId)
    return
  }

  if (!isProcessingDone(status as ProcessingStatus) && status !== PROCESSING_STATUS.RATE_LIMITED) {
    return
  }

  const lifecycleMessageId = lifecycle.getMessageId()
  const sseMessageId = sseMessage.data.message_id as string | undefined
  const relatedMessageId = (sseMessage.data as { related_message_id?: string | null }).related_message_id ?? undefined
  const resolvedClientMessageId = correlation.clientReqId
    ? getResolvedMessageId(correlation.clientReqId)
    : undefined
  const terminalUserMsgId = resolvedClientMessageId ?? relatedMessageId ?? sseMessageId ?? lifecycleMessageId
  const terminalUser = findProcessingStatusUserEntity(roomId, {
    messageId: terminalUserMsgId,
    clientRequestId: correlation.clientReqId,
    relatedMessageId,
    preferClientRequestId: true,
  })
  const hasTurnMessageReference =
    !!lifecycleMessageId || !!resolvedClientMessageId || !!terminalUser

  if (
    hasTurnMessageReference &&
    !isTurnLevelTerminalProcessingStatus(
      sseMessageId,
      lifecycleMessageId,
      resolvedClientMessageId,
      terminalUser,
      relatedMessageId,
      correlation.clientReqId,
    )
  ) {
    return
  }

  const resolvedTerminalUserMsgId = terminalUser?.id ?? terminalUserMsgId
  const isCurrentLifecycleTerminal = isCurrentProcessingUser(
    roomId,
    lifecycleMessageId,
    terminalUser,
    lifecycle,
    correlation.clientReqId,
    relatedMessageId,
  )

  if (isCurrentLifecycleTerminal) {
    lifecycle.markProcessingResolved()
    lifecycle.stopProcessing({ clearMessageId: false })
    ctx.setCancelling(false)
    lifecycle.disarmCancelTimeout()
    store.removeMessage(lifecycle.placeholderId(roomId))
    lifecycle.dismissPlaceholder()

    const turnClientRequestId = correlation.clientReqId ?? sseMessage.data.client_request_id
    if (turnClientRequestId) {
      applyRoomCommands([
        { type: 'stream_clear_client_request', clientRequestId: turnClientRequestId },
      ])
    }

    if (sseMessage.data.message_id === lifecycleMessageId) {
      lifecycle.setMessageId(null)
    }
    if (!lifecycle.hasCancelTimedOut()) {
      if (status === PROCESSING_STATUS.CANCELED) {
        banner.info('Processing stopped by user')
        store.upsertMessage({
          id: `cancel-confirm-${Date.now()}`,
          roomId,
          messageType: 'agent',
          content: 'Processing was stopped by the user.',
          senderName: 'System',
          taskStatus: TASK_STATE.CANCELED,
          taskContent: 'Processing stopped by user',
          timestamp: new Date().toISOString(),
          isEphemeral: true,
        }, 'optimistic')
        store.cancelAllNonTerminal(roomId)
      } else if (status === PROCESSING_STATUS.FAILED) {
        banner.error(`Processing failed: ${processingDetailsToLogMessage(sseMessage.data.details) ?? 'Unknown error'}`)
      } else if (status === PROCESSING_STATUS.RATE_LIMITED) {
        // rate limit terminal — banner handled elsewhere if needed
      }
    }
    lifecycle.setCancelTimedOut(false)
  }

  if (resolvedTerminalUserMsgId) {
    const existingUserMsg = store.entities[resolvedTerminalUserMsgId]
    if (existingUserMsg && !existingUserMsg.turnTerminalStatus) {
      const terminalStatus =
        status === PROCESSING_STATUS.CANCELED ? 'canceled' :
        status === PROCESSING_STATUS.FAILED ||
        status === PROCESSING_STATUS.ERROR ||
        status === PROCESSING_STATUS.REJECTED ||
        status === PROCESSING_STATUS.RATE_LIMITED ? 'failed' : 'completed'
      const rawKind = sseMessage.data.details?.turn_completion_kind
      const turnCompletionKind: 'synthesis' | 'deterministic' | undefined =
        rawKind === 'synthesis' || rawKind === 'deterministic' ? rawKind : undefined
      store.upsertMessage({
        id: resolvedTerminalUserMsgId,
        roomId,
        messageType: existingUserMsg.messageType,
        content: existingUserMsg.content,
        senderName: existingUserMsg.senderName,
        timestamp: existingUserMsg.timestamp,
        turnTerminalStatus: terminalStatus,
        turnCompletionKind,
      }, 'sse')
    }
  }

  if (isCurrentLifecycleTerminal) {
    if (lifecycle.hadSseDisconnection()) {
      scheduleTerminalReconcile(ctx, roomId, lifecycle, correlation.clientReqId, 1500)
      lifecycle.clearSseDisconnection()
    } else {
      scheduleTerminalReconcile(ctx, roomId, lifecycle, correlation.clientReqId, 150)
    }
  }
}
