import { describe, expect, it } from 'vitest'
import { buildStructureGroups, DEFAULT_STRUCTURE_SORT, scopeCounts } from '@/lib/structure-groups'
import { placeholderAgent } from '@/lib/network-agent'
import type { AgentGroup } from '@/lib/types/agent-group'

const a = { ...placeholderAgent('a', '', 'active'), name: 'alpha', roomCalls: 1 }
const b = { ...placeholderAgent('b', '', 'inactive'), name: 'beta writer', roomCalls: 9 }
const c = { ...placeholderAgent('c', '', 'active'), name: 'gamma', roomCalls: 5 }
const g = (id: string, agents: string[]) => ({ group_id: id, name: id, agents, type: 'user', owner_id: 'u' }) as AgentGroup
const networks = [g('R', ['a', 'b']), g('T', ['b'])]
const base = { agents: [a, b, c], networks, q: '', sort: DEFAULT_STRUCTURE_SORT }

describe('structure groups', () => {
  it('lists every agent in scope sorted by room calls descending', () => {
    const [list] = buildStructureGroups({ ...base, scope: { kind: 'all' }, layout: 'list' })
    expect(list.rows.map(row => row.agent.id)).toEqual(['b', 'c', 'a'])
    expect(list.rows.find(row => row.agent.id === 'b')!.subnets.map(s => s.group_id)).toEqual(['R', 'T'])
  })

  it('filters by scope and search independently', () => {
    expect(buildStructureGroups({ ...base, scope: { kind: 'unassigned' }, layout: 'list' })[0].rows.map(r => r.agent.id)).toEqual(['c'])
    expect(buildStructureGroups({ ...base, scope: { kind: 'subnet', id: 'R' }, layout: 'list', q: 'writer' })[0].rows.map(r => r.agent.id)).toEqual(['b'])
  })

  it('matches agents through their subnet names', () => {
    expect(buildStructureGroups({ ...base, scope: { kind: 'all' }, layout: 'list', q: 't' })[0].rows.map(r => r.agent.id)).toContain('b')
  })

  it('groups by subnet with duplicates, unique keys, and one tab stop per agent', () => {
    const groups = buildStructureGroups({ ...base, scope: { kind: 'all' }, layout: 'subnet' })
    expect(groups.map(group => group.title)).toEqual(['R', 'T', 'Unassigned'])
    const rows = groups.flatMap(group => group.rows)
    expect(new Set(rows.map(row => row.key)).size).toBe(rows.length)
    expect(rows.filter(row => row.agent.id === 'b').map(row => row.tabbable)).toEqual([true, false])
  })

  it('shows only the scoped group in the by-subnet layout', () => {
    expect(buildStructureGroups({ ...base, scope: { kind: 'subnet', id: 'T' }, layout: 'subnet' }).map(g => g.title)).toEqual(['T'])
    expect(buildStructureGroups({ ...base, scope: { kind: 'unassigned' }, layout: 'subnet' }).map(g => g.title)).toEqual(['Unassigned'])
  })

  it('sorts by name and status', () => {
    const byName = buildStructureGroups({ ...base, scope: { kind: 'all' }, layout: 'list', sort: { by: 'name', direction: 'asc' } })
    expect(byName[0].rows.map(r => r.agent.id)).toEqual(['a', 'b', 'c'])
    const byStatus = buildStructureGroups({ ...base, scope: { kind: 'all' }, layout: 'list', sort: { by: 'status', direction: 'asc' } })
    expect(byStatus[0].rows.at(-1)!.agent.id).toBe('b')
  })

  it('counts matches per scope', () => {
    expect(scopeCounts({ ...base, q: '' })).toEqual({ all: 3, unassigned: 1, subnets: new Map([['R', 2], ['T', 1]]) })
    expect(scopeCounts({ ...base, q: 'gamma' })).toEqual({ all: 1, unassigned: 1, subnets: new Map([['R', 0], ['T', 0]]) })
  })
})
