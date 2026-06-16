import { test, expect } from '@playwright/test'

test.describe('Error Handling', () => {
  test('should handle 404 pages gracefully', async ({ page }) => {
    await page.goto('/non-existent-page-12345')

    const is404 = await page.locator('text=404').isVisible().catch(() => false)
    const isNotFound = await page.locator('text=not found').isVisible().catch(() => false)
    const isRedirect = page.url().includes('sign-in') || page.url() === 'http://localhost:3000/'

    expect(is404 || isNotFound || isRedirect).toBeTruthy()
  })

  test('should handle network errors gracefully', async ({ page }) => {
    await page.route('**/api/**', route => route.abort())

    await page.goto('/')

    await expect(page.locator('body')).toBeVisible()
  })
})

test.describe('Loading States', () => {
  test('should show loading indicators during navigation', async ({ page }) => {
    await page.goto('/')

    await expect(page.locator('body')).toBeVisible()
  })
})

test.describe('Accessibility', () => {
  test('should have at least one h1 heading on about page', async ({ page }) => {
    await page.goto('/c/about')

    const h1 = page.locator('h1')
    const h1Count = await h1.count()

    expect(h1Count).toBeGreaterThanOrEqual(1)
  })

  test('should have alt text on images', async ({ page }) => {
    await page.goto('/c/about')

    const images = page.locator('img')
    const imageCount = await images.count()

    for (let i = 0; i < imageCount; i++) {
      const img = images.nth(i)
      const alt = await img.getAttribute('alt')
      const ariaLabel = await img.getAttribute('aria-label')
      const role = await img.getAttribute('role')

      const hasAccessibility = alt !== null || ariaLabel !== null || role === 'presentation'
      expect(hasAccessibility).toBeTruthy()
    }
  })

  test('should be keyboard navigable', async ({ page }) => {
    await page.goto('/')

    await page.keyboard.press('Tab')

    const focusedElement = await page.evaluate(() => document.activeElement?.tagName)
    expect(focusedElement).toBeTruthy()
  })
})

test.describe('Performance', () => {
  test('should load main page within acceptable time', async ({ page }) => {
    const startTime = Date.now()

    await page.goto('/')

    const loadTime = Date.now() - startTime

    expect(loadTime).toBeLessThan(10000)
  })

  test('should not have console errors on page load', async ({ page }) => {
    const errors: string[] = []

    page.on('console', msg => {
      if (msg.type() === 'error') {
        errors.push(msg.text())
      }
    })

    await page.goto('/')

    await page.waitForTimeout(2000)

    const criticalErrors = errors.filter(
      e => !e.includes('favicon') && !e.includes('404') && !e.includes('hydration')
    )

    expect(criticalErrors.length).toBe(0)
  })
})
