import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useTurnProjection } from '@/hooks/turn/useTurnProjection'
import { TurnEventLog } from '@/stores/turn-event-store/event-log'
import type { TurnEvent, ProjectionReducer, UserInputData } from '@/stores/turn-event-store/types'

const userInput: UserInputData = { text: 'hello', attachments: [] }

// Simple counting reducer for testing
const countReducer: ProjectionReducer<number> = {
  init: () => 0,
  reduce: (count: number) => count + 1,
}

describe('useTurnProjection', () => {
  let log: TurnEventLog

  beforeEach(() => {
    log = new TurnEventLog('turn-1')
  })

  it('returns init() value for empty log', () => {
    const { result } = renderHook(() => useTurnProjection(log, countReducer))
    expect(result.current).toBe(0)
  })

  it('reduces existing events on mount', () => {
    log.append({
      eventId: 'e1', turnId: 'turn-1', seq: 1, ts: 1000,
      type: 'turn_started', userInput,
    })
    log.append({
      eventId: 'e2', turnId: 'turn-1', seq: 2, ts: 2000,
      type: 'phase_changed', phase: { name: 'planning' },
    } as TurnEvent)

    const { result } = renderHook(() => useTurnProjection(log, countReducer))
    expect(result.current).toBe(2)
  })

  it('updates on new event append', () => {
    const { result } = renderHook(() => useTurnProjection(log, countReducer))
    expect(result.current).toBe(0)

    act(() => {
      log.append({
        eventId: 'e1', turnId: 'turn-1', seq: 1, ts: 1000,
        type: 'turn_started', userInput,
      })
    })

    expect(result.current).toBe(1)
  })

  it('does full replay on dirty event', () => {
    log.append({
      eventId: 'e1', turnId: 'turn-1', seq: 1, ts: 1000,
      type: 'turn_started', userInput,
    })
    log.append({
      eventId: 'e3', turnId: 'turn-1', seq: 3, ts: 3000,
      type: 'turn_completed', durationMs: 1000,
    } as TurnEvent)

    const { result } = renderHook(() => useTurnProjection(log, countReducer))
    expect(result.current).toBe(2)

    act(() => {
      log.append({
        eventId: 'e2', turnId: 'turn-1', seq: 2, ts: 2000,
        type: 'phase_changed', phase: { name: 'planning' },
      } as TurnEvent)
    })

    expect(result.current).toBe(3)
  })

  it('unsubscribes on unmount', () => {
    const { result, unmount } = renderHook(() => useTurnProjection(log, countReducer))
    unmount()

    log.append({
      eventId: 'e1', turnId: 'turn-1', seq: 1, ts: 1000,
      type: 'turn_started', userInput,
    })
    // No assertion needed — verifying no errors/memory leaks
  })
})
