import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { RoomSettingForm } from '@/components/room-setting-form'

vi.mock('@/components/agent-selector', () => ({
  AgentSelector: () => <div data-testid="agent-selector" />,
}))

describe('RoomSettingForm execution-mode cleanup', () => {
  it('contains no room-scoped Supervisor or Debate control', () => {
    render(<RoomSettingForm onSubmit={vi.fn()} />)

    expect(screen.queryByText('Supervisor Mode')).not.toBeInTheDocument()
    expect(screen.queryByText('Debate Mode')).not.toBeInTheDocument()
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
  })
})
