import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import React from 'react'

const mockUseUser = vi.fn()
vi.mock('@clerk/nextjs', () => ({
  useUser: () => mockUseUser(),
}))

vi.mock('@/hooks/useMyAgents', () => ({
  useMyAgents: () => ({ agents: [], isLoading: false }),
}))

vi.mock('@/components/nav-agent', () => ({
  NavAgent: () => <div data-testid="nav-agent" />,
}))

vi.mock('@/components/nav-hub', () => ({
  NavHub: () => <div data-testid="nav-hub" />,
}))

vi.mock('@/components/nav-main', () => ({
  NavMain: () => <div data-testid="nav-main" />,
}))

vi.mock('@/components/nav-user', () => ({
  NavUser: () => <div data-testid="nav-user" />,
}))

vi.mock('@/components/logo', () => ({
  Logo: () => <div data-testid="logo" />,
}))

vi.mock('@/components/nav-discord-button', () => ({
  DiscordButton: () => <div data-testid="discord-button" />,
}))

vi.mock('@/components/nav-docs-button', () => ({
  DocsButton: () => <div data-testid="docs-button" />,
}))

vi.mock('next/link', () => ({
  default: ({
    children,
    href,
    prefetch: _prefetch,
    ...props
  }: React.AnchorHTMLAttributes<HTMLAnchorElement> & { href: string; prefetch?: boolean }) => (
    <a href={href} {...props}>{children}</a>
  ),
}))

// eslint-disable-next-line @next/next/no-img-element
vi.mock('next/image', () => ({
  default: (props: React.ImgHTMLAttributes<HTMLImageElement>) => <img alt="" {...props} />,
}))


vi.mock('@/components/ui/sidebar', () => ({
  Sidebar: ({ children, ...props }: React.HTMLAttributes<HTMLElement>) => <aside {...props}>{children}</aside>,
  SidebarContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SidebarFooter: ({ children }: { children: React.ReactNode }) => <footer>{children}</footer>,
  SidebarHeader: ({ children }: { children: React.ReactNode }) => <header>{children}</header>,
  SidebarMenu: ({ children }: { children: React.ReactNode }) => <nav>{children}</nav>,
  SidebarMenuItem: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SidebarMenuButton: ({ children, tooltip }: { children: React.ReactNode; tooltip?: string }) => (
    <div aria-label={tooltip}>{children}</div>
  ),
  useSidebar: () => ({ state: 'expanded', toggleSidebar: vi.fn() }),
}))

let DeveloperSidebar: React.ComponentType

beforeEach(async () => {
  vi.clearAllMocks()
  mockUseUser.mockReturnValue({ isLoaded: true, isSignedIn: true })
  const mod = await import('@/components/developer/developer-sidebar')
  DeveloperSidebar = mod.DeveloperSidebar
})

afterEach(() => {
  cleanup()
})

describe('DeveloperSidebar', () => {
  it('links to the user portal chat', () => {
    render(<DeveloperSidebar />)

    const link = screen.getByRole('link', { name: /User Portal/i })
    expect(link).toHaveAttribute('href', 'http://localhost:3000/chat')
    expect(screen.getByLabelText('User Portal')).toBeInTheDocument()
  })
})
