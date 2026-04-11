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

/** Supervisor-specific system agent IDs. Used for isSupervisorTurn derivation. */
const SUPERVISOR_SYSTEM_AGENT_IDS = new Set(['supervisor_hitl', 'supervisor_synthesis'])

/** Summary-family system agent IDs. Used for HYBRO AI visual treatment.
 *  Excludes supervisor_hitl which is NOT a summary agent. */
const SUMMARY_SYSTEM_AGENT_IDS = new Set([
  'supervisor_synthesis',
  'debate_summary',
  'non_debate_summary',
  'summary',
])

export function isSystemAgent(agentId: string | undefined): boolean {
  return !!agentId && agentId in SYSTEM_AGENTS
}

export function isSupervisorSystemAgent(agentId: string | undefined): boolean {
  return !!agentId && SUPERVISOR_SYSTEM_AGENT_IDS.has(agentId)
}

export function isSummarySystemAgent(agentId: string | undefined): boolean {
  return !!agentId && SUMMARY_SYSTEM_AGENT_IDS.has(agentId)
}

export function getSystemAgentName(agentId: string): string | undefined {
  return SYSTEM_AGENTS[agentId]?.name
}
