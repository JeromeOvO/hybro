import type { ArtifactData } from '@/stores/message-store/types'

function isTextOnlyArtifact(a: ArtifactData): boolean {
  return a.parts.length > 0 && a.parts.every(p => p.kind === 'text')
}

function artifactText(a: ArtifactData): string {
  return a.parts.map(p => p.text || '').join('')
}

function isPunctuationOnlySnapshot(text: string): boolean {
  const trimmed = text.trim()
  return trimmed.length > 0 && trimmed.length <= 3 && /^[.!?,;:]+$/.test(trimmed)
}

/**
 * Merge two text snapshots for the same artifactId when append=false.
 * Handles cumulative snapshots, stale prefixes, sliding-window overlap, and
 * mislabeled token deltas (e.g. "news." continuing "...agent news.").
 */
export function mergeStreamingTextSnapshots(existing: string, incoming: string): string {
  if (!incoming) return existing
  if (!existing) return incoming
  if (isPunctuationOnlySnapshot(incoming) && existing.length > incoming.length) return existing
  if (incoming.startsWith(existing)) return incoming
  if (existing.startsWith(incoming)) return existing

  let overlap = 0
  const maxOverlap = Math.min(existing.length, incoming.length)
  for (let size = maxOverlap; size > 0; size--) {
    if (existing.endsWith(incoming.slice(0, size))) {
      overlap = size
      break
    }
  }
  if (overlap > 0) {
    return existing + incoming.slice(overlap)
  }

  if (existing.includes(incoming)) return existing
  if (incoming.includes(existing)) return incoming

  // Short unrelated replacement (e.g. Draft → Final)
  if (existing.length <= 48 && incoming.length <= 48) {
    return incoming.length >= existing.length ? incoming : existing + incoming
  }

  // Disjoint streaming window: stitch after sentence punctuation, never clobber
  if (incoming.startsWith('.')) {
    const rest = incoming.slice(1).replace(/^\s+/, ' ')
    return existing.endsWith(' ') || existing.endsWith('\n') ? existing + incoming : existing + rest
  }

  return existing + incoming
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
      const mergedText = mergeStreamingTextSnapshots(existingText, incomingText)
      if (mergedText === existingText) return list
      list[idx] = {
        ...list[idx],
        parts: [{ kind: 'text', text: mergedText }],
        isStreaming: incoming.isStreaming ?? list[idx].isStreaming,
      }
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
