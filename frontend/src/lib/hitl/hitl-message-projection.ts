import type { TaskState } from '@/lib/types/sse'
import { normalizeTimestampOrNow } from '@/lib/time'
import type { IncomingMessage } from '@/stores/message-store/types'

export type HitlAgentSource = 'cloud' | 'hub' | undefined

export type PendingHitlProjectionInput = {
  roomId: string
  messageId: string
  requestId: string
  source?: 'agent' | 'supervisor' | string | null | undefined
  prompt: string | null | undefined
  promptType: 'text' | 'choice' | 'confirmation' | string | null | undefined
  choices: string[] | null | undefined
  timestamp: string | null | undefined
  agentId: string | null | undefined
  agentName: string | null | undefined
  agentSource: HitlAgentSource
  expiresAt: string | null | undefined
  groupId: string | null | undefined
  groupTotal: number | null | undefined
  groupIndex: number | null | undefined
  stepNumber: number | null | undefined
  totalSteps: number | null | undefined
  relatedMessageId: string | null | undefined
  clientRequestId: string | null | undefined
}

function normalizePromptType(
  promptType: PendingHitlProjectionInput['promptType'],
): 'text' | 'choice' | 'confirmation' {
  if (promptType === 'choice' || promptType === 'confirmation') return promptType
  return 'text'
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
    hitlRequestId: input.requestId,
    hitlSource: input.source === 'supervisor' ? 'supervisor' : 'agent',
    hitlPrompt: input.prompt || '',
    hitlPromptType: normalizePromptType(input.promptType),
    hitlChoices: Array.isArray(input.choices) ? input.choices : null,
    hitlExpiresAt: input.expiresAt ?? undefined,
    hitlResolved: false,
    hitlUserAnswer: '',
    hitlGroupId: input.groupId ?? null,
    hitlGroupTotal: input.groupTotal ?? null,
    hitlGroupIndex: input.groupIndex ?? null,
    stepNumber: input.stepNumber ?? undefined,
    totalSteps: input.totalSteps ?? undefined,
    relatedMessageId: input.relatedMessageId ?? undefined,
    clientRequestId: input.clientRequestId ?? undefined,
  }
}
