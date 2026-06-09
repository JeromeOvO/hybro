import type { RoomSSEFrameMap } from '@/lib/types/sse'
import { isTerminalState, TASK_STATE } from '@/lib/types/sse'
import { useMessageStore } from '@/stores/message-store'
import { useStreamingStore } from '@/stores/streaming-store'
import type { ArtifactData } from '@/stores/message-store/types'
import { normalizeTimestampOrNow } from '@/lib/time'
import { isSummarySystemAgent } from '@/lib/system-agents'
import { isDeterministicDigestContent } from '@/lib/room-timeline/derive-final-answer'
import { stampLiveTurnTerminalIfInferable } from '@/lib/room-timeline/stamp-live-turn-terminal'
import { scheduleTurnTerminalBackendTruthCheck } from '@/lib/room-timeline/turn-terminal-stamp'
import type { SummaryOrigin } from '@/lib/room-timeline/types'
import { partsToReplacementArtifacts } from '../artifacts'
import type { SSEHandlerDeps } from '../types'
import type { CorrelationResult } from '../correlation'
import { getResolvedMessageId } from '../pending-turn-buffer'

const PARTIAL_STREAM_ARTIFACT_SUFFIX = '-partial-stream'

function partialStreamArtifactId(messageId: string): string {
  return `${messageId}${PARTIAL_STREAM_ARTIFACT_SUFFIX}`
}

function textPartialToArtifact(messageId: string, content: string): ArtifactData {
  return {
    artifactId: partialStreamArtifactId(messageId),
    name: 'response',
    parts: [{ kind: 'text', text: content }],
    isStreaming: true,
  }
}

function inferSummaryOriginFromAgentResponse(
  agentId: string | undefined,
  messageId: string,
  content: string,
): SummaryOrigin | undefined {
  if (!agentId || !isSummarySystemAgent(agentId)) return undefined
  if (!messageId.startsWith('summary-')) return undefined
  const text = content.trim()
  if (!text) return undefined
  if (isDeterministicDigestContent(text)) return 'deterministic'
  return 'llm'
}

function artifactsEqual(a: ArtifactData[] | undefined, b: ArtifactData[] | undefined): boolean {
  const left = a ?? []
  const right = b ?? []
  if (left.length !== right.length) return false
  return JSON.stringify(left) === JSON.stringify(right)
}

export function handleAgentResponsePartial(
  ctx: SSEHandlerDeps,
  sseMessage: RoomSSEFrameMap['agent_response_partial'],
  correlation: CorrelationResult,
): void {
  const { message_id, content_delta } = sseMessage.data
  if (!message_id || typeof content_delta !== 'string') return

  const streaming = useStreamingStore.getState()
  const hasPartialArtifact = (streaming.buffers[message_id]?.artifacts ?? [])
    .some(a => a.artifactId === partialStreamArtifactId(message_id))

  streaming.append(
    message_id,
    ctx.roomId,
    textPartialToArtifact(message_id, content_delta),
    hasPartialArtifact,
    {
      clientRequestId: correlation.clientReqId,
      userMessageId: correlation.clientReqId
        ? getResolvedMessageId(correlation.clientReqId)
        : undefined,
    },
  )
}

export async function handleAgentResponse(ctx: SSEHandlerDeps, sseMessage: RoomSSEFrameMap['agent_response']): Promise<void> {
  if (!sseMessage.data.message_id) return

  const store = useMessageStore.getState()
  const streaming = useStreamingStore.getState()
  const messageId = sseMessage.data.message_id
  const { roomId } = ctx
  const incomingArtifacts = partsToReplacementArtifacts(
    sseMessage.data.parts as Record<string, unknown>[] | undefined,
    messageId,
  )

  const existing = store.entities[messageId]
  if (existing) {
    const incomingContent = (sseMessage.data.content ?? '').trim()
    const existingContent = (existing.content ?? '').trim()
    const hasExistingRenderable = existingContent.length > 0
      || (existing.artifacts?.length ?? 0) > 0
    const looksDuplicateContent = incomingContent.length > 0
      && incomingContent === existingContent
      && artifactsEqual(existing.artifacts, incomingArtifacts)
    if (hasExistingRenderable && looksDuplicateContent) {
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
          clientRequestId: existing.clientRequestId || sseMessage.data.client_request_id,
          timestamp: existing.timestamp,
          taskStatus: TASK_STATE.COMPLETED,
          taskContent: '',
          taskUpdatedAt: normalizeTimestampOrNow(sseMessage.timestamp),
          isEphemeral: false,
          ...(existing.artifacts ? { artifacts: existing.artifacts } : {}),
        }, 'sse')
      }
      streaming.clear(messageId)
      const stamped = stampLiveTurnTerminalIfInferable(ctx.roomId, ctx.lifecycle, {
        clientRequestId: existing.clientRequestId || sseMessage.data.client_request_id,
        relatedMessageId: existing.relatedMessageId ?? sseMessage.data.related_message_id,
      })
      if (!stamped) {
        scheduleTurnTerminalBackendTruthCheck(
          ctx.roomId,
          ctx.lifecycle,
          {
            clientRequestId: existing.clientRequestId || sseMessage.data.client_request_id,
            relatedMessageId: existing.relatedMessageId ?? sseMessage.data.related_message_id,
          },
          ctx.getToken,
        )
      }
      return
    }
  }

  if (sseMessage.data.content === undefined && !sseMessage.data.parts) return

  const agentId = sseMessage.data.agent_id
  const agentName = agentId
    ? await ctx.getAgentName(agentId)
    : 'Agent'
  const content = sseMessage.data.content ?? ''
  const msgTimestamp = normalizeTimestampOrNow(sseMessage.timestamp)
  const entity = store.entities[messageId]
  const responseTaskStatus = entity?.taskStatus && isTerminalState(entity.taskStatus)
    ? entity.taskStatus
    : TASK_STATE.COMPLETED
  const summaryOrigin = inferSummaryOriginFromAgentResponse(agentId, messageId, content)
    ?? entity?.summaryOrigin

  store.upsertMessage({
    id: messageId,
    roomId,
    messageType: 'agent',
    content,
    senderName: agentName,
    agentId,
    agentSource: agentId ? ctx.getAgentSource(agentId) : undefined,
    clientRequestId: entity?.clientRequestId || sseMessage.data.client_request_id,
    relatedMessageId: entity?.relatedMessageId ?? sseMessage.data.related_message_id ?? undefined,
    timestamp: msgTimestamp,
    taskStatus: responseTaskStatus,
    taskContent: '',
    taskUpdatedAt: msgTimestamp,
    isEphemeral: false,
    artifacts: incomingArtifacts,
    ...(summaryOrigin ? { summaryOrigin } : {}),
  }, 'sse')
  streaming.clear(messageId)

  const stamped = stampLiveTurnTerminalIfInferable(ctx.roomId, ctx.lifecycle, {
    clientRequestId: entity?.clientRequestId || sseMessage.data.client_request_id,
    relatedMessageId: entity?.relatedMessageId ?? sseMessage.data.related_message_id,
  })
  if (!stamped) {
    scheduleTurnTerminalBackendTruthCheck(
      ctx.roomId,
      ctx.lifecycle,
      {
        clientRequestId: entity?.clientRequestId || sseMessage.data.client_request_id,
        relatedMessageId: entity?.relatedMessageId ?? sseMessage.data.related_message_id,
      },
      ctx.getToken,
    )
  }
}
