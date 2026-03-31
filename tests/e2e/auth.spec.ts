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

  test('RequireAuth redirect_url preserves the room path so login returns to the room', async ({ page }) => {
    const roomId = 'test-room-redirect'
    await page.goto(`/c/room/${roomId}`)

    await waitForRoomAuthDecision(page)
    await page.waitForTimeout(1500)

    const url = page.url()

    // If RequireAuth redirected to sign-in, the redirect_url must point back to
    // the room so the user lands there after login — not on the home page.
    if (url.includes('sign-in')) {
      expect(
        decodeURIComponent(url),
        'redirect_url should contain the room path'
      ).toContain(`/room/${roomId}`)
    }
    // If "Room not found" is shown the room path was reached correctly — pass.
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
    // The sidebar only exists on /c/* routes — the root is a marketing landing page.
    await page.goto('/c/chat')

    // Wait for Clerk to finish loading so the sidebar fully renders.
    await page.waitForFunction(
      () => {
        const sidebar = document.querySelector('[data-slot="sidebar"]')
        if (!sidebar) return false
        return sidebar.querySelectorAll('.animate-pulse').length === 0
      },
      { timeout: 15000 },
    )

    // The nav item href is "/agents" which Next.js resolves as-is; match by visible
    // text content in the sidebar instead of href, which may vary by route context.
    const agentsLink = page.locator('[data-slot="sidebar"] a').filter({ hasText: 'Explore Agents' }).first()
    await expect(agentsLink).toBeVisible({ timeout: 5000 })
    await agentsLink.click()
    await expect(page).toHaveURL(/agents/)
  })
})

test.describe('Sidebar Sign-in Button', () => {
  // Wait for Clerk to finish hydrating so NavUser renders either the sign-in button
  // or the user avatar — never the loading skeleton (.animate-pulse).
  async function waitForSidebarSignInButton(page: import('@playwright/test').Page) {
    await page.waitForFunction(
      () => {
        const sidebar = document.querySelector('[data-slot="sidebar"]')
        if (!sidebar) return false
        return sidebar.querySelectorAll('.animate-pulse').length === 0
      },
      { timeout: 15000 },
    )
  }

  test('sidebar sign-in button preserves path and query params in redirect_url', async ({ page }) => {
    // Navigate to the chat page with an agentId param — the exact scenario from
    // "Chat with this agent" on the agent profile page.
    await page.goto('/c/chat?agentId=test-agent-nav')

    await waitForSidebarSignInButton(page)

    // If the dev server has an active Clerk session the user is already signed in —
    // the sign-in button never renders in that case. Skip rather than fail.
    const signInBtn = page.locator('[title="Sign in"]')
    const isSignInVisible = await signInBtn.isVisible({ timeout: 2000 }).catch(() => false)
    if (!isSignInVisible) {
      test.skip()
      return
    }

    // Dismiss cookie consent if it overlays the sidebar footer (only needed when unauthenticated)
    const declineBtn = page.getByRole('button', { name: 'Decline' })
    if (await declineBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
      await declineBtn.click({ force: true })
    }

    await expect(signInBtn).toBeVisible({ timeout: 5000 })
    await signInBtn.click({ force: true })

    // After clicking, the sidebar button does window.location.href = /sign-in?redirect_url=...
    await expect(page).toHaveURL(/sign-in/, { timeout: 10000 })
    const decoded = decodeURIComponent(page.url())
    expect(decoded).toContain('redirect_url')
    expect(decoded).toContain('/chat')
    expect(decoded).toContain('agentId=test-agent-nav')
  })
})

test.describe('Developer Portal Auth', () => {
  test('register page loads for unauthenticated users', async ({ page }) => {
    // The register page is publicly accessible — auth is only checked when
    // the user clicks "Register Agent" after completing the inspection flow.
    // This test verifies the page renders without crashing for unauthenticated users.
    await page.goto('/d/register')

    await page.waitForFunction(
      () => !document.querySelector('.animate-spin'),
      { timeout: 15000 },
    )

    // The page heading should be visible — confirms the page loaded correctly.
    await expect(page.getByRole('heading', { name: /register agent/i })).toBeVisible({ timeout: 5000 })

    // The register button is only shown after a successful agent inspection,
    // so it should not be visible yet for a fresh unauthenticated page load.
    const registerBtn = page.getByRole('button', { name: /^register agent$/i })
    await expect(registerBtn).not.toBeVisible()
  })
})
