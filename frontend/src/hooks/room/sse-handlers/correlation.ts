import type { RoomSSEMessage, RoomSSEType } from '@/lib/types/sse'
import type { ProcessingLifecycle } from '../processing-lifecycle'
import {
  enqueuePendingSseEvent,
  getResolvedMessageId,
} from './pending-turn-buffer'

/** Events that require client_request_id to apply. */
export const TURN_CORRELATED_EVENT_TYPES = new Set<RoomSSEType>([
  'processing_status',
  'task_submitted',
  'task_update',
  'artifact_update',
  'agent_response',
  'agent_response_partial',
])

/** Events buffered until the send response resolves client_request_id to the user message id. */
export const CORRELATION_BUFFER_EVENT_TYPES = new Set<RoomSSEType>([
  'processing_status',
  'task_submitted',
  'task_update',
  'artifact_update',
  'agent_response',
  'agent_response_partial',
])

export interface CorrelationResult {
  clientReqId?: string
  shouldBuffer: boolean
  shouldDrop: boolean
}

export function resolveSseCorrelation(
  sseMessage: RoomSSEMessage,
  _lifecycle: ProcessingLifecycle,
): CorrelationResult {
  if (!TURN_CORRELATED_EVENT_TYPES.has(sseMessage.type)) {
    return { shouldBuffer: false, shouldDrop: false }
  }

  const clientReqId = sseMessage.data && typeof sseMessage.data === 'object'
    ? (sseMessage.data as { client_request_id?: unknown }).client_request_id
    : undefined

  if (typeof clientReqId === 'string' && clientReqId.length > 0) {
    const resolvedMessageId = getResolvedMessageId(clientReqId)
    if (resolvedMessageId) {
      return { clientReqId, shouldBuffer: false, shouldDrop: false }
    }

    return {
      clientReqId,
      shouldBuffer: CORRELATION_BUFFER_EVENT_TYPES.has(sseMessage.type),
      shouldDrop: false,
    }
  }

  console.debug('Dropping turn-correlated SSE event without client_request_id:', sseMessage.type)
  return { shouldBuffer: false, shouldDrop: true }
}

export function bufferCorrelatedEvent(
  clientReqId: string,
  sseMessage: RoomSSEMessage,
): void {
  enqueuePendingSseEvent(clientReqId, sseMessage)
}
