import { unified } from 'unified'
import remarkGfm from 'remark-gfm'
import remarkParse from 'remark-parse'
import remarkStringify from 'remark-stringify'
import { remarkConversationLists } from '@/lib/markdown/remark-conversation-lists'
import { splitInlineOrderedListItems } from '@/lib/markdown/split-inline-ordered'

const processor = unified()
  .use(remarkParse)
  .use(remarkGfm)
  .use(remarkConversationLists)
  .use(remarkStringify, {
    bullet: '-',
    bulletOrdered: '.',
    incrementListMarker: true,
    listItemIndent: 'one',
    tightDefinitions: true,
  })

function stripTrailingNewline(value: string): string {
  return value.endsWith('\n') ? value.slice(0, -1) : value
}

/** Collapse extra blank lines introduced by remark-stringify around headings. */
function compactConversationMarkdown(value: string): string {
  return fixNestedOrderedDelimiters(
    value
      .replace(/\n{3,}/g, '\n\n')
      .replace(/\n\n(#{1,6}\s)/g, '\n$1')
      .replace(/(#{1,6}[^\n]*)\n\n+(?!\n)/g, '$1\n')
      .replace(/([^\n#])\n\n(\d+\.\s)/g, '$1\n$2')
      .replace(/((?:^|\n)\d+\. .*(?:\n\d+\. .*)*)\n\n(?![#*\d-])/g, '$1\n'),
  )
}

/** remark-stringify may emit `1)` for nested ordered markers; normalize to `N.`. */
function fixNestedOrderedDelimiters(markdown: string): string {
  const counters: number[] = []
  return markdown
    .split('\n')
    .map((line) => {
      const match = /^(\s*)(\d+)([\).])\s+(.*)$/.exec(line)
      if (!match) return line
      const [, indent, numText, delimiter, body] = match
      const level = indent.length > 0 ? Math.ceil(indent.length / 3) : 0
      while (counters.length <= level) counters.push(0)
      counters.length = level + 1
      if (delimiter === '.') {
        counters[level] = parseInt(numText, 10)
        return line
      }
      counters[level] += 1
      return `${indent}${counters[level]}. ${body}`
    })
    .join('\n')
}

/** Normalize agent/supervisor markdown via remark (inline split pre-parse, list fixes on mdast). */
export function normalizeConversationMarkdown(
  content: string,
  options?: { streaming?: boolean },
): string {
  const split = splitInlineOrderedListItems(content)
  if (options?.streaming) return split
  try {
    const result = processor.processSync(split)
    return compactConversationMarkdown(stripTrailingNewline(String(result)))
  } catch {
    return split
  }
}

export { splitInlineOrderedListItems } from '@/lib/markdown/split-inline-ordered'

/** @deprecated Use normalizeConversationMarkdown — kept for tests during remark migration. */
export function normalizeOrderedListMarkers(content: string): string {
  return normalizeConversationMarkdown(content)
}

/** @deprecated Use normalizeConversationMarkdown — kept for tests during remark migration. */
export function indentSubBulletsUnderOrderedItems(content: string): string {
  return normalizeConversationMarkdown(content)
}
