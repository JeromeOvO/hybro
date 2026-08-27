import type { ArtifactData, ArtifactPart } from '@/stores/message-store/types'

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`
  if (value && typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`)
    return `{${entries.join(',')}}`
  }
  return JSON.stringify(value) ?? String(value)
}

/** Stable payload identity used to reconcile canonical and synthetic SSE artifacts. */
export function artifactPartIdentity(part: ArtifactPart): string {
  if (part.kind === 'file') {
    if (part.file?.fileId) return `file-id:${part.file.fileId}`
    if (part.file?.sha256) return `sha256:${part.file.sha256}`
    if (part.file?.uri) return `uri:${part.file.uri}`
  }
  if (part.kind === 'data') return `data:${canonicalJson(part.data ?? null)}`
  return `text:${part.text ?? ''}`
}

export function artifactPartIdentities(
  artifacts: ArtifactData[] | undefined,
): Set<string> {
  return new Set(
    artifacts?.flatMap(artifact => artifact.parts.map(artifactPartIdentity)) ?? [],
  )
}

function deduplicateParts(parts: ArtifactPart[]): ArtifactPart[] {
  const seen = new Set<string>()
  return parts.filter(part => {
    const identity = artifactPartIdentity(part)
    if (seen.has(identity)) return false
    seen.add(identity)
    return true
  })
}

/**
 * Remove duplicate parts while retaining canonical artifact ownership.
 * Canonical artifacts beat synthetic task/response wrappers regardless of
 * entity/buffer ordering; synthetic artifacts retain only unique remainder parts.
 */
export function deduplicateArtifactsByPart(
  artifacts: ArtifactData[] | undefined,
): ArtifactData[] | undefined {
  if (!artifacts?.length) return undefined

  const normalized = artifacts.map(artifact => ({
    ...artifact,
    parts: deduplicateParts(artifact.parts),
  }))
  const owner = new Map<string, { index: number; synthetic: boolean }>()
  normalized.forEach((artifact, index) => {
    artifact.parts.forEach(part => {
      const identity = artifactPartIdentity(part)
      const candidate = { index, synthetic: artifact.isSynthetic === true }
      const current = owner.get(identity)
      if (!current || (current.synthetic && !candidate.synthetic)) {
        owner.set(identity, candidate)
      }
    })
  })

  const result: ArtifactData[] = []
  normalized.forEach((artifact, index) => {
    const parts = artifact.parts.filter(
      part => owner.get(artifactPartIdentity(part))?.index === index,
    )
    if (parts.length > 0) {
      result.push(
        parts.length === artifact.parts.length ? artifact : { ...artifact, parts },
      )
    }
  })
  return result.length > 0 ? result : undefined
}

export function countDurableArtifactFiles(
  artifacts: ArtifactData[] | undefined,
): number {
  return artifacts?.reduce(
    (count, artifact) => count + artifact.parts.filter(
      part => part.kind === 'file' && Boolean(part.file?.fileId),
    ).length,
    0,
  ) ?? 0
}

function dataPartType(data: ArtifactPart['data']): unknown {
  return data && !Array.isArray(data) ? data.type : undefined
}

export function hasUnavailableArtifactOutput(
  artifacts: ArtifactData[] | undefined,
): boolean {
  return artifacts?.some(artifact => artifact.parts.some(
    part => part.kind === 'data' && dataPartType(part.data) === 'file_unavailable',
  )) ?? false
}

export function hasUsableArtifactOutput(
  artifacts: ArtifactData[] | undefined,
): boolean {
  return artifacts?.some(artifact => artifact.parts.some(part => {
    if (part.kind === 'text') return Boolean(part.text?.trim())
    if (part.kind === 'file') return Boolean(part.file?.fileId)
    if (part.kind === 'data') {
      return Boolean(
        part.data
        && Object.keys(part.data).length > 0
        && dataPartType(part.data) !== 'file_unavailable',
      )
    }
    return false
  })) ?? false
}
