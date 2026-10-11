import { describe, expect, it } from 'vitest'
import { projectAgent } from '@/lib/network-agent'
import type { Agent } from '@/lib/types/response'

describe('projectAgent', () => {
  it('projects card fields, tags, source, and room call counters', () => {
    const agent = {
      agent_id: 'a',
      agent_status: 'active',
      source: 'local',
      call_count: 7,
      call_success_count: 6,
      agent_card: {
        name: 'Planner', description: 'Plans', version: '1.2.0', iconUrl: 'https://x/icon.png',
        provider: { organization: 'Acme', url: 'https://acme' },
        skills: [{ id: 's', name: 'Plan', description: '', tags: ['trip', 'travel'] }],
      },
    } as unknown as Agent
    expect(projectAgent(agent)).toEqual({
      id: 'a', name: 'Planner', description: 'Plans', skills: ['Plan'], tags: ['trip', 'travel'],
      status: 'active', iconUrl: 'https://x/icon.png', provider: 'Acme', version: '1.2.0', source: 'local',
      roomCalls: 7, roomCallSuccesses: 6,
    })
  })

  it('marks an offline hub agent unavailable', () => {
    const agent = { agent_id: 'h', agent_status: 'active', source: 'hub', is_hub_online: false, agent_card: { name: 'H', skills: [] } } as unknown as Agent
    expect(projectAgent(agent).status).toBe('unavailable')
  })
})
