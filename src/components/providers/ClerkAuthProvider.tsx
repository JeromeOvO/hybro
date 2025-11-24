'use client'

import { useAuth } from '@clerk/nextjs'
import { useEffect } from 'react'
import { setDefaultGetToken } from '@/lib/auth'

/**
 * Provider that sets up the default Clerk token getter for all API calls
 * This should wrap your app to enable automatic authentication
 */
export function ClerkAuthProvider({ children }: { children: React.ReactNode }) {
  const { getToken } = useAuth()

  useEffect(() => {
    // Set the default token getter so all API calls can use it automatically
    setDefaultGetToken(getToken)
  }, [getToken])

  return <>{children}</>
}
