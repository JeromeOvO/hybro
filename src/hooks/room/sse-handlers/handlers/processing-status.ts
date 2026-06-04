import { banner } from '@/components/ui/banner'
import type { SSEMessage, ProcessingStatus } from '@/lib/types/sse'
import { PROCESSING_STATUS, isProcessingDone, TASK_STATE } from '@/lib/types/sse'
import { useMessageStore } from '@/stores/message-store'
import type { MessageEntity } from '@/stores/message-store/types'
import {
  appendProcessingStatusLog,
  findProcessingStatusUserEntity,
  normalizeProcessingDetails,
} from '../../processing-status-log'
import { getResolvedMessageId, resolveClientRequestMessageId } from '../pending-turn-buffer'
import type { CorrelationResult } from '../correlation'
import type { SSEHandlerDeps } from '../types'

function isTurnLevelTerminalProcessingStatus(
  sseMessageId: string | undefined,
  relatedMessageId: string | undefined,
  lifecycleMessageId: string | null,
  resolvedMessageId: string | undefined,
  userEntity: MessageEntity | undefined,
): boolean {
  if (!sseMessageId) return true
  if (relatedMessageId && userEntity?.id === relatedMessageId) return true
  if (relatedMessageId && lifecycleMessageId && relatedMessageId === lifecycleMessageId) return true
  if (relatedMessageId && resolvedMessageId && relatedMessageId === resolvedMessageId) return true
  if (lifecycleMessageId && sseMessageId === lifecycleMessageId) return true
  if (resolvedMessageId && sseMessageId === resolvedMessageId) return true
  if (userEntity?.id === sseMessageId) return true
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

export function handleProcessingStatus(
  ctx: SSEHandlerDeps,
  sseMessage: SSEMessage,
  correlation: CorrelationResult,
): void {
  console.log('⚙️ Processing status update:', sseMessage.data?.status, {
    client_request_id: sseMessage.data?.client_request_id,
  })
  if (!sseMessage.data?.status) return

  const status = sseMessage.data.status
  const store = useMessageStore.getState()
  const { roomId, lifecycle } = ctx

  if (status === PROCESSING_STATUS.PROCESSING) {
    console.log('[SSE] PROCESSING event received', {
      status,
      details: sseMessage.data.details,
      messageId: sseMessage.data.message_id,
      clientReqId: correlation.clientReqId,
    })
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
    const relatedMessageId = sseMessage.data.related_message_id as string | undefined
    const existingUserForEventId = findProcessingStatusUserEntity(roomId, {
      messageId: realMessageId,
      relatedMessageId,
    })
    if (correlation.clientReqId && existingUserForEventId) {
      resolveClientRequestMessageId(correlation.clientReqId, existingUserForEventId.id)
    }

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
      console.log('[SSE PROCESSING] skipping placeholder/log — turn already terminal', {
        turnTerminalStatus: processingUserEntity.turnTerminalStatus,
        userMsgId: processingUserEntity.id,
      })
      return
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
      sseMessage.data.details,
      sseMessage.timestamp,
      'sse',
    )

    lifecycle.startProcessing(processingUserEntity?.id ?? lifecycleMessageId ?? userMsgId)
    return
  }

  if (status === PROCESSING_STATUS.AWAITING_INPUT) {
    console.log('⏸️ [SSE] awaiting_input — clearing lifecycle without terminal stamp')
    const lifecycleMessageId = lifecycle.getMessageId()
    const awaitingMessageId = (sseMessage.data.message_id as string | undefined) ?? lifecycleMessageId
    const relatedMessageId = sseMessage.data.related_message_id as string | undefined
    const awaitingUser = findProcessingStatusUserEntity(roomId, {
      messageId: awaitingMessageId,
      clientRequestId: correlation.clientReqId,
      relatedMessageId,
      preferClientRequestId: true,
    })
    if (!isCurrentProcessingUser(
      roomId,
      lifecycleMessageId,
      awaitingUser,
      lifecycle,
      correlation.clientReqId,
      relatedMessageId,
    )) {
      return
    }
    lifecycle.markProcessingResolved()
    lifecycle.stopProcessing({ clearMessageId: false })
    ctx.setCancelling(false)
    lifecycle.disarmCancelTimeout()
    store.removeMessage(lifecycle.placeholderId(roomId))
    lifecycle.dismissPlaceholder()
    return
  }

  if (!isProcessingDone(status as ProcessingStatus) && status !== PROCESSING_STATUS.RATE_LIMITED) {
    return
  }

  const lifecycleMessageId = lifecycle.getMessageId()
  const sseMessageId = sseMessage.data.message_id as string | undefined
  const relatedMessageId = sseMessage.data.related_message_id as string | undefined
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
      relatedMessageId,
      lifecycleMessageId,
      resolvedClientMessageId,
      terminalUser,
    )
  ) {
    console.log('🚫 [SSE] Ignoring per-agent processing_status — terminal id is not the user message', {
      status,
      sseMessageId,
      lifecycleMessageId,
      resolvedClientMessageId,
      terminalUserMsgId: terminalUser?.id ?? terminalUserMsgId,
    })
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

  console.log('✅ [SSE] Terminal processing_status received — clearing send guard', {
    status,
    messageId: sseMessage.data.message_id,
    clientRequestId: sseMessage.data.client_request_id,
    sendGuardBefore: lifecycle.isSendGuardActive(),
  })
  if (isCurrentLifecycleTerminal) {
    lifecycle.markProcessingResolved()
    lifecycle.stopProcessing({ clearMessageId: false })
    console.log('✅ [SSE] Send guard after stopProcessing:', lifecycle.isSendGuardActive())
    ctx.setCancelling(false)
    lifecycle.disarmCancelTimeout()
    store.removeMessage(lifecycle.placeholderId(roomId))
    lifecycle.dismissPlaceholder()

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
        banner.error(`Processing failed: ${normalizeProcessingDetails(sseMessage.data.details) ?? 'Unknown error'}`)
      } else if (status === PROCESSING_STATUS.RATE_LIMITED) {
        console.log('Rate limit reached, processing stopped')
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
      store.upsertMessage({
        id: resolvedTerminalUserMsgId,
        roomId,
        messageType: existingUserMsg.messageType,
        content: existingUserMsg.content,
        senderName: existingUserMsg.senderName,
        timestamp: existingUserMsg.timestamp,
        turnTerminalStatus: terminalStatus,
      }, 'sse')
    }
  }

  if (isCurrentLifecycleTerminal) {
    if (lifecycle.hadSseDisconnection()) {
      console.log('🔄 SSE had disconnection during processing — reconciling with DB')
      scheduleTerminalReconcile(ctx, roomId, lifecycle, correlation.clientReqId, 1500)
      lifecycle.clearSseDisconnection()
    } else {
      scheduleTerminalReconcile(ctx, roomId, lifecycle, correlation.clientReqId, 150)
    }
  }
}
