import type { ConversationScrollSnapshot } from '@/lib/conversation/conversation-scroll'
import { clampScrollTop } from '@/lib/conversation/conversation-scroll'

export function isNearScrollBottom(element: HTMLElement, threshold = 48): boolean {
  return element.scrollHeight - element.scrollTop - element.clientHeight <= threshold
}

export function scrollElementToBottom(element: HTMLElement): void {
  element.scrollTop = element.scrollHeight
}

export function scrollElementToTop(element: HTMLElement): void {
  element.scrollTop = 0
}

export type DetailPaneScrollApplyResult = 'default-top' | 'restored-bottom' | 'restored-position'

/** First open → top; revisit → restore saved position or bottom when pinned. */
export function applyDetailPaneScrollSnapshot(
  element: HTMLElement,
  snapshot: ConversationScrollSnapshot | undefined,
): DetailPaneScrollApplyResult {
  if (!snapshot) {
    scrollElementToTop(element)
    return 'default-top'
  }

  if (snapshot.atBottom) {
    element.scrollTo({ top: element.scrollHeight, behavior: 'auto' })
    return 'restored-bottom'
  }

  element.scrollTop = clampScrollTop(element, snapshot.scrollTop)
  return 'restored-position'
}

function scrollHeightReady(element: HTMLElement): boolean {
  return element.scrollHeight > element.clientHeight || element.scrollTop === 0
}

export function restoreDetailPaneScrollWithRetry(
  element: HTMLElement,
  snapshot: ConversationScrollSnapshot | undefined,
  onApplied?: (result: DetailPaneScrollApplyResult) => void,
  maxRetries = 8,
): void {
  let retries = 0

  const finish = (result: DetailPaneScrollApplyResult) => {
    onApplied?.(result)
  }

  const attempt = () => {
    const result = applyDetailPaneScrollSnapshot(element, snapshot)

    if (!snapshot || snapshot.atBottom || result === 'default-top') {
      finish(result)
      return
    }

    const targetTop = clampScrollTop(element, snapshot.scrollTop)
    const heightReady = scrollHeightReady(element)
    const aligned = Math.abs(element.scrollTop - targetTop) <= 2

    if ((heightReady && aligned) || retries >= maxRetries) {
      finish(result)
      return
    }

    retries += 1
    requestAnimationFrame(attempt)
  }

  attempt()
}
