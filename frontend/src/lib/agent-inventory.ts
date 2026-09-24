import type { Agent } from '@/lib/types/response'

function isVisibleAgent(agent: Agent): boolean {
  if (agent.agent_status === 'deleted') return false

  if (agent.source === 'hub') {
    return agent.agent_status === 'active' && agent.is_hub_online === true
  }
  if (agent.source === 'local') {
    return agent.agent_status === 'active'
  }

  return true
}

export function mergeAgents(discovered: Agent[], registered: Agent[]): Agent[] {
  const agentsById = new Map<string, Agent>()

  for (const agent of discovered) {
    agentsById.set(agent.agent_id, agent)
  }

  // The registered response contains Remote agents that may be inactive and
  // therefore absent from public discovery. It is the authoritative copy.
  for (const agent of registered) {
    agentsById.set(agent.agent_id, agent)
  }

  return [...agentsById.values()].filter(isVisibleAgent)
}
