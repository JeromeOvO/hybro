import { test, expect } from './fixtures/auth'

test.describe('Authenticated flows', () => {
  test('send message and see agent response', async ({ roomPath, page }) => {
    await page.goto(roomPath)

    const input = page.locator('[data-testid="chat-input"]')
    await input.waitFor({ timeout: 15_000 })

    test.setTimeout(120_000)
    const turns = page.locator('.conversation-turn')
    const turnsBefore = await turns.count()

    await input.fill('Hello, test message')
    await input.press('Enter')

    await expect(turns).toHaveCount(turnsBefore + 1, { timeout: 30_000 })
    const newTurn = turns.last()
    await expect(newTurn.getByText('Finished')).toBeVisible({ timeout: 90_000 })
    // "Finished" also labels failed and canceled turns, so require a real answer.
    const finalSlot = newTurn.locator('[data-turn-slot="final"]')
    await expect(finalSlot).not.toBeEmpty()
    await expect(finalSlot).not.toContainText('Request failed')
  })

  test('cancel in-flight message', async ({ roomPath, page }) => {
    await page.goto(roomPath)

    const input = page.locator('[data-testid="chat-input"]')
    await input.waitFor({ timeout: 15_000 })

    const canceledBefore = await page.getByText('Request stopped', { exact: true }).count()

    await input.fill('Write a long essay about quantum computing')
    await input.press('Enter')

    const stopButton = page.locator('[data-testid="stop-processing"]')
    await stopButton.waitFor({ state: 'visible', timeout: 10_000 })
    await stopButton.click()

    await expect(stopButton).toBeHidden({ timeout: 10_000 })
    await expect(page.getByText('Request stopped', { exact: true })).toHaveCount(canceledBefore + 1, {
      timeout: 10_000,
    })
  })

  test('HITL reply flow', async ({ roomPath, page }) => {
    test.skip(true, 'Requires backend agent that triggers input_required — run manually')
    await page.goto(roomPath)

    const input = page.locator('[data-testid="chat-input"]')
    await input.waitFor({ timeout: 15_000 })
    await input.fill('trigger input_required state')
    await input.press('Enter')

    const hitlInput = page.locator('[data-testid="hitl-reply-input"]')
    await hitlInput.waitFor({ timeout: 30_000 })
    await hitlInput.fill('HITL reply content')
    await hitlInput.press('Enter')

    await expect(page.locator('[data-testid="hitl-reply-input"]')).toHaveCount(0, {
      timeout: 15_000,
    })
  })

  test('file attachment upload and send', async ({ roomPath, page, request }) => {
    // Earlier tests in this room (e.g. cancel) can leave a run settling; the
    // composer refuses to send while one is active, so wait for an idle room.
    const conversationId = roomPath.split('/').pop()
    const backendUrl = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000/api/v1'
    await expect.poll(async () => {
      const resp = await request.get(`${backendUrl}/rooms/${conversationId}/active-runs`)
      return resp.ok() ? ((await resp.json()).active_runs ?? []).length : -1
    }, { timeout: 60_000 }).toBe(0)

    await page.goto(roomPath)

    const fileName = `test-upload-${Date.now()}.txt`
    const attachmentButton = new RegExp(fileName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))

    const input = page.locator('[data-testid="chat-input"]')
    await input.waitFor({ timeout: 15_000 })

    // The attach button opens a hidden file input directly.
    const fileInput = page.locator('input[type="file"]').first()
    await fileInput.setInputFiles({
      name: fileName,
      mimeType: 'text/plain',
      buffer: Buffer.from('Test file content for upload'),
    })

    const preview = page.locator('[data-testid="attachment-preview"]')
    await expect(preview).toBeVisible({ timeout: 10_000 })
    await expect(page.locator(`text=${fileName}`)).toBeVisible()

    await input.fill('See attached file')
    await input.press('Enter')

    await expect(preview).toBeHidden({ timeout: 10_000 })

    // Sent attachments render as a download button on the user message.
    const sentAttachment = page
      .locator('.conversation-turn')
      .last()
      .getByRole('button', { name: attachmentButton })
    await expect(sentAttachment).toBeVisible({ timeout: 15_000 })

    // After a reload the attachment comes from the persisted message, not the
    // optimistic local copy.
    await page.reload()
    await expect(
      page.locator('.conversation-turn').getByRole('button', { name: attachmentButton }),
    ).toBeVisible({ timeout: 15_000 })
  })
})
