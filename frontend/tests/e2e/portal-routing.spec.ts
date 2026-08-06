import { expect, test } from '@playwright/test'

test.describe('Unified portal routing', () => {
  test('serves canonical routes and redirects /manage to the agent list', async ({ page, request }) => {
    for (const path of [
      '/',
      '/about',
      '/pricing',
      '/chat',
      '/agents',
      '/manage/agents',
      '/manage/agents/new',
    ]) {
      const response = await request.get(path)
      expect(response.status(), `${path} should resolve`).toBe(200)
    }

    await page.goto('/manage')
    await expect(page).toHaveURL(/\/manage\/agents$/)
    await expect(page.getByRole('button', { name: 'Manage' })).toBeVisible()
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
})
