/* tslint:disable */
/**
/* This file was automatically generated from pydantic models by running pydantic2ts.
/* Do not modify it by hand - just update the pydantic models and then re-run the script
*/

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
}
/**
 * Room conversation memory with structured history.
 * Similar to ChatGPT/Claude conversation context management.
 */
export interface MemoryContent {
  summary?: string | null;
  conversation_history?: ConversationTurn[];
  memory_text?: string | null;
}
export interface RoomMemory {
  room_id: string;
  memory_id: string;
  memory_content?: MemoryContent;
  memory_created_at?: string;
  extend_info?: unknown;
}
