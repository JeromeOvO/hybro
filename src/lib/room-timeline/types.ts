import type { ArtifactData } from '@/stores/message-store/types'
import type { AttachmentData } from '@/lib/types/attachments'

// ── Turn-level status ──────────────────────────────────────────

export type TurnStatus =
  | 'active'
  | 'awaiting_input'
  | 'completed'
  | 'failed'
  | 'partial'

// ── Turn view model ────────────────────────────────────────────

export interface TurnViewModel {
  id: string
  roomId: string
  userMessageId: string | null
  userContent: string
  userAttachments: AttachmentData[]
  timestamp: string
  status: TurnStatus
  events: TimelineEventViewModel[]
  summary: TurnSummaryViewModel | null
  agentResults: AgentResultViewModel[]
  activeAgentIds: string[]
  /** Whether this turn was dispatched via Supervisor orchestration.
   *  Derived from presence of supervisor_hitl / supervisor_synthesis entities. */
  isSupervisorTurn: boolean
  /** Supervisor stage details (active turns only). */
  supervisorStage?: {
    stepNumber?: number
    totalSteps?: number
    details?: string
  }
}

// ── Timeline event types ───────────────────────────────────────

export type TimelineEventKind =
  | 'user_prompt'
  | 'agent_started'
  | 'agent_progress'
  | 'hitl_requested'
  | 'hitl_answered'
  | 'artifact_emitted'
  | 'agent_completed'
  | 'agent_failed'

export interface TimelineEventViewModel {
  id: string
  kind: TimelineEventKind
  timestamp: string
  agentId?: string
  agentName?: string
  label: string
  body?: string
  artifactPayload?: ArtifactData
  hitlPayload?: { prompt: string; answer?: string }
  isLive: boolean
  isHiddenInCompact: boolean
}

// ── Turn summary ───────────────────────────────────────────────

export interface TurnSummaryViewModel {
  sourceAgentId?: string
  sourceAgentName: string
  title: string
  body: string
  confidence?: 'high' | 'medium' | 'low'
}

// ── Agent result ───────────────────────────────────────────────

export interface AgentResultViewModel {
  agentId?: string
  agentName: string
  agentSource?: 'hub' | 'cloud'
  messageId: string
  status: 'completed' | 'failed' | 'awaiting_input' | 'working'
  content: string
  artifacts: ArtifactData[]
  hitlHistory?: { prompt: string; answer: string }[]
  /** Whether this agent is a summary-family system agent. */
  isSummaryAgent: boolean
  /** Resolved HITL: prompt and user answer. */
  hitlResolved?: { prompt: string; answer: string }
  /** Active (unanswered) HITL prompt. */
  hitlPending?: { prompt: string }
  /** Event count for inline chips. */
  eventCount?: number
  /** Duration in ms for inline chips. */
  durationMs?: number
}

// ── Event log input (raw event from SSE handler) ───────────────

export interface RawTimelineEvent {
  kind: TimelineEventKind
  timestamp: string
  agentId?: string
  agentName?: string
  label: string
  body?: string
  artifactPayload?: ArtifactData
  hitlPayload?: { prompt: string; answer?: string }
}
