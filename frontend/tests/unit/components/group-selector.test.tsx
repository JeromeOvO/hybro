import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { GroupSelector } from '@/components/group-selector'

afterEach(() => {
  cleanup()
})

describe('GroupSelector', () => {
  it('keeps mentioned agent display compact with avatar and count', () => {
    render(
      <GroupSelector
        selectedGroup="all_agents"
        onGroupChange={vi.fn()}
        groups={[]}
        mentionedAgents={[{ id: 'a-1', name: 'Hello World Agent With A Long Name' }]}
        className="min-w-0 max-w-[11rem]"
      />,
    )

    expect(screen.getByAltText('Hello World Agent With A Long Name')).toBeInTheDocument()
    const label = screen.getByText('1 agent')
    const pill = label.closest('div')

    expect(pill?.className).toContain('h-8')
    expect(pill?.className).toContain('min-w-0')
    expect(pill?.className).toContain('whitespace-nowrap')
    expect(label.className).toContain('text-sm')
  })

  it('shows plural count for multiple mentioned agents', () => {
    render(
      <GroupSelector
        selectedGroup="all_agents"
        onGroupChange={vi.fn()}
        groups={[]}
        mentionedAgents={[
          { id: 'a-1', name: 'Agent One' },
          { id: 'a-2', name: 'Agent Two' },
        ]}
      />,
    )

    expect(screen.getByText('2 agents')).toBeInTheDocument()
    expect(screen.getByAltText('Agent One')).toBeInTheDocument()
    expect(screen.getByAltText('Agent Two')).toBeInTheDocument()
  })

  it('does not render a separate clear control for an override', () => {
    render(
      <GroupSelector
        selectedGroup="all_agents"
        onGroupChange={vi.fn()}
        groups={[]}
      />,
    )

    expect(screen.getAllByRole('button')).toHaveLength(1)
    expect(screen.getByRole('button', { name: /all agents/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /clear/i })).not.toBeInTheDocument()
  })

  it('uses team terminology for saved agent collections', async () => {
    const user = userEvent.setup()
    render(
      <GroupSelector
        selectedGroup="all_agents"
        onGroupChange={vi.fn()}
        groups={[
          {
            group_id: 'research',
            name: 'Research Team',
            type: 'user',
            agents: [],
            owner_id: 'user-1',
            created_at: '2026-01-01T00:00:00Z',
            updated_at: '2026-01-01T00:00:00Z',
          },
        ]}
        onCreateGroup={vi.fn()}
      />,
    )

    await user.click(screen.getByRole('button', { name: /all agents/i }))

    expect(await screen.findByText('Research Team')).toBeInTheDocument()
    expect(screen.getByText('Create Team')).toBeInTheDocument()
    expect(screen.queryByText('Saved Teams')).not.toBeInTheDocument()
    expect(screen.queryByText('Saved Groups')).not.toBeInTheDocument()
    expect(screen.queryByText('Create Group')).not.toBeInTheDocument()
    expect(screen.queryByText('Room Default Agents')).not.toBeInTheDocument()
  })

  it('shows an existing default team without requiring an override', () => {
    render(
      <GroupSelector
        selectedGroup="research"
        onGroupChange={vi.fn()}
        groups={[
          {
            group_id: 'research',
            name: 'Research Team',
            type: 'user',
            agents: ['agent-1'],
            owner_id: 'user-1',
            created_at: '2026-01-01T00:00:00Z',
            updated_at: '2026-01-01T00:00:00Z',
          },
        ]}
      />,
    )

    expect(screen.getByRole('button', { name: /research team/i })).toBeInTheDocument()
    expect(screen.queryByText('Room Default')).not.toBeInTheDocument()
  })

  it('keeps the persisted source-team label while the catalog is loading', () => {
    render(
      <GroupSelector
        selectedGroup="research"
        selectedGroupName="Research Team"
        onGroupChange={vi.fn()}
        groups={[]}
        loadingGroups
      />,
    )

    expect(screen.getByRole('button', { name: /research team/i })).toBeInTheDocument()
    expect(screen.queryByText('Loading teams...')).not.toBeInTheDocument()
  })

  it('falls back to All Agents when the selected team no longer exists', () => {
    render(
      <GroupSelector
        selectedGroup="deleted-team"
        onGroupChange={vi.fn()}
        groups={[]}
      />,
    )

    expect(screen.getByRole('button', { name: /all agents/i })).toBeInTheDocument()
    expect(screen.queryByText('Room Default')).not.toBeInTheDocument()
  })

  it('keeps the selected override label inside the constrained selector width', () => {
    render(
      <GroupSelector
        selectedGroup="insurance"
        onGroupChange={vi.fn()}
        groups={[
          {
            group_id: 'insurance',
            name: 'Insurance Group',
            type: 'user',
            agents: ['agent-1'],
            owner_id: 'user-1',
            created_at: '2026-01-01T00:00:00Z',
            updated_at: '2026-01-01T00:00:00Z',
          },
        ]}
        className="min-w-0 max-w-[11rem]"
      />,
    )

    expect(screen.getByRole('button', { name: /insurance group/i }).className).toContain('flex-1')
    expect(screen.getAllByRole('button')).toHaveLength(1)
  })
})
