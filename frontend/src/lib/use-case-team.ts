import {
  createAgentGroup,
  listAgentGroups,
  updateAgentGroup,
} from '@/lib/api/agent-group'
import type { Agent } from '@/lib/types/agent'
import type { AgentGroup } from '@/lib/types/agent-group'
import { resolveTemplateAgents, type UseCaseTemplate } from '@/lib/use-case-templates'

const PRESET_MARKER_PREFIX = 'hybro-use-case:'

export function getUseCaseTeamDescription(template: UseCaseTemplate): string {
  return `Preset team for the “${template.title}” use case. [${PRESET_MARKER_PREFIX}${template.id}]`
}

export function findUseCaseTeam(
  groups: AgentGroup[],
  template: UseCaseTemplate,
): AgentGroup | undefined {
  const marker = `[${PRESET_MARKER_PREFIX}${template.id}]`
  return groups.find(
    group => group.type === 'user' && group.description?.includes(marker),
  )
}

function hasSameAgents(group: AgentGroup, agentIds: string[]): boolean {
  if (group.agents.length !== agentIds.length) return false
  const expectedIds = new Set(agentIds)
  return group.agents.every(agentId => expectedIds.has(agentId))
}

async function ensureUseCaseTeamAgents(
  group: AgentGroup,
  agentIds: string[],
  getToken?: () => Promise<string | null>,
): Promise<AgentGroup> {
  if (hasSameAgents(group, agentIds)) return group

  const response = await updateAgentGroup({
    group_id: group.group_id,
    agents: agentIds,
  }, getToken)
  if (!response.success || !response.group) {
    throw new Error(response.error || 'Failed to update preset team')
  }
  return response.group
}

interface EnsureUseCaseTeamOptions {
  template: UseCaseTemplate
  ownerId: string
  catalog: Agent[]
  getToken?: () => Promise<string | null>
}

/**
 * Return the user's persisted preset team, creating it once when absent.
 * A second list after a failed create recovers when another tab won the race.
 */
export async function ensureUseCaseTeam({
  template,
  ownerId,
  catalog,
  getToken,
}: EnsureUseCaseTeamOptions): Promise<AgentGroup> {
  const resolvedAgents = resolveTemplateAgents(template.agents, catalog)
  const agentIds = resolvedAgents.map(agent => agent.agent_id)
  const listResponse = await listAgentGroups(ownerId, getToken)

  if (!listResponse.success || !listResponse.groups) {
    throw new Error(listResponse.error || 'Failed to load saved teams')
  }

  const existingTeam = findUseCaseTeam(listResponse.groups, template)
  if (existingTeam) {
    return ensureUseCaseTeamAgents(existingTeam, agentIds, getToken)
  }

  const createResponse = await createAgentGroup({
    name: `${template.title} Team`,
    description: getUseCaseTeamDescription(template),
    owner_id: ownerId,
    agents: agentIds,
    preset_key: `${PRESET_MARKER_PREFIX}${template.id}`,
  }, getToken)

  if (createResponse.success && createResponse.group) {
    return ensureUseCaseTeamAgents(createResponse.group, agentIds, getToken)
  }

  const retryListResponse = await listAgentGroups(ownerId, getToken)
  const concurrentlyCreatedTeam = retryListResponse.groups
    ? findUseCaseTeam(retryListResponse.groups, template)
    : undefined
  if (concurrentlyCreatedTeam) {
    return ensureUseCaseTeamAgents(concurrentlyCreatedTeam, agentIds, getToken)
  }

  throw new Error(createResponse.error || 'Failed to create preset team')
}
