import { expect, test } from '@playwright/test'

const ROOM_ID = 'privacy-room'
const CLIENT_REQUEST_ID = 'client-privacy-1'
const INTERNAL_TEXT = 'INTERNAL DISPATCH TASK: include private planner context'
const PUBLIC_LABEL = 'Requesting Insurer'

test('streaming agent turn never displays internal dispatch prompt', async ({ page }) => {
  const now = new Date().toISOString()

  await page.route('**/api/v1/agent/getAllAgents', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        agents: [
          {
            agent_id: 'agent-1',
            agent_status: 'active',
            agent_card: {
              name: 'Insurer Agent',
              description: 'Quotes insurance submissions',
              url: 'https://example.test/agent-1',
              version: '1.0.0',
              capabilities: {},
              skills: [],
            },
          },
        ],
      }),
    })
  })

  await page.route('**/api/v1/agent/getAllActiveAgents', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        agents: [
          {
            agent_id: 'agent-1',
            agent_status: 'active',
            agent_card: {
              name: 'Insurer Agent',
              description: 'Quotes insurance submissions',
              url: 'https://example.test/agent-1',
              version: '1.0.0',
              capabilities: {},
              skills: [],
            },
          },
        ],
      }),
    })
  })

  await page.route('**/api/v1/agentGroups**', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, groups: [] }),
    })
  })

  await page.route('**/api/v1/roomCenter/inquiryRoomSetting', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        room_id: ROOM_ID,
        resolved_agents: [],
        active_runs: [
          {
            state: 'running',
            trigger_message_id: 'user-msg-1',
            updated_at: now,
          },
        ],
        room: {
          room_id: ROOM_ID,
          room_name: 'Privacy Room',
          room_owner_id: 'user-1',
          room_owner_name: 'User',
          room_agent_set: { 'agent-1': 'Insurer Agent' },
          room_created_at: now,
          extend_info: { use_supervisor: true },
        },
      }),
    })
  })

  await page.route('**/api/v1/roomCenter/inquiryRoomMessagesByRoomId', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        room_id: ROOM_ID,
        message_list: [
          {
            room_id: ROOM_ID,
            message_id: 'user-msg-1',
            message_type: 'user',
            user_id: 'user-1',
            client_request_id: CLIENT_REQUEST_ID,
            message_created_at: now,
            message_content: { message_text: 'Get a quote' },
            extend_info: { use_supervisor: true },
          },
          {
            room_id: ROOM_ID,
            message_id: 'agent-msg-1',
            message_type: 'agent',
            agent_id: 'agent-1',
            related_message_id: 'user-msg-1',
            client_request_id: CLIENT_REQUEST_ID,
            message_created_at: now,
            task_updated_at: now,
            task_content: INTERNAL_TEXT,
            extend_info: { public_task_label: PUBLIC_LABEL },
            message_content: {
              message_text: PUBLIC_LABEL,
              message_task: {
                id: 'task-1',
                status: { state: 'working' },
                metadata: {
                  agent_id: 'agent-1',
                  public_task_label: PUBLIC_LABEL,
                  client_request_id: CLIENT_REQUEST_ID,
                  task_content: INTERNAL_TEXT,
                },
              },
            },
          },
        ],
      }),
    })
  })

  await page.route('**/api/v1/roomCenter/inquiryActiveRuns', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        room_id: ROOM_ID,
        active_runs: [
          {
            state: 'running',
            trigger_message_id: 'user-msg-1',
            updated_at: now,
          },
        ],
      }),
    })
  })

  await page.route(`**/api/v1/rooms/${ROOM_ID}/hitl/pending`, async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ requests: [] }),
    })
  })

  await page.route(`**/api/v1/sse/room/${ROOM_ID}/stream`, async route => {
    const frames = [
      {
        type: 'connected',
        room_id: ROOM_ID,
        timestamp: now,
        data: { status: 'connected' },
      },
      {
        type: 'task_update',
        room_id: ROOM_ID,
        timestamp: now,
        data: {
          message_id: 'agent-msg-1',
          agent_id: 'agent-1',
          agent_name: 'Insurer Agent',
          status: 'working',
          task_content: INTERNAL_TEXT,
          client_request_id: CLIENT_REQUEST_ID,
        },
      },
    ]

    await route.fulfill({
      status: 200,
      headers: {
        'content-type': 'text/event-stream',
        'cache-control': 'no-cache',
      },
      body: frames.map(frame => `data: ${JSON.stringify(frame)}\n\n`).join(''),
    })
  })

  await page.goto(`/c/room/${ROOM_ID}`)

  await expect(page.getByText('Get a quote')).toBeVisible()
  await expect(page.getByText('Insurer Agent')).toBeVisible()
  await expect(page.getByText(INTERNAL_TEXT)).toHaveCount(0)
  await page.waitForTimeout(500)
  await expect(page.getByText(INTERNAL_TEXT)).toHaveCount(0)
})
