import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import React from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const mockUseUser = vi.fn()
vi.mock('@clerk/nextjs', () => ({
  useUser: () => mockUseUser(),
  useAuth: () => ({ getToken: async () => 'test-token' }),
}))

const mockUseHubStatus = vi.fn()
vi.mock('@/hooks/useHubStatus', () => ({
  useHubStatus: () => mockUseHubStatus(),
}))

vi.mock('@/components/settings/profile-section', () => ({
  ProfileSection: () => <div data-testid="profile-section">Profile</div>,
}))

vi.mock('@/components/settings/password-section', () => ({
  PasswordSection: () => <div data-testid="password-section">Password</div>,
}))

vi.mock('@/components/settings/sessions-section', () => ({
  SessionsSection: () => <div data-testid="sessions-section">Sessions</div>,
}))

vi.mock('@/components/settings/danger-zone-section', () => ({
  DangerZoneSection: () => <div data-testid="danger-zone-section">Danger Zone</div>,
}))

vi.mock('next/link', () => ({
  default: ({ children, href, onClick, ...props }: any) => (
    <a href={href} onClick={onClick} {...props}>{children}</a>
  ),
}))

const mockPathname = vi.fn(() => '/c')
vi.mock('next/navigation', () => ({
  usePathname: () => mockPathname(),
}))

function createWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

let SettingsDialog: React.ComponentType<{ open: boolean; onOpenChange: (open: boolean) => void }>

beforeEach(async () => {
  vi.clearAllMocks()
  mockUseUser.mockReturnValue({ isLoaded: true, user: { id: 'u1' } })
  const mod = await import('@/components/settings/settings-dialog')
  SettingsDialog = mod.SettingsDialog
})

afterEach(() => {
  cleanup()
})

describe('SettingsDialog - HubStatusLine', () => {
  it('shows "Connected" when hub is online', async () => {
    mockUseHubStatus.mockReturnValue({ hasHub: true, isOnline: true, isLoading: false })

    render(<SettingsDialog open={true} onOpenChange={vi.fn()} />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('Connected')).toBeInTheDocument()
    })
  })

  it('shows "Offline" when hub exists but is offline', async () => {
    mockUseHubStatus.mockReturnValue({ hasHub: true, isOnline: false, isLoading: false })

    render(<SettingsDialog open={true} onOpenChange={vi.fn()} />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('Offline')).toBeInTheDocument()
    })
  })

  it('shows "Not set up" when no hub exists', async () => {
    mockUseHubStatus.mockReturnValue({ hasHub: false, isOnline: false, isLoading: false })

    render(<SettingsDialog open={true} onOpenChange={vi.fn()} />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('Not set up')).toBeInTheDocument()
    })
  })

  it('shows "Checking..." when loading', async () => {
    mockUseHubStatus.mockReturnValue({ hasHub: false, isOnline: false, isLoading: true })

    render(<SettingsDialog open={true} onOpenChange={vi.fn()} />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('Checking...')).toBeInTheDocument()
    })
  })

  it('includes a link to /c/hub when on consumer portal', async () => {
    mockPathname.mockReturnValue('/c/settings')
    mockUseHubStatus.mockReturnValue({ hasHub: true, isOnline: true, isLoading: false })

    render(<SettingsDialog open={true} onOpenChange={vi.fn()} />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('Connected')).toBeInTheDocument()
    })
    const link = screen.getByText('Connected').closest('a')
    expect(link?.getAttribute('href')).toBe('/c/hub')
  })

  it('includes a link to /d/hub when on developer portal', async () => {
    mockPathname.mockReturnValue('/d/agents')
    mockUseHubStatus.mockReturnValue({ hasHub: true, isOnline: true, isLoading: false })

    render(<SettingsDialog open={true} onOpenChange={vi.fn()} />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('Connected')).toBeInTheDocument()
    })
    const link = screen.getByText('Connected').closest('a')
    expect(link?.getAttribute('href')).toBe('/d/hub')
  })

  it('calls onOpenChange(false) when hub link is clicked', async () => {
    mockUseHubStatus.mockReturnValue({ hasHub: true, isOnline: true, isLoading: false })
    const mockOnOpenChange = vi.fn()

    render(<SettingsDialog open={true} onOpenChange={mockOnOpenChange} />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('Connected')).toBeInTheDocument()
    })

    const link = screen.getByText('Connected').closest('a')!
    await userEvent.click(link)

    expect(mockOnOpenChange).toHaveBeenCalledWith(false)
  })

  it('shows "My Hub" section title', async () => {
    mockUseHubStatus.mockReturnValue({ hasHub: false, isOnline: false, isLoading: false })

    render(<SettingsDialog open={true} onOpenChange={vi.fn()} />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(screen.getByText('My Hub')).toBeInTheDocument()
    })
  })

  it('shows loading state when user is not loaded', () => {
    mockUseUser.mockReturnValue({ isLoaded: false, user: null })

    render(<SettingsDialog open={true} onOpenChange={vi.fn()} />, { wrapper: createWrapper() })

    expect(screen.getByText('Loading settings...')).toBeInTheDocument()
  })
})
