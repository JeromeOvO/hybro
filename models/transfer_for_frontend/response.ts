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
  context_id: string;
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
  meta_task_ids?: string[] | null;
  agent_id?: string | null;
  success: boolean;
  error?: string | null;
  status_code?: number;
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
