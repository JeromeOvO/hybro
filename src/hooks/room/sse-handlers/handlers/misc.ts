import { banner } from '@/components/ui/banner'
import type { SSEMessage } from '@/lib/types/sse'
import type { ProcessingLifecycle } from '../../processing-lifecycle'
import type { SSEHandlerDeps } from '../types'

export function handleError(_ctx: SSEHandlerDeps, sseMessage: SSEMessage): void {
  console.error('❌ SSE error message:', sseMessage.data)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const errorData = sseMessage.data as any
  if (errorData?.error_type === 'rate_limit_exceeded') {
    const retryAfter = errorData.retry_after_seconds
    const retryMinutes = retryAfter ? Math.ceil(retryAfter / 60) : 60
    banner.error(
      errorData.error || `Rate limit exceeded. Please try again in ${retryMinutes} minutes.`,
      { duration: 15000 },
    )
  } else {
    banner.error(errorData?.error || errorData?.details || 'Unknown error')
  }
}

export function handleHeartbeat(): void {
  console.log('💓 SSE heartbeat received')
}

export function handleTurnEvent(): void {
  console.log('ℹ️ Ignoring turn_event SSE in single-writer mode')
}

export function handleRunEvent(
  ctx: SSEHandlerDeps,
  lifecycle: ProcessingLifecycle,
  sseMessage: SSEMessage,
): void {
  const correlationId = sseMessage.data?.correlation_id
  if (typeof correlationId === 'string' && correlationId.length > 0) {
    const pendingAckClientRequestId = lifecycle.getPendingRunEventAck()
    if (pendingAckClientRequestId && pendingAckClientRequestId === correlationId) {
      lifecycle.clearPendingRunEventAck()
    }
  }

  const sub = sseMessage.data?.type as string | undefined
  if (sub === 'run_failed' || sub === 'run_completed' || sub === 'run_canceled') {
    void ctx.reconcileWithDb(ctx.roomId)
  }
}
