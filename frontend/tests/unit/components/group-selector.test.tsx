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

  it('does not use a native title tooltip for the clear override control', () => {
    render(
      <GroupSelector
        selectedGroup="all_agents"
        onGroupChange={vi.fn()}
        groups={[]}
        isOverride
        onClearOverride={vi.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: /clear override/i })).toBeInTheDocument()
    expect(screen.queryByTitle('Clear override, use room default')).not.toBeInTheDocument()
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
  })

  it('keeps override controls inside the constrained selector width', () => {
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
        isOverride
        onClearOverride={vi.fn()}
        className="min-w-0 max-w-[11rem]"
      />,
    )

    expect(screen.getByRole('button', { name: /insurance group/i }).className).toContain('flex-1')
    expect(screen.getByRole('button', { name: /clear override/i }).className).toContain('shrink-0')
  })
})
