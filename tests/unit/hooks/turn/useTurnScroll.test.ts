import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useTurnEventStore } from '@/stores/turn-event-store'
import { useMessageStore } from '@/stores/message-store'
import type { TurnEvent, UserInputData } from '@/stores/turn-event-store/types'

// jsdom doesn't implement scrollTo / scrollIntoView
HTMLElement.prototype.scrollTo = vi.fn()
Element.prototype.scrollIntoView = vi.fn()

// Do NOT mock useTurnScroll — we're testing the real implementation
vi.mock('@/hooks/useAutoHideScroll', () => ({
  useAutoHideScroll: vi.fn(),
}))

const userInput: UserInputData = { text: 'hello', attachments: [] }

function makeContainer() {
  const container = document.createElement('div')
  Object.defineProperty(container, 'scrollHeight', { value: 1000, configurable: true })
  Object.defineProperty(container, 'scrollTop', { value: 900, configurable: true })
  Object.defineProperty(container, 'clientHeight', { value: 100, configurable: true })
  container.scrollTo = vi.fn()
  container.querySelector = vi.fn().mockImplementation((selector: string) => {
    if (selector === '[data-scroll-spacer]') return null
    if (selector === '[data-content-end]') return null
    return {
      scrollIntoView: vi.fn(),
      offsetTop: 1000,
      getBoundingClientRect: () => ({ top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0, toJSON: () => ({}) }),
    }
  })
  container.getBoundingClientRect = () => ({ top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0, toJSON: () => ({}) })
  return container
}

describe('useTurnScroll', () => {
  beforeEach(() => {
    useTurnEventStore.getState().reset()
    useMessageStore.getState().clearRoom()
    useMessageStore.getState().setRoom('room-1')
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('stays in user-anchor mode after new send — streaming does not auto-scroll', async () => {
    const { useTurnScroll } = await import('@/hooks/turn/useTurnScroll')

    const store = useTurnEventStore.getState()
    store.append('turn-1', {
      eventId: 'e1', turnId: 'turn-1', seq: 1, ts: Date.now(),
      type: 'turn_started', userInput, clientRequestId: 'crid-1',
    } as TurnEvent)
    store.markHydrated()

    const container = makeContainer()
    const ref = { current: container }
    const { result } = renderHook(() => useTurnScroll(ref))

    const scrollCallsBefore = (container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length

    // Append a slot_delta event (streaming content) to the active turn
    act(() => {
      const turnLog = useTurnEventStore.getState().turnLogs.get('turn-1')!
      turnLog.append({
        eventId: 'e2', turnId: 'turn-1', seq: 2, ts: Date.now(),
        type: 'slot_delta', slotId: 'slot-1', textDelta: 'streaming...',
      } as TurnEvent)
    })

    // After P1 (user-anchor mode), streaming events should NOT trigger auto-scroll.
    // The mode-based guard in Effect 3 prevents scrolling — only 'bottom-follow' scrolls.
    const scrollCallsAfter = (container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length
    expect(scrollCallsAfter).toBe(scrollCallsBefore)
    // shouldAutoScroll reflects scroll-button visibility (true = button hidden).
    // Near the bottom in user-anchor, the button is hidden since user can already see content.
    expect(result.current.shouldAutoScroll).toBe(true)
  })

  it('does not double-scroll when optimistic turn merges to real turn (same clientRequestId)', async () => {
    const { useTurnScroll } = await import('@/hooks/turn/useTurnScroll')

    const store = useTurnEventStore.getState()

    // Step 1: create optimistic turn via the real store API.
    store.createOptimisticTurn('crid-1', userInput)
    store.markHydrated()

    const container = makeContainer()
    const ref = { current: container }
    const { rerender } = renderHook(() => useTurnScroll(ref))

    // P2 anchor fires (scrollIntoView). Record scrollTo count after initial render.
    const scrollToAfterP2 = (container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length

    // Step 2: simulate backend confirming the turn — append real
    // turn_started with a different turnId but the same clientRequestId.
    act(() => {
      store.append('turn-real', {
        eventId: 'e-real', turnId: 'turn-real', seq: 1, ts: Date.now(),
        type: 'turn_started', userInput, clientRequestId: 'crid-1',
      } as TurnEvent)
    })

    rerender()

    // lastUserSendKey is still 'crid-1' — P1 must NOT fire scrollToBottom again.
    expect((container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length).toBe(scrollToAfterP2)
  })
})
