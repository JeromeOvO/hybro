import type { ArtifactData, ProcessingStatusLogEntry } from '@/stores/message-store/types'
import type { AttachmentData } from '@/lib/types/attachments'

// ── Turn-level status ──────────────────────────────────────────

export type TurnStatus =
  | 'active'
  | 'awaiting_input'
  | 'completed'
  | 'failed'
  | 'partial'

export type TurnDisplayMode =
  | 'single_agent'
  | 'summary_with_sources'
  | 'parallel_results'
  | 'awaiting_input'
  | 'working'

export type TurnPhase =
  | 'collecting'
  | 'synthesizing'
  | 'answering'
  | 'completed'

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
  /** Resolved display mode (legacy; derived from finalAnswer for incremental rebuild). */
  displayMode: TurnDisplayMode
  /** Layout phase within a live turn. */
  phase?: TurnPhase
  /** Message whose stream drives scroll-follow (synthesis streaming). */
  primaryStreamMessageId?: string
  /** Scroll-follow target; equals primaryStreamMessageId when set. */
  primaryMessageId?: string
  /** Room-level terminal signal from user entity (processing_status SSE). */
  turnTerminalStatus?: 'completed' | 'failed' | 'canceled'
  /** Transient room-level processing status details shown while the turn is live. */
  processingStatusLogs: ProcessingStatusLogEntry[]
  /** V3: unified final-answer slot (§17). */
  finalAnswer: FinalAnswerViewModel
}

// ── V3 final answer ──────────────────────────────────────────

export type FinalAnswerKind =
  | 'pending'
  | 'hitl'
  | 'llm_synthesis'
  | 'deterministic_done'
  | 'canceled'
  | 'failed'
  | 'single'

export type SummaryOrigin = 'llm' | 'deterministic'

export type FinalAnswerLabel =
  | 'Synthesized'
  | 'Combined agent responses'
  | 'Working'
  | 'Needs input'
  | 'Canceled'
  | 'Failed'

export interface FinalAnswerSection {
  messageId: string
  agentId?: string
  agentName: string
  content: string
  artifacts: ArtifactData[]
  status: 'working' | 'completed' | 'failed' | 'awaiting_input'
}

export interface FinalAnswerHitlPrompt {
  messageId: string
  agentName: string
  prompt: string
  resolved?: { prompt: string; answer: string }
}

export interface FinalAnswerHitlViewModel {
  source: 'supervisor' | 'agent'
  prompts: FinalAnswerHitlPrompt[]
}

export interface FinalAnswerViewModel {
  kind: FinalAnswerKind
  label: FinalAnswerLabel
  primaryMessageId?: string
  /** Short HYBRO presenter copy for deterministic_done (virtual or backend entity body). */
  deterministicIntro?: string
  /** HYBRO presenter copy when the turn was canceled. */
  canceledIntro?: string
  /** HYBRO presenter copy when the turn failed (all agents failed / room failed). */
  failedIntro?: string
  summaryOrigin?: SummaryOrigin
  sections?: FinalAnswerSection[]
  hitl?: FinalAnswerHitlViewModel
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
  clientRequestId?: string
  status: 'completed' | 'failed' | 'awaiting_input' | 'working'
  content: string
  artifacts: ArtifactData[]
  /** Task-level status hint (for terminal/interactive states). */
  taskStatusMessage?: string | null
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
  /** True when this result came from an ephemeral placeholder entity. */
  isEphemeral?: boolean
  /** When isSummaryAgent — distinguishes LLM synthesis from deterministic DONE intro. */
  summaryOrigin?: SummaryOrigin
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
