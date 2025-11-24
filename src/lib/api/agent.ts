// Agent-related API functions
import type { 
  AgentCenterRequest, 
  AgentCenterResponse,
  InspectionCenterRequest,
  InspectionCenterResponse,
} from '@/lib/types'

import { getApiUrl } from '../utils'
import { getClientAuthHeaders } from '../auth'

const API_BASE_URL = getApiUrl('agent')

// Get agent card from URL
export async function getAgentCardFromUrl(
  request: InspectionCenterRequest,
  getToken?: () => Promise<string | null>
): Promise<InspectionCenterResponse> {
  const headers = await getClientAuthHeaders(getToken)
  const response = await fetch(`${API_BASE_URL}/getAgentCardFromUrl`, {
    method: 'POST',
    headers,
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}

// Register agent
export async function registerAgent(
  request: AgentCenterRequest,
  getToken?: () => Promise<string | null>
): Promise<AgentCenterResponse> {
  const headers = await getClientAuthHeaders(getToken)
  const response = await fetch(`${API_BASE_URL}/registerAgent`, {
    method: 'POST',
    headers,
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}

// Get agent by ID
export async function getAgent(
  agentId: string,
  getToken?: () => Promise<string | null>
): Promise<AgentCenterResponse> {
  const headers = await getClientAuthHeaders(getToken)
  const response = await fetch(`${API_BASE_URL}/getAgent/${agentId}`, {
    method: 'GET',
    headers,
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}

// Get all agents
export async function getAllAgents(
  getToken?: () => Promise<string | null>
): Promise<AgentCenterResponse> {
  const headers = await getClientAuthHeaders(getToken)
  const response = await fetch(`${API_BASE_URL}/getAllAgents`, {
    method: 'GET',
    headers,
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}

// Delete agent
export async function deleteAgent(
  request: AgentCenterRequest,
  getToken?: () => Promise<string | null>
): Promise<AgentCenterResponse> {
  const headers = await getClientAuthHeaders(getToken)
  const response = await fetch(`${API_BASE_URL}/deleteAgent`, {
    method: 'POST',
    headers,
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}

// Get agent list with conditions
export async function getAgentListWithConditions(
  request: AgentCenterRequest,
  getToken?: () => Promise<string | null>
): Promise<AgentCenterResponse> {
  const headers = await getClientAuthHeaders(getToken)
  const response = await fetch(`${API_BASE_URL}/getAgentListWithConditions`, {
    method: 'POST',
    headers,
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
} 