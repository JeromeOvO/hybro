import type { MutableRefObject } from 'react'
import type { TaskState } from '@/lib/types/sse'
import type { HitlPendingRequest } from '@/lib/api/hitl'
import { useMessageStore } from '@/stores/message-store'
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

  for (const req of requests) {
    pendingMessageIds.add(req.message_id)
    let resolvedName = req.agent_name
    if (!resolvedName && req.agent_id) {
      resolvedName = await deps.getAgentName(req.agent_id)
    }
    store.upsertMessage({
      id: req.message_id,
      roomId,
      messageType: 'agent',
      content: req.prompt || '',
      senderName: resolvedName || 'Agent',
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

  return pendingMessageIds
}
