// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import {
  contentEndScrollTop,
  isNearContentEnd,
  scrollToContentEnd,
} from '@/lib/conversation/content-end-scroll'

function createScrollContainer(options: {
  scrollHeight?: number
  clientHeight?: number
  scrollTop?: number
  contentEndTop?: number
  includeContentEnd?: boolean
}) {
  const {
    scrollHeight = 1000,
    clientHeight = 400,
    scrollTop = 0,
    contentEndTop = 600,
    includeContentEnd = true,
  } = options

  const container = document.createElement('div')
  Object.defineProperty(container, 'scrollHeight', { value: scrollHeight, writable: true, configurable: true })
  Object.defineProperty(container, 'clientHeight', { value: clientHeight, writable: true, configurable: true })
  Object.defineProperty(container, 'scrollTop', { value: scrollTop, writable: true, configurable: true })

  container.getBoundingClientRect = () => ({
    top: 0,
    left: 0,
    right: 0,
    bottom: clientHeight,
    width: 0,
    height: clientHeight,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  })

  if (includeContentEnd) {
    const contentEnd = document.createElement('div')
    contentEnd.setAttribute('data-content-end', '')
    contentEnd.getBoundingClientRect = () => ({
      top: contentEndTop - container.scrollTop,
      left: 0,
      right: 0,
      bottom: contentEndTop - container.scrollTop,
      width: 0,
      height: 0,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    })
    container.appendChild(contentEnd)
  }

  container.scrollTo = vi.fn(function (this: HTMLElement, opts: ScrollToOptions) {
    const maxTop = Math.max(0, this.scrollHeight - this.clientHeight)
    Object.defineProperty(this, 'scrollTop', {
      value: Math.min(Math.max(0, opts.top ?? 0), maxTop),
      writable: true,
      configurable: true,
    })
  }) as typeof container.scrollTo

  return container
}

describe('content-end-scroll', () => {
  it('computes scrollTop that aligns content-end with the viewport bottom', () => {
    const container = createScrollContainer({ contentEndTop: 600, clientHeight: 400 })
    expect(contentEndScrollTop(container)).toBe(200)
  })

  it('detects when content-end is near the viewport bottom', () => {
    const container = createScrollContainer({ contentEndTop: 600, scrollTop: 200, clientHeight: 400 })
    expect(isNearContentEnd(container)).toBe(true)

    const scrolledUp = createScrollContainer({ contentEndTop: 600, scrollTop: 0, clientHeight: 400 })
    expect(isNearContentEnd(scrolledUp)).toBe(false)
  })

  it('scrolls to content-end instead of the full scroll height', () => {
    const container = createScrollContainer({ contentEndTop: 600, clientHeight: 400, scrollHeight: 1000 })
    scrollToContentEnd(container, 'smooth')
    expect(container.scrollTop).toBe(200)
    expect(container.scrollTo).toHaveBeenCalledWith({ top: 200, behavior: 'smooth' })
  })

  it('falls back to scrollHeight when content-end is missing', () => {
    const container = createScrollContainer({ includeContentEnd: false, scrollHeight: 1000, clientHeight: 400 })
    scrollToContentEnd(container)
    expect(container.scrollTop).toBe(600)
  })
})
