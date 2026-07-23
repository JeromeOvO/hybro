import { describe, it, expect } from 'vitest'
import {
  BUILTIN_GROUP_ALL_AGENTS,
  BUILTIN_GROUP_ROOM_TEAM,
  resolveSelectedGroupDispatch,
} from '@/lib/types/agent-group'
import type { StaleAgentRef } from '@/lib/types/agent-group'

describe('resolveSelectedGroupDispatch', () => {
  it('maps room_team sentinel to room_default mode', () => {
    const result = resolveSelectedGroupDispatch(BUILTIN_GROUP_ROOM_TEAM)
    expect(result).toEqual({ message_target_mode: 'room_default' })
  })

  it('maps all_agents sentinel to all_agents mode', () => {
    const result = resolveSelectedGroupDispatch(BUILTIN_GROUP_ALL_AGENTS)
    expect(result).toEqual({ message_target_mode: 'all_agents' })
  })

  it('maps arbitrary group ID to saved_group mode', () => {
    const result = resolveSelectedGroupDispatch('grp-abc-123')
    expect(result).toEqual({
      message_target_mode: 'saved_group',
      target_group_id: 'grp-abc-123',
    })
  })
})

describe('StaleAgentRef', () => {
  it('can represent an inaccessible agent', () => {
    const ref: StaleAgentRef = { id: 'agent-x', name: 'Private Bot', availability: 'inaccessible' }
    expect(ref.availability).toBe('inaccessible')
  })

  it('can represent a deleted agent', () => {
    const ref: StaleAgentRef = { id: 'agent-y', name: 'Old Bot', availability: 'deleted' }
    expect(ref.availability).toBe('deleted')
  })

  it('can represent an inactive agent', () => {
    const ref: StaleAgentRef = { id: 'agent-z', name: 'Sleeping Bot', availability: 'inactive' }
    expect(ref.availability).toBe('inactive')
  })
})

describe('membership change detection', () => {
  it('detects unchanged membership when IDs match (regardless of order)', () => {
    const currentIds = ['agent-a', 'agent-b', 'agent-c']
    const newIds = ['agent-c', 'agent-a', 'agent-b']
    const changed =
      currentIds.length !== newIds.length ||
      [...new Set(currentIds)].sort().join(',') !== [...new Set(newIds)].sort().join(',')
    expect(changed).toBe(false)

  })

  it('detects changed membership when an agent is added', () => {
    const currentIds = ['agent-a', 'agent-b']
    const newIds = ['agent-a', 'agent-b', 'agent-c']
    const changed =
      [...new Set(currentIds)].sort().join(',') !== [...new Set(newIds)].sort().join(',')
    expect(changed).toBe(true)
  })

  it('detects changed membership when an agent is removed', () => {
    const currentIds = ['agent-a', 'agent-b', 'agent-c']
    const newIds = ['agent-a', 'agent-c']
    const changed =
      [...new Set(currentIds)].sort().join(',') !== [...new Set(newIds)].sort().join(',')
    expect(changed).toBe(true)
  })

  it('merges active + stale IDs correctly', () => {
    const activeIds = ['agent-a', 'agent-b']
    const staleRefs: StaleAgentRef[] = [
      { id: 'agent-stale-1', name: 'Old Bot', availability: 'inaccessible' },
      { id: 'agent-stale-2', name: 'Deleted Bot', availability: 'deleted' },
    ]
    const merged = [...activeIds, ...staleRefs.map(r => r.id)]
    expect(merged).toEqual(['agent-a', 'agent-b', 'agent-stale-1', 'agent-stale-2'])
  })
})

describe('selectedGroup derivation', () => {
  function deriveSelectedGroup(
    overrideGroup: string | null,
    roomAgentCount: number,
    defaultGroup?: string,
  ): string {
    if (overrideGroup !== null) return overrideGroup
    if (roomAgentCount > 0) return BUILTIN_GROUP_ROOM_TEAM
    return defaultGroup || BUILTIN_GROUP_ALL_AGENTS
  }

  it('returns override when set', () => {
    expect(deriveSelectedGroup('grp-override', 5)).toBe('grp-override')
  })

  it('returns room_team when room has agents and no override', () => {
    expect(deriveSelectedGroup(null, 3)).toBe(BUILTIN_GROUP_ROOM_TEAM)
  })

  it('returns all_agents for empty room with no defaultGroup', () => {
    expect(deriveSelectedGroup(null, 0)).toBe(BUILTIN_GROUP_ALL_AGENTS)
  })

  it('returns all_agents for empty room with undefined defaultGroup', () => {
    expect(deriveSelectedGroup(null, 0, undefined)).toBe(BUILTIN_GROUP_ALL_AGENTS)
  })

  it('returns defaultGroup for empty room when provided', () => {
    expect(deriveSelectedGroup(null, 0, BUILTIN_GROUP_ALL_AGENTS)).toBe(BUILTIN_GROUP_ALL_AGENTS)
  })

  it('override takes precedence over room agents', () => {
    expect(deriveSelectedGroup('grp-custom', 10, BUILTIN_GROUP_ALL_AGENTS)).toBe('grp-custom')
  })
})

describe('resolvedTargetMode from selectedGroup', () => {
  it('returns all_agents for all_agents selectedGroup', () => {
    const mode = resolveSelectedGroupDispatch(BUILTIN_GROUP_ALL_AGENTS)
    expect(mode).toEqual({ message_target_mode: 'all_agents' })
  })

  it('returns room_default for room_team', () => {
    const mode = resolveSelectedGroupDispatch(BUILTIN_GROUP_ROOM_TEAM)
    expect(mode).toEqual({ message_target_mode: 'room_default' })
  })

  it('returns saved_group for custom group ID', () => {
    const mode = resolveSelectedGroupDispatch('grp-my-saved')
    expect(mode).toEqual({ message_target_mode: 'saved_group', target_group_id: 'grp-my-saved' })
  })
})
