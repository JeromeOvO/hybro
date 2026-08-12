'use client'

import { useEffect } from 'react'
import { useUser } from '@/lib/auth'
import { usePathname } from 'next/navigation'
import { Loader2 } from 'lucide-react'

/**
 * Redirects unauthenticated users to /sign-in, preserving the current path
 * as redirect_url so they land back here after signing in.
 *
 * Renders a loading spinner while the auth adapter initializes, then either
 * renders children (authenticated) or redirects (unauthenticated).
 */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { isLoaded, isSignedIn } = useUser()
  const pathname = usePathname()

  useEffect(() => {
    if (isLoaded && !isSignedIn) {
      window.location.href = `/sign-in?redirect_url=${encodeURIComponent(pathname)}`
    }
  }, [isLoaded, isSignedIn, pathname])

  if (!isLoaded) {
    return (
      <div className="flex items-center justify-center h-full min-h-64">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (!isSignedIn) {
    return null
  }

  return <>{children}</>
}
