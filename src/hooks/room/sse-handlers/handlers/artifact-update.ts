import type { SSEMessage } from '@/lib/types/sse'
import { useMessageStore } from '@/stores/message-store'
import { useStreamingStore } from '@/stores/streaming-store'
import { appendEvent } from '@/lib/room-timeline/event-log'
import type { ProcessingLifecycle } from '../../processing-lifecycle'
import { sseArtifactDataFromPayload } from '../artifacts'

export interface ArtifactUpdateContext {
  roomId: string
  lifecycle: ProcessingLifecycle
}

export function handleArtifactUpdate(
  ctx: ArtifactUpdateContext,
  sseMessage: SSEMessage,
): void {
  const store = useMessageStore.getState()
  const streaming = useStreamingStore.getState()

  if (!ctx.lifecycle.isPlaceholderDismissed()) {
    store.removeMessage(ctx.lifecycle.placeholderId(ctx.roomId))
    ctx.lifecycle.dismissPlaceholder()
  }

  if (!sseMessage.data?.message_id || !sseMessage.data?.artifact) return

  const { message_id, artifact, append: isAppend, last_chunk } = sseMessage.data
  const artifactData = sseArtifactDataFromPayload(
    artifact as Record<string, unknown>,
    isAppend ?? false,
    last_chunk,
  )

  streaming.append(message_id, ctx.roomId, artifactData, isAppend ?? false)
  if (last_chunk) streaming.markComplete(message_id)

  if (!isAppend) {
    appendEvent(ctx.roomId, {
      kind: 'artifact_emitted',
      timestamp: sseMessage.timestamp,
      agentId: sseMessage.data.agent_id,
      label: `Artifact: ${(artifact as Record<string, unknown>).name ?? 'output'}`,
    })
  }
}
