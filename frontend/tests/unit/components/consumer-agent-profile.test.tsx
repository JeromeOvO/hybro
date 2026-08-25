import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor } from '../../utils/test-utils'
import userEvent from '@testing-library/user-event'
import React from 'react'
import type { AgentCenterResponse, Agent, AgentCard, AgentSkill, AgentCapabilities } from '@/lib/types'
import { useRoomUiStore } from '@/stores/room-ui-store'

/* ── Mocks ── */

const mockPush = vi.fn()
const mockParamId = vi.fn(() => 'agent-test-1')

const mockIntersectionObserver = vi.fn(() => ({
  observe: vi.fn(),
  unobserve: vi.fn(),
  disconnect: vi.fn(),
}))
vi.stubGlobal('IntersectionObserver', mockIntersectionObserver)

Object.assign(navigator, {
  clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
})

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: mockParamId() }),
  useRouter: () => ({ push: mockPush }),
}))

vi.mock('next/link', () => ({
  default: ({ children, href, ...rest }: { children: React.ReactNode; href: string; [k: string]: unknown }) => (
    <a href={href} {...rest}>{children}</a>
  ),
}))

const mockGetToken = vi.fn(async () => 'test-token')
vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ getToken: mockGetToken, userId: 'owner-user' }),
}))

const mockGetAgent = vi.fn<() => Promise<AgentCenterResponse>>()
const mockDeleteAgent = vi.fn()
vi.mock('@/lib/api/agent', () => ({
  getAgent: mockGetAgent,
  deleteAgent: mockDeleteAgent,
}))

vi.mock('@/components/ui/banner', () => ({
  banner: { info: vi.fn(), error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/lib/agent-icon-utils', () => ({
  getModeIcon: () => {
    const Stub = (props: React.SVGProps<SVGSVGElement>) =>
      <svg data-testid="mode-icon" {...props} />
    Stub.displayName = 'StubIcon'
    return Stub
  },
  getModeLabel: (mime: string) => mime,
}))

/* ── Local factory ── */

function buildSkill(overrides: Partial<AgentSkill> = {}): AgentSkill {
  return {
    id: 'skill-1',
    name: 'Default Skill',
    description: 'Skill description',
    tags: ['tag-a'],
    examples: ['example one', 'example two', 'example three'],
    inputModes: ['text/plain'],
    outputModes: ['text/plain'],
    ...overrides,
  }
}

function buildCard(overrides: Partial<AgentCard> = {}): AgentCard {
  return {
    name: 'Test Agent',
    description: 'A helpful test agent.',
    version: '1.0.0',
    protocolVersion: '1.0.0',
    url: 'http://localhost:8001',
    iconUrl: undefined,
    documentationUrl: 'https://docs.example.com',
    provider: { organization: 'Acme Corp', url: 'https://acme.com' },
    capabilities: {
      streaming: true,
      pushNotifications: false,
      stateTransitionHistory: false,
      extensions: undefined,
    } as AgentCapabilities,
    defaultInputModes: ['text/plain', 'application/json'],
    defaultOutputModes: ['text/plain'],
    skills: [buildSkill()],
    ...overrides,
  }
}

function buildAgentResponse(
  overrides: {
    card?: Partial<AgentCard>
    providerId?: string
    status?: Agent['agent_status']
    source?: Agent['source']
    isHubOnline?: boolean
  } = {},
): AgentCenterResponse {
  return {
    success: true,
    agent: {
      agent_id: 'agent-test-1',
      provider_id: overrides.providerId ?? 'owner-user',
      agent_card: buildCard(overrides.card),
      agent_status: overrides.status ?? 'active',
      source: overrides.source ?? 'cloud',
      is_hub_online: overrides.isHubOnline,
    },
  }
}

/* ── Lazy import (after mocks) ── */

let ConsumerAgentProfilePage: React.ComponentType

beforeEach(async () => {
  vi.clearAllMocks()
  useRoomUiStore.setState({ pendingChatHandoff: null })
  mockDeleteAgent.mockResolvedValue({ success: true })
  mockParamId.mockReturnValue('agent-test-1')
  const mod = await import('@/app/(portal)/agents/[id]/page')
  ConsumerAgentProfilePage = mod.default
})

afterEach(() => {
  cleanup()
})

/* ── Tests ── */

describe('ConsumerAgentProfilePage', () => {
  describe('normal agent rendering', () => {
    it('renders Hero with name, description, status, provider, and version', async () => {
      mockGetAgent.mockResolvedValue(buildAgentResponse())

      render(<ConsumerAgentProfilePage />)

      await waitFor(() => {
        expect(screen.getByRole('heading', { name: 'Test Agent' })).toBeInTheDocument()
      })
      expect(screen.getByText('A helpful test agent.')).toBeInTheDocument()
      expect(screen.getByText('Active')).toBeInTheDocument()
      expect(screen.getByText(/v1\.0\.0/)).toBeInTheDocument()
      expect(screen.getByText(/Acme Corp/)).toBeInTheDocument()
    })

    it('shows examples in skills section (max 3 per skill)', async () => {
      mockGetAgent.mockResolvedValue(buildAgentResponse({
        card: {
          skills: [buildSkill({
            examples: ['example one', 'example two', 'example three', 'example four'],
          })],
        },
      }))

      render(<ConsumerAgentProfilePage />)

      await waitFor(() => {
        expect(screen.getByTestId('skills-section')).toBeInTheDocument()
      })
      expect(screen.getByText('example one')).toBeInTheDocument()
      expect(screen.getByText('example two')).toBeInTheDocument()
      expect(screen.getByText('example three')).toBeInTheDocument()
      expect(screen.queryByText('example four')).not.toBeInTheDocument()
    })

    it('renders skill name, description, and tags in skills section', async () => {
      mockGetAgent.mockResolvedValue(buildAgentResponse())

      render(<ConsumerAgentProfilePage />)

      await waitFor(() => {
        expect(screen.getByTestId('skills-section')).toBeInTheDocument()
      })

      const section = screen.getByTestId('skills-section')
      expect(section.textContent).toContain('Default Skill')
      expect(section.textContent).toContain('Skill description')
      expect(section.textContent).toContain('tag-a')
    })
  })

  describe('agent with no examples', () => {
    it('still renders skills section when skills exist but have no examples', async () => {
      mockGetAgent.mockResolvedValue(
        buildAgentResponse({
          card: {
            skills: [buildSkill({ examples: [] }), buildSkill({ id: 's2', examples: undefined })],
          },
        }),
      )

      render(<ConsumerAgentProfilePage />)

      await waitFor(() => {
        expect(screen.getByTestId('skills-section')).toBeInTheDocument()
      })
    })

    it('hides skills section when skills array is empty', async () => {
      mockGetAgent.mockResolvedValue(
        buildAgentResponse({ card: { skills: [] } }),
      )

      render(<ConsumerAgentProfilePage />)

      await waitFor(() => {
        expect(screen.getByTestId('technical-section')).toBeInTheDocument()
      })
      expect(screen.queryByTestId('skills-section')).not.toBeInTheDocument()
      expect(
        screen.queryByText('What this agent can help with'),
      ).not.toBeInTheDocument()
    })

    it('still renders "How this agent works" section', async () => {
      mockGetAgent.mockResolvedValue(
        buildAgentResponse({ card: { skills: [] } }),
      )

      render(<ConsumerAgentProfilePage />)

      await waitFor(() => {
        expect(screen.getByText('How this agent works')).toBeInTheDocument()
      })
    })
  })

  describe('source lifecycle', () => {
    it('allows a Remote agent to be unregistered', async () => {
      mockGetAgent.mockResolvedValue(buildAgentResponse({ source: 'cloud' }))

      render(<ConsumerAgentProfilePage />)

      const trigger = await screen.findByRole('button', { name: 'Unregister Agent' })
      await userEvent.click(trigger)
      const actions = screen.getAllByRole('button', { name: 'Unregister Agent' })
      await userEvent.click(actions.at(-1)!)

      await waitFor(() => {
        expect(mockDeleteAgent).toHaveBeenCalledWith(
          { agent_id: 'agent-test-1' },
          mockGetToken,
        )
      })
      expect(mockPush).toHaveBeenCalledWith('/agents')
    })

    it('hides unregister controls for a Remote agent owned by someone else', async () => {
      mockGetAgent.mockResolvedValue(buildAgentResponse({
        source: 'cloud',
        providerId: 'someone-else',
      }))

      render(<ConsumerAgentProfilePage />)

      expect(await screen.findByText('Remote')).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /Unregister Agent/i })).not.toBeInTheDocument()
    })

    it('shows Local agents as discovery-managed without unregister controls', async () => {
      mockGetAgent.mockResolvedValue(buildAgentResponse({
        source: 'hub',
        isHubOnline: true,
      }))

      render(<ConsumerAgentProfilePage />)

      expect(await screen.findByText('Local')).toBeInTheDocument()
      expect(screen.getByText(/managed by automatic discovery/i)).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /Unregister Agent/i })).not.toBeInTheDocument()
    })

    it('shows directly discovered Local agents without Hub liveness', async () => {
      mockGetAgent.mockResolvedValue(buildAgentResponse({
        source: 'local',
      }))

      render(<ConsumerAgentProfilePage />)

      expect(await screen.findByText('Local')).toBeInTheDocument()
      expect(screen.getByText(/managed by automatic discovery/i)).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /Unregister Agent/i })).not.toBeInTheDocument()
    })

    it('does not show stale Local agent details', async () => {
      mockGetAgent.mockResolvedValue(buildAgentResponse({
        source: 'hub',
        isHubOnline: false,
      }))

      render(<ConsumerAgentProfilePage />)

      expect(await screen.findByText('Local Agent Unavailable')).toBeInTheDocument()
      expect(screen.queryByRole('heading', { name: 'Test Agent' })).not.toBeInTheDocument()
    })
  })

  describe('documentation URL', () => {
    it('shows View Documentation when URL is present', async () => {
      mockGetAgent.mockResolvedValue(buildAgentResponse())

      render(<ConsumerAgentProfilePage />)

      await waitFor(() => {
        expect(screen.getByText('View Documentation')).toBeInTheDocument()
      })
    })

    it('hides View Documentation when URL is absent', async () => {
      mockGetAgent.mockResolvedValue(
        buildAgentResponse({ card: { documentationUrl: undefined } }),
      )

      render(<ConsumerAgentProfilePage />)

      await waitFor(() => {
        expect(screen.getByRole('heading', { name: 'Test Agent' })).toBeInTheDocument()
      })
      expect(screen.queryByText('View Documentation')).not.toBeInTheDocument()
    })
  })

  describe('capabilities', () => {
    it('shows enabled capabilities as badges after expanding technical section', async () => {
      mockGetAgent.mockResolvedValue(
        buildAgentResponse({
          card: {
            capabilities: {
              streaming: true,
              pushNotifications: true,
              stateTransitionHistory: false,
              extensions: undefined,
            } as AgentCapabilities,
          },
        }),
      )

      render(<ConsumerAgentProfilePage />)

      await waitFor(() => {
        expect(screen.getByText('How this agent works')).toBeInTheDocument()
      })

      await userEvent.click(screen.getByText('How this agent works'))

      await waitFor(() => {
        expect(screen.getByTestId('capabilities')).toBeInTheDocument()
      })
      expect(screen.getByText('Streaming')).toBeInTheDocument()
      expect(screen.getByText('Push Notifications')).toBeInTheDocument()
      expect(screen.queryByText('State History')).not.toBeInTheDocument()
    })

    it('hides capabilities section when none are enabled', async () => {
      mockGetAgent.mockResolvedValue(
        buildAgentResponse({
          card: {
            capabilities: {
              streaming: false,
              pushNotifications: false,
              stateTransitionHistory: false,
              extensions: undefined,
            } as AgentCapabilities,
          },
        }),
      )

      render(<ConsumerAgentProfilePage />)

      await waitFor(() => {
        expect(screen.getByTestId('technical-section')).toBeInTheDocument()
      })

      await userEvent.click(screen.getByText('How this agent works'))

      expect(screen.queryByTestId('capabilities')).not.toBeInTheDocument()
    })
  })

  describe('system agent', () => {
    it('renders static profile without calling getAgent', async () => {
      mockParamId.mockReturnValue('system:clarifier')

      render(<ConsumerAgentProfilePage />)

      await waitFor(() => {
        expect(screen.getByText('HYBRO AI')).toBeInTheDocument()
      })
      expect(screen.getByText('Built-in System Agent')).toBeInTheDocument()
      expect(mockGetAgent).not.toHaveBeenCalled()
    })
  })

  describe('CTA', () => {
    it('hands a seeded single-agent chat to the composer without URL parameters', async () => {
      mockGetAgent.mockResolvedValue(buildAgentResponse())

      render(<ConsumerAgentProfilePage />)

      await waitFor(() => {
        expect(
          screen.getByRole('button', { name: /Chat with this agent/i }),
        ).toBeInTheDocument()
      })

      await userEvent.click(
        screen.getByRole('button', { name: /Chat with this agent/i }),
      )

      expect(mockPush).toHaveBeenCalledWith('/chat')
      expect(mockPush).toHaveBeenCalledTimes(1)
      const handoff = useRoomUiStore.getState().pendingChatHandoff
      expect(handoff?.draft).toBe('')
      expect(handoff?.seedAgents).toHaveLength(1)
      expect(handoff?.seedAgents?.[0].agent_id).toBe('agent-test-1')
      expect(handoff?.seedAgents?.[0].agent_card.name).toBe('Test Agent')
    })
  })

  describe('no "Best for" section', () => {
    it('does not render Best for badges', async () => {
      mockGetAgent.mockResolvedValue(buildAgentResponse())

      render(<ConsumerAgentProfilePage />)

      await waitFor(() => {
        expect(screen.getByRole('heading', { name: 'Test Agent' })).toBeInTheDocument()
      })
      expect(screen.queryByTestId('best-for')).not.toBeInTheDocument()
      expect(screen.queryByText('Best for')).not.toBeInTheDocument()
    })
  })
})
