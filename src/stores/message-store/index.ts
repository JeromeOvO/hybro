import { create } from 'zustand'
import { subscribeWithSelector } from 'zustand/middleware'
import { isTerminalState } from '@/lib/types/sse'
import type { TaskState } from '@/lib/types/sse'
import type { MessageEntity, IncomingMessage, MessageSource } from './types'
import { applyUpsert, buildSortedIds } from './upsert'
import { resolveDisplayType } from './resolve-display-type'

export type { MessageEntity, IncomingMessage, MessageSource } from './types'
export type { DisplayType } from './types'
export { resolveDisplayType } from './resolve-display-type'
export { detectAndMarkStaleTasks } from './stale-detection'
export { filterHydrationMessages } from './hydration-filter'
export { convertApiMessageToIncoming } from './convert-api-message'
export type { ConvertApiMessageOptions } from './convert-api-message'

interface MessageStoreState {
  // ── Per-room entity storage ───────────────────────────────
  entities: Record<string, MessageEntity>
  orderedIds: string[]
  roomId: string | null

  // ── Sync metadata ────────────────────────────────────────
  hydratedFromDb: boolean
  lastDbSyncAt: number | null
  version: number

  // ── Write operations ─────────────────────────────────────
  upsertMessage: (msg: IncomingMessage, source: MessageSource) => void
  upsertMany: (msgs: IncomingMessage[], source: MessageSource) => void
  removeMessage: (id: string) => void
  cancelAllNonTerminal: (roomId: string) => void
  setRoom: (roomId: string) => void
  clearRoom: () => void
  markDbSynced: () => void
}

const INITIAL_STATE = {
  entities: {} as Record<string, MessageEntity>,
  orderedIds: [] as string[],
  roomId: null as string | null,
  hydratedFromDb: false,
  lastDbSyncAt: null as number | null,
  version: 0,
}

export const useMessageStore = create<MessageStoreState>()(
  subscribeWithSelector((set, get) => ({
    ...INITIAL_STATE,

    upsertMessage: (incoming, source) => {
      set((state) => {
        const result = applyUpsert(state.entities, state.orderedIds, incoming, source)
        if (!result) return state

        const newOrderedIds = result.idsChanged
          ? buildSortedIds(result.entities)
          : state.orderedIds

        return {
          entities: result.entities,
          orderedIds: newOrderedIds,
          version: state.version + 1,
        }
      })
    },

    upsertMany: (msgs, source) => {
      set((state) => {
        let newEntities = { ...state.entities }
        let idsChanged = false
        let anyChanged = false

        for (const incoming of msgs) {
          const result = applyUpsert(newEntities, state.orderedIds, incoming, source)
          if (result) {
            newEntities = result.entities
            idsChanged = idsChanged || result.idsChanged
            anyChanged = true
          }
        }

        if (!anyChanged) return state

        const newOrderedIds = idsChanged
          ? buildSortedIds(newEntities)
          : state.orderedIds

        return {
          entities: newEntities,
          orderedIds: newOrderedIds,
          version: state.version + 1,
        }
      })
    },

    removeMessage: (id) => set((state) => {
      if (!state.entities[id]) return state
      const { [id]: _, ...rest } = state.entities
      return {
        entities: rest,
        orderedIds: state.orderedIds.filter(oid => oid !== id),
        version: state.version + 1,
      }
    }),

    /**
     * Batch-cancel all non-terminal tasks in a room.
     * Uses resolveDisplayType instead of hardcoding displayType (Gap 19).
     */
    cancelAllNonTerminal: (roomId) => set((state) => {
      let changed = false
      const newEntities = { ...state.entities }

      for (const [id, entity] of Object.entries(newEntities)) {
        if (
          entity.roomId === roomId &&
          entity.taskStatus &&
          !isTerminalState(entity.taskStatus) &&
          !entity.isEphemeral
        ) {
          newEntities[id] = {
            ...entity,
            taskStatus: 'canceled' as TaskState,
            displayType: resolveDisplayType({
              messageType: entity.messageType,
              taskStatus: 'canceled' as TaskState,
              content: entity.content,
              isEphemeral: entity.isEphemeral,
            }),
            sourceVersion: entity.sourceVersion + 1,
            updatedAt: Date.now(),
          }
          changed = true
        }
      }

      return changed
        ? { entities: newEntities, version: state.version + 1 }
        : state
    }),

    setRoom: (roomId) => set({
      ...INITIAL_STATE,
      roomId,
    }),

    clearRoom: () => set({
      ...INITIAL_STATE,
    }),

    markDbSynced: () => set({
      hydratedFromDb: true,
      lastDbSyncAt: Date.now(),
    }),
  }))
)
