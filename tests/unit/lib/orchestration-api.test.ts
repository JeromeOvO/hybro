import { describe, it, expect, beforeEach } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../setup/msw-server'
import { getApiUrl } from '@/lib/utils'
import {
  decomposeTask,
  assignAgentsToMetaTasks,
  assignAgentToMetaTask,
  runWorkflow,
  retryMetaTask,
  summarizeMetaTaskForBaseTask,
  processRoomUserMessage,
} from '@/lib/api/orchestration'

const BASE = getApiUrl('orchestrationCenter')

describe('Orchestration API', () => {
  beforeEach(() => {
    server.resetHandlers()
  })

  // ─── decomposeTask ──────────────────────────────────────────

  describe('decomposeTask', () => {
    it('should POST to /decomposeTask with task_id', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(`${BASE}/decomposeTask`, async ({ request }) => {
          capturedBody = (await request.json()) as Record<string, unknown>
          return HttpResponse.json({ success: true })
        }),
      )

      const result = await decomposeTask({ task_id: 'task-1' })

      expect(result.success).toBe(true)
      expect(capturedBody).toMatchObject({ task_id: 'task-1' })
    })

    it('should handle server errors', async () => {
      server.use(
        http.post(`${BASE}/decomposeTask`, () =>
          HttpResponse.json({ error: 'fail' }, { status: 500 }),
        ),
      )

      await expect(decomposeTask({ task_id: 'x' })).rejects.toThrow()
    })

    it('should handle network errors', async () => {
      server.use(
        http.post(`${BASE}/decomposeTask`, () => HttpResponse.error()),
      )

      await expect(decomposeTask({ task_id: 'x' })).rejects.toThrow()
    })
  })

  // ─── assignAgentsToMetaTasks ────────────────────────────────

  describe('assignAgentsToMetaTasks', () => {
    it('should POST to /assignAgentsToMetaTasks with task_id', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(`${BASE}/assignAgentsToMetaTasks`, async ({ request }) => {
          capturedBody = (await request.json()) as Record<string, unknown>
          return HttpResponse.json({ success: true })
        }),
      )

      const result = await assignAgentsToMetaTasks({ task_id: 'task-2' })

      expect(result.success).toBe(true)
      expect(capturedBody).toMatchObject({ task_id: 'task-2' })
    })

    it('should handle server errors', async () => {
      server.use(
        http.post(`${BASE}/assignAgentsToMetaTasks`, () =>
          HttpResponse.json({ error: 'fail' }, { status: 500 }),
        ),
      )

      await expect(
        assignAgentsToMetaTasks({ task_id: 'x' }),
      ).rejects.toThrow()
    })
  })

  // ─── assignAgentToMetaTask ──────────────────────────────────

  describe('assignAgentToMetaTask', () => {
    it('should POST to /assignAgentToMetaTask with task_id', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(`${BASE}/assignAgentToMetaTask`, async ({ request }) => {
          capturedBody = (await request.json()) as Record<string, unknown>
          return HttpResponse.json({ success: true })
        }),
      )

      const result = await assignAgentToMetaTask({ task_id: 'task-3' })

      expect(result.success).toBe(true)
      expect(capturedBody).toMatchObject({ task_id: 'task-3' })
    })

    it('should handle server errors', async () => {
      server.use(
        http.post(`${BASE}/assignAgentToMetaTask`, () =>
          HttpResponse.json({ error: 'fail' }, { status: 500 }),
        ),
      )

      await expect(
        assignAgentToMetaTask({ task_id: 'x' }),
      ).rejects.toThrow()
    })
  })

  // ─── runWorkflow ────────────────────────────────────────────

  describe('runWorkflow', () => {
    it('should POST to /runWorkflow with task_id', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(`${BASE}/runWorkflow`, async ({ request }) => {
          capturedBody = (await request.json()) as Record<string, unknown>
          return HttpResponse.json({ success: true })
        }),
      )

      const result = await runWorkflow({ task_id: 'task-4' })

      expect(result.success).toBe(true)
      expect(capturedBody).toMatchObject({ task_id: 'task-4' })
    })

    it('should handle server errors', async () => {
      server.use(
        http.post(`${BASE}/runWorkflow`, () =>
          HttpResponse.json({ error: 'fail' }, { status: 500 }),
        ),
      )

      await expect(runWorkflow({ task_id: 'x' })).rejects.toThrow()
    })
  })

  // ─── retryMetaTask ─────────────────────────────────────────

  describe('retryMetaTask', () => {
    it('should POST to /retryMetaTask with task_id', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(`${BASE}/retryMetaTask`, async ({ request }) => {
          capturedBody = (await request.json()) as Record<string, unknown>
          return HttpResponse.json({ success: true })
        }),
      )

      const result = await retryMetaTask({ task_id: 'task-5' })

      expect(result.success).toBe(true)
      expect(capturedBody).toMatchObject({ task_id: 'task-5' })
    })

    it('should handle server errors', async () => {
      server.use(
        http.post(`${BASE}/retryMetaTask`, () =>
          HttpResponse.json({ error: 'fail' }, { status: 500 }),
        ),
      )

      await expect(retryMetaTask({ task_id: 'x' })).rejects.toThrow()
    })
  })

  // ─── summarizeMetaTaskForBaseTask ───────────────────────────

  describe('summarizeMetaTaskForBaseTask', () => {
    it('should POST to /summarizeMetaTaskForBaseTask with task_id', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(
          `${BASE}/summarizeMetaTaskForBaseTask`,
          async ({ request }) => {
            capturedBody = (await request.json()) as Record<string, unknown>
            return HttpResponse.json({ success: true })
          },
        ),
      )

      const result = await summarizeMetaTaskForBaseTask({ task_id: 'task-6' })

      expect(result.success).toBe(true)
      expect(capturedBody).toMatchObject({ task_id: 'task-6' })
    })

    it('should handle server errors', async () => {
      server.use(
        http.post(`${BASE}/summarizeMetaTaskForBaseTask`, () =>
          HttpResponse.json({ error: 'fail' }, { status: 500 }),
        ),
      )

      await expect(
        summarizeMetaTaskForBaseTask({ task_id: 'x' }),
      ).rejects.toThrow()
    })
  })

  // ─── processRoomUserMessage ─────────────────────────────────

  describe('processRoomUserMessage', () => {
    it('should POST to /processRoomUserMessage with all required fields', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(`${BASE}/processRoomUserMessage`, async ({ request }) => {
          capturedBody = (await request.json()) as Record<string, unknown>
          return HttpResponse.json({ success: true })
        }),
      )

      const data = {
        room_id: 'room-1',
        room_user_message_id: 'msg-1',
        room_related_message_id: 'msg-0',
      }
      const result = await processRoomUserMessage(data)

      expect(result.success).toBe(true)
      expect(capturedBody).toMatchObject(data)
    })

    it('should handle server errors', async () => {
      server.use(
        http.post(`${BASE}/processRoomUserMessage`, () =>
          HttpResponse.json({ error: 'fail' }, { status: 500 }),
        ),
      )

      await expect(
        processRoomUserMessage({
          room_id: 'r',
          room_user_message_id: 'm',
          room_related_message_id: 'x',
        }),
      ).rejects.toThrow()
    })

    it('should handle network errors', async () => {
      server.use(
        http.post(`${BASE}/processRoomUserMessage`, () =>
          HttpResponse.error(),
        ),
      )

      await expect(
        processRoomUserMessage({
          room_id: 'r',
          room_user_message_id: 'm',
          room_related_message_id: 'x',
        }),
      ).rejects.toThrow()
    })
  })
})
