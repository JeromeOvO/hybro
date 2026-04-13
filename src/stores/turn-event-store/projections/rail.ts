import type { ProjectionReducer, TurnEvent, RailItemView, RailIcon } from '../types'

// ── Internal State ────────────────────────────────────────────────

interface RailState {
  items: RailItemView[]
  keyIndex: Map<string, number> // key → items array index
  hitlLabels: Map<string, string> // hitlId → base label (to build answered label)
  startTs: number | null // ts of first event, for computing duration
}

// ── Helpers ───────────────────────────────────────────────────────

function formatDuration(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`
}

function makePhaseLabel(phase: TurnEvent & { type: 'phase_changed' }): string {
  switch (phase.phase.name) {
    case 'planning':
      return 'Plan'
    case 'delegating':
      return `Delegate to ${phase.phase.agentNames.join(', ')}`
    case 'evaluating':
      return 'Evaluate'
    case 'synthesizing':
      return 'Synthesize'
    case 'awaiting_input':
      return 'Await input'
    case 'round':
      return `Round ${phase.phase.current}/${phase.phase.total}`
    case 'workflow_step':
      return `Step ${phase.phase.current}/${phase.phase.total}: ${phase.phase.stepName}`
  }
}

function makeTerminalIcon(status: 'completed' | 'failed' | 'canceled' | 'rejected'): RailIcon {
  return status === 'completed' ? 'check' : 'x'
}

function makeTerminalLabel(
  baseLabel: string,
  status: 'completed' | 'failed' | 'canceled' | 'rejected',
): string {
  return `${baseLabel}: ${status}`
}

// ── Reducer ───────────────────────────────────────────────────────

function init(): RailState {
  return {
    items: [],
    keyIndex: new Map(),
    hitlLabels: new Map(),
    startTs: null,
  }
}

function reduce(state: RailState, event: TurnEvent): RailState {
  // Clone state for immutability
  const items = [...state.items]
  const keyIndex = new Map(state.keyIndex)
  const hitlLabels = new Map(state.hitlLabels)
  const startTs = state.startTs ?? event.ts

  // After a turn terminal event, ignore non-terminal events.
  // This prevents stale phase_changed events (from processing_status SSE)
  // that arrive after the turn has already completed from appearing in the rail.
  if (keyIndex.has('turn-terminal')) {
    return state
  }

  switch (event.type) {
    case 'phase_changed': {
      // Deactivate all active items by setting them to check icon
      for (let i = 0; i < items.length; i++) {
        if (items[i].isActive) {
          items[i] = { ...items[i], isActive: false, icon: 'check' }
        }
      }

      // Add new phase item
      const key = `phase-${event.seq}`
      const item: RailItemView = {
        key,
        icon: 'spinner',
        label: makePhaseLabel(event),
        ts: event.ts,
        isActive: true,
      }
      keyIndex.set(key, items.length)
      items.push(item)
      break
    }

    case 'slot_opened': {
      // Only track agent slots, not summary slots
      if (event.slotType === 'agent') {
        const key = `slot-${event.slotId}`
        // Deduplicate: hydration + sync bridge can both emit slot_opened
        // with different eventIds for the same slot.
        if (keyIndex.has(key)) break
        const agentName = event.agentName || 'Agent'
        const item: RailItemView = {
          key,
          icon: 'spinner',
          label: `${agentName}: working`,
          ts: event.ts,
          isActive: true,
          agentId: event.agentId,
        }
        keyIndex.set(key, items.length)
        items.push(item)
      }
      break
    }

    case 'slot_terminated': {
      const key = `slot-${event.slotId}`
      const idx = keyIndex.get(key)
      if (idx !== undefined) {
        const oldItem = items[idx]
        const agentName = oldItem.label.split(':')[0] || 'Unknown Agent'
        items[idx] = {
          ...oldItem,
          icon: makeTerminalIcon(event.status),
          label: makeTerminalLabel(agentName, event.status),
          isActive: false,
        }
      }
      break
    }

    case 'hitl_requested': {
      const key = `hitl-${event.hitlId}`
      // Deduplicate: if this hitlId already has a rail item (e.g. from
      // journal replay), skip the duplicate from hydration/reconnect.
      if (keyIndex.has(key)) break
      const agentName = event.agentName || 'Agent'
      const baseLabel = `${agentName} asked for input`
      hitlLabels.set(event.hitlId, baseLabel)
      const item: RailItemView = {
        key,
        icon: 'pause',
        label: baseLabel,
        ts: event.ts,
        isActive: true,
        agentId: undefined, // HITL events don't carry agentId
      }
      keyIndex.set(key, items.length)
      items.push(item)
      break
    }

    case 'hitl_answered':
    case 'hitl_expired':
    case 'hitl_canceled':
    case 'hitl_error': {
      const key = `hitl-${event.hitlId}`
      const idx = keyIndex.get(key)
      if (idx !== undefined) {
        const oldItem = items[idx]
        const baseLabel = hitlLabels.get(event.hitlId) || oldItem.label
        let newLabel = baseLabel
        let newIcon: RailIcon = 'check'

        if (event.type === 'hitl_answered') {
          newLabel = `${baseLabel} — answered`
        } else if (event.type === 'hitl_expired') {
          newLabel = `${baseLabel} — expired`
          newIcon = 'x'
        } else if (event.type === 'hitl_canceled') {
          newLabel = `${baseLabel} — canceled`
          newIcon = 'x'
        } else if (event.type === 'hitl_error') {
          newLabel = `${baseLabel} — error`
          newIcon = 'x'
        }

        items[idx] = {
          ...oldItem,
          icon: newIcon,
          label: newLabel,
          isActive: false,
        }
      }
      break
    }

    case 'turn_completed': {
      // Deactivate all active items
      for (let i = 0; i < items.length; i++) {
        if (items[i].isActive) {
          items[i] = { ...items[i], isActive: false, icon: 'check' }
        }
      }

      // Add terminal item (deduplicate: hydration + sync can both emit)
      const key = 'turn-terminal'
      if (keyIndex.has(key)) break
      // Use event.durationMs if available, otherwise compute from first event ts
      const duration = event.durationMs > 0
        ? event.durationMs
        : (startTs ? event.ts - startTs : 0)
      const item: RailItemView = {
        key,
        icon: 'check',
        label: `Completed (${formatDuration(duration)})`,
        ts: event.ts,
        isActive: false,
      }
      keyIndex.set(key, items.length)
      items.push(item)
      break
    }

    case 'turn_failed': {
      // Deactivate all active items
      for (let i = 0; i < items.length; i++) {
        if (items[i].isActive) {
          items[i] = { ...items[i], isActive: false, icon: 'check' }
        }
      }

      // Add terminal item (deduplicate)
      const key = 'turn-terminal'
      if (keyIndex.has(key)) break
      const item: RailItemView = {
        key,
        icon: 'x',
        label: `Failed: ${event.reason}`,
        ts: event.ts,
        isActive: false,
      }
      keyIndex.set(key, items.length)
      items.push(item)
      break
    }

    case 'turn_canceled': {
      // Deactivate all active items
      for (let i = 0; i < items.length; i++) {
        if (items[i].isActive) {
          items[i] = { ...items[i], isActive: false, icon: 'x' }
        }
      }

      // Add terminal item (deduplicate)
      const key = 'turn-terminal'
      if (keyIndex.has(key)) break
      const item: RailItemView = {
        key,
        icon: 'x',
        label: 'Canceled',
        ts: event.ts,
        isActive: false,
      }
      keyIndex.set(key, items.length)
      items.push(item)
      break
    }

    // Other event types don't affect the rail
    case 'turn_started':
    case 'slot_delta':
    case 'artifact_appended':
    case 'slot_snapshot':
      break
  }

  return { items, keyIndex, hitlLabels, startTs }
}

// ── Public API ────────────────────────────────────────────────────

export const railReducer: ProjectionReducer<RailItemView[]> = {
  init: () => init().items,
  reduce: (view, event) => {
    const state: RailState = {
      items: view,
      keyIndex: new Map(view.map((item, idx) => [item.key, idx])),
      hitlLabels: new Map(), // reconstructed on replay
      startTs: view.length > 0 ? view[0].ts : null,
    }
    return reduce(state, event).items
  },
}

/**
 * Full replay helper that maintains hitlLabels across events.
 * Use this when replaying an entire event log to ensure HITL answered labels are correct.
 */
export function replayRail(events: TurnEvent[]): RailItemView[] {
  let state = init()
  for (const event of events) {
    state = reduce(state, event)
  }
  return state.items
}
