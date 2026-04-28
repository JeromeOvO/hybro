import { useCallback } from 'react'
import { SendMessage } from '@/lib/api/room'
import { banner } from '@/components/ui/banner'
import type { QuoteData } from '@/lib/types/quote'
import type { MessageDispatchInput } from '@/lib/types/agent-group'
import { TASK_STATE } from '@/lib/types/sse'
import { useMessageStore } from '@/stores/message-store'
import { useTurnEventStore } from '@/stores/turn-event-store'
import type { PendingAttachment } from '@/lib/types/attachments'
import type { ProcessingLifecycle } from './processing-lifecycle'
import { clearPendingSseForClientRequest } from './sse-handlers/pending-turn-buffer'
import { useRoomUiStore } from '@/stores/room-ui-store'

export function useSendMessage(
  roomId: string,
  userId: string | undefined,
  userName: string | undefined,
  room: unknown,
  getToken: (() => Promise<string | null>) | undefined,
  sending: boolean,
  sseConnected: boolean,
  lifecycle: ProcessingLifecycle,
  setSending: (v: boolean) => void,
  setCancelling: (v: boolean) => void,
  reconcileWithDb: (roomId: string) => Promise<void>,
  onPostMessageIdResolved?: (clientRequestId: string, messageId: string) => Promise<void>,
) {
  const sendUserMessage = useCallback(async (
    userInput: string,
    targetGroup: string = "all_agents",
    quoteData?: QuoteData,
    pendingAttachments?: PendingAttachment[],
    dispatch?: MessageDispatchInput,
  ) => {
    if (!userId || !userName || !room || sending || lifecycle.isSendGuardActive()) {
      return false
    }

    const clientRequestId = crypto.randomUUID()
    const optimisticUserMessageId = `cr:${clientRequestId}`
    const currentTime = new Date().toISOString()
    useRoomUiStore.getState().setPendingTurnSkeleton(roomId, {
      text: userInput,
      attachments: pendingAttachments,
    })

    // Reset placeholder dismissed flag so SSE processing_status events
    // can manage the placeholder lifecycle. The flag will be set to true
    // when SSE dismisses the placeholder (task_submitted or terminal status).
    lifecycle.resetPlaceholder()

    // Step 0: Add optimistic user anchor + processing placeholder immediately.
    // This prevents fast SSE events from attaching to the previous turn.
    const optimisticAttachments = pendingAttachments?.map(att => ({
      fileId: att.id,
      fileUrl: att.previewUrl || undefined,
      mimeType: att.file.type,
      fileName: att.file.name,
      sizeBytes: att.file.size,
    }))
    const turnStore = useTurnEventStore.getState()
    turnStore.createOptimisticTurn(clientRequestId, {
      text: userInput,
      attachments: optimisticAttachments ?? [],
    })

    const processingPlaceholderId = lifecycle.placeholderId(roomId)
    const msgStoreSend = useMessageStore.getState()
    msgStoreSend.upsertMany([
      {
        id: optimisticUserMessageId,
        roomId,
        messageType: 'user',
        content: userInput,
        senderName: userName,
        userId,
        timestamp: currentTime,
        clientRequestId,
        attachments: optimisticAttachments,
      },
      {
        id: processingPlaceholderId,
        roomId,
        messageType: 'agent',
        content: '',
        senderName: 'HYBRO AI',
        taskStatus: TASK_STATE.WORKING,
        taskContent: 'Processing your request\u2026',
        timestamp: new Date(Date.now() + 1).toISOString(),
        isEphemeral: true,
        clientRequestId,
      },
    ], 'optimistic')

    try {
      setSending(true)  // Show spinner during message creation & parsing
      lifecycle.setSendGuard(true)

      // Upload pending attachments
      let uploadedAttachments: Array<{ file_id: string }> | undefined
      let uploadResponses: Map<string, { fileId: string; fileUrl: string; mimeType: string; fileName: string; sizeBytes: number }> | undefined
      if (pendingAttachments && pendingAttachments.length > 0) {
        const { uploadFile } = await import('@/lib/api/files')
        uploadResponses = new Map()
        const results = await Promise.all(
          pendingAttachments.map(async (att) => {
            const res = await uploadFile(att.file, roomId, getToken)
            uploadResponses!.set(att.id, {
              fileId: res.file_id,
              fileUrl: res.file_url,
              mimeType: res.mime_type,
              fileName: res.file_name,
              sizeBytes: res.size_bytes,
            })
            return { file_id: res.file_id }
          })
        )
        uploadedAttachments = results
      }

      // Step 1: Send user message to backend using unified SendMessage API
      const createResponse = await SendMessage(
        roomId, userInput, getToken, userId, userName, targetGroup,
        quoteData?.messageId ?? null,
        quoteData?.content ?? null,
        uploadedAttachments,
        dispatch,
        clientRequestId,
      )

      if (!createResponse.success) {
        throw new Error(`Failed to create user message: ${createResponse.error}`)
      }

      // Extract message_id from createResponse
      const messageId = createResponse.message_id || createResponse.message?.message_id || ""

      if (!messageId) {
        console.error('SendMessage returned no message_id; treating as failure')

        // Rollback optimistic entities and pending SSE state.
        const msgStoreNoId = useMessageStore.getState()
        msgStoreNoId.removeMessage(optimisticUserMessageId)
        msgStoreNoId.removeMessage(lifecycle.placeholderId(roomId))
        useTurnEventStore.getState().removeTurn(clientRequestId)
        clearPendingSseForClientRequest(clientRequestId)

        banner.error('Message sent but server returned no ID. Please try again.')

        // Revoke orphaned blob URLs since attachments have already been cleared
        // from the input component and are unreachable by its cleanup.
        if (pendingAttachments) {
          for (const att of pendingAttachments) {
            if (att.previewUrl) {
              try { URL.revokeObjectURL(att.previewUrl) } catch { /* ignore */ }
            }
          }
        }

        lifecycle.setProcessing(false)
        lifecycle.setMessageId(null)
        lifecycle.setSendGuard(false)
        useRoomUiStore.getState().setPendingTurnSkeleton(roomId)

        return false
      }

      // Step 2: Insert the real user message with stable messageId, then
      // remove the optimistic user anchor.
      const msgStoreSwap = useMessageStore.getState()
      msgStoreSwap.upsertMessage({
        id: messageId,
        roomId,
        messageType: 'user',
        content: userInput,
        senderName: userName,
        userId,
        timestamp: currentTime,
        clientRequestId,
        attachments: pendingAttachments?.map(att => {
          const uploaded = uploadResponses?.get(att.id)
          return {
            fileId: uploaded?.fileId || att.id,
            fileUrl: uploaded?.fileUrl || att.previewUrl || undefined,
            mimeType: uploaded?.mimeType || att.file.type,
            fileName: uploaded?.fileName || att.file.name,
            sizeBytes: uploaded?.sizeBytes || att.file.size,
          }
        }),
      }, 'optimistic')
      msgStoreSwap.replaceMessageId(optimisticUserMessageId, messageId)
      turnStore.append(clientRequestId, {
        eventId: `post_started_${clientRequestId}`,
        turnId: clientRequestId,
        seq: 1,
        ts: new Date(currentTime).getTime() || Date.now(),
        type: 'turn_started',
        userInput: {
          text: userInput,
          attachments: optimisticAttachments ?? [],
        },
        clientRequestId,
      })

      useRoomUiStore.getState().setPendingTurnSkeleton(roomId)
      if (onPostMessageIdResolved) {
        await onPostMessageIdResolved(clientRequestId, messageId)
      }

      // Blob preview URLs are no longer needed now that server URLs are in
      // the store.  Revoke them to free browser blob memory.
      if (pendingAttachments) {
        for (const att of pendingAttachments) {
          if (att.previewUrl) {
            try { URL.revokeObjectURL(att.previewUrl) } catch { /* ignore */ }
          }
        }
      }

      // Store message ID for potential cancellation
      lifecycle.setMessageId(messageId)

      // Step 3: Processing is auto-triggered by backend when sendMessage completes.
      // Only set processing state if SSE hasn't already dismissed the placeholder
      // (race condition: fast agents can complete before the HTTP response returns).
      setSending(false)
      if (!lifecycle.isPlaceholderDismissed()) {
        lifecycle.setProcessing(true)
      } else {
        // SSE already handled the full lifecycle — just make sure ref is clean
        lifecycle.setSendGuard(false)
      }
      setCancelling(false)
      lifecycle.setCancelTimedOut(false)
      lifecycle.clearSseDisconnection()
      lifecycle.disarmCancelTimeout()

      console.log('📡 Message queued for processing, waiting for agent responses via SSE...',
        lifecycle.isPlaceholderDismissed() ? '(SSE already completed during HTTP round-trip)' : '')

      if (!sseConnected) {
        console.log('⚠️ SSE not connected, processing will complete but updates may be delayed')
      }

      return true

    } catch (error) {
      console.error('Error in message workflow:', error)

      // Targeted rollback: remove only the specific optimistic messages (Gap 5)
      const msgStoreErr = useMessageStore.getState()
      msgStoreErr.removeMessage(optimisticUserMessageId)
      msgStoreErr.removeMessage(lifecycle.placeholderId(roomId))
      useTurnEventStore.getState().removeTurn(clientRequestId)
      clearPendingSseForClientRequest(clientRequestId)

      banner.error(`Failed to send message: ${error instanceof Error ? error.message : 'Unknown error'}`)

      // Revoke orphaned blob URLs since attachments have already been cleared
      // from the input component and are unreachable by its cleanup.
      if (pendingAttachments) {
        for (const att of pendingAttachments) {
          if (att.previewUrl) {
            try { URL.revokeObjectURL(att.previewUrl) } catch { /* ignore */ }
          }
        }
      }

      // Reconcile to recover any messages that might have been lost
      try {
        console.log('🔄 Reconciling messages after error to ensure sync...')
        await reconcileWithDb(roomId)
      } catch (reloadError) {
        console.error('Failed to reconcile messages after error:', reloadError)
      }

      lifecycle.setProcessing(false)
      lifecycle.setMessageId(null)
      useRoomUiStore.getState().setPendingTurnSkeleton(roomId)

      return false
    } finally {
      setSending(false)
      // NOTE: Do NOT clear send guard here on the success path.
      // It stays true until processing completes (via SSE terminal events)
      // to prevent a race window where the user could double-send between
      // lifecycle.setProcessing(true) propagating through Zustand and the next render.
    }
  }, [userId, userName, room, roomId, sending, sseConnected, getToken, setSending, lifecycle, setCancelling, reconcileWithDb, onPostMessageIdResolved])

  return { sendUserMessage }
}
