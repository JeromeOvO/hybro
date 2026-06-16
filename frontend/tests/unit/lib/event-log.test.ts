import { describe, it, expect, beforeEach } from 'vitest'
import {
  appendEvent,
  getEvents,
  clearRoom,
  resetEventStore,
} from '@/lib/room-timeline/event-log'
import type { RawTimelineEvent } from '@/lib/room-timeline/types'

function makeEvent(overrides: Partial<RawTimelineEvent> = {}): RawTimelineEvent {
  return {
    kind: 'agent_started',
    timestamp: new Date().toISOString(),
    label: 'Agent started',
    ...overrides,
  }
}

describe('event-log', () => {
  beforeEach(() => {
    resetEventStore()
  })

  it('appends events and retrieves them in order', () => {
    const e1 = makeEvent({ label: 'First' })
    const e2 = makeEvent({ label: 'Second' })
    appendEvent('room-1', e1)
    appendEvent('room-1', e2)
    const events = getEvents('room-1')
    expect(events).toHaveLength(2)
    expect(events[0].label).toBe('First')
    expect(events[1].label).toBe('Second')
  })

  it('clears events for a specific room', () => {
    appendEvent('room-1', makeEvent({ label: 'A' }))
    appendEvent('room-1', makeEvent({ label: 'B' }))
    clearRoom('room-1')
    expect(getEvents('room-1')).toHaveLength(0)
  })

  it('isolates events between rooms', () => {
    appendEvent('room-1', makeEvent({ label: 'Room 1 event' }))
    appendEvent('room-2', makeEvent({ label: 'Room 2 event' }))
    expect(getEvents('room-1')).toHaveLength(1)
    expect(getEvents('room-1')[0].label).toBe('Room 1 event')
    expect(getEvents('room-2')).toHaveLength(1)
    expect(getEvents('room-2')[0].label).toBe('Room 2 event')
    clearRoom('room-1')
    expect(getEvents('room-1')).toHaveLength(0)
    expect(getEvents('room-2')).toHaveLength(1)
  })

  it('returns empty array for unknown room', () => {
    const events = getEvents('nonexistent-room')
    expect(events).toHaveLength(0)
    expect(Array.isArray(events)).toBe(true)
  })
})
