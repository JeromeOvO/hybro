import { describe, expect, it } from 'vitest'
import {
  DEFAULT_NETWORK_URL_STATE,
  parseNetworkUrlState,
  resolveNetworkUrlState,
  serializeNetworkUrlState,
} from '@/lib/network-url-state'

const parse = (query: string) => parseNetworkUrlState(new URLSearchParams(query))

describe('network URL state', () => {
  it('parses all five params independently', () => {
    expect(parse('view=structure&q=writer&scope=subnet%3AA&layout=subnet&select=agent%3AB')).toEqual({
      view: 'structure',
      q: 'writer',
      scope: { kind: 'subnet', id: 'A' },
      layout: 'subnet',
      select: { type: 'agent', id: 'B' },
    })
  })

  it('falls back to defaults for malformed values', () => {
    expect(parse('view=foo&scope=subnet%3A&layout=grid&select=room%3Ax')).toEqual(DEFAULT_NETWORK_URL_STATE)
    expect(parse('select=agent%3A')).toEqual(DEFAULT_NETWORK_URL_STATE)
    expect(parse('scope=unassigned').scope).toEqual({ kind: 'unassigned' })
  })

  it('uses the first value when a param repeats', () => {
    expect(parse('view=structure&view=topology').view).toBe('structure')
  })

  it('omits defaults when serializing and round-trips ids with colons', () => {
    expect(serializeNetworkUrlState(DEFAULT_NETWORK_URL_STATE)).toBe('')
    const state = { ...DEFAULT_NETWORK_URL_STATE, q: 'c++ (', scope: { kind: 'subnet' as const, id: 'a:b' } }
    expect(parse(serializeNetworkUrlState(state))).toEqual(state)
  })

  it('resets stale ids to defaults without touching other params', () => {
    const state = parse('view=structure&q=x&scope=subnet%3Agone&select=agent%3Agone')
    expect(resolveNetworkUrlState(state, { subnetIds: new Set(), agentIds: new Set() })).toEqual({
      ...state,
      scope: { kind: 'all' },
      select: null,
    })
  })

  it('keeps a selected subnet that still exists', () => {
    const state = parse('select=subnet%3AA')
    expect(resolveNetworkUrlState(state, { subnetIds: new Set(['A']), agentIds: new Set() }).select).toEqual({ type: 'subnet', id: 'A' })
  })
})
