import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import React, { createRef } from 'react'
import { RoomSettingForm, type RoomSettingFormHandle } from '@/components/room-setting-form'
import type { Agent } from '@/lib/types/agent'

// Radix Switch uses ResizeObserver which jsdom doesn't provide
class MockResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = MockResizeObserver as unknown as typeof ResizeObserver

vi.mock('@/components/agent-selector', () => ({
  AgentSelector: ({ selectedAgents, onAgentAdd, availableAgents, loading, error }: {
    selectedAgents: Record<string, Agent>
    onAgentAdd: (agent: Agent) => void
    availableAgents: Agent[]
    loading: boolean
    error: string | null
  }) => (
    <div data-testid="agent-selector">
      <span>Agent Invitation</span>
      {loading && <span>Loading agents...</span>}
      {error && <span>{error}</span>}
      {availableAgents
        .filter(a => !selectedAgents[a.agent_id])
        .map(a => (
          <button key={a.agent_id} data-testid={`add-${a.agent_id}`} onClick={() => onAgentAdd(a)}>
            Add {a.agent_card.name}
          </button>
        ))}
      {Object.values(selectedAgents).map(a => (
        <span key={a.agent_id} data-testid={`selected-${a.agent_id}`}>{a.agent_card.name}</span>
      ))}
    </div>
  ),
}))

const mockAgents: Agent[] = [
  {
    agent_id: 'agent-1',
    agent_card: { name: 'Research Bot' } as Agent['agent_card'],
    agent_status: 'active' as Agent['agent_status'],
  },
  {
    agent_id: 'agent-2',
    agent_card: { name: 'Code Bot' } as Agent['agent_card'],
    agent_status: 'active' as Agent['agent_status'],
  },
]

const defaultProps = {
  onSubmit: vi.fn(),
}

describe('RoomSettingForm', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    cleanup()
  })

  describe('rendering', () => {
    it('should render room name input and submit button', () => {
      render(<RoomSettingForm {...defaultProps} />)

      expect(screen.getByLabelText(/Room Name/)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /Create Room/ })).toBeInTheDocument()
    })

    it('should render debate mode switch', () => {
      render(<RoomSettingForm {...defaultProps} />)

      expect(screen.getByText('Debate Mode')).toBeInTheDocument()
      expect(screen.getByRole('switch')).toBeInTheDocument()
    })

    it('should show "Update Room" when isEditing is true', () => {
      render(<RoomSettingForm {...defaultProps} isEditing />)

      expect(screen.getByRole('button', { name: /Update Room/ })).toBeInTheDocument()
    })

    it('should show custom submit button text', () => {
      render(<RoomSettingForm {...defaultProps} submitButtonText="Save Settings" />)

      expect(screen.getByRole('button', { name: /Save Settings/ })).toBeInTheDocument()
    })

    it('should show optional label when requireRoomName is false', () => {
      render(<RoomSettingForm {...defaultProps} requireRoomName={false} />)

      expect(screen.getByText('(optional)')).toBeInTheDocument()
    })

    it('should disable submit button when isSubmitting', () => {
      render(<RoomSettingForm {...defaultProps} isSubmitting />)

      expect(screen.getByRole('button', { name: /Creating Room/ })).toBeDisabled()
    })

    it('should show "Updating Room..." when isEditing and isSubmitting', () => {
      render(<RoomSettingForm {...defaultProps} isEditing isSubmitting />)

      expect(screen.getByRole('button', { name: /Updating Room/ })).toBeDisabled()
    })
  })

  describe('form submission', () => {
    it('should call onSubmit with form values', async () => {
      const onSubmit = vi.fn()
      const user = userEvent.setup()

      render(
        <RoomSettingForm
          onSubmit={onSubmit}
          requireRoomName={false}
          availableAgents={mockAgents}
        />
      )

      await user.click(screen.getByRole('button', { name: /Create Room/ }))

      await waitFor(() => {
        expect(onSubmit).toHaveBeenCalledWith('', expect.any(Object), false)
      })
    })

    it('should validate room name is required when requireRoomName=true', async () => {
      const onSubmit = vi.fn()
      const user = userEvent.setup()

      render(<RoomSettingForm onSubmit={onSubmit} requireRoomName />)

      await user.click(screen.getByRole('button', { name: /Create Room/ }))

      await waitFor(() => {
        expect(screen.getByText(/Room name must be at least 2 characters/)).toBeInTheDocument()
      })

      expect(onSubmit).not.toHaveBeenCalled()
    })

    it('should submit with typed room name', async () => {
      const onSubmit = vi.fn()
      const user = userEvent.setup()

      render(<RoomSettingForm onSubmit={onSubmit} requireRoomName />)

      await user.type(screen.getByLabelText(/Room Name/), 'My Test Room')
      await user.click(screen.getByRole('button', { name: /Create Room/ }))

      await waitFor(() => {
        expect(onSubmit).toHaveBeenCalledWith('My Test Room', expect.any(Object), false)
      })
    })

    it('should submit with debate mode toggled on', async () => {
      const onSubmit = vi.fn()
      const user = userEvent.setup()

      render(
        <RoomSettingForm
          onSubmit={onSubmit}
          requireRoomName={false}
        />
      )

      await user.click(screen.getByRole('switch'))
      await user.click(screen.getByRole('button', { name: /Create Room/ }))

      await waitFor(() => {
        expect(onSubmit).toHaveBeenCalledWith('', expect.any(Object), true)
      })
    })
  })

  describe('initialData', () => {
    it('should populate form fields from initialData', async () => {
      render(
        <RoomSettingForm
          {...defaultProps}
          availableAgents={mockAgents}
          initialData={{
            roomName: 'Existing Room',
            selectedAgents: { 'agent-1': 'Research Bot' },
            debateMode: true,
          }}
        />
      )

      await waitFor(() => {
        expect(screen.getByDisplayValue('Existing Room')).toBeInTheDocument()
      })
    })
  })

  describe('imperative handle', () => {
    it('should reset form via ref.reset()', async () => {
      const ref = createRef<RoomSettingFormHandle>()
      const user = userEvent.setup()

      render(
        <RoomSettingForm
          ref={ref}
          {...defaultProps}
          requireRoomName={false}
        />
      )

      await user.type(screen.getByLabelText(/Room Name/), 'Temp Room')
      expect(screen.getByDisplayValue('Temp Room')).toBeInTheDocument()

      // eslint-disable-next-line testing-library/no-unnecessary-act
      await waitFor(() => {
        ref.current?.reset()
      })

      await waitFor(() => {
        expect(screen.getByLabelText(/Room Name/)).toHaveValue('')
      })
    })
  })

  describe('agent selection', () => {
    it('should render AgentSelector with availableAgents', () => {
      render(
        <RoomSettingForm
          {...defaultProps}
          availableAgents={mockAgents}
        />
      )

      expect(screen.getByText('Agent Invitation')).toBeInTheDocument()
      expect(screen.getByTestId('add-agent-1')).toBeInTheDocument()
      expect(screen.getByTestId('add-agent-2')).toBeInTheDocument()
    })

    it('should add agent when clicking add button', async () => {
      const user = userEvent.setup()

      render(
        <RoomSettingForm
          {...defaultProps}
          availableAgents={mockAgents}
          requireRoomName={false}
        />
      )

      await user.click(screen.getByTestId('add-agent-1'))

      expect(screen.getByTestId('selected-agent-1')).toBeInTheDocument()
    })

    it('should include selected agents in onSubmit', async () => {
      const onSubmit = vi.fn()
      const user = userEvent.setup()

      render(
        <RoomSettingForm
          onSubmit={onSubmit}
          availableAgents={mockAgents}
          requireRoomName={false}
        />
      )

      await user.click(screen.getByTestId('add-agent-1'))
      await user.click(screen.getByRole('button', { name: /Create Room/ }))

      await waitFor(() => {
        expect(onSubmit).toHaveBeenCalledWith(
          '',
          expect.objectContaining({ 'agent-1': mockAgents[0] }),
          false
        )
      })
    })

    it('should show loading state in agent selector', () => {
      render(
        <RoomSettingForm
          {...defaultProps}
          loadingAgents
        />
      )

      expect(screen.getByText('Loading agents...')).toBeInTheDocument()
    })

    it('should show error in agent selector', () => {
      render(
        <RoomSettingForm
          {...defaultProps}
          agentsError="Failed to load"
        />
      )

      expect(screen.getByText(/Failed to load/)).toBeInTheDocument()
    })
  })
})
