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

// Get agent card from URL
export async function getAgentCardFromUrl(
  request: InspectionCenterRequest,
  getToken?: () => Promise<string | null>
): Promise<InspectionCenterResponse> {
  return apiPost<InspectionCenterResponse>(
    `${API_BASE_URL}/getAgentCardFromUrl`,
    request,
    getToken
  )
}

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

// Get agent by ID
export async function getAgent(
  agentId: string,
  getToken?: () => Promise<string | null>
): Promise<AgentCenterResponse> {
  return apiGet<AgentCenterResponse>(
    `${API_BASE_URL}/getAgent/${agentId}`,
    getToken
  )
}

// Get all agents
export async function getAllAgents(
  getToken?: () => Promise<string | null>
): Promise<AgentCenterResponse> {
  return apiGet<AgentCenterResponse>(
    `${API_BASE_URL}/getAllAgents`,
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

// Get agent list with conditions
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