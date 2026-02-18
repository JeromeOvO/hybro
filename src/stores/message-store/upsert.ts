import type { TaskState } from '@/lib/types/sse'
import { isTerminalState } from '@/lib/types/sse'
import { resolveDisplayType } from './resolve-display-type'
import type { MessageEntity, IncomingMessage, MessageSource } from './types'

/**
 * Core upsert logic, extracted so it can be used by both single and batch writes.
 * Returns null if the update was rejected or is a no-op.
 *
 * Conflict Resolution Rules:
 *   1. Never downgrade a terminal task status.
 *   2. SSE wins over DB for non-terminal states.
 *   3. DB wins for terminal states (handled implicitly — rules 1 & 2 only block, not this).
 *   4. Skip no-op updates.
 *   5. Never overwrite ephemeral messages from DB.
 */
export function applyUpsert(
  entities: Record<string, MessageEntity>,
  orderedIds: string[],
  incoming: IncomingMessage,
  source: MessageSource,
): { entities: Record<string, MessageEntity>; idsChanged: boolean } | null {
  const existing = entities[incoming.id]

  // ── Rule 5: Never overwrite ephemeral from DB ──
  if (existing?.isEphemeral && source === 'db') {
    return null
  }

  if (existing) {
    // ── Rule 1: Never downgrade terminal status ──
    if (
      existing.taskStatus &&
      isTerminalState(existing.taskStatus) &&
      incoming.taskStatus &&
      !isTerminalState(incoming.taskStatus)
    ) {
      return null
    }

    // ── Rule 2: SSE wins over DB for non-terminal ──
    if (
      existing.source === 'sse' &&
      source === 'db' &&
      existing.taskStatus &&
      !isTerminalState(existing.taskStatus)
    ) {
      return null
    }

    // ── Rule 4: Skip no-op updates ──
    if (isNoOpUpdate(existing, incoming, source)) {
      return null
    }
  }

  // ── Build the new entity ──
  const displayType = resolveDisplayType({
    messageType: incoming.messageType,
    taskStatus: incoming.taskStatus,
    content: incoming.content,
    isEphemeral: incoming.isEphemeral,
  })

  // Merge: preserve fields not present in incoming, overlay incoming fields,
  // then set computed/provenance fields.
  // For nullable fields (taskError, taskStatusMessage), we must distinguish
  // between `undefined` (not provided → keep existing) and `null` (explicitly clear).
  const merged = mergeIncoming(existing, incoming)

  const entity: MessageEntity = {
    ...merged,
    displayType,
    source,
    sourceVersion: (existing?.sourceVersion ?? 0) + 1,
    updatedAt: Date.now(),
    createdAt: existing?.createdAt ?? Date.now(),
    isEphemeral: incoming.isEphemeral ?? existing?.isEphemeral ?? false,
  }

  const newEntities = { ...entities, [entity.id]: entity }
  const idsChanged = !existing // new message added

  return { entities: newEntities, idsChanged }
}

/**
 * Merge incoming fields onto existing entity, handling undefined vs null correctly.
 * - undefined in incoming → preserve existing value
 * - null in incoming → explicitly set to null
 * - value in incoming → overwrite
 */
function mergeIncoming(
  existing: MessageEntity | undefined,
  incoming: IncomingMessage,
): Omit<MessageEntity, 'displayType' | 'source' | 'sourceVersion' | 'updatedAt' | 'createdAt' | 'isEphemeral'> {
  if (!existing) {
    return {
      id: incoming.id,
      roomId: incoming.roomId,
      messageType: incoming.messageType,
      content: incoming.content,
      senderName: incoming.senderName,
      timestamp: incoming.timestamp,
      agentId: incoming.agentId,
      userId: incoming.userId,
      taskStatus: incoming.taskStatus,
      taskError: incoming.taskError,
      taskStatusMessage: incoming.taskStatusMessage,
      taskRequiresInput: incoming.taskRequiresInput,
      taskRequiresAuth: incoming.taskRequiresAuth,
      taskContent: incoming.taskContent,
      taskCreatedAt: incoming.taskCreatedAt,
      taskUpdatedAt: incoming.taskUpdatedAt,
      stepNumber: incoming.stepNumber,
      totalSteps: incoming.totalSteps,
    }
  }

  return {
    id: incoming.id,
    roomId: incoming.roomId,
    messageType: incoming.messageType,
    content: incoming.content,
    senderName: incoming.senderName,
    timestamp: incoming.timestamp,
    agentId: incoming.agentId !== undefined ? incoming.agentId : existing.agentId,
    userId: incoming.userId !== undefined ? incoming.userId : existing.userId,
    taskStatus: incoming.taskStatus !== undefined ? incoming.taskStatus : existing.taskStatus,
    taskError: incoming.taskError !== undefined ? incoming.taskError : existing.taskError,
    taskStatusMessage: incoming.taskStatusMessage !== undefined ? incoming.taskStatusMessage : existing.taskStatusMessage,
    taskRequiresInput: incoming.taskRequiresInput !== undefined ? incoming.taskRequiresInput : existing.taskRequiresInput,
    taskRequiresAuth: incoming.taskRequiresAuth !== undefined ? incoming.taskRequiresAuth : existing.taskRequiresAuth,
    taskContent: incoming.taskContent !== undefined ? incoming.taskContent : existing.taskContent,
    taskCreatedAt: incoming.taskCreatedAt !== undefined ? incoming.taskCreatedAt : existing.taskCreatedAt,
    taskUpdatedAt: incoming.taskUpdatedAt !== undefined ? incoming.taskUpdatedAt : existing.taskUpdatedAt,
    stepNumber: incoming.stepNumber !== undefined ? incoming.stepNumber : existing.stepNumber,
    totalSteps: incoming.totalSteps !== undefined ? incoming.totalSteps : existing.totalSteps,
  }
}

/**
 * Helper: if incoming field is undefined, treat as "not changing" → use existing.
 * If incoming field is explicitly null or a value, it IS a change candidate.
 * This distinguishes "field not provided" from "field explicitly set to null".
 */
function coalesce<T>(incomingVal: T | undefined, existingVal: T): T {
  return incomingVal === undefined ? existingVal : incomingVal
}

/**
 * Detect whether an incoming update changes any rendering-visible fields.
 * Returns true if nothing visible changed — the store should skip this update.
 *
 * Gap 18: includes taskStatusMessage, taskContent, taskRequiresInput, and
 * taskRequiresAuth which are all displayed in the TaskStatusMessage component.
 */
export function isNoOpUpdate(
  existing: MessageEntity,
  incoming: IncomingMessage,
  _source: MessageSource,
): boolean {
  const incomingDisplayType = resolveDisplayType({
    messageType: incoming.messageType ?? existing.messageType,
    taskStatus: coalesce(incoming.taskStatus, existing.taskStatus) as TaskState | undefined,
    content: incoming.content ?? existing.content,
  })

  return (
    existing.content           === coalesce(incoming.content, existing.content) &&
    existing.taskStatus        === coalesce(incoming.taskStatus, existing.taskStatus) &&
    existing.taskError         === coalesce(incoming.taskError, existing.taskError) &&
    existing.taskStatusMessage === coalesce(incoming.taskStatusMessage, existing.taskStatusMessage) &&
    existing.senderName        === coalesce(incoming.senderName, existing.senderName) &&
    existing.stepNumber        === coalesce(incoming.stepNumber, existing.stepNumber) &&
    existing.totalSteps        === coalesce(incoming.totalSteps, existing.totalSteps) &&
    existing.taskContent       === coalesce(incoming.taskContent, existing.taskContent) &&
    existing.taskRequiresInput === coalesce(incoming.taskRequiresInput, existing.taskRequiresInput) &&
    existing.taskRequiresAuth  === coalesce(incoming.taskRequiresAuth, existing.taskRequiresAuth) &&
    existing.displayType       === incomingDisplayType
  )
}

/**
 * Build a sorted array of message IDs from the entities map.
 * Sort order: timestamp (primary), stepNumber within same workflow batch
 * (timestamps within 60s), then message ID for stability.
 */
export function buildSortedIds(entities: Record<string, MessageEntity>): string[] {
  return Object.values(entities)
    .sort((a, b) => {
      const aTime = new Date(a.timestamp).getTime()
      const bTime = new Date(b.timestamp).getTime()
      const timeDiff = aTime - bTime

      // Within the same workflow batch (< 60s apart), sort by step number
      if (
        a.stepNumber != null && b.stepNumber != null &&
        Math.abs(timeDiff) < 60_000
      ) {
        const stepDiff = a.stepNumber - b.stepNumber
        if (stepDiff !== 0) return stepDiff
      }

      if (timeDiff !== 0) return timeDiff

      // Tiebreakers
      const stepA = a.stepNumber ?? Infinity
      const stepB = b.stepNumber ?? Infinity
      if (stepA !== stepB) return stepA - stepB

      return a.id.localeCompare(b.id)
    })
    .map(e => e.id)
}
