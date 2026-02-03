// Agent-related API functions
import type { 
  AgentCenterRequest, 
  AgentCenterResponse,
  InspectionCenterRequest,
  InspectionCenterResponse,
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
): Promise<InspectionCenterResponse> {
  return apiPost<InspectionCenterResponse>(
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