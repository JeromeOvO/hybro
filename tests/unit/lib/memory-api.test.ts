import { describe, it, expect, beforeEach } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../setup/msw-server'
import { getApiUrl } from '@/lib/utils'
import {
  addChatContext,
  getChatContextBySessionId,
  updateChatContextBySessionId,
  deleteChatContextBySessionId,
} from '@/lib/api/memory'

const BASE = getApiUrl('memoryCenter')

describe('Memory API', () => {
  beforeEach(() => {
    server.resetHandlers()
  })

  // ─── addChatContext ──────────────────────────────────────────

  describe('addChatContext', () => {
    it('should POST to /addChatContext with the request body', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(`${BASE}/addChatContext`, async ({ request }) => {
          capturedBody = (await request.json()) as Record<string, unknown>
          return HttpResponse.json({ success: true })
        }),
      )

      const req = { session_id: 'sess-1', context: 'Hello world' }
      const result = await addChatContext(req as never)

      expect(result.success).toBe(true)
      expect(capturedBody).toMatchObject(req)
    })

    it('should handle server errors', async () => {
      server.use(
        http.post(`${BASE}/addChatContext`, () =>
          HttpResponse.json({ error: 'fail' }, { status: 500 }),
        ),
      )

      await expect(addChatContext({} as never)).rejects.toThrow()
    })

    it('should handle network errors', async () => {
      server.use(
        http.post(`${BASE}/addChatContext`, () => HttpResponse.error()),
      )

      await expect(addChatContext({} as never)).rejects.toThrow()
    })
  })

  // ─── getChatContextBySessionId ──────────────────────────────

  describe('getChatContextBySessionId', () => {
    it('should POST to /getChatContextBySessionId with the session_id', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(`${BASE}/getChatContextBySessionId`, async ({ request }) => {
          capturedBody = (await request.json()) as Record<string, unknown>
          return HttpResponse.json({
            success: true,
            chat_context: [{ role: 'user', content: 'hi' }],
          })
        }),
      )

      const req = { session_id: 'sess-42' }
      const result = await getChatContextBySessionId(req as never)

      expect(result.success).toBe(true)
      expect(capturedBody).toMatchObject({ session_id: 'sess-42' })
    })

    it('should handle server errors', async () => {
      server.use(
        http.post(`${BASE}/getChatContextBySessionId`, () =>
          HttpResponse.json({ error: 'fail' }, { status: 500 }),
        ),
      )

      await expect(getChatContextBySessionId({} as never)).rejects.toThrow()
    })
  })

  // ─── updateChatContextBySessionId ───────────────────────────

  describe('updateChatContextBySessionId', () => {
    it('should POST to /updateChatContextBySessionId with the request body', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(
          `${BASE}/updateChatContextBySessionId`,
          async ({ request }) => {
            capturedBody = (await request.json()) as Record<string, unknown>
            return HttpResponse.json({ success: true })
          },
        ),
      )

      const req = { session_id: 'sess-42', context: 'updated context' }
      const result = await updateChatContextBySessionId(req as never)

      expect(result.success).toBe(true)
      expect(capturedBody).toMatchObject(req)
    })

    it('should handle server errors', async () => {
      server.use(
        http.post(`${BASE}/updateChatContextBySessionId`, () =>
          HttpResponse.json({ error: 'fail' }, { status: 500 }),
        ),
      )

      await expect(
        updateChatContextBySessionId({} as never),
      ).rejects.toThrow()
    })
  })

  // ─── deleteChatContextBySessionId ───────────────────────────

  describe('deleteChatContextBySessionId', () => {
    it('should POST to /deleteChatContextBySessionId with the session_id', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(
          `${BASE}/deleteChatContextBySessionId`,
          async ({ request }) => {
            capturedBody = (await request.json()) as Record<string, unknown>
            return HttpResponse.json({ success: true })
          },
        ),
      )

      const req = { session_id: 'sess-42' }
      const result = await deleteChatContextBySessionId(req as never)

      expect(result.success).toBe(true)
      expect(capturedBody).toMatchObject({ session_id: 'sess-42' })
    })

    it('should handle server errors', async () => {
      server.use(
        http.post(`${BASE}/deleteChatContextBySessionId`, () =>
          HttpResponse.json({ error: 'fail' }, { status: 500 }),
        ),
      )

      await expect(
        deleteChatContextBySessionId({} as never),
      ).rejects.toThrow()
    })

    it('should handle network errors', async () => {
      server.use(
        http.post(`${BASE}/deleteChatContextBySessionId`, () =>
          HttpResponse.error(),
        ),
      )

      await expect(
        deleteChatContextBySessionId({} as never),
      ).rejects.toThrow()
    })
  })
})
