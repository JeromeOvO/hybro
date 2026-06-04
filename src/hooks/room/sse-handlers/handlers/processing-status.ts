import { banner } from '@/components/ui/banner'
import type { SSEMessage, ProcessingStatus } from '@/lib/types/sse'
import { PROCESSING_STATUS, isProcessingDone, TASK_STATE } from '@/lib/types/sse'
import { useMessageStore } from '@/stores/message-store'
import type { MessageEntity, ProcessingStatusLogEntry } from '@/stores/message-store/types'
import { getResolvedMessageId, resolveClientRequestMessageId } from '../pending-turn-buffer'
import type { CorrelationResult } from '../correlation'
import type { SSEHandlerDeps } from '../types'

function findProcessingUserEntity(
  roomId: string,
  messageId: string | null | undefined,
  clientRequestId: string | null | undefined,
  relatedMessageId?: string | null,
): MessageEntity | undefined {
  const store = useMessageStore.getState()
  if (clientRequestId) {
    const correlated = store.orderedIds
      .map((id) => store.entities[id])
      .find((entity) =>
        entity?.roomId === roomId &&
        entity.messageType === 'user' &&
        entity.clientRequestId === clientRequestId
      )
    if (correlated) return correlated
  }

  if (relatedMessageId) {
    const related = store.entities[relatedMessageId]
    if (related?.roomId === roomId && related.messageType === 'user') return related
  }

  if (!messageId) return undefined

  const direct = store.entities[messageId]
  if (direct?.roomId === roomId && direct.messageType === 'user') return direct
  return undefined
}

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

function upsertProcessingLogs(
  roomId: string,
  userEntity: MessageEntity | undefined,
  logs: ProcessingStatusLogEntry[],
): void {
  if (!userEntity) return
  useMessageStore.getState().upsertMessage({
    id: userEntity.id,
    roomId,
    messageType: 'user',
    content: userEntity.content,
    senderName: userEntity.senderName,
    timestamp: userEntity.timestamp,
    processingStatusLogs: logs,
  }, 'sse')
}

function appendProcessingLog(
  roomId: string,
  userEntity: MessageEntity | undefined,
  message: string | undefined,
  timestamp: string,
): void {
  const trimmed = message?.trim()
  if (!trimmed || !userEntity) return

  const existing = userEntity.processingStatusLogs ?? []
  if (existing.some((entry) => entry.message === trimmed)) return

  upsertProcessingLogs(roomId, userEntity, [
    ...existing,
    {
      id: `processing-log-${timestamp}-${existing.length}`,
      message: trimmed,
      timestamp,
    },
  ])
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
    const existingUserForEventId = findProcessingUserEntity(
      roomId,
      realMessageId,
      undefined,
      relatedMessageId,
    )
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
    const processingUserEntity = findProcessingUserEntity(
      roomId,
      userMsgId,
      correlation.clientReqId,
      relatedMessageId,
    )
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

    appendProcessingLog(
      roomId,
      processingUserEntity,
      sseMessage.data.details,
      sseMessage.timestamp,
    )

    lifecycle.startProcessing(processingUserEntity?.id ?? lifecycleMessageId ?? userMsgId)
    return
  }

  if (status === PROCESSING_STATUS.AWAITING_INPUT) {
    console.log('⏸️ [SSE] awaiting_input — clearing lifecycle without terminal stamp')
    const lifecycleMessageId = lifecycle.getMessageId()
    const awaitingMessageId = (sseMessage.data.message_id as string | undefined) ?? lifecycleMessageId
    const relatedMessageId = sseMessage.data.related_message_id as string | undefined
    const awaitingUser = findProcessingUserEntity(
      roomId,
      awaitingMessageId,
      correlation.clientReqId,
      relatedMessageId,
    )
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
  const terminalUser = findProcessingUserEntity(
    roomId,
    terminalUserMsgId,
    correlation.clientReqId,
    relatedMessageId,
  )
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
        banner.error(`Processing failed: ${sseMessage.data.details || 'Unknown error'}`)
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
