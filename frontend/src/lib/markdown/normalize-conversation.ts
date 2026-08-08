import { normalizeAgentListMarkers } from '@/lib/markdown/normalize-agent-list-markers'
import { splitInlineOrderedListItems } from '@/lib/markdown/split-inline-ordered'

const PSEUDO_IMAGE_LINK_RE =
  /!\[([^\]]*)\]\((?:(?:attachment|file|local|image):\/\/[^)]*|(?!https?:\/\/|\/api\/|data:)[^)]+\.(?:png|jpe?g|gif|webp|svg))\)/gi

/** Strip unrenderable pseudo-protocol or relative filename image embeds so rehype-harden does not block them. */
export function stripPseudoImageLinks(content: string): string {
  return content.replace(PSEUDO_IMAGE_LINK_RE, '')
}

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
  const stripped = stripPseudoImageLinks(content)
  return splitInlineOrderedListItems(normalizeAgentListMarkers(stripped), options)
}

export { splitInlineOrderedListItems } from '@/lib/markdown/split-inline-ordered'
