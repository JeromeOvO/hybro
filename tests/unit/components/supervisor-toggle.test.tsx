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

describe('RoomSettingForm — Supervisor Toggle migration', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    cleanup()
  })

  describe('supervisor UI removed from form', () => {
    it('should NOT render Supervisor Mode toggle (migrated to chat input)', () => {
      render(<RoomSettingForm onSubmit={vi.fn()} />)

      expect(screen.queryByText('Supervisor Mode')).not.toBeInTheDocument()
    })

    it('should still render Debate Mode toggle', () => {
      render(<RoomSettingForm onSubmit={vi.fn()} />)

      expect(screen.getByText('Debate Mode')).toBeInTheDocument()
    })

    it('should only have one switch (Debate Mode)', () => {
      render(<RoomSettingForm onSubmit={vi.fn()} />)

      const switches = screen.getAllByRole('switch')
      expect(switches).toHaveLength(1)
    })
  })

  describe('form submission excludes supervisor', () => {
    it('should submit with only debateMode (no useSupervisor)', async () => {
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
          { debateMode: false },
        )
      })
    })

    it('should not include useSupervisor even when initialData has it', async () => {
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
          }}
        />
      )

      await user.click(screen.getByRole('button', { name: /Create Room/ }))

      await waitFor(() => {
        const opts = onSubmit.mock.calls[0][2]
        expect(opts).toEqual({ debateMode: false })
        expect(opts).not.toHaveProperty('useSupervisor')
      })
    })

    it('should submit debateMode from initialData without supervisor', async () => {
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
          }}
        />
      )

      await user.click(screen.getByRole('button', { name: /Create Room/ }))

      await waitFor(() => {
        expect(onSubmit).toHaveBeenCalledWith(
          'Full Config Room',
          expect.any(Object),
          { debateMode: true },
        )
      })
    })

    it('should toggle debateMode correctly', async () => {
      const onSubmit = vi.fn()
      const user = userEvent.setup()

      render(
        <RoomSettingForm
          onSubmit={onSubmit}
          requireRoomName={false}
        />
      )

      const switches = screen.getAllByRole('switch')
      await user.click(switches[0])
      await user.click(screen.getByRole('button', { name: /Create Room/ }))

      await waitFor(() => {
        expect(onSubmit).toHaveBeenCalledWith(
          '',
          expect.any(Object),
          { debateMode: true },
        )
      })
    })
  })
})
