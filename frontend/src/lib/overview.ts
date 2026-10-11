import type { AgentGroup } from '@/lib/types/agent-group'
import type { RoomHistoryItem } from '@/lib/api/room'
import type { McpCheck } from '@/hooks/useMcpStatus'
import type { NetworkAgent } from '@/components/networks/types'

export type AttentionItem =
  | { kind: 'room-input'; key: string; roomId: string; title: string }
  | { kind: 'gateway'; key: 'gateway'; status: Exclude<McpCheck, 'ready' | 'checking'> }
  | { kind: 'agent-offline'; key: string; agentId: string; name: string; status: NetworkAgent['status'] }
  | { kind: 'unassigned'; key: 'unassigned'; count: number }

export interface OverviewMetrics {
  agents: { total: number; online: number; offline: number }
  subnets: { total: number; unassigned: number }
  rooms: { total: number; running: number; needsInput: number }
}

function unassignedCount(agents: readonly NetworkAgent[], networks: readonly AgentGroup[]) {
  const assigned = new Set(networks.flatMap(network => network.agents))
  return agents.filter(agent => !assigned.has(agent.id)).length
}

export function overviewMetrics(input: { agents: readonly NetworkAgent[]; networks: readonly AgentGroup[]; rooms: readonly RoomHistoryItem[] }): OverviewMetrics {
  const online = input.agents.filter(agent => agent.status === 'active').length
  return {
    agents: { total: input.agents.length, online, offline: input.agents.length - online },
    subnets: { total: input.networks.length, unassigned: unassignedCount(input.agents, input.networks) },
    rooms: {
      total: input.rooms.length,
      running: input.rooms.filter(room => room.status === 'processing' || room.status === 'queued').length,
      needsInput: input.rooms.filter(room => room.status === 'awaiting_input').length,
    },
  }
}

export function deriveAttention(input: {
  agents: readonly NetworkAgent[]
  networks: readonly AgentGroup[]
  rooms: readonly RoomHistoryItem[]
  gateway: McpCheck
}): AttentionItem[] {
  const items: AttentionItem[] = input.rooms
    .filter(room => room.status === 'awaiting_input')
    .map(room => ({ kind: 'room-input', key: `room:${room.room_id}`, roomId: room.room_id, title: room.title }))
  if (input.gateway !== 'ready' && input.gateway !== 'checking') items.push({ kind: 'gateway', key: 'gateway', status: input.gateway })
  for (const agent of input.agents) {
    if (agent.status === 'inactive' || agent.status === 'unavailable') {
      items.push({ kind: 'agent-offline', key: `agent:${agent.id}`, agentId: agent.id, name: agent.name, status: agent.status })
    }
  }
  const unassigned = unassignedCount(input.agents, input.networks)
  if (unassigned) items.push({ kind: 'unassigned', key: 'unassigned', count: unassigned })
  return items
}

export function topAgentsByRoomCalls(agents: readonly NetworkAgent[], limit: number): NetworkAgent[] {
  return agents.filter(agent => agent.roomCalls > 0).sort((a, b) => b.roomCalls - a.roomCalls).slice(0, limit)
}
