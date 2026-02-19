import { clerkMiddleware } from '@clerk/nextjs/server'
import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

/**
 * Subdomain-routing middleware.
 *
 * Routes requests based on hostname:
 *   developer.hybro.ai  /  dev.localhost  → /d/* (developer portal)
 *   hybro.ai            /  localhost      → /c/* (consumer app)
 *
 * Shared paths that are never rewritten:
 *   /api/*, /sign-in, /sign-up, /_next/*, static files
 *
 * Local dev fallback:
 *   ?_subdomain=developer query param → developer routes
 */

/** Hostnames (or prefixes) that map to the developer portal. */
const DEV_HOSTS = ['developer.', 'dev.']

/** Paths that should never be rewritten. */
const SHARED_PATH_PREFIXES = ['/api/', '/_next/', '/sign-in', '/sign-up', '/privacy']

/** Determine if running locally based on NEXT_PUBLIC_CONSUMER_URL */
const IS_LOCAL = process.env.NEXT_PUBLIC_CONSUMER_URL?.includes('localhost')

/** Authorized parties for Clerk - includes localhost in local dev */
const AUTHORIZED_PARTIES = [
  'https://hybro.ai',
  'https://developer.hybro.ai',
  ...(IS_LOCAL ? ['http://localhost:3000', 'http://dev.localhost:3000'] : []),
]

/** Static file extensions to skip. */
const STATIC_EXT = /\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)$/

function isDeveloperHost(host: string): boolean {
  return DEV_HOSTS.some((prefix) => host.startsWith(prefix))
}

function isSharedPath(pathname: string): boolean {
  return SHARED_PATH_PREFIXES.some((p) => pathname.startsWith(p))
}

function isStaticFile(pathname: string): boolean {
  return STATIC_EXT.test(pathname)
}

function handleSubdomainRewrite(request: NextRequest): NextResponse | null {
  const { pathname, searchParams } = request.nextUrl

  // Never rewrite shared paths or static assets
  if (isSharedPath(pathname) || isStaticFile(pathname)) {
    return null
  }

  // Don't double-rewrite paths already prefixed
  if (pathname.startsWith('/c/') || pathname.startsWith('/d/') || pathname === '/c' || pathname === '/d') {
    return null
  }

  // Determine subdomain
  const host = request.headers.get('host') || ''
  const isDevPortal =
    isDeveloperHost(host) || searchParams.get('_subdomain') === 'developer'

  // Rewrite
  const prefix = isDevPortal ? '/d' : '/c'
  const rewritten = request.nextUrl.clone()
  rewritten.pathname = `${prefix}${pathname}`

  return NextResponse.rewrite(rewritten)
}

export default clerkMiddleware(
  async (_auth, request) => {
    const rewrite = handleSubdomainRewrite(request)
    if (rewrite) return rewrite
    return NextResponse.next()
  },
  {
    authorizedParties: AUTHORIZED_PARTIES,
  }
)

export const config = {
  matcher: [
    // Skip Next.js internals and all static files, unless found in search params
    '/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)',
    // Always run for API routes
    '/(api|trpc)(.*)',
  ],
}
