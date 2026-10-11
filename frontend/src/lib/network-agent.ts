import type { Agent } from '@/lib/types/response'
import type { NetworkAgent } from '@/components/networks/types'

// Only the fields read here, so both Agent shapes (types/agent.ts and types/response.ts) satisfy it.
export type ProjectableAgent = Pick<
  Agent,
  'agent_id' | 'agent_card' | 'agent_status' | 'source' | 'is_hub_online' | 'call_count' | 'call_success_count' | 'provider_name'
>

export function projectAgent(agent: ProjectableAgent): NetworkAgent {
  const card = agent.agent_card
  const skills = card.skills ?? []
  return {
    id: agent.agent_id,
    name: card.name || agent.agent_id,
    description: card.description ?? '',
    skills: skills.map((skill) => skill.name),
    tags: [...new Set(skills.flatMap((skill) => skill.tags ?? []))],
    status:
      agent.agent_status === 'active' && agent.source === 'hub' && agent.is_hub_online === false
        ? 'unavailable'
        : (agent.agent_status ?? 'unknown'),
    iconUrl: card.iconUrl || undefined,
    provider: card.provider?.organization || agent.provider_name || undefined,
    version: card.version || undefined,
    source: agent.source,
    roomCalls: agent.call_count ?? 0,
    roomCallSuccesses: agent.call_success_count ?? 0,
  }
}

export function placeholderAgent(id: string, description: string, status: NetworkAgent['status']): NetworkAgent {
  return { id, name: id, description, skills: [], tags: [], status, roomCalls: 0, roomCallSuccesses: 0 }
}
