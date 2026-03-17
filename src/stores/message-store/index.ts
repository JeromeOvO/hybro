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
  replaceMessageId: (oldId: string, newId: string, updates?: Partial<IncomingMessage>) => void
  findByClientRequestId: (clientRequestId: string) => MessageEntity | undefined
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
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      const { [id]: _removed, ...rest } = state.entities
      return {
        entities: rest,
        orderedIds: state.orderedIds.filter(oid => oid !== id),
        version: state.version + 1,
      }
    }),

    replaceMessageId: (oldId, newId, updates) => set((state) => {
      const old = state.entities[oldId]
      const existingReal = state.entities[newId]

      // Case D: neither exists — true no-op
      if (!old && !existingReal) return state

      // Extract entity-compatible fields from updates
      const patch: Partial<MessageEntity> = updates
        ? {
            content: updates.content,
            senderName: updates.senderName,
            userId: updates.userId,
            clientRequestId: updates.clientRequestId,
            attachments: updates.attachments,
            timestamp: updates.timestamp,
          }
        : {}
      // Remove undefined keys so spread doesn't overwrite with undefined
      for (const k of Object.keys(patch) as (keyof typeof patch)[]) {
        if (patch[k] === undefined) delete patch[k]
      }

      // Start from entities minus the old entry (if it exists)
      const rest = { ...state.entities }
      if (old) delete rest[oldId]

      let merged: MessageEntity
      if (old && existingReal) {
        // Case B: SSE already created the real entity while temp still exists
        merged = {
          ...existingReal,
          ...patch,
          id: newId,
          content: patch.content ?? old.content ?? existingReal.content,
          attachments: patch.attachments ?? old.attachments ?? existingReal.attachments,
          clientRequestId: patch.clientRequestId ?? old.clientRequestId ?? existingReal.clientRequestId,
          sourceVersion: existingReal.sourceVersion + 1,
          updatedAt: Date.now(),
        }
      } else if (old) {
        // Case A: Normal swap — real entity doesn't exist yet
        merged = {
          ...old,
          ...patch,
          id: newId,
          sourceVersion: old.sourceVersion + 1,
          updatedAt: Date.now(),
        }
      } else {
        // Case C: temp already gone (SSE handler swapped first), but real exists
        merged = {
          ...existingReal!,
          ...patch,
          id: newId,
          sourceVersion: existingReal!.sourceVersion + 1,
          updatedAt: Date.now(),
        }
      }

      rest[newId] = merged
      return {
        entities: rest,
        orderedIds: buildSortedIds(rest),
        version: state.version + 1,
      }
    }),

    findByClientRequestId: (clientRequestId: string): MessageEntity | undefined => {
      const ents = get().entities
      return Object.values(ents).find(e => e.clientRequestId === clientRequestId)
    },

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
