import { create } from 'zustand'
import type { MessageData } from '@/components/room-messages'

type RoomId = string

interface RoomUiState {
  sending: boolean
  processing: boolean
  updatingRoom: boolean
  sseEnabled: boolean
  sseConnected: boolean
  sseError: string | null
  liveMessagesByRoom: Record<RoomId, MessageData[]>
  setSending: (v: boolean) => void
  setProcessing: (v: boolean) => void
  setUpdatingRoom: (v: boolean) => void
  setSseEnabled: (v: boolean) => void
  setSseConnected: (v: boolean) => void
  setSseError: (v: string | null) => void
  addLiveMessage: (roomId: RoomId, msg: MessageData) => void
  replaceLiveMessage: (roomId: RoomId, tempId: string, msg: MessageData) => void
  removeLiveMessage: (roomId: RoomId, messageId: string) => void
  resetRoomState: (roomId: RoomId) => void
  resetAll: () => void
}

export const useRoomUiStore = create<RoomUiState>((set) => ({
  sending: false,
  processing: false,
  updatingRoom: false,
  sseEnabled: true,
  sseConnected: false,
  sseError: null,
  liveMessagesByRoom: {},
  setSending: (v) => set({ sending: v }),
  setProcessing: (v) => set({ processing: v }),
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
  resetRoomState: (roomId) =>
    set((state) => {
      const copy = { ...state.liveMessagesByRoom }
      delete copy[roomId]
      return {
        liveMessagesByRoom: copy,
        sending: false,
        processing: false,
        updatingRoom: false,
        sseConnected: false,
        sseError: null,
      }
    }),
  resetAll: () =>
    set({
      liveMessagesByRoom: {},
      sending: false,
      processing: false,
      updatingRoom: false,
      sseConnected: false,
      sseError: null,
      sseEnabled: true,
    }),
}))

