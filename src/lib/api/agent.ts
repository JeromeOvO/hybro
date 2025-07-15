// Agent-related API functions
import type { 
  AgentCenterRequest, 
  AgentCenterResponse,
  InspectionCenterRequest,
  InspectionCenterResponse,
  InsepectionCenterConnectionValidationResponse
} from '@/lib/types'

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'

// Get agent card from URL
export async function getAgentCardFromUrl(request: InspectionCenterRequest): Promise<InspectionCenterResponse> {
  const response = await fetch(`${API_BASE_URL}/agent/getAgentCardFromUrl`, {
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
  const response = await fetch(`${API_BASE_URL}/agent/registerAgent`, {
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
  const response = await fetch(`${API_BASE_URL}/agent/getAgent/${agentId}`, {
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
  const response = await fetch(`${API_BASE_URL}/agent/getAllAgents`, {
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
  const response = await fetch(`${API_BASE_URL}/agent/deleteAgent`, {
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
  const response = await fetch(`${API_BASE_URL}/agent/getAgentListWithConditions`, {
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