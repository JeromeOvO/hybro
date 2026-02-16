import { create } from 'zustand'
import type { MessageData } from '@/components/room-messages'

type RoomId = string

interface PendingRoomData {
  initialMessage: string
  targetGroup?: string
}

interface RoomUiState {
  sending: boolean
  processing: boolean
  cancelling: boolean
  updatingRoom: boolean
  sseEnabled: boolean
  sseConnected: boolean
  sseError: string | null
  liveMessagesByRoom: Record<RoomId, MessageData[]>
  /** Pending initial messages for rooms (replaces sessionStorage) */
  pendingRoomData: Record<RoomId, PendingRoomData>
  setSending: (v: boolean) => void
  setProcessing: (v: boolean) => void
  setCancelling: (v: boolean) => void
  setUpdatingRoom: (v: boolean) => void
  setSseEnabled: (v: boolean) => void
  setSseConnected: (v: boolean) => void
  setSseError: (v: string | null) => void
  addLiveMessage: (roomId: RoomId, msg: MessageData) => void
  replaceLiveMessage: (roomId: RoomId, tempId: string, msg: MessageData) => void
  removeLiveMessage: (roomId: RoomId, messageId: string) => void
  /** Reset live messages & SSE state for a room (does not touch pendingRoomData). */
  resetRoomLiveState: (roomId: RoomId) => void
  resetAll: () => void
  /** Store a pending initial message + target group for a room */
  setPendingRoomData: (roomId: RoomId, data: PendingRoomData) => void
  /** Consume (read + delete) pending data for a room */
  consumePendingRoomData: (roomId: RoomId) => PendingRoomData | null
}

export const useRoomUiStore = create<RoomUiState>((set, get) => ({
  sending: false,
  processing: false,
  cancelling: false,
  updatingRoom: false,
  sseEnabled: true,
  sseConnected: false,
  sseError: null,
  liveMessagesByRoom: {},
  pendingRoomData: {},
  setSending: (v) => set({ sending: v }),
  setProcessing: (v) => set({ processing: v }),
  setCancelling: (v) => set({ cancelling: v }),
  setUpdatingRoom: (v) => set({ updatingRoom: v }),
  setSseEnabled: (v) => set({ sseEnabled: v }),
  setSseConnected: (v) => set({ sseConnected: v }),
  setSseError: (v) => set({ sseError: v }),
  addLiveMessage: (roomId, msg) =>
    set((state) => {
      const existing = state.liveMessagesByRoom[roomId] || []
      // dedupe by id
      if (existing.some((m) => m.id === msg.id)) {
        return state
      }
      return {
        liveMessagesByRoom: {
          ...state.liveMessagesByRoom,
          [roomId]: [...existing, msg],
        },
      }
    }),
  replaceLiveMessage: (roomId, tempId, msg) =>
    set((state) => {
      const existing = state.liveMessagesByRoom[roomId] || []
      const withoutTemp = existing.filter((m) => m.id !== tempId)
      const deduped = withoutTemp.some((m) => m.id === msg.id)
        ? withoutTemp
        : [...withoutTemp, msg]
      return {
        liveMessagesByRoom: {
          ...state.liveMessagesByRoom,
          [roomId]: deduped,
        },
      }
    }),
  removeLiveMessage: (roomId, messageId) =>
    set((state) => {
      const existing = state.liveMessagesByRoom[roomId] || []
      const filtered = existing.filter((m) => m.id !== messageId)
      return {
        liveMessagesByRoom: {
          ...state.liveMessagesByRoom,
          [roomId]: filtered,
        },
      }
    }),
  // Reset live messages & SSE state for a room.
  resetRoomLiveState: (roomId) =>
    set((state) => {
      const liveCopy = { ...state.liveMessagesByRoom }
      delete liveCopy[roomId]
      return {
        liveMessagesByRoom: liveCopy,
        sending: false,
        processing: false,
        cancelling: false,
        updatingRoom: false,
        sseConnected: false,
        sseError: null,
      }
    }),
  resetAll: () =>
    set({
      liveMessagesByRoom: {},
      pendingRoomData: {},
      sending: false,
      processing: false,
      cancelling: false,
      updatingRoom: false,
      sseConnected: false,
      sseError: null,
      sseEnabled: true,
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
}))

