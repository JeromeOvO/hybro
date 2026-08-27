import { normalizeAgentListMarkers } from '@/lib/markdown/normalize-agent-list-markers'
import { splitInlineOrderedListItems } from '@/lib/markdown/split-inline-ordered'

const PSEUDO_IMAGE_LINK_RE =
  /!\[([^\]]*)\]\((?:(?:attachment|file|local|image):\/\/[^)]*|(?!https?:\/\/|\/api\/|data:)[^)]+\.(?:png|jpe?g|gif|webp|svg))\)/gi
const SANDBOX_ROOM_FILE_IMAGE_RE =
  /!\[([^\]]*)\]\(sandbox:\/api\/v1\/files\/[A-Za-z0-9_-]+\/content\)/gi
const SANDBOX_ROOM_FILE_LINK_RE =
  /\[([^\]]+)\]\(sandbox:\/api\/v1\/files\/[A-Za-z0-9_-]+\/content\)/gi

/** Strip unrenderable pseudo-protocol or relative filename image embeds so rehype-harden does not block them. */
export function stripPseudoImageLinks(content: string): string {
  return content.replace(PSEUDO_IMAGE_LINK_RE, '')
}

/**
 * Preserve labels but remove model-authored sandbox destinations. Canonical
 * room artifacts are rendered from authenticated descriptors instead of an
 * untrusted textual file id, which can be stale or hallucinated.
 */
export function stripSandboxRoomFileLinks(content: string): string {
  return content
    .replace(SANDBOX_ROOM_FILE_IMAGE_RE, '$1')
    .replace(SANDBOX_ROOM_FILE_LINK_RE, '$1')
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
  const stripped = stripPseudoImageLinks(stripSandboxRoomFileLinks(content))
  return splitInlineOrderedListItems(normalizeAgentListMarkers(stripped), options)
}

export { splitInlineOrderedListItems } from '@/lib/markdown/split-inline-ordered'
