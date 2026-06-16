import { renderHook, waitFor, cleanup } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { inquiryRoomMessagesByRoomId } from '@/lib/api/room'
import { fetchPendingHitlRequests } from '@/lib/api/hitl'
import { useRoomHydration } from '@/hooks/room/useRoomHydration'
import { useMessageStore } from '@/stores/message-store'
import { useRoomUiStore } from '@/stores/room-ui-store'

vi.mock('@/lib/api/room', () => ({
  inquiryRoomMessagesByRoomId: vi.fn(),
}))

vi.mock('@/lib/api/hitl', () => ({
  fetchPendingHitlRequests: vi.fn(),
}))

describe('useRoomHydration initial scroll signal', () => {
  beforeEach(() => {
    useMessageStore.getState().setRoom('room-1')
    useRoomUiStore.getState().resetAll()
    vi.mocked(inquiryRoomMessagesByRoomId).mockReset()
    vi.mocked(fetchPendingHitlRequests).mockReset()
  })

  afterEach(() => {
    cleanup()
  })

  it('marks initial hydration after DB sync without waiting for pending HITL fetch', async () => {
    vi.mocked(inquiryRoomMessagesByRoomId).mockResolvedValue({
      success: true,
      message_list: [],
    })
    vi.mocked(fetchPendingHitlRequests).mockReturnValue(new Promise(() => {}))

    renderHook(() => useRoomHydration(
      'room-1',
      'user-1',
      'Test User',
      undefined,
      { room_id: 'room-1' },
      { current: new Map() },
      async () => 'Agent',
      () => undefined,
    ))

    await waitFor(() => {
      expect(useRoomUiStore.getState().initialHydrationSeqByRoom['room-1']).toBe(1)
    }, { timeout: 250 })
  })
})
