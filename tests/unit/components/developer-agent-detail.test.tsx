import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor } from '../../utils/test-utils'
import React from 'react'
import type { AgentCenterResponse, Agent, AgentCard } from '@/lib/types'

/* ── Mocks ── */

const mockPush = vi.fn()
const mockParamId = vi.fn(() => 'agent-1')

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: mockParamId() }),
  useRouter: () => ({ push: mockPush }),
}))

const mockUseAuth = vi.fn()
vi.mock('@clerk/nextjs', () => ({
  useAuth: () => mockUseAuth(),
}))

const mockGetAgent = vi.fn<() => Promise<AgentCenterResponse>>()
const mockDeleteAgent = vi.fn<() => Promise<AgentCenterResponse>>()
const mockUpdateAgent = vi.fn<() => Promise<AgentCenterResponse>>()
const mockBanner = { info: vi.fn(), error: vi.fn(), success: vi.fn(), warning: vi.fn() }

vi.mock('@/lib/api', () => ({
  getAgent: (...args: unknown[]) => mockGetAgent(...args),
  deleteAgent: (...args: unknown[]) => mockDeleteAgent(...args),
  updateAgent: (...args: unknown[]) => mockUpdateAgent(...args),
}))

vi.mock('@/lib/urls', () => ({
  consumerUrl: (path: string) => `http://localhost:3000${path}`,
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

vi.mock('@/components/developer/agent-settings-card', () => ({
  AgentSettingsCard: () => <div data-testid="settings-card">Settings</div>,
  validateAgentSettings: vi.fn(() => null),
  settingsToUpdatePayload: vi.fn(() => ({})),
}))

vi.mock('@/components/developer/agent-avatar-upload', () => ({
  AgentAvatarUpload: ({ agentName }: { agentName: string }) => (
    <div data-testid="avatar-upload">{agentName}</div>
  ),
}))

vi.mock('@/hooks/useMyAgents', () => ({
  useMyAgents: () => ({ invalidate: vi.fn() }),
}))

/* ── Factory helpers ── */

function buildCard(overrides: Partial<AgentCard> = {}): AgentCard {
  return {
    name: 'My Agent',
    description: 'Agent description',
    version: '2.0.0',
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
    provider_id: 'owner-user',
    agent_card: buildCard(),
    agent_status: 'active',
    is_public: true,
    provider_name: null,
    rate_limit_per_user_per_hour: null,
    rate_limit_system_per_hour: null,
    ...overrides,
  }
}

function buildResponse(agent: Agent | null): AgentCenterResponse {
  return agent
    ? { success: true, agent }
    : { success: false, error: 'Not found' }
}

/* ── Lazy import ── */

let DeveloperAgentManagePage: React.ComponentType

beforeEach(async () => {
  vi.clearAllMocks()
  mockParamId.mockReturnValue('agent-1')
  mockUseAuth.mockReturnValue({ userId: 'owner-user', getToken: vi.fn() })
  const mod = await import('@/app/d/agents/[id]/page')
  DeveloperAgentManagePage = mod.default
})

afterEach(() => {
  cleanup()
})

/* ── Tests ── */

describe('DeveloperAgentManagePage', () => {
  describe('provider display', () => {
    it('shows organization from agent_card.provider when set', async () => {
      mockGetAgent.mockResolvedValue(
        buildResponse(
          buildAgent({ agent_card: buildCard({ provider: { organization: 'Acme Corp', url: 'https://acme.com' } }) })
        )
      )

      render(<DeveloperAgentManagePage />)

      await waitFor(() => {
        expect(screen.getByText(/Acme Corp/)).toBeInTheDocument()
      })
    })

    it('falls back to provider_name when agent_card.provider is absent', async () => {
      mockGetAgent.mockResolvedValue(
        buildResponse(
          buildAgent({ agent_card: buildCard({ provider: null }), provider_name: 'Kevin Lu' })
        )
      )

      render(<DeveloperAgentManagePage />)

      await waitFor(() => {
        expect(screen.getByText(/Kevin Lu/)).toBeInTheDocument()
      })
    })

    it('shows "Unknown Provider" when neither organization nor provider_name is set', async () => {
      mockGetAgent.mockResolvedValue(
        buildResponse(
          buildAgent({ agent_card: buildCard({ provider: null }), provider_name: null })
        )
      )

      render(<DeveloperAgentManagePage />)

      await waitFor(() => {
        expect(screen.getByText(/Unknown Provider/)).toBeInTheDocument()
      })
    })

    it('shows "Built by" prefix with the provider name', async () => {
      mockGetAgent.mockResolvedValue(
        buildResponse(
          buildAgent({ provider_name: 'Kevin Lu' })
        )
      )

      render(<DeveloperAgentManagePage />)

      await waitFor(() => {
        expect(screen.getByText(/Built by/)).toBeInTheDocument()
        expect(screen.getByText('Kevin Lu')).toBeInTheDocument()
      })
    })
  })

  describe('agent info', () => {
    it('renders agent name and version', async () => {
      mockGetAgent.mockResolvedValue(
        buildResponse(buildAgent({ agent_card: buildCard({ name: 'Weather Bot', version: '3.1.0' }) }))
      )

      render(<DeveloperAgentManagePage />)

      await waitFor(() => {
        expect(screen.getByText(/v3\.1\.0/)).toBeInTheDocument()
      })
      expect(screen.getAllByText('Weather Bot').length).toBeGreaterThan(0)
    })

    it('shows "Agent Not Found" card when API returns failure', async () => {
      mockGetAgent.mockResolvedValue(buildResponse(null))

      render(<DeveloperAgentManagePage />)

      await waitFor(() => {
        expect(screen.getByText('Agent Not Found')).toBeInTheDocument()
      })
      expect(screen.getByRole('button', { name: /Back to My Agents/i })).toBeInTheDocument()
    })

    it('shows error banner when API throws', async () => {
      mockGetAgent.mockRejectedValue(new Error('network error'))

      render(<DeveloperAgentManagePage />)

      await waitFor(() => {
        expect(mockBanner.error).toHaveBeenCalledWith('Failed to load agent details')
      })
    })
  })

  describe('owner vs non-owner', () => {
    it('shows settings card and delete button for the owner', async () => {
      mockGetAgent.mockResolvedValue(
        buildResponse(buildAgent({ provider_id: 'owner-user' }))
      )

      render(<DeveloperAgentManagePage />)

      await waitFor(() => {
        expect(screen.getByTestId('settings-card')).toBeInTheDocument()
      })
      expect(screen.getByRole('button', { name: /Delete Agent/i })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /Save Settings/i })).toBeInTheDocument()
    })

    it('shows non-owner message for a different authenticated user', async () => {
      mockUseAuth.mockReturnValue({ userId: 'other-user', getToken: vi.fn() })
      mockGetAgent.mockResolvedValue(
        buildResponse(buildAgent({ provider_id: 'owner-user' }))
      )

      render(<DeveloperAgentManagePage />)

      await waitFor(() => {
        expect(screen.getByText(/You do not own this agent/i)).toBeInTheDocument()
      })
      expect(screen.queryByTestId('settings-card')).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /Delete Agent/i })).not.toBeInTheDocument()
    })

    it('shows sign in prompt for unauthenticated viewer', async () => {
      mockUseAuth.mockReturnValue({ userId: null, getToken: vi.fn() })
      mockGetAgent.mockResolvedValue(
        buildResponse(buildAgent({ provider_id: 'owner-user' }))
      )

      render(<DeveloperAgentManagePage />)

      await waitFor(() => {
        expect(screen.getByText(/Please sign in to manage/i)).toBeInTheDocument()
      })
      expect(screen.getByRole('button', { name: /Sign In/i })).toBeInTheDocument()
    })
  })
})
