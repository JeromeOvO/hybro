import type { MutableRefObject } from 'react'
import type { TaskState } from '@/lib/types/sse'
import type { HitlPendingRequest } from '@/lib/api/hitl'
import { fetchPendingHitlRequests } from '@/lib/api/hitl'
import { useMessageStore } from '@/stores/message-store'
import { normalizeTimestampOrNow } from '@/lib/time'
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
    store.upsertMessage({
      id: req.message_id,
      roomId,
      messageType: 'agent',
      content: req.prompt || '',
      senderName: name,
      timestamp: normalizeTimestampOrNow(req.created_at),
      agentId: req.agent_id,
      agentSource: deps.getAgentSource(req.agent_id),
      taskStatus: 'input-required' as TaskState,
      hitlRequestId: req.request_id,
      hitlPrompt: req.prompt,
      hitlPromptType: req.prompt_type || 'text',
      hitlChoices: req.choices,
      hitlExpiresAt: req.expires_at,
      hitlResolved: false,
      hitlGroupId: req.group_id ?? undefined,
      hitlGroupTotal: req.group_total ?? undefined,
      hitlGroupIndex: req.group_index ?? undefined,
      relatedMessageId: req.related_message_id ?? undefined,
      clientRequestId: req.client_request_id ?? undefined,
    }, 'sse')
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
  }

  return pendingMessageIds
}
