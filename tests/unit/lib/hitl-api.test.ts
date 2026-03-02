import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../setup/msw-server'

let respondToHitl: typeof import('@/lib/api/hitl').respondToHitl
let fetchPendingHitlRequests: typeof import('@/lib/api/hitl').fetchPendingHitlRequests

beforeEach(async () => {
  const mod = await import('@/lib/api/hitl')
  respondToHitl = mod.respondToHitl
  fetchPendingHitlRequests = mod.fetchPendingHitlRequests
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('HITL API Client', () => {
  describe('respondToHitl', () => {
    it('sends POST with request_id and user_input to room-scoped URL', async () => {
      let capturedBody: any = null
      let capturedUrl: string | null = null
      server.use(
        http.post('*/rooms/room-1/hitl/respond', async ({ request }) => {
          capturedUrl = request.url
          capturedBody = await request.json()
          return HttpResponse.json({ status: 'ok', request_id: 'req-1' })
        })
      )

      const result = await respondToHitl('room-1', 'req-1', '2024-2026')
      expect(result.status).toBe('ok')
      expect(result.request_id).toBe('req-1')
      expect(capturedBody).toEqual({
        request_id: 'req-1',
        user_input: '2024-2026',
      })
      expect(capturedUrl).toContain('/rooms/room-1/hitl/respond')
    })

    it('throws on HTTP error', async () => {
      server.use(
        http.post('*/rooms/room-1/hitl/respond', () => {
          return new HttpResponse('Server Error', { status: 500 })
        })
      )

      await expect(respondToHitl('room-1', 'req-1', 'test')).rejects.toThrow()
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
