import type { TaskState } from '@/lib/types/sse'
import type { HITLPromptType } from '@/lib/types/sse'
import type { AttachmentData } from '@/lib/types/attachments'

/** A single part within an A2A artifact. */
export interface ArtifactPart {
  kind: 'text' | 'file' | 'data'
  text?: string
  file?: { uri?: string; bytes?: string; mime_type?: string; name?: string }
  data?: Record<string, unknown>
}

/** An artifact emitted by an agent, stored alongside the message entity. */
export interface ArtifactData {
  artifactId: string
  name?: string
  parts: ArtifactPart[]
  isStreaming?: boolean
}

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
  agentSource?: 'cloud' | 'hub'
  userId?: string
  clientRequestId?: string

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
  relatedMessageId?: string

  // ── HITL (Human-in-the-Loop) ─────────────────────────────
  hitlRequestId?: string
  hitlPrompt?: string
  hitlPromptType?: HITLPromptType
  hitlChoices?: string[] | null
  hitlExpiresAt?: string
  hitlResolved?: boolean
  hitlGroupId?: string
  hitlGroupTotal?: number
  hitlGroupIndex?: number
  hitlUserAnswer?: string

  // ── Provenance & conflict resolution ──────────────────────
  source: MessageSource
  sourceVersion: number
  displayType: DisplayType
  isEphemeral: boolean
  createdAt: number
  updatedAt: number

  // ── Multimodal artifacts ──────────────────────────────────
  artifacts?: ArtifactData[]

  // ── User attachments ──────────────────────────────────────
  attachments?: AttachmentData[]
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
  agentSource?: 'cloud' | 'hub'
  userId?: string
  clientRequestId?: string
  taskStatus?: TaskState | null
  taskError?: string | null
  taskStatusMessage?: string | null
  taskRequiresInput?: boolean
  taskRequiresAuth?: boolean
  taskContent?: string
  taskCreatedAt?: string
  taskUpdatedAt?: string
  stepNumber?: number
  totalSteps?: number
  relatedMessageId?: string
  hitlRequestId?: string
  hitlPrompt?: string
  hitlPromptType?: HITLPromptType
  hitlChoices?: string[] | null
  hitlExpiresAt?: string
  hitlResolved?: boolean
  hitlGroupId?: string
  hitlGroupTotal?: number
  hitlGroupIndex?: number
  hitlUserAnswer?: string
  isEphemeral?: boolean
  artifacts?: ArtifactData[]
  attachments?: AttachmentData[]
}
