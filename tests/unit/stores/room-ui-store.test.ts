import { describe, it, expect, beforeEach } from 'vitest'
import { useRoomUiStore } from '@/stores/room-ui-store'

describe('RoomUiStore', () => {
  beforeEach(() => {
    useRoomUiStore.getState().resetAll()
  })

  describe('boolean flags', () => {
    it('should start with default values', () => {
      const state = useRoomUiStore.getState()
      expect(state.sending).toBe(false)
      expect(state.processing).toBe(false)
      expect(state.cancelling).toBe(false)
      expect(state.updatingRoom).toBe(false)
      expect(state.sseEnabled).toBe(true)
      expect(state.sseConnected).toBe(false)
      expect(state.sseError).toBeNull()
    })

    it('should update sending flag', () => {
      useRoomUiStore.getState().setSending(true)
      expect(useRoomUiStore.getState().sending).toBe(true)
      useRoomUiStore.getState().setSending(false)
      expect(useRoomUiStore.getState().sending).toBe(false)
    })

    it('should update processing flag', () => {
      useRoomUiStore.getState().setProcessing(true)
      expect(useRoomUiStore.getState().processing).toBe(true)
    })

    it('should update cancelling flag', () => {
      useRoomUiStore.getState().setCancelling(true)
      expect(useRoomUiStore.getState().cancelling).toBe(true)
    })

    it('should update updatingRoom flag', () => {
      useRoomUiStore.getState().setUpdatingRoom(true)
      expect(useRoomUiStore.getState().updatingRoom).toBe(true)
    })

    it('should update SSE flags', () => {
      useRoomUiStore.getState().setSseEnabled(false)
      expect(useRoomUiStore.getState().sseEnabled).toBe(false)

      useRoomUiStore.getState().setSseConnected(true)
      expect(useRoomUiStore.getState().sseConnected).toBe(true)

      useRoomUiStore.getState().setSseError('Connection failed')
      expect(useRoomUiStore.getState().sseError).toBe('Connection failed')
    })
  })

  describe('resetAll', () => {
    it('should reset all state to defaults', () => {
      const store = useRoomUiStore.getState()
      store.setSending(true)
      store.setProcessing(true)
      store.setCancelling(true)
      store.setUpdatingRoom(true)
      store.setSseEnabled(false)
      store.setSseConnected(true)
      store.setSseError('error')
      store.setPendingRoomData('room-1', { initialMessage: 'hi' })

      store.resetAll()

      const reset = useRoomUiStore.getState()
      expect(reset.sending).toBe(false)
      expect(reset.processing).toBe(false)
      expect(reset.cancelling).toBe(false)
      expect(reset.updatingRoom).toBe(false)
      expect(reset.sseEnabled).toBe(true)
      expect(reset.sseConnected).toBe(false)
      expect(reset.sseError).toBeNull()
      expect(reset.pendingRoomData).toEqual({})
    })
  })

  describe('pendingRoomData', () => {
    it('should store pending data for a room', () => {
      useRoomUiStore.getState().setPendingRoomData('room-1', {
        initialMessage: 'Hello',
        targetGroup: 'room_team',
      })

      const data = useRoomUiStore.getState().pendingRoomData['room-1']
      expect(data).toEqual({ initialMessage: 'Hello', targetGroup: 'room_team' })
    })

    it('should store data for multiple rooms independently', () => {
      const store = useRoomUiStore.getState()
      store.setPendingRoomData('room-1', { initialMessage: 'msg1' })
      store.setPendingRoomData('room-2', { initialMessage: 'msg2' })

      expect(useRoomUiStore.getState().pendingRoomData['room-1']?.initialMessage).toBe('msg1')
      expect(useRoomUiStore.getState().pendingRoomData['room-2']?.initialMessage).toBe('msg2')
    })

    it('should consume (read and delete) pending data', () => {
      useRoomUiStore.getState().setPendingRoomData('room-1', { initialMessage: 'test' })

      const consumed = useRoomUiStore.getState().consumePendingRoomData('room-1')
      expect(consumed).toEqual({ initialMessage: 'test' })
      expect(useRoomUiStore.getState().pendingRoomData['room-1']).toBeUndefined()
    })

    it('should return null when consuming non-existent data', () => {
      const consumed = useRoomUiStore.getState().consumePendingRoomData('room-999')
      expect(consumed).toBeNull()
    })

    it('should not affect other rooms when consuming', () => {
      const store = useRoomUiStore.getState()
      store.setPendingRoomData('room-1', { initialMessage: 'keep' })
      store.setPendingRoomData('room-2', { initialMessage: 'consume' })

      useRoomUiStore.getState().consumePendingRoomData('room-2')

      expect(useRoomUiStore.getState().pendingRoomData['room-1']?.initialMessage).toBe('keep')
      expect(useRoomUiStore.getState().pendingRoomData['room-2']).toBeUndefined()
    })

    it('should overwrite pending data for same room', () => {
      const store = useRoomUiStore.getState()
      store.setPendingRoomData('room-1', { initialMessage: 'old' })
      store.setPendingRoomData('room-1', { initialMessage: 'new', targetGroup: 'custom' })

      const data = useRoomUiStore.getState().pendingRoomData['room-1']
      expect(data).toEqual({ initialMessage: 'new', targetGroup: 'custom' })
    })
  })
})
