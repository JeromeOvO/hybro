// Agent-related API functions
import type { 
  AgentCenterRequest, 
  AgentCenterResponse,
  InspectionCenterRequest,
  InspectionCenterResponse,
} from '@/lib/types'

import { getApiUrl } from '../utils'
import { apiGet, apiPost } from '../api-client'

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
  agentId: string
): Promise<AgentCenterResponse> {
  return apiGet<AgentCenterResponse>(
    `${API_BASE_URL}/getAgent/${agentId}`
  )
}

// Get all agents - PUBLIC
export async function getAllAgents(): Promise<AgentCenterResponse> {
  return apiGet<AgentCenterResponse>(
    `${API_BASE_URL}/getAllAgents`
  )
}

// Get agent list with conditions - PUBLIC
export async function getAgentListWithConditions(
  request: AgentCenterRequest
): Promise<AgentCenterResponse> {
  return apiPost<AgentCenterResponse>(
    `${API_BASE_URL}/getAgentListWithConditions`,
    request
  )
} 