import type { SSEMessage } from '@/lib/types/sse'
import { useMessageStore } from '@/stores/message-store'
import { normalizeTimestampOrNow } from '@/lib/time'
import type { SSEHandlerDeps } from '../types'

export function handleUserMessage(ctx: SSEHandlerDeps, sseMessage: SSEMessage): void {
  console.log('📨 User message received via SSE')
  if (!sseMessage.data?.content) return

  useMessageStore.getState().upsertMessage({
    id: sseMessage.data.message_id || `sse-${Date.now()}`,
    roomId: ctx.roomId,
    messageType: 'user',
    content: sseMessage.data.content,
    senderName: sseMessage.data.user_id || 'User',
    userId: sseMessage.data.user_id,
    timestamp: normalizeTimestampOrNow(sseMessage.timestamp),
    clientRequestId: sseMessage.data.client_request_id || undefined,
  }, 'sse')
}
