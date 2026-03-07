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

// ── Legacy built-in group IDs (kept for backward compatibility) ──────────

export const BUILTIN_GROUP_ALL_AGENTS = "all_agents"
/** @deprecated Use MessageTargetMode "room_default" instead of this sentinel. */
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
  | { message_target_mode: "room_default" }
  | { message_target_mode: "all_agents" }
  | { message_target_mode: "saved_group"; target_group_id: string }

export interface MentionDispatchInput {
  mentioned_agent_ids: string[]
}

export type MessageDispatchInput =
  | MentionDispatchInput
  | TargetModeDispatchInput

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

// ── Legacy normalization helpers ─────────────────────────────────────────

/**
 * Normalize a legacy `target_group` value into canonical MessageTargetMode.
 * Canonical fields always win; this is only used as fallback.
 */
export function normalizeLegacyTargetGroup(
  targetGroup: string,
): TargetModeDispatchInput {
  switch (targetGroup) {
    case BUILTIN_GROUP_ROOM_TEAM:
      return { message_target_mode: "room_default" }
    case BUILTIN_GROUP_ALL_AGENTS:
      return { message_target_mode: "all_agents" }
    default:
      return { message_target_mode: "saved_group", target_group_id: targetGroup }
  }
}

