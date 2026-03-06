import { vi, describe, it, expect, beforeEach } from 'vitest'

vi.mock('next/server', () => ({
  NextRequest: vi.fn(),
  NextResponse: {
    json: vi.fn((data: unknown, init?: { status?: number }) => ({
      json: async () => data,
      status: init?.status ?? 200,
    })),
  },
}))

const mockFetch = vi.fn()

import { POST, GET } from '@/app/api/agent/[...endpoint]/route'

function makePostRequest(body: object) {
  return { json: async () => body } as any
}

function makeGetRequest() {
  return {} as any
}

const makeParams = (endpoint: string[]) => ({
  params: Promise.resolve({ endpoint }),
})

describe('Simple proxy routes (agent representative)', () => {
  beforeEach(() => {
    mockFetch.mockReset()
    vi.stubGlobal('fetch', mockFetch)
  })

  it('POST proxies body and returns JSON', async () => {
    const payload = { task: 'summarize', content: 'hello' }
    const backendData = { result: 'done' }

    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => backendData,
    })

    const res = await POST(makePostRequest(payload), makeParams(['run']))

    expect(mockFetch).toHaveBeenCalledOnce()
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toContain('/agent/run')
    expect(opts.method).toBe('POST')
    expect(JSON.parse(opts.body)).toEqual(payload)

    expect(res.status).toBe(200)
    expect(await res.json()).toEqual(backendData)
  })

  it('GET joins endpoint segments correctly', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: '123' }),
    })

    const res = await GET(makeGetRequest(), makeParams(['room', '123']))

    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toContain('/agent/room/123')
    expect(opts.method).toBe('GET')
    expect(res.status).toBe(200)
    expect(await res.json()).toEqual({ id: '123' })
  })

  it('backend non-2xx returns error with forwarded status', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      text: async () => 'Internal failure',
    })

    const res = await POST(
      makePostRequest({ x: 1 }),
      makeParams(['process']),
    )

    expect(res.status).toBe(500)
    const body = await res.json()
    expect(body.error).toContain('Internal failure')
  })

  it('network error returns 500', async () => {
    mockFetch.mockRejectedValueOnce(new Error('ECONNREFUSED'))

    const res = await POST(
      makePostRequest({ x: 1 }),
      makeParams(['process']),
    )

    expect(res.status).toBe(500)
    const body = await res.json()
    expect(body.error).toBe('Internal server error')
  })
})
