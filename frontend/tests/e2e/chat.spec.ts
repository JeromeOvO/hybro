import { test, expect } from '@playwright/test'

test.describe('Chat Flow', () => {
  test('should display the main page', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveTitle(/Hybro/i)
  })

  test('should have responsive layout on mobile', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 })
    await page.goto('/')
    await expect(page.locator('body')).toBeVisible()
  })

  test('should have responsive layout on desktop', async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 })
    await page.goto('/')
    await expect(page.locator('body')).toBeVisible()
  })
})

test.describe('Chat Page (public render, auth on submit)', () => {
  // The chat page renders its UI for all users, including unauthenticated.
  // Auth is checked only on submit — it redirects to /sign-in?redirect_url=<current path + query>.
  // These tests verify the page loads correctly without auth.

  test('should render chat page with quick-start templates', async ({ page }) => {
    await page.goto('/c/chat')

    // Wait for Clerk isLoaded (the spinner disappears)
    await page.waitForFunction(
      () => !document.querySelector('.animate-spin'),
      { timeout: 15000 },
    )

    // Quick-start templates should be visible
    const templates = page.locator('text=Travel Plan')
    await expect(templates.first()).toBeVisible({ timeout: 5000 })
  })

  test('unauthenticated submit should redirect to sign-in', async ({ page }) => {
    await page.goto('/c/chat')

    // Wait for Clerk to finish loading (spinner disappears).
    // If this times out, Clerk failed to hydrate — which is a real failure.
    await page.waitForFunction(
      () => !document.querySelector('.animate-spin'),
      { timeout: 15000 },
    )

    // The chat page uses a contenteditable div, not a textarea.
    const chatInput = page.locator('[contenteditable="true"]')
    await expect(chatInput).toBeVisible({ timeout: 5000 })

    // Type a message and submit
    await chatInput.click()
    await page.keyboard.type('Test message from unauthenticated user')
    await page.keyboard.press('Enter')

    // After submit, unauthenticated user should be redirected to /sign-in
    await expect(page).toHaveURL(/sign-in/, { timeout: 10000 })
  })

  test('unauthenticated submit from agent link should preserve agentId in redirect_url', async ({ page }) => {
    // Simulate arriving via "Chat with this agent" on the agent profile page
    await page.goto('/c/chat?agentId=test-agent-123')

    await page.waitForFunction(
      () => !document.querySelector('.animate-spin'),
      { timeout: 15000 },
    )

    const chatInput = page.locator('[contenteditable="true"]')
    await expect(chatInput).toBeVisible({ timeout: 5000 })

    await chatInput.click()
    await page.keyboard.type('Hello agent')
    await page.keyboard.press('Enter')

    // After redirect, the sign-in URL must include redirect_url with the agentId param
    // so the user returns to the correct agent chat after login.
    await expect(page).toHaveURL(/sign-in/, { timeout: 10000 })
    const url = page.url()
    expect(url).toContain('redirect_url')
    expect(decodeURIComponent(url)).toContain('agentId=test-agent-123')
  })

  test('unauthenticated submit preserves both agentId and prompt params in redirect_url', async ({ page }) => {
    // Simulate arriving via "Chat with this agent" with a pre-filled prompt
    await page.goto('/c/chat?agentId=test-agent-456&prompt=Hello%20world')

    await page.waitForFunction(
      () => !document.querySelector('.animate-spin'),
      { timeout: 15000 },
    )

    const chatInput = page.locator('[contenteditable="true"]')
    await expect(chatInput).toBeVisible({ timeout: 5000 })

    await chatInput.click()
    await page.keyboard.press('Enter')

    await expect(page).toHaveURL(/sign-in/, { timeout: 10000 })
    const url = page.url()
    const decoded = decodeURIComponent(url)
    expect(decoded).toContain('agentId=test-agent-456')
    // The prompt value may be encoded as + or %20 for spaces; check the key is present
    // and that the value survived the redirect round-trip.
    expect(decoded).toMatch(/prompt=Hello[+ ]world/)
  })
})

test.describe('Page Content', () => {
  test('should render about page with main content area', async ({ page }) => {
    await page.goto('/c/about')

    // The c/ layout renders two <main> elements:
    //   1. SidebarInset: <main data-slot="sidebar-inset">
    //   2. Layout content: <main class="flex flex-1 flex-col min-w-0"> (no side padding — pages own their own padding)
    // Target the inner content main via :not([data-slot]).
    const contentMain = page.locator('main:not([data-slot])')
    await expect(contentMain).toBeVisible()
  })
})

test.describe('Theme Toggle', () => {
  test('should toggle between light and dark themes', async ({ page }) => {
    // The sidebar (and theme toggle) only exists on /c/* routes, not the marketing homepage.
    await page.goto('/c/chat')

    // ThemeToggle renders in two places depending on auth state:
    //   - Unauthenticated: small button in the sidebar footer (always visible)
    //   - Authenticated: inside the user dropdown menu (must be opened first)
    // Wait for Clerk to finish loading either way.
    const clerkLoaded = await page.waitForFunction(
      () => {
        const sidebar = document.querySelector('[data-slot="sidebar"]')
        if (!sidebar) return false
        const skeletons = sidebar.querySelectorAll('.animate-pulse')
        return skeletons.length === 0
      },
      { timeout: 15000 },
    ).then(() => true).catch(() => false)

    if (!clerkLoaded) {
      test.fail(true, 'Clerk did not hydrate within 15s — theme toggle requires Clerk test keys to be configured')
      return
    }

    // Dismiss the cookie consent banner if present — it overlays the sidebar
    // footer and blocks pointer events on the theme toggle button.
    const declineBtn = page.getByRole('button', { name: 'Decline' })
    if (await declineBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
      await declineBtn.click()
    }

    const htmlElement = page.locator('html')
    const initialClass = await htmlElement.getAttribute('class')

    // Try the unauthenticated path: standalone toggle button in sidebar footer
    const themeToggle = page.getByRole('button', { name: 'Toggle theme' })
    const isDirectVisible = await themeToggle.isVisible({ timeout: 2000 }).catch(() => false)

    if (isDirectVisible) {
      await themeToggle.scrollIntoViewIfNeeded()
      await themeToggle.click()
    } else {
      // Authenticated path: open user dropdown, navigate to Theme submenu
      const userMenuTrigger = page.locator('[data-slot="sidebar"] button[title]').first()
      await userMenuTrigger.click()
      const themeMenuItem = page.getByRole('menuitem', { name: /theme/i })
      await expect(themeMenuItem).toBeVisible({ timeout: 5000 })
      await themeMenuItem.click()
      // Click one of the explicit theme options (Light or Dark)
      const htmlClass = await htmlElement.getAttribute('class')
      const isDark = htmlClass?.includes('dark')
      const targetOption = page.getByRole('menuitemradio', { name: isDark ? 'Light' : 'Dark' })
      await expect(targetOption).toBeVisible({ timeout: 3000 })
      await targetOption.click()
    }

    await page.waitForTimeout(500)

    const newClass = await htmlElement.getAttribute('class')
    expect(newClass).not.toBe(initialClass)
  })
})
