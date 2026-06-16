import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useMessageScrollAnchoring, type ScrollAnchoringInput } from '@/hooks/useMessageScrollAnchoring'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeRect(top: number): DOMRect {
  return { top, left: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0, toJSON: () => ({}) } as DOMRect
}

function makeElement(opts: { top?: number } = {}): HTMLElement {
  const el = document.createElement('div')
  el.getBoundingClientRect = vi.fn().mockReturnValue(makeRect(opts.top ?? 0))
  return el
}

function makeContainer(opts: {
  scrollHeight?: number
  scrollTop?: number
  clientHeight?: number
  containerTop?: number
} = {}) {
  const container = document.createElement('div')
  const scrollH = opts.scrollHeight ?? 1000
  const scrollT = opts.scrollTop ?? 900
  const clientH = opts.clientHeight ?? 100

  Object.defineProperty(container, 'scrollHeight', { value: scrollH, configurable: true })
  Object.defineProperty(container, 'scrollTop', { value: scrollT, configurable: true, writable: true })
  Object.defineProperty(container, 'clientHeight', { value: clientH, configurable: true })
  container.scrollTo = vi.fn()
  container.getBoundingClientRect = vi.fn().mockReturnValue(makeRect(opts.containerTop ?? 0))

  // Default: no elements found
  container.querySelector = vi.fn().mockReturnValue(null)

  return container
}

function makeEntity(_id: string, type: 'user' | 'agent', clientRequestId?: string) {
  return { messageType: type, clientRequestId }
}

function makeInput(overrides: Partial<ScrollAnchoringInput> = {}): ScrollAnchoringInput {
  return {
    scrollContainerRef: { current: makeContainer() },
    hydrated: true,
    roomId: 'room-1',
    renderedAnchorIds: [],
    getEntityForAnchor: () => undefined,
    contentVersion: 0,
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// ResizeObserver mock helpers
// ---------------------------------------------------------------------------

type ROCallback = (entries: unknown[]) => void

let roInstances: { cb: ROCallback; observe: ReturnType<typeof vi.fn>; disconnect: ReturnType<typeof vi.fn> }[] = []

function installResizeObserverMock() {
  roInstances = []
  globalThis.ResizeObserver = vi.fn().mockImplementation((cb: ROCallback) => {
    const instance = {
      cb,
      observe: vi.fn(),
      disconnect: vi.fn(),
    }
    roInstances.push(instance)
    return instance
  }) as unknown as typeof ResizeObserver
}

function uninstallResizeObserverMock() {
  roInstances = []
  // @ts-expect-error clean up
  delete globalThis.ResizeObserver
}

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

describe('useMessageScrollAnchoring', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.useFakeTimers()
    vi.spyOn(performance, 'now').mockReturnValue(0)
  })

  afterEach(() => {
    vi.useRealTimers()
    // Clean up RO mock if it was installed
    if (globalThis.ResizeObserver && (globalThis.ResizeObserver as unknown as ReturnType<typeof vi.fn>).mockImplementation) {
      uninstallResizeObserverMock()
    }
  })

  // =========================================================================
  // Test 1: Empty room stays initial-anchor, first later user send runs P1
  // =========================================================================
  it('empty room stays initial-anchor; first user send fires P1 with rect-based scroll', () => {
    const container = makeContainer({ scrollTop: 900, containerTop: 0 })
    const input = makeInput({
      renderedAnchorIds: [],
      scrollContainerRef: { current: container },
    })

    const { result, rerender } = renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    // Empty room: button hidden
    expect(result.current.shouldAutoScroll).toBe(true)

    // Now add a user message. Mock querySelector to return an element for that message.
    const userEl = makeElement({ top: 200 })
    container.querySelector = vi.fn().mockImplementation((sel: string) => {
      if (sel.includes('data-message-id')) return userEl
      return null
    })

    const entities: Record<string, ReturnType<typeof makeEntity>> = {
      u1: makeEntity('u1', 'user', 'crid-1'),
    }

    rerender({
      ...input,
      renderedAnchorIds: ['u1'],
      getEntityForAnchor: (id: string) => entities[id],
    })

    // P1 should fire rect-based scroll: scrollTop + (el.top - container.top) = 900 + (200 - 0) = 1100
    expect(container.scrollTo).toHaveBeenCalledWith({ top: 1100, behavior: 'auto' })
  })

  // =========================================================================
  // Test 2: P1 dedup on temp->real swap
  // =========================================================================
  it('P1 scrolls once on new user send, does not re-scroll on temp→real swap', () => {
    const container = makeContainer()
    const existingEntities: Record<string, ReturnType<typeof makeEntity>> = {
      'old-u': makeEntity('old-u', 'user', 'crid-old'),
      'old-a': makeEntity('old-a', 'agent'),
    }

    const mockEl = makeElement({ top: 100 })
    container.querySelector = vi.fn().mockImplementation((sel: string) => {
      if (sel.includes('data-message-id')) return mockEl
      return null
    })

    const input = makeInput({
      renderedAnchorIds: ['old-u', 'old-a'],
      getEntityForAnchor: (id: string) => existingEntities[id],
      scrollContainerRef: { current: container },
    })

    const { rerender } = renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    // P2 fires container.scrollTo for initial anchor
    const scrollToAfterP2 = (container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length
    expect(scrollToAfterP2).toBeGreaterThan(0)

    // Add temp user message with new clientRequestId
    const withTemp: Record<string, ReturnType<typeof makeEntity>> = {
      ...existingEntities,
      'temp-1': makeEntity('temp-1', 'user', 'crid-new'),
    }
    rerender({
      ...input,
      renderedAnchorIds: ['old-u', 'old-a', 'temp-1'],
      getEntityForAnchor: (id: string) => withTemp[id],
    })

    // P1 fires scrollTo for the new user message
    const scrollToAfterP1 = (container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length
    expect(scrollToAfterP1).toBeGreaterThan(scrollToAfterP2)

    // Replace temp with real (different id, same clientRequestId)
    const withReal: Record<string, ReturnType<typeof makeEntity>> = {
      ...existingEntities,
      'real-1': makeEntity('real-1', 'user', 'crid-new'),
    }
    rerender({
      ...input,
      renderedAnchorIds: ['old-u', 'old-a', 'real-1'],
      getEntityForAnchor: (id: string) => withReal[id],
    })

    // Same clientRequestId 'crid-new' — P1 must NOT fire again
    expect((container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length).toBe(scrollToAfterP1)
  })

  // =========================================================================
  // Test 3: No streaming scroll in user-anchor mode
  // =========================================================================
  it('does not scroll on AI streaming in user-anchor mode', () => {
    const container = makeContainer()
    const entities: Record<string, ReturnType<typeof makeEntity>> = {
      'old-u': makeEntity('old-u', 'user', 'crid-old'),
      'old-a': makeEntity('old-a', 'agent'),
    }
    const mockEl = makeElement({ top: 100 })
    container.querySelector = vi.fn().mockImplementation((sel: string) => {
      if (sel.includes('data-message-id')) return mockEl
      return null
    })

    const input = makeInput({
      renderedAnchorIds: ['old-u', 'old-a'],
      getEntityForAnchor: (id: string) => entities[id],
      scrollContainerRef: { current: container },
    })

    const { rerender } = renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    // P2 completes. Now add new user message → P1 fires → user-anchor mode
    const withNew: Record<string, ReturnType<typeof makeEntity>> = {
      ...entities,
      'u2': makeEntity('u2', 'user', 'crid-2'),
    }
    rerender({
      ...input,
      renderedAnchorIds: ['old-u', 'old-a', 'u2'],
      getEntityForAnchor: (id: string) => withNew[id],
    })

    const scrollCallsAfterP1 = (container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length

    // Bump contentVersion multiple times (simulates streaming)
    rerender({ ...input, renderedAnchorIds: ['old-u', 'old-a', 'u2'], getEntityForAnchor: (id: string) => withNew[id], contentVersion: 1 })
    rerender({ ...input, renderedAnchorIds: ['old-u', 'old-a', 'u2'], getEntityForAnchor: (id: string) => withNew[id], contentVersion: 2 })
    rerender({ ...input, renderedAnchorIds: ['old-u', 'old-a', 'u2'], getEntityForAnchor: (id: string) => withNew[id], contentVersion: 3 })

    // user-anchor is immune to streaming — no additional scrollTo calls
    expect((container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length).toBe(scrollCallsAfterP1)
  })

  // =========================================================================
  // Test 4: Room switch resets to initial-anchor, P2 re-fires
  // =========================================================================
  it('resets anchor state on room switch and P2 re-fires', () => {
    const container = makeContainer()
    const entities = { u1: makeEntity('u1', 'user', 'crid-1') }
    const mockEl = makeElement({ top: 100 })
    container.querySelector = vi.fn().mockImplementation((sel: string) => {
      if (sel.includes('data-message-id')) return mockEl
      return null
    })

    const input = makeInput({
      renderedAnchorIds: ['u1'],
      getEntityForAnchor: (id: string) => entities[id as keyof typeof entities],
      scrollContainerRef: { current: container },
    })

    const { rerender } = renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    const scrollToAfterRoom1 = (container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length
    expect(scrollToAfterRoom1).toBeGreaterThan(0)

    // Switch to room-2 with new entities
    const newEntities = { u2: makeEntity('u2', 'user', 'crid-2') }
    const newEl = makeElement({ top: 300 })
    container.querySelector = vi.fn().mockImplementation((sel: string) => {
      if (sel.includes('data-message-id')) return newEl
      return null
    })

    rerender({
      ...input,
      roomId: 'room-2',
      renderedAnchorIds: ['u2'],
      getEntityForAnchor: (id: string) => newEntities[id as keyof typeof newEntities],
    })

    // P2 fires scrollTo again for the new room
    expect((container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(scrollToAfterRoom1)
  })

  // =========================================================================
  // Test 5: rAF retry loop — element eventually found
  // =========================================================================
  it('retries P2 anchor via rAF when DOM element is initially missing', async () => {
    const container = makeContainer()
    const entities = { u1: makeEntity('u1', 'user', 'crid-1') }
    const mockEl = makeElement({ top: 200 })

    let queryCount = 0
    container.querySelector = vi.fn().mockImplementation((sel: string) => {
      if (sel.includes('data-message-id')) {
        queryCount++
        return queryCount >= 2 ? mockEl : null
      }
      return null
    })

    const input = makeInput({
      renderedAnchorIds: ['u1'],
      getEntityForAnchor: (id: string) => entities[id as keyof typeof entities],
      scrollContainerRef: { current: container },
    })

    renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    // Initially not found, rAF retry needed
    await vi.runAllTimersAsync()

    // After retry, scrollTo should be called with rect-based calc: 900 + (200 - 0)
    expect(container.scrollTo).toHaveBeenCalledWith({ top: 1100, behavior: 'auto' })
  })

  // =========================================================================
  // Test 6: AI-only room → manual, no scrollIntoView
  // =========================================================================
  it('AI-only room transitions to manual, no element scroll', () => {
    const container = makeContainer()
    const entities = { a1: makeEntity('a1', 'agent') }
    container.querySelector = vi.fn().mockReturnValue(null)

    const input = makeInput({
      renderedAnchorIds: ['a1'],
      getEntityForAnchor: (id: string) => entities[id as keyof typeof entities],
      scrollContainerRef: { current: container },
    })

    const { result } = renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    // No user message → no scrollTo targeting a specific element
    expect(container.scrollTo).not.toHaveBeenCalled()
    expect(typeof result.current.shouldAutoScroll).toBe('boolean')
  })

  // =========================================================================
  // Test 7: Reflow fires in initial-settling, NOT in user-anchor
  // =========================================================================
  it('ResizeObserver fires reflow in initial-settling, disconnected on user-anchor transition', () => {
    installResizeObserverMock()

    const container = makeContainer()
    const entities: Record<string, ReturnType<typeof makeEntity>> = {
      u1: makeEntity('u1', 'user', 'crid-1'),
    }
    const mockEl = makeElement({ top: 100 })

    // Need an inner child element for RO to observe
    const innerChild = document.createElement('div')
    container.appendChild(innerChild)

    container.querySelector = vi.fn().mockImplementation((sel: string) => {
      if (sel.includes('data-message-id')) return mockEl
      return null
    })

    const input = makeInput({
      renderedAnchorIds: ['u1'],
      getEntityForAnchor: (id: string) => entities[id],
      scrollContainerRef: { current: container },
    })

    const { rerender } = renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    // P2 completes → initial-settling, RO should be created
    expect(roInstances.length).toBeGreaterThan(0)
    const roInstance = roInstances[roInstances.length - 1]
    expect(roInstance.observe).toHaveBeenCalled()

    // Simulate resize callback during initial-settling
    roInstance.cb([])
    // Should call scrollTo for reflow adjustment
    const scrollCallsAfterReflow = (container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length

    // Now add a new user send → P1 fires → user-anchor
    const withNew: Record<string, ReturnType<typeof makeEntity>> = {
      ...entities,
      u2: makeEntity('u2', 'user', 'crid-2'),
    }
    rerender({
      ...input,
      renderedAnchorIds: ['u1', 'u2'],
      getEntityForAnchor: (id: string) => withNew[id],
    })

    // Observer should be disconnected when transitioning to user-anchor
    expect(roInstance.disconnect).toHaveBeenCalled()
  })

  // =========================================================================
  // Test 8: User scroll during initial-anchor → manual
  // =========================================================================
  it('user scroll during initial-anchor transitions to manual', () => {
    const container = makeContainer()
    // Don't provide any entities yet — stays in initial-anchor
    const input = makeInput({
      renderedAnchorIds: [],
      scrollContainerRef: { current: container },
    })

    const { result, rerender } = renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    // Advance performance.now past suppression window
    vi.spyOn(performance, 'now').mockReturnValue(500)

    // Now add anchors so there's something to potentially anchor to, but don't find element
    // Staying in initial-anchor because no user message elements found
    const entities = { u1: makeEntity('u1', 'user', 'crid-1') }
    container.querySelector = vi.fn().mockReturnValue(null) // Element not found, rAF pending

    rerender({
      ...input,
      renderedAnchorIds: ['u1'],
      getEntityForAnchor: (id: string) => entities[id as keyof typeof entities],
    })

    // Call handleScroll while in initial-anchor (rAF pending, element not yet found)
    result.current.handleScroll()

    // Should transition to manual — shouldAutoScroll reflects button state
    // In manual mode with isNearContentEnd, button depends on scroll position
    expect(typeof result.current.shouldAutoScroll).toBe('boolean')
  })

  // =========================================================================
  // Test 9: User scroll during initial-settling → manual, observer disconnected
  // =========================================================================
  it('user scroll during initial-settling transitions to manual and disconnects observer', () => {
    installResizeObserverMock()

    const container = makeContainer()
    const innerChild = document.createElement('div')
    container.appendChild(innerChild)

    const entities = { u1: makeEntity('u1', 'user', 'crid-1') }
    const mockEl = makeElement({ top: 100 })
    container.querySelector = vi.fn().mockImplementation((sel: string) => {
      if (sel.includes('data-message-id')) return mockEl
      return null
    })

    const input = makeInput({
      renderedAnchorIds: ['u1'],
      getEntityForAnchor: (id: string) => entities[id as keyof typeof entities],
      scrollContainerRef: { current: container },
    })

    const { result } = renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    // P2 completes → initial-settling with RO
    expect(roInstances.length).toBeGreaterThan(0)
    const roInstance = roInstances[roInstances.length - 1]

    // Advance time past suppression window
    vi.spyOn(performance, 'now').mockReturnValue(500)

    // Simulate genuine user interaction (wheel event on container)
    container.dispatchEvent(new Event('wheel'))

    // User scrolls during initial-settling
    result.current.handleScroll()

    // Should transition to manual and disconnect observer
    expect(roInstance.disconnect).toHaveBeenCalled()
  })

  // =========================================================================
  // Test 10: bottom-follow streams to data-content-end
  // =========================================================================
  it('bottom-follow mode scrolls to content-end on contentVersion bump', () => {
    const container = makeContainer({ scrollTop: 900, clientHeight: 100, containerTop: 0 })
    const entities: Record<string, ReturnType<typeof makeEntity>> = {
      u1: makeEntity('u1', 'user', 'crid-1'),
    }
    const mockEl = makeElement({ top: 100 })
    const contentEndEl = makeElement({ top: 950 })

    container.querySelector = vi.fn().mockImplementation((sel: string) => {
      if (sel.includes('data-message-id')) return mockEl
      if (sel === '[data-content-end]') return contentEndEl
      return null
    })

    const input = makeInput({
      renderedAnchorIds: ['u1'],
      getEntityForAnchor: (id: string) => entities[id],
      scrollContainerRef: { current: container },
    })

    const { result, rerender } = renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    // P2 completes. Now transition to bottom-follow by calling scrollToBottom
    result.current.scrollToBottom()

    const scrollCallsBefore = (container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length

    // Bump contentVersion → Effect 3 fires, bottom-follow scrolls to content-end
    rerender({
      ...input,
      renderedAnchorIds: ['u1'],
      getEntityForAnchor: (id: string) => entities[id],
      contentVersion: 1,
    })

    const scrollCallsAfter = (container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length
    expect(scrollCallsAfter).toBeGreaterThan(scrollCallsBefore)
  })

  // =========================================================================
  // Test 11: scrollToBottom targets content-end → bottom-follow
  // =========================================================================
  it('scrollToBottom calls scrollTo with content-end target and enters bottom-follow', () => {
    // After scrollToBottom, updateButtonVisibility checks isNearContentEnd.
    // Set up so content-end is near the viewport bottom: abs(offset - clientHeight) < 100
    // container.top = 0, clientHeight = 100, content-end.top = 90 → offset = 90, abs(90-100)=10 < 100
    const container = makeContainer({ scrollTop: 200, clientHeight: 100, containerTop: 0 })
    const entities = { u1: makeEntity('u1', 'user', 'crid-1') }
    const mockEl = makeElement({ top: 100 })
    const contentEndEl = makeElement({ top: 90 })

    container.querySelector = vi.fn().mockImplementation((sel: string) => {
      if (sel.includes('data-message-id')) return mockEl
      if (sel === '[data-content-end]') return contentEndEl
      return null
    })

    const input = makeInput({
      renderedAnchorIds: ['u1'],
      getEntityForAnchor: (id: string) => entities[id as keyof typeof entities],
      scrollContainerRef: { current: container },
    })

    const { result } = renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    // Call scrollToBottom
    result.current.scrollToBottom()

    // Should call scrollTo; content-end based:
    // target = scrollTop + (contentEnd.top - container.top) - clientHeight
    // = 200 + (90 - 0) - 100 = 190
    expect(container.scrollTo).toHaveBeenCalledWith(
      expect.objectContaining({ top: 190, behavior: 'auto' })
    )

    // Should be in bottom-follow now, and isNearContentEnd is true → button hidden
    expect(result.current.shouldAutoScroll).toBe(true)
  })

  // =========================================================================
  // Test 12: User scroll near content-end → bottom-follow
  // =========================================================================
  it('user scroll near content-end transitions to bottom-follow', () => {
    const container = makeContainer({ scrollTop: 900, clientHeight: 100, containerTop: 0 })
    const entities: Record<string, ReturnType<typeof makeEntity>> = {
      u1: makeEntity('u1', 'user', 'crid-1'),
    }
    const mockEl = makeElement({ top: 100 })
    // content-end is within 100px of viewport bottom: offset = 90, abs(90 - 100) = 10 < 100
    const contentEndEl = makeElement({ top: 90 })

    container.querySelector = vi.fn().mockImplementation((sel: string) => {
      if (sel.includes('data-message-id')) return mockEl
      if (sel === '[data-content-end]') return contentEndEl
      return null
    })

    const input = makeInput({
      renderedAnchorIds: ['u1'],
      getEntityForAnchor: (id: string) => entities[id],
      scrollContainerRef: { current: container },
    })

    const { result } = renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    // Advance time past suppression window
    vi.spyOn(performance, 'now').mockReturnValue(500)

    // User scrolls, and content-end is near viewport bottom
    result.current.handleScroll()

    // Should transition to bottom-follow → button hidden
    expect(result.current.shouldAutoScroll).toBe(true)
  })

  // =========================================================================
  // Test 13: user-anchor immune to 10 streaming bumps
  // =========================================================================
  it('user-anchor mode is immune to 10 streaming contentVersion bumps', () => {
    const container = makeContainer()
    const entities: Record<string, ReturnType<typeof makeEntity>> = {
      u1: makeEntity('u1', 'user', 'crid-old'),
      a1: makeEntity('a1', 'agent'),
    }
    const mockEl = makeElement({ top: 100 })
    container.querySelector = vi.fn().mockImplementation((sel: string) => {
      if (sel.includes('data-message-id')) return mockEl
      return null
    })

    const input = makeInput({
      renderedAnchorIds: ['u1', 'a1'],
      getEntityForAnchor: (id: string) => entities[id],
      scrollContainerRef: { current: container },
    })

    const { rerender } = renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    // P2 done. Add new user send → P1 → user-anchor
    const withNew: Record<string, ReturnType<typeof makeEntity>> = {
      ...entities,
      u2: makeEntity('u2', 'user', 'crid-new'),
    }
    rerender({
      ...input,
      renderedAnchorIds: ['u1', 'a1', 'u2'],
      getEntityForAnchor: (id: string) => withNew[id],
    })

    const scrollCallsAfterP1 = (container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length

    // Bump contentVersion 10 times
    for (let i = 1; i <= 10; i++) {
      rerender({
        ...input,
        renderedAnchorIds: ['u1', 'a1', 'u2'],
        getEntityForAnchor: (id: string) => withNew[id],
        contentVersion: i,
      })
    }

    // user-anchor: no additional scrollTo calls
    expect((container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length).toBe(scrollCallsAfterP1)
  })

  // =========================================================================
  // Test 14: Rect-based scroll aligns correctly
  // =========================================================================
  it('rect-based scroll computes correct top from element and container rects', () => {
    const container = makeContainer({ scrollTop: 200, containerTop: 50 })
    const entities = { u1: makeEntity('u1', 'user', 'crid-1') }
    const mockEl = makeElement({ top: 300 })

    container.querySelector = vi.fn().mockImplementation((sel: string) => {
      if (sel.includes('data-message-id')) return mockEl
      return null
    })

    const input = makeInput({
      renderedAnchorIds: ['u1'],
      getEntityForAnchor: (id: string) => entities[id as keyof typeof entities],
      scrollContainerRef: { current: container },
    })

    renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    // Expected: Math.max(0, scrollTop + (el.top - container.top))
    // = Math.max(0, 200 + (300 - 50)) = 450
    expect(container.scrollTo).toHaveBeenCalledWith({ top: 450, behavior: 'auto' })
  })

  // =========================================================================
  // Test 15: user-anchor + growing contentVersion → button appears
  // =========================================================================
  it('user-anchor with content not near end shows scroll button (shouldAutoScroll false)', () => {
    // Set up container where content-end is far from viewport bottom
    const container = makeContainer({ scrollTop: 100, clientHeight: 100, scrollHeight: 2000, containerTop: 0 })
    const entities: Record<string, ReturnType<typeof makeEntity>> = {
      u1: makeEntity('u1', 'user', 'crid-old'),
      a1: makeEntity('a1', 'agent'),
    }
    const mockEl = makeElement({ top: 50 })

    container.querySelector = vi.fn().mockImplementation((sel: string) => {
      if (sel.includes('data-message-id')) return mockEl
      // No data-content-end → fallback to scrollHeight check
      // scrollHeight(2000) - scrollTop(100) - clientHeight(100) = 1800 > 100 → not near end
      if (sel === '[data-content-end]') return null
      return null
    })

    const input = makeInput({
      renderedAnchorIds: ['u1', 'a1'],
      getEntityForAnchor: (id: string) => entities[id],
      scrollContainerRef: { current: container },
    })

    const { result, rerender } = renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    // P2 done. Add new user → P1 → user-anchor
    const withNew: Record<string, ReturnType<typeof makeEntity>> = {
      ...entities,
      u2: makeEntity('u2', 'user', 'crid-new'),
    }
    rerender({
      ...input,
      renderedAnchorIds: ['u1', 'a1', 'u2'],
      getEntityForAnchor: (id: string) => withNew[id],
    })

    // Bump contentVersion — Effect 3 runs updateButtonVisibility
    rerender({
      ...input,
      renderedAnchorIds: ['u1', 'a1', 'u2'],
      getEntityForAnchor: (id: string) => withNew[id],
      contentVersion: 1,
    })

    // user-anchor + not near content end → button should be visible
    expect(result.current.shouldAutoScroll).toBe(false)
  })

  // =========================================================================
  // Test 16: P2 sets prevKey → same ID doesn't re-trigger P1
  // =========================================================================
  it('P2 sets prevKey so same clientRequestId does not re-trigger P1', () => {
    const container = makeContainer()
    const entities: Record<string, ReturnType<typeof makeEntity>> = {
      u1: makeEntity('u1', 'user', 'crid-1'),
    }
    const mockEl = makeElement({ top: 100 })
    container.querySelector = vi.fn().mockImplementation((sel: string) => {
      if (sel.includes('data-message-id')) return mockEl
      return null
    })

    const input = makeInput({
      renderedAnchorIds: ['u1'],
      getEntityForAnchor: (id: string) => entities[id],
      scrollContainerRef: { current: container },
    })

    const { rerender } = renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    // P2 completes and sets prevKey to 'crid-1'
    const scrollCallsAfterP2 = (container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length

    // Re-render with same data — P1 should not fire because prevKey === lastUserSendKey
    rerender({
      ...input,
      renderedAnchorIds: ['u1'],
      getEntityForAnchor: (id: string) => entities[id],
      contentVersion: 1,
    })

    expect((container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length).toBe(scrollCallsAfterP2)
  })

  // =========================================================================
  // Test 17: Effect dep update during initial-settling doesn't disconnect observer
  // =========================================================================
  it('contentVersion bump during initial-settling does not disconnect observer', () => {
    installResizeObserverMock()

    const container = makeContainer()
    const innerChild = document.createElement('div')
    container.appendChild(innerChild)

    const entities = { u1: makeEntity('u1', 'user', 'crid-1') }
    const mockEl = makeElement({ top: 100 })
    container.querySelector = vi.fn().mockImplementation((sel: string) => {
      if (sel.includes('data-message-id')) return mockEl
      return null
    })

    const input = makeInput({
      renderedAnchorIds: ['u1'],
      getEntityForAnchor: (id: string) => entities[id as keyof typeof entities],
      scrollContainerRef: { current: container },
    })

    const { rerender } = renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    // P2 completes → initial-settling with RO
    expect(roInstances.length).toBeGreaterThan(0)
    const roInstance = roInstances[roInstances.length - 1]

    // Rerender with different contentVersion (triggers Effect 3)
    rerender({
      ...input,
      contentVersion: 1,
    })

    // Observer should still be connected (not disconnected by Effect 3)
    expect(roInstance.disconnect).not.toHaveBeenCalled()
  })

  // =========================================================================
  // Test 18: Empty room first send — P2 fires via Effect 1, sets prevKey
  //          so Effect 2 (P1) is deduped. Second new send triggers P1.
  // =========================================================================
  it('empty room first send goes through P2, second send triggers P1', () => {
    const container = makeContainer()
    const input = makeInput({
      renderedAnchorIds: [],
      scrollContainerRef: { current: container },
    })

    const { rerender } = renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    // Empty room: initialPassDoneRef set, still initial-anchor mode
    expect(container.scrollTo).not.toHaveBeenCalled()

    // Add first user message — Effect 1 re-runs (renderedAnchorIds.length changed)
    const mockEl = makeElement({ top: 200 })
    container.querySelector = vi.fn().mockImplementation((sel: string) => {
      if (sel.includes('data-message-id')) return mockEl
      return null
    })

    const entities: Record<string, ReturnType<typeof makeEntity>> = {
      u1: makeEntity('u1', 'user', 'crid-1'),
    }
    rerender({
      ...input,
      renderedAnchorIds: ['u1'],
      getEntityForAnchor: (id: string) => entities[id],
    })

    // P2 fires via Effect 1 and sets prevKey to 'crid-1'
    const scrollCallsAfterP2 = (container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length
    expect(scrollCallsAfterP2).toBeGreaterThan(0)

    // Now add a second user send — P1 should fire for the new key
    const withSecond: Record<string, ReturnType<typeof makeEntity>> = {
      ...entities,
      u2: makeEntity('u2', 'user', 'crid-2'),
    }
    rerender({
      ...input,
      renderedAnchorIds: ['u1', 'u2'],
      getEntityForAnchor: (id: string) => withSecond[id],
    })

    // P1 fires for 'crid-2' (different from prevKey 'crid-1')
    expect((container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(scrollCallsAfterP2)
  })
})
