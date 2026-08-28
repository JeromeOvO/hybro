import { beforeEach, describe, expect, it, vi } from 'vitest'
import { overlayHitlForRoom } from '@/lib/room-sync/hitl-overlay'
import { useMessageStore } from '@/stores/message-store'
import { fetchPendingHitlRequests, type HitlPendingRequest } from '@/lib/api/hitl'
import { selectPendingHitls } from '@/lib/selectors/select-hitl'
import {
  hitlQuestionEntityId,
  hitlRequestKey,
} from '@/lib/hitl/hitl-message-projection'

vi.mock('@/lib/api/hitl', () => ({
  fetchPendingHitlRequests: vi.fn(),
}))

describe('degraded HITL pending hydration', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    const store = useMessageStore.getState()
    store.clearRoom()
    store.setRoom('room-1')
    store.upsertMessage({
      id: 'agent-message-1',
      roomId: 'room-1',
      messageType: 'agent',
      content: 'Which market?',
      senderName: 'Broker',
      timestamp: '2027-01-01T00:00:00Z',
      taskStatus: 'input-required',
      hitlRequestId: 'request-1',
      hitlPrompt: 'Which market?',
      hitlPromptType: 'text',
      hitlResolved: false,
    }, 'db')
  })

  it('preserves unresolved interactions when the authoritative pending read fails', async () => {
    vi.mocked(fetchPendingHitlRequests).mockRejectedValue(new Error('database unavailable'))

    await expect(overlayHitlForRoom({
      roomId: 'room-1',
      hitlRequestIndex: { current: new Map() },
      getAgentName: async () => 'Broker',
      getAgentSource: () => 'cloud',
      hydratedIdsForResolve: new Set(['agent-message-1']),
    })).rejects.toThrow('database unavailable')

    expect(useMessageStore.getState().entities['agent-message-1'].hitlResolved).toBe(false)
  })

  it('restores delivery-uncertain interactions as visible nonterminal state', async () => {
    vi.mocked(fetchPendingHitlRequests).mockResolvedValue({
      requests: [{
        request_id: 'request-1',
        message_id: 'agent-message-1',
        source: 'agent',
        agent_name: 'Broker',
        prompt: 'Which market?',
        prompt_type: 'text',
        status: 'pending',
        created_at: '2027-01-01T00:00:00Z',
        interaction_id: 'interaction-1',
        interaction_status: 'delivery_uncertain',
        application_status: 'delivery_uncertain',
        application_error: 'Answer delivery is uncertain',
        client_request_id: 'client-1',
      }],
    })

    await overlayHitlForRoom({
      roomId: 'room-1',
      hitlRequestIndex: { current: new Map() },
      getAgentName: async () => 'Broker',
      getAgentSource: () => 'cloud',
    })

    const store = useMessageStore.getState()
    const pending = selectPendingHitls('room-1', store.entities, store.orderedIds)
    expect(pending).toHaveLength(1)
    expect(pending[0]).toMatchObject({
      interactionId: 'interaction-1',
      lifecycleState: 'delivery_uncertain',
      errorMessage: 'Answer delivery is uncertain',
      clientRequestId: 'client-1',
    })
  })

  it('restores and independently reconciles sibling questions sharing one message', async () => {
    const store = useMessageStore.getState()
    store.clearRoom()
    store.setRoom('room-1')
    store.upsertMessage({
      id: 'agent-message-1', roomId: 'room-1', messageType: 'agent', content: '',
      senderName: 'Broker', timestamp: '2027-01-01T00:00:00Z',
      taskStatus: 'input-required', relatedMessageId: 'user-1', clientRequestId: 'client-1',
    }, 'db')
    const requests: HitlPendingRequest[] = [
      {
        request_id: 'security_training', message_id: 'agent-message-1', source: 'agent' as const,
        agent_name: 'Broker', prompt: 'Is training in place?', prompt_type: 'confirmation',
        status: 'pending' as const, created_at: '2027-01-01T00:00:00Z',
        interaction_id: 'interaction-1', interaction_status: 'open',
        question_count: 2, question_index: 0, related_message_id: 'user-1',
        client_request_id: 'client-1',
      },
      {
        request_id: 'cloud_providers', message_id: 'agent-message-1', source: 'agent' as const,
        agent_name: 'Broker', prompt: 'Which cloud providers?', prompt_type: 'text',
        status: 'pending' as const, created_at: '2027-01-01T00:00:00Z',
        interaction_id: 'interaction-1', interaction_status: 'open',
        question_count: 2, question_index: 1, related_message_id: 'user-1',
        client_request_id: 'client-1',
      },
    ]
    vi.mocked(fetchPendingHitlRequests).mockResolvedValue({ requests })
    const index = { current: new Map<string, string>() }

    const pendingIds = await overlayHitlForRoom({
      roomId: 'room-1', hitlRequestIndex: index,
      getAgentName: async () => 'Broker', getAgentSource: () => 'cloud',
    })

    const trainingKey = hitlRequestKey('interaction-1', 'security_training')
    const providersKey = hitlRequestKey('interaction-1', 'cloud_providers')
    const trainingId = hitlQuestionEntityId(
      'agent-message-1', 'interaction-1', 'security_training', 2,
    )
    const providersId = hitlQuestionEntityId(
      'agent-message-1', 'interaction-1', 'cloud_providers', 2,
    )
    expect(pendingIds).toEqual(new Set([trainingKey, providersKey]))
    expect(index.current).toEqual(new Map([
      [trainingKey, trainingId],
      [providersKey, providersId],
    ]))
    const restored = useMessageStore.getState()
    expect(selectPendingHitls('room-1', restored.entities, restored.orderedIds)).toHaveLength(2)

    restored.upsertMessage({
      id: trainingId, roomId: 'room-1', messageType: 'agent', content: 'Is training in place?',
      senderName: 'Broker', timestamp: '2027-01-01T00:00:00Z',
      hitlInteractionStatus: 'applying', hitlApplicationStatus: 'applying',
    }, 'optimistic')
    vi.mocked(fetchPendingHitlRequests).mockResolvedValue({ requests: [requests[1]] })
    await overlayHitlForRoom({
      roomId: 'room-1', hitlRequestIndex: index,
      getAgentName: async () => 'Broker', getAgentSource: () => 'cloud',
    })

    const reconciled = useMessageStore.getState()
    expect(reconciled.entities[trainingId]).toMatchObject({
      hitlResolved: true, hitlApplicationStatus: 'applied',
    })
    expect(reconciled.entities[providersId].hitlResolved).toBe(false)
    expect(index.current).toEqual(new Map([[providersKey, providersId]]))
  })

  it('does not reopen an exact resolved interaction from stale REST pending', async () => {
    const request: HitlPendingRequest = {
      request_id: 'request-1',
      message_id: 'agent-message-1',
      source: 'agent',
      agent_name: 'Broker',
      prompt: 'Which market?',
      prompt_type: 'text',
      status: 'pending',
      created_at: '2027-01-01T00:00:00Z',
      interaction_id: 'interaction-1',
      interaction_status: 'open',
      interaction_version: 1,
      question_count: 1,
      question_index: 0,
    }
    vi.mocked(fetchPendingHitlRequests).mockResolvedValue({ requests: [request] })
    const index = { current: new Map<string, string>() }
    await overlayHitlForRoom({
      roomId: 'room-1', hitlRequestIndex: index,
      getAgentName: async () => 'Broker', getAgentSource: () => 'cloud',
    })
    const entityId = hitlQuestionEntityId(
      'agent-message-1', 'interaction-1', 'request-1', 1,
    )
    useMessageStore.getState().upsertMessage({
      id: entityId, roomId: 'room-1', messageType: 'agent', content: 'Which market?',
      senderName: 'Broker', timestamp: '2027-01-01T00:00:00Z',
      hitlResolved: true, hitlUserAnswer: 'London',
      hitlInteractionStatus: 'responded', hitlApplicationStatus: 'applied',
      hitlInteractionVersion: 1,
    }, 'sse')

    const pendingIds = await overlayHitlForRoom({
      roomId: 'room-1', hitlRequestIndex: index,
      getAgentName: async () => 'Broker', getAgentSource: () => 'cloud',
    })

    const entity = useMessageStore.getState().entities[entityId]
    expect(pendingIds).toEqual(new Set())
    expect(entity.hitlResolved).toBe(true)
    expect(entity.hitlUserAnswer).toBe('London')
    expect(entity.hitlInteractionStatus).toBe('responded')
    expect(index.current.size).toBe(0)
  })

  it('clears local applying HITL when the pending set is empty', async () => {
    useMessageStore.getState().upsertMessage({
      id: 'agent-message-1',
      roomId: 'room-1',
      messageType: 'agent',
      content: 'Which market?',
      senderName: 'Broker',
      timestamp: '2027-01-01T00:00:00Z',
      taskStatus: 'input-required',
      hitlRequestId: 'request-1',
      hitlPrompt: 'Which market?',
      hitlPromptType: 'text',
      hitlResolved: false,
      hitlUserAnswer: 'NYC',
      hitlInteractionStatus: 'applying',
      hitlApplicationStatus: 'applying',
    }, 'optimistic')

    vi.mocked(fetchPendingHitlRequests).mockResolvedValue({ requests: [] })

    await overlayHitlForRoom({
      roomId: 'room-1',
      hitlRequestIndex: { current: new Map() },
      getAgentName: async () => 'Broker',
      getAgentSource: () => 'cloud',
    })

    const entity = useMessageStore.getState().entities['agent-message-1']
    expect(entity.hitlResolved).toBe(true)
    expect(entity.hitlApplicationStatus).toBe('applied')
    expect(selectPendingHitls('room-1', useMessageStore.getState().entities, useMessageStore.getState().orderedIds)).toHaveLength(0)
  })

  it('preserves open HITL when live overlay sees an empty pending set', async () => {
    vi.mocked(fetchPendingHitlRequests).mockResolvedValue({ requests: [] })

    await overlayHitlForRoom({
      roomId: 'room-1',
      hitlRequestIndex: { current: new Map() },
      getAgentName: async () => 'Broker',
      getAgentSource: () => 'cloud',
    })

    const entity = useMessageStore.getState().entities['agent-message-1']
    expect(entity.hitlResolved).toBe(false)
    expect(selectPendingHitls('room-1', useMessageStore.getState().entities, useMessageStore.getState().orderedIds)).toHaveLength(1)
  })
})
