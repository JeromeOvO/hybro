import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import React from 'react'
import { RoomSettingForm } from '@/components/room-setting-form'
import type { Agent } from '@/lib/types/agent'

class MockResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = MockResizeObserver as unknown as typeof ResizeObserver

vi.mock('@/components/agent-selector', () => ({
  AgentSelector: ({ selectedAgents }: { selectedAgents: Record<string, Agent> }) => (
    <div data-testid="agent-selector">
      {Object.values(selectedAgents).map(a => (
        <span key={a.agent_id} data-testid={`selected-${a.agent_id}`}>
          {a.agent_card.name}
        </span>
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
]

describe('RoomSettingForm — Supervisor Toggle', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    cleanup()
  })

  describe('rendering', () => {
    it('should render Supervisor Mode toggle', () => {
      render(<RoomSettingForm onSubmit={vi.fn()} />)

      expect(screen.getByText('Supervisor Mode')).toBeInTheDocument()
    })

    it('should render Supervisor Mode description text', () => {
      render(<RoomSettingForm onSubmit={vi.fn()} />)

      expect(
        screen.getByText(/enable ai supervisor to coordinate agents/i)
      ).toBeInTheDocument()
    })

    it('should render both Supervisor and Debate Mode toggles independently', () => {
      render(<RoomSettingForm onSubmit={vi.fn()} />)

      expect(screen.getByText('Supervisor Mode')).toBeInTheDocument()
      expect(screen.getByText('Debate Mode')).toBeInTheDocument()

      const switches = screen.getAllByRole('switch')
      expect(switches.length).toBeGreaterThanOrEqual(2)
    })

    it('should render Supervisor Mode before Debate Mode in DOM order', () => {
      render(<RoomSettingForm onSubmit={vi.fn()} />)

      const supervisorLabel = screen.getByText('Supervisor Mode')
      const debateLabel = screen.getByText('Debate Mode')

      // Supervisor should appear before Debate in the document
      const allElements = document.querySelectorAll('.text-base')
      const labels = Array.from(allElements).map(el => el.textContent)
      const supervisorIdx = labels.findIndex(t => t?.includes('Supervisor Mode'))
      const debateIdx = labels.findIndex(t => t?.includes('Debate Mode'))

      expect(supervisorIdx).toBeLessThan(debateIdx)
    })
  })

  describe('form submission', () => {
    it('should submit with useSupervisor=false by default', async () => {
      const onSubmit = vi.fn()
      const user = userEvent.setup()

      render(
        <RoomSettingForm
          onSubmit={onSubmit}
          requireRoomName={false}
        />
      )

      await user.click(screen.getByRole('button', { name: /Create Room/ }))

      await waitFor(() => {
        expect(onSubmit).toHaveBeenCalledWith(
          '',
          expect.any(Object),
          { debateMode: false, useSupervisor: false },
        )
      })
    })

    it('should submit with useSupervisor=true when toggled on', async () => {
      const onSubmit = vi.fn()
      const user = userEvent.setup()

      render(
        <RoomSettingForm
          onSubmit={onSubmit}
          requireRoomName={false}
        />
      )

      // Find the Supervisor Mode switch specifically
      const switches = screen.getAllByRole('switch')
      // Supervisor Mode toggle is the first switch (rendered before Debate Mode)
      const supervisorSwitch = switches[0]
      await user.click(supervisorSwitch)

      await user.click(screen.getByRole('button', { name: /Create Room/ }))

      await waitFor(() => {
        expect(onSubmit).toHaveBeenCalledWith(
          '',
          expect.any(Object),
          { debateMode: false, useSupervisor: true },
        )
      })
    })

    it('should submit with both Supervisor and Debate toggled on', async () => {
      const onSubmit = vi.fn()
      const user = userEvent.setup()

      render(
        <RoomSettingForm
          onSubmit={onSubmit}
          requireRoomName={false}
        />
      )

      const switches = screen.getAllByRole('switch')
      // Toggle both switches on
      for (const sw of switches) {
        await user.click(sw)
      }

      await user.click(screen.getByRole('button', { name: /Create Room/ }))

      await waitFor(() => {
        expect(onSubmit).toHaveBeenCalledWith(
          '',
          expect.any(Object),
          { debateMode: true, useSupervisor: true },
        )
      })
    })

    it('should include useSupervisor alongside room name and agents', async () => {
      const onSubmit = vi.fn()
      const user = userEvent.setup()

      render(
        <RoomSettingForm
          onSubmit={onSubmit}
          requireRoomName
          availableAgents={mockAgents}
        />
      )

      await user.type(screen.getByLabelText(/Room Name/), 'My Test Room')

      const switches = screen.getAllByRole('switch')
      await user.click(switches[0]) // Supervisor on

      await user.click(screen.getByRole('button', { name: /Create Room/ }))

      await waitFor(() => {
        expect(onSubmit).toHaveBeenCalledWith(
          'My Test Room',
          expect.any(Object),
          { debateMode: false, useSupervisor: true },
        )
      })
    })
  })

  describe('initialData', () => {
    it('should initialize useSupervisor=true from initialData', async () => {
      const onSubmit = vi.fn()
      const user = userEvent.setup()

      render(
        <RoomSettingForm
          onSubmit={onSubmit}
          requireRoomName={false}
          availableAgents={mockAgents}
          initialData={{
            roomName: 'Existing Room',
            selectedAgents: { 'agent-1': 'Research Bot' },
            debateMode: false,
            useSupervisor: true,
          }}
        />
      )

      // Submit without changing anything to verify initial state propagates
      await user.click(screen.getByRole('button', { name: /Create Room/ }))

      await waitFor(() => {
        expect(onSubmit).toHaveBeenCalledWith(
          'Existing Room',
          expect.any(Object),
          { debateMode: false, useSupervisor: true },
        )
      })
    })

    it('should default useSupervisor to false when initialData has no useSupervisor', async () => {
      const onSubmit = vi.fn()
      const user = userEvent.setup()

      render(
        <RoomSettingForm
          onSubmit={onSubmit}
          requireRoomName={false}
          availableAgents={mockAgents}
          initialData={{
            roomName: 'Old Room',
            selectedAgents: {},
            debateMode: true,
          }}
        />
      )

      await user.click(screen.getByRole('button', { name: /Create Room/ }))

      await waitFor(() => {
        expect(onSubmit).toHaveBeenCalledWith(
          'Old Room',
          expect.any(Object),
          { debateMode: true, useSupervisor: false },
        )
      })
    })

    it('should initialize both supervisor and debate from initialData', async () => {
      const onSubmit = vi.fn()
      const user = userEvent.setup()

      render(
        <RoomSettingForm
          onSubmit={onSubmit}
          requireRoomName={false}
          availableAgents={mockAgents}
          initialData={{
            roomName: 'Full Config Room',
            selectedAgents: { 'agent-1': 'Research Bot' },
            debateMode: true,
            useSupervisor: true,
          }}
        />
      )

      await user.click(screen.getByRole('button', { name: /Create Room/ }))

      await waitFor(() => {
        expect(onSubmit).toHaveBeenCalledWith(
          'Full Config Room',
          expect.any(Object),
          { debateMode: true, useSupervisor: true },
        )
      })
    })
  })
})
