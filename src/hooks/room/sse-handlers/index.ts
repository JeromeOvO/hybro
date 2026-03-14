import { banner } from '@/components/ui/banner'
import type { SSEMessage, TaskState, ProcessingStatus } from '@/lib/types/sse'
import { isTerminalState, PROCESSING_STATUS, isProcessingDone, TASK_STATE } from '@/lib/types/sse'
import { useMessageStore } from '@/stores/message-store'
import type { ArtifactPart, ArtifactData, MessageEntity } from '@/stores/message-store/types'
import { mergeArtifacts } from '@/stores/message-store/upsert'
import { streamingBuffer } from '@/stores/streaming-buffer'
import { TypewriterManager } from '@/stores/typewriter'
import { normalizeTimestampOrNow } from '@/lib/time'
import type { SSEHandlerDeps } from './types'

const typewriterManager = new TypewriterManager()

export { typewriterManager }

function partsToArtifacts(
  rawParts: Record<string, unknown>[] | undefined,
  messageId: string,
  existing: MessageEntity | undefined,
): ArtifactData[] | undefined {
  if (!rawParts || rawParts.length === 0) return existing?.artifacts
  const nonTextParts = rawParts
    .filter((p) => (p.kind as string) !== 'text')
    .map((p) => {
      const fileData = p.file as Record<string, unknown> | undefined
      return {
        kind: ((p.kind as string) || 'text') as ArtifactPart['kind'],
        text: p.text as string | undefined,
        file: fileData ? {
          uri: (fileData.uri as string | undefined),
          bytes: (fileData.bytes as string | undefined),
          mime_type: ((fileData.mime_type || fileData.mimeType) as string | undefined),
          name: (fileData.name as string | undefined),
        } : undefined,
        data: p.data as Record<string, unknown> | undefined,
      }
    })
  if (nonTextParts.length === 0) return existing?.artifacts
  const inline: ArtifactData = {
    artifactId: `${messageId}-parts`,
    name: 'Response files',
    parts: nonTextParts,
  }
  return mergeArtifacts(existing?.artifacts, inline, false)
}

export function createSSEDispatcher(deps: SSEHandlerDeps) {
  const { roomId, lifecycle, getAgentName, getAgentSource, getSupervisorMode,
          reconcileWithDb, hitlRequestIndex, setCancelling } = deps

  return async (sseMessage: SSEMessage) => {
    console.log('🔔 Room webhook received SSE message:', sseMessage)
    const store = useMessageStore.getState()

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
          }, 'sse')
        }
        break

      case 'agent_response':
        console.log('🤖 Agent response received via SSE')
        if (sseMessage.data?.message_id) {
          const messageId = sseMessage.data.message_id
          streamingBuffer.finalize(messageId)
          typewriterManager.finish(messageId)

          if (sseMessage.data?.content !== undefined && sseMessage.data?.agent_id) {
            const agentName = await getAgentName(sseMessage.data.agent_id)
            const content = sseMessage.data.content
            const msgTimestamp = normalizeTimestampOrNow(sseMessage.timestamp)
            const existing = store.entities[messageId]
            const artifacts = partsToArtifacts(
              sseMessage.data.parts as Record<string, unknown>[] | undefined,
              messageId,
              existing,
            )

            store.upsertMessage({
              id: messageId,
              roomId,
              messageType: 'agent',
              content,
              senderName: agentName,
              agentId: sseMessage.data.agent_id,
              agentSource: getAgentSource(sseMessage.data.agent_id),
              timestamp: msgTimestamp,
              taskStatus: null,
              isEphemeral: false,
              ...(artifacts ? { artifacts } : {}),
            }, 'sse')
          }
        }
        break

      case 'agent_token': {
        const { message_id, agent_id, token } = sseMessage.data || {}
        if (!message_id || !token) break

        typewriterManager.abort(message_id)

        const existingEntity = store.entities[message_id]
        if (existingEntity && existingEntity.content && !existingEntity.isEphemeral) {
          break
        }

        if (!existingEntity || existingEntity.displayType === 'task-status') {
          const agentName = agent_id ? await getAgentName(agent_id) : (existingEntity?.senderName || 'Agent')
          store.upsertMessage({
            id: message_id,
            roomId,
            messageType: 'agent',
            content: '',
            senderName: agentName,
            timestamp: existingEntity?.timestamp || normalizeTimestampOrNow(sseMessage.timestamp),
            agentId: agent_id,
            agentSource: getAgentSource(agent_id),
            isEphemeral: true,
          }, 'sse')
        }

        streamingBuffer.append(message_id, token)
        break
      }

      case 'processing_status':
        console.log('⚙️ Processing status update:', sseMessage.data?.status)
        if (sseMessage.data?.status) {
          const status = sseMessage.data.status

          if (status === PROCESSING_STATUS.PROCESSING) {
            // Correlate via client_request_id: swap temp→real atomically.
            // Only processing_status PROCESSING carries client_request_id —
            // if the backend ever sends a user_message SSE event before this,
            // correlation would need to be added there too.
            const clientReqId = sseMessage.data.client_request_id
            const realMessageId = sseMessage.data.message_id
            if (clientReqId && realMessageId) {
              const pending = store.findByClientRequestId(clientReqId)
              if (pending && pending.id !== realMessageId && pending.id.startsWith('temp-')) {
                store.replaceMessageId(pending.id, realMessageId)
              }
            }

            lifecycle.setProcessing(true)
            if (!lifecycle.getMessageId() && sseMessage.data.message_id) {
              lifecycle.setMessageId(sseMessage.data.message_id)
            }
            if (!lifecycle.isPlaceholderDismissed()) {
              const isSupervisor = getSupervisorMode()
              store.upsertMessage({
                id: lifecycle.placeholderId(roomId),
                roomId,
                messageType: 'agent',
                content: '',
                senderName: 'HYBRO AI',
                taskStatus: TASK_STATE.WORKING,
                taskContent: isSupervisor
                  ? 'Supervisor is analyzing your request…'
                  : 'Processing your request…',
                timestamp: new Date().toISOString(),
                isEphemeral: true,
              }, 'optimistic')
            }
          } else if (isProcessingDone(status as ProcessingStatus) || status === PROCESSING_STATUS.RATE_LIMITED) {
            lifecycle.setProcessing(false)
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

            if (lifecycle.hadSseDisconnection()) {
              console.log('🔄 SSE had disconnection during processing — reconciling with DB')
              setTimeout(() => {
                reconcileWithDb(roomId)
              }, 1500)
              lifecycle.clearSseDisconnection()
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
        store.removeMessage(lifecycle.placeholderId(roomId))
        lifecycle.dismissPlaceholder()

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
            timestamp: normalizeTimestampOrNow(taskTimestamp),
            taskCreatedAt: normalizeTimestampOrNow(taskTimestamp),
          }, 'sse')
        }
        break

      case 'task_update':
        console.log('📋 Task update via SSE:', sseMessage.data)
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
            timestamp: new Date().toISOString(),
          }

          const existing = store.entities[messageId]
          const artifacts = partsToArtifacts(
            sseMessage.data.parts as Record<string, unknown>[] | undefined,
            messageId,
            existing,
          )

          if (isTerminalState(status)) {
            const hadRealStreaming = streamingBuffer.isStreaming(messageId)
            streamingBuffer.finalize(messageId)
            typewriterManager.finish(messageId)

            store.removeMessage(lifecycle.placeholderId(roomId))
            lifecycle.dismissPlaceholder()

            if (!hadRealStreaming && content && status === TASK_STATE.COMPLETED) {
              store.upsertMessage({
                ...baseMsg,
                content: '',
                isEphemeral: true,
                ...(artifacts ? { artifacts } : {}),
              }, 'sse')

              const finalContent = content
              const finalTaskFields = taskFields
              typewriterManager.start(messageId, finalContent, () => {
                streamingBuffer.finalize(messageId)
                store.upsertMessage({
                  ...baseMsg,
                  content: finalContent,
                  isEphemeral: false,
                  ...finalTaskFields,
                  ...(artifacts ? { artifacts } : {}),
                }, 'sse')
              })
            } else {
              store.upsertMessage({
                ...baseMsg,
                content,
                isEphemeral: false,
                ...taskFields,
                ...(artifacts ? { artifacts } : {}),
              }, 'sse')
            }

            lifecycle.setProcessing(false)
            setCancelling(false)
            lifecycle.disarmCancelTimeout()
            if (!lifecycle.hasCancelTimedOut()) {
              if (status === TASK_STATE.FAILED) {
                banner.error(sseMessage.data.error || 'Task failed')
              } else if (status === TASK_STATE.REJECTED) {
                banner.error(sseMessage.data.error || 'Task was rejected')
              }
            }
            lifecycle.setCancelTimedOut(false)
          } else {
            const isCurrentlyStreaming = streamingBuffer.isStreaming(messageId)
            if (!isCurrentlyStreaming) {
              store.upsertMessage({
                ...baseMsg,
                content,
                ...taskFields,
                ...(artifacts ? { artifacts } : {}),
              }, 'sse')
            }
          }
        }
        break

      case 'artifact_update': {
        if (sseMessage.data?.message_id && sseMessage.data?.artifact) {
          const { message_id, artifact, append: isAppend, last_chunk } = sseMessage.data
          const existing = store.entities[message_id]
          const artifactData = {
            artifactId: artifact.artifact_id || (artifact as Record<string, unknown>).artifactId as string,
            name: artifact.name,
            parts: (artifact.parts || []).map((p: Record<string, unknown>) => {
              const fileData = p.file as Record<string, unknown> | undefined
              return {
                kind: ((p.kind as string) || 'text') as ArtifactPart['kind'],
                text: p.text as string | undefined,
                file: fileData ? {
                  uri: (fileData.uri as string | undefined),
                  bytes: (fileData.bytes as string | undefined),
                  mime_type: (fileData.mime_type || fileData.mimeType) as string | undefined,
                  name: (fileData.name as string | undefined),
                } : undefined,
                data: p.data as Record<string, unknown> | undefined,
              }
            }),
            isStreaming: isAppend ? !last_chunk : false,
          }
          const merged = mergeArtifacts(existing?.artifacts, artifactData, isAppend)
          store.upsertMessage({
            id: message_id,
            roomId,
            messageType: 'agent',
            content: existing?.content || '',
            senderName: existing?.senderName || 'Agent',
            timestamp: existing?.timestamp || normalizeTimestampOrNow(sseMessage.timestamp),
            artifacts: merged,
          }, 'sse')
        }
        break
      }

      case 'hitl_input_requested': {
        console.log('🔔 HITL input requested via SSE:', sseMessage.data)
        if (sseMessage.data) {
          const { request_id, message_id, prompt, prompt_type, choices,
                  agent_name, agent_id, step_number, total_steps, expires_at,
                  group_id, group_total, group_index, related_message_id } = sseMessage.data
          if (request_id && message_id) {
            store.removeMessage(lifecycle.placeholderId(roomId))
            lifecycle.dismissPlaceholder()

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
              hitlGroupId: group_id ?? undefined,
              hitlGroupTotal: group_total ?? undefined,
              hitlGroupIndex: group_index ?? undefined,
              stepNumber: step_number,
              totalSteps: total_steps,
              relatedMessageId: related_message_id,
            }, 'sse')
            hitlRequestIndex.current.set(request_id, message_id)
          }
        }
        break
      }

      case 'hitl_status_update': {
        console.log('🔔 HITL status update via SSE:', sseMessage.data)
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
              }
            }
          }
        }
        break
      }

      default:
        console.log('❓ Unknown SSE message type:', sseMessage.type)
    }
  }
}
