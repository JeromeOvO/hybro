import type { RoomSSEFrameMap } from '@/lib/types/sse'
import { isTerminalState } from '@/lib/types/sse'
import { useMessageStore } from '@/stores/message-store'
import { useStreamingStore } from '@/stores/streaming-store'
import { appendEvent } from '@/lib/room-timeline/event-log'
import type { ProcessingLifecycle } from '../../processing-lifecycle'
import { sseArtifactDataFromPayload } from '../artifacts'
import type { CorrelationResult } from '../correlation'
import { getResolvedMessageId } from '../pending-turn-buffer'

export interface ArtifactUpdateContext {
  roomId: string
  lifecycle: ProcessingLifecycle
}

export function handleArtifactUpdate(
  ctx: ArtifactUpdateContext,
  sseMessage: RoomSSEFrameMap['artifact_update'],
  correlation: CorrelationResult,
): void {
  if (sseMessage.data.last_chunk !== true) return

  const store = useMessageStore.getState()
  const streaming = useStreamingStore.getState()

  if (!ctx.lifecycle.isPlaceholderDismissed()) {
    store.removeMessage(ctx.lifecycle.placeholderId(ctx.roomId))
    ctx.lifecycle.dismissPlaceholder()
  }

  if (!sseMessage.data.message_id || !sseMessage.data.artifact) return

  const { message_id, artifact, append: isAppend, last_chunk } = sseMessage.data

  const entity = store.entities[message_id]
  if (entity?.taskStatus && isTerminalState(entity.taskStatus)) {
    streaming.clear(message_id)
    return
  }
  const artifactData = sseArtifactDataFromPayload(
    artifact as Record<string, unknown>,
    isAppend,
    last_chunk,
  )
  const artifactId = artifactData.artifactId
  const hasExistingArtifact = (streaming.buffers[message_id]?.artifacts ?? [])
    .some(a => a.artifactId === artifactId)
  const resolvedAppend = isAppend ?? hasExistingArtifact

  streaming.append(message_id, ctx.roomId, artifactData, resolvedAppend, {
    clientRequestId: correlation.clientReqId,
    userMessageId: correlation.clientReqId
      ? getResolvedMessageId(correlation.clientReqId)
      : undefined,
  })
  if (last_chunk) streaming.markComplete(message_id)

  if (!resolvedAppend) {
    appendEvent(ctx.roomId, {
      kind: 'artifact_emitted',
      timestamp: sseMessage.timestamp,
      agentId: sseMessage.data.agent_id,
      label: `Artifact: ${(artifact as Record<string, unknown>).name ?? 'output'}`,
    })
  }
}
