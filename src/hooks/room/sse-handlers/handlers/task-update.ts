import { banner } from '@/components/ui/banner'
import type { SSEMessage, TaskState } from '@/lib/types/sse'
import { isTerminalState, TASK_STATE } from '@/lib/types/sse'
import { useMessageStore } from '@/stores/message-store'
import { useStreamingStore } from '@/stores/streaming-store'
import { normalizeTimestampOrNow } from '@/lib/time'
import { appendEvent } from '@/lib/room-timeline/event-log'
import { partsToArtifacts } from '../artifacts'
import { applyRoomCommands } from '../apply-commands'
import type { CorrelationResult } from '../correlation'
import type { SSEHandlerDeps } from '../types'

export async function handleTaskUpdate(
  ctx: SSEHandlerDeps,
  sseMessage: SSEMessage,
  correlation: CorrelationResult,
): Promise<void> {
  if (correlation.shouldDrop) return
  if (correlation.shouldBuffer && correlation.clientReqId) return
  console.log('📋 Task update via SSE:', sseMessage.data)
  if (!sseMessage.data?.message_id) return

  const messageId = sseMessage.data.message_id
  const status = sseMessage.data.status as TaskState
  let resolvedAgentName = sseMessage.data.agent_name
  if (!resolvedAgentName && sseMessage.data.agent_id) {
    resolvedAgentName = await ctx.getAgentName(sseMessage.data.agent_id)
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

  const store = useMessageStore.getState()
  const existing = store.entities[messageId]

  const baseMsg = {
    id: messageId,
    roomId: ctx.roomId,
    messageType: 'agent' as const,
    senderName: resolvedAgentName || 'Agent',
    agentId: sseMessage.data.agent_id,
    agentSource: ctx.getAgentSource(sseMessage.data.agent_id),
    clientRequestId: sseMessage.data.client_request_id,
    timestamp: existing?.timestamp ?? normalizeTimestampOrNow(taskTimestamp),
  }

  // INVARIANT: buffer read + stream_clear in same sync turn (see applyRoomCommands).
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
    applyRoomCommands([
      { type: 'remove_message', id: ctx.lifecycle.placeholderId(ctx.roomId) },
      {
        type: 'upsert_message',
        source: 'sse',
        message: {
          ...baseMsg,
          content: resolvedContent,
          isEphemeral: false,
          ...taskFields,
          ...(artifacts ? { artifacts } : {}),
        },
      },
      { type: 'stream_clear', messageId },
    ])
    ctx.lifecycle.dismissPlaceholder()

    if (status === TASK_STATE.COMPLETED) {
      appendEvent(ctx.roomId, {
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
      appendEvent(ctx.roomId, {
        kind: 'agent_failed',
        timestamp: sseMessage.timestamp,
        agentId: sseMessage.data.agent_id,
        agentName: resolvedAgentName,
        label: `${resolvedAgentName ?? 'Agent'} failed`,
        body: sseMessage.data.error,
      })
    }

    if (status === TASK_STATE.CANCELED) {
      ctx.setCancelling(false)
      ctx.lifecycle.disarmCancelTimeout()
    }

    if (!ctx.lifecycle.hasCancelTimedOut()) {
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
