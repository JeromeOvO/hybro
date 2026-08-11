import { act, cleanup, renderHook } from '@testing-library/react'
import { QueryClient } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RoomHistoryResponse } from '@/lib/api/room'
import { roomHistoryQueryKey } from '@/lib/room-history-query'
import { MAX_QUOTE_TEXT_LENGTH } from '@/lib/types/quote'
import { useMessageStore } from '@/stores/message-store'
import { useRoomUiStore } from '@/stores/room-ui-store'
import type { ProcessingLifecycle } from '@/hooks/room/processing-lifecycle'
import { useSendMessage } from '@/hooks/room/useSendMessage'

const { sendMessageMock } = vi.hoisted(() => ({
  sendMessageMock: vi.fn(),
}))

let activeQueryClient: QueryClient

vi.mock('@/components/providers/query-provider', () => ({
  getActiveQueryClient: () => activeQueryClient,
}))

vi.mock('@/lib/api/room', async importOriginal => {
  const original = await importOriginal<typeof import('@/lib/api/room')>()
  return {
    ...original,
    SendMessage: (...args: unknown[]) => sendMessageMock(...args),
  }
})

vi.mock('@/components/ui/banner', () => ({
  banner: { error: vi.fn(), info: vi.fn(), success: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/hooks/room/sse-handlers/pending-turn-buffer', () => ({
  clearPendingSseForClientRequest: vi.fn(),
  resolveClientRequestMessageId: vi.fn(),
}))

function createLifecycle(): ProcessingLifecycle {
  return {
    isSendGuardActive: vi.fn(() => false),
    getMessageId: vi.fn(),
    resetPlaceholder: vi.fn(),
    resetProcessingResolved: vi.fn(),
    setPendingRunEventAck: vi.fn(),
    placeholderId: vi.fn((roomId: string) => `placeholder:${roomId}`),
    startProcessing: vi.fn(),
    setSendGuard: vi.fn(),
    stopProcessing: vi.fn(),
    setMessageId: vi.fn(),
    isProcessingResolved: vi.fn(() => false),
    setCancelTimedOut: vi.fn(),
    clearSseDisconnection: vi.fn(),
    disarmCancelTimeout: vi.fn(),
  } as unknown as ProcessingLifecycle
}

const initialHistory: RoomHistoryResponse = {
  items: [
    {
      room_id: 'room-1',
      title: 'Room 1',
      last_activity_at: '2026-08-01T00:00:00.000Z',
      is_pinned: false,
      pin_order: null,
      status: 'idle',
    },
    {
      room_id: 'room-2',
      title: 'Room 2',
      last_activity_at: '2026-08-02T00:00:00.000Z',
      is_pinned: false,
      pin_order: null,
      status: 'awaiting_input',
    },
  ],
}

describe('useSendMessage room-history rollback', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    activeQueryClient = new QueryClient()
    activeQueryClient.setQueryData(roomHistoryQueryKey('user-1'), initialHistory)
    useMessageStore.getState().clearRoom()
    useMessageStore.getState().setRoom('room-1')
    useRoomUiStore.getState().resetAll()
  })

  afterEach(() => {
    cleanup()
    activeQueryClient.clear()
  })

  async function send(overrides: { quoteData?: { content: string; messageId: string } } = {}) {
    const lifecycle = createLifecycle()
    const { result } = renderHook(() => useSendMessage(
      'room-1',
      'user-1',
      'User',
      { room_id: 'room-1' },
      async () => 'token',
      false,
      true,
      lifecycle,
      vi.fn(),
      vi.fn(),
      vi.fn().mockResolvedValue(undefined),
    ))

    let sent: boolean | undefined
    await act(async () => {
      sent = await result.current.sendUserMessage({
        userInput: 'Hello',
        quoteData: overrides.quoteData,
        mode: 'direct',
        agentScope: { source: 'room_default' },
      })
    })
    return sent
  }

  it('restores history when sending throws', async () => {
    sendMessageMock.mockRejectedValueOnce(new Error('network failure'))

    expect(await send()).toBe(false)
    expect(activeQueryClient.getQueryData(roomHistoryQueryKey('user-1'))).toEqual(initialHistory)
  })

  it('restores history when the response has no message id', async () => {
    sendMessageMock.mockResolvedValueOnce({ success: true })

    expect(await send()).toBe(false)
    expect(activeQueryClient.getQueryData(roomHistoryQueryKey('user-1'))).toEqual(initialHistory)
  })

  it('restores history when the quote is too long', async () => {
    expect(await send({
      quoteData: {
        content: 'x'.repeat(MAX_QUOTE_TEXT_LENGTH + 1),
        messageId: 'quoted-message',
      },
    })).toBe(false)
    expect(sendMessageMock).not.toHaveBeenCalled()
    expect(activeQueryClient.getQueryData(roomHistoryQueryKey('user-1'))).toEqual(initialHistory)
  })
})
