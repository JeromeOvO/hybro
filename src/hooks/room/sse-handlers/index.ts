import { banner } from '@/components/ui/banner'
import type { SSEMessage, TaskState, ProcessingStatus } from '@/lib/types/sse'
import { isTerminalState, PROCESSING_STATUS, isProcessingDone, TASK_STATE } from '@/lib/types/sse'
import { useMessageStore } from '@/stores/message-store'
import type { ArtifactPart, ArtifactData, MessageEntity } from '@/stores/message-store/types'
import { mergeArtifacts, extractTextFromArtifacts } from '@/stores/message-store/upsert'
import { normalizeTimestampOrNow } from '@/lib/time'
import { appendEvent } from '@/lib/room-timeline/event-log'
import type { PhasePayload } from '@/stores/turn-event-store/types'
import type { SSEHandlerDeps } from './types'

/**
 * Parse backend processing_status details string into a typed PhasePayload.
 * Backend sends: "Planning next action...", "Delegating to N agent(s)...",
 * "Evaluating agent results...", "Synthesizing responses..."
 *
 * When delegating, the backend also sends an `agents` array with
 * `{ agent_id, agent_name }` objects so the rail can show actual names.
 */
function parseStageDetails(
  details: string,
  agents?: Array<{ agent_id: string; agent_name: string }>,
): PhasePayload | null {
  if (details.startsWith('Planning')) {
    return { name: 'planning' }
  }
  const delegatingMatch = details.match(/^Delegating to (\d+) agent/)
  if (delegatingMatch) {
    const count = parseInt(delegatingMatch[1], 10)
    const agentNames = agents && agents.length > 0
      ? agents.map(a => a.agent_name)
      : [`${count} agent(s)`]
    return { name: 'delegating', agentNames, count }
  }
  if (details.startsWith('Evaluating')) {
    return { name: 'evaluating' }
  }
  if (details.startsWith('Synthesizing')) {
    return { name: 'synthesizing' }
  }
  return null
}

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

function resolveSingleWriteContent(
  existing: MessageEntity | undefined,
  incomingContent: string,
  status: TaskState,
): { content: string; droppedRewrite: boolean } {
  const existingContent = existing?.content ?? ''
  const trimmedIncoming = incomingContent.trim()

  // Single-write invariant: task_update can create the first visible answer,
  // but never rewrite it later (including terminal updates).
  if (existingContent.trim().length === 0) {
    return {
      content: trimmedIncoming.length > 0 ? incomingContent : existingContent,
      droppedRewrite: false,
    }
  }

  if (trimmedIncoming.length > 0 && incomingContent !== existingContent) {
    console.warn(
      '🔒 Dropping task_update content rewrite for',
      existing?.id,
      'status=',
      status,
    )
    return { content: existingContent, droppedRewrite: true }
  }

  return { content: existingContent, droppedRewrite: false }
}

export function createSSEDispatcher(deps: SSEHandlerDeps) {
  const { roomId, lifecycle, getAgentName, getAgentSource,
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

          // Deduplicate: the backend may send agent content via both
          // task_update and agent_response. Skip if:
          // (a) Same message_id already exists with a terminal taskStatus
          //     (agent_response sets taskStatus: null which would clear it), OR
          // (b) Different message_id but a task-tracked entity for the same
          //     agent already exists in this room.
          const existing = store.entities[messageId]
          if (existing?.taskStatus && isTerminalState(existing.taskStatus)) {
            console.log('🔄 Skipping agent_response for', messageId, '— already terminal')
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
            const preserveInFlightTaskStatus = !!(
              existing?.taskStatus &&
              !isTerminalState(existing.taskStatus)
            )

            store.upsertMessage({
              id: messageId,
              roomId,
              messageType: 'agent',
              content,
              senderName: agentName,
              agentId,
              agentSource: agentId ? getAgentSource(agentId) : undefined,
              timestamp: msgTimestamp,
              // Keep non-terminal taskStatus authoritative until task_update
              // explicitly transitions it. Clearing here caused "working"
              // indicators to disappear while processing was still active.
              ...(preserveInFlightTaskStatus ? {} : { taskStatus: null }),
              isEphemeral: false,
              ...(artifacts ? { artifacts } : {}),
            }, 'sse')
          }
        }
        break

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
            const stageDetails = sseMessage.data.details as string | undefined

            // Bridge supervisor stage details into phase_changed turn events
            // so the OrchestrationRail shows real-time Supervisor phases.
            if (stageDetails) {
              const turnId = realMessageId || lifecycle.getMessageId()
              if (turnId) {
                const { useTurnEventStore } = await import('@/stores/turn-event-store')
                const sseAgents = (sseMessage.data as Record<string, unknown>).agents as Array<{ agent_id: string; agent_name: string }> | undefined
                const phase = parseStageDetails(stageDetails, sseAgents)
                if (phase) {
                  useTurnEventStore.getState().append(turnId, {
                    eventId: `sse_phase_${turnId}_${Date.now()}`,
                    turnId,
                    seq: Date.now(),
                    ts: Date.now(),
                    type: 'phase_changed',
                    phase,
                  } as import('@/stores/turn-event-store/types').TurnEvent)
                }
              }
            }

            // When details are present (supervisor stage updates), always
            // re-show the placeholder — even after task_submitted dismissed
            // it — so the user sees "Evaluating...", "Synthesizing...", etc.
            if (stageDetails || !lifecycle.isPlaceholderDismissed()) {
              const defaultText = 'Processing your request\u2026'
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
            lifecycle.setProcessing(false)
            setCancelling(false)
            lifecycle.disarmCancelTimeout()
            store.removeMessage(lifecycle.placeholderId(roomId))
            lifecycle.dismissPlaceholder()

            // Emit turn terminal event — this is the authoritative signal
            // that the room-level processing is done (not individual task completion).
            const turnId = sseMessage.data.message_id || lifecycle.getMessageId()
            if (turnId) {
              const { useTurnEventStore } = await import('@/stores/turn-event-store')
              const terminalType: 'turn_completed' | 'turn_failed' | 'turn_canceled' =
                status === PROCESSING_STATUS.CANCELED ? 'turn_canceled'
                : status === PROCESSING_STATUS.FAILED ? 'turn_failed'
                : 'turn_completed'
              const terminalEvent = terminalType === 'turn_failed'
                ? {
                    eventId: `sse_terminal_${turnId}`,
                    turnId,
                    seq: Date.now(),
                    ts: Date.now(),
                    type: 'turn_failed' as const,
                    reason: ((sseMessage.data as Record<string, unknown>).details as string) || 'Processing failed',
                  }
                : terminalType === 'turn_completed'
                ? {
                    eventId: `sse_terminal_${turnId}`,
                    turnId,
                    seq: Date.now(),
                    ts: Date.now(),
                    type: 'turn_completed' as const,
                    durationMs: 0, // will be overridden by hydration
                  }
                : {
                    eventId: `sse_terminal_${turnId}`,
                    turnId,
                    seq: Date.now(),
                    ts: Date.now(),
                    type: 'turn_canceled' as const,
                  }
              useTurnEventStore.getState().append(turnId, terminalEvent as import('@/stores/turn-event-store/types').TurnEvent)
            }

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
          const { content: resolvedContent } = resolveSingleWriteContent(existing, content, status)
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
            // lifecycle.setProcessing(false).

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
          }
        }
        break

      case 'artifact_update': {
        if (!lifecycle.isPlaceholderDismissed()) {
          store.removeMessage(lifecycle.placeholderId(roomId))
          lifecycle.dismissPlaceholder()
        }

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

          // Promote text from text-only artifacts into content so the
          // bubble renders it inline instead of as a separate artifact card.
          const existingContent = existing?.content || ''
          const promotedText = extractTextFromArtifacts(merged)
          const content = promotedText.length > existingContent.length
            ? promotedText : existingContent

          store.upsertMessage({
            id: message_id,
            roomId,
            messageType: 'agent',
            content,
            senderName: existing?.senderName || 'Agent',
            agentId: existing?.agentId || sseMessage.data.agent_id,
            agentSource: existing?.agentSource || getAgentSource(sseMessage.data.agent_id),
            timestamp: existing?.timestamp || normalizeTimestampOrNow(sseMessage.timestamp),
            artifacts: merged,
          }, 'sse')

          if (!isAppend) {
            appendEvent(roomId, {
              kind: 'artifact_emitted',
              timestamp: sseMessage.timestamp,
              agentId: existing?.agentId || sseMessage.data.agent_id,
              label: `Artifact: ${artifact.name ?? 'output'}`,
            })
          }
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
        const turnEventData = (sseMessage.data?.turn_event ?? sseMessage.data) as Record<string, unknown> | undefined
        if (turnEventData?.turn_id && turnEventData?.type) {
          const { camelCaseEvent } = await import('@/hooks/turn/useSSEToEventLog')
          const { useTurnEventStore } = await import('@/stores/turn-event-store')
          const event = camelCaseEvent(turnEventData)
          useTurnEventStore.getState().append(event.turnId, event)
        }
        break
      }

      default:
        console.log('❓ Unknown SSE message type:', sseMessage.type)
    }
  }
}
