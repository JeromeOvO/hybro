import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { NavManage } from '@/components/portal/nav-manage'
import { SidebarProvider } from '@/components/ui/sidebar'

const mockPathname = vi.fn(() => '/manage/agents')

vi.mock('next/navigation', () => ({
  usePathname: () => mockPathname(),
}))

vi.mock('next/link', () => ({
  default: ({ children, href, ...props }: React.ComponentProps<'a'> & {
    prefetch?: boolean
    scroll?: boolean
  }) => {
    delete props.prefetch
    delete props.scroll
    return <a href={href} {...props}>{children}</a>
  },
}))

describe('NavManage', () => {
  beforeAll(() => {
    window.matchMedia = vi.fn().mockReturnValue({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })
  })

  beforeEach(() => {
    mockPathname.mockReturnValue('/manage/agents')
  })

  afterEach(cleanup)

  it('uses a non-link Manage trigger and exposes the canonical secondary links', () => {
    render(
      <SidebarProvider>
        <NavManage />
      </SidebarProvider>
    )

    const manage = screen.getByRole('button', { name: /Manage/i })
    expect(manage.closest('a')).toBeNull()
    expect(manage).toHaveAttribute('aria-label', 'Manage')

    expect(screen.getByRole('link', { name: /My Agents/i })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: /My Agents/i })).toHaveAttribute('href', '/manage/agents')
    expect(screen.getByRole('link', { name: /My Hub/i })).toHaveAttribute('href', '/hub')
    expect(screen.getByRole('link', { name: /My Hub/i })).not.toHaveAttribute('aria-current')
    expect(screen.getByRole('link', { name: /Register Agent/i })).toHaveAttribute('href', '/manage/agents/new')
    expect(screen.getByRole('link', { name: /API Keys/i })).toHaveAttribute('href', '/manage/api-keys')
    expect(screen.getByRole('link', { name: /Inspector/i })).toHaveAttribute('href', '/manage/inspector')
  })

  it('opens when client navigation enters the management area', () => {
    mockPathname.mockReturnValue('/chat')
    const view = render(
      <SidebarProvider>
        <NavManage />
      </SidebarProvider>
    )
    expect(screen.getByRole('button', { name: /Manage/i })).toHaveAttribute('aria-expanded', 'false')

    mockPathname.mockReturnValue('/hub')
    view.rerender(
      <SidebarProvider>
        <NavManage />
      </SidebarProvider>
    )

    expect(screen.getByRole('button', { name: /Manage/i })).toHaveAttribute('aria-expanded', 'true')
  })

  it('keeps an accessible Manage name when the sidebar is collapsed', () => {
    render(
      <SidebarProvider defaultOpen={false}>
        <NavManage />
      </SidebarProvider>
    )

    expect(screen.getByRole('button', { name: 'Manage' })).toHaveAttribute(
      'aria-label',
      'Manage'
    )
  })
})
