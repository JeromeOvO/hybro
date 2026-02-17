import { create } from 'zustand'

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
  /** Pending initial messages for rooms (replaces sessionStorage) */
  pendingRoomData: Record<RoomId, PendingRoomData>
  setSending: (v: boolean) => void
  setProcessing: (v: boolean) => void
  setCancelling: (v: boolean) => void
  setUpdatingRoom: (v: boolean) => void
  setSseEnabled: (v: boolean) => void
  setSseConnected: (v: boolean) => void
  setSseError: (v: string | null) => void
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
  pendingRoomData: {},
  setSending: (v) => set({ sending: v }),
  setProcessing: (v) => set({ processing: v }),
  setCancelling: (v) => set({ cancelling: v }),
  setUpdatingRoom: (v) => set({ updatingRoom: v }),
  setSseEnabled: (v) => set({ sseEnabled: v }),
  setSseConnected: (v) => set({ sseConnected: v }),
  setSseError: (v) => set({ sseError: v }),
  resetAll: () =>
    set({
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
