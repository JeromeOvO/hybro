import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '../../utils/test-utils'
import { placeholderAgent } from '@/lib/network-agent'

vi.mock('@/lib/auth', () => ({ useUser: () => ({ user: { id: 'u' }, isLoaded: true, isSignedIn: true }) }))
vi.mock('@/hooks/useMcpStatus', () => ({ useMcpStatus: () => ({ status: 'ready', refresh: vi.fn() }) }))
vi.mock('@/hooks/useRoomHistory', () => ({ useRoomHistory: () => ({ data: { items: [{ room_id: 'r1', title: 'Trip plan', last_activity_at: '2026-10-10T00:00:00Z', is_pinned: false, pin_order: null, status: 'awaiting_input' }] }, dataUpdatedAt: Date.parse('2026-10-10T10:42:00Z'), isLoading: false, isError: false }) }))
vi.mock('@/hooks/useNetworks', () => ({
  useNetworks: () => ({ networks: [], agents: [{ ...placeholderAgent('a', '', 'active'), name: 'writer', roomCalls: 2, roomCallSuccesses: 1 }], loading: false, agentsLoading: false, error: null, agentsError: null, refresh: vi.fn() }),
}))

import { OverviewPage } from '@/components/overview/overview-page'

afterEach(cleanup)

describe('OverviewPage', () => {
  it('has a hidden h1 and no visible page header', () => {
    render(<OverviewPage />)
    expect(screen.getByRole('heading', { level: 1, name: 'Overview' })).toHaveClass('sr-only')
  })

  it('shows metric tiles, a needs-input row linking to the room, and honest call labels', () => {
    render(<OverviewPage />)
    expect(screen.getAllByText('Needs input').length).toBeGreaterThan(0)
    expect(screen.getByText(/Updated /)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Trip plan needs input/ })).toHaveAttribute('href', '/room/r1')
    expect(screen.getByRole('heading', { name: 'Most-called agents' })).toBeInTheDocument()
    expect(screen.queryByText('Room calls')).toBeNull()
    expect(screen.queryByText(/success/)).toBeNull()
  })
})
