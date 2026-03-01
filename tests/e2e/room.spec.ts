import { test, expect } from '@playwright/test'

async function waitForRoomAuthDecision(page: import('@playwright/test').Page, timeoutMs = 20000) {
  await page.waitForFunction(
    () => {
      if (window.location.pathname.includes('sign-in')) return true
      const text = document.body.innerText
      return !text.includes('Loading room')
    },
    { timeout: timeoutMs },
  )
}

test.describe('Room Auth Gate', () => {
  test('unauthenticated user must not reach room settings UI', async ({ page }) => {
    await page.goto('/c/room/test-room-id')

    // Wait for Clerk hydration to complete before checking.
    await waitForRoomAuthDecision(page)

    const settingsBtn = page.locator('button[aria-label="Room settings"]')
    await expect(settingsBtn).not.toBeVisible()
  })

  test('room page should not throw JS errors', async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', (err) => errors.push(err.message))

    await page.goto('/c/room/test-room-id')

    // Wait for auth decision so we capture errors from the full lifecycle.
    await waitForRoomAuthDecision(page)

    expect(errors).toHaveLength(0)
  })
})

test.describe('Agent Selection', () => {
  test('should display agents page with title', async ({ page }) => {
    await page.goto('/c/agents')
    await expect(page).toHaveTitle(/Hybro/)
    await expect(page.locator('body')).toBeVisible()
  })
})

test.describe('Room Settings Auth Gate', () => {
  test('unauthenticated user must not see room settings form', async ({ page }) => {
    await page.goto('/c/room/test-room/settings')

    // /c/room/test-room/settings either hits the room page (which has auth gate)
    // or returns 404. Either way, a settings form must not appear.
    // Wait for any loading to resolve first.
    await page.waitForFunction(
      () => {
        if (window.location.pathname.includes('sign-in')) return true
        const text = document.body.innerText
        return !text.includes('Loading room') && !text.includes('Loading')
      },
      { timeout: 20000 },
    )

    const settingsForm = page.locator('form, [data-testid="room-settings"]')
    await expect(settingsForm).not.toBeVisible()
  })
})
