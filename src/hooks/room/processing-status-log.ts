import { useMessageStore } from '@/stores/message-store'
import type { MessageEntity, MessageSource, ProcessingStatusLogEntry } from '@/stores/message-store/types'
import type { ProcessingLifecycle } from './processing-lifecycle'

export const INITIAL_PROCESSING_STATUS_MESSAGE = 'Thinking...'

export function createInitialProcessingStatusLog(timestamp: string): ProcessingStatusLogEntry {
  return {
    id: `processing-log-${timestamp}-0`,
    message: INITIAL_PROCESSING_STATUS_MESSAGE,
    timestamp,
  }
}

export function ensureInitialProcessingStatusLog(
  roomId: string,
  userEntity: MessageEntity | undefined,
  timestamp = new Date().toISOString(),
): void {
  if (!userEntity) return

  appendProcessingStatusLog(roomId, userEntity, INITIAL_PROCESSING_STATUS_MESSAGE, timestamp)
}

export function findProcessingStatusUserEntity(
  roomId: string,
  options: {
    messageId?: string | null
    clientRequestId?: string | null
    relatedMessageId?: string | null
    beforeTimestamp?: string | null
    latestWithLogs?: boolean
    preferClientRequestId?: boolean
  },
): MessageEntity | undefined {
  const store = useMessageStore.getState()
  const findByClientRequestId = () => {
    if (!options.clientRequestId) return undefined
    return store.orderedIds
      .map((id) => store.entities[id])
      .find((entity) =>
        entity?.roomId === roomId &&
        entity.messageType === 'user' &&
        entity.clientRequestId === options.clientRequestId
      )
  }

  if (options.preferClientRequestId) {
    const correlated = findByClientRequestId()
    if (correlated) return correlated
  }

  if (options.messageId) {
    const direct = store.entities[options.messageId]
    if (direct?.roomId === roomId && direct.messageType === 'user') return direct
  }

  if (options.relatedMessageId) {
    const related = store.entities[options.relatedMessageId]
    if (related?.roomId === roomId && related.messageType === 'user') return related
  }

  if (!options.preferClientRequestId) {
    const correlated = findByClientRequestId()
    if (correlated) return correlated
  }

  if (options.beforeTimestamp) {
    const beforeTime = new Date(options.beforeTimestamp).getTime()
    const candidates = store.orderedIds
      .map((id) => store.entities[id])
      .filter((entity): entity is MessageEntity =>
        !!entity &&
        entity.roomId === roomId &&
        entity.messageType === 'user' &&
        new Date(entity.timestamp).getTime() <= beforeTime
      )
    const latest = candidates.at(-1)
    if (latest) return latest
  }

  if (options.latestWithLogs) {
    return store.orderedIds
      .map((id) => store.entities[id])
      .filter((entity): entity is MessageEntity =>
        !!entity &&
        entity.roomId === roomId &&
        entity.messageType === 'user' &&
        (entity.processingStatusLogs?.length ?? 0) > 0
      )
      .at(-1)
  }

  return undefined
}

export function normalizeProcessingDetails(details: unknown): string | undefined {
  if (typeof details === 'string') return details.trim() || undefined
  if (!details || typeof details !== 'object') return undefined

  const message = (details as { message?: unknown }).message
  return typeof message === 'string' ? message.trim() || undefined : undefined
}

export function appendProcessingStatusLog(
  roomId: string,
  userEntity: MessageEntity | undefined,
  message: unknown,
  timestamp = new Date().toISOString(),
  source: MessageSource = 'optimistic',
): void {
  const trimmed = normalizeProcessingDetails(message)
  if (!trimmed || !userEntity) return

  const latestUserEntity = useMessageStore.getState().entities[userEntity.id] ?? userEntity
  const existing = latestUserEntity.processingStatusLogs ?? []
  if (existing.some((entry) => entry.message === trimmed)) return

  useMessageStore.getState().upsertMessage({
    id: latestUserEntity.id,
    roomId,
    messageType: 'user',
    content: latestUserEntity.content,
    senderName: latestUserEntity.senderName,
    timestamp: latestUserEntity.timestamp,
    processingStatusLogs: [
      ...existing,
      {
        id: `processing-log-${timestamp}-${existing.length}`,
        message: trimmed,
        timestamp,
      },
    ],
  }, source)
}

export function clearProcessingStatusLogs(
  roomId: string,
  userEntity: MessageEntity | undefined,
): void {
  if (!userEntity || (userEntity.processingStatusLogs?.length ?? 0) === 0) return

  useMessageStore.getState().upsertMessage({
    id: userEntity.id,
    roomId,
    messageType: 'user',
    content: userEntity.content,
    senderName: userEntity.senderName,
    timestamp: userEntity.timestamp,
    processingStatusLogs: [],
  }, 'optimistic')
}

export function clearCurrentProcessingStatusLogs(
  roomId: string,
  lifecycle: ProcessingLifecycle,
): void {
  const userEntity = findProcessingStatusUserEntity(roomId, {
    messageId: lifecycle.getMessageId(),
    clientRequestId: lifecycle.getPendingRunEventAck(),
    latestWithLogs: true,
  })
  clearProcessingStatusLogs(roomId, userEntity)
}
