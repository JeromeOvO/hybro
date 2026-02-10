/* tslint:disable */
/**
/* This file was automatically generated from pydantic models by running pydantic2ts.
/* Do not modify it by hand - just update the pydantic models and then re-run the script
*/

export type SecurityScheme =
  | APIKeySecurityScheme
  | HTTPAuthSecurityScheme
  | OAuth2SecurityScheme
  | OpenIdConnectSecurityScheme
  | MutualTLSSecurityScheme;
/**
 * The location of the API key.
 */
export type In = "cookie" | "header" | "query";
export type AgentStatus = "active" | "inactive" | "deleted";
export type Part = TextPart | FilePart | DataPart;
/**
 * Identifies the sender of the message. `user` for the client, `agent` for the service.
 */
export type Role = "agent" | "user";
/**
 * Defines the lifecycle states of a Task.
 */
export type TaskState =
  | "submitted"
  | "working"
  | "input-required"
  | "completed"
  | "canceled"
  | "failed"
  | "rejected"
  | "auth-required"
  | "unknown";

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
/**
 * The AgentCard is a self-describing manifest for an agent. It provides essential
 * metadata including the agent's identity, capabilities, skills, supported
 * communication methods, and security requirements.
 */
export interface AgentCard {
  additionalInterfaces?: AgentInterface[] | null;
  capabilities: AgentCapabilities;
  defaultInputModes: string[];
  defaultOutputModes: string[];
  description: string;
  documentationUrl?: string | null;
  iconUrl?: string | null;
  name: string;
  preferredTransport?: string | null;
  protocolVersion?: string | null;
  provider?: AgentProvider | null;
  security?:
    | {
        [k: string]: string[];
      }[]
    | null;
  securitySchemes?: {
    [k: string]: SecurityScheme;
  } | null;
  signatures?: AgentCardSignature[] | null;
  skills: AgentSkill[];
  supportsAuthenticatedExtendedCard?: boolean | null;
  url: string;
  version: string;
}
/**
 * Declares a combination of a target URL and a transport protocol for interacting with the agent.
 * This allows agents to expose the same functionality over multiple transport mechanisms.
 */
export interface AgentInterface {
  transport: string;
  url: string;
  [k: string]: unknown;
}
/**
 * Defines optional capabilities supported by an agent.
 */
export interface AgentCapabilities {
  extensions?: AgentExtension[] | null;
  pushNotifications?: boolean | null;
  stateTransitionHistory?: boolean | null;
  streaming?: boolean | null;
  [k: string]: unknown;
}
/**
 * A declaration of a protocol extension supported by an Agent.
 */
export interface AgentExtension {
  description?: string | null;
  params?: {
    [k: string]: unknown;
  } | null;
  required?: boolean | null;
  uri: string;
  [k: string]: unknown;
}
/**
 * Represents the service provider of an agent.
 */
export interface AgentProvider {
  organization: string;
  url: string;
  [k: string]: unknown;
}
/**
 * Defines a security scheme using an API key.
 */
export interface APIKeySecurityScheme {
  description?: string | null;
  in: In;
  name: string;
  type?: "apiKey";
  [k: string]: unknown;
}
/**
 * Defines a security scheme using HTTP authentication.
 */
export interface HTTPAuthSecurityScheme {
  bearerFormat?: string | null;
  description?: string | null;
  scheme: string;
  type?: "http";
  [k: string]: unknown;
}
/**
 * Defines a security scheme using OAuth 2.0.
 */
export interface OAuth2SecurityScheme {
  description?: string | null;
  flows: OAuthFlows;
  oauth2MetadataUrl?: string | null;
  type?: "oauth2";
  [k: string]: unknown;
}
/**
 * Defines the configuration for the supported OAuth 2.0 flows.
 */
export interface OAuthFlows {
  authorizationCode?: AuthorizationCodeOAuthFlow | null;
  clientCredentials?: ClientCredentialsOAuthFlow | null;
  implicit?: ImplicitOAuthFlow | null;
  password?: PasswordOAuthFlow | null;
  [k: string]: unknown;
}
/**
 * Defines configuration details for the OAuth 2.0 Authorization Code flow.
 */
export interface AuthorizationCodeOAuthFlow {
  authorizationUrl: string;
  refreshUrl?: string | null;
  scopes: {
    [k: string]: string;
  };
  tokenUrl: string;
  [k: string]: unknown;
}
/**
 * Defines configuration details for the OAuth 2.0 Client Credentials flow.
 */
export interface ClientCredentialsOAuthFlow {
  refreshUrl?: string | null;
  scopes: {
    [k: string]: string;
  };
  tokenUrl: string;
  [k: string]: unknown;
}
/**
 * Defines configuration details for the OAuth 2.0 Implicit flow.
 */
export interface ImplicitOAuthFlow {
  authorizationUrl: string;
  refreshUrl?: string | null;
  scopes: {
    [k: string]: string;
  };
  [k: string]: unknown;
}
/**
 * Defines configuration details for the OAuth 2.0 Resource Owner Password flow.
 */
export interface PasswordOAuthFlow {
  refreshUrl?: string | null;
  scopes: {
    [k: string]: string;
  };
  tokenUrl: string;
  [k: string]: unknown;
}
/**
 * Defines a security scheme using OpenID Connect.
 */
export interface OpenIdConnectSecurityScheme {
  description?: string | null;
  openIdConnectUrl: string;
  type?: "openIdConnect";
  [k: string]: unknown;
}
/**
 * Defines a security scheme using mTLS authentication.
 */
export interface MutualTLSSecurityScheme {
  description?: string | null;
  type?: "mutualTLS";
  [k: string]: unknown;
}
/**
 * AgentCardSignature represents a JWS signature of an AgentCard.
 * This follows the JSON format of an RFC 7515 JSON Web Signature (JWS).
 */
export interface AgentCardSignature {
  header?: {
    [k: string]: unknown;
  } | null;
  protected: string;
  signature: string;
  [k: string]: unknown;
}
/**
 * Represents a distinct capability or function that an agent can perform.
 */
export interface AgentSkill {
  description: string;
  examples?: string[] | null;
  id: string;
  inputModes?: string[] | null;
  name: string;
  outputModes?: string[] | null;
  security?:
    | {
        [k: string]: string[];
      }[]
    | null;
  tags: string[];
  [k: string]: unknown;
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
/**
 * Represents a single message in the conversation between a user and an agent.
 */
export interface Message {
  contextId?: string | null;
  extensions?: string[] | null;
  kind?: "message";
  messageId: string;
  metadata?: {
    [k: string]: unknown;
  } | null;
  parts: Part[];
  referenceTaskIds?: string[] | null;
  role: Role;
  taskId?: string | null;
}
/**
 * Represents a text segment within a message or artifact.
 */
export interface TextPart {
  kind?: "text";
  metadata?: {
    [k: string]: unknown;
  } | null;
  text: string;
}
/**
 * Represents a file segment within a message or artifact. The file content can be
 * provided either directly as bytes or as a URI.
 */
export interface FilePart {
  file: FileWithBytes | FileWithUri;
  kind?: "file";
  metadata?: {
    [k: string]: unknown;
  } | null;
  [k: string]: unknown;
}
/**
 * Represents a file with its content provided directly as a base64-encoded string.
 */
export interface FileWithBytes {
  bytes: string;
  mimeType?: string | null;
  name?: string | null;
  [k: string]: unknown;
}
/**
 * Represents a file with its content located at a specific URI.
 */
export interface FileWithUri {
  mimeType?: string | null;
  name?: string | null;
  uri: string;
  [k: string]: unknown;
}
/**
 * Represents a structured data segment (e.g., JSON) within a message or artifact.
 */
export interface DataPart {
  data: {
    [k: string]: unknown;
  };
  kind?: "data";
  metadata?: {
    [k: string]: unknown;
  } | null;
  [k: string]: unknown;
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
/**
 * Represents a single, stateful operation or conversation between a client and an agent.
 */
export interface Task {
  artifacts?: Artifact[] | null;
  contextId: string;
  history?: Message[] | null;
  id: string;
  kind?: "task";
  metadata?: {
    [k: string]: unknown;
  } | null;
  status: TaskStatus;
}
/**
 * Represents a file, data structure, or other resource generated by an agent during a task.
 */
export interface Artifact {
  artifactId: string;
  description?: string | null;
  extensions?: string[] | null;
  metadata?: {
    [k: string]: unknown;
  } | null;
  name?: string | null;
  parts: Part[];
  [k: string]: unknown;
}
/**
 * Represents the status of a task at a specific point in time.
 */
export interface TaskStatus {
  message?: Message | null;
  state: TaskState;
  timestamp?: string | null;
  [k: string]: unknown;
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
export interface ChatMemoryRequest {
  user_name?: string | null;
  session_id?: string | null;
  user_input?: string | null;
  agent_response?: string | null;
  chat_context?: ChatContext | null;
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
export interface Room {
  room_id?: string;
  room_name: string;
  room_owner_id: string;
  room_owner_name: string;
  room_agent_set?: {
    [k: string]: string;
  };
  room_created_at?: string;
  applied_from_group?: string | null;
  extend_info?: unknown;
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
  room_agent_set?: {
    [k: string]: string;
  } | null;
  room_created_at?: string | null;
  applied_from_group?: string | null;
  extend_info?: {
    [k: string]: unknown;
  } | null;
  room?: Room | null;
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
}
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
