import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor } from '../../utils/test-utils'
import React from 'react'
import type { AgentCenterResponse, Agent, AgentCard } from '@/lib/types'

/* ── Mocks ── */

const mockPush = vi.fn()

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush }),
}))

const mockUseUser = vi.fn()
const mockGetToken = vi.fn().mockResolvedValue(null)
vi.mock('@clerk/nextjs', () => ({
  useUser: () => mockUseUser(),
  useAuth: () => ({ getToken: mockGetToken }),
}))

const mockGetAgentsByProviderId = vi.fn<() => Promise<AgentCenterResponse>>()
vi.mock('@/lib/api', () => ({
  getAgentsByProviderId: (...args: unknown[]) => mockGetAgentsByProviderId(...args),
}))

vi.mock('@/lib/urls', () => ({
  consumerUrl: (path: string) => `http://localhost:3000${path}`,
}))

vi.mock('@/lib/agent-avatar', () => ({
  getAgentAvatarUri: (id: string) => `/avatar/${id}`,
}))

vi.mock('@/components/developer-docs-content', () => ({
  DeveloperDocsContent: () => <div data-testid="docs-content">Docs</div>,
}))

vi.mock('@/components/agent-source-badge', () => ({
  AgentSourceBadge: () => null,
}))

const mockUseHubStatus = vi.fn()
vi.mock('@/hooks/useHubStatus', () => ({
  useHubStatus: () => mockUseHubStatus(),
  HUB_STATUS_QUERY_KEY: ['hub', 'status'],
}))

vi.mock('@/components/ui/banner', () => ({
  banner: { info: vi.fn(), error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}))

/* ── Factory helpers ── */

function buildCard(overrides: Partial<AgentCard> = {}): AgentCard {
  return {
    name: 'My Agent',
    description: 'Agent description',
    version: '1.0.0',
    url: 'http://localhost:8001',
    iconUrl: null,
    documentationUrl: null,
    provider: null,
    capabilities: { streaming: false, pushNotifications: false, stateTransitionHistory: false, extensions: null },
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

let DeveloperLandingPage: React.ComponentType

beforeEach(async () => {
  vi.clearAllMocks()
  mockUseUser.mockReturnValue({ isLoaded: true, isSignedIn: true, user: { firstName: 'Alice' } })
  mockUseHubStatus.mockReturnValue({ hasHub: false, isOnline: false, hub: null, isLoading: false, isError: false, error: null, hubs: [], invalidate: vi.fn() })
  const mod = await import('@/app/d/page')
  DeveloperLandingPage = mod.default
})

afterEach(() => {
  cleanup()
})

/* ── Tests ── */

describe('DeveloperLandingPage', () => {
  describe('authentication states', () => {
    it('shows loading spinner while Clerk is initializing', () => {
      mockUseUser.mockReturnValue({ isLoaded: false, isSignedIn: false, user: null })

      render(<DeveloperLandingPage />)

      expect(document.querySelector('.animate-spin')).toBeTruthy()
    })

    it('shows docs content when not signed in', async () => {
      mockUseUser.mockReturnValue({ isLoaded: true, isSignedIn: false, user: null })

      render(<DeveloperLandingPage />)

      expect(screen.getByTestId('docs-content')).toBeInTheDocument()
    })

    it('shows dashboard when signed in', async () => {
      mockGetAgentsByProviderId.mockResolvedValue(buildAgentsResponse([]))

      render(<DeveloperLandingPage />)

      await waitFor(() => {
        expect(screen.getByText(/Welcome back/)).toBeInTheDocument()
      })
    })

    it('shows first name in welcome message', async () => {
      mockGetAgentsByProviderId.mockResolvedValue(buildAgentsResponse([]))

      render(<DeveloperLandingPage />)

      await waitFor(() => {
        expect(screen.getByText(/Welcome back, Alice/)).toBeInTheDocument()
      })
    })
  })

  describe('empty state', () => {
    it('shows empty state prompt when no agents are registered', async () => {
      mockGetAgentsByProviderId.mockResolvedValue(buildAgentsResponse([]))

      render(<DeveloperLandingPage />)

      await waitFor(() => {
        expect(screen.getByText(/haven.*t registered any agents/i)).toBeInTheDocument()
      })
    })

    it('shows Register Your First Agent button in empty state', async () => {
      mockGetAgentsByProviderId.mockResolvedValue(buildAgentsResponse([]))

      render(<DeveloperLandingPage />)

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /Register Your First Agent/i })).toBeInTheDocument()
      })
    })
  })

  describe('agents table', () => {
    it('renders agent name and status in table', async () => {
      mockGetAgentsByProviderId.mockResolvedValue(
        buildAgentsResponse([buildAgent({ agent_card: buildCard({ name: 'Weather Bot' }), agent_status: 'active' })])
      )

      render(<DeveloperLandingPage />)

      await waitFor(() => {
        expect(screen.getByText('Weather Bot')).toBeInTheDocument()
      })
      expect(screen.getByText('active')).toBeInTheDocument()
    })

    it('shows provider organization from agent_card when set', async () => {
      mockGetAgentsByProviderId.mockResolvedValue(
        buildAgentsResponse([
          buildAgent({ agent_card: buildCard({ provider: { organization: 'Acme Corp', url: 'https://acme.com' } }) }),
        ])
      )

      render(<DeveloperLandingPage />)

      await waitFor(() => {
        expect(screen.getByText('Acme Corp')).toBeInTheDocument()
      })
    })

    it('falls back to provider_name when agent_card.provider is absent', async () => {
      mockGetAgentsByProviderId.mockResolvedValue(
        buildAgentsResponse([
          buildAgent({ agent_card: buildCard({ provider: null }), provider_name: 'Kevin Lu' }),
        ])
      )

      render(<DeveloperLandingPage />)

      await waitFor(() => {
        expect(screen.getByText('Kevin Lu')).toBeInTheDocument()
      })
    })

    it('shows "Unknown" when neither organization nor provider_name is set', async () => {
      mockGetAgentsByProviderId.mockResolvedValue(
        buildAgentsResponse([
          buildAgent({ agent_card: buildCard({ provider: null }), provider_name: null }),
        ])
      )

      render(<DeveloperLandingPage />)

      await waitFor(() => {
        expect(screen.getByText('Unknown')).toBeInTheDocument()
      })
    })
  })

  describe('stats', () => {
    it('displays correct total, active, and inactive counts', async () => {
      mockGetAgentsByProviderId.mockResolvedValue(
        buildAgentsResponse([
          buildAgent({ agent_id: 'a1', agent_status: 'active' }),
          buildAgent({ agent_id: 'a2', agent_status: 'active' }),
          buildAgent({ agent_id: 'a3', agent_status: 'inactive' }),
        ])
      )

      render(<DeveloperLandingPage />)

      await waitFor(() => {
        expect(screen.getByText('My Agents')).toBeInTheDocument()
      })
      // Total Agents=3, Active=2, Inactive=1
      const totalCard = screen.getByText('Total Agents').closest('[data-slot="card-content"]')!
      const activeCard = screen.getByText('Active').closest('[data-slot="card-content"]')!
      const inactiveCard = screen.getByText('Inactive').closest('[data-slot="card-content"]')!
      expect(totalCard.textContent).toContain('3')
      expect(activeCard.textContent).toContain('2')
      expect(inactiveCard.textContent).toContain('1')
    })
  })

  describe('pagination', () => {
    it('does not show pagination controls for 5 or fewer agents', async () => {
      mockGetAgentsByProviderId.mockResolvedValue(
        buildAgentsResponse(
          Array.from({ length: 5 }, (_, i) =>
            buildAgent({ agent_id: `a${i}`, agent_card: buildCard({ name: `Agent ${i}` }) })
          )
        )
      )

      render(<DeveloperLandingPage />)

      await waitFor(() => {
        expect(screen.getByText('Agent 0')).toBeInTheDocument()
      })
      expect(screen.queryByRole('button', { name: /previous/i })).not.toBeInTheDocument()
    })

    it('shows pagination controls when more than 5 agents exist', async () => {
      mockGetAgentsByProviderId.mockResolvedValue(
        buildAgentsResponse(
          Array.from({ length: 6 }, (_, i) =>
            buildAgent({ agent_id: `a${i}`, agent_card: buildCard({ name: `Agent ${i}` }) })
          )
        )
      )

      render(<DeveloperLandingPage />)

      await waitFor(() => {
        expect(screen.getByText('1-5 of 6 agents')).toBeInTheDocument()
      })
    })
  })
})
