import { vi, describe, it, expect, beforeEach } from 'vitest'

vi.mock('@clerk/nextjs/server', () => ({
  clerkMiddleware: vi.fn((handler) => handler),
}))

vi.mock('next/server', () => {
  const NextResponse = {
    next: vi.fn(() => ({ type: 'next' })),
    rewrite: vi.fn((url: unknown) => ({ type: 'rewrite', url })),
  }
  return { NextResponse, NextRequest: vi.fn() }
})

import {
  isDeveloperHost,
  isSharedPath,
  isStaticFile,
  handleSubdomainRewrite,
} from '@/proxy'
import { NextResponse } from 'next/server'

function makeRequest(
  pathname: string,
  host: string = 'hybro.ai',
  searchParams: Record<string, string> = {},
) {
  const params = new URLSearchParams(searchParams)
  const url = {
    pathname,
    searchParams: params,
    clone: () => ({ ...url, pathname: url.pathname }),
  }
  return {
    nextUrl: url,
    headers: new Map([['host', host]]),
  } as any
}

describe('isDeveloperHost', () => {
  it('matches developer. prefix', () => {
    expect(isDeveloperHost('developer.hybro.ai')).toBe(true)
  })

  it('matches dev. prefix', () => {
    expect(isDeveloperHost('dev.localhost:3000')).toBe(true)
  })

  it('rejects consumer hosts', () => {
    expect(isDeveloperHost('hybro.ai')).toBe(false)
    expect(isDeveloperHost('app.hybro.ai')).toBe(false)
  })
})

describe('isSharedPath', () => {
  it('matches /api/ prefix', () => {
    expect(isSharedPath('/api/health')).toBe(true)
  })

  it('matches /_next/ prefix', () => {
    expect(isSharedPath('/_next/static/chunk.js')).toBe(true)
  })

  it('matches auth pages', () => {
    expect(isSharedPath('/sign-in')).toBe(true)
    expect(isSharedPath('/sign-up')).toBe(true)
  })

  it('rejects regular paths', () => {
    expect(isSharedPath('/dashboard')).toBe(false)
    expect(isSharedPath('/chat')).toBe(false)
  })
})

describe('isStaticFile', () => {
  it('matches static extensions', () => {
    expect(isStaticFile('/logo.png')).toBe(true)
    expect(isStaticFile('/style.css')).toBe(true)
    expect(isStaticFile('/app.js')).toBe(true)
    expect(isStaticFile('/favicon.ico')).toBe(true)
  })

  it('rejects dynamic paths', () => {
    expect(isStaticFile('/about')).toBe(false)
    expect(isStaticFile('/api/data.json')).toBe(false)
  })
})

describe('handleSubdomainRewrite', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('developer host rewrites to /d prefix', () => {
    const req = makeRequest('/dashboard', 'developer.hybro.ai')
    handleSubdomainRewrite(req)

    expect(NextResponse.rewrite).toHaveBeenCalledOnce()
    const rewrittenUrl = vi.mocked(NextResponse.rewrite).mock.calls[0][0] as any
    expect(rewrittenUrl.pathname).toBe('/d/dashboard')
  })

  it('consumer host rewrites to /c prefix', () => {
    const req = makeRequest('/chat', 'hybro.ai')
    handleSubdomainRewrite(req)

    expect(NextResponse.rewrite).toHaveBeenCalledOnce()
    const rewrittenUrl = vi.mocked(NextResponse.rewrite).mock.calls[0][0] as any
    expect(rewrittenUrl.pathname).toBe('/c/chat')
  })

  it('shared path not rewritten', () => {
    const req = makeRequest('/api/health', 'developer.hybro.ai')
    const result = handleSubdomainRewrite(req)

    expect(result).toBeNull()
    expect(NextResponse.rewrite).not.toHaveBeenCalled()
  })

  it('static file not rewritten', () => {
    const req = makeRequest('/favicon.ico', 'hybro.ai')
    const result = handleSubdomainRewrite(req)

    expect(result).toBeNull()
    expect(NextResponse.rewrite).not.toHaveBeenCalled()
  })

  it('already prefixed not double rewritten', () => {
    const resultC = handleSubdomainRewrite(makeRequest('/c/chat', 'hybro.ai'))
    const resultD = handleSubdomainRewrite(makeRequest('/d/settings', 'developer.hybro.ai'))

    expect(resultC).toBeNull()
    expect(resultD).toBeNull()
    expect(NextResponse.rewrite).not.toHaveBeenCalled()
  })
})
