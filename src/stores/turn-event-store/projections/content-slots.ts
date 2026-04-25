import type {
  TurnEvent,
  ContentSlotView,
  ProjectionReducer,
  ArtifactData,
  SlotStatus,
} from '../types'
import { isSlotTerminal } from '../types'

// ── Internal Reducer State ────────────────────────────────────────

interface ReducerState {
  slots: ContentSlotView[]
  slotIndex: Map<string, number>
}

// ── Helper Functions ──────────────────────────────────────────────

function createSlotFromEvent(event: TurnEvent & { type: 'slot_opened' }): ContentSlotView {
  const base: ContentSlotView = {
    slotId: event.slotId,
    slotType: event.slotType,
    content: '',
    artifacts: [],
    status: 'streaming',
  }

  if (event.slotType === 'agent') {
    return {
      ...base,
      agentId: event.agentId,
      agentName: event.agentName,
    }
  }

  if (event.slotType === 'summary') {
    return {
      ...base,
      mode: event.mode,
    }
  }

  return base
}

function createHitlPendingSlot(event: TurnEvent & { type: 'hitl_requested' }): ContentSlotView {
  // Store pending HITL as a temporary slot that will be converted on answer
  return {
    slotId: `hitl-pending:${event.hitlId}`,
    slotType: 'hitl_record',
    content: '',
    artifacts: [],
    status: 'streaming', // pending state
    hitlPrompt: event.prompt,
    hitlSource: event.source,
    agentName: event.agentName,
  }
}

function reconstructReducerState(view: ContentSlotView[]): ReducerState {
  const slotIndex = new Map<string, number>()
  view.forEach((slot, idx) => {
    slotIndex.set(slot.slotId, idx)
  })

  return {
    slots: [...view],
    slotIndex,
  }
}

// ── Event Handlers ────────────────────────────────────────────────

function handleSlotOpened(state: ReducerState, event: TurnEvent & { type: 'slot_opened' }): void {
  if (state.slotIndex.has(event.slotId)) return // duplicate

  const newSlot = createSlotFromEvent(event)
  state.slotIndex.set(event.slotId, state.slots.length)
  state.slots.push(newSlot)
}

function handleSlotDelta(state: ReducerState, event: TurnEvent & { type: 'slot_delta' }): void {
  const idx = state.slotIndex.get(event.slotId)
  if (idx === undefined) return // unknown slot

  const slot = state.slots[idx]
  if (!slot || isSlotTerminal(slot.status)) return // terminated

  state.slots[idx] = {
    ...slot,
    content: slot.content + event.textDelta,
  }
}

function handleArtifactAppended(state: ReducerState, event: TurnEvent & { type: 'artifact_appended' }): void {
  const idx = state.slotIndex.get(event.slotId)
  if (idx === undefined) return

  const slot = state.slots[idx]
  if (!slot || isSlotTerminal(slot.status)) return

  state.slots[idx] = {
    ...slot,
    artifacts: [...slot.artifacts, event.artifact],
  }
}

function handleSlotSnapshot(state: ReducerState, event: TurnEvent & { type: 'slot_snapshot' }): void {
  const idx = state.slotIndex.get(event.slotId)
  if (idx === undefined) return

  const slot = state.slots[idx]
  if (!slot || isSlotTerminal(slot.status)) return

  const existingContent = slot.content ?? ''
  const incomingContent = event.content ?? ''
  const hasVisibleExisting = existingContent.trim().length > 0
  const hasVisibleIncoming = incomingContent.trim().length > 0

  // Single-write/append-only policy for block UI:
  // - First visible content wins.
  // - Later snapshots may extend content only when append-only.
  // - Divergent rewrites are ignored to avoid end-of-stream text swap.
  const shouldApplyIncomingContent =
    !hasVisibleExisting
      ? hasVisibleIncoming
      : !hasVisibleIncoming
        ? false
        : incomingContent === existingContent || incomingContent.startsWith(existingContent)

  state.slots[idx] = {
    ...slot,
    content: shouldApplyIncomingContent ? incomingContent : existingContent,
    artifacts: event.artifacts,
    hydrated: event.hydrated !== false,
  }
}

function handleSlotTerminated(state: ReducerState, event: TurnEvent & { type: 'slot_terminated' }): void {
  const idx = state.slotIndex.get(event.slotId)
  if (idx === undefined) return

  const slot = state.slots[idx]
  if (!slot) return

  // Idempotent: first termination wins
  if (isSlotTerminal(slot.status)) return

  state.slots[idx] = {
    ...slot,
    status: event.status,
    error: event.error,
    hasPartialContent: event.hasPartialContent,
  }
}

function handleHitlRequested(state: ReducerState, event: TurnEvent & { type: 'hitl_requested' }): void {
  const pendingSlotId = `hitl-pending:${event.hitlId}`
  if (state.slotIndex.has(pendingSlotId)) return // duplicate

  // Create a pending marker slot (will be filtered out from view)
  const pendingSlot = createHitlPendingSlot(event)
  state.slotIndex.set(pendingSlotId, state.slots.length)
  state.slots.push(pendingSlot)
}

function handleHitlAnswered(state: ReducerState, event: TurnEvent & { type: 'hitl_answered' }): void {
  const pendingSlotId = `hitl-pending:${event.hitlId}`
  const recordSlotId = `hitl-record:${event.hitlId}`

  const pendingIdx = state.slotIndex.get(pendingSlotId)
  if (pendingIdx === undefined) return // no pending request

  const pendingSlot = state.slots[pendingIdx]
  if (!pendingSlot) return

  // Replace the pending marker with the completed record in-place
  // so the HITL card keeps its original position in the slot order.
  const recordSlot: ContentSlotView = {
    ...pendingSlot,
    slotId: recordSlotId,
    status: 'completed',
    hitlAnswer: event.answer,
  }

  state.slots[pendingIdx] = recordSlot
  state.slotIndex.delete(pendingSlotId)
  state.slotIndex.set(recordSlotId, pendingIdx)
}

function handleTurnTermination(state: ReducerState, finalStatus: SlotStatus): void {
  // Close all unterminated slots
  state.slots.forEach((slot, idx) => {
    if (!isSlotTerminal(slot.status)) {
      state.slots[idx] = {
        ...slot,
        status: finalStatus,
      }
    }
  })
}

// ── Reducer Implementation ────────────────────────────────────────

function reduceInternal(state: ReducerState, event: TurnEvent): void {
  switch (event.type) {
    case 'slot_opened':
      handleSlotOpened(state, event)
      break
    case 'slot_delta':
      handleSlotDelta(state, event)
      break
    case 'artifact_appended':
      handleArtifactAppended(state, event)
      break
    case 'slot_snapshot':
      handleSlotSnapshot(state, event)
      break
    case 'slot_terminated':
      handleSlotTerminated(state, event)
      break
    case 'hitl_requested':
      handleHitlRequested(state, event)
      break
    case 'hitl_answered':
      handleHitlAnswered(state, event)
      break
    case 'turn_completed':
      handleTurnTermination(state, 'completed')
      break
    case 'turn_failed':
      handleTurnTermination(state, 'failed')
      break
    case 'turn_canceled':
      handleTurnTermination(state, 'canceled')
      break
    // Ignored events
    case 'turn_started':
    case 'phase_changed':
    case 'hitl_expired':
    case 'hitl_canceled':
    case 'hitl_error':
      break
  }
}

// ── Public API ────────────────────────────────────────────────────

export const contentSlotsReducer: ProjectionReducer<ContentSlotView[]> = {
  init(): ContentSlotView[] {
    return []
  },

  reduce(view: ContentSlotView[], event: TurnEvent): ContentSlotView[] {
    const state = reconstructReducerState(view)
    reduceInternal(state, event)
    return state.slots
  },
}

/**
 * Helper to filter out internal pending markers from the view.
 * Use this when consuming the projection for rendering.
 */
export function getVisibleSlots(slots: ContentSlotView[]): ContentSlotView[] {
  return slots
    .filter(slot => {
      if (slot.slotId.startsWith('hitl-pending:')) return false
      if (slot.status === 'canceled' || slot.status === 'failed' || slot.status === 'rejected') return false
      return true
    })
    .sort((a, b) => {
      // HITL records render before agent/summary content blocks —
      // HITL Q&A happens during processing, before the agent's final response.
      if (a.slotType === 'hitl_record' && b.slotType !== 'hitl_record') return -1
      if (a.slotType !== 'hitl_record' && b.slotType === 'hitl_record') return 1
      return 0
    })
}
