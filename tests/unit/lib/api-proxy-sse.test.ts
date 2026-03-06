import { vi, describe, it, expect, beforeEach } from 'vitest'

vi.mock('next/server', () => ({
  NextRequest: vi.fn(),
}))

const mockFetch = vi.fn()

import { POST, GET, OPTIONS } from '@/app/api/sse/[...endpoint]/route'

function makePostRequest(body: object) {
  return { json: async () => body } as any
}

function makeGetRequest(signal?: { addEventListener: (...args: any[]) => void }) {
  return { signal: signal ?? { addEventListener: vi.fn() } } as any
}

const makeParams = (endpoint: string[]) => ({
  params: Promise.resolve({ endpoint }),
})

function makeMockReadableBody(chunks: string[]) {
  let index = 0
  return {
    getReader: () => ({
      read: async () => {
        if (index < chunks.length) {
          return { done: false, value: new TextEncoder().encode(chunks[index++]) }
        }
        return { done: true, value: undefined }
      },
    }),
  }
}

describe('SSE proxy route', () => {
  beforeEach(() => {
    mockFetch.mockReset()
    vi.stubGlobal('fetch', mockFetch)
  })

  // --- POST ---

  it('POST proxies body and returns JSON', async () => {
    const payload = { room_id: 'r1', message: 'hi' }
    const backendData = { session_id: 's1' }

    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => backendData,
    })

    const res = await POST(makePostRequest(payload), makeParams(['connect']))

    expect(mockFetch).toHaveBeenCalledOnce()
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toContain('/sse/connect')
    expect(opts.method).toBe('POST')
    expect(JSON.parse(opts.body)).toEqual(payload)

    expect(res.status).toBe(200)
    const body = JSON.parse(await res.text())
    expect(body).toEqual(backendData)
  })

  it('POST AbortError returns 504', async () => {
    const abortError = new Error('The operation was aborted')
    abortError.name = 'AbortError'
    mockFetch.mockRejectedValueOnce(abortError)

    const res = await POST(makePostRequest({ x: 1 }), makeParams(['connect']))

    expect(res.status).toBe(504)
    const body = JSON.parse(await res.text())
    expect(body.error).toBe('Request timeout')
  })

  // --- GET (non-stream) ---

  it('GET non-stream endpoint returns JSON', async () => {
    const backendData = { status: 'active' }

    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => backendData,
    })

    const res = await GET(makeGetRequest(), makeParams(['room', 'r1', 'status']))

    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toContain('/sse/room/r1/status')
    expect(opts.method).toBe('GET')
    expect(res.status).toBe(200)
    const body = JSON.parse(await res.text())
    expect(body).toEqual(backendData)
  })

  // --- GET (stream) ---

  it('stream endpoint returns ReadableStream with SSE headers', async () => {
    const chunks = ['data: {"type":"message","content":"hi"}\n\n']

    mockFetch.mockResolvedValueOnce({
      ok: true,
      body: makeMockReadableBody(chunks),
    })

    const res = await GET(makeGetRequest(), makeParams(['room', 'r1', 'stream']))

    expect(res.status).toBe(200)
    expect(res.headers.get('Content-Type')).toBe('text/event-stream')
    expect(res.headers.get('Cache-Control')).toBe('no-cache')
    expect(res.headers.get('Connection')).toBe('keep-alive')
    expect(res.headers.get('Access-Control-Allow-Origin')).toBe('*')

    const reader = res.body!.getReader()
    const { value } = await reader.read()
    const text = new TextDecoder().decode(value)
    expect(text).toContain('message')
  })

  it('stream backend error returns SSE-formatted error with status 200', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 502,
      text: async () => 'Bad Gateway',
    })

    const res = await GET(makeGetRequest(), makeParams(['room', 'r1', 'stream']))

    expect(res.status).toBe(200)
    expect(res.headers.get('Content-Type')).toBe('text/event-stream')

    const body = await res.text()
    expect(body).toContain('data: ')
    const parsed = JSON.parse(body.replace('data: ', '').trim())
    expect(parsed.type).toBe('error')
    expect(parsed.error).toContain('Bad Gateway')
  })

  it('stream client abort propagates to upstream controller', async () => {
    const addEventListenerSpy = vi.fn()

    const req = {
      signal: {
        addEventListener: addEventListenerSpy,
        aborted: false,
      },
    } as any

    mockFetch.mockResolvedValueOnce({
      ok: true,
      body: makeMockReadableBody(['data: chunk\n\n']),
    })

    await GET(req, makeParams(['room', 'r1', 'stream']))

    expect(addEventListenerSpy).toHaveBeenCalledWith('abort', expect.any(Function))

    const abortHandler = addEventListenerSpy.mock.calls[0][1]
    const upstreamSignal = mockFetch.mock.calls[0][1].signal as AbortSignal
    expect(upstreamSignal.aborted).toBe(false)
    abortHandler()
    expect(upstreamSignal.aborted).toBe(true)
  })

  it('stream with null body errors gracefully', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      body: null,
    })

    const res = await GET(makeGetRequest(), makeParams(['room', 'r1', 'stream']))

    expect(res.status).toBe(200)
    expect(res.headers.get('Content-Type')).toBe('text/event-stream')

    const reader = res.body!.getReader()
    await expect(reader.read()).rejects.toThrow('No response body')
  })

  // --- OPTIONS ---

  it('OPTIONS returns CORS headers with null body', async () => {
    const res = await OPTIONS()

    expect(res.status).toBe(200)
    expect(res.headers.get('Access-Control-Allow-Origin')).toBe('*')
    expect(res.headers.get('Access-Control-Allow-Methods')).toBe('GET, POST, OPTIONS')
    expect(res.headers.get('Access-Control-Allow-Headers')).toBe('Content-Type, Cache-Control')
    expect(res.body).toBeNull()
  })
})
