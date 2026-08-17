import { beforeEach, describe, expect, it, vi } from 'vitest'
import { overlayHitlForRoom } from '@/lib/room-sync/hitl-overlay'
import { useMessageStore } from '@/stores/message-store'
import { fetchPendingHitlRequests } from '@/lib/api/hitl'
import { selectPendingHitls } from '@/lib/selectors/select-hitl'

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
})
