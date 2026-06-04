import { create } from 'zustand'
import type { PendingAttachment } from '@/lib/types/attachments'
import type { MessageDispatchInput } from '@/lib/types/agent-group'

type RoomId = string

interface PendingRoomData {
  initialMessage: string
  dispatch?: MessageDispatchInput
  /** @deprecated Use dispatch instead. */
  targetGroup?: string
  attachments?: PendingAttachment[]
  handoffMode?: "autosend" | "prefill"
}

export interface PendingTurnSkeleton {
  text: string
  attachments?: PendingAttachment[]
}

export interface RoomFlags {
  sending: boolean
  processing: boolean
  cancelling: boolean
  updatingRoom: boolean
  sseEnabled: boolean
  sseConnected: boolean
  sseError: string | null
  turnBasedTimeline: boolean
}

export const DEFAULT_ROOM_FLAGS: RoomFlags = {
  sending: false,
  processing: false,
  cancelling: false,
  updatingRoom: false,
  sseEnabled: true,
  sseConnected: false,
  sseError: null,
  turnBasedTimeline: false,
}

function patchRoom(rooms: Record<RoomId, RoomFlags>, roomId: RoomId, patch: Partial<RoomFlags>): Record<RoomId, RoomFlags> {
  return { ...rooms, [roomId]: { ...(rooms[roomId] ?? DEFAULT_ROOM_FLAGS), ...patch } }
}

interface RoomUiState {
  rooms: Record<RoomId, RoomFlags>
  /** Pending initial messages for rooms (replaces sessionStorage) */
  pendingRoomData: Record<RoomId, PendingRoomData>
  pendingTurnSkeletons: Record<RoomId, PendingTurnSkeleton | undefined>
  localSendSeqByRoom: Record<RoomId, number>
  initialHydrationSeqByRoom: Record<RoomId, number>
  selectedAgentMessageIdByRoom: Record<RoomId, string | undefined>

  // Per-room flag setters (roomId, value)
  setSending: (roomId: RoomId, v: boolean) => void
  setProcessing: (roomId: RoomId, v: boolean) => void
  setCancelling: (roomId: RoomId, v: boolean) => void
  setUpdatingRoom: (roomId: RoomId, v: boolean) => void
  setSseEnabled: (roomId: RoomId, v: boolean) => void
  setSseConnected: (roomId: RoomId, v: boolean) => void
  setSseError: (roomId: RoomId, v: string | null) => void
  setTurnBasedTimeline: (roomId: RoomId, v: boolean) => void

  // Non-reactive getter for getState() callers
  getRoomFlags: (roomId: RoomId) => RoomFlags
  // Delete a single room's entry (falls back to defaults on next read)
  resetRoom: (roomId: RoomId) => void
  resetAll: () => void

  /** Store a pending initial message + target group for a room */
  setPendingRoomData: (roomId: RoomId, data: PendingRoomData) => void
  /** Consume (read + delete) pending data for a room */
  consumePendingRoomData: (roomId: RoomId) => PendingRoomData | null
  setPendingTurnSkeleton: (roomId: RoomId, value?: PendingTurnSkeleton) => void
  markLocalSend: (roomId: RoomId) => void
  markInitialHydrated: (roomId: RoomId) => void
  openAgentDetail: (roomId: RoomId, messageId: string) => void
  closeAgentDetail: (roomId: RoomId) => void
}

function readLocalStorageBool(key: string, fallback: boolean): boolean {
  if (typeof window === 'undefined') return fallback
  try { return localStorage.getItem(key) === 'true' } catch { return fallback }
}

export const useRoomUiStore = create<RoomUiState>((set, get) => ({
  rooms: {},
  pendingRoomData: {},
  pendingTurnSkeletons: {},
  localSendSeqByRoom: {},
  initialHydrationSeqByRoom: {},
  selectedAgentMessageIdByRoom: {},

  setSending: (roomId, v) => set(s => ({ rooms: patchRoom(s.rooms, roomId, { sending: v }) })),
  setProcessing: (roomId, v) => set(s => ({ rooms: patchRoom(s.rooms, roomId, { processing: v }) })),
  setCancelling: (roomId, v) => set(s => ({ rooms: patchRoom(s.rooms, roomId, { cancelling: v }) })),
  setUpdatingRoom: (roomId, v) => set(s => ({ rooms: patchRoom(s.rooms, roomId, { updatingRoom: v }) })),
  setSseEnabled: (roomId, v) => set(s => ({ rooms: patchRoom(s.rooms, roomId, { sseEnabled: v }) })),
  setSseConnected: (roomId, v) => set(s => ({ rooms: patchRoom(s.rooms, roomId, { sseConnected: v }) })),
  setSseError: (roomId, v) => set(s => ({ rooms: patchRoom(s.rooms, roomId, { sseError: v }) })),
  setTurnBasedTimeline: (roomId, v) => set(s => ({ rooms: patchRoom(s.rooms, roomId, { turnBasedTimeline: v }) })),

  getRoomFlags: (roomId) => get().rooms[roomId] ?? DEFAULT_ROOM_FLAGS,

  resetRoom: (roomId) =>
    set(s => {
      const rooms = { ...s.rooms }
      delete rooms[roomId]
      const localSendSeqByRoom = { ...s.localSendSeqByRoom }
      delete localSendSeqByRoom[roomId]
      const initialHydrationSeqByRoom = { ...s.initialHydrationSeqByRoom }
      delete initialHydrationSeqByRoom[roomId]
      const selectedAgentMessageIdByRoom = { ...s.selectedAgentMessageIdByRoom }
      delete selectedAgentMessageIdByRoom[roomId]
      return { rooms, localSendSeqByRoom, initialHydrationSeqByRoom, selectedAgentMessageIdByRoom }
    }),

  resetAll: () =>
    set({
      rooms: {},
      pendingRoomData: {},
      pendingTurnSkeletons: {},
      localSendSeqByRoom: {},
      initialHydrationSeqByRoom: {},
      selectedAgentMessageIdByRoom: {},
    }),

  setPendingRoomData: (roomId, data) =>
    set((state) => ({
      pendingRoomData: {
        ...state.pendingRoomData,
        [roomId]: data,
      },
    })),
  consumePendingRoomData: (roomId) => {
    const data = get().pendingRoomData[roomId] || null
    if (data) {
      set((state) => {
        const copy = { ...state.pendingRoomData }
        delete copy[roomId]
        return { pendingRoomData: copy }
      })
    }
    return data
  },
  setPendingTurnSkeleton: (roomId, value) =>
    set((state) => {
      const copy = { ...state.pendingTurnSkeletons }
      if (!value) delete copy[roomId]
      else copy[roomId] = value
      return { pendingTurnSkeletons: copy }
    }),
  markLocalSend: (roomId) =>
    set((state) => ({
      localSendSeqByRoom: {
        ...state.localSendSeqByRoom,
        [roomId]: (state.localSendSeqByRoom[roomId] ?? 0) + 1,
      },
    })),
  markInitialHydrated: (roomId) =>
    set((state) => ({
      initialHydrationSeqByRoom: {
        ...state.initialHydrationSeqByRoom,
        [roomId]: (state.initialHydrationSeqByRoom[roomId] ?? 0) + 1,
      },
    })),
  openAgentDetail: (roomId, messageId) =>
    set((state) => ({
      selectedAgentMessageIdByRoom: {
        ...state.selectedAgentMessageIdByRoom,
        [roomId]: messageId,
      },
    })),
  closeAgentDetail: (roomId) =>
    set((state) => {
      const selectedAgentMessageIdByRoom = { ...state.selectedAgentMessageIdByRoom }
      delete selectedAgentMessageIdByRoom[roomId]
      return { selectedAgentMessageIdByRoom }
    }),
}))

/** Narrow selector: room processing lifecycle flag only. */
export function useRoomProcessing(roomId: string): boolean {
  return useRoomUiStore((s) => (s.rooms[roomId] ?? DEFAULT_ROOM_FLAGS).processing)
}

export function useRoomSending(roomId: string): boolean {
  return useRoomUiStore((s) => (s.rooms[roomId] ?? DEFAULT_ROOM_FLAGS).sending)
}

export function useRoomCancelling(roomId: string): boolean {
  return useRoomUiStore((s) => (s.rooms[roomId] ?? DEFAULT_ROOM_FLAGS).cancelling)
}

export function useRoomUpdating(roomId: string): boolean {
  return useRoomUiStore((s) => (s.rooms[roomId] ?? DEFAULT_ROOM_FLAGS).updatingRoom)
}

export function useRoomSseEnabled(roomId: string): boolean {
  return useRoomUiStore((s) => (s.rooms[roomId] ?? DEFAULT_ROOM_FLAGS).sseEnabled)
}

export function useLocalSendSeq(roomId: string): number {
  return useRoomUiStore(s => s.localSendSeqByRoom[roomId] ?? 0)
}

export function useInitialHydrationSeq(roomId: string): number {
  return useRoomUiStore(s => s.initialHydrationSeqByRoom[roomId] ?? 0)
}

export function useSelectedAgentMessageId(roomId: string): string | undefined {
  return useRoomUiStore(s => s.selectedAgentMessageIdByRoom[roomId])
}
