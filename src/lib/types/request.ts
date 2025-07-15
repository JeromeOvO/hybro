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
 * The location of the API key. Valid values are "query", "header", or "cookie".
 */
export type In = "cookie" | "header" | "query";
export type AgentStatus = "active" | "inactive" | "deleted";
export type Part = TextPart | FilePart | DataPart;
/**
 * Message sender's role
 */
export type Role = "agent" | "user";
/**
 * Represents the possible states of a Task.
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
 * An AgentCard conveys key information:
 * - Overall details (version, name, description, uses)
 * - Skills: A set of capabilities the agent can perform
 * - Default modalities/content types supported by the agent.
 * - Authentication requirements
 */
export interface AgentCard {
  capabilities: AgentCapabilities;
  defaultInputModes: string[];
  defaultOutputModes: string[];
  description: string;
  documentationUrl?: string | null;
  iconUrl?: string | null;
  name: string;
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
 * A declaration of an extension supported by an Agent.
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
 * API Key security scheme.
 */
export interface APIKeySecurityScheme {
  description?: string | null;
  in: In;
  name: string;
  type?: "apiKey";
  [k: string]: unknown;
}
/**
 * HTTP Authentication security scheme.
 */
export interface HTTPAuthSecurityScheme {
  bearerFormat?: string | null;
  description?: string | null;
  scheme: string;
  type?: "http";
  [k: string]: unknown;
}
/**
 * OAuth2.0 security scheme configuration.
 */
export interface OAuth2SecurityScheme {
  description?: string | null;
  flows: OAuthFlows;
  type?: "oauth2";
  [k: string]: unknown;
}
/**
 * Allows configuration of the supported OAuth Flows
 */
export interface OAuthFlows {
  authorizationCode?: AuthorizationCodeOAuthFlow | null;
  clientCredentials?: ClientCredentialsOAuthFlow | null;
  implicit?: ImplicitOAuthFlow | null;
  password?: PasswordOAuthFlow | null;
  [k: string]: unknown;
}
/**
 * Configuration details for a supported OAuth Flow
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
 * Configuration details for a supported OAuth Flow
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
 * Configuration details for a supported OAuth Flow
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
 * Configuration details for a supported OAuth Flow
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
 * OpenID Connect security scheme configuration.
 */
export interface OpenIdConnectSecurityScheme {
  description?: string | null;
  openIdConnectUrl: string;
  type?: "openIdConnect";
  [k: string]: unknown;
}
/**
 * Represents a unit of capability that an agent can perform.
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
export interface AgentCenterRequest {
  agent_url?: string | null;
  agent_id?: string | null;
  agent_card?: AgentCard | null;
  call_increment?: number | null;
  call_success_increment?: number | null;
  like_increment?: number | null;
  dislike_increment?: number | null;
  query_text?: string | null;
  agent?: Agent | null;
  agent_count?: number | null;
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
 * Represents a single message exchanged between user and agent.
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
 * Represents a text segment within parts.
 */
export interface TextPart {
  kind?: "text";
  metadata?: {
    [k: string]: unknown;
  } | null;
  text: string;
}
/**
 * Represents a File segment within parts.
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
 * Define the variant where 'bytes' is present and 'uri' is absent
 */
export interface FileWithBytes {
  bytes: string;
  mimeType?: string | null;
  name?: string | null;
  [k: string]: unknown;
}
/**
 * Define the variant where 'uri' is present and 'bytes' is absent
 */
export interface FileWithUri {
  mimeType?: string | null;
  name?: string | null;
  uri: string;
  [k: string]: unknown;
}
/**
 * Represents a structured data segment within a message part.
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
 * A base task model for one request from user
 */
export interface BaseTask {
  task_id: string;
  session_id: string;
  user_name: string;
  task: Task;
  extend_info?: unknown;
}
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
 * Represents an artifact generated for a task.
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
 * TaskState and accompanying message.
 */
export interface TaskStatus {
  message?: Message | null;
  state: TaskState;
  timestamp?: string | null;
  [k: string]: unknown;
}
export interface ChatRequest {
  user_name: string;
  user_input: string;
  session_id?: string | null;
}
export interface DebatationCenterRequest {
  task_id: string;
}
export interface InspectionCenterRequest {
  agent_id?: string | null;
  agent_url: string;
}
/**
 * A meta task model represents the smallest atomic tasks in the system, usually subtasks from decomposition. It is designed for convenient a2a agent communication.
 */
export interface MetaTask {
  task_id: string;
  parent_task_id: string;
  agent_id?: string;
  task_description?: string | null;
  task?: Task | null;
  execution_order?: number;
  extend_info?: unknown;
}
export interface OrchestrationCenterRequest {
  task_id: string;
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
}
/**
 * Model for a task session. One meta session for one chat session
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
