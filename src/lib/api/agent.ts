// Agent-related API functions
import type { 
  AgentCenterRequest, 
  AgentCenterResponse,
  InspectionCenterRequest,
} from '@/lib/types'

import { getApiUrl } from '../utils'
import { apiGet, apiPost, apiPut } from '../api-client'

const API_BASE_URL = getApiUrl('agent')

// ============= PROTECTED ENDPOINTS (Auth Required) =============

// Register agent
export async function registerAgent(
  request: AgentCenterRequest,
  getToken?: () => Promise<string | null>
): Promise<AgentCenterResponse> {
  return apiPost<AgentCenterResponse>(
    `${API_BASE_URL}/registerAgent`,
    request,
    getToken
  )
}

// Get agents from provider_id
export async function getAgentsByProviderId(
  getToken?: () => Promise<string | null>
): Promise<AgentCenterResponse> {
  return apiGet<AgentCenterResponse>(
    `${API_BASE_URL}/getAgent/me`,
    getToken
  )
}

// Update agent settings (rate limits, status, etc.)
export interface UpdateAgentRequest {
  rate_limit_per_user_per_hour?: number | null
  rate_limit_system_per_hour?: number | null
  agent_status?: 'active' | 'inactive'
  is_public?: boolean
}

export async function updateAgent(
  agentId: string,
  request: UpdateAgentRequest,
  getToken?: () => Promise<string | null>
): Promise<AgentCenterResponse> {
  return apiPut<AgentCenterResponse>(
    `${API_BASE_URL}/updateAgent/${agentId}`,
    request,
    getToken
  )
}

// Upload a custom avatar image for an agent
export async function uploadAgentAvatar(
  agentId: string,
  file: File,
  getToken?: () => Promise<string | null>
): Promise<{ iconUrl: string }> {
  const { getClientAuthHeaders } = await import('../auth')
  const authHeaders = await getClientAuthHeaders(getToken)
  // Remove Content-Type so the browser sets multipart/form-data with the correct boundary
  const { 'Content-Type': _ct, ...multipartHeaders } = authHeaders as Record<string, string>
  const formData = new FormData()
  formData.append('file', file)
  const response = await fetch(`${API_BASE_URL}/${agentId}/avatar`, {
    method: 'POST',
    headers: multipartHeaders,
    body: formData,
  })
  if (!response.ok) {
    const text = await response.text()
    throw new Error(`Avatar upload failed (${response.status}): ${text}`)
  }
  return response.json()
}

// Delete agent
export async function deleteAgent(
  request: AgentCenterRequest,
  getToken?: () => Promise<string | null>
): Promise<AgentCenterResponse> {
  return apiPost<AgentCenterResponse>(
    `${API_BASE_URL}/deleteAgent`,
    request,
    getToken
  )
}

// ============= PUBLIC ENDPOINTS (No Auth Required) =============

// Get agent card from URL - PUBLIC
export async function getAgentCardFromUrl(
  request: InspectionCenterRequest
): Promise<AgentCenterResponse> {
  return apiPost<AgentCenterResponse>(
    `${API_BASE_URL}/getAgentCardFromUrl`,
    request
  )
}

// Get agent by ID - PUBLIC
export async function getAgent(
  agentId: string,
  signal?: AbortSignal,
  getToken?: () => Promise<string | null>
): Promise<AgentCenterResponse> {
  return apiGet<AgentCenterResponse>(
    `${API_BASE_URL}/getAgent/${agentId}`,
    getToken,
    signal
  )
}

// Get all agents - PUBLIC, with optional timeout override
export async function getAllAgents(
  signal?: AbortSignal, 
  timeoutMs?: number,
  getToken?: () => Promise<string | null>
): Promise<AgentCenterResponse> {
  return apiGet<AgentCenterResponse>(
    `${API_BASE_URL}/getAllAgents`,
    getToken,
    signal,
    timeoutMs
  )
}

// Get all active agents - PUBLIC, with optional timeout override
// Returns only agents with active status, filtering out inactive and deleted agents
export async function getAllActiveAgents(
  signal?: AbortSignal, 
  timeoutMs?: number,
  getToken?: () => Promise<string | null>
): Promise<AgentCenterResponse> {
  return apiGet<AgentCenterResponse>(
    `${API_BASE_URL}/getAllActiveAgents`,
    getToken,
    signal,
    timeoutMs
  )
}

// Get agent list with conditions - PUBLIC
export async function getAgentListWithConditions(
  request: AgentCenterRequest,
  getToken?: () => Promise<string | null>
): Promise<AgentCenterResponse> {
  return apiPost<AgentCenterResponse>(
    `${API_BASE_URL}/getAgentListWithConditions`,
    request,
    getToken
  )
} 