import { describe, expect, it, beforeEach, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useRef } from 'react'
import { useScrollUserMessageOnSend } from '@/hooks/useScrollUserMessageOnSend'

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
  const sticky = document.createElement('div')
  sticky.className = 'conversation-user-sticky'
  const userEl = document.createElement('div')
  userEl.className = 'conversation-user-message'
  userEl.setAttribute('data-message-id', userMessageId)
  Object.defineProperty(sticky, 'getBoundingClientRect', {
    value: () => ({ top: 400, bottom: 452, height: 52, left: 0, right: 400, width: 400, x: 0, y: 400, toJSON: () => ({}) }),
  })

  sticky.appendChild(userEl)
  frame.appendChild(sticky)
  scroll.appendChild(frame)
  document.body.appendChild(scroll)

  return { scroll, frame, sticky }
}

describe('useScrollUserMessageOnSend', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
      cb(0)
      return 1
    })
  })

  it('scrolls the sticky wrapper into place when localSendSeq changes', () => {
    const { scroll } = createDom('user-1')

    const { rerender } = renderHook(
      ({ localSendSeq }) => {
        const scrollRef = useRef(scroll)
        const frameRef = useRef(scroll.firstElementChild as HTMLDivElement)
        const userPausedRef = useRef(false)
        const programmaticScrollRef = useRef(false)
        useScrollUserMessageOnSend({
          scrollRef,
          frameRef,
          lastUserMessageId: 'user-1',
          localSendSeq,
          programmaticScrollRef,
          userPausedRef,
        })
        return null
      },
      { initialProps: { localSendSeq: 0 } },
    )

    rerender({ localSendSeq: 1 })

    expect(scroll.scrollTo).toHaveBeenCalled()
    expect(scroll.scrollTop).toBeGreaterThan(0)
  })

  it('re-scrolls when the last user message id changes after send', () => {
    const { scroll, frame } = createDom('user-optimistic')
    const sticky = frame.querySelector('.conversation-user-sticky') as HTMLDivElement
    const userEl = sticky.firstElementChild as HTMLDivElement
    userEl.setAttribute('data-message-id', 'user-optimistic')

    const { rerender } = renderHook(
      ({ lastUserMessageId }) => {
        const scrollRef = useRef(scroll)
        const frameRef = useRef(frame)
        const userPausedRef = useRef(false)
        const programmaticScrollRef = useRef(false)
        useScrollUserMessageOnSend({
          scrollRef,
          frameRef,
          lastUserMessageId,
          localSendSeq: 1,
          programmaticScrollRef,
          userPausedRef,
        })
        return null
      },
      { initialProps: { lastUserMessageId: 'user-optimistic' } },
    )

    userEl.setAttribute('data-message-id', 'user-real')
    vi.mocked(scroll.scrollTo).mockClear()
    rerender({ lastUserMessageId: 'user-real' })

    expect(scroll.scrollTo).toHaveBeenCalled()
  })
})
