import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import React from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

vi.mock('next/navigation', () => ({
  usePathname: vi.fn().mockReturnValue('/c'),
}))

const mockUseHubStatus = vi.fn()
vi.mock('@/hooks/useHubStatus', () => ({
  useHubStatus: () => mockUseHubStatus(),
}))

vi.mock('@/lib/sidebar-styles', () => ({
  SIDEBAR_ICON_CENTER: 'icon-center',
  SIDEBAR_ICON_HIDDEN: 'icon-hidden',
}))

vi.mock('@/components/ui/sidebar', () => ({
  SidebarGroup: ({ children, ...props }: any) => <div data-testid="sidebar-group" {...props}>{children}</div>,
  SidebarMenu: ({ children, ...props }: any) => <div data-testid="sidebar-menu" {...props}>{children}</div>,
  SidebarMenuButton: ({ children, isActive, tooltip, ...props }: any) => (
    <div data-testid="sidebar-menu-button" data-active={isActive} data-tooltip={tooltip} {...props}>{children}</div>
  ),
  SidebarMenuItem: ({ children, ...props }: any) => <div data-testid="sidebar-menu-item" {...props}>{children}</div>,
}))

function createWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client: queryClient }, children)
  }
}

let NavHub: React.ComponentType<{ basePath?: string }>

beforeEach(async () => {
  vi.clearAllMocks()
  const mod = await import('@/components/nav-hub')
  NavHub = mod.NavHub
})

afterEach(() => {
  cleanup()
})

describe('NavHub', () => {
  it('renders "My Hub" text', () => {
    mockUseHubStatus.mockReturnValue({ hasHub: false, isOnline: false, isLoading: false })

    render(<NavHub />, { wrapper: createWrapper() })

    expect(screen.getByText('My Hub')).toBeInTheDocument()
  })

  it('uses tooltip "My Hub"', () => {
    mockUseHubStatus.mockReturnValue({ hasHub: false, isOnline: false, isLoading: false })

    render(<NavHub />, { wrapper: createWrapper() })

    expect(screen.getByTestId('sidebar-menu-button')).toHaveAttribute('data-tooltip', 'My Hub')
  })

  it('renders with dimmed icon when no hub is connected', () => {
    mockUseHubStatus.mockReturnValue({ hasHub: false, isOnline: false, isLoading: false })

    const { container } = render(<NavHub />, { wrapper: createWrapper() })

    const svg = container.querySelector('svg')
    expect(svg?.classList.contains('text-muted-foreground/50')).toBe(true)
  })

  it('does not show status dot when no hub exists', () => {
    mockUseHubStatus.mockReturnValue({ hasHub: false, isOnline: false, isLoading: false })

    const { container } = render(<NavHub />, { wrapper: createWrapper() })

    const dot = container.querySelector('.rounded-full.border')
    expect(dot).toBeNull()
  })

  it('shows green icon when hub is online', () => {
    mockUseHubStatus.mockReturnValue({ hasHub: true, isOnline: true, isLoading: false })

    const { container } = render(<NavHub />, { wrapper: createWrapper() })

    const svg = container.querySelector('svg')
    expect(svg?.classList.contains('text-emerald-500')).toBe(true)
  })

  it('shows green status dot when hub is online', () => {
    mockUseHubStatus.mockReturnValue({ hasHub: true, isOnline: true, isLoading: false })

    const { container } = render(<NavHub />, { wrapper: createWrapper() })

    const dot = container.querySelector('.bg-emerald-500.rounded-full')
    expect(dot).toBeTruthy()
  })

  it('shows amber icon when hub exists but is offline', () => {
    mockUseHubStatus.mockReturnValue({ hasHub: true, isOnline: false, isLoading: false })

    const { container } = render(<NavHub />, { wrapper: createWrapper() })

    const svg = container.querySelector('svg')
    expect(svg?.classList.contains('text-amber-500')).toBe(true)
  })

  it('shows amber status dot when hub is offline', () => {
    mockUseHubStatus.mockReturnValue({ hasHub: true, isOnline: false, isLoading: false })

    const { container } = render(<NavHub />, { wrapper: createWrapper() })

    const dot = container.querySelector('.bg-amber-500.rounded-full')
    expect(dot).toBeTruthy()
  })

  it('does not show status dot while loading', () => {
    mockUseHubStatus.mockReturnValue({ hasHub: true, isOnline: true, isLoading: true })

    const { container } = render(<NavHub />, { wrapper: createWrapper() })

    const dot = container.querySelector('.rounded-full.border')
    expect(dot).toBeNull()
  })

  it('generates correct link with basePath', () => {
    mockUseHubStatus.mockReturnValue({ hasHub: false, isOnline: false, isLoading: false })

    const { container } = render(<NavHub basePath="/c" />, { wrapper: createWrapper() })

    const link = container.querySelector('a')
    expect(link?.getAttribute('href')).toBe('/c/hub')
  })

  it('generates correct link without basePath', () => {
    mockUseHubStatus.mockReturnValue({ hasHub: false, isOnline: false, isLoading: false })

    const { container } = render(<NavHub />, { wrapper: createWrapper() })

    const link = container.querySelector('a')
    expect(link?.getAttribute('href')).toBe('/hub')
  })
})
