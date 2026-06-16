import type { RawTimelineEvent } from './types'

let eventStore: Map<string, RawTimelineEvent[]> = new Map()

export function appendEvent(roomId: string, event: RawTimelineEvent): void {
  const existing = eventStore.get(roomId)
  if (existing) {
    existing.push(event)
  } else {
    eventStore.set(roomId, [event])
  }
}

export function getEvents(roomId: string): readonly RawTimelineEvent[] {
  return eventStore.get(roomId) ?? []
}

export function clearRoom(roomId: string): void {
  eventStore.delete(roomId)
}

export function resetEventStore(): void {
  eventStore = new Map()
}
