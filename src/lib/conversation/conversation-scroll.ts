export interface ConversationScrollSnapshot {
  scrollTop: number
  atBottom: boolean
}

export function clampScrollTop(element: HTMLElement, scrollTop: number): number {
  const maxTop = Math.max(0, element.scrollHeight - element.clientHeight)
  return Math.min(Math.max(0, scrollTop), maxTop)
}

export function readConversationScrollSnapshot(element: HTMLElement): ConversationScrollSnapshot {
  const metrics = {
    scrollHeight: element.scrollHeight,
    scrollTop: element.scrollTop,
    clientHeight: element.clientHeight,
  }
  const atBottom = metrics.scrollHeight - metrics.scrollTop - metrics.clientHeight < 100
  return { scrollTop: metrics.scrollTop, atBottom }
}

export type ConversationScrollApplyResult = 'default-bottom' | 'restored-bottom' | 'restored-position'

function scrollHeightReady(element: HTMLElement): boolean {
  return element.scrollHeight > element.clientHeight || element.scrollTop === 0
}

/** Apply snapshot after hydration; retries until layout height is stable enough. */
export function restoreConversationScrollWithRetry(
  element: HTMLElement,
  snapshot: ConversationScrollSnapshot | undefined,
  onApplied?: (result: ConversationScrollApplyResult) => void,
  maxRetries = 8,
): void {
  let retries = 0

  const finish = (result: ConversationScrollApplyResult) => {
    onApplied?.(result)
  }

  const attempt = () => {
    const result = applyConversationScrollSnapshot(element, snapshot)

    if (!snapshot || snapshot.atBottom) {
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

/** Apply a saved snapshot, or scroll to bottom when none exists. */
export function applyConversationScrollSnapshot(
  element: HTMLElement,
  snapshot: ConversationScrollSnapshot | undefined,
): ConversationScrollApplyResult {
  if (!snapshot) {
    element.scrollTo({ top: element.scrollHeight, behavior: 'auto' })
    return 'default-bottom'
  }

  if (snapshot.atBottom) {
    element.scrollTo({ top: element.scrollHeight, behavior: 'auto' })
    return 'restored-bottom'
  }

  element.scrollTop = clampScrollTop(element, snapshot.scrollTop)
  return 'restored-position'
}
