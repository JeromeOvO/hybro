import { expect, test, type Page } from '@playwright/test'
import type { Agent } from '@/lib/types/response'
import type { AgentGroup, AgentGroupUpdateRequest } from '@/lib/types/agent-group'

function catalogAgent(
  id: string,
  name: string,
  source: Agent['source'] = 'cloud',
  status: Agent['agent_status'] = 'active'
): Agent {
  return {
    agent_id: id,
    provider_id: 'inventory-fixture',
    source,
    agent_status: status,
    agent_card: {
      name,
      description: 'Browser topology fixture',
      url: 'https://example.test/agent',
      version: '1.0.0',
      protocolVersion: '0.3.0',
      capabilities: {},
      defaultInputModes: ['text'],
      defaultOutputModes: ['text'],
      skills: []
    }
  }
}

async function installInventory(page: Page, groups: AgentGroup[], discovered: Agent[] = [], registered: Agent[] = []) {
  const state = groups.map((group) => ({ ...group, agents: [...group.agents] }))
  const headers = {
    'access-control-allow-origin': 'http://localhost:3000',
    'access-control-allow-credentials': 'true'
  }
  await page.route('**/agentGroups**', async (route) => {
    const request = route.request()
    if (request.method() === 'GET') {
      await route.fulfill({ status: 200, headers, json: { success: true, groups: state } })
    } else if (request.method() === 'PUT') {
      const id = decodeURIComponent(new URL(request.url()).pathname.split('/').pop()!)
      const group = state.find((item) => item.group_id === id)
      if (!group) {
        await route.fulfill({ status: 404, headers, json: { success: false, error: 'Network not found' } })
        return
      }
      const update = request.postDataJSON() as Pick<AgentGroupUpdateRequest, 'agents' | 'name' | 'description'>
      if (update.name !== undefined) group.name = update.name
      if (update.description !== undefined) group.description = update.description
      if (update.agents) group.agents = [...new Set(update.agents)]
      await route.fulfill({ status: 200, headers, json: { success: true, group } })
    } else await route.abort()
  })
  await page.route('**/getAllAgents**', (route) =>
    route.fulfill({ status: 200, headers, json: { success: true, agents: discovered } })
  )
  await page.route('**/agent/getAgent/me', (route) =>
    route.fulfill({ status: 200, headers, json: { success: true, agents: registered } })
  )
}


test('pins only the dragged Agent while linked nodes react and releases it on pointer up', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await installInventory(page, [
    {
      group_id: 'spring-network',
      name: 'Spring fixture',
      owner_id: 'spring-fixture',
      type: 'user',
      agents: ['spring-a', 'spring-b', 'spring-c', 'spring-d']
    }
  ])
  await page.goto('/networks')
  await page.locator('.network-hub[data-network-id="spring-network"]').click()
  await expect(page.getByRole('heading', { name: 'Spring fixture', exact: true })).toBeVisible()
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  const agent = page.locator('.network-agent[data-agent-id="spring-a"] .network-agent-dot')
  const hub = page.locator('.network-hub[data-network-id="spring-network"] .network-hub-dot')
  const start = await agent.boundingBox()
  const origin = await hub.boundingBox()
  const x = start!.x + start!.width / 2
  const y = start!.y + start!.height / 2
  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x + 80, y + 35, { steps: 8 })
  await expect
    .poll(async () => {
      const moved = await hub.boundingBox()
      return Math.hypot(moved!.x - origin!.x, moved!.y - origin!.y)
    })
    .toBeGreaterThan(2)
  await page.waitForTimeout(6000)
  const held = await agent.boundingBox()
  expect(Math.hypot(held!.x + held!.width / 2 - x - 80, held!.y + held!.height / 2 - y - 35)).toBeLessThan(4)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.waitForTimeout(100)
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await page.mouse.move(x + 105, y + 35, { steps: 4 })
  await expect
    .poll(async () => {
      const moved = await agent.boundingBox()
      return Math.hypot(moved!.x + moved!.width / 2 - x - 105, moved!.y + moved!.height / 2 - y - 35)
    })
    .toBeLessThan(4)
  await page.mouse.up()
  await expect
    .poll(async () => {
      const released = await agent.boundingBox()
      return Math.hypot(released!.x + released!.width / 2 - x - 105, released!.y + released!.height / 2 - y - 35)
    })
    .toBeGreaterThan(5)
})

test('renders one shared Agent node and preserves it when one membership is removed', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.setViewportSize({ width: 390, height: 844 })
  await installInventory(
    page,
    [
      { group_id: 'shared', name: 'Network A', owner_id: 'fixture', type: 'user', agents: ['shared', 'local'] },
      { group_id: 'network-b', name: 'Network B', owner_id: 'fixture', type: 'user', agents: ['shared', 'missing'] }
    ],
    [
      catalogAgent('shared', 'Discovery shared'),
      catalogAgent('local', 'Local member with extended display name', 'local'),
      catalogAgent('solo', 'Discovered solo', 'local')
    ],
    [catalogAgent('shared', 'Registered shared'), catalogAgent('orphan', 'Registered orphan', 'cloud', 'inactive')]
  )
  await page.goto('/networks')
  await expect(page.locator('.network-agent')).toHaveCount(5)
  await expect(page.locator('.network-hub')).toHaveCount(2)
  await expect(page.locator('.network-member-edge')).toHaveCount(4)
  await expect(page.getByLabel('Current zoom', { exact: true })).toHaveText('141%')
  await page.getByRole('button', { name: 'Show all connections', exact: true }).click()
  await page.mouse.move(0, 0)
  const stage = await page.locator('.network-stage').boundingBox()
  for (const label of await page.locator('.network-agent-label').all()) {
    expect(await label.evaluate(element => Number(getComputedStyle(element).opacity))).toBeGreaterThan(0)
    const bounds = await label.boundingBox()
    expect(bounds!.x).toBeGreaterThanOrEqual(stage!.x)
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(stage!.x + stage!.width)
  }
  const shared = page.locator('.network-agent[data-agent-id="shared"]')
  await expect(shared).toHaveCount(1)
  await expect(shared).toHaveAccessibleName(/Registered shared/)
  await expect(page.locator('.network-hub[data-network-id="shared"]')).toHaveCount(1)
  await expect(page.locator('.network-member-edge[data-agent-id="shared"]')).toHaveCount(2)
  await page.locator('.network-hub[data-network-id="shared"]').dispatchEvent('click')
  await expect(page.locator('.network-agent:visible')).toHaveCount(5)
  await page.locator('.network-agent[data-agent-id="orphan"]').dispatchEvent('click')
  await expect(page.getByRole('heading', { name: 'Registered orphan', exact: true })).toBeVisible()
  await expect(page.locator('.network-member-edge[data-agent-id="orphan"]')).toHaveCount(0)
  await page.locator('.network-hub[data-network-id="shared"]').dispatchEvent('click')
  await page.getByRole('button', { name: 'Manage subnet', exact: true }).click()
  await page.getByRole('checkbox', { name: 'Select Registered shared', exact: true }).uncheck()
  await page.getByRole('textbox', { name: 'Name', exact: true }).fill('Updated subnet')
  await expect(page.locator('.network-member-edge[data-agent-id="shared"]')).toHaveCount(2)
  await page.getByRole('button', { name: 'Save changes', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Updated subnet', exact: true })).toBeVisible()
  await expect(page.locator('.network-member-edge[data-agent-id="shared"]')).toHaveCount(1)
  await expect(page.locator('.network-member-edge[data-agent-id="shared"][data-network-id="network-b"]')).toHaveCount(1)
  await expect(shared).toHaveCount(1)
  await expect(page.locator('.network-agent')).toHaveCount(5)
  await page.locator('.network-hub[data-network-id="network-b"]').dispatchEvent('click')
  await expect(page.getByRole('region', { name: 'Subnet details' }).getByText('Registered shared', { exact: true })).toBeVisible()
})

test('shows registered and discovered isolated Agents even without any Networks', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await installInventory(
    page,
    [],
    [
      catalogAgent('local-only', 'Discovered standalone', 'local'),
      catalogAgent('inactive-local', 'Inactive local record', 'local', 'inactive'),
      { ...catalogAgent('hub-offline', 'Offline hub Agent', 'hub'), is_hub_online: false }
    ],
    [catalogAgent('registered-only', 'Registered standalone', 'cloud', 'inactive')]
  )
  await page.goto('/networks')
  await expect(page.locator('.network-agent')).toHaveCount(2)
  await expect(page.locator('.network-agent[data-agent-id="registered-only"]')).toHaveCount(1)
  await expect(page.locator('.network-hub')).toHaveCount(0)
  await expect(page.locator('.network-member-edge')).toHaveCount(0)
  await expect(page.locator('.network-state')).toHaveCount(0)
  await page.getByRole('button', { name: 'Show all connections', exact: true }).click()
  await expect.poll(() => page.locator('.network-stage').evaluate(stage => {
    const bounds = stage.getBoundingClientRect()
    return [...stage.querySelectorAll('.network-agent-label')].every(label => {
      const rect = label.getBoundingClientRect()
      return rect.left >= bounds.left && rect.right <= bounds.right
    })
  })).toBe(true)
  await page.locator('.network-agent[data-agent-id="local-only"]').dispatchEvent('click')
  await expect(page.getByRole('heading', { name: 'Discovered standalone', exact: true })).toBeVisible()
})
