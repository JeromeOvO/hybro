import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render } from '@testing-library/react'
import { NetworkCanvas } from '@/components/networks/network-canvas'
import { placeholderAgent } from '@/lib/network-agent'
import { DEFAULT_GRAPH_DISPLAY } from '@/lib/graph-labels'
import type { AgentGroup } from '@/lib/types/agent-group'

vi.mock('@/components/networks/network-canvas-controller', () => ({
  createNetworkCanvasController: () => ({ update: vi.fn(), destroy: vi.fn(), fitAll: vi.fn(), focusNetwork: vi.fn(), zoom: vi.fn(), resetLayout: vi.fn() }),
}))

afterEach(cleanup)

const networks = [{ group_id: 'R', name: 'Research', agents: ['a'], type: 'user', owner_id: 'u' }] as AgentGroup[]
const agents = [{ ...placeholderAgent('a', '', 'active'), name: 'web-search' }, { ...placeholderAgent('z', '', 'inactive'), name: 'lone' }]

function setup(search: Set<string> | null = null) {
  return render(<NetworkCanvas networks={networks} agents={agents} selection={null} search={search} display={DEFAULT_GRAPH_DISPLAY} onSelect={vi.fn()} onZoomChange={vi.fn()} />)
}

describe('NetworkCanvas', () => {
  it('draws subnets and agents at the same radius', () => {
    const { container } = setup()
    const radii = [...container.querySelectorAll('.network-hub-dot, .network-agent-dot')].map(dot => dot.getAttribute('r'))
    expect(new Set(radii)).toEqual(new Set(['6']))
  })

  it('reveals member agent labels while a subnet is hovered', () => {
    const { container } = setup()
    const label = container.querySelector('[data-agent-id="a"] .network-agent-label')!
    expect(label).toHaveClass('label-hidden')
    // React synthesizes onPointerEnter from pointerover.
    fireEvent.pointerOver(container.querySelector('.network-hub[data-network-id="R"]')!)
    expect(label).not.toHaveClass('label-hidden')
  })

  it('dims non-matching nodes during search without removing them', () => {
    const { container } = setup(new Set(['agent:a']))
    expect(container.querySelector('[data-agent-id="z"]')).toHaveClass('dimmed')
    expect(container.querySelectorAll('[data-node-key]')).toHaveLength(3)
  })

  it('marks offline agents', () => {
    const { container } = setup()
    expect(container.querySelector('[data-agent-id="z"]')).toHaveClass('inactive')
  })
})
