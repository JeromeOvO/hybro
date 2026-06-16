import { describe, expect, it, beforeEach, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useRef } from 'react'
import { useDetailPaneScroll } from '@/hooks/useDetailPaneScroll'
import { useRoomUiStore } from '@/stores/room-ui-store'

function createScrollBody() {
  const element = document.createElement('div')
  Object.defineProperty(element, 'scrollHeight', { value: 800, writable: true, configurable: true })
  Object.defineProperty(element, 'clientHeight', { value: 200, writable: true, configurable: true })
  Object.defineProperty(element, 'scrollTop', { value: 0, writable: true, configurable: true })
  document.body.appendChild(element)
  return element
}

describe('useDetailPaneScroll', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    useRoomUiStore.getState().resetAll()
    vi.spyOn(performance, 'now').mockReturnValue(10_000)
  })

  it('scrolls to top when opening a message for the first time', () => {
    const body = createScrollBody()
    body.scrollTop = 400

    renderHook(() => {
      const ref = useRef<HTMLDivElement | null>(body)
      useDetailPaneScroll(ref, 'agent-1', false, 120)
      return ref
    })

    expect(body.scrollTop).toBe(0)
  })

  it('restores saved scroll when reopening the same message', () => {
    const body = createScrollBody()
    useRoomUiStore.getState().saveDetailPaneScroll('agent-1', { scrollTop: 240, atBottom: false })

    renderHook(() => {
      const ref = useRef<HTMLDivElement | null>(body)
      useDetailPaneScroll(ref, 'agent-1', false, 120)
      return ref
    })

    expect(body.scrollTop).toBe(240)
  })

  it('saves previous message scroll when switching agents', () => {
    const body = createScrollBody()

    const { rerender } = renderHook(
      ({ messageId }) => {
        const ref = useRef<HTMLDivElement | null>(body)
        useDetailPaneScroll(ref, messageId, false, 120)
        return ref
      },
      { initialProps: { messageId: 'agent-1' } },
    )

    body.scrollTop = 320
    rerender({ messageId: 'agent-2' })

    expect(useRoomUiStore.getState().getDetailPaneScroll('agent-1')).toEqual({
      scrollTop: 320,
      atBottom: false,
    })
    expect(body.scrollTop).toBe(0)
  })

  it('does not tail-follow until the user scrolls near the bottom', () => {
    const body = createScrollBody()

    const { rerender } = renderHook(
      ({ contentLength }) => {
        const ref = useRef<HTMLDivElement | null>(body)
        useDetailPaneScroll(ref, 'agent-stream', true, contentLength)
        return ref
      },
      { initialProps: { contentLength: 10 } },
    )

    expect(body.scrollTop).toBe(0)

    Object.defineProperty(body, 'scrollHeight', { value: 1200, writable: true, configurable: true })
    rerender({ contentLength: 20 })
    expect(body.scrollTop).toBe(0)

    body.scrollTop = 960
    body.dispatchEvent(new Event('wheel'))

    Object.defineProperty(body, 'scrollHeight', { value: 1600, writable: true, configurable: true })
    rerender({ contentLength: 30 })
    expect(body.scrollTop).toBe(1600)
  })

  it('keeps scroll position when streaming completes', () => {
    const body = createScrollBody()

    const { rerender } = renderHook(
      ({ isStreaming }) => {
        const ref = useRef<HTMLDivElement | null>(body)
        useDetailPaneScroll(ref, 'agent-stream', isStreaming, 40)
        return ref
      },
      { initialProps: { isStreaming: true } },
    )

    expect(body.scrollTop).toBe(0)

    body.scrollTop = 120
    rerender({ isStreaming: false })
    expect(body.scrollTop).toBe(120)
  })
})
