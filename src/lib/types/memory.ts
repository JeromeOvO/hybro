/* tslint:disable */
/* eslint-disable */
/**
/* This file was automatically generated from pydantic models by running pydantic2ts.
/* Do not modify it by hand - just update the pydantic models and then re-run the script
*/

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
}
