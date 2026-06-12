import { normalizeAgentListMarkers } from '@/lib/markdown/normalize-agent-list-markers'
import { splitInlineOrderedListItems } from '@/lib/markdown/split-inline-ordered'

/**
 * Text-only pre-parse for conversation markdown before Streamdown render.
 * Agent list-marker cleanup, inline ordered splits (list-item lines only), and
 * bare `###` folding — inline splits deferred while streaming. List structure
 * fixes run at render time via remarkPlugins.
 */
export function preprocessConversationMarkdown(
  content: string,
  options?: { streaming?: boolean },
): string {
  return splitInlineOrderedListItems(normalizeAgentListMarkers(content), options)
}

export { splitInlineOrderedListItems } from '@/lib/markdown/split-inline-ordered'
