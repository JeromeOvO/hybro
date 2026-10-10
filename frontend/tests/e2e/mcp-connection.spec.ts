import { expect, test } from '@playwright/test'

for (const viewport of [{ width: 1280, height: 800 }, { width: 390, height: 844 }]) {
  test(`Access pages stay readable and keyboard accessible at ${viewport.width}px`, async ({ page, context }, testInfo) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write'])
    await page.setViewportSize(viewport)
    let status = 'ready'
    await page.route('**/hybro-mcp', route => route.fulfill({ json: { service: 'hybro-mcp', status } }))
    await page.goto('/access/mcp')
    await page.getByRole('button', { name: 'Copy URL' }).click()
    await expect(page.getByText('URL copied', { exact: true })).toBeVisible()
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('http://127.0.0.1:8001/mcp')
    await page.getByRole('button', { name: 'Copy configuration' }).click()
    await expect(page.getByText('Configuration copied', { exact: true })).toBeVisible()
    expect(JSON.parse(await page.evaluate(() => navigator.clipboard.readText()))).toEqual({
      mcpServers: { hybro: { type: 'http', url: 'http://127.0.0.1:8001/mcp' } }
    })
    const configTab = page.getByRole('tab', { name: 'Connection configuration' })
    await configTab.focus()
    await page.keyboard.press('ArrowRight')
    await expect(page.getByRole('tab', { name: 'AI setup prompt' })).toBeFocused()
    await expect(page.getByLabel('Access material', { exact: true })).toContainText('discover_agents({})')
    const tabListBounds = await page.getByRole('tablist', { name: 'Access materials' }).boundingBox()
    for (const tab of await page.getByRole('tab').all()) {
      const bounds = await tab.boundingBox()
      expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(tabListBounds!.y + tabListBounds!.height + 1)
    }
    await page.getByRole('button', { name: 'Copy prompt' }).click()
    await expect(page.getByText('Prompt copied', { exact: true })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath(`mcp-${viewport.width}.png`), fullPage: true })
    status = 'unsupported_auth'
    await page.getByRole('button', { name: 'Refresh MCP status' }).click()
    await expect(page.getByText('Unavailable with Clerk', { exact: true })).toBeVisible()
    status = 'unavailable'
    await page.getByRole('button', { name: 'Refresh MCP status' }).click()
    await expect(page.getByText('Unavailable', { exact: true })).toBeVisible()

    await page.goto('/access/api')
    await page.getByRole('button', { name: 'Copy request' }).click()
    await expect(page.getByText('Request copied', { exact: true })).toBeVisible()
    const request = await page.evaluate(() => navigator.clipboard.readText())
    expect(request).toContain('/agents/discovery')
    expect(request).not.toMatch(/authorization|bearer|access_token|group_id/i)
    await expect(page.getByLabel('Access material', { exact: true })).not.toContainText(/authorization|bearer|access_token|group_id/i)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath(`api-${viewport.width}.png`), fullPage: true })
  })
}
