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
  getAllActiveAgents: mockGetAllActiveAgents,
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

let HubPageContent: React.ComponentType

beforeEach(async () => {
  vi.clearAllMocks()
  const mod = await import('@/components/hub-page-content')
  HubPageContent = mod.HubPageContent
})

afterEach(() => {
  vi.useRealTimers()
  cleanup()
})

describe('HubPageContent', () => {
  it('shows loading state when hub status is loading', () => {
    mockUseHubStatus.mockReturnValue({ hub: null, isOnline: false, hasHub: false, isLoading: true, invalidate: mockInvalidate })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubPageContent />, { wrapper: createWrapper() })

    expect(screen.getByText('Loading hub status...')).toBeInTheDocument()
  })

  it('shows "No hub connected" when no hub exists', async () => {
    mockUseHubStatus.mockReturnValue({ hub: null, isOnline: false, hasHub: false, isLoading: false, invalidate: mockInvalidate })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubPageContent />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('No hub connected')).toBeInTheDocument()
    })
    expect(screen.getByText(/pip install hybro-hub/)).toBeInTheDocument()
    expect(screen.queryByText(/Connected since/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Last seen/)).not.toBeInTheDocument()
  })

  it('shows setup guide with API Keys link', async () => {
    mockUseHubStatus.mockReturnValue({ hub: null, isOnline: false, hasHub: false, isLoading: false, invalidate: mockInvalidate })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubPageContent />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('Setup Guide')).toBeInTheDocument()
    })
    const link = screen.getByText('API Keys').closest('a')
    expect(link?.getAttribute('href')).toBe('/manage/api-keys')
  })

  it('shows "Hub Connected" when hub is online', async () => {
    mockUseHubStatus.mockReturnValue({
      hub: { hub_id: 'h1', is_online: true, last_connected_at: '2026-01-01T00:00:00Z', agent_count: 0 },
      isOnline: true, hasHub: true, isLoading: false, invalidate: mockInvalidate,
    })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubPageContent />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('Hub Connected')).toBeInTheDocument()
    })
  })

  it('does not show a redundant status badge when hub status text is shown', async () => {
    mockUseHubStatus.mockReturnValue({
      hub: { hub_id: 'h1', is_online: true, last_connected_at: null, agent_count: 0 },
      isOnline: true, hasHub: true, isLoading: false, invalidate: mockInvalidate,
    })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubPageContent />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('Hub Connected')).toBeInTheDocument()
    })
    expect(screen.queryByText('Online')).not.toBeInTheDocument()
  })

  it('does not add extra top padding to the hub status card content', async () => {
    mockUseHubStatus.mockReturnValue({
      hub: { hub_id: 'h1', is_online: true, last_connected_at: null, agent_count: 0 },
      isOnline: true, hasHub: true, isLoading: false, invalidate: mockInvalidate,
    })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubPageContent />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('Hub Connected')).toBeInTheDocument()
    })
    const statusCard = screen.getByText('Hub Connected').closest('[data-slot="card"]')
    const statusContent = statusCard?.querySelector('[data-slot="card-content"]')
    expect(statusContent).not.toHaveClass('pt-6')
  })

  it('shows "Hub Offline" when hub is not online', async () => {
    mockUseHubStatus.mockReturnValue({
      hub: { hub_id: 'h1', is_online: false, last_connected_at: '2026-01-01T00:00:00Z', agent_count: 0 },
      isOnline: false, hasHub: true, isLoading: false, invalidate: mockInvalidate,
    })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubPageContent />, { wrapper: createWrapper() })

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

    render(<HubPageContent />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText(/Connected since/)).toBeInTheDocument()
    })
    const connectedSince = screen.getByText(/Connected since/)
    expect(connectedSince).toHaveClass('font-medium')
    expect(connectedSince).toHaveClass('text-foreground')
    expect(connectedSince).not.toHaveClass('text-xs')
    expect(connectedSince).not.toHaveClass('text-muted-foreground')
    expect(connectedSince).toHaveClass('ml-auto')
    expect(connectedSince).toHaveClass('text-right')
  })

  it('shows "Last seen" timestamp when offline', async () => {
    mockUseHubStatus.mockReturnValue({
      hub: { hub_id: 'h1', is_online: false, last_connected_at: '2026-01-01T00:00:00Z', agent_count: 0 },
      isOnline: false, hasHub: true, isLoading: false, invalidate: mockInvalidate,
    })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubPageContent />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText(/Last seen/)).toBeInTheDocument()
    })
    const lastSeen = screen.getByText(/Last seen/)
    expect(lastSeen).toHaveClass('font-medium')
    expect(lastSeen).toHaveClass('text-foreground')
    expect(lastSeen).not.toHaveClass('text-xs')
    expect(lastSeen).not.toHaveClass('text-muted-foreground')
  })

  it('does not show last-seen text when hub is offline without connection history', async () => {
    mockUseHubStatus.mockReturnValue({
      hub: { hub_id: 'h1', is_online: false, last_connected_at: null, agent_count: 0 },
      isOnline: false, hasHub: true, isLoading: false, invalidate: mockInvalidate,
    })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubPageContent />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('Hub Offline')).toBeInTheDocument()
    })
    expect(screen.queryByText(/Connected since/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Last seen/)).not.toBeInTheDocument()
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

    render(<HubPageContent />, { wrapper: createWrapper() })

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

    render(<HubPageContent />, { wrapper: createWrapper() })

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

    render(<HubPageContent />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('Hub connected but no agents registered yet.')).toBeInTheDocument()
    })
  })

  it('shows page header "My Hub"', () => {
    mockUseHubStatus.mockReturnValue({ hub: null, isOnline: false, hasHub: false, isLoading: false, invalidate: mockInvalidate })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubPageContent />, { wrapper: createWrapper() })

    expect(screen.getByText('My Hub')).toBeInTheDocument()
  })

  it('has a Refresh button', () => {
    mockUseHubStatus.mockReturnValue({ hub: null, isOnline: false, hasHub: false, isLoading: false, invalidate: mockInvalidate })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubPageContent />, { wrapper: createWrapper() })

    expect(screen.getByRole('button', { name: /Refresh/ })).toBeInTheDocument()
  })

  it('highlights the Refresh button like sidebar items on hover', () => {
    mockUseHubStatus.mockReturnValue({ hub: null, isOnline: false, hasHub: false, isLoading: false, invalidate: mockInvalidate })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubPageContent />, { wrapper: createWrapper() })

    const refreshButton = screen.getByRole('button', { name: /Refresh/ })
    expect(refreshButton).toHaveClass('hover:bg-black/10')
    expect(refreshButton).toHaveClass('dark:hover:bg-white/15')
  })

  it('shows a tooltip for the Refresh button', async () => {
    const user = userEvent.setup()
    mockUseHubStatus.mockReturnValue({ hub: null, isOnline: false, hasHub: false, isLoading: false, invalidate: mockInvalidate })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubPageContent />, { wrapper: createWrapper() })

    await user.hover(screen.getByRole('button', { name: /Refresh/ }))
    await waitFor(() => {
      expect(screen.getByRole('tooltip')).toHaveTextContent('Refresh hub status')
    })
  })

  it('highlights local agent cards like sidebar items on hover', async () => {
    mockUseHubStatus.mockReturnValue({
      hub: { hub_id: 'h1', is_online: true, last_connected_at: null, agent_count: 1 },
      isOnline: true, hasHub: true, isLoading: false, invalidate: mockInvalidate,
    })
    mockGetAllActiveAgents.mockResolvedValue({
      success: true,
      agents: [buildAgent({ agent_id: 'a1', name: 'Hover Agent' })],
    })

    render(<HubPageContent />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('Hover Agent')).toBeInTheDocument()
    })
    const agentCard = screen.getByText('Hover Agent').closest('[data-slot="card"]')
    expect(agentCard).toHaveClass('hover:bg-black/10')
    expect(agentCard).toHaveClass('dark:hover:bg-white/15')
  })

  it('spins the Refresh icon while hub status is refetching', () => {
    mockUseHubStatus.mockReturnValue({
      hub: { hub_id: 'h1', is_online: true, last_connected_at: null, agent_count: 0 },
      isOnline: true,
      hasHub: true,
      isLoading: false,
      isFetching: true,
      invalidate: mockInvalidate,
    })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubPageContent />, { wrapper: createWrapper() })

    const refreshIcon = screen.getByRole('button', { name: /Refresh/ }).querySelector('svg')
    expect(refreshIcon).toHaveClass('animate-spin')
  })

  it('keeps the Refresh icon spinning briefly after a fast manual refresh', async () => {
    const user = userEvent.setup()
    mockUseHubStatus.mockReturnValue({
      hub: { hub_id: 'h1', is_online: true, last_connected_at: null, agent_count: 0 },
      isOnline: true,
      hasHub: true,
      isLoading: false,
      isFetching: false,
      invalidate: mockInvalidate,
    })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })

    render(<HubPageContent />, { wrapper: createWrapper() })

    const refreshButton = screen.getByRole('button', { name: /Refresh/ })
    const refreshIcon = refreshButton.querySelector('svg')
    await waitFor(() => {
      expect(refreshIcon).not.toHaveClass('animate-spin')
    })

    await user.click(refreshButton)

    await new Promise(resolve => setTimeout(resolve, 300))
    expect(refreshIcon).toHaveClass('animate-spin')

    await new Promise(resolve => setTimeout(resolve, 350))
    await waitFor(() => {
      expect(refreshIcon).not.toHaveClass('animate-spin')
    })
  })

  it('renders agent cards as links to the management detail page', async () => {
    mockUseHubStatus.mockReturnValue({
      hub: { hub_id: 'h1', is_online: true, last_connected_at: null, agent_count: 1 },
      isOnline: true, hasHub: true, isLoading: false, invalidate: mockInvalidate,
    })
    mockGetAllActiveAgents.mockResolvedValue({
      success: true,
      agents: [buildAgent({ agent_id: 'a1', name: 'Clickable Agent' })],
    })

    render(<HubPageContent />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('Clickable Agent')).toBeInTheDocument()
    })
    const link = screen.getByText('Clickable Agent').closest('a')
    expect(link?.getAttribute('href')).toBe('/manage/agents/a1')
  })
})
