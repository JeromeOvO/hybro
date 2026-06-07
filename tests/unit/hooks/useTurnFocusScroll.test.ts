import { describe, expect, it, beforeEach, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useRef } from 'react'
import { useTurnFocusScroll } from '@/hooks/useTurnFocusScroll'
import { FOCUS_SCROLL_MIN_SPACER_PX } from '@/lib/conversation/focus-scroll'

class MockResizeObserver {
  static instances: MockResizeObserver[] = []
  private callback: ResizeObserverCallback

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback
    MockResizeObserver.instances.push(this)
  }

  observe() {}
  disconnect() {}
  trigger() {
    this.callback([], this as unknown as ResizeObserver)
  }
}

function createDom(userMessageId: string) {
  const scroll = document.createElement('div')
  Object.defineProperty(scroll, 'clientHeight', { value: 600, writable: true, configurable: true })
  Object.defineProperty(scroll, 'scrollTop', { value: 0, writable: true, configurable: true })
  scroll.scrollTo = vi.fn(function (this: HTMLElement, opts: ScrollToOptions) {
    Object.defineProperty(this, 'scrollTop', { value: opts.top ?? 0, writable: true, configurable: true })
  }) as typeof scroll.scrollTo
  Object.defineProperty(scroll, 'getBoundingClientRect', {
    value: () => ({ top: 0, bottom: 600, height: 600, left: 0, right: 400, width: 400, x: 0, y: 0, toJSON: () => ({}) }),
  })

  const frame = document.createElement('div')
  const userEl = document.createElement('div')
  userEl.setAttribute('data-message-id', userMessageId)
  Object.defineProperty(userEl, 'getBoundingClientRect', {
    value: () => ({ top: 400, bottom: 440, height: 40, left: 0, right: 400, width: 400, x: 0, y: 400, toJSON: () => ({}) }),
  })

  const contentEnd = document.createElement('div')
  contentEnd.setAttribute('data-content-end', '')
  Object.defineProperty(contentEnd, 'getBoundingClientRect', {
    value: () => ({ top: 500, bottom: 500, height: 0, left: 0, right: 400, width: 400, x: 0, y: 500, toJSON: () => ({}) }),
  })

  frame.appendChild(userEl)
  frame.appendChild(contentEnd)
  scroll.appendChild(frame)
  document.body.appendChild(scroll)

  return { scroll, frame, userEl, contentEnd }
}

describe('useTurnFocusScroll', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    MockResizeObserver.instances = []
    vi.stubGlobal('ResizeObserver', MockResizeObserver)
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
      cb(0)
      return 1
    })
  })

  it('focuses the last user message when localSendSeq changes', () => {
    const { scroll } = createDom('user-1')

    const { rerender } = renderHook(
      ({ localSendSeq, turnLive }) => {
        const scrollRef = useRef(scroll)
        const frameRef = useRef(scroll.firstElementChild as HTMLDivElement)
        const userPausedRef = useRef(false)
        const programmaticScrollRef = useRef(false)
        return useTurnFocusScroll({
          scrollRef,
          frameRef,
          lastUserMessageId: 'user-1',
          localSendSeq,
          turnLive,
          contentVersion: 0,
          userPausedRef,
          programmaticScrollRef,
        })
      },
      { initialProps: { localSendSeq: 0, turnLive: false } },
    )

    rerender({ localSendSeq: 1, turnLive: true })

    expect(scroll.scrollTo).toHaveBeenCalled()
    expect(scroll.scrollTop).toBeGreaterThan(0)
  })

  it('returns minimum spacer when turn is not live', () => {
    const { scroll } = createDom('user-1')

    const { result } = renderHook(() => {
      const scrollRef = useRef(scroll)
      const frameRef = useRef(scroll.firstElementChild as HTMLDivElement)
      const userPausedRef = useRef(false)
      const programmaticScrollRef = useRef(false)
      return useTurnFocusScroll({
        scrollRef,
        frameRef,
        lastUserMessageId: 'user-1',
        localSendSeq: 0,
        turnLive: false,
        contentVersion: 0,
        userPausedRef,
        programmaticScrollRef,
      })
    })

    expect(result.current.spacerHeight).toBe(FOCUS_SCROLL_MIN_SPACER_PX)
  })

  it('expands spacer while turn is live', () => {
    const { scroll } = createDom('user-1')

    const { result } = renderHook(() => {
      const scrollRef = useRef(scroll)
      const frameRef = useRef(scroll.firstElementChild as HTMLDivElement)
      const userPausedRef = useRef(false)
      const programmaticScrollRef = useRef(false)
      return useTurnFocusScroll({
        scrollRef,
        frameRef,
        lastUserMessageId: 'user-1',
        localSendSeq: 1,
        turnLive: true,
        contentVersion: 1,
        userPausedRef,
        programmaticScrollRef,
      })
    })

    expect(result.current.spacerHeight).toBeGreaterThan(FOCUS_SCROLL_MIN_SPACER_PX)
  })
})
