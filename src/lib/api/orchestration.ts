// Orchestration-related API functions
import type { OrchestrationCenterResponse } from '@/lib/types'

import { getApiUrl } from '../utils'
import { apiPost } from '../api-client'

const API_BASE_URL = getApiUrl('orchestrationCenter')

// Decompose task
export async function decomposeTask(
  data: { task_id: string },
  getToken?: () => Promise<string | null>
): Promise<OrchestrationCenterResponse> {
  console.log('Calling decomposeTask with data:', data)
  
  try {
    const result = await apiPost<OrchestrationCenterResponse>(
      `${API_BASE_URL}/decomposeTask`,
      data,
      getToken
    )
    return result
  } catch (error) {
    console.error('decomposeTask error:', error)
    throw error
  }
}

// Assign agents to meta tasks by parent task ID
export async function assignAgentsToMetaTasks(
  data: { task_id: string },
  getToken?: () => Promise<string | null>
): Promise<OrchestrationCenterResponse> {
  console.log('Calling assignAgentsToMetaTasks with data:', data)
  
  try {
    const result = await apiPost<OrchestrationCenterResponse>(
      `${API_BASE_URL}/assignAgentsToMetaTasks`,
      data,
      getToken
    )
    return result
  } catch (error) {
    console.error('assignAgentsToMetaTasks error:', error)
    throw error
  }
}

// Assign agent to meta task
export async function assignAgentToMetaTask(
  data: { task_id: string },
  getToken?: () => Promise<string | null>
): Promise<OrchestrationCenterResponse> {
  console.log('Calling assignAgentToMetaTask with data:', data)
  
  try {
    const result = await apiPost<OrchestrationCenterResponse>(
      `${API_BASE_URL}/assignAgentToMetaTask`,
      data,
      getToken
    )
    return result
  } catch (error) {
    console.error('assignAgentToMetaTask error:', error)
    throw error
  }
}

// Run workflow
export async function runWorkflow(
  data: { task_id: string },
  getToken?: () => Promise<string | null>
): Promise<OrchestrationCenterResponse> {
  console.log('Calling runWorkflow with data:', data)
  
  try {
    const result = await apiPost<OrchestrationCenterResponse>(
      `${API_BASE_URL}/runWorkflow`,
      data,
      getToken
    )
    return result
  } catch (error) {
    console.error('runWorkflow error:', error)
    throw error
  }
}

// Retry meta task
export async function retryMetaTask(
  data: { task_id: string },
  getToken?: () => Promise<string | null>
): Promise<OrchestrationCenterResponse> {
  console.log('Calling retryMetaTask with data:', data)
  
  try {
    const result = await apiPost<OrchestrationCenterResponse>(
      `${API_BASE_URL}/retryMetaTask`,
      data,
      getToken
    )
    return result
  } catch (error) {
    console.error('retryMetaTask error:', error)
    throw error
  }
}

// Summarize meta task for base task
export async function summarizeMetaTaskForBaseTask(
  data: { task_id: string },
  getToken?: () => Promise<string | null>
): Promise<OrchestrationCenterResponse> {
  console.log('Calling summarizeMetaTaskForBaseTask with data:', data)
  
  try {
    const result = await apiPost<OrchestrationCenterResponse>(
      `${API_BASE_URL}/summarizeMetaTaskForBaseTask`,
      data,
      getToken
    )
    return result
  } catch (error) {
    console.error('summarizeMetaTaskForBaseTask error:', error)
    throw error
  }
} 


export async function processRoomUserMessage(
  data: { room_id: string, room_user_message_id: string, room_related_message_id : string },
  getToken?: () => Promise<string | null>
): Promise<OrchestrationCenterResponse> {
  console.log('Calling processRoomUserMessage with data:', data)
  
  try {
    const result = await apiPost<OrchestrationCenterResponse>(
      `${API_BASE_URL}/processRoomUserMessage`,
      data,
      getToken
    )
    return result
  } catch (error) {
    console.error('processRoomUserMessage error:', error)
    throw error
  }
}