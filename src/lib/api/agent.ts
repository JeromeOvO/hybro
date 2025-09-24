// Agent-related API functions
import type { 
  AgentCenterRequest, 
  AgentCenterResponse,
  InspectionCenterRequest,
  InspectionCenterResponse,
} from '@/lib/types'

import { getApiUrl } from '../utils'

const API_BASE_URL = getApiUrl('agent')

// Get agent card from URL
export async function getAgentCardFromUrl(request: InspectionCenterRequest): Promise<InspectionCenterResponse> {
  const response = await fetch(`${API_BASE_URL}/getAgentCardFromUrl`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}

// Register agent
export async function registerAgent(request: AgentCenterRequest): Promise<AgentCenterResponse> {
  const response = await fetch(`${API_BASE_URL}/registerAgent`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}

// Get agent by ID
export async function getAgent(agentId: string): Promise<AgentCenterResponse> {
  const response = await fetch(`${API_BASE_URL}/getAgent/${agentId}`, {
    method: 'GET',
    headers: {
      'Content-Type': 'application/json',
    },
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}

// Get all agents
export async function getAllAgents(): Promise<AgentCenterResponse> {
  const response = await fetch(`${API_BASE_URL}/getAllAgents`, {
    method: 'GET',
    headers: {
      'Content-Type': 'application/json',
    },
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}

// Delete agent
export async function deleteAgent(request: AgentCenterRequest): Promise<AgentCenterResponse> {
  const response = await fetch(`${API_BASE_URL}/deleteAgent`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}

// Get agent list with conditions
export async function getAgentListWithConditions(request: AgentCenterRequest): Promise<AgentCenterResponse> {
  const response = await fetch(`${API_BASE_URL}/getAgentListWithConditions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
} 