import { describe, it, expect, beforeEach } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../setup/msw-server'
import { getApiUrl } from '@/lib/utils'
import {
  queryTask,
  queryBaseTask,
  getAllSessions,
  getBaseTasksBySessionId,
  getMetaTasksByParentId,
} from '@/lib/api/task'

const BASE = getApiUrl('task')

describe('Task API', () => {
  beforeEach(() => {
    server.resetHandlers()
  })

  // ─── queryTask ──────────────────────────────────────────────

  describe('queryTask', () => {
    it('should GET /queryTask/:taskId and return task data', async () => {
      let capturedUrl = ''
      server.use(
        http.get(`${BASE}/queryTask/:taskId`, ({ request }) => {
          capturedUrl = request.url
          return HttpResponse.json({
            success: true,
            task: { task_id: 'task-1', status: 'completed' },
          })
        }),
      )

      const result = await queryTask('task-1')

      expect(result.success).toBe(true)
      expect(capturedUrl).toContain('/queryTask/task-1')
    })

    it('should handle server errors', async () => {
      server.use(
        http.get(`${BASE}/queryTask/:taskId`, () =>
          HttpResponse.json({ error: 'fail' }, { status: 500 }),
        ),
      )

      await expect(queryTask('task-1')).rejects.toThrow()
    })

    it('should handle network errors', async () => {
      server.use(
        http.get(`${BASE}/queryTask/:taskId`, () => HttpResponse.error()),
      )

      await expect(queryTask('task-1')).rejects.toThrow()
    })
  })

  // ─── queryBaseTask ──────────────────────────────────────────

  describe('queryBaseTask', () => {
    it('should GET /queryBaseTask/:taskId and return base task data', async () => {
      let capturedUrl = ''
      server.use(
        http.get(`${BASE}/queryBaseTask/:taskId`, ({ request }) => {
          capturedUrl = request.url
          return HttpResponse.json({
            success: true,
            task: { task_id: 'base-1' },
          })
        }),
      )

      const result = await queryBaseTask('base-1')

      expect(result.success).toBe(true)
      expect(capturedUrl).toContain('/queryBaseTask/base-1')
    })

    it('should handle server errors', async () => {
      server.use(
        http.get(`${BASE}/queryBaseTask/:taskId`, () =>
          HttpResponse.json({ error: 'fail' }, { status: 500 }),
        ),
      )

      await expect(queryBaseTask('base-1')).rejects.toThrow()
    })
  })

  // ─── getAllSessions ─────────────────────────────────────────

  describe('getAllSessions', () => {
    it('should GET /getAllSessions/:userName and return sessions', async () => {
      let capturedUrl = ''
      server.use(
        http.get(`${BASE}/getAllSessions/:userName`, ({ request }) => {
          capturedUrl = request.url
          return HttpResponse.json({
            success: true,
            sessions: [{ session_id: 's-1' }, { session_id: 's-2' }],
          })
        }),
      )

      const result = await getAllSessions('john')

      expect(result.success).toBe(true)
      expect(capturedUrl).toContain('/getAllSessions/john')
    })

    it('should handle server errors', async () => {
      server.use(
        http.get(`${BASE}/getAllSessions/:userName`, () =>
          HttpResponse.json({ error: 'fail' }, { status: 500 }),
        ),
      )

      await expect(getAllSessions('john')).rejects.toThrow()
    })
  })

  // ─── getBaseTasksBySessionId ────────────────────────────────

  describe('getBaseTasksBySessionId', () => {
    it('should GET /getBaseTasksBySessionId/:sessionId and return tasks', async () => {
      let capturedUrl = ''
      server.use(
        http.get(`${BASE}/getBaseTasksBySessionId/:sessionId`, ({ request }) => {
          capturedUrl = request.url
          return HttpResponse.json({
            success: true,
            tasks: [{ task_id: 't-1' }],
          })
        }),
      )

      const result = await getBaseTasksBySessionId('sess-1')

      expect(result.success).toBe(true)
      expect(capturedUrl).toContain('/getBaseTasksBySessionId/sess-1')
    })

    it('should handle server errors', async () => {
      server.use(
        http.get(`${BASE}/getBaseTasksBySessionId/:sessionId`, () =>
          HttpResponse.json({ error: 'fail' }, { status: 500 }),
        ),
      )

      await expect(getBaseTasksBySessionId('sess-1')).rejects.toThrow()
    })
  })

  // ─── getMetaTasksByParentId ─────────────────────────────────

  describe('getMetaTasksByParentId', () => {
    it('should GET /getMetaTasksByParentTaskId/:parentTaskId and return meta tasks', async () => {
      let capturedUrl = ''
      server.use(
        http.get(`${BASE}/getMetaTasksByParentTaskId/:parentTaskId`, ({ request }) => {
          capturedUrl = request.url
          return HttpResponse.json({
            success: true,
            tasks: [{ task_id: 'meta-1' }, { task_id: 'meta-2' }],
          })
        }),
      )

      const result = await getMetaTasksByParentId('parent-1')

      expect(result.success).toBe(true)
      expect(capturedUrl).toContain('/getMetaTasksByParentTaskId/parent-1')
    })

    it('should handle server errors', async () => {
      server.use(
        http.get(`${BASE}/getMetaTasksByParentTaskId/:parentTaskId`, () =>
          HttpResponse.json({ error: 'fail' }, { status: 500 }),
        ),
      )

      await expect(getMetaTasksByParentId('parent-1')).rejects.toThrow()
    })

    it('should handle network errors', async () => {
      server.use(
        http.get(`${BASE}/getMetaTasksByParentTaskId/:parentTaskId`, () =>
          HttpResponse.error(),
        ),
      )

      await expect(getMetaTasksByParentId('parent-1')).rejects.toThrow()
    })
  })
})
