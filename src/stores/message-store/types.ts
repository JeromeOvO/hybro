import type { TaskState } from '@/lib/types/sse'

/** Which pipeline last wrote this entity. */
export type MessageSource = 'db' | 'sse' | 'optimistic'

/**
 * Display type — resolved once at write time.
 * Determines which React component renders this message.
 */
export type DisplayType = 'user-bubble' | 'agent-bubble' | 'task-status'

/**
 * The normalized entity stored in the message store.
 * Carries provenance metadata for conflict resolution.
 */
export interface MessageEntity {
  // ── Identity ──────────────────────────────────────────────
  id: string
  roomId: string

  // ── Core content ──────────────────────────────────────────
  messageType: 'user' | 'agent'
  content: string
  senderName: string
  agentId?: string
  userId?: string

  // ── Task state (agent messages backed by A2A tasks) ───────
  taskStatus?: TaskState
  taskError?: string | null
  taskStatusMessage?: string | null
  taskRequiresInput?: boolean
  taskRequiresAuth?: boolean
  taskContent?: string
  taskCreatedAt?: string
  taskUpdatedAt?: string

  // ── Ordering ──────────────────────────────────────────────
  timestamp: string
  stepNumber?: number
  totalSteps?: number

  // ── Provenance & conflict resolution ──────────────────────
  source: MessageSource
  sourceVersion: number
  displayType: DisplayType
  isEphemeral: boolean
  createdAt: number
  updatedAt: number
}

/**
 * Input shape for the write gateway.
 * Data sources build IncomingMessage and pass it to upsertMessage,
 * which fills in provenance fields.
 */
export interface IncomingMessage {
  id: string
  roomId: string
  messageType: 'user' | 'agent'
  content: string
  senderName: string
  timestamp: string

  // All optional — omitted fields preserve existing values on update
  agentId?: string
  userId?: string
  taskStatus?: TaskState
  taskError?: string | null
  taskStatusMessage?: string | null
  taskRequiresInput?: boolean
  taskRequiresAuth?: boolean
  taskContent?: string
  taskCreatedAt?: string
  taskUpdatedAt?: string
  stepNumber?: number
  totalSteps?: number
  isEphemeral?: boolean
}
