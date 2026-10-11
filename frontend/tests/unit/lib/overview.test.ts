import { describe, expect, it } from 'vitest'
import { deriveAttention, overviewMetrics, topAgentsByRoomCalls } from '@/lib/overview'
import { placeholderAgent } from '@/lib/network-agent'
import type { AgentGroup } from '@/lib/types/agent-group'
import type { RoomHistoryItem } from '@/lib/api/room'

const room = (id: string, status: RoomHistoryItem['status']): RoomHistoryItem => ({ room_id: id, title: id, last_activity_at: '2026-10-10T00:00:00Z', is_pinned: false, pin_order: null, status })
const agents = [
  { ...placeholderAgent('a', '', 'active'), roomCalls: 3 },
  { ...placeholderAgent('b', '', 'inactive'), name: 'travel-planner', roomCalls: 9 },
  { ...placeholderAgent('c', '', 'active'), roomCalls: 0 },
]
const networks = [{ group_id: 'R', name: 'R', agents: ['a', 'b'], type: 'user', owner_id: 'u' }] as AgentGroup[]
const rooms = [room('r1', 'awaiting_input'), room('r2', 'processing'), room('r3', 'idle')]

describe('overview', () => {
  it('computes metrics', () => {
    expect(overviewMetrics({ agents, networks, rooms })).toEqual({
      agents: { total: 3, online: 2, offline: 1 },
      subnets: { total: 1, unassigned: 1 },
      rooms: { total: 3, running: 1, needsInput: 1 },
    })
  })

  it('orders attention: rooms needing input, gateway, offline agents, unassigned', () => {
    expect(deriveAttention({ agents, networks, rooms, gateway: 'unavailable' }).map(item => item.kind))
      .toEqual(['room-input', 'gateway', 'agent-offline', 'unassigned'])
  })

  it('returns nothing when everything is running', () => {
    const healthy = [{ ...placeholderAgent('a', '', 'active') }]
    expect(deriveAttention({ agents: healthy, networks: [{ ...networks[0], agents: ['a'] }], rooms: [room('x', 'idle')], gateway: 'ready' })).toEqual([])
  })

  it('does not flag the gateway while it is still checking', () => {
    expect(deriveAttention({ agents: [], networks: [], rooms: [], gateway: 'checking' })).toEqual([])
  })

  it('ranks agents by room calls and drops zero-call agents', () => {
    expect(topAgentsByRoomCalls(agents, 5).map(agent => agent.id)).toEqual(['b', 'a'])
  })
})
