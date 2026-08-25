/**
 * Agent Group Types
 *
 * An agent group is a set of agent IDs with two scopes:
 *   - Saved group: reusable across chats and rooms (persisted via CRUD).
 *   - Room group: room-scoped snapshot that lives with the room.
 *
 * Applying a saved group to a room copies its members into the room snapshot.
 * Later edits to the saved group do not change existing rooms.
 */

// ── Built-in group selection IDs ─────────────────────────────────────────

export const BUILTIN_GROUP_ALL_AGENTS = "all_agents"
/** UI selection id for room membership / room_default dispatch. */
export const BUILTIN_GROUP_ROOM_TEAM = "room_team"

// ── Canonical enums ──────────────────────────────────────────────────────

export type AgentAvailability =
  | "available"
  | "inaccessible"
  | "inactive"
  | "deleted"

export type RoomMembershipState = "snapshot"

export type RoomMembershipSeedInput =
  | "manual"
  | "saved_group"
  | "all_current_agents"

export type MembershipOrigin =
  | "manual"
  | "saved_group"
  | "all_current_agents"

export type MembershipOriginStatus =
  | "seeded_never_edited"
  | "seeded_edited"
  | "manual"

export type MessageTargetMode =
  | "room_default"
  | "all_agents"
  | "saved_group"

export type RoomDefaultStatus =
  | "ok"
  | "degraded"
  | "empty"
  | "all_unavailable"

// ── Canonical read models ────────────────────────────────────────────────

export interface RoomAgentRef {
  id: string
  name?: string
  availability: AgentAvailability
}

/**
 * Stale agent references: room/group members not found in the current
 * availableAgents catalog.  Rendered as disabled placeholders and
 * merged back on save so they are never silently pruned.
 */
export type StaleAgentRef = RoomAgentRef

export interface RoomMembershipReadModel {
  membership_state: RoomMembershipState
  agents: RoomAgentRef[]
  membership_origin: MembershipOrigin
  membership_origin_status: MembershipOriginStatus
  source_group_id?: string
  source_group_name?: string
  room_default_status: RoomDefaultStatus
}

export interface SavedGroupReadModel {
  group_id: string
  name: string
  agents: RoomAgentRef[]
}

// ── Canonical write inputs ───────────────────────────────────────────────

export type RoomMembershipWriteInput =
  | { membership_seed_input: "manual"; room_agent_ids: string[] }
  | { membership_seed_input: "saved_group"; seed_group_id: string }
  | { membership_seed_input: "all_current_agents"; seed_all_current_agents: true }

export type TargetModeDispatchInput =
  | {
      message_target_mode: "room_default"
      target_group_id?: never
      mentioned_agent_ids?: never
    }
  | {
      message_target_mode: "all_agents"
      target_group_id?: never
      mentioned_agent_ids?: never
    }
  | {
      message_target_mode: "saved_group"
      target_group_id: string
      mentioned_agent_ids?: never
    }

export type MentionDispatchInput = {
  mentioned_agent_ids: [string, ...string[]]
  message_target_mode?: never
  target_group_id?: never
}

export type MessageDispatchInput =
  | MentionDispatchInput
  | TargetModeDispatchInput

export type AgentScopeInput =
  import('@/lib/types/request').AgentScopeInput

export function dispatchToAgentScope(dispatch: MessageDispatchInput): AgentScopeInput {
  if (isMentionDispatchInput(dispatch)) {
    return { source: 'mention', agent_ids: dispatch.mentioned_agent_ids }
  }
  if (dispatch.message_target_mode === 'saved_group') {
    return { source: 'saved_group', group_id: dispatch.target_group_id }
  }
  return { source: dispatch.message_target_mode }
}

export function isMentionDispatchInput(dispatch: MessageDispatchInput): dispatch is MentionDispatchInput {
  return Array.isArray(dispatch.mentioned_agent_ids) && dispatch.mentioned_agent_ids.length > 0
}

export function isMessageDispatchInput(value: unknown): value is MessageDispatchInput {
  if (!value || typeof value !== 'object') return false
  const dispatch = value as {
    mentioned_agent_ids?: unknown
    message_target_mode?: unknown
    target_group_id?: unknown
  }
  const keys = Object.keys(dispatch)
  const hasMentions = 'mentioned_agent_ids' in dispatch
  const hasMode = 'message_target_mode' in dispatch

  if (hasMentions && hasMode) return false
  if (hasMentions) {
    if (!keys.every((key) => key === 'mentioned_agent_ids')) return false
    return Array.isArray(dispatch.mentioned_agent_ids)
      && dispatch.mentioned_agent_ids.length > 0
      && dispatch.mentioned_agent_ids.every((id) => typeof id === 'string' && id.length > 0)
      && !('target_group_id' in dispatch)
  }
  if (dispatch.message_target_mode === 'room_default' || dispatch.message_target_mode === 'all_agents') {
    if (!keys.every((key) => key === 'message_target_mode')) return false
    return !('mentioned_agent_ids' in dispatch) && !('target_group_id' in dispatch)
  }
  if (dispatch.message_target_mode === 'saved_group') {
    if (!keys.every((key) => key === 'message_target_mode' || key === 'target_group_id')) return false
    return typeof dispatch.target_group_id === 'string'
      && dispatch.target_group_id.length > 0
      && !('mentioned_agent_ids' in dispatch)
  }
  return false
}

export function assertMessageDispatchInput(value: unknown): asserts value is MessageDispatchInput {
  if (!isMessageDispatchInput(value)) {
    throw new Error('Invalid MessageDispatchInput')
  }
}

export function resolveSelectedGroupDispatch(selectedGroup: string): TargetModeDispatchInput {
  switch (selectedGroup) {
    case BUILTIN_GROUP_ROOM_TEAM:
      return { message_target_mode: "room_default" }
    case BUILTIN_GROUP_ALL_AGENTS:
      return { message_target_mode: "all_agents" }
    default:
      return { message_target_mode: "saved_group", target_group_id: selectedGroup }
  }
}

// ── Dispatch result types ────────────────────────────────────────────────

export interface ScopeResolutionError {
  code:
    | "invalid_target"
    | "group_not_usable"
    | "unauthorized_mention"
    | "empty_scope"
  message: string
}

export interface DispatchAcceptedResponse {
  processing_status: "queued" | "running"
  dispatch_root_message_id: string
  scope_resolution_error?: undefined
}

export interface DispatchRejectedResponse {
  processing_status: "rejected"
  dispatch_root_message_id?: undefined
  scope_resolution_error: ScopeResolutionError
}

export type DispatchResponse =
  | DispatchAcceptedResponse
  | DispatchRejectedResponse

export interface AgentExecutionResult {
  agent_id: string
  status: "queued" | "running" | "completed" | "failed" | "unavailable"
  reason?: "inactive" | "inaccessible" | "deleted" | "runtime_error"
  message?: string
}

// ── Persisted saved-group model (CRUD) ───────────────────────────────────

export interface AgentGroup {
  group_id: string
  name: string
  description?: string | null
  type: "builtin" | "user"
  owner_id: string | null
  agents: string[]
  created_at?: string
  updated_at?: string
}

export interface AgentGroupCreateRequest {
  name: string
  description?: string
  owner_id: string
  agents: string[]
  /** Stable owner-scoped key for idempotent preset creation. */
  preset_key?: string
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

// ── Helpers ──────────────────────────────────────────────────────────────

export function isBuiltinGroup(groupId: string): boolean {
  return groupId === BUILTIN_GROUP_ALL_AGENTS || groupId === BUILTIN_GROUP_ROOM_TEAM
}

export function getGroupDisplayName(group: AgentGroup, agentCount?: number): string {
  if (group.group_id === BUILTIN_GROUP_ALL_AGENTS) {
    return "All Agents"
  }
  if (group.group_id === BUILTIN_GROUP_ROOM_TEAM) {
    return agentCount !== undefined ? `Room Team (${agentCount})` : "Room Team"
  }
  return group.name
}
