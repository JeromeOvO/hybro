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
})
