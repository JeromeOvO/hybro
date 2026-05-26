import type { SSEMessage, TaskState } from '@/lib/types/sse'
import { TASK_STATE } from '@/lib/types/sse'
import { useMessageStore } from '@/stores/message-store'
import { normalizeTimestampOrNow } from '@/lib/time'
import { appendEvent } from '@/lib/room-timeline/event-log'
import type { CorrelationResult } from '../correlation'
import type { SSEHandlerDeps } from '../types'

export async function handleTaskSubmitted(
  ctx: SSEHandlerDeps,
  sseMessage: SSEMessage,
  correlation: CorrelationResult,
): Promise<void> {
  console.log('📋 Task submitted via SSE:', sseMessage.data)
  if (!sseMessage.data?.message_id) return

  const messageId = sseMessage.data.message_id
  let resolvedAgentName = sseMessage.data.agent_name
  if (!resolvedAgentName && sseMessage.data.agent_id) {
    resolvedAgentName = await ctx.getAgentName(sseMessage.data.agent_id)
  }
  const taskTimestamp = sseMessage.data.created_at || sseMessage.timestamp

  useMessageStore.getState().upsertMessage({
    id: messageId,
    roomId: ctx.roomId,
    messageType: 'agent',
    content: '',
    senderName: resolvedAgentName || 'Agent',
    agentId: sseMessage.data.agent_id,
    agentSource: ctx.getAgentSource(sseMessage.data.agent_id),
    taskStatus: (sseMessage.data.status as TaskState) || TASK_STATE.WORKING,
    taskContent: sseMessage.data.task_content,
    stepNumber: sseMessage.data.step_number,
    totalSteps: sseMessage.data.total_steps,
    relatedMessageId: sseMessage.data.related_message_id,
    clientRequestId: sseMessage.data.client_request_id,
    timestamp: normalizeTimestampOrNow(taskTimestamp),
    taskCreatedAt: normalizeTimestampOrNow(taskTimestamp),
  }, 'sse')

  appendEvent(ctx.roomId, {
    kind: 'agent_started',
    timestamp: sseMessage.timestamp,
    agentId: sseMessage.data.agent_id,
    agentName: resolvedAgentName ?? 'Agent',
    label: `${resolvedAgentName ?? 'Agent'} started`,
  })
}
