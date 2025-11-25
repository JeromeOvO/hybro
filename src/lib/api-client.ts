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
}

/**
 * Make an authenticated API request
 * Automatically includes authentication headers
 */
export async function apiClient<T = unknown>(
  url: string,
  options: ApiClientOptions = {}
): Promise<T> {
  const { method = 'GET', body, headers: customHeaders, getToken } = options

  // Get auth headers (will use default getToken if not provided)
  const authHeaders = await getClientAuthHeaders(getToken)

  // Merge auth headers with custom headers
  const headers = {
    ...authHeaders,
    ...customHeaders,
  }

  const fetchOptions: RequestInit = {
    method,
    headers,
  }

  // Add body for non-GET requests
  if (body && method !== 'GET') {
    fetchOptions.body = JSON.stringify(body)
  }

  const response = await fetch(url, fetchOptions)

  if (!response.ok) {
    const errorText = await response.text()
    throw new Error(`HTTP error! status: ${response.status}, message: ${errorText}`)
  }

  return await response.json()
}

/**
 * Convenience method for GET requests
 */
export async function apiGet<T = unknown>(
  url: string,
  getToken?: () => Promise<string | null>
): Promise<T> {
  return apiClient<T>(url, { method: 'GET', getToken })
}

/**
 * Convenience method for POST requests
 */
export async function apiPost<T = unknown>(
  url: string,
  body?: unknown,
  getToken?: () => Promise<string | null>
): Promise<T> {
  return apiClient<T>(url, { method: 'POST', body, getToken })
}

/**
 * Convenience method for PUT requests
 */
export async function apiPut<T = unknown>(
  url: string,
  body?: unknown,
  getToken?: () => Promise<string | null>
): Promise<T> {
  return apiClient<T>(url, { method: 'PUT', body, getToken })
}

/**
 * Convenience method for DELETE requests
 */
export async function apiDelete<T = unknown>(
  url: string,
  getToken?: () => Promise<string | null>
): Promise<T> {
  return apiClient<T>(url, { method: 'DELETE', getToken })
}
