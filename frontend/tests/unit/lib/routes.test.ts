import { describe, expect, it } from 'vitest'

import { routes } from '@/lib/routes'

describe('canonical portal routes', () => {
  it('defines the unified public and management paths', () => {
    expect(routes.home).toBe('/')
    expect(routes.chat).toBe('/chat')
    expect(routes.room('room/id')).toBe('/room/room%2Fid')
    expect(routes.agents).toBe('/agents')
    expect(routes.agent('agent/id')).toBe('/agents/agent%2Fid')
    expect(routes.hub).toBe('/hub')
    expect(routes.manage.agents).toBe('/manage/agents')
    expect(routes.manage.register).toBe('/manage/agents/new')
    expect(routes.manage.apiKeys).toBe('/manage/api-keys')
    expect(routes.manage.inspector).toBe('/manage/inspector')
  })
})
