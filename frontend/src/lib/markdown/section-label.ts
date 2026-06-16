const HEADING_LINE = /^\s{0,3}#{1,6}\s/

/** Max length for a line promoted from prose to a section heading. */
export const SECTION_LABEL_MAX_LENGTH = 120

/**
 * True when plain text looks like a short supervisor-style section label
 * (e.g. "TL;DR — Top 3", "Prioritized items (up to 6)"), not body prose.
 */
export function isSectionLabelText(text: string): boolean {
  const trimmed = text.trim()
  if (!trimmed) return false
  if (HEADING_LINE.test(trimmed)) return false
  if (/^\d+\.\s/.test(trimmed)) return false
  if (/^[-*]\s/.test(trimmed)) return false
  if (/^\*\*\d+\./.test(trimmed)) return false
  if (trimmed.length > SECTION_LABEL_MAX_LENGTH) return false
  return true
}
