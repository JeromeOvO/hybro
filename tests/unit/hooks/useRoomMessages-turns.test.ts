import { describe, it, expect, beforeEach, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useMessageStore } from '@/stores/message-store'
import {
  useConversationTurns,
  useActiveTurn,
  useHitlTurnContext,
} from '@/hooks/useRoomMessages'

vi.mock('@/lib/room-timeline/event-log', () => ({
  getEvents: () => [],
  appendEvent: vi.fn(),
  clearRoom: vi.fn(),
}))

describe('Timeline hooks', () => {
  beforeEach(() => {
    useMessageStore.setState({
      entities: {},
      orderedIds: [],
      roomId: 'room-1',
      hydratedFromDb: true,
      version: 0,
      lastDbSyncAt: null,
    })
  })

  it('empty store returns empty turns', () => {
    const { result } = renderHook(() => useConversationTurns())
    expect(result.current).toEqual([])
  })

  it('seeded store produces correct turns', () => {
    const store = useMessageStore.getState()
    store.upsertMessage({
      id: 'u1', roomId: 'room-1', messageType: 'user', content: 'Hello',
      senderName: 'User', timestamp: '2026-01-01T00:00:00Z',
    }, 'db')
    store.upsertMessage({
      id: 'a1', roomId: 'room-1', messageType: 'agent', content: 'Hi there',
      senderName: 'Agent', agentId: 'agent-1', timestamp: '2026-01-01T00:00:01Z',
      taskStatus: 'completed',
    }, 'db')

    const { result } = renderHook(() => useConversationTurns())
    expect(result.current).toHaveLength(1)
    expect(result.current[0].userMessageId).toBe('u1')
    expect(result.current[0].agentResults).toHaveLength(1)
    expect(result.current[0].status).toBe('completed')
  })

  it('active turn is the last turn', () => {
    const store = useMessageStore.getState()
    store.upsertMessage({
      id: 'u1', roomId: 'room-1', messageType: 'user', content: 'First',
      senderName: 'User', timestamp: '2026-01-01T00:00:00Z',
    }, 'db')
    store.upsertMessage({
      id: 'a1', roomId: 'room-1', messageType: 'agent', content: 'Reply',
      senderName: 'Agent', agentId: 'agent-1', timestamp: '2026-01-01T00:00:01Z',
      taskStatus: 'completed',
    }, 'db')
    store.upsertMessage({
      id: 'u2', roomId: 'room-1', messageType: 'user', content: 'Second',
      senderName: 'User', timestamp: '2026-01-01T00:00:02Z',
    }, 'db')

    const { result } = renderHook(() => useActiveTurn())
    expect(result.current).toBeDefined()
    expect(result.current!.userMessageId).toBe('u2')
  })

  it('HITL context finds correct turn', () => {
    const store = useMessageStore.getState()
    store.upsertMessage({
      id: 'u1', roomId: 'room-1', messageType: 'user', content: 'Question',
      senderName: 'User', timestamp: '2026-01-01T00:00:00Z',
    }, 'db')
    store.upsertMessage({
      id: 'a1', roomId: 'room-1', messageType: 'agent', content: '',
      senderName: 'Agent', agentId: 'agent-1', timestamp: '2026-01-01T00:00:01Z',
      taskStatus: 'input-required', hitlRequestId: 'hitl-1', hitlPrompt: 'Confirm?',
    }, 'db')

    const { result } = renderHook(() => useHitlTurnContext('a1'))
    expect(result.current).not.toBeNull()
    expect(result.current!.turnIndex).toBe(0)
    expect(result.current!.turnLabel).toContain('Question')
  })
})
