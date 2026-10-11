import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { SidebarProvider } from '@/components/ui/sidebar'
import { NavAgent } from '@/components/nav-agent'
import { CONSUMER_NAV } from '@/lib/consumer-nav'

vi.mock('next/navigation', () => ({ usePathname: () => '/network' }))
vi.mock('@/hooks/use-mobile', () => ({ useIsMobile: () => false }))

afterEach(cleanup)

describe('NavAgent', () => {
  it('renders the four destinations and marks the current one', () => {
    render(<SidebarProvider><NavAgent navAgents={CONSUMER_NAV} badges={{ '/chat': 2 }} /></SidebarProvider>)
    for (const name of ['Overview', 'Network', 'Room Chat', 'Connect']) {
      expect(screen.getByRole('link', { name: new RegExp(`^${name}`) })).toBeInTheDocument()
    }
    expect(screen.getByRole('link', { name: /^Network/ })).toHaveAttribute('aria-current', 'page')
  })

  it('announces the rooms needing input', () => {
    render(<SidebarProvider><NavAgent navAgents={CONSUMER_NAV} badges={{ '/chat': 2 }} /></SidebarProvider>)
    expect(screen.getByRole('link', { name: 'Room Chat, 2 need input' })).toBeInTheDocument()
  })
})
