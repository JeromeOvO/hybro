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

describe('useGroupManagement – empty room behavior', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    mockListAgentGroups.mockResolvedValue({ success: true, groups: [] })
    mockGetAllActiveAgents.mockResolvedValue({ success: true, agents: [] })
  })

  afterEach(() => {
    cleanup()
  })

  it('selectedGroup defaults to all_agents when roomAgentCount=0 and no defaultGroup', async () => {
    const { result } = renderHook(() =>
      useGroupManagement(defaultOptions({ roomAgentCount: 0 }))
    )

    await waitFor(() => {
      expect(result.current.loadingGroups).toBe(false)
    })

    expect(result.current.selectedGroup).toBe(BUILTIN_GROUP_ALL_AGENTS)
  })

  it('resolvedTargetMode is all_agents when selectedGroup defaults to all_agents', async () => {
    const { result } = renderHook(() =>
      useGroupManagement(defaultOptions({ roomAgentCount: 0 }))
    )

    await waitFor(() => {
      expect(result.current.loadingGroups).toBe(false)
    })

    expect(result.current.resolvedTargetMode).toEqual({ message_target_mode: 'all_agents' })
  })

  it('selectedGroup is room_team when room has agents', async () => {
    const { result } = renderHook(() =>
      useGroupManagement(defaultOptions({ roomAgentCount: 3 }))
    )

    await waitFor(() => {
      expect(result.current.loadingGroups).toBe(false)
    })

    expect(result.current.selectedGroup).toBe(BUILTIN_GROUP_ROOM_TEAM)
  })

  it('explicit override works in empty room', async () => {
    const { result } = renderHook(() =>
      useGroupManagement(defaultOptions({ roomAgentCount: 0 }))
    )

    await waitFor(() => {
      expect(result.current.loadingGroups).toBe(false)
    })

    expect(result.current.selectedGroup).toBe(BUILTIN_GROUP_ALL_AGENTS)

    act(() => {
      result.current.handleGroupChange('grp-custom')
    })

    expect(result.current.selectedGroup).toBe('grp-custom')
    expect(result.current.isOverride).toBe(true)
  })

  it('explicit saved_group override works in empty room', async () => {
    const { result } = renderHook(() =>
      useGroupManagement(defaultOptions({ roomAgentCount: 0, roomId: 'room-empty' }))
    )

    await waitFor(() => {
      expect(result.current.loadingGroups).toBe(false)
    })

    act(() => {
      result.current.handleGroupChange('grp-my-saved')
    })

    expect(result.current.selectedGroup).toBe('grp-my-saved')
    expect(result.current.resolvedTargetMode).toEqual({
      message_target_mode: 'saved_group',
      target_group_id: 'grp-my-saved',
    })
  })

  it('clearing override in empty room restores all_agents', async () => {
    const { result } = renderHook(() =>
      useGroupManagement(defaultOptions({ roomAgentCount: 0, roomId: 'room-empty' }))
    )

    await waitFor(() => {
      expect(result.current.loadingGroups).toBe(false)
    })

    act(() => {
      result.current.handleGroupChange('grp-custom')
    })
    expect(result.current.selectedGroup).toBe('grp-custom')

    act(() => {
      result.current.handleClearOverride()
    })
    expect(result.current.selectedGroup).toBe(BUILTIN_GROUP_ALL_AGENTS)
    expect(result.current.isOverride).toBe(false)
  })

  it('defaultGroup is respected for empty room when provided', async () => {
    const { result } = renderHook(() =>
      useGroupManagement(defaultOptions({
        roomAgentCount: 0,
        defaultGroup: BUILTIN_GROUP_ALL_AGENTS,
      }))
    )

    await waitFor(() => {
      expect(result.current.loadingGroups).toBe(false)
    })

    expect(result.current.selectedGroup).toBe(BUILTIN_GROUP_ALL_AGENTS)
  })
})
