import { banner } from '@/components/ui/banner'
import type { SSEMessage, ProcessingStatus } from '@/lib/types/sse'
import { PROCESSING_STATUS, isProcessingDone, TASK_STATE } from '@/lib/types/sse'
import { useMessageStore } from '@/stores/message-store'
import { resolveClientRequestMessageId } from '../pending-turn-buffer'
import type { CorrelationResult } from '../correlation'
import type { SSEHandlerDeps } from '../types'

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

    const realMessageId = sseMessage.data.message_id
    if (correlation.clientReqId && realMessageId) {
      resolveClientRequestMessageId(correlation.clientReqId, realMessageId)
    }

    lifecycle.startProcessing(lifecycle.getMessageId() ?? sseMessage.data.message_id ?? undefined)
    const stageDetails = sseMessage.data.details as string | undefined

    const userMsgId = lifecycle.getMessageId() ?? (sseMessage.data.message_id as string | undefined)
    const userEntity = userMsgId ? store.entities[userMsgId] : undefined
    if (userEntity?.turnTerminalStatus) {
      console.log('[SSE PROCESSING] skipping placeholder — turn already terminal', {
        turnTerminalStatus: userEntity.turnTerminalStatus,
        userMsgId,
      })
      return
    }

    if (stageDetails) {
      lifecycle.resetPlaceholder()
    }

    if (stageDetails || !lifecycle.isPlaceholderDismissed()) {
      const defaultText = 'Processing your request\u2026'
      const placeholderId = lifecycle.placeholderId(roomId)
      const existingPlaceholder = store.entities[placeholderId]
      console.log('[SSE PROCESSING] upserting placeholder', {
        stageDetails,
        placeholderId,
        roomId,
        isDismissed: lifecycle.isPlaceholderDismissed(),
      })
      store.upsertMessage({
        id: placeholderId,
        roomId,
        messageType: 'agent',
        content: '',
        senderName: 'HYBRO AI',
        taskStatus: TASK_STATE.WORKING,
        taskContent: stageDetails || defaultText,
        timestamp: new Date().toISOString(),
        isEphemeral: true,
        clientRequestId: correlation.clientReqId ?? existingPlaceholder?.clientRequestId,
      }, 'optimistic')
    }
    return
  }

  if (!isProcessingDone(status as ProcessingStatus) && status !== PROCESSING_STATUS.RATE_LIMITED) {
    return
  }

  const terminalUserMsgId = lifecycle.getMessageId() ?? (sseMessage.data.message_id as string | undefined)

  console.log('✅ [SSE] Terminal processing_status received — clearing send guard', {
    status,
    messageId: sseMessage.data.message_id,
    clientRequestId: sseMessage.data.client_request_id,
    sendGuardBefore: lifecycle.isSendGuardActive(),
  })
  lifecycle.stopProcessing({ clearMessageId: false })
  console.log('✅ [SSE] Send guard after stopProcessing:', lifecycle.isSendGuardActive())
  ctx.setCancelling(false)
  lifecycle.disarmCancelTimeout()
  store.removeMessage(lifecycle.placeholderId(roomId))
  lifecycle.dismissPlaceholder()

  if (sseMessage.data.message_id === lifecycle.getMessageId()) {
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

  if (terminalUserMsgId) {
    const existingUserMsg = store.entities[terminalUserMsgId]
    if (existingUserMsg && !existingUserMsg.turnTerminalStatus) {
      const terminalStatus =
        status === PROCESSING_STATUS.CANCELED ? 'canceled' :
        status === PROCESSING_STATUS.FAILED ||
        status === PROCESSING_STATUS.ERROR ||
        status === PROCESSING_STATUS.REJECTED ? 'failed' : 'completed'
      store.upsertMessage({
        id: terminalUserMsgId,
        roomId,
        messageType: existingUserMsg.messageType,
        content: existingUserMsg.content,
        senderName: existingUserMsg.senderName,
        timestamp: existingUserMsg.timestamp,
        turnTerminalStatus: terminalStatus,
      }, 'sse')
    }
  }

  if (lifecycle.hadSseDisconnection()) {
    console.log('🔄 SSE had disconnection during processing — reconciling with DB')
    setTimeout(() => {
      void ctx.reconcileWithDb(roomId)
    }, 1500)
    lifecycle.clearSseDisconnection()
  } else {
    setTimeout(() => {
      void ctx.reconcileWithDb(roomId)
    }, 150)
  }
}
