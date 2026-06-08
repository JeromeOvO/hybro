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

/** @deprecated Use preprocessConversationMarkdown for render; kept for tests. */
export function normalizeConversationMarkdown(
  content: string,
  options?: { streaming?: boolean },
): string {
  return preprocessConversationMarkdown(content, options)
}

export { splitInlineOrderedListItems } from '@/lib/markdown/split-inline-ordered'

/** @deprecated Use preprocessConversationMarkdown — kept for tests during remark migration. */
export function normalizeOrderedListMarkers(content: string): string {
  return preprocessConversationMarkdown(content)
}

/** @deprecated Use preprocessConversationMarkdown — kept for tests during remark migration. */
export function indentSubBulletsUnderOrderedItems(content: string): string {
  return preprocessConversationMarkdown(content)
}
