import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useMessageScrollAnchoring, type ScrollAnchoringInput } from '@/hooks/useMessageScrollAnchoring'

function makeEntity(id: string, type: 'user' | 'agent', clientRequestId?: string) {
  return { messageType: type, clientRequestId }
}

function makeInput(overrides: Partial<ScrollAnchoringInput> = {}): ScrollAnchoringInput {
  const container = document.createElement('div')
  Object.defineProperty(container, 'scrollHeight', { value: 1000, configurable: true })
  Object.defineProperty(container, 'scrollTop', { value: 900, configurable: true })
  Object.defineProperty(container, 'clientHeight', { value: 100, configurable: true })
  container.scrollTo = vi.fn()
  container.querySelector = vi.fn().mockReturnValue(null)

  return {
    scrollContainerRef: { current: container },
    hydrated: true,
    roomId: 'room-1',
    renderedAnchorIds: [],
    getEntityForAnchor: () => undefined,
    contentVersion: 0,
    ...overrides,
  }
}

describe('useMessageScrollAnchoring', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('marks didInitialAnchor for empty room without scrolling', () => {
    const input = makeInput({ renderedAnchorIds: [] })
    const { result, rerender } = renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    expect(result.current.shouldAutoScroll).toBe(true)

    const entities: Record<string, ReturnType<typeof makeEntity>> = {
      'u1': makeEntity('u1', 'user', 'crid-1'),
    }
    rerender({
      ...input,
      renderedAnchorIds: ['u1'],
      getEntityForAnchor: (id: string) => entities[id],
    })

    const container = input.scrollContainerRef.current!
    expect(container.scrollTo).toHaveBeenCalled()
  })

  it('P1 scrolls once on new user send, does not re-scroll on temp→real swap', () => {
    const existingEntities: Record<string, ReturnType<typeof makeEntity>> = {
      'old-u': makeEntity('old-u', 'user', 'crid-old'),
      'old-a': makeEntity('old-a', 'agent'),
    }
    const mockEl = { scrollIntoView: vi.fn() }
    const container = makeInput().scrollContainerRef.current!
    container.querySelector = vi.fn().mockReturnValue(mockEl)

    const input = makeInput({
      renderedAnchorIds: ['old-u', 'old-a'],
      getEntityForAnchor: (id: string) => existingEntities[id],
      scrollContainerRef: { current: container },
    })

    const { rerender } = renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    expect(mockEl.scrollIntoView).toHaveBeenCalledTimes(1)
    const scrollToCallsAfterP2 = (container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length

    const withTemp: Record<string, ReturnType<typeof makeEntity>> = {
      ...existingEntities,
      'temp-1': makeEntity('temp-1', 'user', 'crid-new'),
    }
    rerender({
      ...input,
      renderedAnchorIds: ['old-u', 'old-a', 'temp-1'],
      getEntityForAnchor: (id: string) => withTemp[id],
    })

    const scrollToCallsAfterP1 = (container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length
    expect(scrollToCallsAfterP1).toBe(scrollToCallsAfterP2 + 1)

    const withReal: Record<string, ReturnType<typeof makeEntity>> = {
      ...existingEntities,
      'real-1': makeEntity('real-1', 'user', 'crid-new'),
    }
    rerender({
      ...input,
      renderedAnchorIds: ['old-u', 'old-a', 'real-1'],
      getEntityForAnchor: (id: string) => withReal[id],
    })

    expect((container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length).toBe(scrollToCallsAfterP1)
  })

  it('does not scroll on AI streaming when shouldAutoScroll is false', () => {
    const entities = { u1: makeEntity('u1', 'user', 'crid-1') }
    const mockEl = { scrollIntoView: vi.fn() }
    const container = makeInput().scrollContainerRef.current!
    container.querySelector = vi.fn().mockReturnValue(mockEl)
    Object.defineProperty(container, 'scrollTop', { value: 0, configurable: true })

    const input = makeInput({
      renderedAnchorIds: ['u1'],
      getEntityForAnchor: (id: string) => entities[id as keyof typeof entities],
      scrollContainerRef: { current: container },
    })

    const { rerender } = renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    const scrollCallsBefore = (container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length

    rerender({ ...input, contentVersion: 1 })
    rerender({ ...input, contentVersion: 2 })

    expect((container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length).toBe(scrollCallsBefore)
  })

  it('resets anchor state on room switch', () => {
    const entities = { u1: makeEntity('u1', 'user', 'crid-1') }
    const mockEl = { scrollIntoView: vi.fn() }
    const container = makeInput().scrollContainerRef.current!
    container.querySelector = vi.fn().mockReturnValue(mockEl)

    const input = makeInput({
      renderedAnchorIds: ['u1'],
      getEntityForAnchor: (id: string) => entities[id as keyof typeof entities],
      scrollContainerRef: { current: container },
    })

    const { rerender } = renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    const newEntities = { u2: makeEntity('u2', 'user', 'crid-2') }
    const newMockEl = { scrollIntoView: vi.fn() }
    container.querySelector = vi.fn().mockReturnValue(newMockEl)

    rerender({
      ...input,
      roomId: 'room-2',
      renderedAnchorIds: ['u2'],
      getEntityForAnchor: (id: string) => newEntities[id as keyof typeof newEntities],
    })

    expect(newMockEl.scrollIntoView).toHaveBeenCalled()
  })

  it('retries P2 anchor via rAF when DOM element is initially missing', async () => {
    const entities = { u1: makeEntity('u1', 'user', 'crid-1') }
    const mockEl = { scrollIntoView: vi.fn() }
    const container = makeInput().scrollContainerRef.current!

    let queryCount = 0
    container.querySelector = vi.fn().mockImplementation(() => {
      queryCount++
      return queryCount >= 2 ? mockEl : null
    })

    const input = makeInput({
      renderedAnchorIds: ['u1'],
      getEntityForAnchor: (id: string) => entities[id as keyof typeof entities],
      scrollContainerRef: { current: container },
    })

    renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    expect(mockEl.scrollIntoView).not.toHaveBeenCalled()

    await vi.runAllTimersAsync()

    expect(mockEl.scrollIntoView).toHaveBeenCalledWith({ block: 'start', behavior: 'auto' })
  })

  it('marks initial anchor without scrolling for room with only AI messages', () => {
    const entities = { a1: makeEntity('a1', 'agent') }
    const container = makeInput().scrollContainerRef.current!

    const input = makeInput({
      renderedAnchorIds: ['a1'],
      getEntityForAnchor: (id: string) => entities[id as keyof typeof entities],
      scrollContainerRef: { current: container },
    })

    const { result } = renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    expect(container.querySelector).not.toHaveBeenCalled()
    expect(typeof result.current.shouldAutoScroll).toBe('boolean')
  })
})
