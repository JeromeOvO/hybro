import { toast } from 'sonner'
import type { ErrorData, RoomSSEFrameMap } from '@/lib/types/sse'
import type { ProcessingLifecycle } from '../../processing-lifecycle'
import type { SSEHandlerDeps } from '../types'

function isErrorDataObject(data: unknown): data is ErrorData {
  return Boolean(data && typeof data === 'object' && !Array.isArray(data))
}

function isTurnScopedError(data: ErrorData): boolean {
  return 'client_request_id' in data || 'message_id' in data || 'agent_id' in data
}

export function handleError(_ctx: SSEHandlerDeps, sseMessage: RoomSSEFrameMap['error']): void {
  console.error('❌ SSE error message:', sseMessage.data)
  const errorData = isErrorDataObject(sseMessage.data) ? sseMessage.data : undefined
  if (!errorData) {
    console.debug('Ignoring malformed error SSE data:', sseMessage.data)
    toast.error('Unknown error')
    return
  }

  if (isTurnScopedError(errorData) && !errorData.client_request_id) {
    console.debug('Ignoring turn-scoped error without client_request_id:', errorData)
    return
  }

  if (errorData?.error_type === 'rate_limit_exceeded') {
    const retryAfter = errorData.retry_after_seconds
    const retryMinutes = retryAfter ? Math.ceil(retryAfter / 60) : 60
    const quotaDetails = [
      retryAfter ? `Retry after ${retryMinutes} minutes.` : undefined,
      typeof errorData.user_requests_used === 'number' && typeof errorData.user_requests_limit === 'number'
        ? `User requests: ${errorData.user_requests_used}/${errorData.user_requests_limit}.`
        : undefined,
      typeof errorData.system_requests_used === 'number' && typeof errorData.system_requests_limit === 'number'
        ? `System requests: ${errorData.system_requests_used}/${errorData.system_requests_limit}.`
        : undefined,
    ].filter(Boolean).join(' ')
    toast.error(
      errorData.error || `Rate limit exceeded. Please try again in ${retryMinutes} minutes.`,
      { duration: 15000, description: quotaDetails || undefined },
    )
  } else {
    toast.error(errorData?.error || 'Unknown error')
  }
}

export function handleConnected(sseMessage: RoomSSEFrameMap['connected']): void {
  console.debug('Room SSE connected:', sseMessage.data.connection_id)
}

export function handleHeartbeat(): void {
}

export function handleCancellation(
  _ctx: SSEHandlerDeps,
  sseMessage: RoomSSEFrameMap['cancellation'],
): void {
  console.debug('Room SSE cancellation event:', sseMessage.data)
}

export function handleRunEvent(
  lifecycle: ProcessingLifecycle,
  sseMessage: RoomSSEFrameMap['run_event'],
): void {
  const correlationId = sseMessage.data?.correlation_id
  if (typeof correlationId === 'string' && correlationId.length > 0) {
    const pendingAckClientRequestId = lifecycle.getPendingRunEventAck()
    if (pendingAckClientRequestId && pendingAckClientRequestId === correlationId) {
      lifecycle.clearPendingRunEventAck()
    }
  }
}
