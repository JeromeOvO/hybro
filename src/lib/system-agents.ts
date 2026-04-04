/**
 * Built-in system agents that are not real A2A agents in the database.
 * These are synthetic agent IDs created by the backend's RoomCoordinatorService
 * to author summary messages.
 */

export interface SystemAgentInfo {
  name: string
  description: string
}

export const SYSTEM_AGENTS: Record<string, SystemAgentInfo> = {
  supervisor_hitl: {
    name: 'Question & Answer',
    description:
      'A built-in agent that facilitates human-in-the-loop interactions, collecting clarifications and confirmations from the user.',
  },
  summary: {
    name: 'Summary Agent',
    description:
      'A built-in agent that summarizes responses from multiple agents in a room.',
  },
  // Historical backward compatibility — all map to "Summary Agent"
  supervisor_synthesis: {
    name: 'Summary Agent',
    description:
      'A built-in agent that summarizes responses from multiple agents in a room.',
  },
  debate_summary: {
    name: 'Summary Agent',
    description:
      'A built-in agent that summarizes responses from multiple agents in a room.',
  },
  non_debate_summary: {
    name: 'Summary Agent',
    description:
      'A built-in agent that summarizes responses from multiple agents in a room.',
  },
}

export function isSystemAgent(agentId: string | undefined): boolean {
  return !!agentId && agentId in SYSTEM_AGENTS
}

export function getSystemAgentName(agentId: string): string | undefined {
  return SYSTEM_AGENTS[agentId]?.name
}
