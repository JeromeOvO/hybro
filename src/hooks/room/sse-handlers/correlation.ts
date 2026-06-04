import type { SSEMessage } from '@/lib/types/sse'
import type { ProcessingLifecycle } from '../processing-lifecycle'
import {
  enqueuePendingSseEvent,
  getResolvedMessageId,
  resolveClientRequestMessageId,
} from './pending-turn-buffer'

/** Events that require client_request_id (or compat fallback) to apply. */
export const TURN_CORRELATED_EVENT_TYPES = new Set<SSEMessage['type']>([
  'processing_status',
  'task_submitted',
  'task_update',
  'artifact_update',
  'hitl_input_requested',
  'hitl_status_update',
])

/** Events buffered until user_message resolves client_request_id → message_id. */
export const CORRELATION_BUFFER_EVENT_TYPES = new Set<SSEMessage['type']>([
  'processing_status',
  'task_submitted',
  'task_update',
  'artifact_update',
])

const ENABLE_UNCORRELATED_SSE_COMPAT_FALLBACK =
  process.env.NEXT_PUBLIC_SSE_CORRELATION_COMPAT === '1'

export interface CorrelationResult {
  clientReqId?: string
  shouldBuffer: boolean
  shouldDrop: boolean
}

export function resolveSseCorrelation(
  sseMessage: SSEMessage,
  lifecycle: ProcessingLifecycle,
): CorrelationResult {
  if (!TURN_CORRELATED_EVENT_TYPES.has(sseMessage.type)) {
    return { shouldBuffer: false, shouldDrop: false }
  }

  const clientReqId = sseMessage.data?.client_request_id as string | undefined
  if (clientReqId) {
    const resolvedMessageId = getResolvedMessageId(clientReqId)
    if (resolvedMessageId) {
      return { clientReqId, shouldBuffer: false, shouldDrop: false }
    }

    const messageId = sseMessage.data?.message_id as string | undefined
    const relatedMessageId = sseMessage.data?.related_message_id as string | undefined
    const lifecycleMessageId = lifecycle.getMessageId()
    if (messageId && lifecycleMessageId && messageId === lifecycleMessageId) {
      resolveClientRequestMessageId(clientReqId, messageId)
      return { clientReqId, shouldBuffer: false, shouldDrop: false }
    }
    if (relatedMessageId && lifecycleMessageId && relatedMessageId === lifecycleMessageId) {
      resolveClientRequestMessageId(clientReqId, relatedMessageId)
      return { clientReqId, shouldBuffer: false, shouldDrop: false }
    }

    const shouldBuffer = true
    return { clientReqId, shouldBuffer, shouldDrop: false }
  }

  if (ENABLE_UNCORRELATED_SSE_COMPAT_FALLBACK && lifecycle.getMessageId()) {
    console.warn(
      '[compat] Proceeding with uncorrelated SSE event without client_request_id:',
      sseMessage.type,
    )
    return { shouldBuffer: false, shouldDrop: false }
  }

  console.warn('Dropping turn-correlated SSE event without client_request_id:', sseMessage.type)
  return { shouldBuffer: false, shouldDrop: true }
}

export function bufferCorrelatedEvent(
  clientReqId: string,
  sseMessage: SSEMessage,
): void {
  enqueuePendingSseEvent(clientReqId, sseMessage)
}
