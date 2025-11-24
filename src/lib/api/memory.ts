// Memory-related API functions
import type { 
    ChatMemoryRequest, 
    ChatMemoryResponse,
  } from '@/lib/types'
  
  import { getApiUrl } from '../utils'
  import { apiPost } from '../api-client'
  
  const API_BASE_URL = getApiUrl('memoryCenter')

  // Add chat context
  export async function addChatContext(
    request: ChatMemoryRequest,
    getToken?: () => Promise<string | null>
  ): Promise<ChatMemoryResponse> {
    return apiPost<ChatMemoryResponse>(
      `${API_BASE_URL}/addChatContext`,
      request,
      getToken
    )
  }
  
  export async function getChatContextBySessionId(
    request: ChatMemoryRequest,
    getToken?: () => Promise<string | null>
  ): Promise<ChatMemoryResponse> {
    return apiPost<ChatMemoryResponse>(
      `${API_BASE_URL}/getChatContextBySessionId`,
      request,
      getToken
    )
  }

  export async function updateChatContextBySessionId(
    request: ChatMemoryRequest,
    getToken?: () => Promise<string | null>
  ): Promise<ChatMemoryResponse> {
    return apiPost<ChatMemoryResponse>(
      `${API_BASE_URL}/updateChatContextBySessionId`,
      request,
      getToken
    )
  }

  export async function deleteChatContextBySessionId(
    request: ChatMemoryRequest,
    getToken?: () => Promise<string | null>
  ): Promise<ChatMemoryResponse> {
    return apiPost<ChatMemoryResponse>(
      `${API_BASE_URL}/deleteChatContextBySessionId`,
      request,
      getToken
    )
  }
  
  