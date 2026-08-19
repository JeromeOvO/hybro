import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../setup/msw-server'

let fetchPendingHitlRequests: typeof import('@/lib/api/hitl').fetchPendingHitlRequests
let respondToHitlBatch: typeof import('@/lib/api/hitl').respondToHitlBatch
let cancelHitl: typeof import('@/lib/api/hitl').cancelHitl

beforeEach(async () => {
  const mod = await import('@/lib/api/hitl')
  fetchPendingHitlRequests = mod.fetchPendingHitlRequests
  respondToHitlBatch = mod.respondToHitlBatch
  cancelHitl = mod.cancelHitl
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('HITL API Client', () => {
  describe('respondToHitlBatch', () => {
    it('posts the complete interaction answer inventory with correlation', async () => {
      let capturedBody: unknown
      server.use(
        http.post('*/rooms/room-1/hitl/respond-batch', async ({ request }) => {
          capturedBody = await request.json()
          return HttpResponse.json({
            status: 'applied',
            request_id: 'req-2',
            interaction_id: 'interaction-1',
          })
        }),
      )

      const result = await respondToHitlBatch(
        'room-1',
        'interaction-1',
        [
          { requestId: 'req-1', answer: 'Acme' },
          { requestId: 'req-2', answer: '2027-01-01' },
        ],
        'client-1',
      )

      expect(result.status).toBe('applied')
      expect(capturedBody).toEqual({
        interaction_id: 'interaction-1',
        answers: [
          { request_id: 'req-1', user_input: 'Acme' },
          { request_id: 'req-2', user_input: '2027-01-01' },
        ],
        client_request_id: 'client-1',
      })
    })

    it('surfaces batch HTTP errors', async () => {
      server.use(
        http.post('*/rooms/room-1/hitl/respond-batch', () => (
          new HttpResponse('Conflict', { status: 409 })
        )),
      )

      await expect(respondToHitlBatch(
        'room-1',
        'interaction-1',
        [{ requestId: 'req-1', answer: 'Acme' }],
        undefined,
      )).rejects.toThrow()
    })
  })

  describe('cancelHitl', () => {
    it('cancels the authoritative interaction with version fencing', async () => {
      let capturedBody: unknown
      server.use(
        http.post('*/rooms/room-1/hitl/interactions/interaction-1/cancel', async ({ request }) => {
          capturedBody = await request.json()
          return HttpResponse.json({
            status: 'canceled',
            interaction_id: 'interaction-1',
            interaction_version: 5,
          })
        }),
      )

      await cancelHitl('room-1', 'interaction-1', 4, 'cancel-1')

      expect(capturedBody).toEqual({
        interaction_id: 'interaction-1',
        expected_interaction_version: 4,
        client_request_id: 'cancel-1',
      })
    })
  })

  describe('fetchPendingHitlRequests', () => {
    it('fetches pending requests for a room via room-scoped URL', async () => {
      let capturedUrl: string | null = null
      server.use(
        http.get('*/rooms/room-1/hitl/pending', ({ request }) => {
          capturedUrl = request.url
          return HttpResponse.json({
            requests: [
              {
                request_id: 'req-1',
                message_id: 'msg-1',
                source: 'agent',
                agent_id: 'agent-1',
                agent_name: 'Research Agent',
                prompt: 'Which date range?',
                prompt_type: 'text',
                choices: null,
                status: 'pending',
                expires_at: '2026-12-31T00:00:00Z',
                created_at: '2026-01-01T00:00:00Z',
              },
            ],
          })
        })
      )

      const result = await fetchPendingHitlRequests('room-1')
      expect(result.requests).toHaveLength(1)
      expect(result.requests[0].request_id).toBe('req-1')
      expect(result.requests[0].prompt_type).toBe('text')
      expect(capturedUrl).toContain('/rooms/room-1/hitl/pending')
    })

    it('returns empty array when no pending requests', async () => {
      server.use(
        http.get('*/rooms/room-2/hitl/pending', () => {
          return HttpResponse.json({ requests: [] })
        })
      )

      const result = await fetchPendingHitlRequests('room-2')
      expect(result.requests).toHaveLength(0)
    })

    it('throws on HTTP error', async () => {
      server.use(
        http.get('*/rooms/room-1/hitl/pending', () => {
          return new HttpResponse('Not Found', { status: 404 })
        })
      )

      await expect(fetchPendingHitlRequests('room-1')).rejects.toThrow()
    })
  })
})
