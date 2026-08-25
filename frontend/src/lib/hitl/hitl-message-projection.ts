import type { HITLPromptType, TaskState } from '@/lib/types/sse'
import { normalizeTimestampOrNow } from '@/lib/time'
import type { IncomingMessage } from '@/stores/message-store/types'

export type HitlAgentSource = 'cloud' | 'local' | 'hub' | undefined

export type PendingHitlProjectionInput = {
  roomId: string
  messageId: string
  requestId: string
  source?: 'agent' | 'supervisor' | string | null | undefined
  prompt: string | null | undefined
  promptType: string | null | undefined
  choices: string[] | null | undefined
  timestamp: string | null | undefined
  agentId: string | null | undefined
  agentName: string | null | undefined
  agentSource: HitlAgentSource
  expiresAt: string | null | undefined
  interactionId?: string | null
  interactionStatus?: string | null
  interactionVersion?: number | null
  applicationStatus?: string | null
  applicationError?: string | null
  groupId: string | null | undefined
  groupTotal: number | null | undefined
  groupIndex: number | null | undefined
  stepNumber: number | null | undefined
  totalSteps: number | null | undefined
  relatedMessageId: string | null | undefined
  clientRequestId: string | null | undefined
}

const OPAQUE_INTERNAL_ID = /^(?:[a-f0-9]{32}|[a-f0-9-]{36})$/i

function publicAgentName(value: string | null | undefined): string {
  const name = value?.trim()
  if (!name || OPAQUE_INTERNAL_ID.test(name)) return 'Agent'
  return name
}

const KNOWN_PROMPT_TYPES = new Set([
  'text',
  'textarea',
  'choice',
  'single_choice',
  'multi_choice',
  'confirmation',
  'approval',
  'authentication',
  'date',
])

function normalizePromptType(
  promptType: PendingHitlProjectionInput['promptType'],
): HITLPromptType {
  if (!promptType) return 'text'
  if (KNOWN_PROMPT_TYPES.has(promptType)) return promptType as HITLPromptType
  return 'text'
}

export function buildPendingHitlIncomingMessage(
  input: PendingHitlProjectionInput,
): IncomingMessage {
  const normalizedApplicationStatus = input.applicationStatus ?? 'open'
  return {
    id: input.messageId,
    roomId: input.roomId,
    messageType: 'agent',
    content: input.prompt || '',
    senderName: publicAgentName(input.agentName),
    timestamp: normalizeTimestampOrNow(input.timestamp || undefined),
    agentId: input.agentId ?? undefined,
    agentSource: input.agentSource,
    taskStatus: 'input-required' as TaskState,
    taskError: input.applicationError ?? null,
    hitlRequestId: input.requestId,
    hitlSource: input.source === 'supervisor' ? 'supervisor' : 'agent',
    hitlPrompt: input.prompt || '',
    hitlPromptType: normalizePromptType(input.promptType),
    hitlChoices: Array.isArray(input.choices) ? input.choices : null,
    hitlExpiresAt: input.expiresAt ?? undefined,
    hitlResolved: false,
    hitlInteractionId: input.interactionId ?? input.groupId ?? input.requestId,
    hitlInteractionStatus: input.interactionStatus ?? 'open',
    hitlInteractionVersion: input.interactionVersion ?? undefined,
    // Pending follow-up prompts can reuse the same message id as the prior round.
    // Reset application state so the UI stops showing "Applying your answers"
    // and renders the fresh prompt as actionable.
    hitlApplicationStatus: normalizedApplicationStatus,
    hitlGroupId: input.groupId ?? null,
    hitlGroupTotal: input.groupTotal ?? null,
    hitlGroupIndex: input.groupIndex ?? null,
    hitlUserAnswer: '',
    stepNumber: input.stepNumber ?? undefined,
    totalSteps: input.totalSteps ?? undefined,
    relatedMessageId: input.relatedMessageId ?? undefined,
    clientRequestId: input.clientRequestId ?? undefined,
  }
}
