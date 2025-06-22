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
export interface SessionInput {
  user_name: string;
  session_id?: string | null;
}
export interface TaskIdInput {
  task_id: string;
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
