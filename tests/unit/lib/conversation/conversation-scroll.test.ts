// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import {
  applyConversationScrollSnapshot,
  clampScrollTop,
  readConversationScrollSnapshot,
  restoreConversationScrollWithRetry,
  shouldSkipInitialHydrationScrollRestore,
} from '@/lib/conversation/conversation-scroll'

function createScrollElement(initialHeight = 1000) {
  const element = document.createElement('div')
  Object.defineProperty(element, 'clientHeight', { value: 400, writable: true, configurable: true })
  Object.defineProperty(element, 'scrollHeight', { value: initialHeight, writable: true, configurable: true })
  Object.defineProperty(element, 'scrollTop', { value: 0, writable: true, configurable: true })
  element.scrollTo = vi.fn(function (this: HTMLElement, opts: ScrollToOptions) {
    const maxTop = Math.max(0, this.scrollHeight - this.clientHeight)
    Object.defineProperty(this, 'scrollTop', {
      value: Math.min(Math.max(0, opts.top ?? 0), maxTop),
      writable: true,
      configurable: true,
    })
  }) as typeof element.scrollTo
  return element
}

describe('conversation-scroll', () => {
  it('clamps scrollTop to valid range', () => {
    const element = createScrollElement(1000)
    expect(clampScrollTop(element, 900)).toBe(600)
    expect(clampScrollTop(element, -10)).toBe(0)
  })

  it('reads atBottom snapshot', () => {
    const element = createScrollElement(1000)
    element.scrollTop = 580
    expect(readConversationScrollSnapshot(element)).toEqual({ scrollTop: 580, atBottom: true })
  })

  it('scrolls to bottom when no snapshot exists', () => {
    const element = createScrollElement(1000)
    expect(applyConversationScrollSnapshot(element, undefined)).toBe('default-bottom')
    expect(element.scrollTop).toBe(600)
  })

  it('restores saved scroll position', () => {
    const element = createScrollElement(1000)
    expect(
      applyConversationScrollSnapshot(element, { scrollTop: 220, atBottom: false }),
    ).toBe('restored-position')
    expect(element.scrollTop).toBe(220)
  })

  it('restores bottom when snapshot was at bottom', () => {
    const element = createScrollElement(1000)
    expect(
      applyConversationScrollSnapshot(element, { scrollTop: 580, atBottom: true }),
    ).toBe('restored-bottom')
    expect(element.scrollTop).toBe(600)
  })

  it('retries position restore until layout settles', () => {
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
      cb(0)
      return 1
    })

    const element = createScrollElement(400)
    Object.defineProperty(element, 'scrollHeight', {
      value: 1000,
      writable: true,
      configurable: true,
    })

    const onApplied = vi.fn()
    restoreConversationScrollWithRetry(
      element,
      { scrollTop: 220, atBottom: false },
      onApplied,
    )

    expect(onApplied).toHaveBeenCalledTimes(1)
    expect(onApplied).toHaveBeenCalledWith('restored-position')
    expect(element.scrollTop).toBe(220)
  })

  it('skips hydration scroll restore after a local send', () => {
    expect(shouldSkipInitialHydrationScrollRestore(0)).toBe(false)
    expect(shouldSkipInitialHydrationScrollRestore(1)).toBe(true)
  })
})
