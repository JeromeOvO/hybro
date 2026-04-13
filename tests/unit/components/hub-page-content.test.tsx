import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import React from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { AgentCenterResponse, Agent, AgentCard } from '@/lib/types'

vi.mock('@clerk/nextjs', () => ({
  useAuth: () => ({ getToken: async () => 'test-token' }),
}))

const mockUseHubStatus = vi.fn()
const mockInvalidate = vi.fn()
vi.mock('@/hooks/useHubStatus', () => ({
  useHubStatus: () => mockUseHubStatus(),
  HUB_STATUS_QUERY_KEY: ['hub', 'status'],
}))

const mockGetAllActiveAgents = vi.fn<() => Promise<AgentCenterResponse>>()
vi.mock('@/lib/api/agent', () => ({
  getAllActiveAgents: (...args: unknown[]) => mockGetAllActiveAgents(...args),
}))

vi.mock('@/lib/time', () => ({
  formatTimestamp: (ts: string) => ts,
}))

vi.mock('next/link', () => ({
  default: ({ children, href, ...props }: any) => <a href={href} {...props}>{children}</a>,
}))

vi.mock('@/lib/agent-avatar', () => ({
  getAgentAvatarUri: (seed: string) => `https://avatar.test/${seed}`,
}))

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
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

let HubPageContent: React.ComponentType<{ apiKeysPath: string; basePath: string }>

beforeEach(async () => {
  vi.clearAllMocks()
  const mod = await import('@/components/hub-page-content')
  HubPageContent = mod.HubPageContent
})

afterEach(() => {
  cleanup()
})

describe('HubPageContent', () => {
  it('shows loading state when hub status is loading', () => {
    mockUseHubStatus.mockReturnValue({ hub: null, isOnline: false, hasHub: false, isLoading: true, invalidate: mockInvalidate })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubPageContent apiKeysPath="/d/keys" basePath="/c" />, { wrapper: createWrapper() })

    expect(screen.getByText('Loading hub status...')).toBeInTheDocument()
  })

  it('shows "No hub connected" when no hub exists', async () => {
    mockUseHubStatus.mockReturnValue({ hub: null, isOnline: false, hasHub: false, isLoading: false, invalidate: mockInvalidate })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubPageContent apiKeysPath="/d/keys" basePath="/c" />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('No hub connected')).toBeInTheDocument()
    })
    expect(screen.getByText(/pip install hybro-hub/)).toBeInTheDocument()
  })

  it('shows setup guide with API Keys link', async () => {
    mockUseHubStatus.mockReturnValue({ hub: null, isOnline: false, hasHub: false, isLoading: false, invalidate: mockInvalidate })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubPageContent apiKeysPath="/d/keys" basePath="/c" />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('Setup Guide')).toBeInTheDocument()
    })
    const link = screen.getByText('API Keys').closest('a')
    expect(link?.getAttribute('href')).toBe('/d/keys')
  })

  it('shows "Hub Connected" when hub is online', async () => {
    mockUseHubStatus.mockReturnValue({
      hub: { hub_id: 'h1', is_online: true, last_connected_at: '2026-01-01T00:00:00Z', agent_count: 0 },
      isOnline: true, hasHub: true, isLoading: false, invalidate: mockInvalidate,
    })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubPageContent apiKeysPath="/d/keys" basePath="/c" />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('Hub Connected')).toBeInTheDocument()
    })
  })

  it('shows "Hub Offline" when hub is not online', async () => {
    mockUseHubStatus.mockReturnValue({
      hub: { hub_id: 'h1', is_online: false, last_connected_at: '2026-01-01T00:00:00Z', agent_count: 0 },
      isOnline: false, hasHub: true, isLoading: false, invalidate: mockInvalidate,
    })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubPageContent apiKeysPath="/d/keys" basePath="/c" />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('Hub Offline')).toBeInTheDocument()
    })
  })

  it('shows "Connected since" timestamp when online', async () => {
    mockUseHubStatus.mockReturnValue({
      hub: { hub_id: 'h1', is_online: true, last_connected_at: '2026-01-01T00:00:00Z', agent_count: 0 },
      isOnline: true, hasHub: true, isLoading: false, invalidate: mockInvalidate,
    })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubPageContent apiKeysPath="/d/keys" basePath="/c" />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText(/Connected since/)).toBeInTheDocument()
    })
  })

  it('shows "Last seen" timestamp when offline', async () => {
    mockUseHubStatus.mockReturnValue({
      hub: { hub_id: 'h1', is_online: false, last_connected_at: '2026-01-01T00:00:00Z', agent_count: 0 },
      isOnline: false, hasHub: true, isLoading: false, invalidate: mockInvalidate,
    })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubPageContent apiKeysPath="/d/keys" basePath="/c" />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText(/Last seen/)).toBeInTheDocument()
    })
  })

  it('lists hub agents with name and description', async () => {
    mockUseHubStatus.mockReturnValue({
      hub: { hub_id: 'h1', is_online: true, last_connected_at: null, agent_count: 2 },
      isOnline: true, hasHub: true, isLoading: false, invalidate: mockInvalidate,
    })
    mockGetAllActiveAgents.mockResolvedValue({
      success: true,
      agents: [
        buildAgent({ agent_id: 'a1', name: 'Code Helper', description: 'Writes code' }),
        buildAgent({ agent_id: 'a2', name: 'Chat Bot', description: 'Chats nicely' }),
      ],
    })

    render(<HubPageContent apiKeysPath="/d/keys" basePath="/c" />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('Code Helper')).toBeInTheDocument()
    })
    expect(screen.getByText('Writes code')).toBeInTheDocument()
    expect(screen.getByText('Chat Bot')).toBeInTheDocument()
    expect(screen.getByText('Chats nicely')).toBeInTheDocument()
  })

  it('filters out non-hub agents', async () => {
    mockUseHubStatus.mockReturnValue({
      hub: { hub_id: 'h1', is_online: true, last_connected_at: null, agent_count: 1 },
      isOnline: true, hasHub: true, isLoading: false, invalidate: mockInvalidate,
    })
    mockGetAllActiveAgents.mockResolvedValue({
      success: true,
      agents: [
        buildAgent({ agent_id: 'a1', name: 'Hub Agent', source: 'hub' }),
        buildAgent({ agent_id: 'a2', name: 'Cloud Agent', source: 'cloud' }),
      ],
    })

    render(<HubPageContent apiKeysPath="/d/keys" basePath="/c" />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('Hub Agent')).toBeInTheDocument()
    })
    expect(screen.queryByText('Cloud Agent')).not.toBeInTheDocument()
  })

  it('shows empty state when hub is online but no agents', async () => {
    mockUseHubStatus.mockReturnValue({
      hub: { hub_id: 'h1', is_online: true, last_connected_at: null, agent_count: 0 },
      isOnline: true, hasHub: true, isLoading: false, invalidate: mockInvalidate,
    })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubPageContent apiKeysPath="/d/keys" basePath="/c" />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('Hub connected but no agents registered yet.')).toBeInTheDocument()
    })
  })

  it('shows page header "My Hub"', () => {
    mockUseHubStatus.mockReturnValue({ hub: null, isOnline: false, hasHub: false, isLoading: false, invalidate: mockInvalidate })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubPageContent apiKeysPath="/d/keys" basePath="/c" />, { wrapper: createWrapper() })

    expect(screen.getByText('My Hub')).toBeInTheDocument()
  })

  it('has a Refresh button', () => {
    mockUseHubStatus.mockReturnValue({ hub: null, isOnline: false, hasHub: false, isLoading: false, invalidate: mockInvalidate })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubPageContent apiKeysPath="/d/keys" basePath="/c" />, { wrapper: createWrapper() })

    expect(screen.getByRole('button', { name: /Refresh/ })).toBeInTheDocument()
  })

  it('renders agent cards as links to the correct portal detail page', async () => {
    mockUseHubStatus.mockReturnValue({
      hub: { hub_id: 'h1', is_online: true, last_connected_at: null, agent_count: 1 },
      isOnline: true, hasHub: true, isLoading: false, invalidate: mockInvalidate,
    })
    mockGetAllActiveAgents.mockResolvedValue({
      success: true,
      agents: [buildAgent({ agent_id: 'a1', name: 'Clickable Agent' })],
    })

    render(<HubPageContent apiKeysPath="/d/keys" basePath="/c" />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('Clickable Agent')).toBeInTheDocument()
    })
    const link = screen.getByText('Clickable Agent').closest('a')
    expect(link?.getAttribute('href')).toBe('/c/agents/a1')
  })

  it('uses developer basePath for agent links', async () => {
    mockUseHubStatus.mockReturnValue({
      hub: { hub_id: 'h1', is_online: true, last_connected_at: null, agent_count: 1 },
      isOnline: true, hasHub: true, isLoading: false, invalidate: mockInvalidate,
    })
    mockGetAllActiveAgents.mockResolvedValue({
      success: true,
      agents: [buildAgent({ agent_id: 'a1', name: 'Dev Agent' })],
    })

    render(<HubPageContent apiKeysPath="/d/keys" basePath="/d" />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('Dev Agent')).toBeInTheDocument()
    })
    const link = screen.getByText('Dev Agent').closest('a')
    expect(link?.getAttribute('href')).toBe('/d/agents/a1')
  })
})
