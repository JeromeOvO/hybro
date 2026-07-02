import type { RoomSSEFrameMap, TaskState } from '@/lib/types/sse'
import { buildPendingHitlIncomingMessage } from '@/lib/hitl/hitl-message-projection'
import { useMessageStore } from '@/stores/message-store'
import { normalizeTimestampOrNow } from '@/lib/time'
import { appendEvent } from '@/lib/room-timeline/event-log'
import { findProcessingStatusUserEntity } from '../../processing-status-log'
import type { CorrelationResult } from '../correlation'
import type { SSEHandlerDeps } from '../types'

export async function handleHitlRequest(
  ctx: SSEHandlerDeps,
  sseMessage: RoomSSEFrameMap['hitl_request'],
  correlation: CorrelationResult,
): Promise<void> {
  const {
    request_id, message_id, prompt, prompt_type, choices,
    agent_name, agent_id, step_number, total_steps, expires_at,
    group_id, group_total, group_index, related_message_id,
  } = sseMessage.data

  if (!request_id || !message_id) return

  const store = useMessageStore.getState()
  const { lifecycle, hitlRequestIndex, roomId } = ctx

  store.removeMessage(lifecycle.placeholderId(roomId))
  lifecycle.dismissPlaceholder()

  const processingUser =
    findProcessingStatusUserEntity(roomId, {
      clientRequestId: correlation.clientReqId,
      relatedMessageId: related_message_id,
      latestWithLogs: true,
    }) ??
    findProcessingStatusUserEntity(roomId, {
      messageId: lifecycle.getMessageId(),
      latestWithLogs: true,
    })
  const activeClientRequestId = lifecycle.getPendingRunEventAck()
  const lifecycleMessageId = lifecycle.getMessageId()
  const eventMatchesActiveAck =
    !!activeClientRequestId &&
    !!correlation.clientReqId &&
    activeClientRequestId === correlation.clientReqId
  const userMatchesActiveAck =
    !!activeClientRequestId &&
    !!processingUser?.clientRequestId &&
    activeClientRequestId === processingUser.clientRequestId
  const userMatchesLifecycle =
    !!lifecycleMessageId &&
    processingUser?.id === lifecycleMessageId
  const isCurrentTurnHitl =
    eventMatchesActiveAck ||
    userMatchesActiveAck ||
    userMatchesLifecycle ||
    (!activeClientRequestId && !lifecycleMessageId && !!processingUser)
  if (isCurrentTurnHitl) {
    lifecycle.markProcessingResolved()
    lifecycle.stopProcessing({ clearMessageId: false })
  }

  for (const [oldReqId, oldEntityId] of hitlRequestIndex.current) {
    if (oldEntityId === message_id && oldReqId !== request_id) {
      hitlRequestIndex.current.delete(oldReqId)
    }
  }

  let resolvedAgentName = agent_name
  if (!resolvedAgentName && agent_id) {
    resolvedAgentName = await ctx.getAgentName(agent_id)
  }

  store.upsertMessage(buildPendingHitlIncomingMessage({
    roomId,
    messageId: message_id,
    requestId: request_id,
    prompt,
    promptType: prompt_type,
    choices: Array.isArray(choices) ? choices as string[] : null,
    timestamp: sseMessage.timestamp,
    agentId: agent_id,
    agentName: resolvedAgentName,
    agentSource: ctx.getAgentSource(agent_id ?? undefined),
    expiresAt: expires_at,
    groupId: group_id,
    groupTotal: group_total,
    groupIndex: group_index,
    stepNumber: step_number,
    totalSteps: total_steps,
    relatedMessageId: related_message_id,
    clientRequestId: sseMessage.data.client_request_id,
  }), 'sse')
  hitlRequestIndex.current.set(request_id, message_id)

  appendEvent(roomId, {
    kind: 'hitl_requested',
    timestamp: sseMessage.timestamp,
    agentId: agent_id ?? undefined,
    label: 'Input requested',
    hitlPayload: { prompt: prompt ?? '' },
  })
}

export function handleHitlResponse(
  ctx: SSEHandlerDeps,
  sseMessage: RoomSSEFrameMap['hitl_response'],
  correlation: CorrelationResult,
): void {
  const { request_id, status: hitlStatus, error_message } = sseMessage.data
  if (!request_id) return

  const store = useMessageStore.getState()
  const indexedEntityId = ctx.hitlRequestIndex.current.get(request_id)
  const fallbackEntityId = sseMessage.data.message_id
  const entityId = indexedEntityId ?? fallbackEntityId
  const entity = entityId ? store.entities[entityId] : undefined
  if (!entity) return

  if (entity.hitlRequestId && entity.hitlRequestId !== request_id) {
    if (indexedEntityId) {
      ctx.hitlRequestIndex.current.delete(request_id)
    }
    return
  }

  if (!indexedEntityId) {
    ctx.hitlRequestIndex.current.set(request_id, entity.id)
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
    roomId: ctx.roomId,
    messageType: 'agent',
    content: resolvedContent,
    senderName: entity.senderName,
    timestamp: normalizeTimestampOrNow(sseMessage.timestamp),
    hitlResolved: resolved,
    taskStatus: resolvedTaskStatus,
    taskError: resolvedTaskError,
  }, 'sse')

  if (resolved) {
    ctx.hitlRequestIndex.current.delete(request_id)
    appendEvent(ctx.roomId, {
      kind: 'hitl_answered',
      timestamp: sseMessage.timestamp,
      agentId: entity.agentId,
      label: 'Input provided',
    })
  }
}
