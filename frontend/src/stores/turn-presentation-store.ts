import { create } from 'zustand'
import type { TurnProjection } from '@/lib/turn-lifecycle/types'

export interface TurnPresentationState {
  expanded: boolean
  manualAction: 'collapsed' | 'expanded' | null
  autoCollapseConsumed: boolean
  pinnedToBottom: boolean
}

interface TurnPresentationStore {
  turns: Record<string, TurnPresentationState>
  ensure: (turn: TurnProjection, historical: boolean) => void
  setExpanded: (turnId: string, expanded: boolean, manual?: boolean) => void
  consumeAutoCollapse: (turnId: string, collapse: boolean) => void
  setPinnedToBottom: (turnId: string, pinned: boolean) => void
  clear: () => void
}

const activeDefault = (): TurnPresentationState => ({
  expanded: true,
  manualAction: null,
  autoCollapseConsumed: false,
  pinnedToBottom: true,
})

export const useTurnPresentationStore = create<TurnPresentationStore>((set) => ({
  turns: {},
  ensure: (turn, historical) => set((state) => {
    if (state.turns[turn.id]) return state
    const terminal = ['completed', 'failed', 'canceled'].includes(turn.state)
    return {
      turns: {
        ...state.turns,
        [turn.id]: terminal && historical
          ? {
              expanded: false,
              manualAction: null,
              autoCollapseConsumed: true,
              pinnedToBottom: true,
            }
          : activeDefault(),
      },
    }
  }),
  setExpanded: (turnId, expanded, manual = true) => set((state) => {
    const current = state.turns[turnId] ?? activeDefault()
    return {
      turns: {
        ...state.turns,
        [turnId]: {
          ...current,
          expanded,
          manualAction: manual ? (expanded ? 'expanded' : 'collapsed') : current.manualAction,
        },
      },
    }
  }),
  consumeAutoCollapse: (turnId, collapse) => set((state) => {
    const current = state.turns[turnId] ?? activeDefault()
    if (current.autoCollapseConsumed) return state
    return {
      turns: {
        ...state.turns,
        [turnId]: {
          ...current,
          expanded: collapse ? false : current.expanded,
          autoCollapseConsumed: true,
        },
      },
    }
  }),
  setPinnedToBottom: (turnId, pinnedToBottom) => set((state) => {
    const current = state.turns[turnId] ?? activeDefault()
    if (current.pinnedToBottom === pinnedToBottom) return state
    return { turns: { ...state.turns, [turnId]: { ...current, pinnedToBottom } } }
  }),
  clear: () => set({ turns: {} }),
}))
