/** Run-on numbered marker inside prose (`foo 2. bar`). Pre-parse only; AST cannot recover this. */
const INLINE_ORDERED_MARKER = /([^\n])[ \t]+(\d+\.\s+)/
/** Supervisor-style items (`3. **#3 — … 4. **#4 — …`) — only split before the next hash marker. */
const HASH_NUMBERED_INLINE_MARKER = /([^\n])[ \t]+(\d+\.\s+\*\*#\d+)/
const HASH_NUMBERED_ITEM_LINE = /^\s*\d+\.\s+\*\*#\d+\b/
const HEADING_LINE = /^\s{0,3}#{1,6}\s/
const BARE_HEADING_LINE = /^\s{0,3}#{1,6}\s*$/
/** Lines that already begin a list item — safe to split run-on markers on. */
const LIST_ITEM_LINE = /^\s*\d+\.\s/

function splitRunOnOrderedMarkers(line: string, marker: RegExp): string {
  let result = line
  let prev = ''
  while (prev !== result) {
    prev = result
    result = result.replace(marker, '$1\n$2')
  }
  return result
}

function splitInlineOrderedOnLine(line: string): string {
  if (HASH_NUMBERED_ITEM_LINE.test(line)) {
    return splitRunOnOrderedMarkers(line, HASH_NUMBERED_INLINE_MARKER)
  }
  return splitRunOnOrderedMarkers(line, INLINE_ORDERED_MARKER)
}

/**
 * Fold a bare ATX heading marker (`###` on its own line) into the next
 * non-empty content line, so `###\n1. **Title**\n` becomes `### 1. **Title**\n`.
 *
 * Some agents emit the heading marker on a separate line from the title,
 * which remark parses as an empty `<h3>` followed by an `<ol>` (or paragraph).
 * That breaks list numbering downstream because each section starts a new
 * single-item ordered list at 1. Fixing it here keeps the section as a single
 * heading and avoids any speculative downstream reshaping.
 */
function foldBareHeadingMarkers(content: string): string {
  const lines = content.split('\n')
  const out: string[] = []
  let inFence = false

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i]
    if (line.trimStart().startsWith('```')) {
      inFence = !inFence
      out.push(line)
      continue
    }
    if (!inFence && BARE_HEADING_LINE.test(line) && i + 1 < lines.length) {
      const next = lines[i + 1]
      if (next.trim().length > 0 && !BARE_HEADING_LINE.test(next) && !HEADING_LINE.test(next)) {
        out.push(`${line.trimEnd()} ${next.trimStart()}`)
        i += 1
        continue
      }
    }
    out.push(line)
  }

  return out.join('\n')
}

/**
 * Break run-on numbered items (`1. foo 2. bar`) onto separate lines so markdown
 * parses them as a list. Skips fenced code blocks and ATX heading lines so a
 * heading like `#### 1. Title` is not split into an empty heading + list item.
 * Also folds bare heading markers (`###` alone on a line) into the next line.
 */
function shouldSplitInlineOrderedOnLine(line: string): boolean {
  return LIST_ITEM_LINE.test(line) || HASH_NUMBERED_ITEM_LINE.test(line)
}

export function splitInlineOrderedListItems(
  content: string,
  options?: { streaming?: boolean },
): string {
  const folded = foldBareHeadingMarkers(content)
  let inFence = false
  const lines: string[] = []

  for (const line of folded.split('\n')) {
    if (line.trimStart().startsWith('```')) {
      inFence = !inFence
      lines.push(line)
      continue
    }
    if (inFence || HEADING_LINE.test(line)) {
      lines.push(line)
      continue
    }
    if (options?.streaming || !shouldSplitInlineOrderedOnLine(line)) {
      lines.push(line)
      continue
    }
    lines.push(splitInlineOrderedOnLine(line))
  }

  return lines.join('\n')
}
