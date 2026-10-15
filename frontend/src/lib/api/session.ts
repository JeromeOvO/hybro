// Owner-side session API: list, read the record, close.
import { getApiUrl } from '../utils'
import { apiGet, apiPost } from '../api-client'
import type { RoomEventRecord } from '../session-record'

export type SessionState =
  | 'completed'
  | 'working'
  | 'waiting_on_owner'
  | 'rejected'
  | 'failed'
  | 'closed'

export interface SessionAgent {
  agent_id: string
  name: string | null
}

export interface SessionReply {
  message_id: string
  text: string
  created_at: string | null
}

export interface SessionSummary {
  session_id: string
  client_label: string
  /** The first message, set when the session opened; null for older sessions. */
  title?: string | null
  agents: SessionAgent[]
  state: SessionState
  paused_agent_id: string | null
  conversation_id: string | null
  opened_at: string
  last_activity_at: string
  closed_at: string | null
}

export interface SessionListResponse {
  sessions: SessionSummary[]
}

export interface SessionRecord {
  session_id: string
  state: SessionState
  latest_replies: SessionReply[]
  paused_agent_id: string | null
  reason: string | null
  agents: SessionAgent[]
  opened_at: string
  last_activity_at: string
  client_label: string
  title?: string | null
  status: 'open' | 'closed'
  conversation_id: string | null
  closed_at: string | null
}

export interface SessionClosed {
  session_id: string
  status: 'open' | 'closed'
}

const sessionUrl = (sessionId: string, suffix = '') =>
  getApiUrl(`sessions/${encodeURIComponent(sessionId)}${suffix}`)

export function listSessions(
  getToken?: () => Promise<string | null>,
  signal?: AbortSignal,
): Promise<SessionListResponse> {
  return apiGet<SessionListResponse>(getApiUrl('sessions'), getToken, signal)
}

export function getSessionRecord(
  sessionId: string,
  getToken?: () => Promise<string | null>,
  signal?: AbortSignal,
): Promise<SessionRecord> {
  return apiGet<SessionRecord>(sessionUrl(sessionId, '/record'), getToken, signal)
}

export function closeSession(
  sessionId: string,
  getToken?: () => Promise<string | null>,
): Promise<SessionClosed> {
  return apiPost<SessionClosed>(sessionUrl(sessionId, '/close'), undefined, getToken)
}

export interface SessionEventsResponse {
  room_seq: number
  events: RoomEventRecord[]
}

// The owner reads the session's conversation container through the room event
// replay, the same durable record the client's state is resolved from.
export function fetchSessionEvents(
  conversationId: string,
  getToken?: () => Promise<string | null>,
  signal?: AbortSignal,
): Promise<SessionEventsResponse> {
  const url = getApiUrl(`sse/room/${encodeURIComponent(conversationId)}/events?after=0&limit=1000`)
  return apiGet<SessionEventsResponse>(url, getToken, signal)
}
