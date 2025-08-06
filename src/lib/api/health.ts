// Health-related API functions
import type { HealthCheckResponse } from '@/lib/types'

// Using Next.js API routes as proxy to avoid CORS issues

// Health check
export async function healthCheck(): Promise<HealthCheckResponse> {
  const response = await fetch('/api/health', {
    method: 'GET',
    headers: {
      'Content-Type': 'application/json',
    },
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
} 