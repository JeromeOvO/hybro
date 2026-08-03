/**
 * Response types for the backend API.
 *
 * A2A protocol types (AgentCard, Task, Message, Part, etc.) are imported from
 * the canonical @a2a-js/sdk package.  Only backend-specific response wrappers
 * and orchestration types are defined here.
 */

// ── Re-export A2A protocol types from the SDK ────────────────────────────
// These were previously duplicated via pydantic2ts code-gen.
export type {
  // Core domain
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

  // Agent card & related
  AgentCard,
  AgentCapabilities,
  AgentExtension,
  AgentInterface,
  AgentProvider,
  AgentSkill,
  AgentCardSignature,

  // Security schemes
  SecurityScheme,
  APIKeySecurityScheme,
  HTTPAuthSecurityScheme,
  OAuth2SecurityScheme,
  OpenIdConnectSecurityScheme,
  MutualTLSSecurityScheme,

  // OAuth flows
  OAuthFlows,
  AuthorizationCodeOAuthFlow,
  ClientCredentialsOAuthFlow,
  ImplicitOAuthFlow,
  PasswordOAuthFlow,

  // JSON-RPC error types
  JSONRPCErrorResponse,
  JSONRPCError,
  JSONParseError,
  InvalidRequestError,
  MethodNotFoundError,
  InvalidParamsError,
  InternalError,
  TaskNotFoundError,
  TaskNotCancelableError,
  PushNotificationNotSupportedError,
  UnsupportedOperationError,
  ContentTypeNotSupportedError,
  InvalidAgentResponseError,

  // Streaming event types
  TaskStatusUpdateEvent,
  TaskArtifactUpdateEvent,

  // Response union types
  SendMessageResponse,
  SendStreamingMessageResponse,
  SendMessageSuccessResponse,
  SendStreamingMessageSuccessResponse,
} from '@a2a-js/sdk'

import type {
  AgentCard,
  Task,
  SendMessageResponse,
  SendStreamingMessageResponse,
} from '@a2a-js/sdk'

// ── Types NOT in SDK: kept here as they originate from the backend ────────

/**
 * The location of the API key.
 * (Not exported from @a2a-js/sdk as a standalone type.)
 */
export type In = "cookie" | "header" | "query";

/**
 * Identifies the sender of the message. `user` for the client, `agent` for the service.
 * (Not exported from @a2a-js/sdk as a standalone type.)
 */
export type Role = "agent" | "user";

export type AgentStatus = "active" | "inactive" | "deleted";

export interface Agent {
  agent_id: string;
  provider_id: string;
  agent_card: AgentCard;
  // public_url?: string | null;
  agent_status?: AgentStatus;
  call_count?: number;
  call_success_count?: number;
  like_count?: number;
  dislike_count?: number;
  /** Maximum requests per user per hour (null = unlimited) */
  rate_limit_per_user_per_hour?: number | null;
  /** Maximum requests for entire system per hour (null = unlimited) */
  rate_limit_system_per_hour?: number | null;
  /** Visibility: true = public (everyone can see/use), false = private (owner only) */
  is_public?: boolean;
  /** Agent source: "cloud" for cloud-hosted, "hub" for local hub agents */
  source?: "cloud" | "hub";
  /** Hub ID if this is a hub-sourced agent */
  hub_id?: string;
  /** User ID of the hub owner */
  hub_owner_id?: string;
  /** Whether the hub providing this agent is currently online */
  is_hub_online?: boolean;
  /** Agent ID on the local hub (maps to the hub's internal registry) */
  local_agent_id?: string;
  /** Display name resolved from provider_id via Clerk (when agent_card.provider is absent) */
  provider_name?: string | null;
}

export interface AgentCenterResponse {
  agent_url?: string | null;
  agent_id?: string | null;
  provider_id?: string | null;
  agent_card?: AgentCard | null;
  agent?: Agent | null;
  agents?: Agent[] | null;
  success: boolean;
  error?: string | null;
  status_code?: number;
}
export interface APIKeyItemResponse {
  key_id: string;
  name: string;
  created_at: string;
  last_used_at?: string | null;
  is_active: boolean;
  usage_count: number;
}
export interface APIKeyListResponse {
  keys: APIKeyItemResponse[];
  count: number;
}
export interface APIKeyCreateResponse {
  key_id: string;
  name: string;
  created_at: string;
  api_key: string;
}
export interface APIKeyOperationResponse {
  success: boolean;
  message: string;
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
/**
 * A ChatContext represents a chat context between a user and the multi-agent system.
 * It tracks session metadata like creation time, user info, and context content.
 * Multiple ChatContext objects can belong to one TaskSession during a conversation.
 */
export interface ChatContext {
  memory_id: string;
  user_name: string;
  session_id: string;
  context_data?: ContextData | null;
  created_at?: string;
  updated_at?: string;
  extend_info?: unknown;
}
export interface ContextData {
  context_content?: string | null;
  [k: string]: unknown;
}
export interface ChatMemoryResponse {
  user_name: string;
  chat_context?: ChatContext | null;
  success: boolean;
  error?: string | null;
  status_code?: number;
}
export interface ChatResponse {
  user_name: string;
  user_input: string;
  session_id?: string | null;
  task_id?: string | null;
  success: boolean;
  error?: string | null;
  status_code?: number;
}
export interface DebatationCenterResponse {
  task_id: string;
  agent_id: string;
  step_id: string;
  result?: unknown;
  error?: string | null;
  status_code?: number;
}
export interface InsepectionCenterConnectionValidationResponse {
  agent_url: string;
  agent_card?: AgentCard | null;
  is_valid: boolean;
  result?: string[] | null;
  status_code?: number;
}
export interface InspectionCenterResponse {
  agent_url: string;
  agent_card?: AgentCard | null;
  result: string[];
  status_code?: number;
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
export interface OrchestrationResponse {
  task_id?: string | null;
  room_id?: string | null;
  meta_task_ids?: string[] | null;
  room_agent_message_list?: RoomAgentMessage[] | null;
  agent_id?: string | null;
  success: boolean;
  error?: string | null;
  status_code?: number;
}
export interface RoomAgentMessage {
  room_id: string;
  message_id: string;
  client_request_id?: string | null;
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
export interface Room {
  room_id?: string;
  room_name: string;
  room_owner_id: string;
  room_owner_name: string;
  room_agent_set?: {
    [k: string]: string;
  };
  room_created_at?: string;
  /** @deprecated Use source_group_id from canonical provenance fields. */
  applied_from_group?: string | null;
  extend_info?: unknown;

  // ── Canonical provenance fields ────────────────────────────────────────
  membership_origin?: "manual" | "saved_group" | "all_current_agents";
  membership_origin_status?: "seeded_never_edited" | "seeded_edited" | "manual";
  source_group_id?: string | null;
  source_group_name?: string | null;
}
export interface RoomCenterAgentMessageResponse {
  room_id?: string | null;
  message_id?: string | null;
  agent_id?: string | null;
  agent_name?: string | null;
  message?: RoomAgentMessage | null;
  a2a_response?: SendMessageResponse | SendStreamingMessageResponse | null;
  message_list?: RoomAgentMessage[] | null;
  success: boolean;
  error?: string | null;
  status_code?: number;
}
export interface RoomCenterMemoryResponse {
  room_id?: string | null;
  memory_id?: string | null;
  memory?: RoomMemory | null;
  success: boolean;
  error?: string | null;
  status_code?: number;
}
export interface RoomMemory {
  room_id: string;
  memory_id: string;
  memory_content: MemoryContent;
  memory_created_at?: string;
  extend_info?: unknown;
}
export interface MemoryContent {
  memory_text: string;
  [k: string]: unknown;
}
export interface RoomCenterRoomMessageResponse {
  room_id?: string | null;
  message_list?: RoomMessage[] | null;
  has_more?: boolean | null;
  next_cursor?: string | null;
  success: boolean;
  error?: string | null;
  status_code?: number;
}
/**
 * Unified room message format for both user and agent messages
 */
export interface RoomMessage {
  room_id: string;
  message_id: string;
  client_request_id?: string | null;
  message_created_at?: string;
  message_type: string;
  user_id?: string | null;
  agent_id?: string | null;
  related_message_id?: string | null;
  message_content: MessageContent;
  // Step tracking from task decomposition (1-indexed) - for agent messages
  step_number?: number | null;
  total_steps?: number | null;
  // Task timestamp for staleness detection (only set for agent messages with tasks)
  task_updated_at?: string | null;
  // Task description being processed (only set for agent messages with tasks)
  task_content?: string | null;
  /** Persisted quote snapshot id on user messages (QUOTE_REPLY). */
  quote_id?: string | null;
  extend_info?: unknown;
}
export interface RoomCenterRoomSettingResponse {
  room_id?: string | null;
  room_agent_set?: string[] | null;
  resolved_agents?: RoomAgentRefWire[] | null;
  room_default_status?: "ok" | "degraded" | "empty" | "all_unavailable" | null;
  active_runs?: ActiveRunRefWire[] | null;
  room?: Room | null;
  room_list?: Room[] | null;
  success: boolean;
  error?: string | null;
  status_code?: number;
}
export interface ActiveRunRefWire {
  state: string;
  trigger_message_id?: string | null;
  agent_id?: string | null;
  updated_at?: string | null;
}
export interface RoomCenterActiveRunsResponse {
  room_id?: string | null;
  active_runs?: ActiveRunRefWire[] | null;
  turn_completion_kind?: string | null;
  success: boolean;
  error?: string | null;
  status_code?: number;
}
export interface RoomAgentRefWire {
  id: string;
  name?: string | null;
  availability: "available" | "inaccessible" | "inactive" | "deleted";
}
export interface ScopeResolutionErrorWire {
  code: "invalid_target" | "group_not_usable" | "unauthorized_mention" | "empty_scope";
  message: string;
}
export interface RoomCenterUserMessageResponse {
  room_id?: string | null;
  message_id?: string | null;
  dispatch_root_message_id?: string | null;
  user_id?: string | null;
  user_name?: string | null;
  message?: RoomUserMessage | null;
  message_list?: RoomUserMessage[] | null;
  scope_resolution_error?: ScopeResolutionErrorWire | null;
  success: boolean;
  error?: string | null;
  status_code?: number;
}
export interface RoomUserMessage {
  room_id: string;
  message_id: string;
  client_request_id?: string | null;
  message_created_at?: string;
  message_type?: string;
  user_id?: string | null;
  agent_id?: string | null;
  related_message_id?: string | null;
  /** Persisted quote snapshot id (QUOTE_REPLY). */
  quote_id?: string | null;
  message_content: MessageContent;
  extend_info?: unknown;
}
export interface Step {
  step_id: string;
  description: string;
  agent_id?: string | null;
  status?: string;
  input_data?: unknown;
  output_data?: unknown;
  priority?: number;
  dependencies?: string[];
  error?: string | null;
  result?: unknown;
  agent_name?: string | null;
  is_remote_agent?: boolean | null;
}
export interface TaskCenterResponse {
  task_id?: string | null;
  user_name?: string | null;
  parent_task_id?: string | null;
  session_id?: string | null;
  task?: Task | null;
  meta_task?: MetaTask | null;
  base_task?: BaseTask | null;
  task_session?: TaskSession | null;
  meta_tasks?: MetaTask[] | null;
  base_tasks?: BaseTask[] | null;
  task_sessions?: TaskSession[] | null;
  success: boolean;
  error?: string | null;
  status_code?: number;
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
export interface TaskResponse {
  task_id: string;
  status?: string;
  steps?: Step[];
  result?: unknown;
  error?: string | null;
}
export interface UserResponse {
  session_id: string;
  task_id: string;
  result: string;
}
