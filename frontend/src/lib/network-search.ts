import type { AgentGroup } from '@/lib/types/agent-group'
import type { NetworkAgent } from '@/components/networks/types'

export function normalizeQuery(query: string): string {
  return query.trim().toLowerCase()
}

const includes = (value: string | null | undefined, needle: string) => Boolean(value?.toLowerCase().includes(needle))

export function agentMatches(agent: NetworkAgent, query: string, subnetNames: readonly string[]): boolean {
  const needle = normalizeQuery(query)
  if (!needle) return true
  return [agent.name, agent.description, agent.provider, ...agent.skills, ...agent.tags, ...subnetNames]
    .some((value) => includes(value, needle))
}

export function subnetMatches(network: AgentGroup, query: string): boolean {
  const needle = normalizeQuery(query)
  return !needle || includes(network.name, needle) || includes(network.description, needle)
}

export function searchNodeKeys(
  query: string,
  agents: readonly NetworkAgent[],
  networks: readonly AgentGroup[],
): Set<string> | null {
  if (!normalizeQuery(query)) return null
  const keys = new Set<string>()
  for (const network of networks) if (subnetMatches(network, query)) keys.add(`network:${network.group_id}`)
  // Subnet names are not agent fields here, so a subnet hit highlights the hub and its edges, not every member.
  for (const agent of agents) if (agentMatches(agent, query, [])) keys.add(`agent:${agent.id}`)
  return keys
}
