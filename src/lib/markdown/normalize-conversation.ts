import { splitInlineOrderedListItems } from '@/lib/markdown/split-inline-ordered'

/**
 * Text-only pre-parse for conversation markdown before Streamdown render.
 * Inline ordered splits and bare `###` folding only — list structure fixes
 * run at render time via remarkPlugins on the mdast tree.
 */
export function preprocessConversationMarkdown(
  content: string,
  options?: { streaming?: boolean },
): string {
  return splitInlineOrderedListItems(content, options)
}

export { splitInlineOrderedListItems } from '@/lib/markdown/split-inline-ordered'
