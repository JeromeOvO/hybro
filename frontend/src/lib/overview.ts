import type { AgentGroup } from '@/lib/types/agent-group'
import type { RoomHistoryItem } from '@/lib/api/room'
import type { SessionSummary } from '@/lib/api/session'
import type { NetworkAgent } from '@/components/networks/types'

export interface OverviewMetrics {
  agents: { total: number; online: number; offline: number }
  subnets: { total: number; unassigned: number }
  rooms: { total: number; running: number; needsInput: number }
  sessions: { open: number; waiting: number }
}

function unassignedCount(agents: readonly NetworkAgent[], networks: readonly AgentGroup[]) {
  const assigned = new Set(networks.flatMap(network => network.agents))
  return agents.filter(agent => !assigned.has(agent.id)).length
}

export function overviewMetrics(input: { agents: readonly NetworkAgent[]; networks: readonly AgentGroup[]; rooms: readonly RoomHistoryItem[]; sessions: readonly SessionSummary[] }): OverviewMetrics {
  const online = input.agents.filter(agent => agent.status === 'active').length
  return {
    agents: { total: input.agents.length, online, offline: input.agents.length - online },
    subnets: { total: input.networks.length, unassigned: unassignedCount(input.agents, input.networks) },
    rooms: {
      total: input.rooms.length,
      running: input.rooms.filter(room => room.status === 'processing' || room.status === 'queued').length,
      needsInput: input.rooms.filter(room => room.status === 'awaiting_input').length,
    },
    sessions: {
      open: input.sessions.filter(session => session.state !== 'closed').length,
      waiting: input.sessions.filter(session => session.state === 'waiting_on_owner').length,
    },
  }
}

// Anything waiting on the owner comes first, then the most recent activity.
function byAttentionThenRecent<T extends { last_activity_at: string }>(needsYou: (item: T) => boolean) {
  return (a: T, b: T) => Number(needsYou(b)) - Number(needsYou(a))
    || Date.parse(b.last_activity_at) - Date.parse(a.last_activity_at)
}

export function recentRooms(rooms: readonly RoomHistoryItem[], limit: number): RoomHistoryItem[] {
  return [...rooms].sort(byAttentionThenRecent(room => room.status === 'awaiting_input')).slice(0, limit)
}

export function recentSessions(sessions: readonly SessionSummary[], limit: number): SessionSummary[] {
  return [...sessions].sort(byAttentionThenRecent(session => session.state === 'waiting_on_owner')).slice(0, limit)
}

/** Agents by how often Chat and Sessions called them (the agent's call_count). */
export function topAgentsByCalls(agents: readonly NetworkAgent[], limit: number): NetworkAgent[] {
  return agents.filter(agent => agent.roomCalls > 0).sort((a, b) => b.roomCalls - a.roomCalls).slice(0, limit)
}
