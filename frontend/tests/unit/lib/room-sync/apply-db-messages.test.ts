import { describe, expect, it, beforeEach } from 'vitest'
import { applyDbMessages } from '@/lib/room-sync/apply-db-messages'
import { useMessageStore } from '@/stores/message-store'
import { useStreamingStore } from '@/stores/streaming-store'
import { createUserMessage, resetCounters } from '../../../fixtures'

describe('applyDbMessages', () => {
  beforeEach(() => {
    resetCounters()
    useMessageStore.getState().clearRoom()
    useStreamingStore.setState({ buffers: {} })
  })

  it('returns null when store roomId does not match', () => {
    useMessageStore.getState().setRoom('room-a')
    const result = applyDbMessages('room-b', [
      createUserMessage({ id: 'u1', roomId: 'room-b' }),
    ])
    expect(result).toBeNull()
  })

  it('upserts messages and returns applied ids', () => {
    useMessageStore.getState().setRoom('room-1')
    const result = applyDbMessages('room-1', [
      createUserMessage({ id: 'u1', roomId: 'room-1', content: 'hello' }),
    ])
    expect(result?.appliedCount).toBe(1)
    expect(useMessageStore.getState().entities['u1']?.content).toBe('hello')
  })

  it('preserves transient processing logs when DB reconcile includes a terminal user turn', () => {
    const store = useMessageStore.getState()
    store.setRoom('room-1')
    store.upsertMessage({
      id: 'u1',
      roomId: 'room-1',
      messageType: 'user',
      content: 'hello',
      senderName: 'User',
      timestamp: '2026-06-04T01:00:00.000Z',
      processingStatusLogs: [
        {
          id: 'processing-log-1',
          message: 'Thinking...',
          timestamp: '2026-06-04T01:00:01.000Z',
        },
      ],
    }, 'optimistic')

    const result = applyDbMessages('room-1', [
      createUserMessage({ id: 'u1', roomId: 'room-1', content: 'hello' }),
      {
        id: 'a1',
        roomId: 'room-1',
        messageType: 'agent',
        content: 'done',
        senderName: 'Agent',
        timestamp: '2026-06-04T01:00:02.000Z',
        relatedMessageId: 'u1',
        taskStatus: 'completed',
      },
    ])

    expect(result?.appliedCount).toBe(2)
    expect(useMessageStore.getState().entities['u1'].processingStatusLogs?.map((entry) => entry.message)).toEqual([
      'Thinking...',
    ])
  })

  it('keeps transient processing logs when DB reconcile still has non-terminal agents for the turn', () => {
    const store = useMessageStore.getState()
    store.setRoom('room-1')
    store.upsertMessage({
      id: 'u1',
      roomId: 'room-1',
      messageType: 'user',
      content: 'hello',
      senderName: 'User',
      timestamp: '2026-06-04T01:00:00.000Z',
      processingStatusLogs: [
        {
          id: 'processing-log-1',
          message: 'Thinking...',
          timestamp: '2026-06-04T01:00:01.000Z',
        },
      ],
    }, 'optimistic')

    const result = applyDbMessages('room-1', [
      createUserMessage({ id: 'u1', roomId: 'room-1', content: 'hello' }),
      {
        id: 'a1',
        roomId: 'room-1',
        messageType: 'agent',
        content: 'done',
        senderName: 'Agent 1',
        timestamp: '2026-06-04T01:00:02.000Z',
        relatedMessageId: 'u1',
        taskStatus: 'completed',
      },
      {
        id: 'a2',
        roomId: 'room-1',
        messageType: 'agent',
        content: '',
        senderName: 'Agent 2',
        timestamp: '2026-06-04T01:00:03.000Z',
        relatedMessageId: 'u1',
        taskStatus: 'working',
      },
    ])

    expect(result?.appliedCount).toBe(3)
    expect(useMessageStore.getState().entities['u1'].processingStatusLogs?.map((entry) => entry.message)).toEqual([
      'Thinking...',
    ])
  })
})
