import type { ArtifactData } from '@/stores/message-store/types'

function isTextOnlyArtifact(a: ArtifactData): boolean {
  return a.parts.length > 0 && a.parts.every(p => p.kind === 'text')
}

function artifactText(a: ArtifactData): string {
  return a.parts.map(p => p.text || '').join('')
}

function mergeTextParts(
  existingParts: ArtifactData['parts'],
  newParts: ArtifactData['parts'],
): ArtifactData['parts'] {
  const result = [...existingParts]
  for (const part of newParts) {
    const last = result[result.length - 1]
    if (part.kind === 'text' && last?.kind === 'text') {
      result[result.length - 1] = { ...last, text: (last.text || '') + (part.text || '') }
    } else {
      result.push(part)
    }
  }
  return result
}

/**
 * Merge an incoming artifact into a live streaming buffer list.
 * Differs from persisted mergeArtifacts: disjoint same-name text segments are
 * pushed (Hermes multi-paragraph), while prefix-related same-name segments
 * replace (legacy token-per-id agents).
 */
export function mergeStreamArtifacts(
  existing: ArtifactData[] | undefined,
  incoming: ArtifactData,
  append: boolean = false,
): ArtifactData[] {
  const list = existing ? [...existing] : []
  const idx = list.findIndex(a => a.artifactId === incoming.artifactId)

  if (idx >= 0) {
    if (append) {
      const merged = mergeTextParts([...list[idx].parts], incoming.parts)
      list[idx] = {
        ...list[idx],
        parts: merged,
        isStreaming: incoming.isStreaming ?? list[idx].isStreaming,
      }
    } else if (
      isTextOnlyArtifact(incoming) &&
      isTextOnlyArtifact(list[idx])
    ) {
      const existingText = artifactText(list[idx])
      const incomingText = artifactText(incoming)
      if (existingText.startsWith(incomingText) && existingText.length > incomingText.length) {
        return list
      }
      list[idx] = incoming
    } else {
      list[idx] = incoming
    }
    return list
  }

  if (incoming.name && isTextOnlyArtifact(incoming)) {
    const sameNameIdx = list.findIndex(a => a.name === incoming.name && isTextOnlyArtifact(a))
    if (sameNameIdx >= 0) {
      const existingText = artifactText(list[sameNameIdx])
      const incomingText = artifactText(incoming)
      if (incomingText.startsWith(existingText)) {
        list[sameNameIdx] = incoming
        return list
      }
      if (existingText.startsWith(incomingText)) {
        return list
      }
    }
  }

  list.push(incoming)
  return list
}
