import type { ArtifactData as StoreArtifactData } from '@/stores/message-store/types'
import type { AttachmentData } from '@/lib/types/attachments'

// Re-export for convenience
export type ArtifactData = StoreArtifactData

// ── Wire-to-store field name mapping ──────────────────────────
// SSE wire format uses snake_case. These types are camelCase (frontend convention).
// The adapter in useSSEToEventLog handles the transformation.

// ── Turn Event Envelope ───────────────────────────────────────

export interface TurnEventEnvelope {
  eventId: string
  turnId: string
  seq: number
  ts: number
  clientRequestId?: string
}

// ── User Input ────────────────────────────────────────────────

export interface UserInputData {
  text: string
  attachments: AttachmentData[]
}

// ── Phase Payloads ────────────────────────────────────────────

export type PhasePayload =
  | { name: 'planning' }
  | { name: 'delegating'; agentNames: string[]; count: number }
  | { name: 'evaluating' }
  | { name: 'synthesizing' }
  | { name: 'awaiting_input' }
  | { name: 'round'; current: number; total: number }
  | { name: 'workflow_step'; current: number; total: number; stepName: string }

// ── Turn Event Types ──────────────────────────────────────────

export type TurnEventType =
  | 'turn_started'
  | 'turn_completed'
  | 'turn_failed'
  | 'turn_canceled'
  | 'phase_changed'
  | 'slot_opened'
  | 'slot_delta'
  | 'artifact_appended'
  | 'slot_snapshot'
  | 'slot_terminated'
  | 'hitl_requested'
  | 'hitl_answered'
  | 'hitl_expired'
  | 'hitl_canceled'
  | 'hitl_error'

export type TurnEvent = TurnEventEnvelope & (
  | { type: 'turn_started'; userInput: UserInputData }
  | { type: 'turn_completed'; durationMs: number }
  | { type: 'turn_failed'; reason: string; code?: 'rate_limited' | 'error' | 'timeout' }
  | { type: 'turn_canceled' }
  | { type: 'phase_changed'; phase: PhasePayload }
  | { type: 'slot_opened'; slotId: string; slotType: 'agent' | 'summary'; agentId?: string; agentName?: string; mode?: 'supervisor' | 'debate' }
  | { type: 'slot_delta'; slotId: string; textDelta: string }
  | { type: 'artifact_appended'; slotId: string; artifact: ArtifactData }
  | { type: 'slot_snapshot'; slotId: string; content: string; artifacts: ArtifactData[] }
  | { type: 'slot_terminated'; slotId: string; status: 'completed' | 'failed' | 'canceled' | 'rejected'; error?: string; hasPartialContent?: boolean }
  | { type: 'hitl_requested'; hitlId: string; source: 'supervisor' | 'agent'; agentName?: string; prompt: string; promptType: 'text' | 'choice' | 'confirmation'; choices?: string[]; groupId?: string; groupTotal?: number; groupIndex?: number }
  | { type: 'hitl_answered'; hitlId: string; answer: string }
  | { type: 'hitl_expired'; hitlId: string }
  | { type: 'hitl_canceled'; hitlId: string }
  | { type: 'hitl_error'; hitlId: string; error: string }
)

// ── Type guards ───────────────────────────────────────────────

export const TURN_TERMINAL_TYPES: TurnEventType[] = ['turn_completed', 'turn_failed', 'turn_canceled']

export function isTurnTerminal(type: TurnEventType): boolean {
  return TURN_TERMINAL_TYPES.includes(type)
}

export const SLOT_TERMINAL_STATUSES = ['completed', 'failed', 'canceled', 'rejected'] as const

export function isSlotTerminal(status: string): boolean {
  return (SLOT_TERMINAL_STATUSES as readonly string[]).includes(status)
}

// ── Projection Views ──────────────────────────────────────────

export type SlotType = 'agent' | 'summary' | 'hitl_record'
export type SlotStatus = 'streaming' | 'completed' | 'failed' | 'canceled' | 'rejected'

export interface ContentSlotView {
  slotId: string
  slotType: SlotType
  agentId?: string
  agentName?: string
  content: string
  artifacts: ArtifactData[]
  status: SlotStatus
  error?: string
  hasPartialContent?: boolean
  // hitl_record specific
  hitlPrompt?: string
  hitlAnswer?: string
  hitlSource?: 'supervisor' | 'agent'
  // summary specific
  mode?: 'supervisor' | 'debate'
}

export type RailIcon = 'spinner' | 'check' | 'x' | 'pause' | 'info'

export interface RailItemView {
  key: string
  icon: RailIcon
  label: string
  ts: number
  isActive: boolean
}

export interface HitlPromptView {
  hitlId: string
  turnId: string
  ts: number
  source: 'supervisor' | 'agent'
  agentName?: string
  prompt: string
  promptType: 'text' | 'choice' | 'confirmation'
  choices?: string[]
  groupId?: string
  groupTotal?: number
  groupIndex?: number
}

export type ComposerMode = 'normal' | 'hitl_responding'

export interface ComposerStateView {
  mode: ComposerMode
  pendingHitls: HitlPromptView[]
  isProcessing: boolean
}

// ── Projection Reducer Interface ──────────────────────────────

export interface ProjectionReducer<TView> {
  init(): TView
  reduce(view: TView, event: TurnEvent): TView
}
