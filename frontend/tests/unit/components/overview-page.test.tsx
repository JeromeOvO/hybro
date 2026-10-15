import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '../../utils/test-utils'
import { placeholderAgent } from '@/lib/network-agent'

vi.mock('@/lib/auth', () => ({ useUser: () => ({ user: { id: 'u' }, isLoaded: true, isSignedIn: true }) }))
vi.mock('@/hooks/useMcpStatus', () => ({ useMcpStatus: () => ({ status: 'ready', refresh: vi.fn() }) }))
const history = vi.hoisted(() => ({ isError: false, refetch: vi.fn() }))
const refreshNetworks = vi.hoisted(() => vi.fn())
vi.mock('@/hooks/useRoomHistory', () => ({ useRoomHistory: () => ({ refetch: history.refetch, data: { items: [{ room_id: 'r1', title: 'Trip plan', last_activity_at: '2026-10-10T00:00:00Z', is_pinned: false, pin_order: null, status: 'awaiting_input' }] }, dataUpdatedAt: Date.parse('2026-10-10T10:42:00Z'), isLoading: false, isError: history.isError }) }))
vi.mock('@/hooks/useSessions', () => ({ useSessions: () => ({ data: { sessions: [{ session_id: 's1', client_label: 'Buyer agent', agents: [], state: 'waiting_on_owner', paused_agent_id: 'a', conversation_id: 'c1', opened_at: '2026-10-10T00:00:00Z', last_activity_at: '2026-10-10T00:00:00Z', closed_at: null }] }, isLoading: false, isError: false, refetch: vi.fn() }) }))
vi.mock('@/hooks/useNetworks', () => ({
  useNetworks: () => ({ networks: [], agents: [{ ...placeholderAgent('a', '', 'active'), name: 'writer', roomCalls: 2, roomCallSuccesses: 1 }], loading: false, agentsLoading: false, error: null, agentsError: null, refresh: refreshNetworks }),
}))

import { OverviewPage } from '@/components/overview/overview-page'

afterEach(() => {
  cleanup()
  history.isError = false
})

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

  it('shows a Sessions tile and an approval row linking to the session', () => {
    render(<OverviewPage />)
    expect(screen.getByText('Sessions')).toBeInTheDocument()
    expect(screen.getByText(/1 waiting for you/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Buyer agent is waiting for your approval/ })).toHaveAttribute('href', '/sessions/s1')
  })

  it('retries room history when rooms fail to load', () => {
    history.isError = true
    render(<OverviewPage />)
    expect(screen.getByText("Rooms couldn't load.")).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(history.refetch).toHaveBeenCalledTimes(1)
    expect(refreshNetworks).toHaveBeenCalledTimes(1)
  })
})
