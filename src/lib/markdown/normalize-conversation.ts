import { splitInlineOrderedListItems } from '@/lib/markdown/split-inline-ordered'

/**
 * Text-only pre-parse for conversation markdown before Streamdown render.
 * Inline ordered splits (list-item lines only) and bare `###` folding — deferred
 * while streaming. List structure fixes run at render time via remarkPlugins.
 */
export function preprocessConversationMarkdown(
  content: string,
  options?: { streaming?: boolean },
): string {
  return splitInlineOrderedListItems(content, options)
}

export { splitInlineOrderedListItems } from '@/lib/markdown/split-inline-ordered'
