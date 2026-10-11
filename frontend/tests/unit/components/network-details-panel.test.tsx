import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { NetworkDetailsPanel } from '@/components/networks/network-details-panel'
import { placeholderAgent } from '@/lib/network-agent'
import type { AgentGroup } from '@/lib/types/agent-group'

afterEach(cleanup)

const research = { group_id: 'R', name: 'Research', agents: ['a'], type: 'user', owner_id: 'u' } as AgentGroup
const agent = { ...placeholderAgent('a', 'Searches', 'active'), name: 'web-search', provider: 'Acme', version: '1.0.0', source: 'local' as const, roomCalls: 4 }
const handlers = { pending: false, onClose: vi.fn(), onManage: vi.fn(), onDelete: vi.fn(), onSetMembers: vi.fn(), onCreateWithAgent: vi.fn() }

describe('NetworkDetailsPanel', () => {
  it('shows agent details with subnet chips and room calls', () => {
    render(<NetworkDetailsPanel selection={{ type: 'agent', agent }} networks={[research]} agents={[agent]} {...handlers} />)
    expect(screen.getByRole('heading', { name: 'web-search' })).toBeInTheDocument()
    expect(screen.getByText('v1.0.0 · Acme')).toBeInTheDocument()
    expect(screen.getByText('Online')).toBeInTheDocument()
    expect(screen.getByText('Local')).toBeInTheDocument()
    expect(screen.getByText('4 room calls')).toBeInTheDocument()
    expect(screen.getByText('Research')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open details' })).toHaveAttribute('href', '/network/agents/a')
  })

  it('shows subnet actions including scoped Connect and Delete', () => {
    render(<NetworkDetailsPanel selection={{ type: 'network', network: research }} networks={[research]} agents={[agent]} {...handlers} />)
    expect(screen.getByRole('link', { name: 'Connect with this scope' })).toHaveAttribute('href', '/connect/mcp?scope=R')
    expect(screen.getByRole('button', { name: 'Manage members' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Delete subnet' })).toBeInTheDocument()
  })
})
