import type { TaskState } from '@/lib/types/sse'
import type { ArtifactData } from '@/stores/message-store/types'
import type { StreamBuffer } from '@/stores/streaming-store'

/** True when an active stream buffer has not received last_chunk. */
export function isBufferStreaming(buffer: StreamBuffer | undefined): boolean {
  return buffer != null && !buffer.isComplete
}

/** Streaming for timeline view models (AgentResultViewModel.status). */
export function resolveViewModelStreaming(
  buffer: StreamBuffer | undefined,
  status: 'completed' | 'failed' | 'awaiting_input' | 'working',
): boolean {
  if (buffer) return !buffer.isComplete
  return status === 'working'
}

const ACTIVE_ENTITY_TASK_STATES: ReadonlySet<TaskState> = new Set([
  'working',
  'submitted',
])

/** Streaming for message entities (detail pane / selectors). */
export function resolveEntityStreaming(
  buffer: StreamBuffer | undefined,
  taskStatus: TaskState | undefined,
): boolean {
  if (buffer) return !buffer.isComplete
  return ACTIVE_ENTITY_TASK_STATES.has(taskStatus)
}

export function resolveStreamText(
  buffer: StreamBuffer | undefined,
  fallbackContent: string,
): string {
  return buffer?.text ?? fallbackContent
}

export function resolveStreamArtifacts(
  buffer: StreamBuffer | undefined,
  fallbackArtifacts: ArtifactData[] | undefined,
): ArtifactData[] | undefined {
  if (isBufferStreaming(buffer)) return buffer.artifacts
  return fallbackArtifacts
}

/** Suppress entity artifacts only while the buffer is actively receiving chunks. */
export function resolveDetailArtifacts(
  buffer: StreamBuffer | undefined,
  entityArtifacts: ArtifactData[] | undefined,
): ArtifactData[] | undefined {
  return isBufferStreaming(buffer) ? undefined : entityArtifacts
}
