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

export type TurnPhaseLog = 'collecting' | 'synthesizing' | 'terminal'

export interface ProcessingStatusLogEntry {
  id: string
  message: string
  timestamp: string
  turnPhase?: TurnPhaseLog
}

/** Which pipeline last wrote this entity. */
export type MessageSource = 'db' | 'sse' | 'optimistic'

/**
 * Display type — resolved once at write time.
 * Determines which React component renders this message.
 */
export type DisplayType = 'user-bubble' | 'agent-bubble'

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

  // ── Quoted context (user messages only) ────────────────────
  quotedText?: string
  quotedSenderName?: string
  /** Persisted quote snapshot id (QUOTE_REPLY). */
  quoteId?: string

  // ── Turn terminal signal ──────────────────────────────────
  // Written by the processing_status SSE handler onto the user entity so
  // useMessageStoreSync can derive turn_completed/failed/canceled without
  // the turn store receiving direct writes (derived-only constraint).
  turnTerminalStatus?: 'completed' | 'failed' | 'canceled'

  /** Backend-authoritative signal: whether the turn completed via LLM synthesis or deterministic digest. */
  turnCompletionKind?: 'synthesis' | 'deterministic'

  /** Parsed from backend extend_info.summary_origin for summary-family agents. */
  summaryOrigin?: 'llm' | 'deterministic'

  /** Transient in-memory processing_status detail log for the live turn. */
  processingStatusLogs?: ProcessingStatusLogEntry[]
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
  quotedText?: string
  quotedSenderName?: string
  quoteId?: string
  turnTerminalStatus?: 'completed' | 'failed' | 'canceled'
  turnCompletionKind?: 'synthesis' | 'deterministic'
  summaryOrigin?: 'llm' | 'deterministic'
  processingStatusLogs?: ProcessingStatusLogEntry[]
}
