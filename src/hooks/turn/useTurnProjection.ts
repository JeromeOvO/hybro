import { useState, useEffect } from 'react'
import type { TurnEventLog } from '@/stores/turn-event-store/event-log'
import type { TurnEvent, ProjectionReducer } from '@/stores/turn-event-store/types'

function fullReplay<T>(events: readonly TurnEvent[], reducer: ProjectionReducer<T>): T {
  let view = reducer.init()
  for (const event of events) {
    view = reducer.reduce(view, event)
  }
  return view
}

export function useTurnProjection<T>(
  turnLog: TurnEventLog,
  reducer: ProjectionReducer<T>,
): T {
  const [view, setView] = useState<T>(() =>
    fullReplay(turnLog.getEvents(), reducer)
  )

  useEffect(() => {
    // Immediate rebuild when turnLog instance changes (optimistic merge)
    setView(fullReplay(turnLog.getEvents(), reducer))

    return turnLog.subscribe((event, isDirty) => {
      if (isDirty) {
        // Out-of-order insert — full replay required
        setView(fullReplay(turnLog.getEvents(), reducer))
      } else {
        // Normal incremental reduction
        setView(prev => reducer.reduce(prev, event))
      }
    })
  }, [turnLog, reducer])

  return view
}
