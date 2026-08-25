import type { MutableRefObject } from 'react'
import type { HitlPendingRequest } from '@/lib/api/hitl'
import { fetchPendingHitlRequests } from '@/lib/api/hitl'
import { useMessageStore } from '@/stores/message-store'
import { buildPendingHitlIncomingMessage } from '@/lib/hitl/hitl-message-projection'
import type { HydrateRoomAgentResolver } from './types'

export async function overlayPendingHitlRequests(
  roomId: string,
  requests: HitlPendingRequest[],
  deps: HydrateRoomAgentResolver & {
    hitlRequestIndex: MutableRefObject<Map<string, string>>
  },
): Promise<Set<string>> {
  const pendingMessageIds = new Set<string>()
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
    pendingMessageIds.add(req.message_id)
    store.upsertMessage(buildPendingHitlIncomingMessage({
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
    }), 'sse')
    deps.hitlRequestIndex.current.set(req.request_id, req.message_id)
  }

  return pendingMessageIds
}

/**
 * Initial hydration only: mark DB-hydrated input-required agents as resolved
 * when they are not in the pending HITL set.
 */
export function markResolvedHitlFromHydrationBatch(
  targetRoomId: string,
  hydratedIds: ReadonlySet<string>,
  pendingMessageIds: ReadonlySet<string>,
): void {
  const store = useMessageStore.getState()
  if (store.roomId !== targetRoomId) return

  for (const entity of Object.values(store.entities)) {
    if (
      entity.roomId === targetRoomId &&
      entity.taskStatus === 'input-required' &&
      hydratedIds.has(entity.id) &&
      !pendingMessageIds.has(entity.id)
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
  pendingMessageIds: ReadonlySet<string>,
): void {
  const store = useMessageStore.getState()
  if (store.roomId !== targetRoomId) return

  for (const entity of Object.values(store.entities)) {
    if (entity.roomId !== targetRoomId || !entity.hitlRequestId) continue
    if (pendingMessageIds.has(entity.id)) continue
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

  let pendingMessageIds = new Set<string>()
  if (hitlRes.requests?.length) {
    pendingMessageIds = await overlayPendingHitlRequests(roomId, hitlRes.requests, {
      getAgentName,
      getAgentSource,
      hitlRequestIndex,
    })
  }

  if (hydratedIdsForResolve) {
    markResolvedHitlFromHydrationBatch(roomId, hydratedIdsForResolve, pendingMessageIds)
  } else {
    clearStaleHitlNotInPending(roomId, pendingMessageIds)
  }

  return pendingMessageIds
}
