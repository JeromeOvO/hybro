import { describe, expect, it } from 'vitest'
import {
  isNearScrollBottom,
  scrollElementToBottom,
  scrollElementToTop,
} from '@/lib/streaming/detail-pane-scroll'

describe('detail pane scroll helpers', () => {
  it('isNearScrollBottom detects proximity to bottom', () => {
    const element = {
      scrollHeight: 1000,
      clientHeight: 200,
      scrollTop: 760,
    } as HTMLElement
    expect(isNearScrollBottom(element, 48)).toBe(true)

    element.scrollTop = 100
    expect(isNearScrollBottom(element, 48)).toBe(false)
  })

  it('scrollElementToBottom and scrollElementToTop set scrollTop', () => {
    const element = {
      scrollHeight: 500,
      scrollTop: 0,
    } as HTMLElement

    scrollElementToBottom(element)
    expect(element.scrollTop).toBe(500)

    scrollElementToTop(element)
    expect(element.scrollTop).toBe(0)
  })
})
