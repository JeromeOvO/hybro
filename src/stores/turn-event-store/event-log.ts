import type { TurnEvent, UserInputData } from './types'

type Subscriber = (event: TurnEvent, isDirty: boolean) => void
type Unsubscribe = () => void

export type TurnStatus = 'processing' | 'completed' | 'failed' | 'canceled'

export class TurnEventLog {
  readonly turnId: string
  private events: TurnEvent[] = []
  private eventIdSet = new Set<string>()
  private subscribers = new Set<Subscriber>()
  private lastReducedSeq = 0
  private cachedStatus: TurnStatus = 'processing'

  constructor(turnId: string) {
    this.turnId = turnId
  }

  append(event: TurnEvent): void {
    if (this.eventIdSet.has(event.eventId)) return

    this.eventIdSet.add(event.eventId)

    let insertIdx = this.events.length
    while (insertIdx > 0 && this.events[insertIdx - 1].seq > event.seq) {
      insertIdx--
    }

    const isDirty = event.seq <= this.lastReducedSeq
    this.events.splice(insertIdx, 0, event)

    if (!isDirty && event.seq > this.lastReducedSeq) {
      this.lastReducedSeq = event.seq
    }

    if (event.type === 'turn_completed') this.cachedStatus = 'completed'
    else if (event.type === 'turn_failed') this.cachedStatus = 'failed'
    else if (event.type === 'turn_canceled') this.cachedStatus = 'canceled'

    for (const cb of this.subscribers) {
      cb(event, isDirty)
    }
  }

  subscribe(cb: Subscriber): Unsubscribe {
    this.subscribers.add(cb)
    return () => { this.subscribers.delete(cb) }
  }

  getEvents(): readonly TurnEvent[] {
    return this.events
  }

  getUserInput(): UserInputData | null {
    const started = this.events.find(e => e.type === 'turn_started')
    if (started && started.type === 'turn_started') {
      return started.userInput
    }
    return null
  }

  getStatus(): TurnStatus {
    return this.cachedStatus
  }

  isTerminal(): boolean {
    return this.cachedStatus !== 'processing'
  }
}
