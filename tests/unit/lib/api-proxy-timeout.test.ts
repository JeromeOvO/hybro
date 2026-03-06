import { vi, describe, it, expect, beforeEach } from 'vitest'

vi.mock('next/server', () => ({
  NextRequest: vi.fn(),
  NextResponse: {
    json: vi.fn((data: unknown, init?: { status?: number }) => ({
      json: async () => data,
      status: init?.status || 200,
    })),
  },
}))

const mockFetch = vi.fn()

import {
  POST as roomCenterPost,
  GET as roomCenterGet,
} from '@/app/api/roomCenter/[...endpoint]/route'
import { GET as orchestrationGet } from '@/app/api/orchestrationCenter/[...endpoint]/route'
import { GET as healthGet } from '@/app/api/health/route'

function makeRequest(body?: unknown) {
  return { json: async () => body } as never
}

function makeParams(segments: string[]) {
  return { params: Promise.resolve({ endpoint: segments }) }
}

function makeFetchResponse(ok: boolean, data: unknown, status = 200) {
  return {
    ok,
    status,
    json: async () => data,
    text: async () => (typeof data === 'string' ? data : JSON.stringify(data)),
  }
}

describe('API proxy timeout routes', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('fetch', mockFetch)
  })

  it('test_post_proxies_body_and_returns_json', async () => {
    const requestBody = { room_id: 'abc', action: 'start' }
    const responseData = { success: true, id: '123' }

    mockFetch.mockResolvedValueOnce(makeFetchResponse(true, responseData))

    const result = await roomCenterPost(
      makeRequest(requestBody),
      makeParams(['rooms', 'create']),
    )

    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/roomCenter/rooms/create'),
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify(requestBody),
      }),
    )

    expect(result.status).toBe(200)
    expect(await result.json()).toEqual(responseData)
  })

  it('test_post_timeout_aborts_returns_504', async () => {
    const abortError = new Error('The operation was aborted')
    abortError.name = 'AbortError'
    mockFetch.mockRejectedValueOnce(abortError)

    const result = await roomCenterPost(
      makeRequest({ data: 'test' }),
      makeParams(['long', 'running']),
    )

    expect(result.status).toBe(504)
    expect(await result.json()).toEqual({ error: 'Request timeout' })
  })

  it('test_get_timeout_aborts_returns_504', async () => {
    const abortError = new Error('The operation was aborted')
    abortError.name = 'AbortError'
    mockFetch.mockRejectedValueOnce(abortError)

    const result = await roomCenterGet(
      makeRequest(),
      makeParams(['status', 'check']),
    )

    expect(result.status).toBe(504)
    expect(await result.json()).toEqual({ error: 'Request timeout' })

    const callArgs = mockFetch.mock.calls[0][1]
    expect(callArgs.signal).toBeDefined()
  })

  it('test_backend_non_2xx_returns_error_with_status', async () => {
    mockFetch.mockResolvedValueOnce(
      makeFetchResponse(false, 'Upstream failure', 502),
    )

    const result = await roomCenterPost(
      makeRequest({ query: 'test' }),
      makeParams(['rooms', 'info']),
    )

    expect(result.status).toBe(502)
    const json = await result.json()
    expect(json.error).toContain('Backend error')
  })

  it('test_orchestration_get_has_no_timeout', async () => {
    const responseData = { tasks: [] }
    mockFetch.mockResolvedValueOnce(makeFetchResponse(true, responseData))

    const result = await orchestrationGet(
      makeRequest(),
      makeParams(['tasks', 'list']),
    )

    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/orchestrationCenter/tasks/list'),
      expect.objectContaining({ method: 'GET' }),
    )

    const callArgs = mockFetch.mock.calls[0][1]
    expect(callArgs.signal).toBeUndefined()

    expect(result.status).toBe(200)
    expect(await result.json()).toEqual(responseData)
  })

  it('test_health_get_proxies_without_params', async () => {
    const responseData = { status: 'healthy', uptime: 12345 }
    mockFetch.mockResolvedValueOnce(makeFetchResponse(true, responseData))

    const result = await healthGet()

    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/health'),
      expect.objectContaining({ method: 'GET' }),
    )

    expect(result.status).toBe(200)
    expect(await result.json()).toEqual(responseData)
  })
})
