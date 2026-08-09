/**
 * Request types for the backend API.
 *
 * A2A protocol types are imported from the canonical @a2a-js/sdk package.
 * Only backend-specific request wrappers and orchestration types are defined here.
 */

// ── Re-export A2A types needed by downstream consumers of this module ────
export type {
  TaskState,
  Part,
  TextPart,
  FilePart,
  DataPart,
  FileWithBytes,
  FileWithUri,
  Message,
  Task,
  TaskStatus,
  Artifact,
  AgentCard,
  AgentCapabilities,
  AgentExtension,
  AgentInterface,
  AgentProvider,
  AgentSkill,
  AgentCardSignature,
  SecurityScheme,
  APIKeySecurityScheme,
  HTTPAuthSecurityScheme,
  OAuth2SecurityScheme,
  OpenIdConnectSecurityScheme,
  MutualTLSSecurityScheme,
  OAuthFlows,
  AuthorizationCodeOAuthFlow,
  ClientCredentialsOAuthFlow,
  ImplicitOAuthFlow,
  PasswordOAuthFlow,
} from '@a2a-js/sdk'

import type {
  AgentCard,
  Task,
  Message,
} from '@a2a-js/sdk'

import type { Room } from './response'

export type { Room }

/**
 * The location of the API key.
 * (Not exported from @a2a-js/sdk as a standalone type.)
 */
export type In = "cookie" | "header" | "query";

/**
 * Identifies the sender of the message.
 * (Not exported from @a2a-js/sdk as a standalone type.)
 */
export type Role = "agent" | "user";

export type AgentStatus = "active" | "inactive" | "deleted";

export interface Agent {
  agent_id: string;
  provider_id?: string | null;
  agent_card: AgentCard;
  public_url?: string | null;
  agent_status?: AgentStatus | null;
  call_count?: number;
  call_success_count?: number;
  like_count?: number;
  dislike_count?: number;
}
export interface AgentCenterRequest {
  agent_id?: string | null;
  agent_url?: string | null;
  provider_id?: string | null;
  agent_card?: AgentCard | null;
  call_increment?: number | null;
  call_success_increment?: number | null;
  like_increment?: number | null;
  dislike_increment?: number | null;
  query_text?: string | null;
  agent?: Agent | null;
  agent_count?: number | null;
}
export interface APIKeyCreateRequest {
  name: string;
}
export interface AgentCreate {
  agent_url: string;
  agent_card: AgentCard;
  call_count?: number | null;
  call_success_count?: number | null;
  like_count?: number | null;
  dislike_count?: number | null;
  agent_status?: AgentStatus | null;
  /**
   * Must be a valid UUID string
   */
  agent_id?: string | null;
}
export interface AgentGroupCreateRequest {
  name: string;
  description?: string | null;
  owner_id: string;
  agents?: string[];
}
export interface AgentGroupRequest {
  group_id?: string | null;
  name?: string | null;
  description?: string | null;
  owner_id?: string | null;
  agents?: string[] | null;
}
export interface AgentGroupUpdateRequest {
  group_id: string;
  name?: string | null;
  description?: string | null;
  agents?: string[] | null;
}
export interface AgentPatch {
  agent_url?: string | null;
  agent_card?: AgentCard | null;
  call_count?: number | null;
  call_success_count?: number | null;
  like_count?: number | null;
  dislike_count?: number | null;
  agent_status?: AgentStatus | null;
}
export interface AgentTaskRequest {
  task_id: string;
  agent_id: string;
  step_id: string;
  input_data: unknown;
  context?: {
    [k: string]: unknown;
  } | null;
  message?: Message | null;
}
export interface AgentUpdate {
  agent_url?: string | null;
  agent_card: AgentCard | null;
  call_count: number | null;
  call_success_count: number | null;
  like_count: number | null;
  dislike_count: number | null;
  agent_status: AgentStatus | null;
  agent_id: string | null;
}
export interface BaseAgent {
  agent_url?: string | null;
  agent_card?: AgentCard | null;
  call_count?: number | null;
  call_success_count?: number | null;
  like_count?: number | null;
  dislike_count?: number | null;
  agent_status?: AgentStatus | null;
}
/**
 * A BaseTask represents a complete user request and serves as the top-level container.
 * It wraps a Task object and includes session/user metadata for tracking purposes.
 * This is the main task that gets decomposed into MetaTasks for multi-agent processing.
 */
export interface BaseTask {
  task_id: string;
  session_id: string;
  user_name: string;
  task: Task;
  extend_info?: unknown;
}
export interface ChatRequest {
  user_name: string;
  user_input: string;
  session_id?: string | null;
}
export interface DebatationCenterRequest {
  task_id: string;
}
export interface FilterParams {
  /**
   * MongoDB filter conditions
   */
  filters?: {
    [k: string]: unknown;
  } | null;
  /**
   * Field to sort by
   */
  sort_by?: string | null;
  /**
   * Sort order: 1 for ascending, -1 for descending
   */
  sort_order?: number | null;
}
export interface InspectionCenterRequest {
  agent_id?: string | null;
  agent_url: string;
}
/**
 * A MetaTask represents an atomic subtask created from decomposing a larger user request(BaseTask).
 * These are the individual work units assigned to specific agents in the multi-agent system.
 * Each MetaTask contains a Task object with the actual agent communication data.
 */
export interface MetaTask {
  task_id: string;
  parent_task_id: string;
  agent_id?: string;
  task_description?: string | null;
  task?: Task | null;
  execution_order?: number;
  depends_on_tasks?: string[] | null;
  context_from_previous?: {
    [k: string]: unknown;
  } | null;
  extend_info?: unknown;
}
export interface OrchestrationRequest {
  task_id?: string | null;
  room_id?: string | null;
  room_user_message_id?: string | null;
  room_agent_message_id?: string | null;
  room_related_message_id?: string | null;
}
export interface PaginationParams {
  /**
   * Page number (1-indexed)
   */
  page?: number | null;
  /**
   * Number of items per page
   */
  limit?: number | null;
}
export interface RoomAgentMessage {
  room_id: string;
  message_id: string;
  message_created_at?: string;
  message_type?: string;
  user_id?: string | null;
  agent_id?: string | null;
  related_message_id?: string | null;
  message_content: MessageContent;
  extend_info?: unknown;
}
export interface MessageContent {
  message_text?: string | null;
  message_task?: Task | null;
  [k: string]: unknown;
}
export interface RoomCenterAgentMessageRequest {
  room_id?: string | null;
  message_id?: string | null;
  related_message_id?: string | null;
  agent_id?: string | null;
  agent_name?: string | null;
  agent_message_content?: Task | null;
  message_created_at?: string | null;
  extend_info?: {
    [k: string]: unknown;
  } | null;
  message?: RoomAgentMessage | null;
}
export interface RoomCenterMemoryRequest {
  room_id?: string | null;
  memory_id?: string | null;
  memory_content?: string | null;
  memory_created_at?: string | null;
  extend_info?: {
    [k: string]: unknown;
  } | null;
  memory?: RoomMemory | null;
  room_agent_set?: {
    [k: string]: string;
  } | null;
  user_id?: string | null;
}
export interface RoomMemory {
  room_id: string;
  memory_id: string;
  memory_content?: MemoryContent;
  memory_created_at?: string;
  extend_info?: unknown;
}
/**
 * Room conversation memory with structured history.
 * Similar to ChatGPT/Claude conversation context management.
 */
export interface MemoryContent {
  summary?: string | null;
  conversation_history?: ConversationTurn[];
  memory_text?: string | null;
  [k: string]: unknown;
}
/**
 * A single turn in the conversation (ChatGPT/Claude style).
 * Represents either a user message or an agent response.
 */
export interface ConversationTurn {
  role: "user" | "agent";
  content: string;
  agent_id?: string | null;
  agent_name?: string | null;
  user_id?: string | null;
  timestamp?: string;
  [k: string]: unknown;
}
export interface RoomCenterRoomMessageRequest {
  room_id?: string | null;
  limit?: number | null;
  cursor?: string | null;
  message_id?: string | null;
  message_type?: string | null;
  message_content?: string | null;
  message_created_at?: string | null;
  extend_info?: {
    [k: string]: unknown;
  } | null;
  message?: RoomMessage | null;
}
/**
 * Unified room message format for both user and agent messages
 */
export interface RoomMessage {
  room_id: string;
  message_id: string;
  message_created_at?: string;
  message_type: string;
  user_id?: string | null;
  agent_id?: string | null;
  related_message_id?: string | null;
  message_content: MessageContent;
}
export interface RoomCenterRoomSettingRequest {
  room_id?: string | null;
  room_name?: string | null;
  room_owner_id?: string | null;
  room_owner_name?: string | null;
  room_created_at?: string | null;
  extend_info?: {
    [k: string]: unknown;
  } | null;
  room?: Room | null;

  // ── Legacy fields (accepted during rollout, canonical wins) ────────────
  room_agent_set?: {
    [k: string]: string;
  } | null;
  /** @deprecated Use canonical membership_seed_input instead. */
  applied_from_group?: string | null;

  // ── Canonical membership write input (mutually exclusive union) ────────
  membership_seed_input?: "manual" | "saved_group" | "all_current_agents";
  room_agent_ids?: string[];
  seed_group_id?: string;
  seed_all_current_agents?: true;
}
export interface RoomCenterUserMessageRequest {
  room_id?: string | null;
  message_id?: string | null;
  related_message_id?: string | null;
  user_id?: string | null;
  user_name?: string | null;
  user_input?: string | null;
  message_created_at?: string | null;
  extend_info?: {
    [k: string]: unknown;
  } | null;
  message?: RoomUserMessage | null;
  client_request_id?: string | null;
}

type SendMessageBasePayload = {
  message: unknown
  client_request_id: string
}

export type SendMessagePayload = SendMessageBasePayload & (
  | {
      mentioned_agent_ids: [string, ...string[]]
      message_target_mode?: never
      target_group_id?: never
    }
  | {
      message_target_mode: 'room_default' | 'all_agents'
      mentioned_agent_ids?: never
      target_group_id?: never
    }
  | {
      message_target_mode: 'saved_group'
      target_group_id: string
      mentioned_agent_ids?: never
    }
)

export interface RoomUserMessage {
  room_id: string;
  message_id: string;
  message_created_at?: string;
  message_type?: string;
  user_id?: string | null;
  agent_id?: string | null;
  related_message_id?: string | null;
  message_content: MessageContent;
  extend_info?: unknown;
}
export interface TaskCenterRequest {
  task_id?: string | null;
  user_name?: string | null;
  parent_task_id?: string | null;
  session_id?: string | null;
  agent_id?: string | null;
  meta_task?: MetaTask | null;
  base_task?: BaseTask | null;
  task_session?: TaskSession | null;
  task?: Task | null;
  message?: Message | null;
  user_input?: string | null;
  execution_order?: number;
  depends_on_tasks?: string[] | null;
  context_from_previous?: {
    [k: string]: unknown;
  } | null;
}
/**
 * A TaskSession represents a chat conversation between a user and the multi-agent system.
 * It tracks session metadata like creation time, user info, and session description.
 * Multiple BaseTask objects can belong to one TaskSession during a conversation.
 */
export interface TaskSession {
  session_id: string;
  user_name: string;
  session_name: string;
  session_description?: string | null;
  session_created_at?: string;
  session_updated_at?: string;
  extend_info?: unknown;
}
export interface TaskRequest {
  task_id?: string;
  query: string;
  context?: {
    [k: string]: unknown;
  } | null;
  message?: Message | null;
}
export interface UserInput {
  user_name: string;
  user_input: string;
  session_id?: string | null;
}
