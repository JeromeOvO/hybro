import type { RawTimelineEvent } from './types'

let eventStore: Map<string, RawTimelineEvent[]> = new Map()

export function appendEvent(conversationId: string, event: RawTimelineEvent): void {
  const existing = eventStore.get(conversationId)
  if (existing) {
    existing.push(event)
  } else {
    eventStore.set(conversationId, [event])
  }
}

export function getEvents(conversationId: string): readonly RawTimelineEvent[] {
  return eventStore.get(conversationId) ?? []
}

export function clearRoom(conversationId: string): void {
  eventStore.delete(conversationId)
}

export function resetEventStore(): void {
  eventStore = new Map()
}
