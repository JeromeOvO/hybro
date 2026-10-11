import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '../../utils/test-utils'
import userEvent from '@testing-library/user-event'
import { placeholderAgent } from '@/lib/network-agent'
import type { AgentGroup } from '@/lib/types/agent-group'

let search = ''
const push = vi.fn((href: string) => { search = href.split('?')[1] ?? '' })
const replace = vi.fn((href: string) => { search = href.split('?')[1] ?? '' })
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace }),
  usePathname: () => '/network',
  useSearchParams: () => new URLSearchParams(search),
}))
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ getToken: vi.fn(async () => 't'), isLoaded: true, isSignedIn: true, userId: 'u' }) }))
const discoverLocalAgents = vi.fn().mockResolvedValue({ agents_found: 2 })
vi.mock('@/lib/api/agent', () => ({ discoverLocalAgents: (...args: unknown[]) => discoverLocalAgents(...args) }))
vi.mock('@/components/networks/network-canvas', () => ({ NetworkCanvas: () => <div data-testid="canvas" /> }))
const networks = [{ group_id: 'R', name: 'Research', agents: ['a'], type: 'user', owner_id: 'u' }] as AgentGroup[]
vi.mock('@/hooks/useNetworks', () => ({
  useNetworks: () => ({
    networks, agents: [{ ...placeholderAgent('a', '', 'active'), name: 'writer' }],
    loading: false, error: null, agentsLoading: false, agentsError: null, pending: false,
    refresh: vi.fn(), refreshAgents: vi.fn(), create: vi.fn(), update: vi.fn(), setMembers: vi.fn(), remove: vi.fn(),
  }),
}))

import { NetworkWorkspace } from '@/components/networks/network-workspace'

beforeEach(() => { search = ''; push.mockClear(); replace.mockClear() })
afterEach(cleanup)

describe('NetworkWorkspace', () => {
  it('keeps the three network actions', async () => {
    render(<NetworkWorkspace />)
    expect(screen.getByRole('button', { name: 'Create subnet' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Add agent' }))
    expect(screen.getByRole('menuitem', { name: /Register by URL/ })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('menuitem', { name: /Discover local agents/ }))
    await waitFor(() => expect(discoverLocalAgents).toHaveBeenCalled())
  })

  it('writes search with replace and view with push', async () => {
    render(<NetworkWorkspace />)
    await userEvent.type(screen.getByRole('searchbox', { name: 'Search the network' }), 'w')
    expect(replace).toHaveBeenLastCalledWith('/network?q=w', { scroll: false })
    await userEvent.click(screen.getByRole('radio', { name: 'Structure' }))
    expect(push).toHaveBeenLastCalledWith(expect.stringContaining('view=structure'), { scroll: false })
  })

  it('treats a stale subnet scope as All agents', () => {
    search = 'view=structure&scope=subnet%3Agone&select=agent%3Agone'
    render(<NetworkWorkspace />)
    expect(screen.getByRole('region', { name: 'All agents' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Agent summary' })).toBeNull()
  })

  it('does not gate on sign-in', () => {
    render(<NetworkWorkspace />)
    expect(screen.queryByText(/Sign in to manage subnets/)).toBeNull()
  })
})
