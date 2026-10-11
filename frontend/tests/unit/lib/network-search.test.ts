import { describe, expect, it } from 'vitest'
import { agentMatches, searchNodeKeys, subnetMatches } from '@/lib/network-search'
import { placeholderAgent } from '@/lib/network-agent'
import type { AgentGroup } from '@/lib/types/agent-group'

const writer = { ...placeholderAgent('w', 'Drafts articles', 'active'), name: 'Copy Writer', skills: ['blog'], tags: ['c++'] }
const research: AgentGroup = { group_id: 'r', name: 'Research', description: 'Papers', agents: ['w'], type: 'user', owner_id: 'u' } as AgentGroup

describe('network search', () => {
  it('matches trimmed, case-insensitive substrings across fields', () => {
    expect(agentMatches(writer, '  WRITER ', [])).toBe(true)
    expect(agentMatches(writer, 'articles', [])).toBe(true)
    expect(agentMatches(writer, 'BLOG', [])).toBe(true)
    expect(agentMatches(writer, 'research', ['Research'])).toBe(true)
    expect(agentMatches(writer, 'nothing', [])).toBe(false)
  })

  it('treats regex characters literally', () => {
    expect(agentMatches(writer, 'c++', [])).toBe(true)
    expect(() => agentMatches(writer, '(', [])).not.toThrow()
    expect(agentMatches(writer, '(', [])).toBe(false)
  })

  it('matches everything for an empty query', () => {
    expect(agentMatches(writer, '   ', [])).toBe(true)
    expect(searchNodeKeys('  ', [writer], [research])).toBeNull()
  })

  it('returns matching node keys for topology highlighting', () => {
    expect(subnetMatches(research, 'paper')).toBe(true)
    expect(searchNodeKeys('research', [writer], [research])).toEqual(new Set(['network:r']))
    expect(searchNodeKeys('writer', [writer], [research])).toEqual(new Set(['agent:w']))
  })
})
