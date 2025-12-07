/**
 * Centralized API client with built-in authentication
 * Automatically handles auth headers for all API requests
 */

import { getClientAuthHeaders } from './auth'

interface ApiClientOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH'
  body?: unknown
  headers?: HeadersInit
  getToken?: () => Promise<string | null>
  signal?: AbortSignal  // Support for request cancellation
  timeoutMs?: number  // Optional per-request timeout
}

// Default timeout for API requests (increased to avoid premature aborts on slow endpoints)
const DEFAULT_TIMEOUT_MS = 60000

/**
 * Make an authenticated API request
 * Automatically includes authentication headers
 */
export async function apiClient<T = unknown>(
  url: string,
  options: ApiClientOptions = {}
): Promise<T> {
  const { method = 'GET', body, headers: customHeaders, getToken, signal, timeoutMs } = options

  // Get auth headers (will use default getToken if not provided)
  const authHeaders = await getClientAuthHeaders(getToken)

  // Merge auth headers with custom headers
  const headers = {
    ...authHeaders,
    ...customHeaders,
  }

  // Support cancellation and timeout
  const controller = new AbortController()
  const timeout = timeoutMs ?? DEFAULT_TIMEOUT_MS
  let timedOut = false
  const timeoutId = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeout)

  // Tie external signal if provided
  if (signal) {
    signal.addEventListener('abort', () => controller.abort(), { once: true })
  }

  const fetchOptions: RequestInit = {
    method,
    headers,
    signal: controller.signal,  // Pass abort signal to fetch
  }

  // Add body for non-GET requests
  if (body && method !== 'GET') {
    fetchOptions.body = JSON.stringify(body)
  }

  try {
    const response = await fetch(url, fetchOptions)
    clearTimeout(timeoutId)

    if (!response.ok) {
      const errorText = await response.text()
      throw new Error(`HTTP error! status: ${response.status}, message: ${errorText}`)
    }

    return await response.json()
  } catch (error) {
    clearTimeout(timeoutId)

    // Preserve AbortError so callers can treat it as cancellation.
    if (error instanceof Error && error.name === 'AbortError') {
      if (timedOut) {
        console.error('[apiClient] Timeout', { url, timeoutMs: timeout, method })
      } else {
        console.debug('[apiClient] Aborted', { url, method })
      }
      // Re-throw the original AbortError (do not wrap) so upstream can detect it.
      throw error
    }

    console.error('[apiClient] Request failed', { url, method, error })
    throw error
  }
}

/**
 * Convenience method for GET requests
 */
export async function apiGet<T = unknown>(
  url: string,
  getToken?: () => Promise<string | null>,
  signal?: AbortSignal,
  timeoutMs?: number
): Promise<T> {
  return apiClient<T>(url, { method: 'GET', getToken, signal, timeoutMs })
}

/**
 * Convenience method for POST requests
 */
export async function apiPost<T = unknown>(
  url: string,
  body?: unknown,
  getToken?: () => Promise<string | null>,
  signal?: AbortSignal,
  timeoutMs?: number
): Promise<T> {
  return apiClient<T>(url, { method: 'POST', body, getToken, signal, timeoutMs })
}

/**
 * Convenience method for PUT requests
 */
export async function apiPut<T = unknown>(
  url: string,
  body?: unknown,
  getToken?: () => Promise<string | null>,
  signal?: AbortSignal,
  timeoutMs?: number
): Promise<T> {
  return apiClient<T>(url, { method: 'PUT', body, getToken, signal, timeoutMs })
}

/**
 * Convenience method for DELETE requests
 */
export async function apiDelete<T = unknown>(
  url: string,
  getToken?: () => Promise<string | null>,
  signal?: AbortSignal,
  timeoutMs?: number
): Promise<T> {
  return apiClient<T>(url, { method: 'DELETE', getToken, signal, timeoutMs })
}
