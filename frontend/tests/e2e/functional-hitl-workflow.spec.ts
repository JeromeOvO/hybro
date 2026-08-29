// tests/e2e/functional-hitl-workflow.spec.ts
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

const BACKEND_URL = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000/api/v1'
const DEFAULT_TRAVEL_PLANNER_AGENT_ID = '575ee896f1e24823943a1e98aee111c9'

/**
 * Resolves active Travel Planner Agent ID dynamically.
 */
async function getTravelPlannerAgentId(request: APIRequestContext): Promise<string> {
  try {
    const resp = await request.get(`${BACKEND_URL}/agent/getAllActiveAgents`)
    if (resp.ok()) {
      const data = await resp.json()
      const agents = data.agents || []
      for (const a of agents) {
        const name = (a.agent_card?.name || '').toLowerCase()
        if (name.includes('travel') || name.includes('planner')) {
          return a.agent_id || DEFAULT_TRAVEL_PLANNER_AGENT_ID
        }
      }
    }
  } catch {}
  return DEFAULT_TRAVEL_PLANNER_AGENT_ID
}

/**
 * Automatically detects and responds to any Human-in-the-Loop (HITL) prompt,
 * submitting automated user inputs immediately without waiting for human intervention.
 */
async function autoRespondHitlIfPresent(
  page: Page,
  request: APIRequestContext,
  roomId: string,
  answerText = 'Kyoto, 3 days, $1500 budget'
) {
  // 1. Submit through backend API if pending requests exist
  const pendingResp = await request
    .get(`${BACKEND_URL}/rooms/${roomId}/hitl/pending`)
    .catch(() => null)

  if (pendingResp && pendingResp.ok()) {
    const data = await pendingResp.json().catch(() => ({}))
    const requests = data.requests || []
    for (const req of requests) {
      if (req.request_id && req.interaction_id) {
        await request
          .post(`${BACKEND_URL}/rooms/${roomId}/hitl/respond-batch`, {
            data: {
              interaction_id: req.interaction_id,
              answers: [{ request_id: req.request_id, user_input: answerText }],
              client_request_id: req.client_request_id || `hitl-auto-${Date.now()}`,
            },
          })
          .catch(() => {})
      }
    }
  }

  // 2. Also submit via UI if the Questionnaire / HitlResponseBar is rendered
  const hitlBar = page.locator('[data-testid="hitl-response-bar"]')
  if (await hitlBar.isVisible({ timeout: 1500 }).catch(() => false)) {
    const textInput = hitlBar.locator('textarea, input[type="text"]').first()
    if (await textInput.isVisible({ timeout: 1000 }).catch(() => false)) {
      await textInput.fill(answerText)
      const submitBtn = hitlBar
        .locator('button[type="submit"], button:has-text("Submit"), button:has-text("Send")')
        .first()
      if (await submitBtn.isVisible({ timeout: 1000 }).catch(() => false)) {
        await submitBtn.click().catch(() => {})
      }
    }
  }
}

test.describe('Functional HITL & Timeline Hydration Flow', () => {
  test('creates room, automatically sends human input when requested, and persists across reload', async ({
    page,
    request,
  }) => {
    // 0. Resolve active travel planner agent ID
    const travelAgentId = await getTravelPlannerAgentId(request)

    // 1. Create a real room in backend
    const createResp = await request.post(`${BACKEND_URL}/roomCenter/createNewRoom`, {
      data: {
        room_name: 'E2E Functional Test Room',
        room_owner_name: 'Developer Local',
        room_agent_ids: [travelAgentId],
        extend_info: { use_supervisor: true },
      },
    })
    expect(createResp.ok()).toBeTruthy()
    const roomData = await createResp.json()
    const roomId = roomData.room_id
    expect(roomId).toBeTruthy()

    // 2. Dispatch a message to the room
    const promptText = 'Plan a 3-day trip to Tokyo'
    const sendResp = await request.post(`${BACKEND_URL}/roomCenter/sendMessage`, {
      data: {
        room_id: roomId,
        user_input: promptText,
        message: {
          room_id: roomId,
          message_id: '',
          message_type: 'user',
          message_content: {
            message_text: promptText,
          },
        },
        mode: 'supervisor',
        client_request_id: `e2e-req-${Date.now()}`,
        agent_scope: {
          source: 'mention',
          agent_ids: [travelAgentId],
        },
      },
    })
    expect(sendResp.ok()).toBeTruthy()

    // 3. Navigate to the real room in browser
    await page.goto(`/room/${roomId}`)

    // 4. Verify that the sent user prompt is rendered in the timeline
    await expect(page.getByText(promptText)).toBeVisible({ timeout: 15000 })

    // 5. Automatically send input if human input is requested
    await autoRespondHitlIfPresent(page, request, roomId, 'Kyoto, 3 days, $1500')

    // 6. Simulate page reload to verify timeline hydration
    await page.reload()

    // 7. Verify user prompt and message persistence after reload
    await expect(page.getByText(promptText)).toBeVisible({ timeout: 15000 })
  })
})
