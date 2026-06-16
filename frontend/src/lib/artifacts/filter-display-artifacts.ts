import type { ArtifactData } from '@/stores/message-store/types'

export function textOnlyArtifactContent(artifact: ArtifactData): string | null {
  if (artifact.parts.length === 0) return null
  if (!artifact.parts.every((part) => part.kind === 'text')) return null
  return artifact.parts.map((part) => part.text ?? '').join('').trim()
}

/** Hide text-only artifacts that duplicate the main message body. */
export function filterDuplicateTextArtifacts(
  artifacts: ArtifactData[] | undefined,
  mainContent: string,
): ArtifactData[] | undefined {
  if (!artifacts?.length) return artifacts
  const normalizedContent = mainContent.trim()
  if (!normalizedContent) return artifacts
  return artifacts.filter(
    (artifact) => textOnlyArtifactContent(artifact) !== normalizedContent,
  )
}
