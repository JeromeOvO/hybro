import { describe, it, expect, vi, beforeEach } from 'vitest'
import { TurnEventLog } from '@/stores/turn-event-store/event-log'
import type { TurnEvent, UserInputData } from '@/stores/turn-event-store/types'

function makeEvent(overrides: Partial<TurnEvent> & { type: TurnEvent['type'] }): TurnEvent {
  return {
    eventId: `evt-${Math.random().toString(36).slice(2)}`,
    turnId: 'turn-1',
    seq: 1,
    ts: Date.now(),
    ...overrides,
  } as TurnEvent
}

const userInput: UserInputData = { text: 'hello', attachments: [] }

describe('TurnEventLog', () => {
  let log: TurnEventLog

  beforeEach(() => {
    log = new TurnEventLog('turn-1')
  })

  it('appends events and maintains seq order', () => {
    const e1 = makeEvent({ type: 'turn_started', seq: 1, eventId: 'e1', userInput })
    const e3 = makeEvent({ type: 'slot_opened', seq: 3, eventId: 'e3', slotId: 's1', slotType: 'agent', agentName: 'A' })
    const e2 = makeEvent({ type: 'phase_changed', seq: 2, eventId: 'e2', phase: { name: 'planning' } })

    log.append(e1)
    log.append(e3)
    log.append(e2)

    const events = log.getEvents()
    expect(events.map(e => e.seq)).toEqual([1, 2, 3])
  })

  it('deduplicates by eventId', () => {
    const e1 = makeEvent({ type: 'turn_started', seq: 1, eventId: 'dup', userInput })
    log.append(e1)
    log.append(e1)

    expect(log.getEvents()).toHaveLength(1)
  })

  it('notifies subscribers on append', () => {
    const cb = vi.fn()
    log.subscribe(cb)

    const e1 = makeEvent({ type: 'turn_started', seq: 1, eventId: 'e1', userInput })
    log.append(e1)

    expect(cb).toHaveBeenCalledOnce()
    expect(cb).toHaveBeenCalledWith(e1, false)
  })

  it('notifies with isDirty=true when out-of-order insert requires replay', () => {
    const cb = vi.fn()
    const e1 = makeEvent({ type: 'turn_started', seq: 1, eventId: 'e1', userInput })
    const e3 = makeEvent({ type: 'slot_opened', seq: 3, eventId: 'e3', slotId: 's1', slotType: 'agent', agentName: 'A' })
    log.append(e1)
    log.append(e3)

    log.subscribe(cb)
    const e2 = makeEvent({ type: 'phase_changed', seq: 2, eventId: 'e2', phase: { name: 'planning' } })
    log.append(e2)

    expect(cb).toHaveBeenCalledWith(e2, true)
  })

  it('unsubscribes correctly', () => {
    const cb = vi.fn()
    const unsub = log.subscribe(cb)
    unsub()

    log.append(makeEvent({ type: 'turn_started', seq: 1, eventId: 'e1', userInput }))
    expect(cb).not.toHaveBeenCalled()
  })

  it('getUserInput returns data from turn_started event', () => {
    log.append(makeEvent({ type: 'turn_started', seq: 1, eventId: 'e1', userInput }))
    expect(log.getUserInput()).toEqual(userInput)
  })

  it('getUserInput returns null when no turn_started event', () => {
    expect(log.getUserInput()).toBeNull()
  })

  it('getStatus returns processing before terminal event', () => {
    log.append(makeEvent({ type: 'turn_started', seq: 1, eventId: 'e1', userInput }))
    expect(log.getStatus()).toBe('processing')
  })

  it('getStatus returns completed after turn_completed', () => {
    log.append(makeEvent({ type: 'turn_started', seq: 1, eventId: 'e1', userInput }))
    log.append(makeEvent({ type: 'turn_completed', seq: 2, eventId: 'e2', durationMs: 1000 }))
    expect(log.getStatus()).toBe('completed')
  })

  it('getStatus returns failed after turn_failed', () => {
    log.append(makeEvent({ type: 'turn_started', seq: 1, eventId: 'e1', userInput }))
    log.append(makeEvent({ type: 'turn_failed', seq: 2, eventId: 'e2', reason: 'error', code: 'error' }))
    expect(log.getStatus()).toBe('failed')
  })
})
