// Client-request correlation extraction (post-correlation-buffer replacement).
//
// Phase 3 of the Room Stream Snapshot plan removed the heuristic correlation
// buffer and pending-turn buffer: the reducer's ordered delta patch owns
// ordering, and message-id resolution is a direct store lookup against the
// optimistic entity (which carries client_request_id from send time).

import type { RoomSSEMessage, RoomSSEType } from '@/lib/types/sse'
import { findProcessingStatusUserEntity } from '../processing-status-log'

/** Frame types that require a client_request_id to apply. */
export const TURN_CORRELATED_EVENT_TYPES = new Set<RoomSSEType>([
  'processing_status',
  'task_submitted',
  'task_update',
  'artifact_update',
  'agent_response',
  'agent_response_partial',
])

export function clientRequestIdOf(frame: RoomSSEMessage): string | null {
  const data = frame.data
  if (!data || typeof data !== 'object') return null
  const record = data as { client_request_id?: unknown; correlation_id?: unknown }
  const value = record.client_request_id ?? record.correlation_id
  return typeof value === 'string' && value.length > 0 ? value : null
}

/** Resolve the user message id for a client_request_id from the store. */
export function resolveUserMessageId(
  roomId: string,
  clientRequestId: string | null,
): string | undefined {
  if (!clientRequestId) return undefined
  return findProcessingStatusUserEntity(roomId, {
    clientRequestId,
    preferClientRequestId: true,
  })?.id
}
