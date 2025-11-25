// Chat-related API functions
import type { ChatRequest, ChatResponse } from '@/lib/types'

import { getApiUrl } from '../utils'
import { apiPost } from '../api-client'

const API_BASE_URL = getApiUrl('chat')

// Send message
export async function sendMessage(
  request: ChatRequest,
  getToken?: () => Promise<string | null>
): Promise<ChatResponse> {
  return apiPost<ChatResponse>(
    `${API_BASE_URL}/sendMessage`,
    request,
    getToken
  )
}
