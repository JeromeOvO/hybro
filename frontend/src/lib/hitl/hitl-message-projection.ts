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
  'file',
])

function normalizePromptType(
  promptType: PendingHitlProjectionInput['promptType'],
): HITLPromptType {
  if (!promptType) return 'text'
  if (KNOWN_PROMPT_TYPES.has(promptType)) return promptType as HITLPromptType
  return 'unknown'
}

export function buildPendingHitlIncomingMessage(
  input: PendingHitlProjectionInput,
): IncomingMessage {
  return {
    id: input.messageId,
    roomId: input.roomId,
    messageType: 'agent',
    content: input.prompt || '',
    senderName: input.agentName || 'Agent',
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
    hitlApplicationStatus: input.applicationStatus ?? undefined,
    hitlGroupId: input.groupId ?? null,
    hitlGroupTotal: input.groupTotal ?? null,
    hitlGroupIndex: input.groupIndex ?? null,
    stepNumber: input.stepNumber ?? undefined,
    totalSteps: input.totalSteps ?? undefined,
    relatedMessageId: input.relatedMessageId ?? undefined,
    clientRequestId: input.clientRequestId ?? undefined,
  }
}
