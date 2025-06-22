/* tslint:disable */
/* eslint-disable */
/**
/* This file was automatically generated from pydantic models by running pydantic2ts.
/* Do not modify it by hand - just update the pydantic models and then re-run the script
*/

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

/**
 * Model for a subtask created from AI decomposition.
 *
 * Contains a Task from common/types.py and adds fields
 * for task decomposition relationships.
 */
export interface ChildTask {
  task_id?: string;
  agent_id?: string;
  description?: string | null;
  task: Task;
  parent_id: string;
  order?: number;
  priority?: number;
  dependencies?: number[];
  subtasks?: ChildTask[];
  depth?: number;
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
 * Represents a text segment within parts.
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
/**
 * Enhanced Task model for MongoDB storage.
 *
 * Contains a Task from common/types.py and adds task_id and subtasks fields
 * for storing AI-decomposed subtasks.
 */
export interface RootTask {
  task_id?: string;
  task?: Task | null;
  description?: string | null;
  subtasks?: ChildTask[];
}
export interface TaskSession {
  user_name?: string;
  session_id: string;
  session_name: string;
  session_description?: string | null;
  session_created_at?: string;
  session_updated_at?: string;
  rootTasks?: string[];
}
