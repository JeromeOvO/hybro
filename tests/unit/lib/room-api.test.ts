import { describe, it, expect, beforeEach, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../setup/msw-server'
import { errorHandlers } from '../../setup/msw-handlers'
import { getApiUrl } from '@/lib/utils'
import {
  createNewRoom,
  inquiryActiveRuns,
  inquiryRoomSetting,
  inquiryRoomsByRoomOwnerId,
  SendMessage,
  inquiryRoomMessagesByRoomId,
  updateRoomAgentSet,
  updateRoomName,
  suggestAgents,
} from '@/lib/api/room'

const roomCenter = getApiUrl('roomCenter')

describe('Room API', () => {
  beforeEach(() => {
    server.resetHandlers()
  })

  describe('createNewRoom', () => {
    it('should create a new room with correct request body', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(`${roomCenter}/createNewRoom`, async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>
          return HttpResponse.json({
            success: true,
            room_id: 'test-room-id',
            room: {
              room_id: 'test-room-id',
              room_name: capturedBody.room_name,
              room_owner_id: capturedBody.room_owner_id,
              room_owner_name: capturedBody.room_owner_name,
              room_agent_set: capturedBody.room_agent_set || {},
              room_created_at: new Date().toISOString(),
            },
          })
        })
      )

      const result = await createNewRoom('Test Room', 'user-1', 'Test User')

      expect(result.success).toBe(true)
      expect(result.room?.room_name).toBe('Test Room')
      expect(capturedBody).toMatchObject({
        room_name: 'Test Room',
        room_owner_id: 'user-1',
        room_owner_name: 'Test User',
      })
    })

    it('should include agent set in request body when provided', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(`${roomCenter}/createNewRoom`, async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>
          return HttpResponse.json({ success: true, room_id: 'test-room-id' })
        })
      )

      const agentSet = { 'agent-1': 'Agent One' }
      await createNewRoom('Test Room', 'user-1', 'Test User', undefined, agentSet)

      expect(capturedBody).toMatchObject({
        room_agent_set: { 'agent-1': 'Agent One' },
      })
    })
  })

  describe('inquiryRoomSetting', () => {
    it('should fetch room settings with correct room_id', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(`${roomCenter}/inquiryRoomSetting`, async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>
          return HttpResponse.json({
            success: true,
            room_id: capturedBody.room_id,
            room: { room_id: capturedBody.room_id, room_name: 'Test Room' },
          })
        })
      )

      const result = await inquiryRoomSetting('room-42')

      expect(result.success).toBe(true)
      expect(capturedBody).toMatchObject({ room_id: 'room-42' })
    })
  })

  describe('inquiryActiveRuns', () => {
    it('should fetch active runs with correct room_id', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(`${roomCenter}/inquiryActiveRuns`, async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>
          return HttpResponse.json({
            success: true,
            room_id: capturedBody.room_id,
            active_runs: [{ run_id: 'run-1', state: 'processing', trigger_message_id: 'm1' }],
          })
        })
      )

      const result = await inquiryActiveRuns('room-42')

      expect(result.success).toBe(true)
      expect(result.active_runs).toHaveLength(1)
      expect(capturedBody).toMatchObject({ room_id: 'room-42' })
    })
  })

  describe('inquiryRoomsByRoomOwnerId', () => {
    it('should fetch rooms with correct owner_id', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(`${roomCenter}/inquiryRoomsByRoomOwnerId`, async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>
          return HttpResponse.json({
            success: true,
            room_list: [{ room_id: 'room-1', room_name: 'Room 1' }],
          })
        })
      )

      const result = await inquiryRoomsByRoomOwnerId('user-1')

      expect(result.success).toBe(true)
      expect(result.room_list).toHaveLength(1)
      expect(capturedBody).toMatchObject({ room_owner_id: 'user-1' })
    })
  })

  describe('SendMessage', () => {
    it('should send a message with correct request structure', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(`${roomCenter}/sendMessage`, async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>
          return HttpResponse.json({
            success: true,
            room_id: capturedBody.room_id,
            message_id: 'msg-new',
          })
        })
      )

      const result = await SendMessage(
        'room-1', 'Hello, world!', undefined, 'user-1', 'Test User'
      )

      expect(result.success).toBe(true)
      expect(result.message_id).toBe('msg-new')
      expect(capturedBody).toMatchObject({
        room_id: 'room-1',
        user_input: 'Hello, world!',
        user_id: 'user-1',
        user_name: 'Test User',
        target_group: 'all_agents',
      })
    })

    it('should include target_group in request body when specified', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(`${roomCenter}/sendMessage`, async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>
          return HttpResponse.json({ success: true, message_id: 'msg-1' })
        })
      )

      await SendMessage('room-1', 'Hello!', undefined, 'user-1', 'Test User', 'room_team')

      expect(capturedBody).toMatchObject({ target_group: 'room_team' })
    })

    it('should include quoted text in extend_info', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(`${roomCenter}/sendMessage`, async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>
          return HttpResponse.json({ success: true, message_id: 'msg-1' })
        })
      )

      await SendMessage(
        'room-1', 'Reply', undefined, 'user-1', 'Test User',
        'all_agents', 'related-msg-1', 'Quoted text here'
      )

      expect(capturedBody).not.toBeNull()
      const body = capturedBody as unknown as Record<string, unknown>
      const message = body.message as Record<string, unknown>
      expect(message.related_message_id).toBe('related-msg-1')
      expect(message.extend_info).toMatchObject({ quoted_text: 'Quoted text here' })
    })

    it('should include quoted sender name in extend_info', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(`${roomCenter}/sendMessage`, async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>
          return HttpResponse.json({ success: true, message_id: 'msg-1' })
        })
      )

      await SendMessage(
        'room-1', 'Reply', undefined, 'user-1', 'Test User',
        'all_agents', 'related-msg-1', 'Quoted text here', 'Spec Agent',
      )

      expect(capturedBody).not.toBeNull()
      const body = capturedBody as unknown as Record<string, unknown>
      const message = body.message as Record<string, unknown>
      expect(message.extend_info).toMatchObject({
        quoted_text: 'Quoted text here',
        quoted_sender_name: 'Spec Agent',
      })
    })

    it('should omit target_group when MentionDispatchInput is provided', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(`${roomCenter}/sendMessage`, async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>
          return HttpResponse.json({ success: true, message_id: 'msg-1' })
        })
      )

      await SendMessage(
        'room-1', 'Hello @agent', undefined, 'user-1', 'Test User',
        'all_agents', null, null, undefined, undefined,
        { mentioned_agent_ids: ['agent-a', 'agent-b'] },
      )

      expect(capturedBody).toHaveProperty('mentioned_agent_ids', ['agent-a', 'agent-b'])
      expect(capturedBody).not.toHaveProperty('target_group')
    })

    it('should keep target_group when TargetModeDispatchInput is provided', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(`${roomCenter}/sendMessage`, async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>
          return HttpResponse.json({ success: true, message_id: 'msg-1' })
        })
      )

      await SendMessage(
        'room-1', 'Hello', undefined, 'user-1', 'Test User',
        'room_team', null, null, undefined, undefined,
        { message_target_mode: 'room_default' },
      )

      expect(capturedBody).toHaveProperty('target_group', 'room_team')
      expect(capturedBody).toHaveProperty('message_target_mode', 'room_default')
      expect(capturedBody).not.toHaveProperty('mentioned_agent_ids')
    })

    it('should include client_request_id in request body when provided', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(`${roomCenter}/sendMessage`, async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>
          return HttpResponse.json({ success: true, message_id: 'msg-1' })
        })
      )

      await SendMessage(
        'room-1', 'Hello', undefined, 'user-1', 'Test User',
        'all_agents', null, null, undefined, undefined, undefined,
        'cr-uuid-123',
      )

      expect(capturedBody).toHaveProperty('client_request_id', 'cr-uuid-123')
    })

    it('should include client_request_id as undefined when not provided', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(`${roomCenter}/sendMessage`, async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>
          return HttpResponse.json({ success: true, message_id: 'msg-1' })
        })
      )

      await SendMessage('room-1', 'Hello', undefined, 'user-1', 'Test User')

      // client_request_id should not be present when not provided
      expect(capturedBody!['client_request_id']).toBeUndefined()
    })

    it('should include target_group_id for saved_group dispatch', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(`${roomCenter}/sendMessage`, async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>
          return HttpResponse.json({ success: true, message_id: 'msg-1' })
        })
      )

      await SendMessage(
        'room-1', 'Hello', undefined, 'user-1', 'Test User',
        'grp-123', null, null, undefined, undefined,
        { message_target_mode: 'saved_group', target_group_id: 'grp-123' },
      )

      expect(capturedBody).toHaveProperty('message_target_mode', 'saved_group')
      expect(capturedBody).toHaveProperty('target_group_id', 'grp-123')
      expect(capturedBody).toHaveProperty('target_group', 'grp-123')
    })
  })

  describe('inquiryRoomMessagesByRoomId', () => {
    it('should fetch room messages with correct room_id', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(`${roomCenter}/inquiryRoomMessagesByRoomId`, async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>
          return HttpResponse.json({ success: true, room_id: 'room-1', message_list: [] })
        })
      )

      const result = await inquiryRoomMessagesByRoomId('room-1')

      expect(result.success).toBe(true)
      expect(result.message_list).toBeDefined()
      expect(capturedBody).toMatchObject({ room_id: 'room-1' })
    })
  })

  describe('updateRoomAgentSet', () => {
    it('should send agent set in request body', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(`${roomCenter}/updateRoomAgentSet`, async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>
          return HttpResponse.json({ success: true })
        })
      )

      await updateRoomAgentSet('room-1', { 'a-1': 'Agent One', 'a-2': 'Agent Two' })

      expect(capturedBody).toMatchObject({
        room_id: 'room-1',
        room_agent_set: { 'a-1': 'Agent One', 'a-2': 'Agent Two' },
      })
    })
  })

  describe('updateRoomName', () => {
    it('should send new name in request body', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(`${roomCenter}/updateRoomName`, async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>
          return HttpResponse.json({ success: true })
        })
      )

      await updateRoomName('room-1', 'New Room Name')

      expect(capturedBody).toMatchObject({
        room_id: 'room-1',
        room_name: 'New Room Name',
      })
    })
  })

  describe('suggestAgents', () => {
    it('should send message_text and top_k in request body', async () => {
      let capturedBody: Record<string, unknown> | null = null
      server.use(
        http.post(`${roomCenter}/suggestAgents`, async ({ request }) => {
          capturedBody = await request.json() as Record<string, unknown>
          return HttpResponse.json({
            success: true,
            routing_strategy: 'single',
            suggested_agents: [{ agent_id: 'a-1', name: 'Agent', reason: 'Best' }],
          })
        })
      )

      const result = await suggestAgents('Help me with coding', 5)

      expect(result.success).toBe(true)
      expect(result.suggested_agents).toHaveLength(1)
      expect(capturedBody).toMatchObject({
        message_text: 'Help me with coding',
        top_k: 5,
      })
    })
  })

  describe('error handling', () => {
    it('should handle network errors', async () => {
      server.use(errorHandlers.networkError)
      await expect(
        SendMessage('room-1', 'Hello', undefined, 'user-1', 'Test User')
      ).rejects.toThrow()
    })

    it('should handle server errors (500)', async () => {
      server.use(errorHandlers.serverError)
      await expect(
        SendMessage('room-1', 'Hello', undefined, 'user-1', 'Test User')
      ).rejects.toThrow()
    })

    it('should handle auth errors (401)', async () => {
      server.use(errorHandlers.authError)
      await expect(
        SendMessage('room-1', 'Hello', undefined, 'user-1', 'Test User')
      ).rejects.toThrow()
    })

    it('should handle rate limit errors (429)', async () => {
      server.use(errorHandlers.rateLimitError)
      await expect(
        SendMessage('room-1', 'Hello', undefined, 'user-1', 'Test User')
      ).rejects.toThrow()
    })
  })
})
