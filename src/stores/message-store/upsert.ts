import { isTerminalState } from '@/lib/types/sse'
import { resolveDisplayType } from './resolve-display-type'
import type { MessageEntity, IncomingMessage, MessageSource, ArtifactData } from './types'

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
  const resolvedEphemeral = incoming.isEphemeral ?? existing?.isEphemeral ?? false

  // Merge first so displayType is computed from the *final* field values,
  // not the raw incoming (which may omit taskStatus, causing a false
  // agent-bubble resolution when the existing entity has input-required).
  const merged = mergeIncoming(existing, incoming)

  const displayType = resolveDisplayType({
    messageType: merged.messageType,
  })

  const entity: MessageEntity = {
    ...merged,
    displayType,
    source,
    sourceVersion: (existing?.sourceVersion ?? 0) + 1,
    updatedAt: Date.now(),
    createdAt: existing?.createdAt ?? Date.now(),
    isEphemeral: resolvedEphemeral,
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
      agentSource: incoming.agentSource,
      userId: incoming.userId,
      taskStatus: incoming.taskStatus ?? undefined,
      taskError: incoming.taskError,
      taskStatusMessage: incoming.taskStatusMessage,
      taskRequiresInput: incoming.taskRequiresInput,
      taskRequiresAuth: incoming.taskRequiresAuth,
      taskContent: incoming.taskContent,
      taskCreatedAt: incoming.taskCreatedAt,
      taskUpdatedAt: incoming.taskUpdatedAt,
      stepNumber: incoming.stepNumber,
      totalSteps: incoming.totalSteps,
      relatedMessageId: incoming.relatedMessageId,
      hitlRequestId: incoming.hitlRequestId,
      hitlPrompt: incoming.hitlPrompt,
      hitlPromptType: incoming.hitlPromptType,
      hitlChoices: incoming.hitlChoices,
      hitlExpiresAt: incoming.hitlExpiresAt,
      hitlResolved: incoming.hitlResolved,
      hitlGroupId: incoming.hitlGroupId,
      hitlGroupTotal: incoming.hitlGroupTotal,
      hitlGroupIndex: incoming.hitlGroupIndex,
      hitlUserAnswer: incoming.hitlUserAnswer,
      clientRequestId: incoming.clientRequestId,
      artifacts: incoming.artifacts,
      attachments: incoming.attachments,
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
    agentSource: incoming.agentSource !== undefined ? incoming.agentSource : existing.agentSource,
    userId: incoming.userId !== undefined ? incoming.userId : existing.userId,
    taskStatus: incoming.taskStatus !== undefined
      ? (incoming.taskStatus ?? undefined)  // null → undefined (clear the field)
      : existing.taskStatus,
    taskError: incoming.taskError !== undefined ? incoming.taskError : existing.taskError,
    taskStatusMessage: incoming.taskStatusMessage !== undefined ? incoming.taskStatusMessage : existing.taskStatusMessage,
    taskRequiresInput: incoming.taskRequiresInput !== undefined ? incoming.taskRequiresInput : existing.taskRequiresInput,
    taskRequiresAuth: incoming.taskRequiresAuth !== undefined ? incoming.taskRequiresAuth : existing.taskRequiresAuth,
    taskContent: incoming.taskContent !== undefined ? incoming.taskContent : existing.taskContent,
    taskCreatedAt: incoming.taskCreatedAt !== undefined ? incoming.taskCreatedAt : existing.taskCreatedAt,
    taskUpdatedAt: incoming.taskUpdatedAt !== undefined ? incoming.taskUpdatedAt : existing.taskUpdatedAt,
    stepNumber: incoming.stepNumber !== undefined ? incoming.stepNumber : existing.stepNumber,
    totalSteps: incoming.totalSteps !== undefined ? incoming.totalSteps : existing.totalSteps,
    relatedMessageId: incoming.relatedMessageId !== undefined ? incoming.relatedMessageId : existing.relatedMessageId,
    hitlRequestId: incoming.hitlRequestId !== undefined ? incoming.hitlRequestId : existing.hitlRequestId,
    hitlPrompt: incoming.hitlPrompt !== undefined ? incoming.hitlPrompt : existing.hitlPrompt,
    hitlPromptType: incoming.hitlPromptType !== undefined ? incoming.hitlPromptType : existing.hitlPromptType,
    hitlChoices: incoming.hitlChoices !== undefined ? incoming.hitlChoices : existing.hitlChoices,
    hitlExpiresAt: incoming.hitlExpiresAt !== undefined ? incoming.hitlExpiresAt : existing.hitlExpiresAt,
    hitlResolved: incoming.hitlResolved !== undefined ? incoming.hitlResolved : existing.hitlResolved,
    hitlGroupId: incoming.hitlGroupId !== undefined ? incoming.hitlGroupId : existing.hitlGroupId,
    hitlGroupTotal: incoming.hitlGroupTotal !== undefined ? incoming.hitlGroupTotal : existing.hitlGroupTotal,
    hitlGroupIndex: incoming.hitlGroupIndex !== undefined ? incoming.hitlGroupIndex : existing.hitlGroupIndex,
    hitlUserAnswer: incoming.hitlUserAnswer !== undefined ? incoming.hitlUserAnswer : existing.hitlUserAnswer,
    clientRequestId: incoming.clientRequestId !== undefined ? incoming.clientRequestId : existing.clientRequestId,
    artifacts: incoming.artifacts !== undefined ? incoming.artifacts : existing.artifacts,
    attachments: incoming.attachments !== undefined ? incoming.attachments : existing.attachments,
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

function arraysShallowEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true
  if (a == null && b == null) return true
  if (!Array.isArray(a) || !Array.isArray(b)) return a === b
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false
  }
  return true
}

/**
 * Detect whether an incoming update changes any rendering-visible fields.
 * Returns true if nothing visible changed — the store should skip this update.
 */
export function isNoOpUpdate(
  existing: MessageEntity,
  incoming: IncomingMessage,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  _source: MessageSource,
): boolean {
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
    existing.hitlResolved      === coalesce(incoming.hitlResolved, existing.hitlResolved) &&
    existing.hitlPrompt        === coalesce(incoming.hitlPrompt, existing.hitlPrompt) &&
    existing.hitlRequestId     === coalesce(incoming.hitlRequestId, existing.hitlRequestId) &&
    existing.hitlPromptType    === coalesce(incoming.hitlPromptType, existing.hitlPromptType) &&
    existing.hitlExpiresAt     === coalesce(incoming.hitlExpiresAt, existing.hitlExpiresAt) &&
    arraysShallowEqual(existing.hitlChoices, coalesce(incoming.hitlChoices, existing.hitlChoices)) &&
    existing.hitlGroupId       === coalesce(incoming.hitlGroupId, existing.hitlGroupId) &&
    existing.hitlGroupTotal    === coalesce(incoming.hitlGroupTotal, existing.hitlGroupTotal) &&
    existing.hitlGroupIndex    === coalesce(incoming.hitlGroupIndex, existing.hitlGroupIndex) &&
    existing.hitlUserAnswer    === coalesce(incoming.hitlUserAnswer, existing.hitlUserAnswer) &&
    existing.clientRequestId   === coalesce(incoming.clientRequestId, existing.clientRequestId) &&
    existing.isEphemeral       === (incoming.isEphemeral ?? existing.isEphemeral) &&
    existing.artifacts         === coalesce(incoming.artifacts, existing.artifacts) &&
    existing.attachments       === coalesce(incoming.attachments, existing.attachments)
  )
}

/**
 * Build a sorted array of message IDs from the entities map.
 * Sort order: timestamp (primary), stepNumber within the same workflow
 * (same relatedMessageId and timestamps within 60s), then message ID
 * for stability.
 */
export function buildSortedIds(entities: Record<string, MessageEntity>): string[] {
  return Object.values(entities)
    .sort((a, b) => {
      const aTime = new Date(a.timestamp).getTime()
      const bTime = new Date(b.timestamp).getTime()
      const timeDiff = aTime - bTime

      // Step-number sorting only applies within the SAME workflow
      // (same relatedMessageId) and only when timestamps are close.
      if (
        a.stepNumber != null && b.stepNumber != null &&
        a.relatedMessageId && b.relatedMessageId &&
        a.relatedMessageId === b.relatedMessageId &&
        Math.abs(timeDiff) < 60_000
      ) {
        const stepDiff = a.stepNumber - b.stepNumber
        if (stepDiff !== 0) return stepDiff
      }

      if (timeDiff !== 0) return timeDiff

      // Tiebreakers: step number within same workflow, then ID
      if (
        a.relatedMessageId && b.relatedMessageId &&
        a.relatedMessageId === b.relatedMessageId
      ) {
        const stepA = a.stepNumber ?? Infinity
        const stepB = b.stepNumber ?? Infinity
        if (stepA !== stepB) return stepA - stepB
      }

      return a.id.localeCompare(b.id)
    })
    .map(e => e.id)
}

function isTextOnlyArtifact(a: ArtifactData): boolean {
  return a.parts.length > 0 && a.parts.every(p => p.kind === 'text')
}

/**
 * Merge an incoming artifact into an existing artifact list.
 * - If append=true and artifact already exists, append new parts.
 * - Otherwise replace the existing artifact with same ID.
 * - New artifact IDs are appended to the list.
 * - Same-name text-only artifacts with different IDs are deduplicated
 *   (keeps only the latest) to contain misbehaving agents that emit a
 *   new artifact ID per streaming token instead of using append semantics.
 */
export function mergeArtifacts(
  existing: ArtifactData[] | undefined,
  incoming: ArtifactData,
  append: boolean = false,
): ArtifactData[] {
  const list = existing ? [...existing] : []
  const idx = list.findIndex(a => a.artifactId === incoming.artifactId)

  if (idx >= 0) {
    if (append) {
      const merged = mergeTextParts([...list[idx].parts], incoming.parts)
      list[idx] = {
        ...list[idx],
        parts: merged,
        isStreaming: incoming.isStreaming ?? list[idx].isStreaming,
      }
    } else {
      list[idx] = incoming
    }
  } else {
    // Dedup: if incoming is a text-only artifact with a name that matches
    // an existing text-only artifact, replace it instead of pushing a new entry.
    const sameNameIdx = incoming.name && isTextOnlyArtifact(incoming)
      ? list.findIndex(a => a.name === incoming.name && isTextOnlyArtifact(a))
      : -1

    if (sameNameIdx >= 0) {
      list[sameNameIdx] = incoming
    } else {
      list.push(incoming)
    }
  }

  return list
}

/**
 * Extract the combined text from all text-only artifacts.
 * Returns the longest single artifact's text (not concatenated across
 * artifacts) to handle cumulative-snapshot patterns where each artifact
 * contains all prior text plus the latest token.
 */
export function extractTextFromArtifacts(artifacts: ArtifactData[]): string {
  let longest = ''
  for (const a of artifacts) {
    if (!isTextOnlyArtifact(a)) continue
    const text = a.parts.map(p => p.text || '').join('')
    if (text.length > longest.length) longest = text
  }
  return longest
}

/**
 * When appending artifact parts during streaming, concatenate consecutive
 * text parts into the trailing text part instead of creating separate
 * `<p>` elements per token (which causes the one-word-per-line glitch).
 */
function mergeTextParts(
  existingParts: ArtifactData['parts'],
  newParts: ArtifactData['parts'],
): ArtifactData['parts'] {
  const result = [...existingParts]
  for (const part of newParts) {
    const last = result[result.length - 1]
    if (part.kind === 'text' && last?.kind === 'text') {
      result[result.length - 1] = { ...last, text: (last.text || '') + (part.text || '') }
    } else {
      result.push(part)
    }
  }
  return result
}
