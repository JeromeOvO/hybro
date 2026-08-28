import type { MutableRefObject } from 'react'
import type { HitlPendingRequest } from '@/lib/api/hitl'
import { fetchPendingHitlRequests } from '@/lib/api/hitl'
import { useMessageStore } from '@/stores/message-store'
import {
  buildPendingHitlIncomingMessage,
  hitlRequestKey,
} from '@/lib/hitl/hitl-message-projection'
import type { HydrateRoomAgentResolver } from './types'

export async function overlayPendingHitlRequests(
  roomId: string,
  requests: HitlPendingRequest[],
  deps: HydrateRoomAgentResolver & {
    hitlRequestIndex: MutableRefObject<Map<string, string>>
  },
): Promise<Set<string>> {
  const pendingRequestIds = new Set<string>()
  const store = useMessageStore.getState()

  const resolved: Array<{ req: HitlPendingRequest; name: string }> = []
  for (const req of requests) {
    let resolvedName = req.agent_name
    if (!resolvedName && req.agent_id) {
      resolvedName = await deps.getAgentName(req.agent_id)
    }
    resolved.push({ req, name: resolvedName || 'Agent' })
  }

  for (const { req, name } of resolved) {
    const requestKey = hitlRequestKey(req.interaction_id, req.request_id)
    const incoming = buildPendingHitlIncomingMessage({
      roomId,
      messageId: req.message_id,
      requestId: req.request_id,
      source: req.source,
      prompt: req.prompt,
      promptType: req.prompt_type,
      choices: req.choices,
      timestamp: req.created_at,
      agentId: req.agent_id,
      agentName: name,
      agentSource: deps.getAgentSource(req.agent_id),
      expiresAt: req.expires_at,
      interactionId: req.interaction_id,
      interactionStatus: req.interaction_status,
      interactionVersion: req.interaction_version,
      applicationStatus: req.application_status,
      applicationError: req.application_error,
      groupId: req.interaction_id,
      groupTotal: req.question_count,
      groupIndex: req.question_index,
      stepNumber: undefined,
      totalSteps: undefined,
      relatedMessageId: req.related_message_id,
      clientRequestId: req.client_request_id,
    })
    const indexedId = deps.hitlRequestIndex.current.get(requestKey)
    const existingExact = store.entities[incoming.id]
      ?? (indexedId ? store.entities[indexedId] : undefined)
    const sameIdentity = Boolean(
      existingExact
      && existingExact.hitlRequestId === req.request_id
      && (existingExact.hitlInteractionId ?? existingExact.hitlGroupId)
        === req.interaction_id
    )
    const incomingIsNewer = Boolean(
      req.interaction_version !== undefined
      && req.interaction_version !== null
      && existingExact?.hitlInteractionVersion !== undefined
      && req.interaction_version > existingExact.hitlInteractionVersion
    )
    const existingIsAnsweredOrApplying = Boolean(
      sameIdentity
      && !incomingIsNewer
      && (
        existingExact?.hitlResolved === true
        || existingExact?.hitlUserAnswer
        || ['answers_recorded', 'applying', 'applied', 'responded'].includes(
          existingExact?.hitlInteractionStatus ?? '',
        )
        || ['applying', 'applied'].includes(
          existingExact?.hitlApplicationStatus ?? '',
        )
      )
    )
    if (existingIsAnsweredOrApplying) {
      // REST pending is recovery overlay, not authority to regress canonical
      // response/application evidence. Keep applying rows indexed until the
      // server removes them; fully resolved rows are no longer actionable.
      if (existingExact && existingExact.hitlResolved !== true) {
        pendingRequestIds.add(requestKey)
        deps.hitlRequestIndex.current.set(requestKey, existingExact.id)
      }
      continue
    }
    pendingRequestIds.add(requestKey)
    const legacyProjection = store.entities[req.message_id]
    if (
      legacyProjection
      && legacyProjection.id !== incoming.id
      && legacyProjection.hitlRequestId === req.request_id
      && legacyProjection.hitlResolved !== true
    ) {
      store.upsertMessage({
        id: legacyProjection.id,
        roomId,
        messageType: 'agent',
        content: legacyProjection.content,
        senderName: legacyProjection.senderName,
        timestamp: legacyProjection.timestamp,
        hitlResolved: true,
      }, 'sse')
    }
    store.upsertMessage(incoming, 'sse')
    deps.hitlRequestIndex.current.set(requestKey, incoming.id)
  }

  return pendingRequestIds
}

/**
 * Initial hydration only: mark DB-hydrated input-required agents as resolved
 * when they are not in the pending HITL set.
 */
export function markResolvedHitlFromHydrationBatch(
  targetRoomId: string,
  hydratedIds: ReadonlySet<string>,
  pendingRequestIds: ReadonlySet<string>,
): void {
  const store = useMessageStore.getState()
  if (store.roomId !== targetRoomId) return

  for (const entity of Object.values(store.entities)) {
    if (
      entity.roomId === targetRoomId &&
      entity.taskStatus === 'input-required' &&
      hydratedIds.has(entity.id) &&
      (!entity.hitlRequestId || !pendingRequestIds.has(hitlRequestKey(
        entity.hitlInteractionId,
        entity.hitlRequestId,
      )))
    ) {
      store.upsertMessage({
        id: entity.id,
        roomId: targetRoomId,
        messageType: 'agent',
        content: entity.content,
        senderName: entity.senderName,
        timestamp: entity.timestamp,
        hitlResolved: true,
      }, 'sse')
    }
  }
}

/**
 * Live applying refresh: drop local *applying* HITL projections that are no
 * longer in the server pending set so the composer can leave "Applying your
 * answers" without a full page reload. Open input-required prompts are left
 * alone here — a brief empty pending window must not clear a still-open UI.
 * Initial hydration still resolves absent open HITL via
 * markResolvedHitlFromHydrationBatch.
 */
export function clearStaleHitlNotInPending(
  targetRoomId: string,
  pendingRequestIds: ReadonlySet<string>,
): void {
  const store = useMessageStore.getState()
  if (store.roomId !== targetRoomId) return

  for (const entity of Object.values(store.entities)) {
    if (entity.roomId !== targetRoomId || !entity.hitlRequestId) continue
    if (pendingRequestIds.has(hitlRequestKey(
      entity.hitlInteractionId,
      entity.hitlRequestId,
    ))) continue
    if (entity.hitlResolved === true) continue

    const wasApplying =
      entity.hitlApplicationStatus === 'applying'
      || entity.hitlInteractionStatus === 'applying'
      || entity.hitlInteractionStatus === 'answers_recorded'

    if (!wasApplying) continue

    store.upsertMessage({
      id: entity.id,
      roomId: targetRoomId,
      messageType: 'agent',
      content: entity.content,
      senderName: entity.senderName,
      timestamp: entity.timestamp,
      hitlResolved: true,
      hitlInteractionStatus: 'applied',
      hitlApplicationStatus: 'applied',
    }, 'sse')
  }
}

export interface OverlayHitlOptions extends HydrateRoomAgentResolver {
  roomId: string
  getToken?: () => Promise<string | null>
  hitlRequestIndex: MutableRefObject<Map<string, string>>
  /** When set, run markResolvedHitlFromHydrationBatch after overlay (initial load). */
  hydratedIdsForResolve?: ReadonlySet<string>
}

export async function overlayHitlForRoom(
  options: OverlayHitlOptions,
): Promise<Set<string>> {
  const { roomId, getToken, hitlRequestIndex, hydratedIdsForResolve, getAgentName, getAgentSource } =
    options

  const hitlRes = await fetchPendingHitlRequests(roomId, getToken)
  const store = useMessageStore.getState()
  if (store.roomId !== roomId) return new Set()

  let pendingRequestIds = new Set<string>()
  if (hitlRes.requests?.length) {
    pendingRequestIds = await overlayPendingHitlRequests(roomId, hitlRes.requests, {
      getAgentName,
      getAgentSource,
      hitlRequestIndex,
    })
  }

  for (const requestId of hitlRequestIndex.current.keys()) {
    if (!pendingRequestIds.has(requestId)) {
      hitlRequestIndex.current.delete(requestId)
    }
  }

  if (hydratedIdsForResolve) {
    markResolvedHitlFromHydrationBatch(roomId, hydratedIdsForResolve, pendingRequestIds)
  } else {
    clearStaleHitlNotInPending(roomId, pendingRequestIds)
  }

  return pendingRequestIds
}
