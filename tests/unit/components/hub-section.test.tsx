import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import React from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { HubStatusResponse, HubStatus } from '@/lib/api/hub'
import type { AgentCenterResponse, Agent, AgentCard } from '@/lib/types'

/* ── Mocks ── */

vi.mock('@clerk/nextjs', () => ({
  useAuth: () => ({ getToken: async () => 'test-token' }),
}))

const mockGetMyHubStatus = vi.fn<() => Promise<HubStatusResponse>>()
vi.mock('@/lib/api/hub', () => ({
  getMyHubStatus: mockGetMyHubStatus,
}))

const mockGetAllActiveAgents = vi.fn<() => Promise<AgentCenterResponse>>()
vi.mock('@/lib/api/agent', () => ({
  getAllAgents: vi.fn().mockResolvedValue({ success: true, agents: [] }),
  getAllActiveAgents: mockGetAllActiveAgents,
}))

vi.mock('@/lib/time', () => ({
  formatTimestamp: (ts: string) => ts,
}))

vi.mock('@/lib/agent-avatar', () => ({
  getAgentAvatarUri: (seed: string) => `https://avatar.test/${seed}`,
}))

/* ── Helpers ── */

function buildHub(overrides: Partial<HubStatus> = {}): HubStatus {
  return {
    hub_id: 'hub-1',
    is_online: true,
    last_connected_at: '2026-03-07T10:00:00Z',
    agent_count: 0,
    ...overrides,
  }
}

function buildAgent(overrides: Partial<Agent> & { name?: string; description?: string } = {}): Agent {
  const { name, description, ...rest } = overrides
  return {
    agent_id: 'agent-1',
    provider_id: 'provider-1',
    agent_card: {
      name: name ?? 'Local LLM',
      description: description ?? 'A local agent',
      url: 'http://localhost:9000',
      version: '1.0.0',
    } as AgentCard,
    agent_status: 'active',
    source: 'hub',
    ...rest,
  }
}

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
  }
}

/* ── Lazy import (after mocks) ── */

let HubSection: React.ComponentType

beforeEach(async () => {
  vi.clearAllMocks()
  const mod = await import('@/components/settings/hub-section')
  HubSection = mod.HubSection
})

afterEach(() => {
  cleanup()
})

/* ── Tests ── */

describe('HubSection', () => {
  it('shows loading spinner while hub status is fetching', async () => {
    mockGetMyHubStatus.mockReturnValue(new Promise(() => {}))
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubSection />, { wrapper: createWrapper() })

    expect(screen.getByText('Loading hub status...')).toBeInTheDocument()
  })

  it('shows "No hub connected" when hub query returns empty list', async () => {
    mockGetMyHubStatus.mockResolvedValue({ hubs: [] })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubSection />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('No hub connected')).toBeInTheDocument()
    })
    expect(screen.getByText(/pip install hybro-hub/)).toBeInTheDocument()
  })

  it('shows "Hub Connected" when hub is online', async () => {
    mockGetMyHubStatus.mockResolvedValue({ hubs: [buildHub({ is_online: true })] })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubSection />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('Hub Connected')).toBeInTheDocument()
    })
  })

  it('shows "Hub Offline" when hub is not online', async () => {
    mockGetMyHubStatus.mockResolvedValue({
      hubs: [buildHub({ is_online: false })],
    })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubSection />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('Hub Offline')).toBeInTheDocument()
    })
    expect(screen.getByText('Hub is offline')).toBeInTheDocument()
  })

  it('shows "Connected since" timestamp when online', async () => {
    mockGetMyHubStatus.mockResolvedValue({
      hubs: [buildHub({ is_online: true, last_connected_at: '2026-03-07T10:00:00Z' })],
    })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubSection />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText(/Connected since/)).toBeInTheDocument()
    })
  })

  it('shows "Last seen" timestamp when offline', async () => {
    mockGetMyHubStatus.mockResolvedValue({
      hubs: [buildHub({ is_online: false, last_connected_at: '2026-03-07T09:00:00Z' })],
    })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubSection />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText(/Last seen/)).toBeInTheDocument()
    })
  })

  it('lists hub agents with name and description', async () => {
    mockGetMyHubStatus.mockResolvedValue({ hubs: [buildHub()] })
    mockGetAllActiveAgents.mockResolvedValue({
      success: true,
      agents: [
        buildAgent({ agent_id: 'a1', name: 'Code Helper', description: 'Writes code' }),
        buildAgent({ agent_id: 'a2', name: 'Chat Bot', description: 'Chats nicely' }),
      ],
    })

    render(<HubSection />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('Code Helper')).toBeInTheDocument()
    })
    expect(screen.getByText('Writes code')).toBeInTheDocument()
    expect(screen.getByText('Chat Bot')).toBeInTheDocument()
    expect(screen.getByText('Chats nicely')).toBeInTheDocument()
    expect(screen.getByText('Local Agents (2)')).toBeInTheDocument()
  })

  it('filters out non-hub agents', async () => {
    mockGetMyHubStatus.mockResolvedValue({ hubs: [buildHub()] })
    mockGetAllActiveAgents.mockResolvedValue({
      success: true,
      agents: [
        buildAgent({ agent_id: 'a1', name: 'Hub Agent', source: 'hub' }),
        buildAgent({ agent_id: 'a2', name: 'Cloud Agent', source: 'cloud' }),
      ],
    })

    render(<HubSection />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('Hub Agent')).toBeInTheDocument()
    })
    expect(screen.queryByText('Cloud Agent')).not.toBeInTheDocument()
    expect(screen.getByText('Local Agents (1)')).toBeInTheDocument()
  })

  it('shows empty state when hub is online but no agents registered', async () => {
    mockGetMyHubStatus.mockResolvedValue({ hubs: [buildHub({ is_online: true })] })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubSection />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(
        screen.getByText('Hub connected but no agents registered yet.'),
      ).toBeInTheDocument()
    })
  })

  it('invalidates queries when Refresh is clicked', async () => {
    mockGetMyHubStatus.mockResolvedValue({ hubs: [buildHub()] })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubSection />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('Hub Connected')).toBeInTheDocument()
    })

    mockGetMyHubStatus.mockClear()
    mockGetAllActiveAgents.mockClear()
    mockGetMyHubStatus.mockResolvedValue({ hubs: [buildHub()] })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    await userEvent.click(screen.getByRole('button', { name: /Refresh/ }))

    await waitFor(() => {
      expect(mockGetMyHubStatus).toHaveBeenCalled()
    })
    await waitFor(() => {
      expect(mockGetAllActiveAgents).toHaveBeenCalled()
    })
  })
})
