/**
 * Agent Group API Client
 */

import { getApiUrl } from '../utils'
import { apiPost } from '../api-client'
import type { 
  AgentGroup, 
  AgentGroupCreateRequest, 
  AgentGroupUpdateRequest,
  AgentGroupResponse,
  AgentGroupListResponse
} from '../types/agent-group'

const API_BASE_URL = getApiUrl('agentGroups')

/**
 * Create a new agent group
 */
export async function createAgentGroup(
  request: AgentGroupCreateRequest,
  getToken?: () => Promise<string | null>
): Promise<AgentGroupResponse> {
  return apiPost<AgentGroupResponse>(
    `${API_BASE_URL}/create`,
    request,
    getToken
  )
}

/**
 * List all agent groups for a user (including built-in groups)
 */
export async function listAgentGroups(
  owner_id: string,
  getToken?: () => Promise<string | null>
): Promise<AgentGroupListResponse> {
  return apiPost<AgentGroupListResponse>(
    `${API_BASE_URL}/list`,
    { owner_id },
    getToken
  )
}

/**
 * Get a specific agent group by ID
 */
export async function getAgentGroup(
  group_id: string,
  getToken?: () => Promise<string | null>
): Promise<AgentGroupResponse> {
  return apiPost<AgentGroupResponse>(
    `${API_BASE_URL}/get`,
    { group_id },
    getToken
  )
}

/**
 * Update an agent group
 */
export async function updateAgentGroup(
  request: AgentGroupUpdateRequest,
  getToken?: () => Promise<string | null>
): Promise<AgentGroupResponse> {
  return apiPost<AgentGroupResponse>(
    `${API_BASE_URL}/update`,
    request,
    getToken
  )
}

/**
 * Delete an agent group
 */
export async function deleteAgentGroup(
  group_id: string,
  getToken?: () => Promise<string | null>
): Promise<{ success: boolean; error?: string; status_code?: number }> {
  return apiPost<{ success: boolean; error?: string; status_code?: number }>(
    `${API_BASE_URL}/delete`,
    { group_id },
    getToken
  )
}

