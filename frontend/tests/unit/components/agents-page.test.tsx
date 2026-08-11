import { cleanup, render, screen, waitFor } from '../../utils/test-utils'
import userEvent from '@testing-library/user-event'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { Agent, AgentCard, AgentCenterResponse } from '@/lib/types'

const mockPush = vi.fn()
const mockGetAllAgents = vi.fn<() => Promise<AgentCenterResponse>>()
const mockGetAgentsByProviderId = vi.fn<() => Promise<AgentCenterResponse>>()
const mockDiscoverLocalAgents = vi.fn()

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush }),
}))

vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ getToken: vi.fn(async () => 'test-token') }),
}))

vi.mock('@/lib/api/agent', () => ({
  discoverLocalAgents: mockDiscoverLocalAgents,
  getAllAgents: mockGetAllAgents,
  getAgentsByProviderId: mockGetAgentsByProviderId,
}))

vi.mock('@/components/consumer-agent-card', () => ({
  ConsumerAgentCard: ({ agent }: { agent: Agent }) => (
    <div data-testid={`agent-${agent.agent_id}`}>{agent.agent_card.name}</div>
  ),
}))

function buildCard(name: string): AgentCard {
  return {
    name,
    description: `${name} description`,
    version: '1.0.0',
    protocolVersion: '1.0.0',
    url: 'http://localhost:8001',
    capabilities: {
      streaming: false,
      pushNotifications: false,
      stateTransitionHistory: false,
    },
    defaultInputModes: ['text/plain'],
    defaultOutputModes: ['text/plain'],
    skills: [],
  }
}

function buildAgent(
  id: string,
  name: string,
  overrides: Partial<Agent> = {},
): Agent {
  return {
    agent_id: id,
    provider_id: 'user_local_developer',
    agent_card: buildCard(name),
    agent_status: 'active',
    source: 'cloud',
    ...overrides,
  }
}

let AgentsPage: React.ComponentType

beforeEach(async () => {
  vi.clearAllMocks()
  mockGetAllAgents.mockResolvedValue({ success: true, agents: [] })
  mockGetAgentsByProviderId.mockResolvedValue({ success: true, agents: [] })
  mockDiscoverLocalAgents.mockResolvedValue({
    trigger: 'manual',
    open_ports: 1,
    agents_found: 1,
    agents_added: 1,
    agents_reactivated: 0,
    agents_deactivated: 0,
    duration_ms: 10,
    reused_running_discovery: false,
  })
  const mod = await import('@/app/(portal)/agents/page')
  AgentsPage = mod.default
})

afterEach(cleanup)

describe('AgentsPage', () => {
  it('shows registered Remote agents even when inactive', async () => {
    mockGetAgentsByProviderId.mockResolvedValue({
      success: true,
      agents: [buildAgent('remote-1', 'Remote Agent', { agent_status: 'inactive' })],
    })

    render(<AgentsPage />)

    expect(await screen.findByText('Remote Agent')).toBeInTheDocument()
  })

  it('only shows Local agents that are active and online', async () => {
    mockGetAllAgents.mockResolvedValue({
      success: true,
      agents: [
        buildAgent('local-online', 'Online Local', {
          source: 'hub',
          is_hub_online: true,
        }),
        buildAgent('local-offline', 'Offline Local', {
          source: 'hub',
          is_hub_online: false,
        }),
        buildAgent('local-inactive', 'Inactive Local', {
          source: 'hub',
          is_hub_online: true,
          agent_status: 'inactive',
        }),
      ],
    })

    render(<AgentsPage />)

    expect(await screen.findByText('Online Local')).toBeInTheDocument()
    expect(screen.queryByText('Offline Local')).not.toBeInTheDocument()
    expect(screen.queryByText('Inactive Local')).not.toBeInTheDocument()
  })

  it('discovers local agents and refreshes the catalog', async () => {
    render(<AgentsPage />)

    const button = await screen.findByRole('button', {
      name: 'Discover Local Agents',
    })
    await userEvent.click(button)

    await waitFor(() => expect(mockDiscoverLocalAgents).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(mockGetAllAgents.mock.calls.length).toBeGreaterThan(1))
  })

  it('shows directly discovered Local agents only while active', async () => {
    mockGetAllAgents.mockResolvedValue({
      success: true,
      agents: [
        buildAgent('local-active', 'Active Direct Local', { source: 'local' }),
        buildAgent('local-inactive', 'Inactive Direct Local', {
          source: 'local',
          agent_status: 'inactive',
        }),
      ],
    })

    render(<AgentsPage />)

    expect(await screen.findByText('Active Direct Local')).toBeInTheDocument()
    expect(screen.queryByText('Inactive Direct Local')).not.toBeInTheDocument()
  })

  it('lets users open the canonical registration page', async () => {
    render(<AgentsPage />)

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Register Agent' })).toBeInTheDocument()
    })
    await userEvent.click(screen.getByRole('button', { name: 'Register Agent' }))

    expect(mockPush).toHaveBeenCalledWith('/agents/new')
  })
})
