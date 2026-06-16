import type { ArtifactPart, ArtifactData, MessageEntity } from '@/stores/message-store/types'
import { mergeArtifacts } from '@/stores/message-store/upsert'

/** Non-text parts from task_update / agent_response payloads. */
export function isRenderableArtifactPart(part: ArtifactPart): boolean {
  if (part.kind === 'file') return !!(part.file?.uri || part.file?.bytes)
  if (part.kind === 'data') return !!part.data && Object.keys(part.data).length > 0
  return false
}

export function partsToArtifacts(
  rawParts: Record<string, unknown>[] | undefined,
  messageId: string,
  existing: MessageEntity | undefined,
): ArtifactData[] | undefined {
  if (!rawParts || rawParts.length === 0) return existing?.artifacts
  const nonTextParts = rawParts
    .map((p) => {
      const root = (p.root ?? p) as Record<string, unknown>
      const fileData = root.file as Record<string, unknown> | undefined
      return {
        kind: ((root.kind as string) || 'text') as ArtifactPart['kind'],
        text: root.text as string | undefined,
        file: fileData ? {
          uri: (fileData.uri as string | undefined),
          bytes: (fileData.bytes as string | undefined),
          mime_type: ((fileData.mime_type || fileData.mimeType) as string | undefined),
          name: (fileData.name as string | undefined),
        } : undefined,
        data: root.data as Record<string, unknown> | undefined,
      }
    })
    .filter((part) => part.kind !== 'text' && isRenderableArtifactPart(part))
  if (nonTextParts.length === 0) return existing?.artifacts
  const inline: ArtifactData = {
    artifactId: `${messageId}-parts`,
    name: 'Response files',
    parts: nonTextParts,
  }
  return mergeArtifacts(existing?.artifacts, inline, false)
}

export function partsToReplacementArtifacts(
  rawParts: Record<string, unknown>[] | undefined,
  messageId: string,
): ArtifactData[] {
  if (!rawParts || rawParts.length === 0) return []
  const nonTextParts = rawParts
    .map((p) => {
      const root = (p.root ?? p) as Record<string, unknown>
      const fileData = root.file as Record<string, unknown> | undefined
      return {
        kind: ((root.kind as string) || 'text') as ArtifactPart['kind'],
        text: root.text as string | undefined,
        file: fileData ? {
          uri: (fileData.uri as string | undefined),
          bytes: (fileData.bytes as string | undefined),
          mime_type: ((fileData.mime_type || fileData.mimeType) as string | undefined),
          name: (fileData.name as string | undefined),
        } : undefined,
        data: root.data as Record<string, unknown> | undefined,
      }
    })
    .filter((part) => part.kind !== 'text' && isRenderableArtifactPart(part))
  if (nonTextParts.length === 0) return []
  return [{
    artifactId: `${messageId}-parts`,
    name: 'Response files',
    parts: nonTextParts,
  }]
}

export function sseArtifactDataFromPayload(
  artifact: Record<string, unknown>,
  isAppend: boolean,
  lastChunk: boolean | undefined,
): ArtifactData {
  const rawParts = Array.isArray(artifact.parts)
    ? (artifact.parts as Record<string, unknown>[])
    : []

  return {
    artifactId: (artifact.artifact_id || artifact.artifactId) as string,
    name: artifact.name as string | undefined,
    parts: rawParts.map((p: Record<string, unknown>) => {
      const root = (p.root ?? p) as Record<string, unknown>
      const fileData = root.file as Record<string, unknown> | undefined
      return {
        kind: ((root.kind as string) || 'text') as ArtifactPart['kind'],
        text: root.text as string | undefined,
        file: fileData ? {
          uri: (fileData.uri as string | undefined),
          bytes: (fileData.bytes as string | undefined),
          mime_type: (fileData.mime_type || fileData.mimeType) as string | undefined,
          name: (fileData.name as string | undefined),
        } : undefined,
        data: root.data as Record<string, unknown> | undefined,
      }
    }),
    isStreaming: isAppend ? !lastChunk : false,
  }
}
