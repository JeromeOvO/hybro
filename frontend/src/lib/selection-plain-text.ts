/** Block elements that should separate with a newline when extracting selection text. */
const BLOCK_TAGS = new Set([
  'P',
  'DIV',
  'LI',
  'UL',
  'OL',
  'H1',
  'H2',
  'H3',
  'H4',
  'H5',
  'H6',
  'PRE',
  'BLOCKQUOTE',
  'TR',
  'TABLE',
  'SECTION',
  'ARTICLE',
])

function visitNode(node: Node, append: (text: string) => void): void {
  if (node.nodeType === Node.TEXT_NODE) {
    append(node.textContent || '')
    return
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return

  const element = node as HTMLElement
  if (element.tagName === 'BR') {
    append('\n')
    return
  }

  const isBlock = BLOCK_TAGS.has(element.tagName)
  let lengthBefore = 0
  const trackingAppend = (text: string) => {
    lengthBefore += text.length
    append(text)
  }

  const startLen = lengthBefore
  element.childNodes.forEach((child) => visitNode(child, trackingAppend))
  if (isBlock && lengthBefore > startLen) {
    append('\n')
  }
}

/** Extract plain text from a DOM range, preserving line breaks from block elements. */
export function getPlainTextFromRange(range: Range): string {
  const fragment = range.cloneContents()
  let plainText = ''
  const append = (text: string) => {
    plainText += text
  }
  fragment.childNodes.forEach((node) => visitNode(node, append))
  return plainText.replace(/\n{3,}/g, '\n\n').trim()
}

/**
 * Plain text for the current window selection, optionally scoped to a container.
 * Returns null when there is no meaningful selection.
 */
export function getSelectionPlainText(container?: HTMLElement | null): string | null {
  const selection = window.getSelection()
  if (!selection || selection.isCollapsed || !selection.rangeCount) return null

  const range = selection.getRangeAt(0)
  if (container && !container.contains(range.commonAncestorContainer)) return null

  const text = getPlainTextFromRange(range).trim()
  return text || null
}
