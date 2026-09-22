import { createElement, type ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useNetworks } from '@/hooks/useNetworks'
import { getAgentsByProviderId, getAllAgents } from '@/lib/api/agent'
import { createAgentGroup, deleteAgentGroup, listAgentGroups, updateAgentGroup } from '@/lib/api/agent-group'
import type { Agent, AgentCenterResponse } from '@/lib/types/response'
import type { AgentGroup, AgentGroupListResponse, AgentGroupResponse } from '@/lib/types/agent-group'

const auth = vi.hoisted(() => ({
  userId: 'owner-1',
  isLoaded: true,
  isSignedIn: true,
  getToken: async () => 'test-token'
}))

vi.mock('@/lib/auth', () => ({ useAuth: () => auth }))
vi.mock('@/lib/api/agent', () => ({ getAllAgents: vi.fn(), getAgentsByProviderId: vi.fn() }))
vi.mock('@/lib/api/agent-group', () => ({
  createAgentGroup: vi.fn(),
  deleteAgentGroup: vi.fn(),
  listAgentGroups: vi.fn(),
  updateAgentGroup: vi.fn()
}))

const baseGroup: AgentGroup = {
  group_id: 'network-1',
  name: 'Research',
  description: 'Original description',
  owner_id: 'owner-1',
  type: 'user',
  agents: ['agent-1', 'missing-agent']
}
const catalog: Agent[] = [
  {
    agent_id: 'agent-1',
    provider_id: 'owner-1',
    agent_status: 'active',
    agent_card: {
      name: 'Researcher',
      description: 'Researches sources',
      url: 'https://example.test/researcher',
      version: '1.0.0',
      protocolVersion: '0.3.0',
      capabilities: {},
      defaultInputModes: ['text'],
      defaultOutputModes: ['text'],
      skills: [{ id: 'research', name: 'Research', description: 'Find sources', tags: ['research'] }]
    }
  }
]

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((fulfill) => {
    resolve = fulfill
  })
  return { promise, resolve }
}

let serverGroups: AgentGroup[]
const clients: QueryClient[] = []

function mountNetworks() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  clients.push(client)
  const wrapper = ({ children }: { children: ReactNode }) => createElement(QueryClientProvider, { client }, children)
  return renderHook(() => useNetworks(), { wrapper })
}

beforeEach(() => {
  vi.resetAllMocks()
  auth.userId = 'owner-1'
  serverGroups = [{ ...baseGroup, agents: [...baseGroup.agents] }]
  vi.mocked(listAgentGroups).mockImplementation(async (ownerId) => ({
    success: true,
    groups: serverGroups
      .filter((group) => group.owner_id === ownerId || group.type === 'builtin')
      .map((group) => ({ ...group, agents: [...group.agents] }))
  }))
  vi.mocked(getAllAgents).mockImplementation(async (options) => ({
    success: true,
    agents: options?.activeOnly ? catalog.filter((agent) => agent.agent_status === 'active') : catalog
  }))
  vi.mocked(getAgentsByProviderId).mockResolvedValue({ success: true, agents: [] })
  vi.mocked(createAgentGroup).mockImplementation(async (request) => {
    const group: AgentGroup = { ...request, group_id: 'created-network', type: 'user' }
    serverGroups.push(group)
    return { success: true, group }
  })
  vi.mocked(updateAgentGroup).mockImplementation(async (request) => {
    const group = serverGroups.find((item) => item.group_id === request.group_id)
    if (!group) return { success: false, error: 'Network not found' }
    if (request.name !== undefined) group.name = request.name.trim()
    if (request.description !== undefined) group.description = request.description
    if (request.agents !== undefined) group.agents = [...new Set(request.agents)]
    return { success: true, group: { ...group, agents: [...group.agents] } }
  })
  vi.mocked(deleteAgentGroup).mockImplementation(async (id) => {
    serverGroups = serverGroups.filter((group) => group.group_id !== id)
    return { success: true }
  })
})

afterEach(() => {
  cleanup()
  for (const client of clients) client.clear()
  clients.length = 0
})

describe('useNetworks', () => {
  it('keeps a failed group read distinct from an empty collection and recovers through refresh', async () => {
    vi.mocked(listAgentGroups).mockResolvedValueOnce({ success: false, error: 'Access denied', groups: [] })
    const { result } = mountNetworks()

    await waitFor(() => expect(result.current.error).toBe('Access denied'))
    expect(result.current.loading).toBe(false)
    expect(result.current.agentsError).toBeNull()

    await act(async () => {
      await result.current.refresh()
    })
    await waitFor(() => expect(result.current.networks).toEqual([baseGroup]))
    expect(result.current.error).toBeNull()

    vi.mocked(listAgentGroups).mockResolvedValueOnce({ success: false, error: 'Temporarily unavailable' })
    await act(async () => {
      await expect(result.current.refresh()).resolves.toBeUndefined()
    })
    await waitFor(() => expect(result.current.error).toBe('Temporarily unavailable'))
    expect(result.current.networks).toEqual([baseGroup])
  })

  it('rejects successful envelopes that omit the required read or write payload', async () => {
    vi.mocked(listAgentGroups).mockResolvedValueOnce({ success: true })
    vi.mocked(getAllAgents).mockResolvedValueOnce({ success: true })
    const { result } = mountNetworks()

    await waitFor(() => {
      expect(result.current.error).not.toBeNull()
      expect(result.current.agentsError).not.toBeNull()
    })
    await act(async () => {
      await result.current.refresh()
    })
    await waitFor(() => expect(result.current.networks).toEqual([baseGroup]))
    vi.mocked(updateAgentGroup).mockResolvedValueOnce({ success: true })
    await act(async () => {
      await expect(result.current.update(baseGroup.group_id, { name: 'Renamed', description: '' })).rejects.toThrow()
    })
    expect(result.current.networks).toEqual([baseGroup])
  })

  it('retains registered-only inactive agents when discovery fails and verifies missing members after recovery', async () => {
    const inactive = { ...catalog[0], agent_id: 'registered-only', agent_status: 'inactive' as const }
    vi.mocked(getAgentsByProviderId).mockResolvedValue({ success: true, agents: [inactive] })
    vi.mocked(getAllAgents).mockResolvedValueOnce({ success: false, error: 'Catalog unavailable', agents: [] })
    const { result } = mountNetworks()
    await waitFor(() => {
      expect(result.current.agentsLoading).toBe(false)
      expect(result.current.agentsError).not.toBeNull()
      expect(result.current.networks).toEqual([baseGroup])
    })
    expect(result.current.agents.find((agent) => agent.id === inactive.agent_id)?.status).toBe('inactive')
    expect(result.current.agents.find((agent) => agent.id === 'missing-agent')?.status).toBe('unknown')
    expect(result.current.error).toBeNull()

    await act(async () => {
      await result.current.refreshAgents()
    })
    await waitFor(() => expect(result.current.agentsError).toBeNull())
    expect(result.current.agents.find((agent) => agent.id === 'missing-agent')?.status).toBe('unavailable')
    expect(result.current.agents.map((agent) => agent.id)).toEqual(['agent-1', 'registered-only', 'missing-agent'])
    expect(result.current.networks[0].agents).toEqual(['agent-1', 'missing-agent'])
  })

  it('preserves discovered inventory when registration fails and refreshes both independent inventories', async () => {
    vi.mocked(getAgentsByProviderId).mockRejectedValueOnce(new Error('Registration unavailable'))
    const { result } = mountNetworks()
    await waitFor(() => {
      expect(result.current.agentsLoading).toBe(false)
      expect(result.current.agentsError).not.toBeNull()
      expect(result.current.networks).toEqual([baseGroup])
    })
    expect(result.current.agents.find((agent) => agent.id === 'agent-1')?.name).toBe('Researcher')
    expect(result.current.agents.find((agent) => agent.id === 'missing-agent')?.status).toBe('unknown')

    const discoveredRead = deferred<AgentCenterResponse>()
    const registeredRead = deferred<AgentCenterResponse>()
    vi.mocked(getAllAgents).mockReturnValueOnce(discoveredRead.promise)
    vi.mocked(getAgentsByProviderId).mockReturnValueOnce(registeredRead.promise)
    let refreshing!: Promise<void>
    act(() => {
      refreshing = result.current.refreshAgents()
    })
    await waitFor(() => expect(result.current.refreshing).toBe(true))
    await act(async () => {
      discoveredRead.resolve({
        success: true,
        agents: [...catalog, { ...catalog[0], agent_id: 'new-discovery' }]
      })
    })
    await waitFor(() => expect(result.current.agents.some((agent) => agent.id === 'new-discovery')).toBe(true))
    expect(result.current.refreshing).toBe(true)
    expect(result.current.agents.find((agent) => agent.id === 'missing-agent')?.status).toBe('unknown')
    await act(async () => {
      registeredRead.resolve({
        success: true,
        agents: [{ ...catalog[0], agent_id: 'new-registration', agent_status: 'inactive' }]
      })
      await refreshing
    })
    await waitFor(() => expect(result.current.refreshing).toBe(false))
    expect(result.current.agentsError).toBeNull()
    expect(result.current.agents.find((agent) => agent.id === 'new-registration')?.status).toBe('inactive')
    expect(result.current.agents.find((agent) => agent.id === 'missing-agent')?.status).toBe('unavailable')
  })

  it('deduplicates agents across memberships and uses the authoritative registered record', async () => {
    const registered = {
      ...catalog[0],
      agent_status: 'inactive' as const,
      agent_card: { ...catalog[0].agent_card, name: 'Authoritative registration' }
    }
    const orphan = { ...catalog[0], agent_id: 'orphan', agent_status: 'inactive' as const }
    vi.mocked(getAllAgents).mockResolvedValue({ success: true, agents: [...catalog, ...catalog] })
    vi.mocked(getAgentsByProviderId).mockResolvedValue({ success: true, agents: [registered, orphan] })
    serverGroups.push({ ...baseGroup, group_id: 'network-2' })
    const { result } = mountNetworks()
    await waitFor(() => {
      expect(result.current.agentsLoading).toBe(false)
      expect(result.current.networks).toHaveLength(2)
    })
    expect(result.current.agents.filter((agent) => agent.id === 'agent-1')).toEqual([
      expect.objectContaining({ name: 'Authoritative registration', status: 'inactive' })
    ])
    expect(result.current.agents.find((agent) => agent.id === 'orphan')?.status).toBe('inactive')
    expect(result.current.networks.every((network) => !network.agents.includes('orphan'))).toBe(true)
  })

  it('matches Agent inventory visibility and retains unavailable saved references separately', async () => {
    const deleted = { ...catalog[0], agent_id: 'deleted-member', agent_status: 'deleted' as const }
    const local = { ...catalog[0], source: 'local' as const }
    const hub = { ...catalog[0], source: 'hub' as const }
    vi.mocked(getAllAgents).mockResolvedValue({
      success: true,
      agents: [
        ...catalog,
        { ...deleted, agent_status: 'active' },
        { ...local, agent_id: 'local-active' },
        { ...local, agent_id: 'local-inactive', agent_status: 'inactive' },
        { ...hub, agent_id: 'hub-online', is_hub_online: true },
        { ...hub, agent_id: 'hub-offline', is_hub_online: false },
        { ...hub, agent_id: 'hub-inactive', agent_status: 'inactive', is_hub_online: true }
      ]
    })
    vi.mocked(getAgentsByProviderId).mockResolvedValue({
      success: true,
      agents: [deleted, { ...deleted, agent_id: 'unrelated-deleted' }]
    })
    serverGroups[0].agents.push('deleted-member', 'deleted-member')
    serverGroups.push({ ...baseGroup, group_id: 'all_agents', type: 'builtin', owner_id: null })
    const { result } = mountNetworks()
    await waitFor(() => {
      expect(result.current.agentsLoading).toBe(false)
      expect(result.current.networks).toEqual([serverGroups[0]])
    })
    expect(result.current.agents.map((agent) => agent.id)).toEqual([
      'agent-1',
      'local-active',
      'hub-online',
      'missing-agent',
      'deleted-member'
    ])
    expect(result.current.agents.find((agent) => agent.id === 'deleted-member')?.status).toBe('unavailable')
    expect(result.current.agents.some((agent) => agent.id === 'local-inactive')).toBe(false)
    expect(result.current.agents.some((agent) => agent.id === 'hub-offline')).toBe(false)
    expect(result.current.networks[0].agents).toEqual(['agent-1', 'missing-agent', 'deleted-member', 'deleted-member'])
  })

  it('waits for registered inventory and never exposes a previous owner’s late registration', async () => {
    const registeredRead = deferred<AgentCenterResponse>()
    vi.mocked(getAgentsByProviderId).mockReturnValueOnce(registeredRead.promise)
    const { result, rerender } = mountNetworks()
    await waitFor(() => {
      expect(result.current.networks).toEqual([baseGroup])
      expect(result.current.agents.find((agent) => agent.id === 'agent-1')?.status).toBe('active')
    })
    expect(result.current.agentsLoading).toBe(true)
    expect(result.current.agents.find((agent) => agent.id === 'missing-agent')?.status).toBe('unknown')

    auth.userId = 'owner-2'
    rerender()
    await waitFor(() => {
      expect(result.current.agentsLoading).toBe(false)
      expect(result.current.networks).toEqual([])
    })
    await act(async () => {
      registeredRead.resolve({
        success: true,
        agents: [{ ...catalog[0], agent_id: 'owner-one-private' }]
      })
    })
    expect(result.current.agents.map((agent) => agent.id)).toEqual(['agent-1'])
    expect(result.current.agentsError).toBeNull()
  })

  it('rejects failed create, metadata, membership and delete writes without changing the confirmed collection', async () => {
    const { result } = mountNetworks()
    await waitFor(() => expect(result.current.networks).toEqual([baseGroup]))
    vi.mocked(createAgentGroup).mockResolvedValueOnce({ success: false, error: 'Creation denied' })
    vi.mocked(updateAgentGroup)
      .mockResolvedValueOnce({ success: false, error: 'Metadata denied' })
      .mockResolvedValueOnce({ success: false, error: 'Membership denied' })
    vi.mocked(deleteAgentGroup).mockResolvedValueOnce({ success: false, error: 'Deletion denied' })

    await act(async () => {
      await expect(result.current.create({ name: 'New', description: '', agentIds: [] })).rejects.toThrow(
        'Creation denied'
      )
      await expect(result.current.update(baseGroup.group_id, { name: 'Changed', description: '' })).rejects.toThrow(
        'Metadata denied'
      )
      await expect(result.current.setMembers(baseGroup.group_id, [])).rejects.toThrow('Membership denied')
      await expect(result.current.remove(baseGroup.group_id)).rejects.toThrow('Deletion denied')
    })
    await waitFor(() => expect(result.current.pending).toBe(false))
    expect(result.current.networks).toEqual([baseGroup])
    expect(serverGroups).toEqual([baseGroup])
  })

  it('metadata edits preserve a member added on the server after the client read', async () => {
    const { result } = mountNetworks()
    await waitFor(() => expect(result.current.networks).toEqual([baseGroup]))
    serverGroups[0].agents.push('concurrent-member')

    await act(async () => {
      await result.current.update(baseGroup.group_id, { name: '  Renamed  ', description: 'Updated description' })
    })
    await waitFor(() => expect(result.current.networks[0].name).toBe('Renamed'))
    expect(result.current.networks[0].agents).toEqual(['agent-1', 'missing-agent', 'concurrent-member'])
    expect(serverGroups[0].agents).toEqual(['agent-1', 'missing-agent', 'concurrent-member'])
    expect(result.current.agents.find((agent) => agent.id === 'concurrent-member')?.status).toBe('unavailable')
  })

  it('uses canonical membership replies and changes the collection only after confirmed creation and deletion', async () => {
    const { result } = mountNetworks()
    await waitFor(() => expect(result.current.networks).toEqual([baseGroup]))
    await act(async () => {
      await result.current.setMembers(baseGroup.group_id, ['agent-1', 'missing-agent', 'agent-1'])
      await result.current.create({ name: 'Second Network', description: 'Server-created', agentIds: ['agent-1'] })
    })
    await waitFor(() =>
      expect(result.current.networks.map((group) => group.group_id)).toEqual(['network-1', 'created-network'])
    )
    expect(result.current.networks[0].agents).toEqual(['agent-1', 'missing-agent'])

    await act(async () => {
      await result.current.remove(baseGroup.group_id)
    })
    await waitFor(() => expect(result.current.networks.map((group) => group.group_id)).toEqual(['created-network']))
  })

  it('discards a stale read started during a write instead of erasing its confirmed result', async () => {
    const { result } = mountNetworks()
    await waitFor(() => expect(result.current.networks).toEqual([baseGroup]))
    const write = deferred<AgentGroupResponse>()
    const staleRead = deferred<AgentGroupListResponse>()
    vi.mocked(updateAgentGroup).mockReturnValueOnce(write.promise)
    let saving!: Promise<AgentGroup>
    act(() => {
      saving = result.current.update(baseGroup.group_id, { name: 'Confirmed name', description: '' })
    })
    await waitFor(() => expect(result.current.pending).toBe(true))

    vi.mocked(listAgentGroups).mockReturnValueOnce(staleRead.promise)
    let refreshing!: Promise<void>
    act(() => {
      refreshing = result.current.refresh()
    })
    await waitFor(() => expect(result.current.refreshing).toBe(true))
    await act(async () => {
      write.resolve({ success: true, group: { ...baseGroup, name: 'Confirmed name' } })
      await saving
    })
    await act(async () => {
      staleRead.resolve({ success: true, groups: [baseGroup] })
      await refreshing
    })
    await waitFor(() => expect(result.current.networks[0].name).toBe('Confirmed name'))
  })

  it('isolates a pending write from the next owner and cancels unused catalog reads', async () => {
    const { result, rerender, unmount } = mountNetworks()
    await waitFor(() => expect(result.current.networks).toEqual([baseGroup]))
    const write = deferred<AgentGroupResponse>()
    vi.mocked(updateAgentGroup).mockReturnValueOnce(write.promise)
    let saving!: Promise<AgentGroup>
    act(() => {
      saving = result.current.update(baseGroup.group_id, { name: 'Owner one changed', description: '' })
    })
    await waitFor(() => expect(result.current.pending).toBe(true))

    auth.userId = 'owner-2'
    const nextGroup = { ...baseGroup, group_id: 'owner-two-network', owner_id: 'owner-2', name: 'Owner two' }
    serverGroups.push(nextGroup)
    let catalogAborted = false
    vi.mocked(getAllAgents).mockImplementationOnce(async ({ signal } = {}) => {
      signal?.addEventListener(
        'abort',
        () => {
          catalogAborted = true
        },
        { once: true }
      )
      return new Promise(() => {})
    })
    rerender()
    await waitFor(() => expect(result.current.networks).toEqual([nextGroup]))
    await act(async () => {
      write.resolve({ success: true, group: { ...baseGroup, name: 'Owner one changed' } })
      await saving
    })
    expect(result.current.networks).toEqual([nextGroup])
    unmount()
    expect(catalogAborted).toBe(true)
  })
})
