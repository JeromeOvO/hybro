/**
 * Agent Group Types
 * 
 * Agent groups are reusable templates for selecting agents.
 * They can be built-in (All Agents, Room Team) or user-created.
 */

// Built-in group IDs
export const BUILTIN_GROUP_ALL_AGENTS = "all_agents"
export const BUILTIN_GROUP_ROOM_TEAM = "room_team"

export interface AgentGroup {
  group_id: string
  name: string
  description?: string | null
  type: "builtin" | "user"
  owner_id: string | null
  agents: string[]  // List of agent IDs
  created_at?: string
  updated_at?: string
}

export interface AgentGroupCreateRequest {
  name: string
  description?: string
  owner_id: string
  agents: string[]
}

export interface AgentGroupUpdateRequest {
  group_id: string
  name?: string
  description?: string
  agents?: string[]
}

export interface AgentGroupResponse {
  success: boolean
  group?: AgentGroup
  error?: string
  status_code?: number
}

export interface AgentGroupListResponse {
  success: boolean
  groups?: AgentGroup[]
  error?: string
  status_code?: number
}

// Helper to check if a group is built-in
export function isBuiltinGroup(groupId: string): boolean {
  return groupId === BUILTIN_GROUP_ALL_AGENTS || groupId === BUILTIN_GROUP_ROOM_TEAM
}

// Helper to get display name for built-in groups
export function getGroupDisplayName(group: AgentGroup, agentCount?: number): string {
  if (group.group_id === BUILTIN_GROUP_ALL_AGENTS) {
    return "All Agents"
  }
  if (group.group_id === BUILTIN_GROUP_ROOM_TEAM) {
    return agentCount !== undefined ? `Room Team (${agentCount})` : "Room Team"
  }
  return group.name
}

