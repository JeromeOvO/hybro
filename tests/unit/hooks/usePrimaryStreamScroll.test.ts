import { describe, expect, it, beforeEach, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useRef } from 'react'
import { usePrimaryStreamScroll } from '@/hooks/usePrimaryStreamScroll'

class MockResizeObserver {
  observe() {}
  disconnect() {}
}

function createDom() {
  const scroll = document.createElement('div')
  Object.defineProperty(scroll, 'clientHeight', { value: 400, writable: true, configurable: true })
  Object.defineProperty(scroll, 'scrollTop', { value: 100, writable: true, configurable: true })
  scroll.scrollBy = vi.fn(function (this: HTMLElement, opts: ScrollToOptions) {
    Object.defineProperty(this, 'scrollTop', {
      value: this.scrollTop + (opts.top ?? 0),
      writable: true,
      configurable: true,
    })
  }) as typeof scroll.scrollBy
  Object.defineProperty(scroll, 'getBoundingClientRect', {
    value: () => ({
      top: 0,
      bottom: 400,
      height: 400,
      left: 0,
      right: 400,
      width: 400,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    }),
  })

  const primary = document.createElement('div')
  Object.defineProperty(primary, 'getBoundingClientRect', {
    value: () => ({
      top: 200,
      bottom: 450,
      height: 250,
      left: 0,
      right: 400,
      width: 400,
      x: 0,
      y: 200,
      toJSON: () => ({}),
    }),
  })

  const contentEnd = document.createElement('div')
  contentEnd.setAttribute('data-content-end', '')
  Object.defineProperty(contentEnd, 'getBoundingClientRect', {
    value: () => ({
      top: 380,
      bottom: 380,
      height: 0,
      left: 0,
      right: 400,
      width: 400,
      x: 0,
      y: 380,
      toJSON: () => ({}),
    }),
  })

  scroll.appendChild(primary)
  scroll.appendChild(contentEnd)
  document.body.appendChild(scroll)

  return { scroll, primary }
}

describe('usePrimaryStreamScroll', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    vi.stubGlobal('ResizeObserver', MockResizeObserver)
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
      cb(0)
      return 1
    })
  })

  it('does not tail-follow while focus mode is active', () => {
    const { scroll, primary } = createDom()
    const focusModeRef = { current: true }

    renderHook(() => {
      const scrollRef = useRef(scroll)
      const primarySurfaceRef = useRef(primary)
      const userPausedRef = useRef(false)
      const programmaticScrollRef = useRef(false)
      return usePrimaryStreamScroll({
        scrollRef,
        primarySurfaceRef,
        primaryStreamMessageId: 'agent-1',
        userPausedRef,
        programmaticScrollRef,
        focusModeRef,
      })
    })

    expect(scroll.scrollBy).not.toHaveBeenCalled()
  })

  it('tail-follows near content end when focus mode is inactive', () => {
    const { scroll, primary } = createDom()
    const focusModeRef = { current: false }

    renderHook(() => {
      const scrollRef = useRef(scroll)
      const primarySurfaceRef = useRef(primary)
      const userPausedRef = useRef(false)
      const programmaticScrollRef = useRef(false)
      return usePrimaryStreamScroll({
        scrollRef,
        primarySurfaceRef,
        primaryStreamMessageId: 'agent-1',
        userPausedRef,
        programmaticScrollRef,
        focusModeRef,
      })
    })

    expect(scroll.scrollBy).toHaveBeenCalled()
  })
})
