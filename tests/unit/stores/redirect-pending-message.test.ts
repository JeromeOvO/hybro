import { describe, it, expect, beforeEach } from 'vitest'
import { useRoomUiStore } from '@/stores/room-ui-store'

describe('Room redirect – pending message in empty room', () => {
  beforeEach(() => {
    useRoomUiStore.getState().resetAll()
  })

  it('setPendingRoomData stores message for a room', () => {
    useRoomUiStore.getState().setPendingRoomData('room-empty', {
      initialMessage: 'Hello from redirect',
    })

    const pending = useRoomUiStore.getState().pendingRoomData['room-empty']
    expect(pending).toBeDefined()
    expect(pending.initialMessage).toBe('Hello from redirect')
  })

  it('consumePendingRoomData returns and clears the data', () => {
    useRoomUiStore.getState().setPendingRoomData('room-x', {
      initialMessage: 'Test message',
      targetGroup: 'all_agents',
    })

    const consumed = useRoomUiStore.getState().consumePendingRoomData('room-x')
    expect(consumed).toEqual({
      initialMessage: 'Test message',
      targetGroup: 'all_agents',
    })

    const afterConsume = useRoomUiStore.getState().pendingRoomData['room-x']
    expect(afterConsume).toBeUndefined()
  })

  it('consumePendingRoomData returns null when no data exists', () => {
    const consumed = useRoomUiStore.getState().consumePendingRoomData('room-nonexistent')
    expect(consumed).toBeNull()
  })

  it('pending data without targetGroup stays in store (simulating empty room re-store)', () => {
    const pendingData = { initialMessage: 'My message' }

    useRoomUiStore.getState().setPendingRoomData('room-empty-1', pendingData)

    const stored = useRoomUiStore.getState().pendingRoomData['room-empty-1']
    expect(stored).toBeDefined()
    expect(stored.initialMessage).toBe('My message')
    expect(stored.targetGroup).toBeUndefined()
  })

  it('pending data with explicit targetGroup survives store roundtrip', () => {
    const pendingData = {
      initialMessage: 'Explicit target',
      targetGroup: 'grp-saved-123',
    }

    useRoomUiStore.getState().setPendingRoomData('room-target', pendingData)

    const consumed = useRoomUiStore.getState().consumePendingRoomData('room-target')
    expect(consumed).toBeDefined()
    expect(consumed!.targetGroup).toBe('grp-saved-123')
    expect(consumed!.initialMessage).toBe('Explicit target')
  })

  it('re-storing pending data after failed send preserves content', () => {
    const pendingData = { initialMessage: 'Will fail', targetGroup: undefined }

    useRoomUiStore.getState().setPendingRoomData('room-fail', pendingData)
    const consumed = useRoomUiStore.getState().consumePendingRoomData('room-fail')
    expect(consumed).toBeDefined()

    useRoomUiStore.getState().setPendingRoomData('room-fail', consumed!)

    const reStored = useRoomUiStore.getState().pendingRoomData['room-fail']
    expect(reStored).toEqual(pendingData)
  })

  it('multiple rooms can have independent pending data', () => {
    useRoomUiStore.getState().setPendingRoomData('room-a', {
      initialMessage: 'Message A',
    })
    useRoomUiStore.getState().setPendingRoomData('room-b', {
      initialMessage: 'Message B',
      targetGroup: 'all_agents',
    })

    expect(useRoomUiStore.getState().pendingRoomData['room-a']?.initialMessage).toBe('Message A')
    expect(useRoomUiStore.getState().pendingRoomData['room-b']?.targetGroup).toBe('all_agents')

    useRoomUiStore.getState().consumePendingRoomData('room-a')
    expect(useRoomUiStore.getState().pendingRoomData['room-a']).toBeUndefined()
    expect(useRoomUiStore.getState().pendingRoomData['room-b']).toBeDefined()
  })
})
