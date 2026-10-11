import { describe, expect, it } from 'vitest'
import { buildNeighbors, DEFAULT_GRAPH_DISPLAY, emphasisFor, isLabelVisible } from '@/lib/graph-labels'

const neighbors = buildNeighbors([
  { networkId: 'R', agentId: 'a' },
  { networkId: 'R', agentId: 'b' },
  { networkId: 'T', agentId: 'b' },
])
const ctx = { hovered: null, selected: null, prefs: DEFAULT_GRAPH_DISPLAY, neighbors }

describe('graph labels', () => {
  it('shows subnet labels and hides agent labels by default', () => {
    expect(isLabelVisible('network:R', ctx)).toBe(true)
    expect(isLabelVisible('agent:a', ctx)).toBe(false)
  })

  it('shows an agent label on hover or focus of the agent', () => {
    expect(isLabelVisible('agent:a', { ...ctx, hovered: 'agent:a' })).toBe(true)
  })

  it('shows agent labels when a subnet they belong to is hovered', () => {
    const hoverR = { ...ctx, hovered: 'network:R' }
    expect(isLabelVisible('agent:a', hoverR)).toBe(true)
    expect(isLabelVisible('agent:b', hoverR)).toBe(true)
    expect(isLabelVisible('agent:b', { ...ctx, hovered: 'network:T' })).toBe(true)
    expect(isLabelVisible('agent:a', { ...ctx, hovered: 'network:T' })).toBe(false)
  })

  it('does not reveal sibling agents when an agent is hovered', () => {
    expect(isLabelVisible('agent:b', { ...ctx, hovered: 'agent:a' })).toBe(false)
  })

  it('shows labels for the selection and its related agents', () => {
    expect(isLabelVisible('agent:a', { ...ctx, selected: 'network:R' })).toBe(true)
    expect(isLabelVisible('agent:a', { ...ctx, selected: 'agent:a' })).toBe(true)
  })

  it('honors both display switches', () => {
    const off = { ...ctx, prefs: { alwaysSubnetLabels: false, alwaysAgentLabels: true } }
    expect(isLabelVisible('network:R', off)).toBe(false)
    expect(isLabelVisible('network:R', { ...off, hovered: 'network:R' })).toBe(true)
    expect(isLabelVisible('agent:a', off)).toBe(true)
  })

  it('prioritizes hover over search over selection for emphasis', () => {
    const search = new Set(['agent:a'])
    expect(emphasisFor({ hovered: null, selected: null, search: null, neighbors })).toBeNull()
    expect(emphasisFor({ hovered: 'network:T', selected: 'network:R', search, neighbors })).toEqual({
      nodes: new Set(['network:T', 'agent:b']), anchors: new Set(['network:T']),
    })
    expect(emphasisFor({ hovered: null, selected: 'network:R', search, neighbors })).toEqual({
      nodes: new Set(['agent:a', 'network:R']), anchors: new Set(['agent:a']),
    })
  })
})
