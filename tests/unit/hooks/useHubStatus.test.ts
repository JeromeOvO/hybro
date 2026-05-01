import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor, cleanup } from '@testing-library/react'
import React from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { HubStatusResponse } from '@/lib/api/hub'

const mockGetMyHubStatus = vi.fn<() => Promise<HubStatusResponse>>()
vi.mock('@/lib/api/hub', () => ({
  getMyHubStatus: mockGetMyHubStatus,
}))

vi.mock('@clerk/nextjs', () => ({
  useUser: () => ({ isLoaded: true, isSignedIn: true }),
  useAuth: () => ({ getToken: async () => 'test-token' }),
}))

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client: queryClient }, children)
  }
}

let useHubStatus: typeof import('@/hooks/useHubStatus').useHubStatus

beforeEach(async () => {
  vi.clearAllMocks()
  const mod = await import('@/hooks/useHubStatus')
  useHubStatus = mod.useHubStatus
})

afterEach(() => {
  cleanup()
})

describe('useHubStatus', () => {
  it('returns loading state initially', () => {
    mockGetMyHubStatus.mockReturnValue(new Promise(() => {}))

    const { result } = renderHook(() => useHubStatus(), { wrapper: createWrapper() })

    expect(result.current.isLoading).toBe(true)
    expect(result.current.hasHub).toBe(false)
    expect(result.current.isOnline).toBe(false)
    expect(result.current.hub).toBeNull()
  })

  it('returns hasHub=false when no hubs exist', async () => {
    mockGetMyHubStatus.mockResolvedValue({ hubs: [] })

    const { result } = renderHook(() => useHubStatus(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false)
    })
    expect(result.current.hasHub).toBe(false)
    expect(result.current.isOnline).toBe(false)
    expect(result.current.hub).toBeNull()
    expect(result.current.hubs).toEqual([])
  })

  it('returns online hub data when hub is connected', async () => {
    const hub = { hub_id: 'h1', is_online: true, last_connected_at: '2026-01-01T00:00:00Z', agent_count: 2 }
    mockGetMyHubStatus.mockResolvedValue({ hubs: [hub] })

    const { result } = renderHook(() => useHubStatus(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false)
    })
    expect(result.current.hasHub).toBe(true)
    expect(result.current.isOnline).toBe(true)
    expect(result.current.hub).toEqual(hub)
    expect(result.current.hubs).toEqual([hub])
  })

  it('returns offline hub data when hub is not online', async () => {
    const hub = { hub_id: 'h1', is_online: false, last_connected_at: '2026-01-01T00:00:00Z', agent_count: 0 }
    mockGetMyHubStatus.mockResolvedValue({ hubs: [hub] })

    const { result } = renderHook(() => useHubStatus(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false)
    })
    expect(result.current.hasHub).toBe(true)
    expect(result.current.isOnline).toBe(false)
    expect(result.current.hub).toEqual(hub)
  })

  it('uses primary (first) hub when multiple hubs exist', async () => {
    const hub1 = { hub_id: 'h1', is_online: true, last_connected_at: '2026-01-01T00:00:00Z', agent_count: 1 }
    const hub2 = { hub_id: 'h2', is_online: false, last_connected_at: '2026-01-01T00:00:00Z', agent_count: 0 }
    mockGetMyHubStatus.mockResolvedValue({ hubs: [hub1, hub2] })

    const { result } = renderHook(() => useHubStatus(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false)
    })
    expect(result.current.hub).toEqual(hub1)
    expect(result.current.isOnline).toBe(true)
    expect(result.current.hubs).toHaveLength(2)
  })

  it('exposes invalidate function', async () => {
    mockGetMyHubStatus.mockResolvedValue({ hubs: [] })

    const { result } = renderHook(() => useHubStatus(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false)
    })
    expect(typeof result.current.invalidate).toBe('function')
  })

  it('handles API errors gracefully', async () => {
    mockGetMyHubStatus.mockRejectedValue(new Error('Network error'))

    const { result } = renderHook(() => useHubStatus(), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(result.current.isError).toBe(true)
    })
    expect(result.current.hasHub).toBe(false)
    expect(result.current.isOnline).toBe(false)
  })
})
