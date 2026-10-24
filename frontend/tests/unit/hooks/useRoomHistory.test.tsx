import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

const listRoomHistory = vi.fn()
vi.mock('@/lib/api/room', () => ({ listRoomHistory: (...args: unknown[]) => listRoomHistory(...args) }))

import { useRoomHistory } from '@/hooks/useRoomHistory'
import { roomHistoryQueryKey } from '@/lib/room-history-query'

afterEach(cleanup)

describe('useRoomHistory', () => {
  it('shares the chat-history cache key', async () => {
    listRoomHistory.mockResolvedValue({ items: [{ conversation_id: 'r', title: 'T', last_activity_at: '2026-10-10T00:00:00Z', is_pinned: false, pin_order: null, status: 'awaiting_input' }] })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
    const { result } = renderHook(() => useRoomHistory({ userId: 'u', enabled: true }), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(client.getQueryData(roomHistoryQueryKey('u'))).toEqual(result.current.data)
  })
})
