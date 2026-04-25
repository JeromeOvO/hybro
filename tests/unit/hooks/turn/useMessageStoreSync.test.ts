import { beforeEach, describe, expect, it } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'
import { useMessageStoreSync } from '@/hooks/turn/useMessageStoreSync'
import { useMessageStore } from '@/stores/message-store'
import { useTurnEventStore, TurnEventLog } from '@/stores/turn-event-store'
import type { TurnEvent } from '@/stores/turn-event-store/types'

function makeTurnStarted(turnId: string, clientRequestId: string): TurnEvent {
  return {
    eventId: `${turnId}-start`,
    turnId,
    seq: 1,
    ts: Date.now(),
    type: 'turn_started',
    userInput: { text: 'hello', attachments: [] },
    clientRequestId,
  }
}

describe('useMessageStoreSync optimistic cleanup', () => {
  beforeEach(() => {
    cleanup()
    useMessageStore.getState().clearRoom()
    useMessageStore.getState().setRoom('room-1')
    useTurnEventStore.getState().reset()
  })

  it('keeps a fresh optimistic turn when no correlated real user message exists', () => {
    renderHook(() => useMessageStoreSync())

    act(() => {
      useMessageStore.getState().upsertMessage({
        id: 'u-existing',
        roomId: 'room-1',
        messageType: 'user',
        content: 'older message',
        senderName: 'User',
        timestamp: new Date().toISOString(),
      }, 'db')
      useTurnEventStore.getState().createOptimisticTurn('cr-new')
      useMessageStore.getState().nudgeSyncBridge()
    })

    const store = useTurnEventStore.getState()
    expect(store.turnLogs.has('cr-new')).toBe(true)
    expect(store.orderedTurnIds).toContain('cr-new')
  })

  it('removes an optimistic turn when correlated real turn is present', () => {
    renderHook(() => useMessageStoreSync())

    const optimisticId = 'cr-123'
    const realTurnId = 'u-real'
    const started = makeTurnStarted(optimisticId, optimisticId)

    act(() => {
      useMessageStore.getState().upsertMessage({
        id: realTurnId,
        roomId: 'room-1',
        messageType: 'user',
        content: 'real message',
        senderName: 'User',
        timestamp: new Date().toISOString(),
        clientRequestId: optimisticId,
      }, 'db')

      const optimisticLog = new TurnEventLog(optimisticId)
      optimisticLog.append(started)
      const realLog = new TurnEventLog(realTurnId)
      realLog.append({ ...started, eventId: `${realTurnId}-start`, turnId: realTurnId })

      useTurnEventStore.setState({
        turnLogs: new Map([
          [optimisticId, optimisticLog],
          [realTurnId, realLog],
        ]),
        orderedTurnIds: [optimisticId, realTurnId],
        turnIdByClientRequestId: new Map([[optimisticId, realTurnId]]),
      })

      useMessageStore.getState().nudgeSyncBridge()
    })

    const store = useTurnEventStore.getState()
    expect(store.turnLogs.has(optimisticId)).toBe(false)
    expect(store.turnLogs.has(realTurnId)).toBe(true)
    expect(store.orderedTurnIds).not.toContain(optimisticId)
  })
})
