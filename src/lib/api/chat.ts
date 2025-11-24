// Chat-related API functions
import type { ChatRequest, ChatResponse } from '@/lib/types'

import { getApiUrl } from '../utils'
import { getClientAuthHeaders } from '../auth'

const API_BASE_URL = getApiUrl('chat')

// Send message
export async function sendMessage(
  request: ChatRequest,
  getToken?: () => Promise<string | null>
): Promise<ChatResponse> {
  const headers = await getClientAuthHeaders(getToken)
  const response = await fetch(`${API_BASE_URL}/sendMessage`, {
    method: 'POST',
    headers,
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}
