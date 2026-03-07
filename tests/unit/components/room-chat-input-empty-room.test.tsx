import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { RoomChatInput } from '@/components/room-chat-input'

vi.mock('@/components/group-selector', () => ({
  GroupSelector: ({ selectedGroup }: { selectedGroup: string }) => (
    <div data-testid="group-selector" data-selected={selectedGroup} />
  ),
}))

if (!globalThis.URL.createObjectURL) {
  globalThis.URL.createObjectURL = vi.fn(() => 'blob:mock')
}
if (!globalThis.URL.revokeObjectURL) {
  globalThis.URL.revokeObjectURL = vi.fn()
}

const defaultProps = {
  onSubmit: vi.fn(),
  agents: [
    { id: 'a-1', name: 'Alpha Agent' },
    { id: 'a-2', name: 'Beta Agent' },
  ],
}

function renderInput(overrides: Record<string, unknown> = {}) {
  return render(<RoomChatInput {...defaultProps} {...overrides} />)
}

describe('RoomChatInput – group selector integration', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    cleanup()
  })

  it('defaults to all_agents when no selectedGroup provided', () => {
    renderInput()

    const selector = screen.getAllByTestId('group-selector')[0]
    expect(selector).toHaveAttribute('data-selected', 'all_agents')
  })

  it('GroupSelector receives the provided selectedGroup', () => {
    renderInput({ selectedGroup: 'room_team' })

    const selector = screen.getAllByTestId('group-selector')[0]
    expect(selector).toHaveAttribute('data-selected', 'room_team')
  })

  it('GroupSelector receives all_agents when explicitly passed', () => {
    renderInput({ selectedGroup: 'all_agents' })

    const selector = screen.getAllByTestId('group-selector')[0]
    expect(selector).toHaveAttribute('data-selected', 'all_agents')
  })
})
