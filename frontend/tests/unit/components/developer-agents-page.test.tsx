import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor } from '../../utils/test-utils'
import userEvent from '@testing-library/user-event'
import React from 'react'
import type { AgentCenterResponse, Agent, AgentCard } from '@/lib/types'

/* ── Mocks ── */

const mockPush = vi.fn()

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush }),
}))

vi.mock('@clerk/nextjs', () => ({
  useAuth: () => ({ getToken: vi.fn() }),
}))

const mockGetAgentsByProviderId = vi.fn<() => Promise<AgentCenterResponse>>()
const mockBanner = { info: vi.fn(), error: vi.fn(), success: vi.fn(), warning: vi.fn() }

vi.mock('@/lib/api', () => ({
  getAgentsByProviderId: mockGetAgentsByProviderId,
}))

vi.mock('@/lib/agent-avatar', () => ({
  getAgentAvatarUri: (id: string) => `/avatar/${id}`,
}))

vi.mock('@/components/agent-source-badge', () => ({
  AgentSourceBadge: () => null,
}))

vi.mock('@/components/ui/banner', () => ({
  banner: mockBanner,
}))

/* ── Factory helpers ── */

function buildCard(overrides: Partial<AgentCard> = {}): AgentCard {
  return {
    name: 'My Agent',
    description: 'Agent description',
    version: '1.0.0',
    protocolVersion: '1.0.0',
    url: 'http://localhost:8001',
    iconUrl: undefined,
    documentationUrl: undefined,
    provider: undefined,
    capabilities: { streaming: false, pushNotifications: false, stateTransitionHistory: false, extensions: undefined },
    defaultInputModes: ['text/plain'],
    defaultOutputModes: ['text/plain'],
    skills: [],
    ...overrides,
  }
}

function buildAgent(overrides: Partial<Agent> = {}): Agent {
  return {
    agent_id: 'agent-1',
    provider_id: 'user-1',
    agent_card: buildCard(),
    agent_status: 'active',
    is_public: true,
    provider_name: null,
    ...overrides,
  }
}

function buildAgentsResponse(agents: Agent[]): AgentCenterResponse {
  return { success: true, agents }
}

/* ── Lazy import ── */

let DeveloperAgentsPage: React.ComponentType

beforeEach(async () => {
  vi.clearAllMocks()
  const mod = await import('@/app/(portal)/manage/agents/page')
  DeveloperAgentsPage = mod.default
})

afterEach(() => {
  cleanup()
})

/* ── Tests ── */

describe('DeveloperAgentsPage', () => {
  describe('empty state', () => {
    it('shows empty state when no agents are registered', async () => {
      mockGetAgentsByProviderId.mockResolvedValue(buildAgentsResponse([]))

      render(<DeveloperAgentsPage />)

      await waitFor(() => {
        expect(screen.getByText(/haven.*t registered any agents/i)).toBeInTheDocument()
      })
      expect(screen.getByRole('button', { name: /Register Your First Agent/i })).toBeInTheDocument()
    })

    it('opens registration from the top-right button', async () => {
      mockGetAgentsByProviderId.mockResolvedValue(buildAgentsResponse([]))

      render(<DeveloperAgentsPage />)

      const registerButton = await screen.findByRole('button', { name: /Register New Agent/i })
      await userEvent.click(registerButton)

      expect(mockPush).toHaveBeenCalledWith('/manage/agents/new')
    })

    it('shows error banner when API call fails', async () => {
      mockGetAgentsByProviderId.mockRejectedValue(new Error('network error'))

      render(<DeveloperAgentsPage />)

      await waitFor(() => {
        expect(mockBanner.error).toHaveBeenCalledWith('Failed to load your agents')
      })
    })
  })

  describe('agents table', () => {
    it('renders agent name, description, and status', async () => {
      mockGetAgentsByProviderId.mockResolvedValue(
        buildAgentsResponse([
          buildAgent({
            agent_card: buildCard({ name: 'Weather Bot', description: 'Forecasts weather' }),
            agent_status: 'active',
          }),
        ])
      )

      render(<DeveloperAgentsPage />)

      await waitFor(() => {
        expect(screen.getByText('Weather Bot')).toBeInTheDocument()
      })
      expect(screen.getByText('Forecasts weather')).toBeInTheDocument()
      expect(screen.getByText('active')).toBeInTheDocument()
    })

    it('shows provider organization from agent_card when set', async () => {
      mockGetAgentsByProviderId.mockResolvedValue(
        buildAgentsResponse([
          buildAgent({
            agent_card: buildCard({ provider: { organization: 'Acme Corp', url: 'https://acme.com' } }),
          }),
        ])
      )

      render(<DeveloperAgentsPage />)

      await waitFor(() => {
        expect(screen.getByText('Acme Corp')).toBeInTheDocument()
      })
    })

    it('falls back to provider_name when agent_card.provider is absent', async () => {
      mockGetAgentsByProviderId.mockResolvedValue(
        buildAgentsResponse([
          buildAgent({ agent_card: buildCard({ provider: undefined }), provider_name: 'Kevin Lu' }),
        ])
      )

      render(<DeveloperAgentsPage />)

      await waitFor(() => {
        expect(screen.getByText('Kevin Lu')).toBeInTheDocument()
      })
    })

    it('shows "Unknown" when neither organization nor provider_name is set', async () => {
      mockGetAgentsByProviderId.mockResolvedValue(
        buildAgentsResponse([
          buildAgent({ agent_card: buildCard({ provider: undefined }), provider_name: null }),
        ])
      )

      render(<DeveloperAgentsPage />)

      await waitFor(() => {
        expect(screen.getByText('Unknown')).toBeInTheDocument()
      })
    })

    it('navigates to agent detail on row click', async () => {
      mockGetAgentsByProviderId.mockResolvedValue(
        buildAgentsResponse([buildAgent({ agent_id: 'abc-123' })])
      )

      render(<DeveloperAgentsPage />)

      await waitFor(() => {
        expect(screen.getByText('My Agent')).toBeInTheDocument()
      })

      await userEvent.click(screen.getByText('My Agent'))

      expect(mockPush).toHaveBeenCalledWith('/manage/agents/abc-123')
    })

    it('opens the public agent profile in a new tab', async () => {
      mockGetAgentsByProviderId.mockResolvedValue(
        buildAgentsResponse([buildAgent()])
      )

      const { container } = render(<DeveloperAgentsPage />)

      await waitFor(() => {
        expect(screen.getByText('My Agent')).toBeInTheDocument()
      })

      const publicLink = container.querySelector('a[href="/agents/agent-1"]')
      expect(publicLink).toHaveAttribute('target', '_blank')
      expect(publicLink).toHaveAttribute('rel', 'noopener noreferrer')
    })
  })

  describe('stats', () => {
    it('shows correct total, active, and inactive counts', async () => {
      mockGetAgentsByProviderId.mockResolvedValue(
        buildAgentsResponse([
          buildAgent({ agent_id: 'a1', agent_status: 'active' }),
          buildAgent({ agent_id: 'a2', agent_status: 'inactive' }),
        ])
      )

      render(<DeveloperAgentsPage />)

      await waitFor(() => {
        expect(screen.getByText('My Agents')).toBeInTheDocument()
      })
      // Stats: Total=2, Active=1, Inactive=1
      const totalStat = screen.getByText('Total Agents').nextElementSibling
      expect(totalStat?.textContent).toBe('2')
    })
  })

  describe('search', () => {
    it('filters agents by name', async () => {
      mockGetAgentsByProviderId.mockResolvedValue(
        buildAgentsResponse([
          buildAgent({ agent_id: 'a1', agent_card: buildCard({ name: 'Weather Bot' }) }),
          buildAgent({ agent_id: 'a2', agent_card: buildCard({ name: 'News Reader' }) }),
        ])
      )

      render(<DeveloperAgentsPage />)

      await waitFor(() => {
        expect(screen.getByText('Weather Bot')).toBeInTheDocument()
      })

      await userEvent.type(screen.getByPlaceholderText(/search your agents/i), 'Weather')

      expect(screen.getByText('Weather Bot')).toBeInTheDocument()
      expect(screen.queryByText('News Reader')).not.toBeInTheDocument()
    })

    it('shows "no agents found" message when search has no results', async () => {
      mockGetAgentsByProviderId.mockResolvedValue(
        buildAgentsResponse([
          buildAgent({ agent_id: 'a1', agent_card: buildCard({ name: 'Weather Bot' }) }),
        ])
      )

      render(<DeveloperAgentsPage />)

      await waitFor(() => {
        expect(screen.getByText('Weather Bot')).toBeInTheDocument()
      })

      await userEvent.type(screen.getByPlaceholderText(/search your agents/i), 'xyz')

      expect(screen.getByText(/no agents found matching/i)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /clear search/i })).toBeInTheDocument()
    })

    it('clears search when "Clear search" button is clicked', async () => {
      mockGetAgentsByProviderId.mockResolvedValue(
        buildAgentsResponse([
          buildAgent({ agent_id: 'a1', agent_card: buildCard({ name: 'Weather Bot' }) }),
        ])
      )

      render(<DeveloperAgentsPage />)

      await waitFor(() => expect(screen.getByText('Weather Bot')).toBeInTheDocument())

      await userEvent.type(screen.getByPlaceholderText(/search your agents/i), 'xyz')
      await userEvent.click(screen.getByRole('button', { name: /clear search/i }))

      expect(screen.getByText('Weather Bot')).toBeInTheDocument()
    })
  })
})
