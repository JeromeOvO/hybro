import { afterEach, describe, expect, it, vi } from 'vitest'

import { fetchRoomFileBlob } from '@/lib/api/files'
import publicConfig from '../../fixtures/public-config.json'

describe('fetchRoomFileBlob', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.unstubAllEnvs()
    vi.resetModules()
  })

  it('fetches only the constructed room-file endpoint with bearer auth', async () => {
    const response = new Response(new Blob(['hello'], { type: 'text/plain' }), {
      status: 200,
    })
    const fetchMock = vi.fn().mockResolvedValue(response)
    vi.stubGlobal('fetch', fetchMock)
    const signal = new AbortController().signal

    const blob = await fetchRoomFileBlob(
      'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
      async () => 'token-1',
      signal,
    )

    expect(await blob.text()).toBe('hello')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/files/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/content',
      {
        headers: { Authorization: 'Bearer token-1' },
        signal,
      },
    )
  })

  it('rejects unvalidated ids before issuing a request', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      fetchRoomFileBlob('../external', async () => 'token', undefined),
    ).rejects.toThrow('Invalid file id')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('uses the configured JSON API prefix', async () => {
    vi.stubEnv('HYBRO_FRONTEND_CONFIG', JSON.stringify({ ...publicConfig, api_prefix: '/v1' }))
    vi.resetModules()
    const { fetchRoomFileBlob } = await import('@/lib/api/files')
    const response = new Response(new Blob(['hello']), { status: 200 })
    const fetchMock = vi.fn().mockResolvedValue(response)
    vi.stubGlobal('fetch', fetchMock)

    await fetchRoomFileBlob(
      'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
      async () => 'token-1',
    )

    expect(fetchMock).toHaveBeenCalledWith(
      '/v1/files/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/content',
      {
        headers: { Authorization: 'Bearer token-1' },
        signal: undefined,
      },
    )
  })
})
