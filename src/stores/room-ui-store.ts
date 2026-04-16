import { create } from 'zustand'
import { useShallow } from 'zustand/react/shallow'
import type { PendingAttachment } from '@/lib/types/attachments'

type RoomId = string

interface PendingRoomData {
  initialMessage: string
  targetGroup?: string
  attachments?: PendingAttachment[]
  handoffMode?: "autosend" | "prefill"
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
  /** Global user preference: use turn-based timeline for all rooms */
  globalTurnBasedTimeline: boolean

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
  /** Set global turn-based timeline preference (persisted to localStorage) */
  setGlobalTurnBasedTimeline: (v: boolean) => void
}

function readLocalStorageBool(key: string, fallback: boolean): boolean {
  if (typeof window === 'undefined') return fallback
  try { return localStorage.getItem(key) === 'true' } catch { return fallback }
}

export const useRoomUiStore = create<RoomUiState>((set, get) => ({
  rooms: {},
  pendingRoomData: {},
  globalTurnBasedTimeline: readLocalStorageBool('hybro:turnBasedTimeline', false),

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
      const copy = { ...s.rooms }
      delete copy[roomId]
      return { rooms: copy }
    }),

  resetAll: () =>
    set({
      rooms: {},
      pendingRoomData: {},
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
  setGlobalTurnBasedTimeline: (v) => {
    set({ globalTurnBasedTimeline: v })
    try { localStorage.setItem('hybro:turnBasedTimeline', String(v)) } catch { /* ignore */ }
  },
}))

/** Reactive hook that returns room-scoped flags with shallow equality. */
export function useRoomFlags(roomId: string): RoomFlags {
  return useRoomUiStore(useShallow(s => s.rooms[roomId] ?? DEFAULT_ROOM_FLAGS))
}
