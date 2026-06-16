/** Supervisor-style list rows that already carry their own `#N` marker in prose. */
export const HASH_NUMBERED_ITEM_TEXT = /^#\d+\b/

export function isHashNumberedListItemText(text: string): boolean {
  return HASH_NUMBERED_ITEM_TEXT.test(text.trim())
}
