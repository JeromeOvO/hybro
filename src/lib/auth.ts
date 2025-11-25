/**
 * Client-side authentication utilities for Clerk integration
 * Safe to import in client components
 */

/**
 * Default token getter for client-side API calls
 * Set by ClerkAuthProvider wrapper
 */
let defaultGetToken: (() => Promise<string | null>) | null = null

/**
 * Set the default token getter (called automatically by ClerkAuthProvider)
 */
export function setDefaultGetToken(getToken: () => Promise<string | null>) {
  defaultGetToken = getToken
}

/**
 * Get auth token (stub for compatibility with unused API routes)
 * @deprecated These API routes are not used - direct backend calls are used instead
 */
export async function getAuthToken(): Promise<string | null> {
  return null
}

/**
 * Get auth headers (stub for compatibility with unused API routes)
 * @deprecated These API routes are not used - direct backend calls are used instead
 */
export async function getAuthHeaders(): Promise<Record<string, string>> {
  return {}
}

/**
 * Get authorization headers for client-side API requests
 * Uses provided getToken or falls back to default
 */
export async function getClientAuthHeaders(
  getToken?: () => Promise<string | null>
): Promise<Record<string, string>> {
  const baseHeaders = { 'Content-Type': 'application/json' }
  
  const tokenGetter = getToken || defaultGetToken
  
  if (!tokenGetter) {
    console.warn('No token getter available - API call will be made without authentication')
    return baseHeaders
  }

  try {
    const token = await tokenGetter()
    
    if (token) {
      return {
        ...baseHeaders,
        'Authorization': `Bearer ${token}`,
      }
    } else {
      console.warn('Token getter returned null - user may not be authenticated')
    }
  } catch (error) {
    console.warn('Failed to get auth token:', error)
  }
  
  return baseHeaders
}

