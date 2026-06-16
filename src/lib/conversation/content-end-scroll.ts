/** Keep in sync with conversation feed bottom-follow threshold. */
export const CONTENT_END_BOTTOM_THRESHOLD_PX = 100

export function findContentEnd(container: HTMLElement): HTMLElement | null {
  return container.querySelector('[data-content-end]') as HTMLElement | null
}

export function contentEndScrollTop(container: HTMLElement): number {
  const contentEnd = findContentEnd(container)
  if (!contentEnd) {
    return Math.max(0, container.scrollHeight - container.clientHeight)
  }

  const offset = contentEnd.getBoundingClientRect().top - container.getBoundingClientRect().top
  return Math.max(0, container.scrollTop + offset - container.clientHeight)
}

export function isNearContentEnd(
  container: HTMLElement,
  threshold = CONTENT_END_BOTTOM_THRESHOLD_PX,
): boolean {
  const contentEnd = findContentEnd(container)
  if (!contentEnd) {
    return container.scrollHeight - container.scrollTop - container.clientHeight < threshold
  }

  const offset = contentEnd.getBoundingClientRect().top - container.getBoundingClientRect().top
  return offset - container.clientHeight <= threshold
}

export function scrollToContentEnd(
  container: HTMLElement,
  behavior: ScrollBehavior = 'auto',
): void {
  container.scrollTo({ top: contentEndScrollTop(container), behavior })
}
