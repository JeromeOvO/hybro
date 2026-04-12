import type { MutableRefObject } from 'react'
import type { TaskState } from '@/lib/types/sse'
import type { HitlPendingRequest } from '@/lib/api/hitl'
import { useMessageStore } from '@/stores/message-store'
import { useTurnEventStore } from '@/stores/turn-event-store'
import type { TurnEvent } from '@/stores/turn-event-store/types'
import { normalizeTimestampOrNow } from '@/lib/time'

export async function overlayPendingHitlRequests(
  roomId: string,
  requests: HitlPendingRequest[],
  deps: {
    getAgentName: (id: string) => Promise<string>
    getAgentSource: (id: string | undefined) => 'cloud' | 'hub' | undefined
    hitlRequestIndex: MutableRefObject<Map<string, string>>
  },
): Promise<Set<string>> {
  const pendingMessageIds = new Set<string>()
  const store = useMessageStore.getState()

  // Resolve names upfront so both stores get the same resolved value
  const resolved: Array<{ req: HitlPendingRequest; name: string }> = []
  for (const req of requests) {
    let resolvedName = req.agent_name
    if (!resolvedName && req.agent_id) {
      resolvedName = await deps.getAgentName(req.agent_id)
    }
    resolved.push({ req, name: resolvedName || 'Agent' })
  }

  // Phase 1: Write to message-store (legacy path)
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
    }, 'sse')
    deps.hitlRequestIndex.current.set(req.request_id, req.message_id)
  }

  // Phase 2: Inject into turn-event-store so ComposerShell picks up
  // pending HITLs after SSE reconnect. Idempotent — if the turn-based
  // timeline isn't active, these events sit unused.
  const turnStore = useTurnEventStore.getState()
  if (turnStore.orderedTurnIds.length > 0) {
    // Find last active (non-terminal) turn — HITL only fires during processing
    let activeTurnId: string | undefined
    for (let i = turnStore.orderedTurnIds.length - 1; i >= 0; i--) {
      const id = turnStore.orderedTurnIds[i]
      const log = turnStore.turnLogs.get(id)
      if (log && !log.isTerminal()) {
        activeTurnId = id
        break
      }
    }

    if (activeTurnId) {
      for (const { req, name } of resolved) {
        const hitlEvent: TurnEvent = {
          eventId: `hitl-restore-${req.request_id}`,
          turnId: activeTurnId,
          seq: 0,
          ts: req.created_at ? new Date(req.created_at).getTime() : Date.now(),
          type: 'hitl_requested',
          hitlId: req.request_id,
          source: req.source,
          agentName: name,
          prompt: req.prompt,
          promptType: req.prompt_type,
          choices: req.choices ?? undefined,
          groupId: req.group_id ?? undefined,
          groupTotal: req.group_total ?? undefined,
          groupIndex: req.group_index ?? undefined,
        }
        turnStore.append(activeTurnId, hitlEvent)
      }
    }
  }

  return pendingMessageIds
}
