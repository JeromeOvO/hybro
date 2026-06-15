/* tslint:disable */
/* eslint-disable */
/**
/* This file was automatically generated from pydantic models by running pydantic2ts.
/* Do not modify it by hand - just update the pydantic models and then re-run the script
*/

export type TaskState = "submitted" | "working" | "input-required" | "completed" | "canceled" | "failed" | "unknown";

export interface AgentAuthentication {
  schemes: string[];
  credentials?: string | null;
}
export interface AgentCapabilities {
  streaming?: boolean;
  pushNotifications?: boolean;
  stateTransitionHistory?: boolean;
}
export interface AgentCard {
  name: string;
  description?: string | null;
  url: string;
  provider?: AgentProvider | null;
  version: string;
  documentationUrl?: string | null;
  capabilities: AgentCapabilities;
  authentication?: AgentAuthentication | null;
  defaultInputModes?: string[];
  defaultOutputModes?: string[];
  skills: AgentSkill[];
}
export interface AgentProvider {
  organization: string;
  url?: string | null;
}
export interface AgentSkill {
  id: string;
  name: string;
  description?: string | null;
  tags?: string[] | null;
  examples?: string[] | null;
  inputModes?: string[] | null;
  outputModes?: string[] | null;
}
export interface Artifact {
  name?: string | null;
  description?: string | null;
  parts: (TextPart | FilePart | DataPart)[];
  metadata?: {
    [k: string]: unknown;
  } | null;
  index?: number;
  append?: boolean | null;
  lastChunk?: boolean | null;
}
export interface TextPart {
  type?: "text";
  text: string;
  metadata?: {
    [k: string]: unknown;
  } | null;
}
export interface FilePart {
  type?: "file";
  file: FileContent;
  metadata?: {
    [k: string]: unknown;
  } | null;
}
export interface FileContent {
  name?: string | null;
  mimeType?: string | null;
  bytes?: string | null;
  uri?: string | null;
}
export interface DataPart {
  type?: "data";
  data: {
    [k: string]: unknown;
  };
  metadata?: {
    [k: string]: unknown;
  } | null;
}
export interface AuthenticationInfo {
  schemes: string[];
  credentials?: string | null;
  [k: string]: unknown;
}
export interface CancelTaskRequest {
  jsonrpc?: "2.0";
  id?: number | string | null;
  method?: "tasks/cancel";
  params: TaskIdParams;
}
export interface TaskIdParams {
  id: string;
  metadata?: {
    [k: string]: unknown;
  } | null;
}
export interface CancelTaskResponse {
  jsonrpc?: "2.0";
  id?: number | string | null;
  result?: Task | null;
  error?: JSONRPCError | null;
}
export interface Task {
  id: string;
  sessionId?: string | null;
  status: TaskStatus;
  artifacts?: Artifact[] | null;
  history?: Message[] | null;
  metadata?: {
    [k: string]: unknown;
  } | null;
}
export interface TaskStatus {
  state: TaskState;
  message?: Message | null;
  timestamp?: string;
}
export interface Message {
  role: "user" | "agent";
  parts: (TextPart | FilePart | DataPart)[];
  metadata?: {
    [k: string]: unknown;
  } | null;
}
export interface JSONRPCError {
  code: number;
  message: string;
  data?: unknown;
}
export interface ContentTypeNotSupportedError {
  code?: number;
  message?: string;
  data?: null;
}
export interface GetTaskPushNotificationRequest {
  jsonrpc?: "2.0";
  id?: number | string | null;
  method?: "tasks/pushNotification/get";
  params: TaskIdParams;
}
export interface GetTaskPushNotificationResponse {
  jsonrpc?: "2.0";
  id?: number | string | null;
  result?: TaskPushNotificationConfig | null;
  error?: JSONRPCError | null;
}
export interface TaskPushNotificationConfig {
  id: string;
  pushNotificationConfig: PushNotificationConfig;
}
export interface PushNotificationConfig {
  url: string;
  token?: string | null;
  authentication?: AuthenticationInfo | null;
}
export interface GetTaskRequest {
  jsonrpc?: "2.0";
  id?: number | string | null;
  method?: "tasks/get";
  params: TaskQueryParams;
}
export interface TaskQueryParams {
  id: string;
  metadata?: {
    [k: string]: unknown;
  } | null;
  historyLength?: number | null;
}
export interface GetTaskResponse {
  jsonrpc?: "2.0";
  id?: number | string | null;
  result?: Task | null;
  error?: JSONRPCError | null;
}
export interface InternalError {
  code?: number;
  message?: string;
  data?: unknown;
}
export interface InvalidParamsError {
  code?: number;
  message?: string;
  data?: unknown;
}
export interface InvalidRequestError {
  code?: number;
  message?: string;
  data?: unknown;
}
export interface JSONParseError {
  code?: number;
  message?: string;
  data?: unknown;
}
export interface JSONRPCMessage {
  jsonrpc?: "2.0";
  id?: number | string | null;
}
export interface JSONRPCRequest {
  jsonrpc?: "2.0";
  id?: number | string | null;
  method: string;
  params?: {
    [k: string]: unknown;
  } | null;
}
export interface JSONRPCResponse {
  jsonrpc?: "2.0";
  id?: number | string | null;
  result?: unknown;
  error?: JSONRPCError | null;
}
export interface MethodNotFoundError {
  code?: number;
  message?: string;
  data?: null;
}
export interface PushNotificationNotSupportedError {
  code?: number;
  message?: string;
  data?: null;
}
export interface SendTaskRequest {
  jsonrpc?: "2.0";
  id?: number | string | null;
  method?: "tasks/send";
  params: TaskSendParams;
}
export interface TaskSendParams {
  id: string;
  sessionId?: string;
  message: Message;
  acceptedOutputModes?: string[] | null;
  pushNotification?: PushNotificationConfig | null;
  historyLength?: number | null;
  metadata?: {
    [k: string]: unknown;
  } | null;
}
export interface SendTaskResponse {
  jsonrpc?: "2.0";
  id?: number | string | null;
  result?: Task | null;
  error?: JSONRPCError | null;
}
export interface SendTaskStreamingRequest {
  jsonrpc?: "2.0";
  id?: number | string | null;
  method?: "tasks/sendSubscribe";
  params: TaskSendParams;
}
export interface SendTaskStreamingResponse {
  jsonrpc?: "2.0";
  id?: number | string | null;
  result?: TaskStatusUpdateEvent | TaskArtifactUpdateEvent | null;
  error?: JSONRPCError | null;
}
export interface TaskStatusUpdateEvent {
  id: string;
  status: TaskStatus;
  final?: boolean;
  metadata?: {
    [k: string]: unknown;
  } | null;
}
export interface TaskArtifactUpdateEvent {
  id: string;
  artifact: Artifact;
  metadata?: {
    [k: string]: unknown;
  } | null;
}
export interface SetTaskPushNotificationRequest {
  jsonrpc?: "2.0";
  id?: number | string | null;
  method?: "tasks/pushNotification/set";
  params: TaskPushNotificationConfig;
}
export interface SetTaskPushNotificationResponse {
  jsonrpc?: "2.0";
  id?: number | string | null;
  result?: TaskPushNotificationConfig | null;
  error?: JSONRPCError | null;
}
export interface TaskNotCancelableError {
  code?: number;
  message?: string;
  data?: null;
}
export interface TaskNotFoundError {
  code?: number;
  message?: string;
  data?: null;
}
export interface TaskResubscriptionRequest {
  jsonrpc?: "2.0";
  id?: number | string | null;
  method?: "tasks/resubscribe";
  params: TaskIdParams;
}
export interface UnsupportedOperationError {
  code?: number;
  message?: string;
  data?: null;
}
