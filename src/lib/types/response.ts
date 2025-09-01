/* tslint:disable */
/* eslint-disable */
/**
/* This file was automatically generated from pydantic models by running pydantic2ts.
/* Do not modify it by hand - just update the pydantic models and then re-run the script
*/

export type SecurityScheme =
  | APIKeySecurityScheme
  | HTTPAuthSecurityScheme
  | OAuth2SecurityScheme
  | OpenIdConnectSecurityScheme;
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
export type SendMessageResponse = JSONRPCErrorResponse | SendMessageSuccessResponse;
export type SendStreamingMessageResponse = JSONRPCErrorResponse | SendStreamingMessageSuccessResponse;

export interface Agent {
  agent_id: string;
  agent_card: AgentCard;
  agent_status?: AgentStatus;
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
  skills: AgentSkill[];
  supportsAuthenticatedExtendedCard?: boolean | null;
  url: string;
  version: string;
}
/**
 * Declares a combination of a target URL and a transport protocol for interacting with the agent.
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
 * Represents a distinct capability or function that an agent can perform.
 */
export interface AgentSkill {
  description: string;
  examples?: string[] | null;
  id: string;
  inputModes?: string[] | null;
  name: string;
  outputModes?: string[] | null;
  tags: string[];
  [k: string]: unknown;
}
export interface AgentCenterResponse {
  agent_url?: string | null;
  agent_id?: string | null;
  agent_card?: AgentCard | null;
  agent?: Agent | null;
  agents?: Agent[] | null;
  success: boolean;
  error?: string | null;
  status_code?: number;
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
 * Represents a text segment within a message or artifact.
 */
export interface TextPart {
  kind?: "text";
  metadata?: {
    [k: string]: unknown;
  } | null;
  text: string;
  [k: string]: unknown;
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
export interface OrchestrationCenterResponse {
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
  extend_info?: unknown;
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
/**
 * Represents a JSON-RPC 2.0 Error Response object.
 */
export interface JSONRPCErrorResponse {
  error:
    | JSONRPCError
    | JSONParseError
    | InvalidRequestError
    | MethodNotFoundError
    | InvalidParamsError
    | InternalError
    | TaskNotFoundError
    | TaskNotCancelableError
    | PushNotificationNotSupportedError
    | UnsupportedOperationError
    | ContentTypeNotSupportedError
    | InvalidAgentResponseError;
  id?: string | number | null;
  jsonrpc?: "2.0";
  [k: string]: unknown;
}
/**
 * Represents a JSON-RPC 2.0 Error object, included in an error response.
 */
export interface JSONRPCError {
  code: number;
  data?: unknown;
  message: string;
  [k: string]: unknown;
}
/**
 * An error indicating that the server received invalid JSON.
 */
export interface JSONParseError {
  code?: -32700;
  data?: unknown;
  message?: string | null;
  [k: string]: unknown;
}
/**
 * An error indicating that the JSON sent is not a valid Request object.
 */
export interface InvalidRequestError {
  code?: -32600;
  data?: unknown;
  message?: string | null;
  [k: string]: unknown;
}
/**
 * An error indicating that the requested method does not exist or is not available.
 */
export interface MethodNotFoundError {
  code?: -32601;
  data?: unknown;
  message?: string | null;
  [k: string]: unknown;
}
/**
 * An error indicating that the method parameters are invalid.
 */
export interface InvalidParamsError {
  code?: -32602;
  data?: unknown;
  message?: string | null;
  [k: string]: unknown;
}
/**
 * An error indicating an internal error on the server.
 */
export interface InternalError {
  code?: -32603;
  data?: unknown;
  message?: string | null;
  [k: string]: unknown;
}
/**
 * An A2A-specific error indicating that the requested task ID was not found.
 */
export interface TaskNotFoundError {
  code?: -32001;
  data?: unknown;
  message?: string | null;
  [k: string]: unknown;
}
/**
 * An A2A-specific error indicating that the task is in a state where it cannot be canceled.
 */
export interface TaskNotCancelableError {
  code?: -32002;
  data?: unknown;
  message?: string | null;
  [k: string]: unknown;
}
/**
 * An A2A-specific error indicating that the agent does not support push notifications.
 */
export interface PushNotificationNotSupportedError {
  code?: -32003;
  data?: unknown;
  message?: string | null;
  [k: string]: unknown;
}
/**
 * An A2A-specific error indicating that the requested operation is not supported by the agent.
 */
export interface UnsupportedOperationError {
  code?: -32004;
  data?: unknown;
  message?: string | null;
  [k: string]: unknown;
}
/**
 * An A2A-specific error indicating an incompatibility between the requested
 * content types and the agent's capabilities.
 */
export interface ContentTypeNotSupportedError {
  code?: -32005;
  data?: unknown;
  message?: string | null;
  [k: string]: unknown;
}
/**
 * An A2A-specific error indicating that the agent returned a response that
 * does not conform to the specification for the current method.
 */
export interface InvalidAgentResponseError {
  code?: -32006;
  data?: unknown;
  message?: string | null;
  [k: string]: unknown;
}
/**
 * Represents a successful JSON-RPC response for the `message/send` method.
 */
export interface SendMessageSuccessResponse {
  id?: string | number | null;
  jsonrpc?: "2.0";
  result: Task | Message;
  [k: string]: unknown;
}
/**
 * Represents a successful JSON-RPC response for the `message/stream` method.
 * The server may send multiple response objects for a single request.
 */
export interface SendStreamingMessageSuccessResponse {
  id?: string | number | null;
  jsonrpc?: "2.0";
  result: Task | Message | TaskStatusUpdateEvent | TaskArtifactUpdateEvent;
  [k: string]: unknown;
}
/**
 * An event sent by the agent to notify the client of a change in a task's status.
 * This is typically used in streaming or subscription models.
 */
export interface TaskStatusUpdateEvent {
  contextId: string;
  final: boolean;
  kind?: "status-update";
  metadata?: {
    [k: string]: unknown;
  } | null;
  status: TaskStatus;
  taskId: string;
  [k: string]: unknown;
}
/**
 * An event sent by the agent to notify the client that an artifact has been
 * generated or updated. This is typically used in streaming models.
 */
export interface TaskArtifactUpdateEvent {
  append?: boolean | null;
  artifact: Artifact;
  contextId: string;
  kind?: "artifact-update";
  lastChunk?: boolean | null;
  metadata?: {
    [k: string]: unknown;
  } | null;
  taskId: string;
  [k: string]: unknown;
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
  message_created_at?: string;
  message_type: string;
  user_id?: string | null;
  agent_id?: string | null;
  related_message_id?: string | null;
  message_content: MessageContent;
}
export interface RoomCenterRoomSettingResponse {
  room_id?: string | null;
  room_agent_set?: string[] | null;
  room?: Room | null;
  room_list?: Room[] | null;
  success: boolean;
  error?: string | null;
  status_code?: number;
}
export interface RoomCenterUserMessageResponse {
  room_id?: string | null;
  message_id?: string | null;
  user_id?: string | null;
  user_name?: string | null;
  message?: RoomUserMessage | null;
  message_list?: RoomUserMessage[] | null;
  success: boolean;
  error?: string | null;
  status_code?: number;
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
