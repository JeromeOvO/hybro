import type { SSEMessage } from '@/lib/types/sse'

const PENDING_SSE_BUFFER_TTL_MS = 30_000
const MAX_PENDING_CLIENT_REQUESTS = 64
const MAX_EVENTS_PER_CLIENT_REQUEST = 256

type Buffered = {
  createdAt: number
  events: SSEMessage[]
}

const pendingByClientRequestId = new Map<string, Buffered>()
const messageIdByClientRequestId = new Map<string, string>()

function warnOnce(message: string, ...args: unknown[]) {
  // Keep warnings dev-facing and low-noise in production logs.
  if (process.env.NODE_ENV !== 'production') {
    console.warn(message, ...args)
  }
}

function evictExpired(now: number) {
  for (const [clientRequestId, pending] of pendingByClientRequestId) {
    if (now - pending.createdAt <= PENDING_SSE_BUFFER_TTL_MS) continue
    pendingByClientRequestId.delete(clientRequestId)
    warnOnce(
      'pending SSE buffer evicted for clientRequestId=%s — possible orphan SSE stream',
      clientRequestId,
    )
  }
}

export function getResolvedMessageId(clientRequestId: string): string | undefined {
  return messageIdByClientRequestId.get(clientRequestId)
}

export function resolveClientRequestMessageId(clientRequestId: string, messageId: string): void {
  messageIdByClientRequestId.set(clientRequestId, messageId)
}

export function clearPendingSseForClientRequest(clientRequestId: string): void {
  pendingByClientRequestId.delete(clientRequestId)
}

export function enqueuePendingSseEvent(clientRequestId: string, event: SSEMessage): boolean {
  const now = Date.now()
  evictExpired(now)

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

export function flushPendingSseEvents(
  clientRequestId: string,
  dispatch: (event: SSEMessage, forcedMessageId?: string) => Promise<void>,
  messageId: string,
): Promise<void> {
  resolveClientRequestMessageId(clientRequestId, messageId)
  const pending = pendingByClientRequestId.get(clientRequestId)
  if (!pending || pending.events.length === 0) {
    return Promise.resolve()
  }
  pendingByClientRequestId.delete(clientRequestId)
  return pending.events.reduce<Promise<void>>(
    (p, event) => p.then(() => dispatch(event, messageId)),
    Promise.resolve(),
  )
}

