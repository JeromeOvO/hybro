import { afterEach, describe, expect, it } from 'vitest'

import nextConfig from '../../../next.config'

const originalApiPrefix = process.env.NEXT_PUBLIC_API_PREFIX

describe('Next API proxy', () => {
  afterEach(() => {
    if (originalApiPrefix === undefined) {
      delete process.env.NEXT_PUBLIC_API_PREFIX
    } else {
      process.env.NEXT_PUBLIC_API_PREFIX = originalApiPrefix
    }
  })

  it('proxies the configured API prefix used by authenticated file downloads', async () => {
    process.env.NEXT_PUBLIC_API_PREFIX = '/v1/'
    const rewrites = await nextConfig.rewrites?.()

    expect(rewrites).toEqual([{
      source: '/v1/:path*',
      destination: 'http://127.0.0.1:8000/v1/:path*',
    }])
  })
})
