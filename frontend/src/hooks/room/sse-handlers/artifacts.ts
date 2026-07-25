import type { ArtifactPart, ArtifactData, MessageEntity } from '@/stores/message-store/types'
import { mergeArtifacts } from '@/stores/message-store/upsert'

/** Non-text parts from task_update / agent_response payloads. */
export function isRenderableArtifactPart(part: ArtifactPart): boolean {
  if (part.kind === 'file') return !!(part.file?.fileId || part.file?.uri)
  if (part.kind === 'data') return !!part.data && Object.keys(part.data).length > 0
  return false
}

function artifactPartFromRaw(p: Record<string, unknown>): ArtifactPart | undefined {
  const root = (p.root ?? p) as Record<string, unknown>
  const kind = ((root.kind as string) || 'text') as ArtifactPart['kind']
  const fileData = root.file as Record<string, unknown> | undefined
  const fileMetadata = root.metadata as Record<string, unknown> | undefined
  if (kind === 'file') {
    const uri = fileData?.uri as string | undefined
    const fileId = fileMetadata?.file_id as string | undefined
    if (!uri && !fileId) return undefined
    return {
      kind,
      text: undefined,
      file: {
        uri,
        fileId,
        mime_type: ((
          fileMetadata?.mime_type
          || fileData?.mime_type
          || fileData?.mimeType
        ) as string | undefined),
        name: ((fileMetadata?.file_name || fileData?.name) as string | undefined),
        sizeBytes: fileMetadata?.size_bytes as number | undefined,
        sha256: fileMetadata?.sha256 as string | undefined,
      },
      data: undefined,
    }
  }
  return {
    kind,
    text: root.text as string | undefined,
    file: undefined,
    data: root.data as Record<string, unknown> | undefined,
  }
}

export function partsToArtifacts(
  rawParts: Record<string, unknown>[] | undefined,
  messageId: string,
  existing: MessageEntity | undefined,
): ArtifactData[] | undefined {
  if (!rawParts || rawParts.length === 0) return existing?.artifacts
  const nonTextParts = rawParts
    .map(artifactPartFromRaw)
    .filter((part): part is ArtifactPart => part !== undefined)
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
    .map(artifactPartFromRaw)
    .filter((part): part is ArtifactPart => part !== undefined)
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
    parts: rawParts
      .map(artifactPartFromRaw)
      .filter((part): part is ArtifactPart => part !== undefined),
    isStreaming: isAppend ? !lastChunk : false,
  }
}
