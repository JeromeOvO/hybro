import { describe, expect, it, vi } from 'vitest'
import {
  computeFocusSpacerHeight,
  FOCUS_SCROLL_MIN_SPACER_PX,
  FOCUS_SCROLL_TOP_OFFSET_PX,
  scrollUserMessageToFocus,
} from '@/lib/conversation/focus-scroll'

function mockRect(top: number, height = 40) {
  return { top, bottom: top + height, height, left: 0, right: 100, width: 100, x: 0, y: top, toJSON: () => ({}) }
}

describe('focus-scroll', () => {
  it('computes spacer height from anchor to content end', () => {
    const container = {
      clientHeight: 600,
      getBoundingClientRect: () => mockRect(0, 600),
    } as HTMLElement

    const anchor = {
      getBoundingClientRect: () => mockRect(100),
    } as HTMLElement

    const contentEnd = {
      getBoundingClientRect: () => mockRect(300),
    } as HTMLElement

    expect(computeFocusSpacerHeight(container, anchor, contentEnd)).toBe(400)
  })

  it('never returns less than the minimum spacer', () => {
    const container = {
      clientHeight: 600,
      getBoundingClientRect: () => mockRect(0, 600),
    } as HTMLElement

    const anchor = {
      getBoundingClientRect: () => mockRect(100),
    } as HTMLElement

    const contentEnd = {
      getBoundingClientRect: () => mockRect(500),
    } as HTMLElement

    expect(computeFocusSpacerHeight(container, anchor, contentEnd)).toBe(FOCUS_SCROLL_MIN_SPACER_PX)
  })

  it('scrolls user message near the top of the container', () => {
    const container = {
      scrollTop: 200,
      scrollTo: vi.fn(),
      getBoundingClientRect: () => mockRect(50),
    } as unknown as HTMLElement

    const userMessage = {
      getBoundingClientRect: () => mockRect(250),
    } as HTMLElement

    scrollUserMessageToFocus(container, userMessage)

    expect(container.scrollTo).toHaveBeenCalledWith({
      top: 200 + (250 - 50 - FOCUS_SCROLL_TOP_OFFSET_PX),
      behavior: 'auto',
    })
  })
})
