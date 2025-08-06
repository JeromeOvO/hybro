// Chat-related API functions
import type { ChatRequest, ChatResponse } from '@/lib/types'

// Using Next.js API routes as proxy to avoid CORS issues
const API_BASE_URL = '/api/chat'

// Send message
export async function sendMessage(request: ChatRequest): Promise<ChatResponse> {
  const response = await fetch(`${API_BASE_URL}/sendMessage`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}
