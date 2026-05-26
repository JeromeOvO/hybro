import type { SSEMessage, TaskState } from '@/lib/types/sse'
import { useMessageStore } from '@/stores/message-store'
import { normalizeTimestampOrNow } from '@/lib/time'
import { appendEvent } from '@/lib/room-timeline/event-log'
import { resolveClientRequestMessageId } from '../pending-turn-buffer'
import type { CorrelationResult } from '../correlation'
import type { SSEHandlerDeps } from '../types'

export async function handleHitlInputRequested(
  ctx: SSEHandlerDeps,
  sseMessage: SSEMessage,
  correlation: CorrelationResult,
): Promise<void> {
  console.log('🔔 HITL input requested via SSE:', sseMessage.data)

  if (correlation.clientReqId && sseMessage.data?.message_id) {
    resolveClientRequestMessageId(correlation.clientReqId, sseMessage.data.message_id)
  }

  if (!sseMessage.data) return

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

  for (const [oldReqId, oldEntityId] of hitlRequestIndex.current) {
    if (oldEntityId === message_id && oldReqId !== request_id) {
      hitlRequestIndex.current.delete(oldReqId)
    }
  }

  let resolvedAgentName = agent_name
  if (!resolvedAgentName && agent_id) {
    resolvedAgentName = await ctx.getAgentName(agent_id)
  }

  store.upsertMessage({
    id: message_id,
    roomId,
    messageType: 'agent',
    content: prompt || '',
    senderName: resolvedAgentName || 'Agent',
    timestamp: normalizeTimestampOrNow(sseMessage.timestamp),
    agentId: agent_id,
    agentSource: ctx.getAgentSource(agent_id),
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

export function handleHitlStatusUpdate(
  ctx: SSEHandlerDeps,
  sseMessage: SSEMessage,
  correlation: CorrelationResult,
): void {
  console.log('🔔 HITL status update via SSE:', sseMessage.data)
  if (!sseMessage.data) return

  const { request_id, status: hitlStatus, error_message } = sseMessage.data
  if (!request_id) return

  const store = useMessageStore.getState()
  const entityId = ctx.hitlRequestIndex.current.get(request_id)
  const entity = entityId ? store.entities[entityId] : undefined
  if (!entity) return

  if (entity.hitlRequestId && entity.hitlRequestId !== request_id) {
    console.log('🔔 Skipping stale hitl_status_update for', request_id,
      '— entity now owns', entity.hitlRequestId)
    ctx.hitlRequestIndex.current.delete(request_id)
    return
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
