import type { SSEMessage } from '@/lib/types/sse'

const PENDING_SSE_BUFFER_TTL_MS = 120_000
const MAX_PENDING_CLIENT_REQUESTS = 64
const MAX_EVENTS_PER_CLIENT_REQUEST = 256

// TTL for the message-ID resolution map. Entries are explicitly removed on
// clearPendingSseForClientRequest / flushPendingSseEvents, but this TTL
// acts as a safety net for orphaned request IDs (e.g. the message send
// failed before a resolution event was ever received).
const MESSAGE_ID_RESOLUTION_TTL_MS = 10 * 60_000 // 10 minutes

type Buffered = {
  createdAt: number
  events: SSEMessage[]
}

type Resolved = {
  messageId: string
  resolvedAt: number
}

const pendingByClientRequestId = new Map<string, Buffered>()
const messageIdByClientRequestId = new Map<string, Resolved>()

function warnOnce(message: string, ...args: unknown[]) {
  // Keep warnings dev-facing and low-noise in production logs.
  if (process.env.NODE_ENV !== 'production') {
    console.warn(message, ...args)
  }
}

function evictExpiredPending(now: number) {
  for (const [clientRequestId, pending] of pendingByClientRequestId) {
    if (now - pending.createdAt <= PENDING_SSE_BUFFER_TTL_MS) continue
    const eventCount = pending.events.length
    pendingByClientRequestId.delete(clientRequestId)
    console.warn(
      '[SSE buffer] evicted clientRequestId=%s after %dms — %d events dropped (possible orphan stream)',
      clientRequestId,
      now - pending.createdAt,
      eventCount,
    )
  }
}

function evictExpiredResolutions(now: number) {
  for (const [clientRequestId, resolved] of messageIdByClientRequestId) {
    if (now - resolved.resolvedAt <= MESSAGE_ID_RESOLUTION_TTL_MS) continue
    messageIdByClientRequestId.delete(clientRequestId)
  }
}

export function getResolvedMessageId(clientRequestId: string): string | undefined {
  return messageIdByClientRequestId.get(clientRequestId)?.messageId
}

export function resolveClientRequestMessageId(clientRequestId: string, messageId: string): void {
  messageIdByClientRequestId.set(clientRequestId, { messageId, resolvedAt: Date.now() })
}

/**
 * Remove all buffered SSE events AND the message-ID resolution entry for
 * clientRequestId. Called when a request is definitively completed or
 * abandoned (e.g. send failed, room switched, or flush complete).
 */
export function clearPendingSseForClientRequest(clientRequestId: string): void {
  pendingByClientRequestId.delete(clientRequestId)
  messageIdByClientRequestId.delete(clientRequestId)
}

export function enqueuePendingSseEvent(clientRequestId: string, event: SSEMessage): boolean {
  const now = Date.now()
  evictExpiredPending(now)
  // Opportunistically evict stale resolutions on each enqueue so the map
  // doesn't grow unboundedly in long-lived sessions.
  evictExpiredResolutions(now)

  if (
    !pendingByClientRequestId.has(clientRequestId) &&
    pendingByClientRequestId.size >= MAX_PENDING_CLIENT_REQUESTS
  ) {
    warnOnce('pending SSE buffer at capacity; dropping event for %s', clientRequestId)
    return false
  }

  const pending = pendingByClientRequestId.get(clientRequestId) ?? {
    createdAt: now,
    events: [],
  }
  if (pending.events.length >= MAX_EVENTS_PER_CLIENT_REQUEST) {
    warnOnce('pending SSE buffer reached per-request cap; dropping event for %s', clientRequestId)
    return false
  }

  pending.events.push(event)
  pendingByClientRequestId.set(clientRequestId, pending)
  return true
}

/** Test-only: clear module-level pending/resolution maps between tests. */
export function resetPendingTurnBufferForTests(): void {
  pendingByClientRequestId.clear()
  messageIdByClientRequestId.clear()
}

export function flushPendingSseEvents(
  clientRequestId: string,
  dispatch: (event: SSEMessage) => Promise<void>,
  messageId: string,
): Promise<void> {
  resolveClientRequestMessageId(clientRequestId, messageId)
  const pending = pendingByClientRequestId.get(clientRequestId)
  if (!pending || pending.events.length === 0) {
    // No buffered events — still clear the pending entry. The resolution
    // entry is intentionally kept: subsequent same-session SSE events that
    // arrive after the flush still need getResolvedMessageId() to work.
    pendingByClientRequestId.delete(clientRequestId)
    return Promise.resolve()
  }
  pendingByClientRequestId.delete(clientRequestId)
  return pending.events.reduce<Promise<void>>(
    (p, event) => p.then(() => dispatch(event)),
    Promise.resolve(),
  )
}

