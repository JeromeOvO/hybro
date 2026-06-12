/**
 * Room-related types.
 *
 * A2A protocol types (Task, Message, Part, etc.) are imported from the
 * canonical @a2a-js/sdk package.  Only room-specific types are defined here.
 */

import type {
  Message as A2AMessage,
  Task,
  TaskStatus,
  Part,
  TextPart,
  FilePart,
  DataPart,
  FileWithBytes,
  FileWithUri,
  TaskState,
} from '@a2a-js/sdk'

// Re-export SDK types so existing consumers of this module still work
export type {
  Task,
  TaskStatus,
  Part,
  TextPart,
  FilePart,
  DataPart,
  FileWithBytes,
  FileWithUri,
  TaskState,
}

// Re-export the SDK Message under the A2AMessage alias used by this module
export type { A2AMessage }

/**
 * Identifies the sender of the message.
 * (Not exported from @a2a-js/sdk as a standalone type.)
 */
export type Role = "agent" | "user";

// ── Room-specific message (NOT the A2A protocol Message) ─────────────────

export interface Message {
  room_id: string;
  message_id: string;
  message_created_at?: string;
}

export interface MessageContent {
  message_text?: string | null;
  message_task?: Task | null;
}

// ── Room entities ────────────────────────────────────────────────────────

export type { Room } from './response'
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
