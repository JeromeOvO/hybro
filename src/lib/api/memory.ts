 // Agent-related API functions
import type { 
    ChatMemoryRequest, 
    ChatMemoryResponse,
  } from '@/lib/types'
  
  import { getApiUrl } from '../utils'
  import { getClientAuthHeaders } from '../auth'
  // Using Next.js API routes as proxy to avoid CORS issues
  const API_BASE_URL = getApiUrl('memoryCenter')

  // Add chat context
  export async function addChatContext(
    request: ChatMemoryRequest,
    getToken?: () => Promise<string | null>
  ): Promise<ChatMemoryResponse> {
    const headers = await getClientAuthHeaders(getToken)
    const response = await fetch(`${API_BASE_URL}/addChatContext`, {
      method: 'POST',
      headers,
      body: JSON.stringify(request),
    })
  
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`)
    }
  
    return await response.json()
  }
  
  export async function getChatContextBySessionId(
    request: ChatMemoryRequest,
    getToken?: () => Promise<string | null>
  ): Promise<ChatMemoryResponse> {
    const headers = await getClientAuthHeaders(getToken)
    const response = await fetch(`${API_BASE_URL}/getChatContextBySessionId`, {
      method: 'POST',
      headers,
      body: JSON.stringify(request),
    })

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`)
    }

    return await response.json()
  }

  export async function updateChatContextBySessionId(
    request: ChatMemoryRequest,
    getToken?: () => Promise<string | null>
  ): Promise<ChatMemoryResponse> {
    const headers = await getClientAuthHeaders(getToken)
    const response = await fetch(`${API_BASE_URL}/updateChatContextBySessionId`, {
      method: 'POST',
      headers,
      body: JSON.stringify(request),
    })

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`)
    }

    return await response.json()
  }

  export async function deleteChatContextBySessionId(
    request: ChatMemoryRequest,
    getToken?: () => Promise<string | null>
  ): Promise<ChatMemoryResponse> {
    const headers = await getClientAuthHeaders(getToken)
    const response = await fetch(`${API_BASE_URL}/deleteChatContextBySessionId`, {
      method: 'POST',
      headers,
      body: JSON.stringify(request),
    })

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`)
    }

    return await response.json()
  }
  
  