import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import React, { createRef } from 'react'
import { RoomSettingForm, type RoomSettingFormHandle } from '@/components/room-setting-form'
import type { Agent } from '@/lib/types/agent'

class MockResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = MockResizeObserver as unknown as typeof ResizeObserver

vi.mock('@/components/agent-selector', () => ({
  AgentSelector: ({ selectedAgents, onAgentAdd, onAgentRemove, availableAgents }: {
    selectedAgents: Record<string, Agent>
    onAgentAdd: (agent: Agent) => void
    onAgentRemove: (agentId: string) => void
    availableAgents: Agent[]
  }) => (
    <div data-testid="agent-selector">
      {availableAgents
        .filter(a => !selectedAgents[a.agent_id])
        .map(a => (
          <button key={a.agent_id} data-testid={`add-${a.agent_id}`} onClick={() => onAgentAdd(a)}>
            Add {a.agent_card.name}
          </button>
        ))}
      {Object.values(selectedAgents).map(a => (
        <span key={a.agent_id} data-testid={`selected-${a.agent_id}`}>
          {a.agent_card.name}
          <button data-testid={`remove-${a.agent_id}`} onClick={() => onAgentRemove(a.agent_id)}>
            Remove
          </button>
        </span>
      ))}
    </div>
  ),
}))

const activeAgent1: Agent = {
  agent_id: 'agent-active-1',
  agent_card: { name: 'Active Bot' } as Agent['agent_card'],
  agent_status: 'active' as Agent['agent_status'],
}

const activeAgent2: Agent = {
  agent_id: 'agent-active-2',
  agent_card: { name: 'Helper Bot' } as Agent['agent_card'],
  agent_status: 'active' as Agent['agent_status'],
}

describe('RoomSettingForm – stale member preservation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    cleanup()
  })

  it('preserves stale agent IDs in submitted membershipAgentIds', async () => {
    const onSubmit = vi.fn()
    const user = userEvent.setup()

    render(
      <RoomSettingForm
        onSubmit={onSubmit}
        availableAgents={[activeAgent1]}
        requireRoomName={false}
        isEditing
        initialData={{
          roomName: 'Test Room',
          selectedAgents: {
            'agent-active-1': 'Active Bot',
            'agent-stale-xyz': 'Stale Bot',
          },
        }}
      />
    )

    await waitFor(() => {
      expect(screen.getByDisplayValue('Test Room')).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: /Update Room/ }))

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalled()
    })

    const [, membershipAgentIds] = onSubmit.mock.calls[0]
    expect(membershipAgentIds).toContain('agent-active-1')
    expect(membershipAgentIds).toContain('agent-stale-xyz')
  })

  it('staleAgentRefs are populated when initialData has unknown agents', async () => {
    render(
      <RoomSettingForm
        onSubmit={vi.fn()}
        availableAgents={[activeAgent1, activeAgent2]}
        requireRoomName={false}
        isEditing
        initialData={{
          roomName: 'Mixed Room',
          selectedAgents: {
            'agent-active-1': 'Active Bot',
            'agent-active-2': 'Helper Bot',
            'agent-deleted-1': 'Deleted Bot',
            'agent-private-2': 'Private Bot',
          },
        }}
      />
    )

    await waitFor(() => {
      expect(screen.getByDisplayValue('Mixed Room')).toBeInTheDocument()
    })

    expect(screen.getByTestId('selected-agent-active-1')).toBeInTheDocument()
    expect(screen.getByTestId('selected-agent-active-2')).toBeInTheDocument()

    expect(screen.getByText(/Unavailable members/i)).toBeInTheDocument()
  })

  it('rename-only save still includes stale member IDs', async () => {
    const onSubmit = vi.fn()
    const user = userEvent.setup()

    render(
      <RoomSettingForm
        onSubmit={onSubmit}
        availableAgents={[activeAgent1]}
        requireRoomName
        isEditing
        initialData={{
          roomName: 'Original Name',
          selectedAgents: {
            'agent-active-1': 'Active Bot',
            'agent-stale-abc': 'Stale Member',
          },
        }}
      />
    )

    await waitFor(() => {
      expect(screen.getByDisplayValue('Original Name')).toBeInTheDocument()
    })

    const input = screen.getByLabelText(/Room Name/)
    await user.clear(input)
    await user.type(input, 'Renamed Room')

    await user.click(screen.getByRole('button', { name: /Update Room/ }))

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalled()
    })

    const [roomName, membershipAgentIds] = onSubmit.mock.calls[0]
    expect(roomName).toBe('Renamed Room')
    expect(membershipAgentIds).toContain('agent-active-1')
    expect(membershipAgentIds).toContain('agent-stale-abc')
  })

  it('empty form with no agents uses ID-level submit', async () => {
    const onSubmit = vi.fn()
    const user = userEvent.setup()

    render(
      <RoomSettingForm
        onSubmit={onSubmit}
        availableAgents={[activeAgent1]}
        requireRoomName={false}
      />
    )

    await user.click(screen.getByRole('button', { name: /Create Room/ }))

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalled()
    })

    const [, membershipAgentIds] = onSubmit.mock.calls[0]
    expect(Array.isArray(membershipAgentIds)).toBe(true)
    expect(membershipAgentIds).toHaveLength(0)
  })

  it('initializes form when availableAgents is empty (all-stale room)', async () => {
    render(
      <RoomSettingForm
        onSubmit={vi.fn()}
        availableAgents={[]}
        loadingAgents={false}
        requireRoomName={false}
        isEditing
        initialData={{
          roomName: 'All Stale Room',
          selectedAgents: {
            'agent-gone-1': 'Departed Bot',
            'agent-gone-2': 'Lost Bot',
          },
        }}
      />
    )

    await waitFor(() => {
      expect(screen.getByDisplayValue('All Stale Room')).toBeInTheDocument()
    })

    expect(screen.getByText(/Unavailable members/i)).toBeInTheDocument()
    expect(screen.getByText('Departed Bot')).toBeInTheDocument()
    expect(screen.getByText('Lost Bot')).toBeInTheDocument()
  })

  it('user can remove a stale member via the X button', async () => {
    const onSubmit = vi.fn()
    const user = userEvent.setup()

    render(
      <RoomSettingForm
        onSubmit={onSubmit}
        availableAgents={[activeAgent1]}
        requireRoomName={false}
        isEditing
        initialData={{
          roomName: 'Room With Stale',
          selectedAgents: {
            'agent-active-1': 'Active Bot',
            'agent-deleted-1': 'Deleted Bot',
            'agent-private-1': 'Private Bot',
          },
        }}
      />
    )

    await waitFor(() => {
      expect(screen.getByDisplayValue('Room With Stale')).toBeInTheDocument()
    })

    expect(screen.getByText('Deleted Bot')).toBeInTheDocument()
    expect(screen.getByText('Private Bot')).toBeInTheDocument()

    const removeDeletedBtn = screen.getByLabelText('Remove Deleted Bot')
    await user.click(removeDeletedBtn)

    expect(screen.queryByText('Deleted Bot')).not.toBeInTheDocument()
    expect(screen.getByText('Private Bot')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Update Room/ }))

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalled()
    })

    const [, membershipAgentIds] = onSubmit.mock.calls[0]
    expect(membershipAgentIds).toContain('agent-active-1')
    expect(membershipAgentIds).toContain('agent-private-1')
    expect(membershipAgentIds).not.toContain('agent-deleted-1')
  })

  it('does not wait for agents when loadingAgents is false and catalog is empty', async () => {
    const onSubmit = vi.fn()
    const user = userEvent.setup()

    render(
      <RoomSettingForm
        onSubmit={onSubmit}
        availableAgents={[]}
        loadingAgents={false}
        requireRoomName={false}
        isEditing
        initialData={{
          roomName: 'Empty Catalog Room',
          selectedAgents: { 'agent-x': 'Ghost Agent' },
        }}
      />
    )

    await waitFor(() => {
      expect(screen.getByDisplayValue('Empty Catalog Room')).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: /Update Room/ }))

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalled()
    })

    const [, membershipAgentIds] = onSubmit.mock.calls[0]
    expect(membershipAgentIds).toContain('agent-x')
  })

  it('uses resolvedAgents availability when provided (deleted vs inactive vs inaccessible)', async () => {
    render(
      <RoomSettingForm
        onSubmit={vi.fn()}
        availableAgents={[activeAgent1]}
        loadingAgents={false}
        requireRoomName={false}
        isEditing
        initialData={{
          roomName: 'Resolved Room',
          selectedAgents: {
            'agent-active-1': 'Active Bot',
            'agent-del': 'Deleted Agent',
            'agent-inact': 'Inactive Agent',
            'agent-priv': 'Private Agent',
          },
          resolvedAgents: [
            { id: 'agent-active-1', name: 'Active Bot', availability: 'available' },
            { id: 'agent-del', name: 'Deleted Agent', availability: 'deleted' },
            { id: 'agent-inact', name: 'Inactive Agent', availability: 'inactive' },
            { id: 'agent-priv', name: 'Private Agent', availability: 'inaccessible' },
          ],
        }}
      />
    )

    await waitFor(() => {
      expect(screen.getByDisplayValue('Resolved Room')).toBeInTheDocument()
    })

    expect(screen.getByText('Deleted')).toBeInTheDocument()
    expect(screen.getByText('Inactive')).toBeInTheDocument()
    expect(screen.getByText('Unavailable')).toBeInTheDocument()
  })

  it('falls back to inaccessible when resolvedAgents is not provided', async () => {
    render(
      <RoomSettingForm
        onSubmit={vi.fn()}
        availableAgents={[]}
        loadingAgents={false}
        requireRoomName={false}
        isEditing
        initialData={{
          roomName: 'Fallback Room',
          selectedAgents: {
            'agent-unknown': 'Unknown Agent',
          },
        }}
      />
    )

    await waitFor(() => {
      expect(screen.getByDisplayValue('Fallback Room')).toBeInTheDocument()
    })

    expect(screen.getByText('Unavailable')).toBeInTheDocument()
  })

  it('uses resolved name over legacy name when available', async () => {
    render(
      <RoomSettingForm
        onSubmit={vi.fn()}
        availableAgents={[]}
        loadingAgents={false}
        requireRoomName={false}
        isEditing
        initialData={{
          roomName: 'Name Test Room',
          selectedAgents: {
            'agent-renamed': 'Old Name',
          },
          resolvedAgents: [
            { id: 'agent-renamed', name: 'Correct Name From Backend', availability: 'inactive' },
          ],
        }}
      />
    )

    await waitFor(() => {
      expect(screen.getByDisplayValue('Name Test Room')).toBeInTheDocument()
    })

    expect(screen.getByText('Correct Name From Backend')).toBeInTheDocument()
    expect(screen.queryByText('Old Name')).not.toBeInTheDocument()
  })
})
