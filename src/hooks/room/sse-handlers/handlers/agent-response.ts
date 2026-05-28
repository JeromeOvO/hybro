import type { SSEMessage } from '@/lib/types/sse'
import { isTerminalState, TASK_STATE } from '@/lib/types/sse'
import { useMessageStore } from '@/stores/message-store'
import { useStreamingStore } from '@/stores/streaming-store'
import { normalizeTimestampOrNow } from '@/lib/time'
import { partsToArtifacts } from '../artifacts'
import type { SSEHandlerDeps } from '../types'

export async function handleAgentResponse(ctx: SSEHandlerDeps, sseMessage: SSEMessage): Promise<void> {
  console.log('🤖 Agent response received via SSE')
  if (!sseMessage.data?.message_id) return

  const store = useMessageStore.getState()
  const streaming = useStreamingStore.getState()
  const messageId = sseMessage.data.message_id
  const { roomId } = ctx

  const existing = store.entities[messageId]
  const existingHasContent = (existing?.content ?? '').trim().length > 0
    || (existing?.artifacts?.length ?? 0) > 0
  if (existing?.taskStatus && isTerminalState(existing.taskStatus) && existingHasContent) {
    console.log('🔄 Skipping agent_response for', messageId, '— already terminal with content')
    return
  }

  if (existing) {
    const incomingContent = (sseMessage.data.content ?? '').trim()
    const existingContent = (existing.content ?? '').trim()
    const hasExistingRenderable = existingContent.length > 0
      || (existing.artifacts?.length ?? 0) > 0
    const looksDuplicateContent = incomingContent.length === 0
      || incomingContent === existingContent
      || (incomingContent.length > 0 && existingContent.startsWith(incomingContent))
    const isAppendOnlyUpgrade = incomingContent.length > existingContent.length
      && incomingContent.startsWith(existingContent)
    const isDivergentRewrite = existingContent.length > 0
      && incomingContent.length > 0
      && !looksDuplicateContent
      && !isAppendOnlyUpgrade
    if (hasExistingRenderable && (looksDuplicateContent || isDivergentRewrite)) {
      const canFinalizeExisting = !existing.taskStatus || !isTerminalState(existing.taskStatus)
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
          taskContent: '',
          taskUpdatedAt: normalizeTimestampOrNow(sseMessage.timestamp),
          isEphemeral: false,
          ...(existing.artifacts ? { artifacts: existing.artifacts } : {}),
        }, 'sse')
        streaming.clear(messageId)
      }
      console.log('🔄 Skipping duplicate agent_response for', messageId, '— streamed content already present')
      return
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
      return
    }
  }

  if (sseMessage.data?.content === undefined && !sseMessage.data?.parts) return

  const agentId = sseMessage.data?.agent_id as string | undefined
  const agentName = agentId
    ? await ctx.getAgentName(agentId)
    : (sseMessage.data?.agent_name as string | undefined) || 'Agent'
  const content = sseMessage.data.content ?? ''
  const msgTimestamp = normalizeTimestampOrNow(sseMessage.timestamp)
  const entity = store.entities[messageId]
  const artifacts = partsToArtifacts(
    sseMessage.data.parts as Record<string, unknown>[] | undefined,
    messageId,
    entity,
  )
  const responseTaskStatus = entity?.taskStatus && isTerminalState(entity.taskStatus)
    ? entity.taskStatus
    : TASK_STATE.COMPLETED

  store.upsertMessage({
    id: messageId,
    roomId,
    messageType: 'agent',
    content,
    senderName: agentName,
    agentId,
    agentSource: agentId ? ctx.getAgentSource(agentId) : undefined,
    clientRequestId: entity?.clientRequestId || sseMessage.data?.client_request_id,
    timestamp: msgTimestamp,
    taskStatus: responseTaskStatus,
    taskContent: '',
    taskUpdatedAt: msgTimestamp,
    isEphemeral: false,
    ...(artifacts ? { artifacts } : {}),
  }, 'sse')
  streaming.clear(messageId)
}
