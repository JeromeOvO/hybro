import { create } from 'zustand'
import { TurnEventLog } from './event-log'
import { composerReducer } from './projections/composer'
import type { TurnEvent, ComposerStateView, UserInputData } from './types'

// Re-export for convenience
export type { TurnEvent, ComposerStateView, UserInputData } from './types'
export { TurnEventLog } from './event-log'
export { composerReducer } from './projections/composer'
export { contentSlotsReducer } from './projections/content-slots'
export { railReducer } from './projections/rail'

interface TurnEventStoreState {
  // Core state
  turnLogs: Map<string, TurnEventLog>
  orderedTurnIds: string[]
  turnIdByClientRequestId: Map<string, string>
  composerState: ComposerStateView

  // Actions
  append(turnId: string, event: TurnEvent): void
  createOptimisticTurn(clientRequestId: string, userInput: UserInputData): void
  removeTurn(turnId: string): void
  reset(): void
}

export const useTurnEventStore = create<TurnEventStoreState>((set, get) => ({
  // Initial state
  turnLogs: new Map(),
  orderedTurnIds: [],
  turnIdByClientRequestId: new Map(),
  composerState: composerReducer.init(),

  // Actions
  append(turnId: string, event: TurnEvent): void {
    const state = get()

    // Check for optimistic merge
    let finalTurnId = turnId
    if (event.type === 'turn_started' && event.clientRequestId) {
      const optimisticTurnId = state.turnIdByClientRequestId.get(event.clientRequestId)
      if (optimisticTurnId) {
        // Replace optimistic turn with real turn
        const optimisticLog = state.turnLogs.get(optimisticTurnId)
        if (optimisticLog) {
          // Create new log with real turnId
          const newLog = new TurnEventLog(turnId)
          const optimisticEvents = optimisticLog.getEvents()

          // Transfer events to new log, replacing the first turn_started
          for (let i = 0; i < optimisticEvents.length; i++) {
            if (i === 0) {
              newLog.append(event)
            } else {
              newLog.append({ ...optimisticEvents[i], turnId })
            }
          }

          // Update state
          const newTurnLogs = new Map(state.turnLogs)
          newTurnLogs.delete(optimisticTurnId)
          newTurnLogs.set(turnId, newLog)

          const newOrderedTurnIds = state.orderedTurnIds.map(id =>
            id === optimisticTurnId ? turnId : id
          )

          // Keep the clientRequestId → turnId mapping updated (not deleted)
          // so subsequent ID swaps (tempMessageId → realMessageId) can
          // still find and merge the turn via the same clientRequestId.
          const newLookup = new Map(state.turnIdByClientRequestId)
          newLookup.set(event.clientRequestId, turnId)

          set({
            turnLogs: newTurnLogs,
            orderedTurnIds: newOrderedTurnIds,
            turnIdByClientRequestId: newLookup,
          })
          return
        }
      }
    }

    // Normal append logic
    let log = state.turnLogs.get(finalTurnId)
    const isNewTurn = !log

    if (!log) {
      log = new TurnEventLog(finalTurnId)
    }

    log.append(event)

    // Update composer state if event type is relevant
    const composerEventTypes: TurnEvent['type'][] = [
      'turn_started', 'turn_completed', 'turn_failed', 'turn_canceled',
      'hitl_requested', 'hitl_answered', 'hitl_expired', 'hitl_canceled', 'hitl_error'
    ]

    const shouldUpdateComposer = composerEventTypes.includes(event.type)
    const newComposerState = shouldUpdateComposer
      ? composerReducer.reduce(state.composerState, event)
      : state.composerState

    // Only set state if something changed
    if (isNewTurn || shouldUpdateComposer) {
      const newTurnLogs = new Map(state.turnLogs)
      newTurnLogs.set(finalTurnId, log)

      const newOrderedTurnIds = isNewTurn
        ? [...state.orderedTurnIds, finalTurnId]
        : state.orderedTurnIds

      set({
        turnLogs: newTurnLogs,
        orderedTurnIds: newOrderedTurnIds,
        composerState: newComposerState,
      })
    }
  },

  createOptimisticTurn(clientRequestId: string, userInput: UserInputData): void {
    const state = get()
    const optimisticTurnId = clientRequestId

    const log = new TurnEventLog(optimisticTurnId)
    const syntheticEvent: TurnEvent = {
      eventId: `${optimisticTurnId}-start`,
      turnId: optimisticTurnId,
      seq: 1,
      ts: Date.now(),
      type: 'turn_started',
      userInput,
      clientRequestId,
    }

    log.append(syntheticEvent)

    const newTurnLogs = new Map(state.turnLogs)
    newTurnLogs.set(optimisticTurnId, log)

    const newLookup = new Map(state.turnIdByClientRequestId)
    newLookup.set(clientRequestId, optimisticTurnId)

    const newComposerState = composerReducer.reduce(state.composerState, syntheticEvent)

    set({
      turnLogs: newTurnLogs,
      orderedTurnIds: [...state.orderedTurnIds, optimisticTurnId],
      turnIdByClientRequestId: newLookup,
      composerState: newComposerState,
    })
  },

  removeTurn(turnId: string): void {
    const state = get()
    if (!state.turnLogs.has(turnId)) return

    const newTurnLogs = new Map(state.turnLogs)
    newTurnLogs.delete(turnId)

    const newOrderedTurnIds = state.orderedTurnIds.filter(id => id !== turnId)

    // Clean up clientRequestId lookup if this was an optimistic turn
    const newLookup = new Map(state.turnIdByClientRequestId)
    for (const [crId, tId] of newLookup) {
      if (tId === turnId) { newLookup.delete(crId); break }
    }

    // Recompute composer state from scratch since we removed a turn
    let composerState = composerReducer.init()
    for (const id of newOrderedTurnIds) {
      const log = newTurnLogs.get(id)
      if (!log) continue
      for (const event of log.getEvents()) {
        composerState = composerReducer.reduce(composerState, event)
      }
    }

    set({
      turnLogs: newTurnLogs,
      orderedTurnIds: newOrderedTurnIds,
      turnIdByClientRequestId: newLookup,
      composerState,
    })
  },

  reset(): void {
    set({
      turnLogs: new Map(),
      orderedTurnIds: [],
      turnIdByClientRequestId: new Map(),
      composerState: composerReducer.init(),
    })
  },
}))
