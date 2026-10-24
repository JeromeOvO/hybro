import type { ArtifactData, ArtifactPart } from '@/stores/message-store/types'

function numbered(name: string, occurrence: number): string {
  const dot = name.lastIndexOf('.')
  return dot > 0
    ? `${name.slice(0, dot)} (${occurrence})${name.slice(dot)}`
    : `${name} (${occurrence})`
}

/**
 * Give repeated file names in one list a visible ordinal ("report (2).docx").
 *
 * Agents often name every output the same ("document.docx"); without this a
 * reader cannot tell which file is which. Display only: file ids and download
 * URLs are unchanged.
 */
export function withDistinctFileNames(artifacts: ArtifactData[]): ArtifactData[] {
  const seen = new Map<string, number>()
  const next = (name: string | undefined): string | undefined => {
    if (!name) return name
    const occurrence = (seen.get(name) ?? 0) + 1
    seen.set(name, occurrence)
    return occurrence === 1 ? name : numbered(name, occurrence)
  }
  return artifacts.map((artifact) => {
    const fileParts = artifact.parts.filter((part) => part.kind === 'file' && part.file?.name)
    // A single-file artifact is usually named after its file: number them together.
    const shared = fileParts.length === 1 && fileParts[0].file?.name === artifact.name
    const name = next(artifact.name)
    let changed = name !== artifact.name
    const parts = artifact.parts.map((part): ArtifactPart => {
      if (part.kind !== 'file' || !part.file?.name) return part
      const fileName = shared ? name : next(part.file.name)
      if (fileName === part.file.name) return part
      changed = true
      return { ...part, file: { ...part.file, name: fileName } }
    })
    return changed ? { ...artifact, name, parts } : artifact
  })
}
