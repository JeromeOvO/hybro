import type { AgentGroup } from '@/lib/types/agent-group'
import type { NetworkAgent } from '@/components/networks/types'
import type { NetworkScope, StructureLayout } from '@/lib/network-url-state'
import { agentMatches } from '@/lib/network-search'
import { agentStatusRank } from '@/lib/status'

export type StructureSort = { by: 'calls' | 'name' | 'status'; direction: 'asc' | 'desc' }
export const DEFAULT_STRUCTURE_SORT: StructureSort = { by: 'calls', direction: 'desc' }

export interface StructureRow { key: string; agent: NetworkAgent; subnets: AgentGroup[]; tabbable: boolean }
export interface StructureGroup { key: string; title: string | null; count: number; rows: StructureRow[] }

function membership(networks: readonly AgentGroup[]) {
  const byAgent = new Map<string, AgentGroup[]>()
  for (const network of networks) {
    for (const id of new Set(network.agents)) byAgent.set(id, [...(byAgent.get(id) ?? []), network])
  }
  return byAgent
}

function compare(sort: StructureSort) {
  const sign = sort.direction === 'asc' ? 1 : -1
  return (x: NetworkAgent, y: NetworkAgent) => {
    const primary = sort.by === 'calls' ? x.roomCalls - y.roomCalls
      : sort.by === 'status' ? agentStatusRank(x.status) - agentStatusRank(y.status)
        : x.name.localeCompare(y.name)
    return primary * sign || x.name.localeCompare(y.name)
  }
}

export function buildStructureGroups(input: {
  agents: readonly NetworkAgent[]
  networks: readonly AgentGroup[]
  scope: NetworkScope
  layout: StructureLayout
  q: string
  sort: StructureSort
}): StructureGroup[] {
  const byAgent = membership(input.networks)
  const subnetsOf = (id: string) => byAgent.get(id) ?? []
  const matching = input.agents
    .filter(agent => agentMatches(agent, input.q, subnetsOf(agent.id).map(network => network.name)))
    .sort(compare(input.sort))
  const inScope = (agent: NetworkAgent, scope: NetworkScope) =>
    scope.kind === 'all' || (scope.kind === 'unassigned'
      ? subnetsOf(agent.id).length === 0
      : subnetsOf(agent.id).some(network => network.group_id === scope.id))
  const seen = new Set<string>()
  const row = (groupKey: string, agent: NetworkAgent): StructureRow => {
    const tabbable = !seen.has(agent.id)
    seen.add(agent.id)
    return { key: `${groupKey}:${agent.id}`, agent, subnets: subnetsOf(agent.id), tabbable }
  }

  if (input.layout === 'list') {
    const rows = matching.filter(agent => inScope(agent, input.scope)).map(agent => row('list', agent))
    return [{ key: 'list', title: null, count: rows.length, rows }]
  }

  const groups: StructureGroup[] = []
  if (input.scope.kind !== 'unassigned') {
    for (const network of input.networks) {
      if (input.scope.kind === 'subnet' && input.scope.id !== network.group_id) continue
      const rows = matching.filter(agent => inScope(agent, { kind: 'subnet', id: network.group_id })).map(agent => row(network.group_id, agent))
      groups.push({ key: network.group_id, title: network.name, count: rows.length, rows })
    }
  }
  if (input.scope.kind !== 'subnet') {
    const rows = matching.filter(agent => inScope(agent, { kind: 'unassigned' })).map(agent => row('unassigned', agent))
    groups.push({ key: 'unassigned', title: 'Unassigned', count: rows.length, rows })
  }
  return groups
}

export function scopeCounts(input: { agents: readonly NetworkAgent[]; networks: readonly AgentGroup[]; q: string }) {
  const byAgent = membership(input.networks)
  const matching = input.agents.filter(agent => agentMatches(agent, input.q, (byAgent.get(agent.id) ?? []).map(n => n.name)))
  const ids = new Set(matching.map(agent => agent.id))
  return {
    all: matching.length,
    unassigned: matching.filter(agent => !byAgent.has(agent.id)).length,
    subnets: new Map(input.networks.map(network => [network.group_id, [...new Set(network.agents)].filter(id => ids.has(id)).length])),
  }
}
