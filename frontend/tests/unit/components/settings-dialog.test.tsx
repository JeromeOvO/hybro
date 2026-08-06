import { cleanup, render, screen } from '@testing-library/react'
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mockUseUser = vi.fn()
vi.mock('@/lib/auth', () => ({
  useUser: () => mockUseUser(),
  useAuth: () => ({ getToken: async () => 'test-token' }),
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

let SettingsDialog: React.ComponentType<{
  open: boolean
  onOpenChange: (open: boolean) => void
}>

beforeEach(async () => {
  vi.clearAllMocks()
  mockUseUser.mockReturnValue({ isLoaded: true, user: { id: 'u1' } })
  const mod = await import('@/components/settings/settings-dialog')
  SettingsDialog = mod.SettingsDialog
})

afterEach(cleanup)

describe('SettingsDialog', () => {
  it('shows account settings without a My Hub entry', () => {
    render(<SettingsDialog open={true} onOpenChange={vi.fn()} />)

    expect(screen.getByTestId('profile-section')).toBeInTheDocument()
    expect(screen.getByTestId('password-section')).toBeInTheDocument()
    expect(screen.getByTestId('sessions-section')).toBeInTheDocument()
    expect(screen.getByTestId('danger-zone-section')).toBeInTheDocument()
    expect(screen.queryByText('My Hub')).not.toBeInTheDocument()
  })

  it('shows loading state when user is not loaded', () => {
    mockUseUser.mockReturnValue({ isLoaded: false, user: null })

    render(<SettingsDialog open={true} onOpenChange={vi.fn()} />)

    expect(screen.getByText('Loading settings...')).toBeInTheDocument()
  })
})
