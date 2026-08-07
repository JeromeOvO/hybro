import type { ArtifactPart, ArtifactData, MessageEntity } from '@/stores/message-store/types'
import {
  artifactPartIdentities,
  artifactPartIdentity,
  deduplicateArtifactsByPart,
} from '@/lib/artifacts/artifact-identity'
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

function syntheticArtifactId(
  messageId: string,
  artifacts: ArtifactData[] | undefined,
): string {
  const baseId = `${messageId}-parts`
  const existing = new Map(
    artifacts?.map(artifact => [artifact.artifactId, artifact]) ?? [],
  )
  for (let suffix = 0; ; suffix += 1) {
    const candidate = suffix === 0
      ? baseId
      : suffix === 1
        ? `${baseId}:synthetic`
        : `${baseId}:synthetic-${suffix}`
    const artifact = existing.get(candidate)
    if (!artifact || artifact.isSynthetic) return candidate
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
  const existingPartIds = artifactPartIdentities(existing?.artifacts)
  const uniqueParts = nonTextParts.filter(
    part => !existingPartIds.has(artifactPartIdentity(part)),
  )
  if (uniqueParts.length === 0) return existing?.artifacts
  const inline: ArtifactData = {
    artifactId: syntheticArtifactId(messageId, existing?.artifacts),
    name: 'Response files',
    parts: uniqueParts,
    isSynthetic: true,
  }
  return deduplicateArtifactsByPart(
    mergeArtifacts(existing?.artifacts, inline, false),
  )
}

export function partsToReplacementArtifacts(
  rawParts: Record<string, unknown>[] | undefined,
  messageId: string,
  existingArtifacts?: ArtifactData[],
): ArtifactData[] {
  if (!rawParts || rawParts.length === 0) return []
  const nonTextParts = rawParts
    .map(artifactPartFromRaw)
    .filter((part): part is ArtifactPart => part !== undefined)
    .filter((part) => part.kind !== 'text' && isRenderableArtifactPart(part))
  if (nonTextParts.length === 0) return existingArtifacts ?? []
  const canonicalExisting = existingArtifacts?.filter(
    artifact => !artifact.isSynthetic,
  ) ?? []
  return deduplicateArtifactsByPart([
    ...canonicalExisting,
    {
      artifactId: syntheticArtifactId(messageId, existingArtifacts),
      name: 'Response files',
      parts: nonTextParts,
      isSynthetic: true,
    },
  ]) ?? []
}

export function sseArtifactDataFromPayload(
  artifact: Record<string, unknown>,
  isAppend: boolean,
  lastChunk: boolean | undefined,
): ArtifactData {
  const rawParts = Array.isArray(artifact.parts)
    ? (artifact.parts as Record<string, unknown>[])
    : []

  const normalized: ArtifactData = {
    artifactId: (artifact.artifact_id || artifact.artifactId) as string,
    name: artifact.name as string | undefined,
    parts: rawParts
      .map(artifactPartFromRaw)
      .filter((part): part is ArtifactPart => part !== undefined),
    isStreaming: isAppend ? !lastChunk : false,
  }
  return deduplicateArtifactsByPart([normalized])?.[0] ?? {
    ...normalized,
    parts: [],
  }
}
