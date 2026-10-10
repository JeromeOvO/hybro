import { expect, test } from '@playwright/test'

test.describe('Unified portal routing', () => {
  test('serves canonical routes and redirects legacy management paths', async ({ page, request }) => {
    for (const path of [
      '/',
      '/core',
      '/about',
      '/pricing',
      '/chat',
      '/agents',
      '/agents/new',
      '/networks',
    ]) {
      const response = await request.get(path)
      expect(response.status(), `${path} should resolve`).toBe(200)
    }

    await page.goto('/')
    await expect(page).toHaveURL(/\/core$/)

    await page.goto('/manage')
    await expect(page).toHaveURL(/\/agents$/)

    await page.goto('/manage/agents/new')
    await expect(page).toHaveURL(/\/agents\/new$/)
  })

  test('does not retain retired routes', async ({ request }) => {
    for (const path of [
      '/c',
      '/c/chat',
      '/d',
      '/d/agents',
      '/hub',
      '/manage/api-keys',
      '/manage/inspector',
    ]) {
      const response = await request.get(path)
      expect(response.status(), `${path} should be retired`).toBe(404)
    }
  })

  for (const width of [1440, 390]) {
    test(`uses the original shared menu on core and feature pages at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      await page.route('**/agent/getAllAgents**', route => route.fulfill({ json: { success: true, agents: [] } }))
      await page.route('**/agent/getAgent/me', route => route.fulfill({ json: { success: true, agents: [] } }))
      await page.route('**/agentGroups**', route => route.fulfill({ json: { success: true, groups: [] } }))
      await page.route('**/roomCenter/history**', route => route.fulfill({ json: { items: [{
        room_id: 'navigation-history',
        title: 'Navigation history',
        last_activity_at: '2026-08-01T00:00:00Z',
        is_pinned: false,
        pin_order: null,
        status: 'idle',
      }] } }))

      const openNavigation = async () => {
        if (width < 768) await page.getByRole('button', { name: 'Toggle Sidebar', exact: true }).click()
      }
      const modules = page.getByRole('navigation', { name: 'Modules', exact: true })
      await page.goto('/core')
      await openNavigation()
      await expect(page.locator('[data-sidebar="sidebar"]')).toHaveCount(1)
      await expect(page.getByRole('region', { name: 'History', exact: true })).toHaveCount(0)
      const networkMenu = modules.getByRole('button', { name: 'Network', exact: true })
      await networkMenu.click()
      await expect(modules.getByRole('link', { name: 'Agents', exact: true })).toBeVisible()
      await expect(page).toHaveURL(/\/core$/)
      await networkMenu.click()
      await expect(modules.getByRole('link', { name: 'Agents', exact: true })).toBeHidden()
      if (width >= 768) {
        await page.getByRole('button', { name: 'Collapse sidebar', exact: true }).click()
      }
      await networkMenu.click()
      await expect(modules.getByRole('link', { name: 'Agents', exact: true })).toBeVisible()
      await modules.getByRole('link', { name: 'Agents', exact: true }).click()
      await expect(page).toHaveURL(/\/agents$/)
      if (width < 768) await expect(page.getByRole('dialog')).toHaveCount(0)

      await openNavigation()
      const accessMenu = modules.getByRole('button', { name: 'Access', exact: true })
      if (await accessMenu.getAttribute('aria-expanded') !== 'true') await accessMenu.click()
      await modules.getByRole('link', { name: 'API', exact: true }).click()
      await expect(page).toHaveURL(/\/access\/api$/)
      await openNavigation()
      await expect(page.locator('[data-sidebar="sidebar"]')).toHaveCount(1)
      await expect(page.getByRole('region', { name: 'History', exact: true })).toHaveCount(0)
      await modules.getByRole('link', { name: 'Chat', exact: true }).click()
      await expect(page).toHaveURL(/\/chat$/)
      await openNavigation()
      const history = page.getByRole('region', { name: 'History', exact: true })
      await expect(history).toBeVisible()
      if (width >= 768) {
        const primary = await page.locator('[data-sidebar="sidebar"]').boundingBox()
        const secondary = await page.getByRole('complementary', { name: 'Chat history', exact: true }).boundingBox()
        expect(secondary!.x).toBeGreaterThanOrEqual(primary!.x + primary!.width)
        await page.getByRole('button', { name: 'Collapse sidebar', exact: true }).click()
        await expect(history).toBeVisible()
      }
      const historyTrigger = history.getByRole('button', { name: 'History', exact: true })
      await historyTrigger.click()
      await expect(historyTrigger).toHaveAttribute('aria-expanded', 'false')
      await expect(history).toBeVisible()
      await historyTrigger.click()
      await expect(historyTrigger).toHaveAttribute('aria-expanded', 'true')
      if (width >= 768) {
        const column = page.getByRole('complementary', { name: 'Chat history' })
        const home = column.getByRole('link', { name: 'New chat', exact: true })
        const homeBox = await home.boundingBox()
        const historyBox = await historyTrigger.boundingBox()
        expect(homeBox!.y).toBeLessThan(historyBox!.y)
        await page.getByRole('link', { name: 'Navigation history', exact: true }).click()
        await expect(page).toHaveURL(/\/room\/navigation-history$/)
        await home.click()
        await expect(page).toHaveURL(/\/chat$/)
      }
      await expect(page.getByRole('link', { name: 'Navigation history', exact: true })).toBeVisible()
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    })
  }
})
