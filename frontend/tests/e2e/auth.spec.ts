import { test, expect } from '@playwright/test'

test.describe('Local auth adapter', () => {
  test('keeps the intentional signed-in identity on room routes', async ({ page }) => {
    await page.goto('/room/test-room')

    await expect(page).toHaveURL(/\/room\/test-room$/)
    await expect(page.getByText('Room not found')).toBeVisible({ timeout: 20000 })
    await expect(page).not.toHaveURL(/sign-in/)
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
    await page.goto('/about')
    await expect(page.getByRole('heading', { name: /One network. Every agent./i })).toBeVisible()
  })

  test('should load the pricing page', async ({ page }) => {
    await page.goto('/pricing')
    await expect(page.getByRole('heading', { name: /Simple, Transparent Pricing Plans/i })).toBeVisible()
  })

  test('should load the agents page', async ({ page }) => {
    await page.goto('/agents')
    await expect(page.getByRole('heading', { name: 'Explore Agents' })).toBeVisible()
  })
})

test.describe('Navigation', () => {
  test('should navigate to agents page via sidebar', async ({ page }) => {
    // The sidebar only exists on /* routes — the root is a marketing landing page.
    await page.goto('/chat')

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

test.describe('Sidebar local identity', () => {
  test('renders the signed-in user control and no sign-in action', async ({ page }) => {
    await page.goto('/chat?agentId=test-agent-nav')

    await expect(page.getByTestId('sidebar-sign-in')).toHaveCount(0)
    await expect(page.getByRole('button', { name: /Developer Local/i })).toBeVisible()
  })
})

test.describe('Manage registration', () => {
  test('register page loads under the local identity', async ({ page }) => {
    await page.goto('/manage/agents/new')

    await page.waitForFunction(
      () => !document.querySelector('.animate-spin'),
      { timeout: 15000 },
    )

    // The page heading should be visible — confirms the page loaded correctly.
    await expect(page.getByRole('heading', { name: /register agent/i })).toBeVisible({ timeout: 5000 })

    // The register button is only shown after a successful agent inspection.
    const registerBtn = page.getByRole('button', { name: /^register agent$/i })
    await expect(registerBtn).not.toBeVisible()
  })
})
