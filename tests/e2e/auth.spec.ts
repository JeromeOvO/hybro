import { test, expect } from '@playwright/test'

/**
 * Wait for the room page's Clerk hydration to complete.
 *
 * The room page shows "Loading room..." while `!isLoaded || loading`.
 * Once Clerk hydrates, it transitions to one of:
 *   - redirect to /sign-in  (auth gate fired)
 *   - "Room not found"      (room doesn't exist)
 *   - full room UI          (authenticated, room exists)
 *
 * If "Loading room..." never resolves within the timeout, the test fails —
 * catching the "stuck loading" case that a raw `not.toBeVisible` would miss.
 */
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

test.describe('Auth Gate', () => {
  test('unauthenticated user must not see room UI on protected page', async ({ page }) => {
    await page.goto('/c/room/test-room')

    // Phase 1: Wait for Clerk to finish loading (auth decision made).
    // Fails if page stays in "Loading room..." forever.
    await waitForRoomAuthDecision(page)

    // Phase 2: Now that the page has decided, the auth gate should have
    // prevented the room UI from rendering. These assertions are meaningful
    // because we know the loading phase is over.
    const settingsBtn = page.locator('button[aria-label="Room settings"]')
    const chatInput = page.locator('textarea')
    await expect(settingsBtn).not.toBeVisible()
    await expect(chatInput).not.toBeVisible()
  })

  test('unauthenticated user should be redirected or see non-authenticated state', async ({ page }) => {
    await page.goto('/c/room/test-room')

    // Wait for Clerk to make its decision — "Loading room..." must resolve.
    await waitForRoomAuthDecision(page)

    // RequireAuth fires a useEffect that calls window.location.href = /sign-in once
    // Clerk sets isSignedIn=false. Give the effect time to run and the navigation to
    // complete before reading the URL.
    await page.waitForTimeout(1500)

    // After auth decision + redirect window, the user must either:
    //   (a) have been redirected to /sign-in (RequireAuth fired)
    //   (b) see "Room not found" (room doesn't exist — still behind RequireAuth)
    // "Loading room..." is explicitly NOT acceptable — Clerk failed to hydrate.
    const url = page.url()
    const redirected = url.includes('sign-in')
    const showsNotFound = await page.getByText('Room not found').isVisible().catch(() => false)

    expect(
      redirected || showsNotFound,
      `Expected redirect to /sign-in or "Room not found" but got URL: ${url}`
    ).toBe(true)
  })
})

test.describe('Auth Pages', () => {
  test('should load sign-in page', async ({ page }) => {
    await page.goto('/sign-in')
    await expect(page).toHaveURL(/sign-in/)
  })

  test('should load sign-up page', async ({ page }) => {
    await page.goto('/sign-up')
    await expect(page).toHaveURL(/sign-up/)
  })
})

test.describe('Public Pages', () => {
  test('should load the about page', async ({ page }) => {
    await page.goto('/c/about')
    await expect(page).toHaveTitle(/Hybro/i)
  })

  test('should load the pricing page', async ({ page }) => {
    await page.goto('/c/pricing')
    await expect(page).toHaveTitle(/Hybro/i)
  })

  test('should load the agents page', async ({ page }) => {
    await page.goto('/c/agents')
    await expect(page).toHaveTitle(/Hybro/i)
  })
})

test.describe('Navigation', () => {
  test('should navigate to agents page via sidebar', async ({ page }) => {
    await page.goto('/')

    const agentsLink = page.locator('a:has-text("Explore Agents")').first()
    await expect(agentsLink).toBeVisible({ timeout: 5000 })
    await agentsLink.click()
    await expect(page).toHaveURL(/agents/)
  })
})
