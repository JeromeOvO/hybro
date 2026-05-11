import { banner } from '@/components/ui/banner'
import type { SSEMessage, TaskState, ProcessingStatus } from '@/lib/types/sse'
import { isTerminalState, PROCESSING_STATUS, isProcessingDone, TASK_STATE } from '@/lib/types/sse'
import { useMessageStore } from '@/stores/message-store'
import { useStreamingStore } from '@/stores/streaming-store'
import type { ArtifactPart, ArtifactData, MessageEntity } from '@/stores/message-store/types'
import { mergeArtifacts } from '@/stores/message-store/upsert'
import { normalizeTimestampOrNow } from '@/lib/time'
import { appendEvent } from '@/lib/room-timeline/event-log'
import type { SSEHandlerDeps } from './types'
import {
  enqueuePendingSseEvent,
  getResolvedMessageId,
  resolveClientRequestMessageId,
} from './pending-turn-buffer'

const ENABLE_UNCORRELATED_SSE_COMPAT_FALLBACK =
  process.env.NEXT_PUBLIC_SSE_CORRELATION_COMPAT === '1'

const TURN_CORRELATED_EVENT_TYPES = new Set<SSEMessage['type']>([
  'processing_status',
  'task_submitted',
  'task_update',
  'artifact_update',
  'hitl_input_requested',
  'hitl_status_update',
])

function partsToArtifacts(
  rawParts: Record<string, unknown>[] | undefined,
  messageId: string,
  existing: MessageEntity | undefined,
): ArtifactData[] | undefined {
  if (!rawParts || rawParts.length === 0) return existing?.artifacts
  const nonTextParts = rawParts
    .map((p) => {
      const root = (p.root ?? p) as Record<string, unknown>
      const fileData = root.file as Record<string, unknown> | undefined
      return {
        kind: ((root.kind as string) || 'text') as ArtifactPart['kind'],
        text: root.text as string | undefined,
        file: fileData ? {
          uri: (fileData.uri as string | undefined),
          bytes: (fileData.bytes as string | undefined),
          mime_type: ((fileData.mime_type || fileData.mimeType) as string | undefined),
          name: (fileData.name as string | undefined),
        } : undefined,
        data: root.data as Record<string, unknown> | undefined,
      }
    })
    .filter((part) => part.kind !== 'text' && isRenderableArtifactPart(part))
  if (nonTextParts.length === 0) return existing?.artifacts
  const inline: ArtifactData = {
    artifactId: `${messageId}-parts`,
    name: 'Response files',
    parts: nonTextParts,
  }
  return mergeArtifacts(existing?.artifacts, inline, false)
}

function isNonInformativeTextChunk(text: string | undefined, hasExistingContent = false): boolean {
  if (text === undefined || text === null) return true
  if (text === '') return true
  const t = text.trim()
  // NOTE: pure-whitespace tokens (e.g. a single " ") are meaningful
  // inter-token separators in streaming content and must NOT be treated
  // as non-informative. Only drop the recognised placeholder markers.
  //
  // IMPORTANT: only treat ".", "...", "…" as non-informative when we have
  // NO existing content yet (i.e., this is the very first chunk). Mid-stream
  // these are real sentence punctuation and must be preserved, otherwise the
  // streaming content diverges from the DB canonical text.
  if (hasExistingContent) return false
  return t === '.' || t === '...' || t === '…'
}

function isRenderableArtifactPart(part: ArtifactPart, hasExistingContent = false): boolean {
  if (part.kind === 'text') return !isNonInformativeTextChunk(part.text, hasExistingContent)
  if (part.kind === 'file') return !!(part.file?.uri || part.file?.bytes)
  if (part.kind === 'data') return !!part.data && Object.keys(part.data).length > 0
  return false
}


export function createSSEDispatcher(deps: SSEHandlerDeps) {
  const { roomId, lifecycle, getAgentName, getAgentSource,
          reconcileWithDb, hitlRequestIndex, setCancelling } = deps

  return async (sseMessage: SSEMessage) => {
    console.log('🔔 Room webhook received SSE message:', sseMessage)
    const store = useMessageStore.getState()
    const streaming = useStreamingStore.getState()

    const resolveCorrelation = (): {
      clientReqId?: string
      shouldBuffer: boolean
      shouldDrop: boolean
    } => {
      if (!TURN_CORRELATED_EVENT_TYPES.has(sseMessage.type)) {
        return { shouldBuffer: false, shouldDrop: false }
      }

      const clientReqId = sseMessage.data?.client_request_id as string | undefined
      if (clientReqId) {
        const shouldBuffer = !getResolvedMessageId(clientReqId)
        return { clientReqId, shouldBuffer, shouldDrop: false }
      }

      // Temporary rollout gate for backend drift. Keep disabled by default.
      if (ENABLE_UNCORRELATED_SSE_COMPAT_FALLBACK && lifecycle.getMessageId()) {
        console.warn(
          '[compat] Proceeding with uncorrelated SSE event without client_request_id:',
          sseMessage.type,
        )
        return { shouldBuffer: false, shouldDrop: false }
      }

      console.warn('Dropping turn-correlated SSE event without client_request_id:', sseMessage.type)
      return { shouldBuffer: false, shouldDrop: true }
    }

    switch (sseMessage.type) {
      case 'user_message':
        console.log('📨 User message received via SSE')
        if (sseMessage.data?.content) {
          store.upsertMessage({
            id: sseMessage.data.message_id || `sse-${Date.now()}`,
            roomId,
            messageType: 'user',
            content: sseMessage.data.content,
            senderName: sseMessage.data.user_id || 'User',
            userId: sseMessage.data.user_id,
            timestamp: normalizeTimestampOrNow(sseMessage.timestamp),
            clientRequestId: sseMessage.data.client_request_id || undefined,
          }, 'sse')
        }
        break

      case 'agent_response':
        console.log('🤖 Agent response received via SSE')
        if (sseMessage.data?.message_id) {
          const messageId = sseMessage.data.message_id

          // Deduplicate: the backend may send agent content via both
          // task_update and agent_response. Skip if the entity is already
          // terminal AND has renderable content. If the entity is terminal
          // but has no content, allow agent_response through — task_update
          // can arrive before agent_response with empty content, and we
          // must not permanently block the actual answer.
          const existing = store.entities[messageId]
          const existingHasContent = (existing?.content ?? '').trim().length > 0
            || (existing?.artifacts?.length ?? 0) > 0
          if (existing?.taskStatus && isTerminalState(existing.taskStatus) && existingHasContent) {
            console.log('🔄 Skipping agent_response for', messageId, '— already terminal with content')
            break
          }
          // Streamed artifact updates can already populate content/artifacts
          // for this exact message. If agent_response repeats the same payload,
          // skip to avoid rendering a duplicate final response.
          if (existing) {
            const incomingContent = (sseMessage.data.content ?? '').trim()
            const existingContent = (existing.content ?? '').trim()
            const hasExistingRenderable = existingContent.length > 0
              || (existing.artifacts?.length ?? 0) > 0
            const looksDuplicateContent = incomingContent.length === 0
              || incomingContent === existingContent
              // Skip only when incoming is empty/equal/older-shorter.
              // If incoming is longer than existing, allow it through so
              // terminal payloads can repair partial streamed text.
              || (incomingContent.length > 0 && existingContent.startsWith(incomingContent))
            const isAppendOnlyUpgrade = incomingContent.length > existingContent.length
              && incomingContent.startsWith(existingContent)
            const isDivergentRewrite = existingContent.length > 0
              && incomingContent.length > 0
              && !looksDuplicateContent
              && !isAppendOnlyUpgrade
            // Reading-stability-first:
            // - skip duplicate/older payloads
            // - skip divergent rewrites (different leading text)
            // - allow only append-only upgrades
            if (hasExistingRenderable && (looksDuplicateContent || isDivergentRewrite)) {
              const canFinalizeExisting = !existing.taskStatus || !isTerminalState(existing.taskStatus)
              // Only upsert if we're actually changing the taskStatus to terminal
              const needsStatusUpdate = existing.taskStatus !== TASK_STATE.COMPLETED
              if (canFinalizeExisting && needsStatusUpdate) {
                store.upsertMessage({
                  id: messageId,
                  roomId,
                  messageType: 'agent',
                  content: existing.content,
                  senderName: existing.senderName,
                  agentId: existing.agentId,
                  agentSource: existing.agentSource,
                  clientRequestId: existing.clientRequestId || sseMessage.data?.client_request_id,
                  timestamp: existing.timestamp,
                  taskStatus: TASK_STATE.COMPLETED,
                  taskUpdatedAt: normalizeTimestampOrNow(sseMessage.timestamp),
                  isEphemeral: false,
                  ...(existing.artifacts ? { artifacts: existing.artifacts } : {}),
                }, 'sse')
                streaming.clear(messageId)
              }
              console.log('🔄 Skipping duplicate agent_response for', messageId, '— streamed content already present')
              break
            }
          }
          const agentIdForDedup = sseMessage.data?.agent_id as string | undefined
          if (agentIdForDedup && !existing) {
            const hasDuplicate = store.orderedIds.some(id => {
              const e = store.entities[id]
              return e && e.agentId === agentIdForDedup && e.roomId === roomId
                && e.taskStatus != null && !e.isEphemeral
            })
            if (hasDuplicate) {
              console.log('🔄 Skipping duplicate agent_response for', agentIdForDedup, '— task entity exists')
              break
            }
          }

          if (sseMessage.data?.content !== undefined || sseMessage.data?.parts) {
            const agentId = sseMessage.data?.agent_id as string | undefined
            const agentName = agentId
              ? await getAgentName(agentId)
              : (sseMessage.data?.agent_name as string | undefined) || 'Agent'
            const content = sseMessage.data.content ?? ''
            const msgTimestamp = normalizeTimestampOrNow(sseMessage.timestamp)
            const existing = store.entities[messageId]
            const artifacts = partsToArtifacts(
              sseMessage.data.parts as Record<string, unknown>[] | undefined,
              messageId,
              existing,
            )
            const responseTaskStatus = existing?.taskStatus && isTerminalState(existing.taskStatus)
              ? existing.taskStatus
              : TASK_STATE.COMPLETED

            store.upsertMessage({
              id: messageId,
              roomId,
              messageType: 'agent',
              content,
              senderName: agentName,
              agentId,
              agentSource: agentId ? getAgentSource(agentId) : undefined,
              clientRequestId: existing?.clientRequestId || sseMessage.data?.client_request_id,
              timestamp: msgTimestamp,
              taskStatus: responseTaskStatus,
              taskUpdatedAt: msgTimestamp,
              isEphemeral: false,
              ...(artifacts ? { artifacts } : {}),
            }, 'sse')
            // agent_response is a terminal write — clear any live streaming buffer
            // so the render transitions from the (now stale) buffer to this entity.
            streaming.clear(messageId)
          }
        }
        break

      case 'processing_status':
        console.log('⚙️ Processing status update:', sseMessage.data?.status)
        if (sseMessage.data?.status) {
          const status = sseMessage.data.status
          const correlation = resolveCorrelation()
          if (correlation.shouldDrop) break
          if (correlation.shouldBuffer && correlation.clientReqId) {
            enqueuePendingSseEvent(correlation.clientReqId, sseMessage)
            break
          }

          if (status === PROCESSING_STATUS.PROCESSING) {
            console.log('[SSE] PROCESSING event received', { status, details: sseMessage.data.details, messageId: sseMessage.data.message_id, clientReqId: correlation.clientReqId })
            const pendingAckClientRequestId = lifecycle.getPendingRunEventAck()
            if (
              pendingAckClientRequestId
              && correlation.clientReqId
              && correlation.clientReqId !== pendingAckClientRequestId
            ) {
              // Optional UX follow-up: keep optimistic display pinned to the
              // current pending client_request_id until its first run_event ack.
              break
            }

            const realMessageId = sseMessage.data.message_id
            if (correlation.clientReqId && realMessageId) {
              resolveClientRequestMessageId(correlation.clientReqId, realMessageId)
            }

            lifecycle.startProcessing(lifecycle.getMessageId() ?? sseMessage.data.message_id ?? undefined)
            const stageDetails = sseMessage.data.details as string | undefined

            // When details are present (supervisor stage updates), always
            // re-show the placeholder — even after task_submitted dismissed
            // it — so the user sees "Planning next action...", etc.
            // Reset the dismissed flag so the selector knows the placeholder
            // is active again (the selector uses taskContent to decide whether
            // to render it alongside completed agents, but the flag keeps
            // lifecycle state consistent).
            if (stageDetails) {
              lifecycle.resetPlaceholder()
            }

            if (stageDetails || !lifecycle.isPlaceholderDismissed()) {
              const defaultText = 'Processing your request\u2026'
              console.log('[SSE PROCESSING] upserting placeholder', { stageDetails, placeholderId: lifecycle.placeholderId(roomId), roomId, isDismissed: lifecycle.isPlaceholderDismissed() })
              store.upsertMessage({
                id: lifecycle.placeholderId(roomId),
                roomId,
                messageType: 'agent',
                content: '',
                senderName: 'HYBRO AI',
                taskStatus: TASK_STATE.WORKING,
                taskContent: stageDetails || defaultText,
                timestamp: new Date().toISOString(),
                isEphemeral: true,
              }, 'optimistic')
            }
          } else if (isProcessingDone(status as ProcessingStatus) || status === PROCESSING_STATUS.RATE_LIMITED) {
            // Capture the user message ID BEFORE clearing lifecycle state below.
            // lifecycle.getMessageId() is nulled in this block so we must save it first.
            const terminalUserMsgId = lifecycle.getMessageId() ?? (sseMessage.data.message_id as string | undefined)

            lifecycle.stopProcessing({ clearMessageId: false })
            setCancelling(false)
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

            // Stamp the user entity with the room-level terminal status so the
            // useMessageStoreSync bridge can derive turn_completed/failed/canceled.
            // The turn store is derived-only — we must not write to it directly.
            if (terminalUserMsgId) {
              const existingUserMsg = store.entities[terminalUserMsgId]
              if (existingUserMsg && !existingUserMsg.turnTerminalStatus) {
                const terminalStatus =
                  status === PROCESSING_STATUS.CANCELED  ? 'canceled' :
                  status === PROCESSING_STATUS.FAILED ||
                  status === PROCESSING_STATUS.ERROR ||
                  status === PROCESSING_STATUS.REJECTED  ? 'failed'   : 'completed'
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
                reconcileWithDb(roomId)
              }, 1500)
              lifecycle.clearSseDisconnection()
            } else {
              // Even without an explicit disconnect, terminal SSE can arrive
              // before some task/turn updates (or those updates can be dropped
              // in degraded dual-write paths). A lightweight reconcile here
              // keeps live UI aligned with persisted truth without waiting
              // for a manual refresh.
              setTimeout(() => {
                reconcileWithDb(roomId)
              }, 150)
            }
          }
        }
        break

      case 'error':
        console.error('❌ SSE error message:', sseMessage.data)
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const errorData = sseMessage.data as any
        if (errorData?.error_type === 'rate_limit_exceeded') {
          const retryAfter = errorData.retry_after_seconds
          const retryMinutes = retryAfter ? Math.ceil(retryAfter / 60) : 60
          banner.error(
            errorData.error || `Rate limit exceeded. Please try again in ${retryMinutes} minutes.`,
            { duration: 15000 }
          )
        } else {
          banner.error(errorData?.error || errorData?.details || 'Unknown error')
        }
        break

      case 'heartbeat':
        console.log('💓 SSE heartbeat received')
        break

      case 'task_submitted':
        console.log('📋 Task submitted via SSE:', sseMessage.data)
        {
          const correlation = resolveCorrelation()
          if (correlation.shouldDrop) break
          if (correlation.shouldBuffer && correlation.clientReqId) {
            enqueuePendingSseEvent(correlation.clientReqId, sseMessage)
            break
          }
        }
        if (sseMessage.data?.message_id) {
          const messageId = sseMessage.data.message_id
          let resolvedAgentName = sseMessage.data.agent_name
          if (!resolvedAgentName && sseMessage.data.agent_id) {
            resolvedAgentName = await getAgentName(sseMessage.data.agent_id)
          }
          const taskTimestamp = sseMessage.data.created_at || sseMessage.timestamp

          store.upsertMessage({
            id: messageId,
            roomId,
            messageType: 'agent',
            content: '',
            senderName: resolvedAgentName || 'Agent',
            agentId: sseMessage.data.agent_id,
            agentSource: getAgentSource(sseMessage.data.agent_id),
            taskStatus: (sseMessage.data.status as TaskState) || TASK_STATE.WORKING,
            taskContent: sseMessage.data.task_content,
            stepNumber: sseMessage.data.step_number,
            totalSteps: sseMessage.data.total_steps,
            relatedMessageId: sseMessage.data.related_message_id,
            clientRequestId: sseMessage.data.client_request_id,
            timestamp: normalizeTimestampOrNow(taskTimestamp),
            taskCreatedAt: normalizeTimestampOrNow(taskTimestamp),
          }, 'sse')

          appendEvent(roomId, {
            kind: 'agent_started',
            timestamp: sseMessage.timestamp,
            agentId: sseMessage.data.agent_id,
            agentName: resolvedAgentName ?? 'Agent',
            label: `${resolvedAgentName ?? 'Agent'} started`,
          })
        }
        break

      case 'task_update':
        console.log('📋 Task update via SSE:', sseMessage.data)
        {
          const correlation = resolveCorrelation()
          if (correlation.shouldDrop) break
          if (correlation.shouldBuffer && correlation.clientReqId) {
            enqueuePendingSseEvent(correlation.clientReqId, sseMessage)
            break
          }
        }
        if (sseMessage.data?.message_id) {
          const messageId = sseMessage.data.message_id
          const status = sseMessage.data.status as TaskState
          let resolvedAgentName = sseMessage.data.agent_name
          if (!resolvedAgentName && sseMessage.data.agent_id) {
            resolvedAgentName = await getAgentName(sseMessage.data.agent_id)
          }
          const taskTimestamp = sseMessage.data.created_at || sseMessage.timestamp
          const content = sseMessage.data.content || ''

          const taskFields = {
            taskStatus: status,
            taskError: sseMessage.data.error !== undefined ? (sseMessage.data.error || null) : undefined,
            taskStatusMessage: sseMessage.data.status_message !== undefined
              ? (sseMessage.data.status_message || null) : undefined,
            taskRequiresInput: sseMessage.data.requires_input,
            taskRequiresAuth: sseMessage.data.requires_auth,
            taskContent: sseMessage.data.task_content,
            stepNumber: sseMessage.data.step_number,
            totalSteps: sseMessage.data.total_steps,
            relatedMessageId: sseMessage.data.related_message_id,
            timestamp: normalizeTimestampOrNow(taskTimestamp),
            taskCreatedAt: normalizeTimestampOrNow(taskTimestamp),
          }

          const baseMsg = {
            id: messageId,
            roomId,
            messageType: 'agent' as const,
            senderName: resolvedAgentName || 'Agent',
            agentId: sseMessage.data.agent_id,
            agentSource: getAgentSource(sseMessage.data.agent_id),
            clientRequestId: sseMessage.data.client_request_id,
            timestamp: new Date().toISOString(),
          }

          const existing = store.entities[messageId]
          // Prefer DB checkpoint content. If the checkpoint has no content (e.g.
          // a status-only task_update), fall back first to the live streaming
          // buffer (which has accumulated all chunks) and only then to whatever
          // the entity currently holds. This prevents a blank flash when
          // task_update arrives before entity content was ever written (pure
          // streaming agents where artifact_update wrote only to streamingStore).
          //
          // INVARIANT: bufferText must be read and streaming.clear() must be
          // called in the same synchronous microtask — NO await between them.
          // Any await here would open a window where a concurrent artifact_update
          // could extend the buffer after the read but before the clear, causing
          // that chunk to be permanently dropped from the entity content.
          const bufferText = useStreamingStore.getState().buffers[messageId]?.text
          const resolvedContent = (content ?? '').trim().length > 0
            ? content
            : (bufferText && bufferText.length > 0 ? bufferText : (existing?.content ?? ''))
          const artifacts = partsToArtifacts(
            sseMessage.data.parts as Record<string, unknown>[] | undefined,
            messageId,
            existing,
          )

          if (isTerminalState(status)) {
            store.removeMessage(lifecycle.placeholderId(roomId))
            lifecycle.dismissPlaceholder()

            store.upsertMessage({
              ...baseMsg,
              content: resolvedContent,
              isEphemeral: false,
              ...taskFields,
              ...(artifacts ? { artifacts } : {}),
            }, 'sse')
            // Buffer cleared — render transitions from live buffer to DB-canonical entity.
            streaming.clear(messageId)

            // Capture timeline events for terminal states
            if (status === TASK_STATE.COMPLETED) {
              appendEvent(roomId, {
                kind: 'agent_completed',
                timestamp: sseMessage.timestamp,
                agentId: sseMessage.data.agent_id,
                agentName: resolvedAgentName,
                label: `${resolvedAgentName ?? 'Agent'} completed`,
              })
            } else if (
              status === TASK_STATE.FAILED ||
              status === TASK_STATE.REJECTED ||
              status === TASK_STATE.CANCELED
            ) {
              appendEvent(roomId, {
                kind: 'agent_failed',
                timestamp: sseMessage.timestamp,
                agentId: sseMessage.data.agent_id,
                agentName: resolvedAgentName,
                label: `${resolvedAgentName ?? 'Agent'} failed`,
                body: sseMessage.data.error,
              })
            }

            // Cancel confirmation: task_update(canceled) confirms a cancel
            // succeeded — clear cancelling UI immediately.  processing_status
            // will also fire, but clearing cancelling here avoids the 15s
            // timeout firing a spurious "timed out" warning.
            if (status === TASK_STATE.CANCELED) {
              setCancelling(false)
              lifecycle.disarmCancelTimeout()
            }

            // NOTE: Do NOT clear processing here.  Individual task_update
            // terminal events mean one agent finished, but the room-level
            // processing may still be ongoing (e.g. supervisor synthesis,
            // multi-agent queue).  The authoritative signal is the
            // processing_status event — only that handler should call
            // lifecycle.stopProcessing(...).

            if (!lifecycle.hasCancelTimedOut()) {
              if (status === TASK_STATE.FAILED) {
                banner.error(sseMessage.data.error || 'Task failed')
              } else if (status === TASK_STATE.REJECTED) {
                banner.error(sseMessage.data.error || 'Task was rejected')
              }
            }
          } else {
            store.upsertMessage({
              ...baseMsg,
              content: resolvedContent,
              ...taskFields,
              ...(artifacts ? { artifacts } : {}),
            }, 'sse')
            // Clear the streaming buffer — render transitions from live buffer
            // to the DB-canonical entity content written above.
            streaming.clear(messageId)
          }
        }
        break

      case 'artifact_update': {
        const correlation = resolveCorrelation()
        if (correlation.shouldDrop) break
        if (correlation.shouldBuffer && correlation.clientReqId) {
          enqueuePendingSseEvent(correlation.clientReqId, sseMessage)
          break
        }

        if (!lifecycle.isPlaceholderDismissed()) {
          store.removeMessage(lifecycle.placeholderId(roomId))
          lifecycle.dismissPlaceholder()
        }

        if (sseMessage.data?.message_id && sseMessage.data?.artifact) {
          const { message_id, artifact, append: isAppend, last_chunk } = sseMessage.data
          const artifactData = {
            artifactId: artifact.artifact_id || (artifact as Record<string, unknown>).artifactId as string,
            name: artifact.name,
            parts: (artifact.parts || []).map((p: Record<string, unknown>) => {
              const root = (p.root ?? p) as Record<string, unknown>
              const fileData = root.file as Record<string, unknown> | undefined
              return {
                kind: ((root.kind as string) || 'text') as ArtifactPart['kind'],
                text: root.text as string | undefined,
                file: fileData ? {
                  uri: (fileData.uri as string | undefined),
                  bytes: (fileData.bytes as string | undefined),
                  mime_type: (fileData.mime_type || fileData.mimeType) as string | undefined,
                  name: (fileData.name as string | undefined),
                } : undefined,
                data: root.data as Record<string, unknown> | undefined,
              }
            }),
            isStreaming: isAppend ? !last_chunk : false,
          }

          // Streaming chunks write ONLY to streamingStore. messageStore is never
          // touched during streaming. The render layer reads streamingStore.buffers
          // for live display. task_update writes DB-canonical content to messageStore
          // and clears the buffer in one synchronous commit (no await between the
          // two — see INVARIANT comment in the task_update handler below).
          streaming.append(message_id, roomId, artifactData, isAppend ?? false)
          if (last_chunk) streaming.markComplete(message_id)

          if (!isAppend) {
            appendEvent(roomId, {
              kind: 'artifact_emitted',
              timestamp: sseMessage.timestamp,
              agentId: sseMessage.data.agent_id,
              label: `Artifact: ${artifact.name ?? 'output'}`,
            })
          }
        }
        break
      }

      case 'hitl_input_requested': {
        console.log('🔔 HITL input requested via SSE:', sseMessage.data)
        {
          const correlation = resolveCorrelation()
          if (correlation.shouldDrop) break
          if (correlation.clientReqId && sseMessage.data?.message_id) {
            // HITL request provides stable message_id immediately; resolve
            // correlation eagerly so follow-up status updates can apply.
            resolveClientRequestMessageId(correlation.clientReqId, sseMessage.data.message_id)
          }
        }
        if (sseMessage.data) {
          const { request_id, message_id, prompt, prompt_type, choices,
                  agent_name, agent_id, step_number, total_steps, expires_at,
                  group_id, group_total, group_index, related_message_id } = sseMessage.data
          if (request_id && message_id) {
            store.removeMessage(lifecycle.placeholderId(roomId))
            lifecycle.dismissPlaceholder()

            // Clean up any stale index entries pointing to this same entity.
            // When a new HITL request reuses the same message_id, the old
            // request's index entry must be removed so that a late-arriving
            // hitl_status_update for the old request can't resolve to this entity.
            for (const [oldReqId, oldEntityId] of hitlRequestIndex.current) {
              if (oldEntityId === message_id && oldReqId !== request_id) {
                hitlRequestIndex.current.delete(oldReqId)
              }
            }

            let resolvedAgentName = agent_name
            if (!resolvedAgentName && agent_id) {
              resolvedAgentName = await getAgentName(agent_id)
            }
            store.upsertMessage({
              id: message_id,
              roomId,
              messageType: 'agent',
              content: prompt || '',
              senderName: resolvedAgentName || 'Agent',
              timestamp: normalizeTimestampOrNow(sseMessage.timestamp),
              agentId: agent_id,
              agentSource: getAgentSource(agent_id),
              taskStatus: 'input-required' as TaskState,
              hitlRequestId: request_id,
              hitlPrompt: prompt,
              hitlPromptType: (prompt_type as 'text' | 'choice' | 'confirmation') || 'text',
              hitlChoices: choices,
              hitlExpiresAt: expires_at,
              hitlResolved: false,
              hitlUserAnswer: '',
              hitlGroupId: group_id ?? undefined,
              hitlGroupTotal: group_total ?? undefined,
              hitlGroupIndex: group_index ?? undefined,
              stepNumber: step_number,
              totalSteps: total_steps,
              relatedMessageId: related_message_id,
              clientRequestId: sseMessage.data.client_request_id,
            }, 'sse')
            hitlRequestIndex.current.set(request_id, message_id)

            appendEvent(roomId, {
              kind: 'hitl_requested',
              timestamp: sseMessage.timestamp,
              agentId: agent_id,
              label: 'Input requested',
              hitlPayload: { prompt: prompt ?? '' },
            })
          }
        }
        break
      }

      case 'hitl_status_update': {
        console.log('🔔 HITL status update via SSE:', sseMessage.data)
        {
          const correlation = resolveCorrelation()
          if (correlation.shouldDrop) break
        }
        if (sseMessage.data) {
          const { request_id, status: hitlStatus, error_message } = sseMessage.data
          if (request_id) {
            const entityId = hitlRequestIndex.current.get(request_id)
            const entity = entityId ? store.entities[entityId] : undefined
            if (entity) {
              if (entity.hitlRequestId && entity.hitlRequestId !== request_id) {
                console.log('🔔 Skipping stale hitl_status_update for', request_id,
                  '— entity now owns', entity.hitlRequestId)
                hitlRequestIndex.current.delete(request_id)
                break
              }

              let resolvedTaskStatus = entity.taskStatus
              let resolvedTaskError: string | null = null
              let resolvedContent = entity.content
              let resolved = true
              if (hitlStatus === 'expired') {
                resolvedTaskStatus = 'failed' as TaskState
                resolvedTaskError = error_message || 'Request expired'
                resolvedContent = error_message || entity.content
              } else if (hitlStatus === 'canceled') {
                resolvedTaskStatus = 'canceled' as TaskState
                resolvedTaskError = error_message || 'Request canceled'
                resolvedContent = error_message || entity.content
              } else if (hitlStatus === 'error') {
                resolved = false
                resolvedTaskError = error_message || 'Delivery failed — you can retry'
              }

              store.upsertMessage({
                id: entity.id,
                roomId,
                messageType: 'agent',
                content: resolvedContent,
                senderName: entity.senderName,
                timestamp: normalizeTimestampOrNow(sseMessage.timestamp),
                hitlResolved: resolved,
                taskStatus: resolvedTaskStatus,
                taskError: resolvedTaskError,
              }, 'sse')
              if (resolved) {
                hitlRequestIndex.current.delete(request_id)

                appendEvent(roomId, {
                  kind: 'hitl_answered',
                  timestamp: sseMessage.timestamp,
                  agentId: entity.agentId,
                  label: 'Input provided',
                })
              }
            }
          }
        }
        break
      }

      case 'turn_event': {
        // Strict single-writer mode for block UI:
        // turn-event-store is derived from normalized message-store only.
        // Ignore direct turn_event writes to prevent dual-source divergence.
        console.log('ℹ️ Ignoring turn_event SSE in single-writer mode')
        break
      }

      case 'run_event': {
        const correlationId = sseMessage.data?.correlation_id
        if (typeof correlationId === 'string' && correlationId.length > 0) {
          const pendingAckClientRequestId = lifecycle.getPendingRunEventAck()
          if (pendingAckClientRequestId && pendingAckClientRequestId === correlationId) {
            lifecycle.clearPendingRunEventAck()
          }
        }

        const sub = sseMessage.data?.type as string | undefined
        if (
          sub === 'run_failed'
          || sub === 'run_completed'
          || sub === 'run_canceled'
        ) {
          void reconcileWithDb(roomId)
        }
        break
      }

      default:
        console.log('❓ Unknown SSE message type:', sseMessage.type)
    }
  }
}
