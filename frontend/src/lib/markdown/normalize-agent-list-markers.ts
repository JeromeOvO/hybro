/** Unicode bullets models emit instead of markdown `-`. */
const UNICODE_BULLET_LINE = /^(\s*)[•·]\s/
/** Wrong ordered marker on a sub-field, e.g. `1. • Summary:` or `2. · Paywall:`. */
const ORDERED_UNICODE_BULLET_LINE = /^(\s*)\d+\.\s*[•·]\s/

/**
 * Rewrite common agent list-marker mistakes into GFM `-` bullets before parse.
 * Skips fenced code blocks.
 */
export function normalizeAgentListMarkers(content: string): string {
  let inFence = false
  const lines: string[] = []

  for (const line of content.split('\n')) {
    if (line.trimStart().startsWith('```')) {
      inFence = !inFence
      lines.push(line)
      continue
    }
    if (inFence) {
      lines.push(line)
      continue
    }
    if (ORDERED_UNICODE_BULLET_LINE.test(line)) {
      lines.push(line.replace(ORDERED_UNICODE_BULLET_LINE, '$1- '))
      continue
    }
    if (UNICODE_BULLET_LINE.test(line)) {
      lines.push(line.replace(UNICODE_BULLET_LINE, '$1- '))
      continue
    }
    lines.push(line)
  }

  return lines.join('\n')
}
