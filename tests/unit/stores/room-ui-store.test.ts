import { describe, it, expect, beforeEach } from 'vitest'
import { useRoomUiStore } from '@/stores/room-ui-store'

const flags = (roomId = 'room-1') => useRoomUiStore.getState().getRoomFlags(roomId)

describe('RoomUiStore', () => {
  beforeEach(() => {
    useRoomUiStore.getState().resetAll()
  })

  describe('boolean flags', () => {
    it('should start with default values', () => {
      const f = flags()
      expect(f.sending).toBe(false)
      expect(f.processing).toBe(false)
      expect(f.cancelling).toBe(false)
      expect(f.updatingRoom).toBe(false)
      expect(f.sseEnabled).toBe(true)
      expect(f.sseConnected).toBe(false)
      expect(f.sseError).toBeNull()
    })

    it('should update sending flag', () => {
      useRoomUiStore.getState().setSending('room-1', true)
      expect(flags().sending).toBe(true)
      useRoomUiStore.getState().setSending('room-1', false)
      expect(flags().sending).toBe(false)
    })

    it('should update processing flag', () => {
      useRoomUiStore.getState().setProcessing('room-1', true)
      expect(flags().processing).toBe(true)
    })

    it('should update cancelling flag', () => {
      useRoomUiStore.getState().setCancelling('room-1', true)
      expect(flags().cancelling).toBe(true)
    })

    it('should update updatingRoom flag', () => {
      useRoomUiStore.getState().setUpdatingRoom('room-1', true)
      expect(flags().updatingRoom).toBe(true)
    })

    it('should update SSE flags', () => {
      useRoomUiStore.getState().setSseEnabled('room-1', false)
      expect(flags().sseEnabled).toBe(false)

      useRoomUiStore.getState().setSseConnected('room-1', true)
      expect(flags().sseConnected).toBe(true)

      useRoomUiStore.getState().setSseError('room-1', 'Connection failed')
      expect(flags().sseError).toBe('Connection failed')
    })
  })

  describe('room isolation', () => {
    it('flags set on one room do not affect another', () => {
      useRoomUiStore.getState().setSending('room-1', true)
      useRoomUiStore.getState().setProcessing('room-1', true)
      expect(flags('room-1').sending).toBe(true)
      expect(flags('room-1').processing).toBe(true)
      expect(flags('room-2').sending).toBe(false)
      expect(flags('room-2').processing).toBe(false)
    })
  })

  describe('resetRoom', () => {
    it('deletes a single room entry, leaving others untouched', () => {
      useRoomUiStore.getState().setSending('room-1', true)
      useRoomUiStore.getState().setProcessing('room-2', true)

      useRoomUiStore.getState().resetRoom('room-1')

      // room-1 returns defaults
      expect(flags('room-1').sending).toBe(false)
      // room-2 untouched
      expect(flags('room-2').processing).toBe(true)
    })
  })

  describe('resetAll', () => {
    it('should reset all state to defaults', () => {
      const store = useRoomUiStore.getState()
      store.setSending('room-1', true)
      store.setProcessing('room-1', true)
      store.setCancelling('room-1', true)
      store.setUpdatingRoom('room-1', true)
      store.setSseEnabled('room-1', false)
      store.setSseConnected('room-1', true)
      store.setSseError('room-1', 'error')
      store.setPendingRoomData('room-1', { initialMessage: 'hi' })

      store.resetAll()

      const f = flags()
      expect(f.sending).toBe(false)
      expect(f.processing).toBe(false)
      expect(f.cancelling).toBe(false)
      expect(f.updatingRoom).toBe(false)
      expect(f.sseEnabled).toBe(true)
      expect(f.sseConnected).toBe(false)
      expect(f.sseError).toBeNull()
      expect(useRoomUiStore.getState().pendingRoomData).toEqual({})
      expect(useRoomUiStore.getState().rooms).toEqual({})
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
