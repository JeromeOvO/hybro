import { create } from 'zustand'
import type { ArtifactData } from '@/stores/message-store/types'
import { mergeArtifacts, extractTextFromArtifacts } from '@/stores/message-store/upsert'

/**
 * Ephemeral display buffer for a single streaming message.
 * Lives only in this store — never written to messageStore.
 * Discarded when task_update (checkpoint) or DB reconcile fires.
 */
export interface StreamBuffer {
  /** Accumulated text for live render, derived from artifacts via extractTextFromArtifacts. */
  text: string
  /** Accumulated artifacts for live render (non-text artifact cards). */
  artifacts: ArtifactData[]
  /** True after last_chunk=true is received. Render uses this for isStreaming flag. */
  isComplete: boolean
}

interface StreamingState {
  buffers: Record<string, StreamBuffer>

  /**
   * Append an incoming artifact chunk to the buffer for message_id.
   * Creates the buffer if it does not yet exist.
   * Mirrors the mergeArtifacts + extractTextFromArtifacts pipeline from
   * message-store/upsert.ts but without any token filtering — buffer
   * corruption only affects the transient display, never permanent entity state.
   */
  append: (id: string, chunk: ArtifactData, isAppend: boolean) => void

  /** Mark the buffer complete after last_chunk=true arrives. */
  markComplete: (id: string) => void

  /**
   * Clear the buffer for a single message_id.
   * Called by the task_update SSE handler after writing the checkpoint to messageStore.
   */
  clear: (id: string) => void

  /**
   * Clear all buffers that belong to a given room.
   * Called after reconcileWithDb writes DB-canonical content to messageStore
   * (processing_status terminal, run_failed/completed/canceled, room switch).
   * Only room-keyed buffers are cleared; buffers from other rooms are preserved.
   *
   * Note: StreamBuffer does not carry roomId. This action clears ALL buffers
   * whose message_id is listed in the provided set.
   */
  clearRoom: (messageIds: ReadonlySet<string>) => void
}

export const useStreamingStore = create<StreamingState>()((set) => ({
  buffers: {},

  append: (id, chunk, isAppend) => set((state) => {
    const existing = state.buffers[id]
    const prevArtifacts = existing?.artifacts ?? []
    const nextArtifacts = mergeArtifacts(prevArtifacts, chunk, isAppend)
    const text = extractTextFromArtifacts(nextArtifacts)
    return {
      buffers: {
        ...state.buffers,
        [id]: {
          text,
          artifacts: nextArtifacts,
          isComplete: existing?.isComplete ?? false,
        },
      },
    }
  }),

  markComplete: (id) => set((state) => {
    const existing = state.buffers[id]
    if (!existing || existing.isComplete) return state
    return {
      buffers: {
        ...state.buffers,
        [id]: { ...existing, isComplete: true },
      },
    }
  }),

  clear: (id) => set((state) => {
    if (!(id in state.buffers)) return state
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    const { [id]: _removed, ...rest } = state.buffers
    return { buffers: rest }
  }),

  clearRoom: (messageIds) => set((state) => {
    const nextBuffers: Record<string, StreamBuffer> = {}
    let changed = false
    for (const [id, buf] of Object.entries(state.buffers)) {
      if (messageIds.has(id)) {
        changed = true
      } else {
        nextBuffers[id] = buf
      }
    }
    return changed ? { buffers: nextBuffers } : state
  }),
}))
