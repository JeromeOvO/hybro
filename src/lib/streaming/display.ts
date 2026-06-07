import type { TaskState } from '@/lib/types/sse'
import type { ArtifactData } from '@/stores/message-store/types'
import type { StreamBuffer } from '@/stores/streaming-store'

function isTextOnlyArtifact(a: ArtifactData): boolean {
  return a.parts.length > 0 && a.parts.every(p => p.kind === 'text')
}

function nonTextArtifacts(artifacts: ArtifactData[] | undefined): ArtifactData[] {
  if (!artifacts?.length) return []
  return artifacts.filter(a => !isTextOnlyArtifact(a))
}

function mergeArtifactsById(...lists: (ArtifactData[] | undefined)[]): ArtifactData[] | undefined {
  const map = new Map<string, ArtifactData>()
  for (const list of lists) {
    if (!list) continue
    for (const artifact of list) {
      map.set(artifact.artifactId, artifact)
    }
  }
  const merged = Array.from(map.values())
  return merged.length > 0 ? merged : undefined
}

/** Non-text artifacts from entity and/or live buffer (files, data parts). */
export function resolveNonTextArtifacts(
  buffer: StreamBuffer | undefined,
  entityArtifacts: ArtifactData[] | undefined,
): ArtifactData[] | undefined {
  return mergeArtifactsById(
    nonTextArtifacts(entityArtifacts),
    nonTextArtifacts(buffer?.artifacts),
  )
}

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
  if (!taskStatus) return false
  return ACTIVE_ENTITY_TASK_STATES.has(taskStatus)
}

export { resolveStreamBuffer } from '@/stores/streaming-store'

export function resolveStreamText(
  buffer: StreamBuffer | undefined,
  fallbackContent: string,
): string {
  if (!buffer) return fallbackContent
  const live = buffer.text
  if (!live) return fallbackContent
  if (!buffer.isComplete) return live
  return live.length >= fallbackContent.length ? live : fallbackContent
}

export function resolveStreamArtifacts(
  buffer: StreamBuffer | undefined,
  fallbackArtifacts: ArtifactData[] | undefined,
): ArtifactData[] | undefined {
  if (buffer && !buffer.isComplete) {
    return resolveNonTextArtifacts(buffer, fallbackArtifacts)
  }
  return fallbackArtifacts
}

/** While streaming: text from buffer; show non-text artifacts from entity and buffer. */
export function resolveDetailArtifacts(
  buffer: StreamBuffer | undefined,
  entityArtifacts: ArtifactData[] | undefined,
): ArtifactData[] | undefined {
  if (isBufferStreaming(buffer)) {
    return resolveNonTextArtifacts(buffer, entityArtifacts)
  }
  return entityArtifacts
}
