import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup, waitFor } from '@testing-library/react'

const mockListAgentGroups = vi.fn()
const mockGetAllActiveAgents = vi.fn()

vi.mock('@/lib/api/agent-group', () => ({
  listAgentGroups: (...args: unknown[]) => mockListAgentGroups(...args),
}))

vi.mock('@/lib/api/agent', () => ({
  getAllAgents: vi.fn().mockResolvedValue({ success: true, agents: [] }),
  getAllActiveAgents: (...args: unknown[]) => mockGetAllActiveAgents(...args),
}))

import { useGroupManagement } from '@/hooks/useGroupManagement'
import { BUILTIN_GROUP_ALL_AGENTS, BUILTIN_GROUP_ROOM_TEAM } from '@/lib/types/agent-group'

const mockGetToken = vi.fn().mockResolvedValue('test-token')

function defaultOptions(overrides: Record<string, unknown> = {}) {
  return {
    userId: 'user-1',
    getToken: mockGetToken,
    isLoaded: true,
    ...overrides,
  }
}

describe('useGroupManagement – default team behavior', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    mockListAgentGroups.mockResolvedValue({ success: true, groups: [] })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })
  })

  afterEach(() => {
    cleanup()
  })

  it('defaults to all_agents when no default team is provided', async () => {
    const { result } = renderHook(() =>
      useGroupManagement(defaultOptions())
    )

    await waitFor(() => {
      expect(result.current.loadingGroups).toBe(false)
    })

    expect(result.current.selectedGroup).toBe(BUILTIN_GROUP_ALL_AGENTS)
    expect(result.current.resolvedTargetMode).toEqual({ message_target_mode: 'all_agents' })
  })

  it('uses the room source team when that team still exists', async () => {
    mockListAgentGroups.mockResolvedValue({
      success: true,
      groups: [{
        group_id: 'team-research',
        name: 'Research Team',
        type: 'user',
        owner_id: 'user-1',
        agents: ['agent-1'],
      }],
    })

    const { result } = renderHook(() =>
      useGroupManagement(defaultOptions({ defaultGroup: 'team-research' }))
    )

    await waitFor(() => {
      expect(result.current.selectedGroup).toBe('team-research')
    })

    expect(result.current.resolvedTargetMode).toEqual({
      message_target_mode: 'saved_group',
      target_group_id: 'team-research',
    })
    expect(result.current.isOverride).toBe(false)
  })

  it('falls back to all_agents when the room source team was deleted', async () => {
    const { result } = renderHook(() =>
      useGroupManagement(defaultOptions({ defaultGroup: 'deleted-team' }))
    )

    await waitFor(() => {
      expect(result.current.loadingGroups).toBe(false)
    })

    expect(result.current.selectedGroup).toBe(BUILTIN_GROUP_ALL_AGENTS)
    expect(result.current.resolvedTargetMode).toEqual({ message_target_mode: 'all_agents' })
  })

  it('treats the legacy room_team default as all_agents', async () => {
    const { result } = renderHook(() =>
      useGroupManagement(defaultOptions({ defaultGroup: BUILTIN_GROUP_ROOM_TEAM }))
    )

    await waitFor(() => {
      expect(result.current.loadingGroups).toBe(false)
    })

    expect(result.current.selectedGroup).toBe(BUILTIN_GROUP_ALL_AGENTS)
  })

  it('explicit override works when the selected team exists', async () => {
    mockListAgentGroups.mockResolvedValue({
      success: true,
      groups: [{
        group_id: 'grp-custom',
        name: 'Custom Team',
        type: 'user',
        owner_id: 'user-1',
        agents: ['agent-1'],
      }],
    })
    const { result } = renderHook(() =>
      useGroupManagement(defaultOptions())
    )

    await waitFor(() => {
      expect(result.current.groups).toHaveLength(1)
    })

    act(() => {
      result.current.handleGroupChange('grp-custom')
    })

    expect(result.current.selectedGroup).toBe('grp-custom')
    expect(result.current.isOverride).toBe(true)
  })

  it('explicit saved_group override works and persists for a room', async () => {
    mockListAgentGroups.mockResolvedValue({
      success: true,
      groups: [{
        group_id: 'grp-my-saved',
        name: 'Saved Team',
        type: 'user',
        owner_id: 'user-1',
        agents: ['agent-1'],
      }],
    })
    const { result } = renderHook(() =>
      useGroupManagement(defaultOptions({ roomId: 'room-empty' }))
    )

    await waitFor(() => {
      expect(result.current.groups).toHaveLength(1)
    })

    act(() => {
      result.current.handleGroupChange('grp-my-saved')
    })

    expect(result.current.selectedGroup).toBe('grp-my-saved')
    expect(result.current.resolvedTargetMode).toEqual({
      message_target_mode: 'saved_group',
      target_group_id: 'grp-my-saved',
    })
    expect(localStorage.getItem('room-room-empty-override-group')).toBe('grp-my-saved')
  })

  it('falls back to all_agents for a stale explicit override', async () => {
    const { result } = renderHook(() =>
      useGroupManagement(defaultOptions({ roomId: 'room-empty' }))
    )

    await waitFor(() => {
      expect(result.current.loadingGroups).toBe(false)
    })

    act(() => {
      result.current.handleGroupChange('deleted-team')
    })

    expect(result.current.selectedGroup).toBe(BUILTIN_GROUP_ALL_AGENTS)
    expect(result.current.resolvedTargetMode).toEqual({ message_target_mode: 'all_agents' })
    expect(result.current.isOverride).toBe(false)
  })

  it('clearing override restores the existing default team', async () => {
    mockListAgentGroups.mockResolvedValue({
      success: true,
      groups: [{
        group_id: 'team-research',
        name: 'Research Team',
        type: 'user',
        owner_id: 'user-1',
        agents: ['agent-1'],
      }],
    })

    const { result } = renderHook(() =>
      useGroupManagement(defaultOptions({
        roomId: 'room-team',
        defaultGroup: 'team-research',
      }))
    )

    await waitFor(() => {
      expect(result.current.selectedGroup).toBe('team-research')
    })

    act(() => {
      result.current.handleGroupChange(BUILTIN_GROUP_ALL_AGENTS)
    })
    expect(result.current.selectedGroup).toBe(BUILTIN_GROUP_ALL_AGENTS)

    act(() => {
      result.current.handleClearOverride()
    })
    expect(result.current.selectedGroup).toBe('team-research')
    expect(result.current.isOverride).toBe(false)
    expect(localStorage.getItem('room-room-team-override-group')).toBeNull()
  })
})
