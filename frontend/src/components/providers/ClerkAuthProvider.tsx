'use client'

import { useRef, useEffect } from 'react'
import { useAuth } from '@/lib/auth'
import { setDefaultGetToken } from '@/lib/auth'

/**
 * Provider that sets up the default Clerk token getter for all API calls.
 * This should wrap your app to enable automatic authentication.
 */
export function ClerkAuthProvider({ children }: { children: React.ReactNode }) {
  const { getToken } = useAuth()
  const initialized = useRef(false)

  // Set token getter synchronously on first render so it's available
  // before any child component mounts and fires an API call.
  if (!initialized.current) {
    setDefaultGetToken(getToken)
    initialized.current = true
  }

  // Keep the token getter in sync when Clerk refreshes it.
  useEffect(() => {
    setDefaultGetToken(getToken)
  }, [getToken])

  return <>{children}</>
}
